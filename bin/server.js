#!/usr/bin/env node
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync, spawn } = require('child_process');

const DEST = path.join(os.homedir(), 'social-autoposter');
const LOG_DIR = path.join(DEST, 'skill', 'logs');
const LAUNCHD_DIR = path.join(DEST, 'launchd');
const CONFIG_FILE = path.join(DEST, 'config.json');
const ENV_FILE = path.join(DEST, '.env');
const PORT = parseInt(process.env.PORT || '3141', 10);

// Matrix: rows = job types, columns = platforms
// Each cell is a job (or null if that combo doesn't exist)
const PLATFORMS = ['all', 'Reddit', 'Twitter', 'LinkedIn', 'MoltBook', 'GitHub'];
const JOB_TYPES = ['Post', 'Engage', 'Stats', 'Audit', 'Octolens'];

const JOBS = [
  // Post row
  { label: 'com.m13v.social-reddit-search', name: 'Reddit', type: 'Post', platform: 'Reddit', script: 'run-reddit-search.sh', logPrefix: 'run-reddit-search-', plist: 'com.m13v.social-reddit-search.plist' },
  { label: 'com.m13v.social-reddit-threads', name: 'Reddit Threads', type: 'Post', platform: 'Reddit', script: 'run-reddit-threads.sh', logPrefix: 'run-reddit-threads-', plist: 'com.m13v.social-reddit-threads.plist' },
  { label: 'com.m13v.social-twitter-cycle', name: 'Twitter', type: 'Post', platform: 'Twitter', script: 'run-twitter-cycle.sh', logPrefix: 'twitter-cycle-', plist: 'com.m13v.social-twitter-cycle.plist' },
  { label: 'com.m13v.social-linkedin', name: 'LinkedIn', type: 'Post', platform: 'LinkedIn', script: 'run-linkedin.sh', logPrefix: 'run-linkedin-', plist: 'com.m13v.social-linkedin.plist' },
  { label: 'com.m13v.social-moltbook', name: 'MoltBook', type: 'Post', platform: 'MoltBook', script: 'run-moltbook.sh', logPrefix: 'run-moltbook-', plist: 'com.m13v.social-moltbook.plist' },
  { label: 'com.m13v.social-github', name: 'GitHub', type: 'Post', platform: 'GitHub', script: 'run-github.sh', logPrefix: 'run-github-', plist: 'com.m13v.social-github.plist' },
  // Engage row
  { label: 'com.m13v.social-engage', name: 'Engage Reddit+MB', type: 'Engage', platform: 'Reddit', script: 'engage.sh', logPrefix: 'engage-', plist: 'com.m13v.social-engage.plist' },
  { label: 'com.m13v.social-engage-twitter', name: 'Engage Twitter', type: 'Engage', platform: 'Twitter', script: 'engage-twitter.sh', logPrefix: 'engage-twitter-', plist: 'com.m13v.social-engage-twitter.plist' },
  { label: 'com.m13v.social-engage-linkedin', name: 'Engage LinkedIn', type: 'Engage', platform: 'LinkedIn', script: 'engage-linkedin.sh', logPrefix: 'engage-linkedin-', plist: 'com.m13v.social-engage-linkedin.plist' },
  { label: 'com.m13v.social-github-engage', name: 'GitHub Engage', type: 'Engage', platform: 'GitHub', script: 'github-engage.sh', logPrefix: 'github-engage-', plist: 'com.m13v.social-github-engage.plist' },
  // Stats row (single job covers all platforms)
  { label: 'com.m13v.social-stats', name: 'Stats', type: 'Stats', platform: 'all', script: 'stats.sh', logPrefix: 'stats-', plist: 'com.m13v.social-stats.plist' },
  // Audit row
  { label: 'com.m13v.social-audit', name: 'Audit', type: 'Audit', platform: 'all', script: 'audit.sh', logPrefix: 'audit-', plist: 'com.m13v.social-audit.plist' },
  // Octolens row
  { label: 'com.m13v.social-octolens', name: 'Octolens', type: 'Octolens', platform: 'all', script: 'octolens.sh', logPrefix: 'octolens-', plist: 'com.m13v.social-octolens.plist' },
];

// --- Helpers ---

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', c => data += c);
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function json(res, obj, status = 200) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(obj));
}

function isJobLoaded(label) {
  try {
    execSync(`launchctl list ${label}`, { stdio: 'pipe' });
    return true;
  } catch { return false; }
}

function getJobPids(script) {
  try {
    const scriptPath = path.join(DEST, 'skill', script);
    const out = execSync(`pgrep -f "${scriptPath}"`, { stdio: 'pipe' }).toString().trim();
    return out.length > 0 ? out.split('\n').map(p => parseInt(p, 10)) : [];
  } catch { return []; }
}

function getPlistInterval(plistPath) {
  try {
    const xml = fs.readFileSync(plistPath, 'utf8');
    const m = xml.match(/<key>StartInterval<\/key>\s*<integer>(\d+)<\/integer>/);
    return m ? parseInt(m[1], 10) : null;
  } catch { return null; }
}

function getLastLog(job) {
  try {
    const files = fs.readdirSync(LOG_DIR).filter(f => {
      if (!f.endsWith('.log')) return false;
      if (f.startsWith('launchd-')) return false;
      if (job.logPrefix) return f.startsWith(job.logPrefix);
      // Post job: files starting with a digit (YYYY-MM-DD_...)
      return /^\d{4}-\d{2}-\d{2}_/.test(f);
    }).sort().reverse();
    if (!files.length) return { file: null, time: null };
    const fname = files[0];
    // Extract time from filename: prefix-YYYY-MM-DD_HHMMSS.log or YYYY-MM-DD_HHMMSS.log
    const timeStr = fname.replace(job.logPrefix, '').replace('.log', '');
    const m = timeStr.match(/(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})/);
    const time = m ? new Date(m[1], m[2]-1, m[3], m[4], m[5], m[6]).toISOString() : null;
    return { file: fname, time };
  } catch { return { file: null, time: null }; }
}

