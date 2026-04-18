#!/usr/bin/env node
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync, spawn, spawnSync } = require('child_process');

const DEST = path.join(os.homedir(), 'social-autoposter');
const LOG_DIR = path.join(DEST, 'skill', 'logs');
const LAUNCHD_DIR = path.join(DEST, 'launchd');
const CONFIG_FILE = path.join(DEST, 'config.json');
const ENV_FILE = path.join(DEST, '.env');
const PORT = parseInt(process.env.PORT || '3141', 10);

// Matrix: rows = job types, columns = platforms
// Each cell is a job (or null if that combo doesn't exist)
const PLATFORMS = ['Reddit', 'Twitter', 'LinkedIn', 'MoltBook', 'GitHub'];
const JOB_TYPES = ['Post', 'Engage', 'Check Replies', 'DM Outreach', 'DM Replies', 'Link Edit', 'Stats', 'Health Check', 'Octolens'];

const JOBS = [
  // Post row
  { label: 'com.m13v.social-reddit-search', name: 'Reddit', type: 'Post', platform: 'Reddit', script: 'run-reddit-search.sh', logPrefix: 'run-reddit-search-', plist: 'com.m13v.social-reddit-search.plist' },
  { label: 'com.m13v.social-reddit-threads', name: 'Reddit Threads', type: 'Post', platform: 'Reddit', script: 'run-reddit-threads.sh', logPrefix: 'run-reddit-threads-', plist: 'com.m13v.social-reddit-threads.plist' },
  { label: 'com.m13v.social-twitter-cycle', name: 'Twitter', type: 'Post', platform: 'Twitter', script: 'run-twitter-cycle.sh', logPrefix: 'twitter-cycle-', plist: 'com.m13v.social-twitter-cycle.plist' },
  { label: 'com.m13v.social-linkedin', name: 'LinkedIn', type: 'Post', platform: 'LinkedIn', script: 'run-linkedin.sh', logPrefix: 'run-linkedin-', plist: 'com.m13v.social-linkedin.plist' },
  { label: 'com.m13v.social-moltbook', name: 'MoltBook', type: 'Post', platform: 'MoltBook', script: 'run-moltbook.sh', logPrefix: 'run-moltbook-', plist: 'com.m13v.social-moltbook.plist' },
  { label: 'com.m13v.social-github', name: 'GitHub', type: 'Post', platform: 'GitHub', script: 'run-github.sh', logPrefix: 'run-github-', plist: 'com.m13v.social-github.plist' },
  // Engage row (reply to comments on your posts)
  { label: 'com.m13v.social-engage', name: 'Engage Reddit+MB', type: 'Engage', platform: 'Reddit', script: 'engage.sh', logPrefix: 'engage-', plist: 'com.m13v.social-engage.plist' },
  { label: 'com.m13v.social-engage-twitter', name: 'Engage Twitter', type: 'Engage', platform: 'Twitter', script: 'engage-twitter.sh', logPrefix: 'engage-twitter-', plist: 'com.m13v.social-engage-twitter.plist' },
  { label: 'com.m13v.social-engage-linkedin', name: 'Engage LinkedIn', type: 'Engage', platform: 'LinkedIn', script: 'engage-linkedin.sh', logPrefix: 'engage-linkedin-', plist: 'com.m13v.social-engage-linkedin.plist' },
  { label: 'com.m13v.social-github-engage', name: 'GitHub Engage', type: 'Engage', platform: 'GitHub', script: 'github-engage.sh', logPrefix: 'github-engage-', plist: 'com.m13v.social-github-engage.plist' },
  // Check Replies row (discover new inbound replies; feeds Engage)
  { label: 'com.m13v.social-scan-reddit-replies', name: 'Check Replies Reddit', type: 'Check Replies', platform: 'Reddit', script: 'run-scan-reddit-replies.sh', logPrefix: 'run-scan-reddit-replies-', plist: 'com.m13v.social-scan-reddit-replies.plist' },
  { label: 'com.m13v.social-scan-moltbook-replies', name: 'Check Replies MoltBook', type: 'Check Replies', platform: 'MoltBook', script: 'run-scan-moltbook-replies.sh', logPrefix: 'run-scan-moltbook-replies-', plist: 'com.m13v.social-scan-moltbook-replies.plist' },
  // DM Outreach row (initiate DMs to engaged users)
  { label: 'com.m13v.social-dm-outreach-reddit', name: 'DM Outreach Reddit', type: 'DM Outreach', platform: 'Reddit', script: 'dm-outreach-reddit.sh', logPrefix: 'dm-outreach-reddit-', plist: 'com.m13v.social-dm-outreach-reddit.plist' },
  { label: 'com.m13v.social-dm-outreach-twitter', name: 'DM Outreach Twitter', type: 'DM Outreach', platform: 'Twitter', script: 'dm-outreach-twitter.sh', logPrefix: 'dm-outreach-twitter-', plist: 'com.m13v.social-dm-outreach-twitter.plist' },
  { label: 'com.m13v.social-dm-outreach-linkedin', name: 'DM Outreach LinkedIn', type: 'DM Outreach', platform: 'LinkedIn', script: 'dm-outreach-linkedin.sh', logPrefix: 'dm-outreach-linkedin-', plist: 'com.m13v.social-dm-outreach-linkedin.plist' },
  // DM Replies row (respond to incoming DMs)
  { label: 'com.m13v.social-dm-replies-reddit', name: 'DM Replies Reddit', type: 'DM Replies', platform: 'Reddit', script: 'engage-dm-replies-reddit.sh', logPrefix: 'engage-dm-replies-reddit-', plist: 'com.m13v.social-dm-replies-reddit.plist' },
  { label: 'com.m13v.social-dm-replies-twitter', name: 'DM Replies Twitter', type: 'DM Replies', platform: 'Twitter', script: 'engage-dm-replies-twitter.sh', logPrefix: 'engage-dm-replies-twitter-', plist: 'com.m13v.social-dm-replies-twitter.plist' },
  { label: 'com.m13v.social-dm-replies-linkedin', name: 'DM Replies LinkedIn', type: 'DM Replies', platform: 'LinkedIn', script: 'engage-dm-replies-linkedin.sh', logPrefix: 'engage-dm-replies-linkedin-', plist: 'com.m13v.social-dm-replies-linkedin.plist' },
  // Link Edit row (batch update links on published posts)
  { label: 'com.m13v.social-link-edit-reddit', name: 'Link Edit Reddit', type: 'Link Edit', platform: 'Reddit', script: 'link-edit-reddit.sh', logPrefix: 'link-edit-reddit-', plist: 'com.m13v.social-link-edit-reddit.plist' },
  { label: 'com.m13v.social-link-edit-linkedin', name: 'Link Edit LinkedIn', type: 'Link Edit', platform: 'LinkedIn', script: 'link-edit-linkedin.sh', logPrefix: 'link-edit-linkedin-', plist: 'com.m13v.social-link-edit-linkedin.plist' },
  { label: 'com.m13v.social-link-edit-moltbook', name: 'Link Edit MoltBook', type: 'Link Edit', platform: 'MoltBook', script: 'link-edit-moltbook.sh', logPrefix: 'link-edit-moltbook-', plist: 'com.m13v.social-link-edit-moltbook.plist' },
  { label: 'com.m13v.social-link-edit-github', name: 'Link Edit GitHub', type: 'Link Edit', platform: 'GitHub', script: 'link-edit-github.sh', logPrefix: 'link-edit-github-', plist: 'com.m13v.social-link-edit-github.plist' },
  // Stats row
  { label: 'com.m13v.social-stats-reddit', name: 'Stats Reddit', type: 'Stats', platform: 'Reddit', script: 'stats-reddit.sh', logPrefix: 'stats-reddit-', plist: 'com.m13v.social-stats-reddit.plist' },
  { label: 'com.m13v.social-stats-twitter', name: 'Stats Twitter', type: 'Stats', platform: 'Twitter', script: 'stats-twitter.sh', logPrefix: 'stats-twitter-', plist: 'com.m13v.social-stats-twitter.plist' },
  { label: 'com.m13v.social-stats-linkedin', name: 'Stats LinkedIn', type: 'Stats', platform: 'LinkedIn', script: 'stats-linkedin.sh', logPrefix: 'stats-linkedin-', plist: 'com.m13v.social-stats-linkedin.plist' },
  { label: 'com.m13v.social-stats-moltbook', name: 'Stats MoltBook', type: 'Stats', platform: 'MoltBook', script: 'stats-moltbook.sh', logPrefix: 'stats-moltbook-', plist: 'com.m13v.social-stats-moltbook.plist' },
  // Health Check row (verify posts still exist / API health)
  { label: 'com.m13v.social-audit-reddit', name: 'Health Check Reddit', type: 'Health Check', platform: 'Reddit', script: 'audit-reddit.sh', logPrefix: 'audit-reddit-', plist: 'com.m13v.social-audit-reddit.plist' },
  { label: 'com.m13v.social-audit-twitter', name: 'Health Check Twitter', type: 'Health Check', platform: 'Twitter', script: 'audit-twitter.sh', logPrefix: 'audit-twitter-', plist: 'com.m13v.social-audit-twitter.plist' },
  { label: 'com.m13v.social-audit-linkedin', name: 'Health Check LinkedIn', type: 'Health Check', platform: 'LinkedIn', script: 'audit-linkedin.sh', logPrefix: 'audit-linkedin-', plist: 'com.m13v.social-audit-linkedin.plist' },
  { label: 'com.m13v.social-audit-moltbook', name: 'Health Check MoltBook', type: 'Health Check', platform: 'MoltBook', script: 'audit-moltbook.sh', logPrefix: 'audit-moltbook-', plist: 'com.m13v.social-audit-moltbook.plist' },
  // Octolens row
  { label: 'com.m13v.social-octolens-reddit', name: 'Octolens Reddit', type: 'Octolens', platform: 'Reddit', script: 'octolens-reddit.sh', logPrefix: 'octolens-reddit-', plist: 'com.m13v.social-octolens-reddit.plist' },
  { label: 'com.m13v.social-octolens-twitter', name: 'Octolens Twitter', type: 'Octolens', platform: 'Twitter', script: 'octolens-twitter.sh', logPrefix: 'octolens-twitter-', plist: 'com.m13v.social-octolens-twitter.plist' },
  { label: 'com.m13v.social-octolens-linkedin', name: 'Octolens LinkedIn', type: 'Octolens', platform: 'LinkedIn', script: 'octolens-linkedin.sh', logPrefix: 'octolens-linkedin-', plist: 'com.m13v.social-octolens-linkedin.plist' },
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

// Batched snapshot: one launchctl list + one log readdir. Every per-request
// helper reads from this instead of forking its own subprocess.
//
// Running-status detection uses launchd's own PID for the job (column 1 of
// `launchctl list`), NOT a ps/pgrep scan. The pgrep-by-script-path approach
// breaks for wrapper scripts that `exec` into a shared script (e.g.
// octolens-reddit.sh -> octolens.sh --platform reddit): after exec the PID is
// preserved but the command line no longer contains the wrapper's path, so
// pgrep -f misses it. launchd tracks by fork, not argv, so its PID survives
// exec and correctly reflects whether the job is alive.
function buildBatchSnapshot() {
  const loadedLabels = new Set();
  const pidByLabel = new Map();
  try {
    const out = execSync('launchctl list', { stdio: 'pipe', maxBuffer: 8 * 1024 * 1024 }).toString();
    // Format: "PID\tStatus\tLabel\n". PID is "-" when the job is loaded but
    // not currently running. Skip header.
    for (const line of out.split('\n').slice(1)) {
      const parts = line.split('\t');
      if (parts.length < 3) continue;
      const label = parts[2];
      if (!label.startsWith('com.m13v.social-')) continue;
      loadedLabels.add(label);
      const pid = parseInt(parts[0], 10);
      if (!isNaN(pid)) pidByLabel.set(label, pid);
    }
  } catch {}

  const logFiles = (() => {
    try { return fs.readdirSync(LOG_DIR); } catch { return []; }
  })();

  return { loadedLabels, pidByLabel, logFiles };
}

function pidsForLabelFromSnapshot(snap, label) {
  const pid = snap.pidByLabel.get(label);
  return pid ? [pid] : [];
}

function lastLogFromSnapshot(snap, job) {
  const logPrefix = job.logPrefix;
  const matches = snap.logFiles.filter(f => {
    if (!f.endsWith('.log')) return false;
    if (f.startsWith('launchd-')) return false;
    if (logPrefix) return f.startsWith(logPrefix);
    return /^\d{4}-\d{2}-\d{2}_/.test(f);
  }).sort().reverse();
  if (!matches.length) return { file: null, time: null };
  const fname = matches[0];
  const timeStr = logPrefix ? fname.replace(logPrefix, '').replace('.log', '') : fname.replace('.log', '');
  const m = timeStr.match(/(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})/);
  const time = m ? new Date(m[1], m[2]-1, m[3], m[4], m[5], m[6]).toISOString() : null;
  return { file: fname, time };
}

