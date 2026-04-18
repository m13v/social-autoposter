'use strict';

const path = require('path');
const fs = require('fs');
const { execSync, spawnSync } = require('child_process');

const UNIT_PREFIX = 'com.m13v.social-';
const SERVICE_SUFFIX = '.service';
const TIMER_SUFFIX = '.timer';

function renderService(job, env) {
  return `[Unit]
Description=${job.label}

[Service]
Type=oneshot
ExecStart=/bin/bash ${job.script}
Environment=PATH=${env.path}
Environment=HOME=${env.home}
StandardOutput=append:${job.stdoutLog}
StandardError=append:${job.stderrLog}
`;
}

function renderTimer(job) {
  const onActive = job.runAtLoad ? 0 : job.interval;
  return `[Unit]
Description=Timer for ${job.label}

[Timer]
OnActiveSec=${onActive}s
OnUnitActiveSec=${job.interval}s
Unit=${job.label}.service

[Install]
WantedBy=timers.target
`;
}

function unitBase(job) {
  return job.file.replace(/\.plist$/, '').replace(/\.service$|\.timer$/, '');
}

function generate({ jobs, outDir, env }) {
  fs.mkdirSync(outDir, { recursive: true });
  const written = [];
  for (const job of jobs) {
    const base = unitBase(job);
    const serviceTarget = path.join(outDir, `${base}${SERVICE_SUFFIX}`);
    const timerTarget = path.join(outDir, `${base}${TIMER_SUFFIX}`);
    fs.writeFileSync(serviceTarget, renderService(job, env));
    fs.writeFileSync(timerTarget, renderTimer(job));
    written.push(serviceTarget, timerTarget);
  }
  return written;
}

function defaultEnv({ home, nodeBin }) {
  const dirs = new Set();
  if (nodeBin) dirs.add(nodeBin);
  dirs.add('/usr/local/bin');
  dirs.add('/usr/bin');
  dirs.add('/bin');
  return {
    home,
    path: [...dirs].join(':'),
  };
}

// ─────────────────────────── Control plane ───────────────────────────

function list() {
  const loadedLabels = new Set();
  const pidByLabel = new Map();
  try {
    const out = execSync(
      'systemctl --user list-units --type=timer --all --no-legend --plain',
      { stdio: 'pipe', maxBuffer: 8 * 1024 * 1024 }
    ).toString();
    for (const line of out.split('\n')) {
      const parts = line.trim().split(/\s+/);
      if (!parts.length) continue;
      const unit = parts[0];
      if (!unit.startsWith(UNIT_PREFIX) || !unit.endsWith(TIMER_SUFFIX)) continue;
      const label = unit.slice(0, -TIMER_SUFFIX.length);
      loadedLabels.add(label);
    }
  } catch {}
  for (const label of loadedLabels) {
    const pid = pidFor(label);
    if (pid != null) pidByLabel.set(label, pid);
  }
  return { loadedLabels, pidByLabel };
}

function isLoaded(label) {
  const r = spawnSync(
    'systemctl',
    ['--user', 'is-enabled', `${label}${TIMER_SUFFIX}`],
    { encoding: 'utf8' }
  );
  const s = (r.stdout || '').trim();
  return s === 'enabled' || s === 'static' || s === 'alias';
}

function pidFor(label) {
  try {
    const out = execSync(
      `systemctl --user show -p MainPID --value ${label}${SERVICE_SUFFIX}`,
      { stdio: 'pipe' }
    ).toString().trim();
    const pid = parseInt(out, 10);
    if (!isNaN(pid) && pid > 0) return pid;
    return null;
  } catch { return null; }
}

function labelFromUnitPath(unitPath) {
  const base = path.basename(unitPath).replace(/\.(service|timer)$/, '');
  return base;
}

// On systemd, "loading" means enabling + starting the timer. The unit file
// must be installed into the user's systemd dir (via `install`) first;
// `systemctl --user enable` expects to find the file under its search path.
function load(unitPath) {
  const label = labelFromUnitPath(unitPath);
  const reload = spawnSync('systemctl', ['--user', 'daemon-reload'], { encoding: 'utf8' });
  if (reload.status !== 0) {
    return { ok: false, stderr: (reload.stderr || '').trim(), status: reload.status };
  }
  const r = spawnSync(
    'systemctl',
    ['--user', 'enable', '--now', `${label}${TIMER_SUFFIX}`],
    { encoding: 'utf8' }
  );
  const stderr = (r.stderr || '').trim();
  return { ok: r.status === 0, stderr, status: r.status };
}

function unload(label, unitPath) {
  const lbl = label || labelFromUnitPath(unitPath);
  const r = spawnSync(
    'systemctl',
    ['--user', 'disable', '--now', `${lbl}${TIMER_SUFFIX}`],
    { encoding: 'utf8' }
  );
  const stderr = (r.stderr || '').trim();
  return { ok: r.status === 0, stderr, status: r.status };
}

function kickstart(label) {
  const r = spawnSync(
    'systemctl',
    ['--user', 'start', `${label}${SERVICE_SUFFIX}`],
    { encoding: 'utf8' }
  );
  const stderr = (r.stderr || '').trim();
  const pid = pidFor(label);
  return { ok: r.status === 0, stderr, pid };
}