function loadEnv() {
  try {
    const raw = fs.readFileSync(ENV_FILE, 'utf8');
    const vars = {};
    const lines = raw.split('\n');
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const eq = trimmed.indexOf('=');
      if (eq < 0) continue;
      const key = trimmed.slice(0, eq);
      let val = trimmed.slice(eq + 1).replace(/^["']|["']$/g, '');
      vars[key] = val;
    }
    return vars;
  } catch { return {}; }
}

function getDbUrl() {
  const env = loadEnv();
  return env.DATABASE_URL || process.env.DATABASE_URL || null;
}

function psql(query) {
  const dbUrl = getDbUrl();
  if (!dbUrl) return null;
  try {
    return execSync(`psql "${dbUrl}" -t -A -c "${query.replace(/"/g, '\\"')}"`, {
      stdio: 'pipe', timeout: 10000
    }).toString().trim();
  } catch { return null; }
}

function getLaunchAgentPath(plistFile) {
  return path.join(os.homedir(), 'Library', 'LaunchAgents', plistFile);
}

// Parse label and script path from a launchd plist XML blob.
// Returns { label, scriptPath } where scriptPath is the first entry in
// ProgramArguments that looks like a script (.sh/.py/.js), or Program if set.
function parsePlist(xml) {
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

// Discover every social-autoposter launchd job from plist files.
// Scans the repo's launchd/ dir plus ~/Library/LaunchAgents/ and merges by
// label so we catch jobs installed without a repo copy (e.g. hand-installed).
// This is the single source of truth for global pause/resume so any new
// plist is covered automatically without touching the JOBS array.
function discoverLaunchdJobs() {
  const byLabel = new Map();
  const scan = (dir) => {
    try {
      const files = fs.readdirSync(dir).filter(f =>
        f.startsWith('com.m13v.social-') && f.endsWith('.plist')
      );
      for (const f of files) {
        try {
          const xml = fs.readFileSync(path.join(dir, f), 'utf8');
          const { label, scriptPath } = parsePlist(xml);
          if (!label) continue;
          if (!byLabel.has(label)) {
            byLabel.set(label, { label, plist: f, scriptPath });
          }
        } catch {}
      }
    } catch {}
  };
  scan(LAUNCHD_DIR);
  scan(path.join(os.homedir(), 'Library', 'LaunchAgents'));
  return [...byLabel.values()];
}

function isPaused() {
  const all = discoverLaunchdJobs();
  if (!all.length) return false;
  return all.every(job => !isJobLoaded(job.label));
}

function pauseAll() {
  const killed = [];
  const all = discoverLaunchdJobs();
  for (const job of all) {
    const agentLink = getLaunchAgentPath(job.plist);
    if (isJobLoaded(job.label)) {
      try { execSync(`launchctl unload "${agentLink}"`, { stdio: 'pipe' }); } catch {}
    }
    if (job.scriptPath) {
      try {
        const out = execSync(`pgrep -f "${job.scriptPath}"`, { stdio: 'pipe' }).toString().trim();
        if (out.length) {
          for (const pidStr of out.split('\n')) {
            const pid = parseInt(pidStr, 10);
            if (!isNaN(pid)) {
              try { process.kill(pid, 'SIGTERM'); killed.push(pid); } catch {}
            }
          }
        }
      } catch {}
      const scriptBase = path.basename(job.scriptPath).replace(/\.(sh|py|js)$/, '');
      try { execSync(`pkill -f "claude.*${scriptBase}" 2>/dev/null`, { stdio: 'pipe' }); } catch {}
    }
  }
  // Also kill helper scripts that may be running outside a launchd job
  try { execSync('pkill -f "social-autoposter/scripts/" 2>/dev/null', { stdio: 'pipe' }); } catch {}
  try { execSync('pkill -f "social-autoposter/seo/" 2>/dev/null', { stdio: 'pipe' }); } catch {}
  return killed;
}

function resumeAll() {
  const agentDir = path.join(os.homedir(), 'Library', 'LaunchAgents');
  fs.mkdirSync(agentDir, { recursive: true });
  const all = discoverLaunchdJobs();
  for (const job of all) {
    if (isJobLoaded(job.label)) continue;
    const agentLink = getLaunchAgentPath(job.plist);
    const plistSrc = path.join(LAUNCHD_DIR, job.plist);
    let loadPath = null;
    if (fs.existsSync(agentLink)) {
      // Already installed (real file or symlink) — load as-is so in-place
      // edits to the installed plist are preserved.
      loadPath = agentLink;
    } else if (fs.existsSync(plistSrc)) {
      try {
        fs.symlinkSync(plistSrc, agentLink);
        loadPath = agentLink;
      } catch {}
    }
    if (loadPath) {
      try { execSync(`launchctl load "${loadPath}"`, { stdio: 'pipe' }); } catch {}
    }
  }
}

function deriveName(label) {
  return label.replace(/^com\.m13v\.social-/, '')
    .split('-')
    .map(s => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

function getPidsForScriptPath(scriptPath) {
  if (!scriptPath) return [];
  try {
    const out = execSync(`pgrep -f "${scriptPath}"`, { stdio: 'pipe' }).toString().trim();
    return out.length > 0 ? out.split('\n').map(p => parseInt(p, 10)).filter(n => !isNaN(n)) : [];
  } catch { return []; }
}

// Returns a display string for a plist's schedule, handling both StartInterval
// (numeric seconds) and StartCalendarInterval (hour/minute cron). Returns null
// if neither is present.
function getPlistSchedule(plistPath) {
  try {
    const xml = fs.readFileSync(plistPath, 'utf8');
    const si = xml.match(/<key>StartInterval<\/key>\s*<integer>(\d+)<\/integer>/);
    if (si) {
      const secs = parseInt(si[1], 10);
      if (secs % 3600 === 0) return `every ${secs / 3600}h`;
      if (secs % 60 === 0) return `every ${secs / 60}m`;
      return `every ${secs}s`;
    }
    // StartCalendarInterval can be either a single <dict> or an <array> of
    // <dict>s. Match each shape explicitly so nested <key> tags inside the
    // inner dicts don't break a generic capture.
    let entries = null;
    const arrM = xml.match(/<key>StartCalendarInterval<\/key>\s*<array>([\s\S]*?)<\/array>/);
    if (arrM) {
      entries = [...arrM[1].matchAll(/<dict>([\s\S]*?)<\/dict>/g)].map(m => m[1]);
    } else {
      const dictM = xml.match(/<key>StartCalendarInterval<\/key>\s*<dict>([\s\S]*?)<\/dict>/);
      if (dictM) entries = [dictM[1]];
    }
    if (!entries || !entries.length) return null;
    const parts = entries.map(body => {
      const h = body.match(/<key>Hour<\/key>\s*<integer>(\d+)<\/integer>/);
      const m = body.match(/<key>Minute<\/key>\s*<integer>(\d+)<\/integer>/);
      if (h && m) return `${h[1].padStart(2, '0')}:${m[1].padStart(2, '0')}`;
      if (h) return `${h[1].padStart(2, '0')}:00`;
      if (m) return `:${m[1].padStart(2, '0')}`;
      return null;
    }).filter(Boolean);
    if (!parts.length) return null;
    // Collapse long lists: show first 3 then "+N more"
    if (parts.length <= 4) return parts.join(', ');
    return parts.slice(0, 3).join(', ') + ` +${parts.length - 3} more`;
  } catch { return null; }
}

// Resolve a job label to a normalized descriptor usable by every per-job
// endpoint. Looks up static JOBS first (for matrix metadata) and falls back to
// discovered plists so newly added jobs work without touching JOBS.
function findJob(label) {
  const staticJob = JOBS.find(j => j.label === label);
  if (staticJob) {
    return {
      label: staticJob.label,
      plist: staticJob.plist,
      scriptPath: path.join(DEST, 'skill', staticJob.script),
      scriptBasename: staticJob.script,
      name: staticJob.name,
      type: staticJob.type,
      platform: staticJob.platform,
      logPrefix: staticJob.logPrefix,
      matrix: true,
    };
  }
  const discovered = discoverLaunchdJobs().find(j => j.label === label);
  if (!discovered) return null;
  const scriptBasename = discovered.scriptPath ? path.basename(discovered.scriptPath) : null;
  return {
    label: discovered.label,
    plist: discovered.plist,
    scriptPath: discovered.scriptPath,
    scriptBasename,
    name: deriveName(discovered.label),
    type: 'Other',
    platform: 'all',
    logPrefix: scriptBasename ? scriptBasename.replace(/\.(sh|py|js)$/, '-') : null,
    matrix: false,
  };
}

// --- API Routes ---

function handleApi(req, res) {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const p = url.pathname;

  // GET /api/status
  if (p === '/api/status' && req.method === 'GET') {
    const jobs = JOBS.map(job => {
      const plistPath = path.join(LAUNCHD_DIR, job.plist);
      const loaded = isJobLoaded(job.label);
      const pids = getJobPids(job.script);
      const running = pids.length > 0;
      const interval = getPlistInterval(plistPath);
      const lastLog = getLastLog(job);
      // status: 'running' (process active), 'scheduled' (loaded, waiting), 'stopped' (not loaded)
      const status = running ? 'running' : loaded ? 'scheduled' : 'stopped';
      return {
        label: job.label,
        name: job.name,
        type: job.type,
        platform: job.platform,
        script: job.script,
        loaded,
        running,
        pids,
        status,
        interval,
        lastRun: lastLog.time,
        lastLogFile: lastLog.file,
        plistFile: job.plist,
      };
    });

    // Discovered jobs that aren't in the static matrix. These get a flat row
    // in the "Other Jobs" table so they're visible and controllable in the UI.
    const matrixLabels = new Set(JOBS.map(j => j.label));
    const discovered = discoverLaunchdJobs();
    const otherJobs = discovered
      .filter(d => !matrixLabels.has(d.label))
      .map(d => {
        const loaded = isJobLoaded(d.label);
        const pids = getPidsForScriptPath(d.scriptPath);
        const running = pids.length > 0;
        const status = running ? 'running' : loaded ? 'scheduled' : 'stopped';
        const scriptBasename = d.scriptPath ? path.basename(d.scriptPath) : null;
        const logPrefix = scriptBasename ? scriptBasename.replace(/\.(sh|py|js)$/, '-') : null;
        const lastLog = logPrefix ? getLastLog({ logPrefix }) : { file: null, time: null };
        // Prefer repo plist for schedule; fall back to installed
        let plistPath = path.join(LAUNCHD_DIR, d.plist);
        if (!fs.existsSync(plistPath)) plistPath = getLaunchAgentPath(d.plist);
        const schedule = getPlistSchedule(plistPath);
        return {
          label: d.label,
          name: deriveName(d.label),
          script: scriptBasename,
          loaded,
          running,
          pids,
          status,
          schedule,
          lastRun: lastLog.time,
          lastLogFile: lastLog.file,
          plistFile: d.plist,
        };
      })
      .sort((a, b) => a.name.localeCompare(b.name));

    const pending = psql("SELECT COUNT(*) FROM replies WHERE status='pending'");
    return json(res, { jobs, otherJobs, pendingReplies: pending ? parseInt(pending, 10) : null, paused: isPaused() });
  }

  // POST /api/pause
  if (p === '/api/pause' && req.method === 'POST') {
    const killed = pauseAll();
    return json(res, { paused: true, killedPids: killed });
  }

  // POST /api/resume
  if (p === '/api/resume' && req.method === 'POST') {
    resumeAll();
    return json(res, { paused: false });
  }

  // POST /api/jobs/:label/toggle
  const toggleMatch = p.match(/^\/api\/jobs\/([^/]+)\/toggle$/);
  if (toggleMatch && req.method === 'POST') {
    const label = decodeURIComponent(toggleMatch[1]);
    const job = findJob(label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    const plistSrc = path.join(LAUNCHD_DIR, job.plist);
    const agentLink = getLaunchAgentPath(job.plist);
    const loaded = isJobLoaded(label);
    try {
      if (loaded) {
        execSync(`launchctl unload "${agentLink}"`, { stdio: 'pipe' });
        // Only unlink symlinks. Real-file installed plists (e.g. daily-report)
        // must be preserved so the job can be toggled back on.
        try {
          const st = fs.lstatSync(agentLink);
          if (st.isSymbolicLink()) fs.unlinkSync(agentLink);
        } catch {}
      } else {
        fs.mkdirSync(path.dirname(agentLink), { recursive: true });
        if (!fs.existsSync(agentLink)) {
          if (!fs.existsSync(plistSrc)) return json(res, { error: 'No plist source' }, 404);
          fs.symlinkSync(plistSrc, agentLink);
        }
        execSync(`launchctl load "${agentLink}"`, { stdio: 'pipe' });
      }
      return json(res, { loaded: !loaded });
    } catch (e) {
      return json(res, { error: e.message }, 500);
    }
  }

  // POST /api/jobs/:label/run
  const runMatch = p.match(/^\/api\/jobs\/([^/]+)\/run$/);
  if (runMatch && req.method === 'POST') {
    const label = decodeURIComponent(runMatch[1]);
    const job = findJob(label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    // Spawn exactly what the plist would run. Works for .sh, .py, .js without
    // needing to hardcode an interpreter.
    let plistPath = path.join(LAUNCHD_DIR, job.plist);
    if (!fs.existsSync(plistPath)) plistPath = getLaunchAgentPath(job.plist);
    try {
      const xml = fs.readFileSync(plistPath, 'utf8');
      let cmd = null;
      let args = [];
      const argsM = xml.match(/<key>ProgramArguments<\/key>\s*<array>([\s\S]*?)<\/array>/);
      if (argsM) {
        const parts = [...argsM[1].matchAll(/<string>([^<]+)<\/string>/g)].map(m => m[1]);
        if (parts.length) { cmd = parts[0]; args = parts.slice(1); }
      }
      if (!cmd) {
        const progM = xml.match(/<key>Program<\/key>\s*<string>([^<]+)<\/string>/);
        if (progM) cmd = progM[1];
      }
      if (!cmd) return json(res, { error: 'No executable in plist' }, 500);
      const jobEnv = { ...process.env, ...loadEnv(), HOME: os.homedir() };
      delete jobEnv.CLAUDECODE;
      const child = spawn(cmd, args, { detached: true, stdio: 'ignore', env: jobEnv });
      child.unref();
      return json(res, { started: true, pid: child.pid });
    } catch (e) {
      return json(res, { error: e.message }, 500);
    }
  }

  // POST /api/jobs/:label/stop
  const stopMatch = p.match(/^\/api\/jobs\/([^/]+)\/stop$/);
  if (stopMatch && req.method === 'POST') {
    const label = decodeURIComponent(stopMatch[1]);
    const job = findJob(label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    const pids = getPidsForScriptPath(job.scriptPath);
    for (const pid of pids) {
      try { process.kill(pid, 'SIGTERM'); } catch {}
    }
    if (job.scriptBasename) {
      const base = job.scriptBasename.replace(/\.(sh|py|js)$/, '');
      try { execSync(`pkill -f "claude.*${base}" 2>/dev/null`, { stdio: 'pipe' }); } catch {}
    }
    return json(res, { stopped: true, killedPids: pids });
  }

  // POST /api/jobs/:label/interval
  const intervalMatch = p.match(/^\/api\/jobs\/([^/]+)\/interval$/);
  if (intervalMatch && req.method === 'POST') {
    return readBody(req).then(body => {
      const { interval } = JSON.parse(body);
      const label = decodeURIComponent(intervalMatch[1]);
      const job = findJob(label);
      if (!job) return json(res, { error: 'Unknown job' }, 404);
      // Prefer editing the repo plist so git tracks the change; fall back to
      // the installed file if the repo doesn't have a copy.
      let plistPath = path.join(LAUNCHD_DIR, job.plist);
      if (!fs.existsSync(plistPath)) plistPath = getLaunchAgentPath(job.plist);
      let xml;
      try { xml = fs.readFileSync(plistPath, 'utf8'); }
      catch (e) { return json(res, { error: e.message }, 500); }
      if (!/<key>StartInterval<\/key>/.test(xml)) {
        return json(res, { error: 'Job uses StartCalendarInterval; interval not settable here' }, 400);
      }
      xml = xml.replace(
        /(<key>StartInterval<\/key>\s*<integer>)\d+(<\/integer>)/,
        `$1${interval}$2`
      );
      fs.writeFileSync(plistPath, xml);
      // Reload if currently loaded so the new interval takes effect
      const agentLink = getLaunchAgentPath(job.plist);
      if (isJobLoaded(label)) {
        try {
          execSync(`launchctl unload "${agentLink}"`, { stdio: 'pipe' });
          execSync(`launchctl load "${agentLink}"`, { stdio: 'pipe' });
        } catch {}
      }
      return json(res, { interval });
    }).catch(e => json(res, { error: e.message }, 400));
  }

  // POST /api/phase/:type/interval - set interval for ALL jobs of a given type
  const phaseMatch = p.match(/^\/api\/phase\/([^/]+)\/interval$/);
  if (phaseMatch && req.method === 'POST') {
    return readBody(req).then(body => {
      const { interval } = JSON.parse(body);
      const jobType = decodeURIComponent(phaseMatch[1]);
      const phaseJobs = JOBS.filter(j => j.type === jobType);
      if (!phaseJobs.length) return json(res, { error: 'Unknown phase' }, 404);
      const results = [];
      for (const job of phaseJobs) {
        const plistPath = path.join(LAUNCHD_DIR, job.plist);
        try {
          let xml = fs.readFileSync(plistPath, 'utf8');
          xml = xml.replace(
            /(<key>StartInterval<\/key>\s*<integer>)\d+(<\/integer>)/,
            `$1${interval}$2`
          );
          fs.writeFileSync(plistPath, xml);
          // Reload if currently loaded
          const agentLink = getLaunchAgentPath(job.plist);
          if (isJobLoaded(job.label)) {
            try {
              execSync(`launchctl unload "${agentLink}"`, { stdio: 'pipe' });
              try { fs.unlinkSync(agentLink); } catch {}
              fs.symlinkSync(plistPath, agentLink);
              execSync(`launchctl load "${agentLink}"`, { stdio: 'pipe' });
            } catch {}
          }
          results.push({ label: job.label, interval });
        } catch (e) {
          results.push({ label: job.label, error: e.message });
        }
      }
      return json(res, { phase: jobType, interval, updated: results });
    }).catch(e => json(res, { error: e.message }, 400));
  }

  // GET /api/logs
  if (p === '/api/logs' && req.method === 'GET') {
    const jobFilter = url.searchParams.get('job');
    try {
      let files = fs.readdirSync(LOG_DIR)
        .filter(f => f.endsWith('.log') && !f.startsWith('launchd-'))
        .sort().reverse();
      if (jobFilter) {
        // Match against static matrix jobs by display name, or any discovered
        // launchd job by label/name. Same derivation as /api/status so the
        // Logs tab dropdown can filter every pipeline, not just the matrix.
        const wanted = jobFilter.toLowerCase();
        const staticJob = JOBS.find(j => j.name.toLowerCase() === wanted);
        let logPrefix = staticJob ? staticJob.logPrefix : null;
        let isPostRow = staticJob && !staticJob.logPrefix;
        if (!staticJob) {
          const discovered = discoverLaunchdJobs().find(d =>
            d.label.toLowerCase() === wanted ||
            deriveName(d.label).toLowerCase() === wanted
          );
          if (discovered && discovered.scriptPath) {
            const basename = path.basename(discovered.scriptPath);
            logPrefix = basename.replace(/\.(sh|py|js)$/, '-');
          }
        }
        if (logPrefix) {
          files = files.filter(f => f.startsWith(logPrefix));
        } else if (isPostRow) {
          files = files.filter(f => /^\d{4}-\d{2}-\d{2}_/.test(f));
        }
      }
      return json(res, { files: files.slice(0, 50) });
    } catch { return json(res, { files: [] }); }
  }

  // GET /api/logs/:filename
  const logFileMatch = p.match(/^\/api\/logs\/(.+)$/);
  if (logFileMatch && req.method === 'GET' && logFileMatch[1] !== 'stream') {
    const fname = decodeURIComponent(logFileMatch[1]);
    // Prevent path traversal
    if (fname.includes('..') || fname.includes('/')) return json(res, { error: 'Invalid' }, 400);
    const fpath = path.join(LOG_DIR, fname);
    try {
      const content = fs.readFileSync(fpath, 'utf8');
      // Return last 500 lines
      const lines = content.split('\n');
      return json(res, { file: fname, content: lines.slice(-500).join('\n'), totalLines: lines.length });
    } catch { return json(res, { error: 'Not found' }, 404); }
  }

  // GET /api/logs/stream (SSE)
  if (p === '/api/logs/stream' && req.method === 'GET') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive',
    });
    // Find the most recent log file
    const files = fs.readdirSync(LOG_DIR)
      .filter(f => f.endsWith('.log') && !f.startsWith('launchd-'))
      .sort().reverse();
    if (!files.length) {
      res.write('data: No log files found\n\n');
      return;
    }
    const logFile = path.join(LOG_DIR, files[0]);
    let pos = 0;
    try {
      const stat = fs.statSync(logFile);
      pos = Math.max(0, stat.size - 4096); // Start from last 4KB
    } catch {}

    const sendNew = () => {
      try {
        const stat = fs.statSync(logFile);
        if (stat.size > pos) {
          const stream = fs.createReadStream(logFile, { start: pos, encoding: 'utf8' });
          let chunk = '';
          stream.on('data', d => chunk += d);
          stream.on('end', () => {
            pos = stat.size;
            if (chunk) res.write(`data: ${JSON.stringify(chunk)}\n\n`);
          });
        }
      } catch {}
    };

    sendNew();
    const watcher = fs.watch(LOG_DIR, () => sendNew());
    const interval = setInterval(sendNew, 5000);
    req.on('close', () => { watcher.close(); clearInterval(interval); });
    return;
  }

  // GET /api/config
  if (p === '/api/config' && req.method === 'GET') {
    try {
      const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
      return json(res, config);
    } catch (e) { return json(res, { error: e.message }, 500); }
  }

  // POST /api/config
  if (p === '/api/config' && req.method === 'POST') {
    return readBody(req).then(body => {
      const config = JSON.parse(body);
      fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2) + '\n');
      return json(res, { saved: true });
    }).catch(e => json(res, { error: e.message }, 400));
  }

  // GET /api/env
  if (p === '/api/env' && req.method === 'GET') {
    const vars = loadEnv();
    const masked = {};
    for (const [k, v] of Object.entries(vars)) {
      masked[k] = v.length > 8 ? v.slice(0, 4) + '****' + v.slice(-4) : '****';
    }
    return json(res, masked);
  }

  // POST /api/env
  if (p === '/api/env' && req.method === 'POST') {
    return readBody(req).then(body => {
      const updates = JSON.parse(body);
      // Read existing, update keys
      let raw = '';
      try { raw = fs.readFileSync(ENV_FILE, 'utf8'); } catch {}
      const lines = raw.split('\n');
      const existing = new Set();
      const newLines = lines.map(line => {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith('#')) return line;
        const eq = trimmed.indexOf('=');
        if (eq < 0) return line;
        const key = trimmed.slice(0, eq);
        if (key in updates) {
          existing.add(key);
          return `${key}=${updates[key]}`;
        }
        return line;
      });
      // Add new keys
      for (const [k, v] of Object.entries(updates)) {
        if (!existing.has(k)) newLines.push(`${k}=${v}`);
      }
      fs.writeFileSync(ENV_FILE, newLines.join('\n'));
      return json(res, { saved: true });
    }).catch(e => json(res, { error: e.message }, 400));
  }

  // GET /api/pending
  if (p === '/api/pending' && req.method === 'GET') {
    const count = psql("SELECT COUNT(*) FROM replies WHERE status='pending'");
    const byPlatform = psql("SELECT json_agg(row_to_json(r)) FROM (SELECT platform, COUNT(*) as count FROM replies WHERE status='pending' GROUP BY platform) r");
    const recent = psql("SELECT json_agg(row_to_json(r)) FROM (SELECT id, platform, their_author, their_content, status FROM replies WHERE status='pending' ORDER BY discovered_at DESC LIMIT 20) r");
    const statusCounts = psql("SELECT json_agg(row_to_json(r)) FROM (SELECT status, COUNT(*) as count FROM replies GROUP BY status ORDER BY status) r");
    return json(res, {
      count: count ? parseInt(count, 10) : null,
      byPlatform: byPlatform ? JSON.parse(byPlatform) : [],
      recent: recent ? JSON.parse(recent) : [],
      statusCounts: statusCounts ? JSON.parse(statusCounts) : [],
    });
  }

  // POST /api/webhooks/octolens
  if (p === '/api/webhooks/octolens' && req.method === 'POST') {
    return readBody(req).then(body => {
      const payload = JSON.parse(body);
      const mentions = Array.isArray(payload) ? payload : (payload.mentions || payload.data || [payload]);
      const dbUrl = getDbUrl();
      if (!dbUrl) return json(res, { error: 'No DATABASE_URL' }, 500);

      let inserted = 0;
      for (const m of mentions) {
        if (!m.url) continue;
        // Map Octolens source to our platform names
        const platform = (m.source || 'unknown').replace('twitter', 'twitter').replace('reddit_comment', 'reddit');
        const tags = Array.isArray(m.tags) ? m.tags.join(',') : (m.tags || '');
        const keywords = Array.isArray(m.keywords)
          ? m.keywords.map(k => k.keyword || k).join(',')
          : '';
        // Insert into octolens_mentions table
        const q = `INSERT INTO octolens_mentions (octolens_id, platform, url, title, body, author, author_url, author_followers, sentiment, tags, keywords, source_timestamp, relevance) VALUES (${parseInt(m.id) || 0}, '${(platform).replace(/'/g, "''")}', '${(m.url || '').replace(/'/g, "''")}', '${(m.title || '').replace(/'/g, "''")}', '${(m.body || '').slice(0, 2000).replace(/'/g, "''")}', '${(m.author || '').replace(/'/g, "''")}', '${(m.authorUrl || '').replace(/'/g, "''")}', ${parseInt(m.authorFollowers) || 0}, '${(m.sentiment || '').replace(/'/g, "''")}', '${tags.replace(/'/g, "''")}', '${keywords.replace(/'/g, "''")}', '${(m.timestamp || new Date().toISOString()).replace(/'/g, "''")}', '${(m.relevance || '').replace(/'/g, "''")}') ON CONFLICT (octolens_id) DO NOTHING`;
        try {
          psql(q);
          inserted++;
        } catch (e) {
          console.error('Failed to insert mention:', m.id, e.message);
        }
      }

      // Log webhook receipt
      const logFile = path.join(LOG_DIR, `octolens-webhook-${new Date().toISOString().slice(0, 10)}.log`);
      const logLine = `[${new Date().toISOString()}] Received ${mentions.length} mentions, inserted ${inserted}\n`;
      try { fs.appendFileSync(logFile, logLine); } catch {}

      return json(res, { received: mentions.length, inserted });
    }).catch(e => {
      console.error('Octolens webhook error:', e.message);
      return json(res, { error: e.message }, 400);
    });
  }

  // GET /api/webhooks/octolens/pending
  if (p === '/api/webhooks/octolens/pending' && req.method === 'GET') {
    const count = psql("SELECT COUNT(*) FROM octolens_mentions WHERE status = 'pending'");
    const recent = psql("SELECT json_agg(row_to_json(r)) FROM (SELECT id, platform, url, author, sentiment, tags, keywords, source_timestamp FROM octolens_mentions WHERE status = 'pending' ORDER BY source_timestamp DESC LIMIT 20) r");
    return json(res, {
      count: count ? parseInt(count, 10) : 0,
      mentions: recent ? JSON.parse(recent) : [],
    });
  }

  return json(res, { error: 'Not found' }, 404);
}

// --- HTML Dashboard ---

const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Social Autoposter</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: #0a0a0a; color: #e5e5e5; min-height: 100vh; }
  .header { padding: 20px 24px; border-bottom: 1px solid #262626; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 20px; font-weight: 600; }
  .header .pending { background: #7c3aed; color: white; padding: 4px 12px; border-radius: 12px; font-size: 13px; }
  .tabs { display: flex; gap: 0; border-bottom: 1px solid #262626; padding: 0 24px; }
  .tab { padding: 12px 20px; cursor: pointer; color: #a3a3a3; font-size: 14px; border-bottom: 2px solid transparent; transition: all 0.15s; }
  .tab:hover { color: #e5e5e5; }
  .tab.active { color: #e5e5e5; border-bottom-color: #7c3aed; }
  .content { padding: 24px; }
  .matrix-wrapper { overflow-x: auto; }
  .matrix-table { width: 100%; border-collapse: collapse; background: #171717; border: 1px solid #262626; border-radius: 12px; overflow: hidden; }
  .matrix-table th { text-align: center; padding: 12px 16px; font-size: 12px; font-weight: 500; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #262626; background: #0f0f0f; }
  .matrix-table th.row-header { width: 90px; }
  .matrix-table th.freq-header { width: 90px; }
  .freq-cell { text-align: center; vertical-align: middle; background: #0f0f0f; }
  .freq-cell select { font-size: 12px; padding: 4px 6px; }
  .matrix-table td { padding: 10px 8px; font-size: 13px; border-bottom: 1px solid #1f1f1f; vertical-align: middle; text-align: center; }
  .matrix-table td.row-label { text-align: left; padding-left: 16px; font-weight: 600; font-size: 14px; color: #e5e5e5; background: #0f0f0f; width: 100px; }
  .matrix-table tr:last-child td { border-bottom: none; }
  .matrix-cell { display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .matrix-cell .badge { font-size: 11px; padding: 2px 8px; cursor: pointer; }
  .matrix-cell .badge:hover { filter: brightness(1.3); }
  .matrix-cell .cell-info { font-size: 11px; color: #6b7280; }
  .matrix-cell .cell-actions { display: flex; gap: 4px; margin-top: 2px; }
  .matrix-cell .cell-actions .btn { padding: 3px 8px; font-size: 11px; }
  .matrix-cell-empty { color: #333; font-size: 20px; }
  .matrix-cell-span { text-align: center; }
  .job-name { font-weight: 600; }
  .badge { padding: 3px 10px; border-radius: 8px; font-size: 12px; font-weight: 500; display: inline-block; }
  .badge.running {
    background: linear-gradient(135deg, #0ea5e9 0%, #22d3ee 100%);
    color: #ffffff;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-shadow: 0 0 6px rgba(255,255,255,0.45);
    animation: runningPulse 1.1s cubic-bezier(0.4, 0, 0.6, 1) infinite;
  }
  .badge.running::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #ffffff;
    margin-right: 7px;
    vertical-align: middle;
    box-shadow: 0 0 6px #ffffff;
    animation: runningDot 1.1s ease-in-out infinite;
  }
  .badge.scheduled { background: #064e3b; color: #6ee7b7; }
  .badge.stopped { background: #292524; color: #a3a3a3; }
  @keyframes runningPulse {
    0%   { box-shadow: 0 0 0 0 rgba(34, 211, 238, 0.75), 0 0 10px rgba(14, 165, 233, 0.55); transform: scale(1); }
    60%  { box-shadow: 0 0 0 10px rgba(34, 211, 238, 0), 0 0 18px rgba(34, 211, 238, 0.85); transform: scale(1.05); }
    100% { box-shadow: 0 0 0 0 rgba(34, 211, 238, 0), 0 0 10px rgba(14, 165, 233, 0.55); transform: scale(1); }
  }
  @keyframes runningDot {
    0%, 100% { transform: scale(1);   opacity: 1;    box-shadow: 0 0 6px #ffffff; }
    50%      { transform: scale(1.5); opacity: 0.85; box-shadow: 0 0 14px #ffffff, 0 0 22px #22d3ee; }
  }
  .job-actions { display: flex; gap: 8px; }
  .card { background: #171717; border: 1px solid #262626; border-radius: 12px; padding: 20px; }
  .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .card-title { font-size: 16px; font-weight: 600; }
  .card-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; font-size: 13px; color: #a3a3a3; }
  .card-row span:last-child { color: #e5e5e5; }
  .btn { padding: 8px 16px; border-radius: 8px; border: 1px solid #404040; background: #262626; color: #e5e5e5; cursor: pointer; font-size: 13px; transition: all 0.15s; }
  .btn:hover { background: #333; border-color: #525252; }
  .btn.primary { background: #7c3aed; border-color: #7c3aed; color: white; }
  .btn.primary:hover { background: #6d28d9; }
  .btn.danger { background: #991b1b; border-color: #991b1b; }
  .btn.danger:hover { background: #7f1d1d; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  select { padding: 8px 12px; border-radius: 8px; border: 1px solid #404040; background: #262626; color: #e5e5e5; font-size: 13px; cursor: pointer; }
  .log-viewer { background: #0d0d0d; border: 1px solid #262626; border-radius: 12px; padding: 16px; margin-top: 16px; }
  .log-controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .log-content { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; max-height: 500px; overflow-y: auto; margin-top: 12px; color: #a3a3a3; padding: 12px; background: #0a0a0a; border-radius: 8px; }
  .settings-section { margin-bottom: 24px; }
  .settings-section h3 { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #a3a3a3; }
  .field { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
  .field label { min-width: 140px; font-size: 13px; color: #a3a3a3; }
  .field input { flex: 1; padding: 8px 12px; border-radius: 8px; border: 1px solid #404040; background: #171717; color: #e5e5e5; font-size: 13px; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #065f46; color: #6ee7b7; padding: 12px 20px; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
  .toast.error { background: #7f1d1d; color: #fca5a5; }
  .pending-card { background: #1a1625; border-color: #3b2d63; }
  .reply-item { padding: 8px 0; border-bottom: 1px solid #262626; font-size: 13px; }
  .reply-item:last-child { border-bottom: none; }
  .reply-author { color: #a78bfa; font-weight: 500; }
  .reply-platform { color: #6b7280; font-size: 11px; text-transform: uppercase; }
  .reply-text { color: #d4d4d4; margin-top: 2px; }
  .hidden { display: none; }
  @media (max-width: 600px) { .cards { grid-template-columns: 1fr; } .content { padding: 16px; } }
</style>
</head>
<body>

<div class="header">
  <h1>Social Autoposter</h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <button class="btn" id="pause-btn" onclick="togglePause()" style="font-weight:600;"></button>
    <span class="pending" id="pending-badge">-- pending</span>
  </div>
</div>

<div class="tabs">
  <div class="tab active" data-tab="status">Status</div>
  <div class="tab" data-tab="logs">Logs</div>
  <div class="tab" data-tab="settings">Settings</div>
</div>

<div class="content" id="tab-status">
  <div class="matrix-wrapper">
    <table class="matrix-table">
      <thead>
        <tr>
          <th class="row-header"></th>
          <th class="freq-header">Freq</th>
          <th>Overall</th>
          <th>Reddit</th>
          <th>Twitter</th>
          <th>LinkedIn</th>
          <th>MoltBook</th>
          <th>GitHub</th>
        </tr>
      </thead>
      <tbody id="matrix-body"></tbody>
    </table>
  </div>
  <div id="other-jobs-section" style="margin-top: 24px;"></div>
  <div id="pending-section" style="margin-top: 16px;"></div>
</div>

<div class="content hidden" id="tab-logs">
  <div class="log-controls">
    <select id="log-job-filter">
      <option value="">All jobs</option>
    </select>
    <select id="log-file-select"><option>Loading...</option></select>
    <button class="btn" id="log-refresh-btn">Refresh</button>
  </div>
  <div class="log-viewer">
    <div class="log-content" id="log-content">Select a log file above...</div>
  </div>
</div>

<div class="content hidden" id="tab-settings">
  <div class="settings-section">
    <h3>Accounts</h3>
    <div id="accounts-fields"></div>
  </div>
  <div class="settings-section">
    <h3>Projects</h3>
    <div id="projects-fields"></div>
  </div>
  <div class="settings-section">
    <h3>Subreddits</h3>
    <div class="field">
      <label>Subreddits</label>
      <input type="text" id="subreddits-input" placeholder="comma-separated">
    </div>
  </div>
  <div class="settings-section">
    <h3>Environment Variables</h3>
    <div id="env-fields"></div>
  </div>
  <div style="margin-top: 16px;">
    <button class="btn primary" id="save-settings">Save Settings</button>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const INTERVALS = [
  { label: '30 min', value: 1800 },
  { label: '1 hour', value: 3600 },
  { label: '2 hours', value: 7200 },
  { label: '4 hours', value: 14400 },
  { label: '6 hours', value: 21600 },
  { label: '12 hours', value: 43200 },
  { label: '24 hours', value: 86400 },
];

let currentConfig = null;

function toast(msg, isError) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => t.className = 'toast', 3000);
}

function relTime(iso) {
  if (!iso) return 'Never';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ' + (mins % 60) + 'm ago';
  const days = Math.floor(hrs / 24);
  return days + 'd ago';
}

function fmtInterval(secs) {
  if (!secs) return '--';
  const found = INTERVALS.find(i => i.value === secs);
  return found ? found.label : Math.round(secs / 3600) + 'h';
}

let _initialized = false;
const PLATFORMS = ['all', 'Reddit', 'Twitter', 'LinkedIn', 'MoltBook', 'GitHub'];
const JOB_TYPES = ['Post', 'Engage', 'Stats', 'Audit', 'Octolens'];

function renderCell(job) {
  if (!job) return '<td><span class="matrix-cell-empty">-</span></td>';
  const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
  const runStopBtn = job.running
    ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
    : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';
  const toggleBtn = job.loaded
    ? '<button class="btn danger" onclick="toggleJob(\\'' + job.label + '\\')">Off</button>'
    : '<button class="btn primary" onclick="toggleJob(\\'' + job.label + '\\')">On</button>';

  return '<td data-job="' + job.label + '"><div class="matrix-cell">' +
    '<span class="badge ' + job.status + '" data-field="status">' + statusLabel + '</span>' +
    '<div class="cell-actions">' + runStopBtn + toggleBtn + '</div>' +
  '</div></td>';
}

function renderFreqCell(jobType, interval, jobs) {
  const intervalOptions = INTERVALS.map(i =>
    '<option value="' + i.value + '"' + (i.value === interval ? ' selected' : '') + '>' + i.label + '</option>'
  ).join('');
  // Find the most recent lastRun across all jobs in this phase
  const rowJobs = jobs.filter(j => j.type === jobType);
  let latestRun = null;
  for (const j of rowJobs) {
    if (j.lastRun && (!latestRun || new Date(j.lastRun) > new Date(latestRun))) latestRun = j.lastRun;
  }
  return '<td class="freq-cell" data-freq="' + jobType + '">' +
    '<div class="cell-info" data-field="freq-lastrun">' + relTime(latestRun) + '</div>' +
    '<select onchange="setPhaseInterval(\\'' + jobType + '\\', this.value)">' + intervalOptions + '</select>' +
  '</td>';
}

function buildMatrix(jobs) {
  const map = {};
  jobs.forEach(j => { map[j.type + ':' + j.platform] = j; });

  let html = '';
  for (const jobType of JOB_TYPES) {
    const rowJobs = jobs.filter(j => j.type === jobType);
    const interval = rowJobs.length ? rowJobs[0].interval : null;

    html += '<tr><td class="row-label">' + jobType + '</td>';
    html += renderFreqCell(jobType, interval, jobs);

    for (const plat of PLATFORMS) {
      html += renderCell(map[jobType + ':' + plat] || null);
    }
    html += '</tr>';
  }
  return html;
}

function updateCell(td, job) {
  const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
  const badge = td.querySelector('[data-field="status"]');
  if (badge) { badge.textContent = statusLabel; badge.className = 'badge ' + job.status; }
}

function renderOtherJobRow(job) {
  const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
  const runStopBtn = job.running
    ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
    : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';
  const toggleBtn = job.loaded
    ? '<button class="btn danger" onclick="toggleJob(\\'' + job.label + '\\')">Off</button>'
    : '<button class="btn primary" onclick="toggleJob(\\'' + job.label + '\\')">On</button>';
  return '<tr data-other-job="' + job.label + '">' +
    '<td style="text-align:left;padding-left:16px;">' + job.name + '</td>' +
    '<td style="color:#6b7280;font-size:12px;">' + (job.schedule || '--') + '</td>' +
    '<td style="color:#6b7280;font-size:12px;" data-field="lastrun">' + relTime(job.lastRun) + '</td>' +
    '<td><span class="badge ' + job.status + '" data-field="status">' + statusLabel + '</span></td>' +
    '<td><div class="cell-actions" style="justify-content:center;">' + runStopBtn + toggleBtn + '</div></td>' +
  '</tr>';
}

function buildOtherJobsTable(jobs) {
  if (!jobs || !jobs.length) return '';
  const rows = jobs.map(renderOtherJobRow).join('');
  return '<table class="matrix-table" style="margin-top:8px;">' +
    '<thead><tr>' +
      '<th style="text-align:left;padding-left:16px;">Other Jobs (' + jobs.length + ')</th>' +
      '<th>Schedule</th>' +
      '<th>Last Run</th>' +
      '<th>Status</th>' +
      '<th>Actions</th>' +
    '</tr></thead>' +
    '<tbody id="other-jobs-body">' + rows + '</tbody>' +
  '</table>';
}

function updateOtherJobsInPlace(jobs) {
  for (const job of jobs) {
    const tr = document.querySelector('[data-other-job="' + job.label + '"]');
    if (!tr) return false;
    const badge = tr.querySelector('[data-field="status"]');
    const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
    if (badge) { badge.textContent = statusLabel; badge.className = 'badge ' + job.status; }
    const lastrun = tr.querySelector('[data-field="lastrun"]');
    if (lastrun) lastrun.textContent = relTime(job.lastRun);
    // Swap run/stop and on/off buttons when loaded/running state changes
    const actions = tr.querySelector('.cell-actions');
    if (actions) {
      const runStopBtn = job.running
        ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
        : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';
      const toggleBtn = job.loaded
        ? '<button class="btn danger" onclick="toggleJob(\\'' + job.label + '\\')">Off</button>'
        : '<button class="btn primary" onclick="toggleJob(\\'' + job.label + '\\')">On</button>';
      actions.innerHTML = runStopBtn + toggleBtn;
    }
  }
  return true;
}

function updateFreqCells(jobs) {
  for (const jobType of JOB_TYPES) {
    const td = document.querySelector('[data-freq="' + jobType + '"]');
    if (!td) continue;
    const rowJobs = jobs.filter(j => j.type === jobType);
    let latestRun = null;
    for (const j of rowJobs) {
      if (j.lastRun && (!latestRun || new Date(j.lastRun) > new Date(latestRun))) latestRun = j.lastRun;
    }
    const el = td.querySelector('[data-field="freq-lastrun"]');
    if (el) el.textContent = relTime(latestRun);
  }
}

async function loadStatus() {
  try {
    const [statusRes, pendingRes] = await Promise.all([
      fetch('/api/status'),
      fetch('/api/pending'),
    ]);
    const data = await statusRes.json();
    const pending = await pendingRes.json();

    _paused = !!data.paused;
    updatePauseBtn();

    document.getElementById('pending-badge').textContent =
      (data.pendingReplies != null ? data.pendingReplies : '--') + ' pending';

    const container = document.getElementById('matrix-body');
    const otherSection = document.getElementById('other-jobs-section');
    const otherJobs = data.otherJobs || [];

    if (!_initialized) {
      container.innerHTML = buildMatrix(data.jobs);
      otherSection.innerHTML = buildOtherJobsTable(otherJobs);
      _initialized = true;
    } else {
      data.jobs.forEach(job => {
        const td = container.querySelector('[data-job="' + job.label + '"]');
        if (td) updateCell(td, job);
      });
      updateFreqCells(data.jobs);
      // If rowset changed (new/removed jobs), rebuild the table. Otherwise
      // update rows in place to avoid flicker.
      const existingRows = otherSection.querySelectorAll('[data-other-job]').length;
      if (existingRows !== otherJobs.length) {
        otherSection.innerHTML = buildOtherJobsTable(otherJobs);
      } else {
        updateOtherJobsInPlace(otherJobs);
      }
    }

    // Pending replies - separate full-width section
    const pendingSection = document.getElementById('pending-section');
    if (pending.count != null) {
      const platformBreakdown = (pending.byPlatform || [])
        .map(p => '<div class="card-row"><span>' + p.platform + '</span><span>' + p.count + '</span></div>')
        .join('');
      const recentReplies = (pending.recent || []).slice(0, 10)
        .map(r => '<div class="reply-item"><span class="reply-platform">' + r.platform + '</span> <span class="reply-author">' + (r.their_author || 'unknown') + '</span><div class="reply-text">' + (r.their_content || '').slice(0, 200) + '</div></div>')
        .join('');

      const statusBreakdown = (pending.statusCounts || [])
        .map(s => {
          const colors = { pending: '#eab308', replied: '#22c55e', skipped: '#6b7280', error: '#ef4444' };
          return '<span style="margin-right:16px;font-size:13px;"><span style="color:' + (colors[s.status] || '#a3a3a3') + ';">' + s.status + '</span> ' + s.count + '</span>';
        }).join('');

      pendingSection.innerHTML = '<div class="card pending-card">' +
        '<div class="card-header"><span class="card-title">Pending Replies</span><span class="badge" style="background:#4c1d95;color:#c4b5fd;">' + pending.count + '</span></div>' +
        '<div class="card-row" style="justify-content:flex-start;padding:8px 16px;border-bottom:1px solid #3b2d63;">' + statusBreakdown + '</div>' +
        platformBreakdown +
        (recentReplies ? '<div style="margin-top:12px;border-top:1px solid #3b2d63;padding-top:12px;">' + recentReplies + '</div>' : '') +
      '</div>';
    }
  } catch(e) { toast('Failed to load status: ' + e.message, true); }
}

let _paused = false;

function updatePauseBtn() {
  const btn = document.getElementById('pause-btn');
  if (_paused) {
    btn.textContent = '\\u25B6 Resume All';
    btn.className = 'btn primary';
  } else {
    btn.textContent = '\\u23F8 Pause All';
    btn.className = 'btn danger';
  }
}

async function togglePause() {
  try {
    const endpoint = _paused ? '/api/resume' : '/api/pause';
    const res = await fetch(endpoint, { method: 'POST' });
    const data = await res.json();
    _paused = data.paused;
    updatePauseBtn();
    toast(_paused ? 'All pipelines paused & processes killed' : 'Pipelines resumed');
    loadStatus();
  } catch(e) { toast('Error: ' + e.message, true); }
}

async function toggleJob(label) {
  try {
    await fetch('/api/jobs/' + encodeURIComponent(label) + '/toggle', { method: 'POST' });
    toast('Job toggled');
    loadStatus();
  } catch(e) { toast('Error: ' + e.message, true); }
}

async function runJob(label) {
  try {
    await fetch('/api/jobs/' + encodeURIComponent(label) + '/run', { method: 'POST' });
    toast('Job started');
    loadStatus();
  } catch(e) { toast('Error: ' + e.message, true); }
}

async function stopJob(label) {
  try {
    await fetch('/api/jobs/' + encodeURIComponent(label) + '/stop', { method: 'POST' });
    toast('Job stopped');
    loadStatus();
  } catch(e) { toast('Error: ' + e.message, true); }
}

async function setInterval_(label, value) {
  try {
    await fetch('/api/jobs/' + encodeURIComponent(label) + '/interval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ interval: parseInt(value) }),
    });
    toast('Interval updated');
    loadStatus();
  } catch(e) { toast('Error: ' + e.message, true); }
}

async function setPhaseInterval(jobType, value) {
  try {
    await fetch('/api/phase/' + encodeURIComponent(jobType) + '/interval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ interval: parseInt(value) }),
    });
    toast(jobType + ' interval updated to ' + fmtInterval(parseInt(value)));
    loadStatus();
  } catch(e) { toast('Error: ' + e.message, true); }
}

// Logs
let _logFilterPopulated = false;

async function populateLogFilter() {
  // Build the job filter dropdown from /api/status so every pipeline
  // (matrix row + discovered Other Job) is selectable, not just a hardcoded
  // subset. Keeps the previously-selected value across rebuilds.
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const sel = document.getElementById('log-job-filter');
    const prev = sel.value;
    const matrixNames = (data.jobs || []).map(j => j.name);
    const otherNames = (data.otherJobs || []).map(j => j.name);
    const seen = new Set();
    const names = [...matrixNames, ...otherNames].filter(n => {
      const k = n.toLowerCase();
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
    const opts = ['<option value="">All jobs</option>']
      .concat(names.map(n =>
        '<option value="' + n.toLowerCase() + '">' + n + '</option>'
      ));
    sel.innerHTML = opts.join('');
    if (prev) sel.value = prev;
    _logFilterPopulated = true;
  } catch(e) { /* leave dropdown as-is on failure */ }
}

async function loadLogFiles() {
  if (!_logFilterPopulated) await populateLogFilter();
  const filter = document.getElementById('log-job-filter').value;
  const res = await fetch('/api/logs?job=' + encodeURIComponent(filter));
  const data = await res.json();
  const sel = document.getElementById('log-file-select');
  sel.innerHTML = data.files.map(f => '<option value="' + f + '">' + f + '</option>').join('');
  if (data.files.length) loadLogContent(data.files[0]);
  else { document.getElementById('log-content').textContent = 'No log files for this filter.'; }
}

let _logAutoRefresh = null;
let _currentLogFile = null;

async function loadLogContent(filename) {
  _currentLogFile = filename;
  const res = await fetch('/api/logs/' + encodeURIComponent(filename));
  const data = await res.json();
  const el = document.getElementById('log-content');
  el.textContent = data.content || 'Empty log file';
  el.scrollTop = el.scrollHeight;
}

function startLogAutoRefresh() {
  stopLogAutoRefresh();
  _logAutoRefresh = setInterval(() => {
    if (_currentLogFile) loadLogContent(_currentLogFile);
  }, 5000);
}

function stopLogAutoRefresh() {
  if (_logAutoRefresh) { clearInterval(_logAutoRefresh); _logAutoRefresh = null; }
}

// Settings
async function loadSettings() {
  try {
    const [configRes, envRes] = await Promise.all([fetch('/api/config'), fetch('/api/env')]);
    currentConfig = await configRes.json();
    const env = await envRes.json();

    // Accounts
    const accts = document.getElementById('accounts-fields');
    accts.innerHTML = '';
    if (currentConfig.accounts) {
      for (const [platform, details] of Object.entries(currentConfig.accounts)) {
        const mainField = details.username || details.handle || details.name || '';
        accts.innerHTML += '<div class="field"><label>' + platform + '</label><input type="text" data-account="' + platform + '" value="' + mainField.replace(/"/g, '&quot;') + '"></div>';
      }
    }

    // Projects
    const projs = document.getElementById('projects-fields');
    projs.innerHTML = '';
    if (currentConfig.projects) {
      for (let i = 0; i < currentConfig.projects.length; i++) {
        const p = currentConfig.projects[i];
        projs.innerHTML += '<div class="field"><label>' + p.name + '</label><input type="text" data-project="' + i + '" value="' + (p.description || '').replace(/"/g, '&quot;') + '"></div>';
      }
    }

    // Subreddits
    if (currentConfig.subreddits) {
      document.getElementById('subreddits-input').value = currentConfig.subreddits.join(', ');
    }

    // Env vars
    const envDiv = document.getElementById('env-fields');
    envDiv.innerHTML = '';
    for (const [key, maskedVal] of Object.entries(env)) {
      envDiv.innerHTML += '<div class="field"><label>' + key + '</label><input type="text" data-env="' + key + '" value="' + maskedVal.replace(/"/g, '&quot;') + '" placeholder="' + maskedVal + '"></div>';
    }
  } catch(e) { toast('Failed to load settings: ' + e.message, true); }
}

async function saveSettings() {
  try {
    // Update config
    if (currentConfig) {
      const subs = document.getElementById('subreddits-input').value;
      if (subs) currentConfig.subreddits = subs.split(',').map(s => s.trim()).filter(Boolean);
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentConfig),
      });
    }

    // Update env (only changed values - skip masked ones)
    const envInputs = document.querySelectorAll('[data-env]');
    const envUpdates = {};
    for (const input of envInputs) {
      if (!input.value.includes('****')) {
        envUpdates[input.dataset.env] = input.value;
      }
    }
    if (Object.keys(envUpdates).length) {
      await fetch('/api/env', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(envUpdates),
      });
    }

    toast('Settings saved');
  } catch(e) { toast('Error: ' + e.message, true); }
}

// Tabs
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.content').forEach(c => c.classList.add('hidden'));
    tab.classList.add('active');
    document.getElementById('tab-' + tab.dataset.tab).classList.remove('hidden');
    if (tab.dataset.tab === 'logs') { loadLogFiles(); startLogAutoRefresh(); }
    else { stopLogAutoRefresh(); }
    if (tab.dataset.tab === 'settings') loadSettings();
  });
});

document.getElementById('log-job-filter').addEventListener('change', () => { loadLogFiles(); startLogAutoRefresh(); });
document.getElementById('log-file-select').addEventListener('change', e => loadLogContent(e.target.value));
document.getElementById('log-refresh-btn').addEventListener('click', loadLogFiles);
document.getElementById('save-settings').addEventListener('click', saveSettings);

// Init
loadStatus();
setInterval(loadStatus, 5000);
</script>
</body>
</html>`;

// --- Server ---

const server = http.createServer((req, res) => {
  // CORS for local dev
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  if (req.url === '/' || req.url === '/index.html') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(HTML);
  } else if (req.url.startsWith('/api/')) {
    handleApi(req, res);
  } else {
    res.writeHead(404);
    res.end('Not found');
  }
});

function tryListen(port, maxAttempts = 10) {
  server.listen(port, '127.0.0.1', () => {
    const actualPort = server.address().port;
    console.log(`Social Autoposter dashboard running at http://localhost:${actualPort}`);
    // Auto-open browser
    const { platform } = os;
    const cmd = platform === 'darwin' ? 'open' : platform === 'win32' ? 'start' : 'xdg-open';
    try { execSync(`${cmd} http://localhost:${actualPort}`, { stdio: 'ignore' }); } catch {}
  });
  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE' && maxAttempts > 1) {
      tryListen(port + 1, maxAttempts - 1);
    } else {
      console.error('Could not start server:', err.message);
      process.exit(1);
    }
  });
}

tryListen(PORT);