// launchctl load/unload exit 0 even on failure (e.g. "Unload failed: 5:
// Input/output error" when the job is already unloaded, or bootstrap-domain
// mismatches on modern macOS). Use spawnSync so we capture stderr regardless
// of exit code and can detect the silent-failure case.
function runLaunchctl(action, agentLink) {
  const r = spawnSync('launchctl', [action, agentLink], { encoding: 'utf8' });
  const stderr = (r.stderr || '').trim();
  const ok = r.status === 0 && !/failed/i.test(stderr);
  return { ok, stderr, status: r.status };
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

// 5s TTL cache so /api/status polling (typically every 1-2s) doesn't spawn
// a psql subprocess on every hit. Stale-by-5s is fine for the pending-reply
// counter since it only affects the dashboard badge.
let _pendingCache = { at: 0, value: null };
function cachedPendingReplies() {
  const now = Date.now();
  if (now - _pendingCache.at < 5000) return _pendingCache.value;
  const raw = psql("SELECT COUNT(*) FROM replies WHERE status='pending'");
  _pendingCache = { at: now, value: raw ? parseInt(raw, 10) : null };
  return _pendingCache.value;
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

// Returns the PID launchd currently tracks for a given job label, or null if
// the job is loaded-but-idle or not loaded. Uses `launchctl list <label>`,
// which survives `exec` in wrapper scripts (pgrep -f does not).
function getLaunchdPid(label) {
  try {
    const out = execSync(`launchctl list ${label}`, { stdio: 'pipe' }).toString();
    const m = out.match(/"PID"\s*=\s*(\d+);/);
    return m ? parseInt(m[1], 10) : null;
  } catch { return null; }
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
    const snap = buildBatchSnapshot();
    const jobs = JOBS.map(job => {
      const plistPath = path.join(LAUNCHD_DIR, job.plist);
      const loaded = snap.loadedLabels.has(job.label);
      const pids = pidsForLabelFromSnapshot(snap, job.label);
      const running = pids.length > 0;
      const interval = getPlistInterval(plistPath);
      const lastLog = lastLogFromSnapshot(snap, job);
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
        const loaded = snap.loadedLabels.has(d.label);
        const pids = pidsForLabelFromSnapshot(snap, d.label);
        const running = pids.length > 0;
        const status = running ? 'running' : loaded ? 'scheduled' : 'stopped';
        const scriptBasename = d.scriptPath ? path.basename(d.scriptPath) : null;
        const logPrefix = scriptBasename ? scriptBasename.replace(/\.(sh|py|js)$/, '-') : null;
        const lastLog = logPrefix ? lastLogFromSnapshot(snap, { logPrefix }) : { file: null, time: null };
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

    const pending = cachedPendingReplies();
    const allDiscovered = discovered;
    const paused = allDiscovered.length > 0 && allDiscovered.every(j => !snap.loadedLabels.has(j.label));
    return json(res, { jobs, otherJobs, pendingReplies: pending, paused });
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
    const wasLoaded = isJobLoaded(label);
    const intent = !wasLoaded;
    let stderr = '';
    try {
      if (wasLoaded) {
        const r = runLaunchctl('unload', agentLink);
        stderr = r.stderr;
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
        const r = runLaunchctl('load', agentLink);
        stderr = r.stderr;
      }
    } catch (e) {
      return json(res, { error: e.message }, 500);
    }
    // Re-check actual state; launchctl may exit 0 while the action silently failed.
    const nowLoaded = isJobLoaded(label);
    const payload = { loaded: nowLoaded };
    if (nowLoaded !== intent) {
      payload.error = stderr || `launchctl ${wasLoaded ? 'unload' : 'load'} reported success but state did not change`;
      return json(res, payload, 500);
    }
    return json(res, payload);
  }

  // POST /api/jobs/:label/run
  const runMatch = p.match(/^\/api\/jobs\/([^/]+)\/run$/);
  if (runMatch && req.method === 'POST') {
    const label = decodeURIComponent(runMatch[1]);
    const job = findJob(label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    // Route through launchd so the run is tracked by the same mechanism that
    // reports status. Spawning the script directly creates a process launchd
    // doesn't know about, which means /api/status (which reads launchd's PID)
    // would never show it as running.
    try {
      if (!isJobLoaded(label)) {
        return json(res, { error: 'Job not loaded; cannot kickstart' }, 400);
      }
      const target = `gui/${process.getuid()}/${label}`;
      const r = spawnSync('launchctl', ['kickstart', '-p', target], { encoding: 'utf8' });
      if (r.status !== 0) {
        return json(res, { error: (r.stderr || r.stdout || 'kickstart failed').trim() }, 500);
      }
      const pid = parseInt((r.stdout || '').trim(), 10);
      return json(res, { started: true, pid: isNaN(pid) ? null : pid });
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
    const launchdPid = getLaunchdPid(label);
    const target = `gui/${process.getuid()}/${label}`;
    // Ask launchd to SIGKILL the job. launchctl kill targets the PID launchd
    // tracks, which survives exec in wrapper scripts. SIGKILL (not SIGTERM)
    // because some scripts trap TERM (e.g. lock.sh's cleanup trap) and the
    // trap fires but the outer bash keeps waiting on its child, so SIGTERM
    // alone doesn't reliably end the job.
    try { spawnSync('launchctl', ['kill', 'SIGKILL', target]); } catch {}
    if (job.scriptBasename) {
      const base = job.scriptBasename.replace(/\.(sh|py|js)$/, '');
      try { execSync(`pkill -f "claude.*${base}" 2>/dev/null`, { stdio: 'pipe' }); } catch {}
    }
    return json(res, { stopped: true, killedPids: launchdPid ? [launchdPid] : [] });
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

  // GET /api/activity - unified recent-events feed across posts, replies, mentions, dms
  if (p === '/api/activity' && req.method === 'GET') {
    const q = "SELECT json_agg(row_to_json(r)) FROM (" +
      "SELECT * FROM (SELECT posted_at AS occurred_at, 'posted' AS type, platform, our_account AS actor, COALESCE(thread_title, LEFT(our_content, 140)) AS summary, engagement_style AS detail, our_url AS link, ('p' || id) AS key, project_name AS project FROM posts WHERE posted_at IS NOT NULL ORDER BY posted_at DESC LIMIT 40) x1 " +
      "UNION ALL SELECT * FROM (SELECT r2.replied_at, 'replied', r2.platform, r2.their_author, COALESCE(LEFT(r2.our_reply_content, 140), LEFT(r2.their_content, 140)), r2.engagement_style, r2.our_reply_url, ('r' || r2.id), p.project_name FROM replies r2 LEFT JOIN posts p ON p.id = r2.post_id WHERE r2.status='replied' AND r2.replied_at IS NOT NULL ORDER BY r2.replied_at DESC LIMIT 40) x2 " +
      "UNION ALL SELECT * FROM (SELECT COALESCE(r3.processing_at, r3.discovered_at), 'skipped', r3.platform, r3.their_author, LEFT(r3.their_content, 140), r3.skip_reason, r3.their_comment_url, ('s' || r3.id), p.project_name FROM replies r3 LEFT JOIN posts p ON p.id = r3.post_id WHERE r3.status='skipped' ORDER BY COALESCE(r3.processing_at, r3.discovered_at) DESC LIMIT 40) x3 " +
      "UNION ALL SELECT * FROM (SELECT COALESCE(source_timestamp, received_at), 'mention', platform, author, COALESCE(title, LEFT(body, 140)), sentiment, url, ('m' || id), NULL::text FROM octolens_mentions ORDER BY COALESCE(source_timestamp, received_at) DESC LIMIT 40) x4 " +
      "UNION ALL SELECT * FROM (SELECT sent_at, 'dm_sent', platform, their_author, LEFT(our_dm_content, 140), NULL::text, chat_url, ('d' || id), NULL::text FROM dms WHERE status='sent' AND sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 40) x5 " +
      "UNION ALL SELECT * FROM (SELECT completed_at, 'page_published', 'seo', product, keyword, slug, page_url, ('k' || id), product FROM seo_keywords WHERE completed_at IS NOT NULL AND page_url IS NOT NULL ORDER BY completed_at DESC LIMIT 40) x6 " +
      "ORDER BY 1 DESC LIMIT 100) r";
    const rows = psql(q);
    return json(res, { events: rows && rows !== '' ? (JSON.parse(rows) || []) : [] });
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
  .toggle-switch { position: relative; display: inline-block; width: 40px; height: 22px; cursor: pointer; flex-shrink: 0; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-slider { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: #3f3f46; border: 1px solid #52525b; border-radius: 22px; transition: background 0.15s, border-color 0.15s; }
  .toggle-slider::before { content: ''; position: absolute; height: 16px; width: 16px; left: 2px; top: 2px; background: #e5e5e5; border-radius: 50%; transition: transform 0.15s, background 0.15s; box-shadow: 0 1px 3px rgba(0,0,0,0.4); }
  .toggle-switch input:checked + .toggle-slider { background: #10b981; border-color: #10b981; }
  .toggle-switch input:checked + .toggle-slider::before { transform: translateX(18px); background: #ffffff; }
  .toggle-switch:hover .toggle-slider { filter: brightness(1.15); }
  .toggle-switch input:disabled + .toggle-slider { opacity: 0.5; cursor: not-allowed; }
  .toggle-label { font-size: 10px; font-weight: 700; letter-spacing: 0.05em; color: #6b7280; margin-left: 6px; }
  .toggle-label.on { color: #10b981; }
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

  /* Activity tab */
  .activity-controls { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
  .activity-filter-group { display: flex; gap: 6px; flex-wrap: wrap; }
  .activity-chip {
    padding: 4px 10px; border-radius: 999px; font-size: 12px; cursor: pointer;
    border: 1px solid #333; background: #171717; color: #a3a3a3;
    transition: all 0.15s; user-select: none;
  }
  .activity-chip:hover { border-color: #525252; color: #e5e5e5; }
  .activity-chip.active { background: #262626; border-color: #525252; color: #e5e5e5; }
  .activity-chip.active.ev-posted   { background: #064e3b; border-color: #10b981; color: #6ee7b7; }
  .activity-chip.active.ev-replied  { background: #0c4a6e; border-color: #0ea5e9; color: #7dd3fc; }
  .activity-chip.active.ev-skipped  { background: #422006; border-color: #d97706; color: #fbbf24; }
  .activity-chip.active.ev-mention  { background: #1f1f1f; border-color: #737373; color: #d4d4d4; }
  .activity-chip.active.ev-dm_sent  { background: #3b0764; border-color: #a855f7; color: #d8b4fe; }
  .activity-chip.active.ev-page_published { background: #422006; border-color: #f59e0b; color: #fcd34d; }

  .activity-status { display: flex; align-items: center; gap: 6px; margin-left: auto; font-size: 12px; color: #22d3ee; }
  .activity-live-dot {
    width: 8px; height: 8px; border-radius: 50%; background: #22d3ee;
    box-shadow: 0 0 8px #22d3ee;
    animation: activityHeartbeat 1.4s ease-in-out infinite;
  }
  @keyframes activityHeartbeat {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.5; transform: scale(0.7); }
  }

  .activity-wrapper { overflow-x: auto; }
  .activity-table { width: 100%; border-collapse: collapse; background: #171717; border: 1px solid #262626; border-radius: 12px; overflow: hidden; }
  .activity-table th {
    text-align: left; padding: 10px 14px; font-size: 11px; font-weight: 500;
    color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 1px solid #262626; background: #0f0f0f;
  }
  .activity-table td {
    padding: 10px 14px; font-size: 13px; border-bottom: 1px solid #1f1f1f;
    vertical-align: top; color: #d4d4d4;
  }
  .activity-table tr:last-child td { border-bottom: none; }
  .activity-table tr:hover td { background: #1c1c1c; }
  .activity-event-cell { display: flex; flex-direction: column; gap: 4px; white-space: nowrap; }
  .activity-time { color: #6b7280; font-size: 12px; font-variant-numeric: tabular-nums; }
  .activity-platform { color: #a3a3a3; font-size: 12px; text-transform: lowercase; }
  .activity-project { color: #a3a3a3; font-size: 12px; word-break: break-all; }
  .activity-account { color: #e5e5e5; font-size: 12px; font-weight: 500; word-break: break-all; }
  .activity-target { color: #a3a3a3; font-size: 12px; word-break: break-all; }
  .activity-summary { color: #d4d4d4; line-height: 1.4; }
  .activity-detail { color: #737373; font-size: 11px; font-family: 'SF Mono', monospace; word-break: break-word; }
  .activity-link { color: #60a5fa; text-decoration: none; font-size: 14px; opacity: 0.7; }
  .activity-link:hover { opacity: 1; }

  .ev-pill {
    display: inline-block; padding: 2px 8px; border-radius: 6px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.02em; text-transform: lowercase;
  }
  .ev-pill.ev-posted  { background: #064e3b; color: #6ee7b7; }
  .ev-pill.ev-replied { background: #0c4a6e; color: #7dd3fc; }
  .ev-pill.ev-skipped { background: #422006; color: #fbbf24; }
  .ev-pill.ev-mention { background: #262626; color: #d4d4d4; }
  .ev-pill.ev-dm_sent { background: #3b0764; color: #d8b4fe; }
  .ev-pill.ev-page_published { background: #422006; color: #fcd34d; border: 1px solid #f59e0b; }

  .activity-row-new { animation: activityRowFlash 2.6s ease-out; }
  @keyframes activityRowFlash {
    0%   { background: rgba(34, 211, 238, 0.22); box-shadow: inset 3px 0 0 #22d3ee; }
    60%  { background: rgba(34, 211, 238, 0.08); box-shadow: inset 3px 0 0 #22d3ee; }
    100% { background: transparent; box-shadow: inset 3px 0 0 transparent; }
  }

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
  <div class="tab" data-tab="activity">Activity</div>
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

<div class="content hidden" id="tab-activity">
  <div class="activity-controls">
    <div class="activity-filter-group" id="activity-type-filters"></div>
    <div class="activity-filter-group" id="activity-platform-filters"></div>
    <div class="activity-status">
      <span class="activity-live-dot"></span>
      <span id="activity-status-text">live</span>
      <span id="activity-count" style="color:#6b7280;margin-left:8px;"></span>
    </div>
  </div>
  <div class="activity-wrapper">
    <table class="activity-table">
      <thead>
        <tr>
          <th style="width:140px;">Event</th>
          <th style="width:90px;">Platform</th>
          <th style="width:120px;">Project</th>
          <th>What</th>
          <th style="width:280px;">Detail</th>
          <th style="width:40px;"></th>
        </tr>
      </thead>
      <tbody id="activity-body">
        <tr><td colspan="6" style="text-align:center;color:#6b7280;padding:40px;">Loading&hellip;</td></tr>
      </tbody>
    </table>
  </div>
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
const PLATFORMS = ['Reddit', 'Twitter', 'LinkedIn', 'MoltBook', 'GitHub'];
const JOB_TYPES = ['Post', 'Engage', 'Check Replies', 'DM Outreach', 'DM Replies', 'Link Edit', 'Stats', 'Health Check', 'Octolens'];

function renderToggle(label, loaded) {
  return '<label class="toggle-switch" data-field="toggle" title="' + (loaded ? 'On — click to disable' : 'Off — click to enable') + '">' +
    '<input type="checkbox"' + (loaded ? ' checked' : '') + ' onchange="toggleJob(\\'' + label + '\\')">' +
    '<span class="toggle-slider"></span>' +
  '</label>';
}

function renderCell(job) {
  if (!job) return '<td><span class="matrix-cell-empty">-</span></td>';
  const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
  const runStopBtn = job.running
    ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
    : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';

  return '<td data-job="' + job.label + '"><div class="matrix-cell">' +
    '<span class="badge ' + job.status + '" data-field="status">' + statusLabel + '</span>' +
    '<div class="cell-actions">' + renderToggle(job.label, job.loaded) + runStopBtn + '</div>' +
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
  const toggleInput = td.querySelector('[data-field="toggle"] input');
  if (toggleInput && toggleInput.checked !== !!job.loaded) toggleInput.checked = !!job.loaded;
  const actions = td.querySelector('.cell-actions');
  if (actions) {
    const runStopBtn = job.running
      ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
      : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';
    const currentBtn = actions.querySelector('.btn');
    if (currentBtn) currentBtn.outerHTML = runStopBtn;
  }
}

function renderOtherJobRow(job) {
  const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
  const runStopBtn = job.running
    ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
    : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';
  return '<tr data-other-job="' + job.label + '">' +
    '<td style="text-align:left;padding-left:16px;">' + job.name + '</td>' +
    '<td style="color:#6b7280;font-size:12px;">' + (job.schedule || '--') + '</td>' +
    '<td style="color:#6b7280;font-size:12px;" data-field="lastrun">' + relTime(job.lastRun) + '</td>' +
    '<td><span class="badge ' + job.status + '" data-field="status">' + statusLabel + '</span></td>' +
    '<td><div class="cell-actions" style="justify-content:center;">' + renderToggle(job.label, job.loaded) + runStopBtn + '</div></td>' +
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
    const toggleInput = tr.querySelector('[data-field="toggle"] input');
    if (toggleInput && toggleInput.checked !== !!job.loaded) toggleInput.checked = !!job.loaded;
    const actions = tr.querySelector('.cell-actions');
    if (actions) {
      const runStopBtn = job.running
        ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
        : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';
      const currentBtn = actions.querySelector('.btn');
      if (currentBtn) currentBtn.outerHTML = runStopBtn;
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
    const res = await fetch('/api/jobs/' + encodeURIComponent(label) + '/toggle', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) {
      toast('Toggle failed: ' + (data.error || ('HTTP ' + res.status)), true);
    } else {
      toast(data.loaded ? 'Scheduled' : 'Unloaded');
    }
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

// Activity tab
const EVENT_TYPES = ['posted', 'replied', 'skipped', 'mention', 'dm_sent', 'page_published'];
const EVENT_LABELS = { posted: 'posted', replied: 'replied', skipped: 'skipped', mention: 'mention', dm_sent: 'dm sent', page_published: 'page published' };
const ACTIVITY_PLATFORMS = ['reddit', 'twitter', 'linkedin', 'moltbook', 'github', 'seo'];
let _activitySeen = new Set();
let _activityFirstLoad = true;
let _activityTypeFilter = new Set(EVENT_TYPES);
let _activityPlatformFilter = new Set(ACTIVITY_PLATFORMS);
let _activityTimer = null;

function buildActivityFilters() {
  const tEl = document.getElementById('activity-type-filters');
  const pEl = document.getElementById('activity-platform-filters');
  if (!tEl || tEl.children.length) return;
  tEl.innerHTML = EVENT_TYPES.map(t =>
    '<span class="activity-chip ev-' + t + ' active" data-type="' + t + '">' + EVENT_LABELS[t] + '</span>'
  ).join('');
  pEl.innerHTML = ACTIVITY_PLATFORMS.map(p =>
    '<span class="activity-chip active" data-platform="' + p + '">' + p + '</span>'
  ).join('');
  tEl.querySelectorAll('[data-type]').forEach(el => {
    el.addEventListener('click', () => {
      const t = el.dataset.type;
      if (_activityTypeFilter.has(t)) { _activityTypeFilter.delete(t); el.classList.remove('active'); }
      else { _activityTypeFilter.add(t); el.classList.add('active'); }
      renderActivity(_lastActivityEvents || []);
    });
  });
  pEl.querySelectorAll('[data-platform]').forEach(el => {
    el.addEventListener('click', () => {
      const p = el.dataset.platform;
      if (_activityPlatformFilter.has(p)) { _activityPlatformFilter.delete(p); el.classList.remove('active'); }
      else { _activityPlatformFilter.add(p); el.classList.add('active'); }
      renderActivity(_lastActivityEvents || []);
    });
  });
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

let _lastActivityEvents = [];
function renderActivity(events) {
  _lastActivityEvents = events;
  const body = document.getElementById('activity-body');
  if (!body) return;
  const filtered = events.filter(e =>
    _activityTypeFilter.has(e.type) && _activityPlatformFilter.has((e.platform || '').toLowerCase())
  );
  document.getElementById('activity-count').textContent =
    filtered.length + ' of ' + events.length + ' events';
  if (!filtered.length) {
    body.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#6b7280;padding:40px;">No matching events</td></tr>';
    return;
  }
  const rows = filtered.map(e => {
    const isNew = !_activityFirstLoad && !_activitySeen.has(e.key);
    const linkHtml = e.link
      ? '<a class="activity-link" href="' + escapeHtml(e.link) + '" target="_blank" rel="noopener" title="Open">&rarr;</a>'
      : '';
    const timeAbs = e.occurred_at ? new Date(e.occurred_at).toLocaleString() : '';
    return '<tr' + (isNew ? ' class="activity-row-new"' : '') + ' data-key="' + escapeHtml(e.key) + '">' +
      '<td title="' + escapeHtml(timeAbs) + '">' +
        '<div class="activity-event-cell">' +
          '<span class="activity-time">' + escapeHtml(relTime(e.occurred_at)) + '</span>' +
          '<span class="ev-pill ev-' + escapeHtml(e.type) + '">' + escapeHtml(EVENT_LABELS[e.type] || e.type) + '</span>' +
        '</div>' +
      '</td>' +
      '<td class="activity-platform">' + escapeHtml(e.platform || '') + '</td>' +
      '<td class="activity-project">' + escapeHtml(e.project || '') + '</td>' +
      '<td class="activity-account">' + escapeHtml(e.our_account || '') + '</td>' +
      '<td class="activity-target">' + escapeHtml(e.target || '') + '</td>' +
      '<td class="activity-summary">' + escapeHtml(e.summary || '') + '</td>' +
      '<td class="activity-detail">' + escapeHtml(e.detail || '') + '</td>' +
      '<td>' + linkHtml + '</td>' +
    '</tr>';
  }).join('');
  body.innerHTML = rows;
  events.forEach(e => _activitySeen.add(e.key));
  _activityFirstLoad = false;
}

async function loadActivity() {
  try {
    const res = await fetch('/api/activity');
    const data = await res.json();
    renderActivity(data.events || []);
    const el = document.getElementById('activity-status-text');
    if (el) el.textContent = 'live';
  } catch (e) {
    const el = document.getElementById('activity-status-text');
    if (el) el.textContent = 'error';
  }
}

function startActivityAutoRefresh() {
  if (_activityTimer) return;
  loadActivity();
  _activityTimer = setInterval(loadActivity, 5000);
}
function stopActivityAutoRefresh() {
  if (_activityTimer) { clearInterval(_activityTimer); _activityTimer = null; }
}

// Tabs — switching is purely a CSS toggle, so it's instant. Data for each tab
// is preloaded on init (see preloadTabs) and kept rendered while hidden, so
// switching back shows cached content immediately while the active tab's
// timer keeps it fresh.
const _tabLoaded = { logs: false, activity: false, settings: false };
document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.content').forEach(c => c.classList.add('hidden'));
    tab.classList.add('active');
    const name = tab.dataset.tab;
    document.getElementById('tab-' + name).classList.remove('hidden');
    if (name === 'logs') {
      if (!_tabLoaded.logs) { loadLogFiles(); _tabLoaded.logs = true; }
      startLogAutoRefresh();
    } else {
      stopLogAutoRefresh();
    }
    if (name === 'activity') {
      buildActivityFilters();
      if (!_tabLoaded.activity) _tabLoaded.activity = true;
      startActivityAutoRefresh();
    } else {
      stopActivityAutoRefresh();
    }
    if (name === 'settings' && !_tabLoaded.settings) {
      loadSettings();
      _tabLoaded.settings = true;
    }
  });
});

document.getElementById('log-job-filter').addEventListener('change', () => { loadLogFiles(); startLogAutoRefresh(); });
document.getElementById('log-file-select').addEventListener('change', e => loadLogContent(e.target.value));
document.getElementById('log-refresh-btn').addEventListener('click', loadLogFiles);
document.getElementById('save-settings').addEventListener('click', saveSettings);

// Init
loadStatus();
setInterval(loadStatus, 5000);

// Preload every tab so switching never blocks on a fetch. Each loader is
// idempotent; the active tab's timer takes over for ongoing refreshes.
(function preloadTabs() {
  setTimeout(() => {
    try { loadLogFiles(); _tabLoaded.logs = true; } catch {}
    try { buildActivityFilters(); loadActivity(); _tabLoaded.activity = true; } catch {}
    try { loadSettings(); _tabLoaded.settings = true; } catch {}
  }, 100);
})();
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