function killJob(label) {
  const r = spawnSync(
    'systemctl',
    ['--user', 'kill', '--signal=SIGKILL', `${label}${SERVICE_SUFFIX}`],
    { encoding: 'utf8' }
  );
  return { ok: r.status === 0, stderr: (r.stderr || '').trim() };
}

// Install unit files into the user's systemd dir (via symlink). A systemd
// "unit" is actually a pair (.service + .timer) so we resolve both and
// install whichever siblings exist next to the given source file.
function install(unitSrc, agentsDir) {
  fs.mkdirSync(agentsDir, { recursive: true });
  const base = labelFromUnitPath(unitSrc);
  const srcDir = path.dirname(unitSrc);
  const linked = [];
  for (const suffix of [SERVICE_SUFFIX, TIMER_SUFFIX]) {
    const src = path.join(srcDir, `${base}${suffix}`);
    if (!fs.existsSync(src)) continue;
    const link = path.join(agentsDir, `${base}${suffix}`);
    if (!fs.existsSync(link)) {
      try { fs.symlinkSync(src, link); } catch { return null; }
    }
    linked.push(link);
  }
  return linked.length ? linked[linked.length - 1] : null;
}

function unitFileName(jobFile) {
  // launchd jobFile is "com.m13v.social-X.plist"; for systemd the timer
  // is the primary unit since that's what schedules the service.
  const base = jobFile.replace(/\.plist$/, '').replace(/\.(service|timer)$/, '');
  return `${base}${TIMER_SUFFIX}`;
}

// Discover every social-autoposter job by scanning for .service files in
// either the repo's systemd/ dir or the user's systemd user dir.
function discoverJobs({ repoUnitDir, agentsDir }) {
  const byLabel = new Map();
  const scan = (dir) => {
    try {
      const files = fs.readdirSync(dir).filter(f =>
        f.startsWith(UNIT_PREFIX) && f.endsWith(SERVICE_SUFFIX)
      );
      for (const f of files) {
        try {
          const body = fs.readFileSync(path.join(dir, f), 'utf8');
          const { label, scriptPath } = parseUnit(body);
          const resolvedLabel = label || f.slice(0, -SERVICE_SUFFIX.length);
          if (!byLabel.has(resolvedLabel)) {
            byLabel.set(resolvedLabel, {
              label: resolvedLabel,
              unitFile: `${resolvedLabel}${TIMER_SUFFIX}`,
              scriptPath,
            });
          }
        } catch {}
      }
    } catch {}
  };
  scan(repoUnitDir);
  scan(agentsDir);
  return [...byLabel.values()];
}

function parseUnit(text) {
  // For a .service file: Description= is the label, ExecStart= points at
  // the script. systemd ExecStart can be "/bin/bash /path/to/x.sh" or
  // just the script itself.
  const descM = text.match(/^Description=(.+)$/m);
  const label = descM ? descM[1].trim() : null;
  let scriptPath = null;
  const execM = text.match(/^ExecStart=(.+)$/m);
  if (execM) {
    const tokens = execM[1].trim().split(/\s+/);
    scriptPath = tokens.find(s => /\.(sh|py|js)$/.test(s)) || tokens[tokens.length - 1] || null;
  }
  return { label, scriptPath };
}

// For systemd, callers pass the TIMER file contents (that's where the
// schedule lives). Returns interval in seconds, or null if calendar-based
// and not reducible to a single gap.
function scheduleFromUnit(text) {
  try {
    const unitActive = text.match(/^OnUnitActiveSec=(\d+)(s|sec|m|min|h|hour)?$/m);
    if (unitActive) {
      return { intervalSecs: toSeconds(unitActive[1], unitActive[2]), kind: 'simple' };
    }
    const active = text.match(/^OnActiveSec=(\d+)(s|sec|m|min|h|hour)?$/m);
    if (active) {
      return { intervalSecs: toSeconds(active[1], active[2]), kind: 'simple' };
    }
    const calendars = [...text.matchAll(/^OnCalendar=(.+)$/gm)].map(m => m[1].trim());
    if (calendars.length) {
      return { intervalSecs: null, kind: 'calendar' };
    }
    return { intervalSecs: null, kind: null };
  } catch { return { intervalSecs: null, kind: null }; }
}

function toSeconds(num, unit) {
  const n = parseInt(num, 10);
  switch ((unit || 's').toLowerCase()) {
    case 's': case 'sec': return n;
    case 'm': case 'min': return n * 60;
    case 'h': case 'hour': return n * 3600;
    default: return n;
  }
}

// In-place edit of the interval on a timer file. Returns true if updated,
// false if the unit uses OnCalendar= (calendar schedule; not settable here).
function updateInterval(unitPath, seconds) {
  const text = fs.readFileSync(unitPath, 'utf8');
  if (/^OnCalendar=/m.test(text) && !/^OnUnitActiveSec=/m.test(text)) return false;
  let next = text;
  if (/^OnUnitActiveSec=/m.test(next)) {
    next = next.replace(/^OnUnitActiveSec=.*$/m, `OnUnitActiveSec=${seconds}s`);
  } else {
    // No schedule line present — cannot infer what to change safely.
    return false;
  }
  if (/^OnActiveSec=/m.test(next)) {
    next = next.replace(/^OnActiveSec=.*$/m, `OnActiveSec=${seconds}s`);
  }
  fs.writeFileSync(unitPath, next);
  return true;
}

module.exports = {
  renderService,
  renderTimer,
  generate,
  defaultEnv,
  unitBase,
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
  updateInterval,
};
