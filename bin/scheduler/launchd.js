'use strict';

const path = require('path');
const fs = require('fs');
const { execSync, spawnSync } = require('child_process');
const platform = require('../platform');

// Unit filter for discovery and listing. Historically everything was
// `com.m13v.social-*`. A few SEO jobs got provisioned as `com.m13v.seo-*`
// (weekly roundup, standalone SEO daily report), so accept either prefix.
// Other m13v jobs (fazm-*, gmail-*, etc.) still get excluded.
const UNIT_PREFIXES = ['com.m13v.social-', 'com.m13v.seo-'];
const UNIT_PREFIX = UNIT_PREFIXES[0]; // kept for renderPlist/install callers
const UNIT_SUFFIX = '.plist';

function hasUnitPrefix(label) {
  return UNIT_PREFIXES.some(p => label.startsWith(p));
}

function fileHasUnitPrefix(filename) {
  return UNIT_PREFIXES.some(p => filename.startsWith(p)) && filename.endsWith(UNIT_SUFFIX);
}

function renderPlist(job, env) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>${job.label}</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>/bin/bash</string>
\t\t<string>${job.script}</string>
\t</array>
\t<key>StartInterval</key>
\t<integer>${job.interval}</integer>
\t<key>StandardOutPath</key>
\t<string>${job.stdoutLog}</string>
\t<key>StandardErrorPath</key>
\t<string>${job.stderrLog}</string>
\t<key>EnvironmentVariables</key>
\t<dict>
\t\t<key>PATH</key>
\t\t<string>${env.path}</string>
\t\t<key>HOME</key>
\t\t<string>${env.home}</string>
\t</dict>
\t<key>RunAtLoad</key>
\t<${job.runAtLoad}/>
</dict>
</plist>
`;
}

function generate({ jobs, outDir, env }) {
  fs.mkdirSync(outDir, { recursive: true });
  const written = [];
  for (const job of jobs) {
    const xml = renderPlist(job, env);
    const target = path.join(outDir, job.file);
    fs.writeFileSync(target, xml);
    written.push(target);
  }
  return written;
}

function defaultEnv({ home, nodeBin }) {
  return {
    home,
    path: platform.launchdPath(nodeBin),
  };
}

// ─────────────────────────── Control plane ───────────────────────────

function list() {
  const loadedLabels = new Set();
  const pidByLabel = new Map();
  try {
    const out = execSync('launchctl list', { stdio: 'pipe', maxBuffer: 8 * 1024 * 1024 }).toString();
    for (const line of out.split('\n').slice(1)) {
      const parts = line.split('\t');
      if (parts.length < 3) continue;
      const label = parts[2];
      if (!hasUnitPrefix(label)) continue;
      loadedLabels.add(label);
      const pid = parseInt(parts[0], 10);
      if (!isNaN(pid)) pidByLabel.set(label, pid);
    }
  } catch {}
  return { loadedLabels, pidByLabel };
}

function isLoaded(label) {
  try {
    execSync(`launchctl list ${label}`, { stdio: 'pipe' });
    return true;
  } catch { return false; }
}

function pidFor(label) {
  try {
    const out = execSync(`launchctl list ${label}`, { stdio: 'pipe' }).toString();
    const m = out.match(/"PID"\s*=\s*(\d+);/);
    return m ? parseInt(m[1], 10) : null;
  } catch { return null; }
}

// launchctl load/unload exit 0 even on failure (e.g. "Unload failed: 5:
// Input/output error" when already unloaded). Use spawnSync so we capture
// stderr and detect silent-failure cases.
function load(unitPath) {
  const r = spawnSync('launchctl', ['load', unitPath], { encoding: 'utf8' });
  const stderr = (r.stderr || '').trim();
  return { ok: r.status === 0 && !/failed/i.test(stderr), stderr, status: r.status };
}

function unload(_label, unitPath) {
  const r = spawnSync('launchctl', ['unload', unitPath], { encoding: 'utf8' });
  const stderr = (r.stderr || '').trim();
  return { ok: r.status === 0 && !/failed/i.test(stderr), stderr, status: r.status };
}

function kickstart(label) {
  const target = `gui/${process.getuid()}/${label}`;
  const r = spawnSync('launchctl', ['kickstart', '-p', target], { encoding: 'utf8' });
  const pid = parseInt((r.stdout || '').trim(), 10);
  return {
    ok: r.status === 0,
    stderr: (r.stderr || r.stdout || '').trim(),
    pid: isNaN(pid) ? null : pid,
  };
}

function killJob(label) {
  const target = `gui/${process.getuid()}/${label}`;
  const r = spawnSync('launchctl', ['kill', 'SIGKILL', target], { encoding: 'utf8' });
  return { ok: r.status === 0, stderr: (r.stderr || '').trim() };
}

// Install a unit file into the user's agents dir (via symlink, matching the
// existing setup flow). Creates the dir if missing.
function install(unitSrc, agentsDir) {
  fs.mkdirSync(agentsDir, { recursive: true });
  const linkPath = path.join(agentsDir, path.basename(unitSrc));
  if (!fs.existsSync(linkPath)) {
    try { fs.symlinkSync(unitSrc, linkPath); } catch { return null; }
  }
  return linkPath;
}

function unitFileName(jobFile) {
  // jobFile is e.g. "com.m13v.social-stats.plist"; launchd needs the plist path.
  return jobFile;
}

// Discover every social-autoposter job from plist files in either the repo's
// launchd/ dir or the user's LaunchAgents dir. Returns [{label, unitFile, scriptPath}].
function discoverJobs({ repoUnitDir, agentsDir }) {
  const byLabel = new Map();
  const scan = (dir) => {
    try {
      const files = fs.readdirSync(dir).filter(fileHasUnitPrefix);
      for (const f of files) {
        try {
          const body = fs.readFileSync(path.join(dir, f), 'utf8');
          const { label, scriptPath } = parseUnit(body);
          if (!label) continue;
          if (!byLabel.has(label)) {
            byLabel.set(label, { label, unitFile: f, scriptPath });
          }
        } catch {}
      }
    } catch {}
  };
  scan(repoUnitDir);
  scan(agentsDir);
  return [...byLabel.values()];
}

function parseUnit(xml) {
  const labelM = xml.match(/<key>Label<\/key>\s*<string>([^<]+)<\/string>/);
  const label = labelM ? labelM[1] : null;
  let scriptPath = null;
  const argsM = xml.match(/<key>ProgramArguments<\/key>\s*<array>([\s\S]*?)<\/array>/);
  if (argsM) {
    const strings = [...argsM[1].matchAll(/<string>([^<]+)<\/string>/g)].map(m => m[1]);
    scriptPath = strings.find(s => /\.(sh|py|js)$/.test(s)) || null;
  }
  if (!scriptPath) {
    const progM = xml.match(/<key>Program<\/key>\s*<string>([^<]+)<\/string>/);
    if (progM) scriptPath = progM[1];
  }
  return { label, scriptPath };
}

// Returns interval in seconds, or null if calendar-based / unsettable.
function scheduleFromUnit(xml) {
  try {
    const si = xml.match(/<key>StartInterval<\/key>\s*<integer>(\d+)<\/integer>/);
    if (si) return { intervalSecs: parseInt(si[1], 10), kind: 'simple' };
    let entries = null;
    const arrM = xml.match(/<key>StartCalendarInterval<\/key>\s*<array>([\s\S]*?)<\/array>/);
    if (arrM) {
      entries = [...arrM[1].matchAll(/<dict>([\s\S]*?)<\/dict>/g)].map(m => m[1]);
    } else {
      const dictM = xml.match(/<key>StartCalendarInterval<\/key>\s*<dict>([\s\S]*?)<\/dict>/);
      if (dictM) entries = [dictM[1]];
    }
    if (!entries || !entries.length) return { intervalSecs: null, kind: null };
    const dayMins = [];
    let minuteOnlyCount = 0;
    for (const body of entries) {
      const h = body.match(/<key>Hour<\/key>\s*<integer>(\d+)<\/integer>/);
      const m = body.match(/<key>Minute<\/key>\s*<integer>(\d+)<\/integer>/);
      if (h) dayMins.push(parseInt(h[1], 10) * 60 + (m ? parseInt(m[1], 10) : 0));
      else if (m) minuteOnlyCount++;
    }
    if (minuteOnlyCount > 0 && dayMins.length === 0) {
      return { intervalSecs: Math.round(3600 / minuteOnlyCount), kind: 'calendar' };
    }
    if (dayMins.length === 1) return { intervalSecs: 86400, kind: 'calendar' };
    if (dayMins.length > 1) {
      dayMins.sort((a, b) => a - b);
      let minGap = Infinity;
      for (let i = 1; i < dayMins.length; i++) {
        minGap = Math.min(minGap, dayMins[i] - dayMins[i - 1]);
      }
      minGap = Math.min(minGap, 1440 - dayMins[dayMins.length - 1] + dayMins[0]);
      return { intervalSecs: minGap * 60, kind: 'calendar' };
    }
    return { intervalSecs: null, kind: null };
  } catch { return { intervalSecs: null, kind: null }; }
}

// Returns a Date for the next scheduled fire in the host's local timezone, or
// null if the unit has no settable schedule (e.g. KeepAlive-only, or a calendar
// entry we can't reduce to a concrete next-fire). Handles:
//   - StartInterval: now + intervalSecs
//   - StartCalendarInterval with a single Hour/Minute dict: next occurrence
//     today at HH:MM (or tomorrow if HH:MM has already passed)
//   - StartCalendarInterval as an array of Hour/Minute dicts: earliest upcoming
//     entry across today/tomorrow
function nextRunFromUnit(xml) {
  try {
    const si = xml.match(/<key>StartInterval<\/key>\s*<integer>(\d+)<\/integer>/);
    if (si) {
      const secs = parseInt(si[1], 10);
      if (!secs) return null;
      return new Date(Date.now() + secs * 1000);
    }
    let entries = null;
    const arrM = xml.match(/<key>StartCalendarInterval<\/key>\s*<array>([\s\S]*?)<\/array>/);
    if (arrM) {
      entries = [...arrM[1].matchAll(/<dict>([\s\S]*?)<\/dict>/g)].map(m => m[1]);
    } else {
      const dictM = xml.match(/<key>StartCalendarInterval<\/key>\s*<dict>([\s\S]*?)<\/dict>/);
      if (dictM) entries = [dictM[1]];
    }
    if (!entries || !entries.length) return null;
    const hhmms = [];
    for (const body of entries) {
      const h = body.match(/<key>Hour<\/key>\s*<integer>(\d+)<\/integer>/);
      const m = body.match(/<key>Minute<\/key>\s*<integer>(\d+)<\/integer>/);
      if (h) hhmms.push({ hour: parseInt(h[1], 10), minute: m ? parseInt(m[1], 10) : 0 });
    }
    if (!hhmms.length) return null;
    const now = new Date();
    let best = null;
    for (const { hour, minute } of hhmms) {
      const today = new Date(now);
      today.setHours(hour, minute, 0, 0);
      const cand = today > now ? today : new Date(today.getTime() + 86400000);
      if (!best || cand < best) best = cand;
    }
    return best;
  } catch { return null; }
}

// In-place edit StartInterval on a plist. Returns true if updated, false if
// the unit uses a calendar schedule (not settable here).
function updateInterval(unitPath, seconds) {
  const xml = fs.readFileSync(unitPath, 'utf8');
  if (!/<key>StartInterval<\/key>/.test(xml)) return false;
  const next = xml.replace(
    /(<key>StartInterval<\/key>\s*<integer>)\d+(<\/integer>)/,
    `$1${seconds}$2`
  );
  fs.writeFileSync(unitPath, next);
  return true;
}

// Read the "start time" from a plist: for a single-dict calendar schedule,
// the Hour/Minute; for an array-form calendar schedule, the earliest entry.
// Returns null for interval-only jobs (no wall-clock anchor).
function startTimeFromUnit(xml) {
  try {
    const arrM = xml.match(/<key>StartCalendarInterval<\/key>\s*<array>([\s\S]*?)<\/array>/);
    if (arrM) {
      // Return the first entry in document order, which is the user-chosen
      // anchor when this plist was written by updateStartTime. For legacy
      // hand-written arrays (typically sorted ascending) this also happens to
      // be the earliest fire of the day.
      const first = arrM[1].match(/<dict>([\s\S]*?)<\/dict>/);
      if (!first) return null;
      const fh = first[1].match(/<key>Hour<\/key>\s*<integer>(\d+)<\/integer>/);
      const fm = first[1].match(/<key>Minute<\/key>\s*<integer>(\d+)<\/integer>/);
      if (!fh && !fm) return null;
      return {
        hour: fh ? parseInt(fh[1], 10) : 0,
        minute: fm ? parseInt(fm[1], 10) : 0,
      };
    }
    const dictM = xml.match(/<key>StartCalendarInterval<\/key>\s*<dict>([\s\S]*?)<\/dict>/);
    if (!dictM) return null;
    const body = dictM[1];
    const h = body.match(/<key>Hour<\/key>\s*<integer>(\d+)<\/integer>/);
    const m = body.match(/<key>Minute<\/key>\s*<integer>(\d+)<\/integer>/);
    if (!h && !m) return null;
    return {
      hour: h ? parseInt(h[1], 10) : 0,
      minute: m ? parseInt(m[1], 10) : 0,
    };
  } catch { return null; }
}

// Count the fires-per-day implied by a plist's schedule, and the cadence
// (minutes between fires). Used to decide whether a user-supplied start time
// produces a single-fire or a multi-fire calendar array.
// Returns { count, cadenceMin }. count=1 means a single daily/weekly fire;
// count>1 means a multi-fire array.
function cadenceFromUnit(xml) {
  const arrM = xml.match(/<key>StartCalendarInterval<\/key>\s*<array>([\s\S]*?)<\/array>/);
  if (arrM) {
    const entries = [...arrM[1].matchAll(/<dict>/g)];
    const count = Math.max(1, entries.length);
    return { count, cadenceMin: count > 1 ? Math.round(1440 / count) : null };
  }
  const dictM = xml.match(/<key>StartCalendarInterval<\/key>\s*<dict>/);
  if (dictM) return { count: 1, cadenceMin: null };
  const siM = xml.match(/<key>StartInterval<\/key>\s*<integer>(\d+)<\/integer>/);
  if (siM) {
    const secs = parseInt(siM[1], 10);
    if (secs <= 0) return { count: 1, cadenceMin: null };
    if (secs >= 86400) return { count: 1, cadenceMin: null };
    return {
      count: Math.max(1, Math.floor(86400 / secs)),
      cadenceMin: Math.max(1, Math.round(secs / 60)),
    };
  }
  return { count: 1, cadenceMin: null };
}

// Shift a plist's start time to {hour, minute}, preserving cadence and count.
//   - Single-dict calendar (incl. Weekday-qualified weekly jobs): surgical
//     H/M rewrite so Weekday (and any other keys) are preserved.
//   - Array-form calendar or StartInterval sub-daily: replace the schedule
//     with an evenly-spaced array of the same count, starting at HH:MM.
//   - StartInterval >= 1 day or no schedule: emit a single daily dict.
// Returns { ok, kind, count } on success, { ok: false, reason } otherwise.
// Caller is responsible for reloading the job so launchd picks up the change.
function updateStartTime(unitPath, hour, minute) {
  const xml = fs.readFileSync(unitPath, 'utf8');
  const h = Math.max(0, Math.min(23, parseInt(hour, 10)));
  const m = Math.max(0, Math.min(59, parseInt(minute, 10)));
  if (Number.isNaN(h) || Number.isNaN(m)) return { ok: false, reason: 'invalid time' };

  // Case 1: single-dict calendar — edit Hour/Minute in place so extra keys
  // (Weekday for weekly jobs, etc.) survive untouched.
  const hasArray = /<key>StartCalendarInterval<\/key>\s*<array>/.test(xml);
  if (!hasArray) {
    const singleM = xml.match(/(<key>StartCalendarInterval<\/key>[ \t\r\n]*<dict>)([\s\S]*?)(<\/dict>)/);
    if (singleM) {
      let body = singleM[2];
      const hadHour = /<key>Hour<\/key>/.test(body);
      const hadMin = /<key>Minute<\/key>/.test(body);
      if (hadHour) {
        body = body.replace(/(<key>Hour<\/key>[ \t\r\n]*<integer>)\d+(<\/integer>)/, `$1${h}$2`);
      }
      if (hadMin) {
        body = body.replace(/(<key>Minute<\/key>[ \t\r\n]*<integer>)\d+(<\/integer>)/, `$1${m}$2`);
      }
      if (!hadHour || !hadMin) {
        // Rare: single-dict schedule with only Weekday. Append missing keys.
        const indent = detectIndent(xml);
        const inner = indent + indent;
        const trimmed = body.replace(/\s+$/, '');
        const addHour = hadHour ? '' : `\n${inner}<key>Hour</key>\n${inner}<integer>${h}</integer>`;
        const addMin = hadMin ? '' : `\n${inner}<key>Minute</key>\n${inner}<integer>${m}</integer>`;
        body = trimmed + addHour + addMin + `\n${indent}`;
      }
      const next = xml.replace(singleM[0], singleM[1] + body + singleM[3]);
      fs.writeFileSync(unitPath, next);
      return { ok: true, kind: 'single', count: 1 };
    }
  }

  // Case 2: array-form or interval — regenerate the schedule block with the
  // same count/cadence, shifted to start at HH:MM.
  const { count, cadenceMin } = cadenceFromUnit(xml);
  const indent = detectIndent(xml);
  const i2 = indent + indent;

  let block;
  if (count <= 1 || cadenceMin == null) {
    block =
      `${indent}<key>StartCalendarInterval</key>\n` +
      `${indent}<dict>\n` +
      `${i2}<key>Hour</key>\n` +
      `${i2}<integer>${h}</integer>\n` +
      `${i2}<key>Minute</key>\n` +
      `${i2}<integer>${m}</integer>\n` +
      `${indent}</dict>\n`;
  } else {
    const startMin = h * 60 + m;
    const lines = [];
    for (let i = 0; i < count; i++) {
      const t = (startMin + i * cadenceMin) % 1440;
      const hh = Math.floor(t / 60);
      const mm = t % 60;
      lines.push(`${i2}<dict><key>Hour</key><integer>${hh}</integer><key>Minute</key><integer>${mm}</integer></dict>`);
    }
    block =
      `${indent}<key>StartCalendarInterval</key>\n` +
      `${indent}<array>\n` +
      lines.join('\n') + '\n' +
      `${indent}</array>\n`;
  }

  let next = xml.replace(
    /[ \t]*<key>StartInterval<\/key>[ \t\r\n]*<integer>\d+<\/integer>\n?/,
    ''
  );
  next = next.replace(
    /[ \t]*<key>StartCalendarInterval<\/key>[ \t\r\n]*(?:<dict>[\s\S]*?<\/dict>|<array>[\s\S]*?<\/array>)\n?/,
    ''
  );
  const idx = next.lastIndexOf('</dict>');
  if (idx === -1) return { ok: false, reason: 'malformed plist' };
  next = next.slice(0, idx) + block + next.slice(idx);
  fs.writeFileSync(unitPath, next);
  return { ok: true, kind: count > 1 ? 'array' : 'single', count };
}

function detectIndent(xml) {
  const spaceIndented = /^    <key>/m.test(xml) && !/^\t<key>/m.test(xml);
  return spaceIndented ? '    ' : '\t';
}

module.exports = {
  renderPlist,
  generate,
  defaultEnv,
  list,
  isLoaded,
  pidFor,
  load,
  unload,
  kickstart,
  killJob,
  install,
  unitFileName,
  discoverJobs,
  parseUnit,
  scheduleFromUnit,
  nextRunFromUnit,
  updateInterval,
  startTimeFromUnit,
  updateStartTime,
  cadenceFromUnit,
};
