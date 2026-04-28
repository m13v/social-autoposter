#!/usr/bin/env node
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { execSync, spawn, spawnSync } = require('child_process');
const pg = require('pg');
const { Pool } = pg;
// Postgres `timestamp without time zone` columns are stored in UTC across this
// repo (DB session tz is GMT and inserts use NOW()). The default node-postgres
// parser interprets naive timestamps as the Node process's LOCAL time, which
// silently shifts every dashboard timestamp by the local offset (e.g. 7h in
// PDT). Force OID 1114 (timestamp) to be parsed as UTC so posted_at,
// engagement_updated_at, status_checked_at, etc. render with the correct
// relative-time on the dashboard. See investigation 2026-04-27 (Cyrano post
// id=20555 displaying "19m ago" for a 7-hour-old post).
pg.types.setTypeParser(1114, str => str === null ? null : new Date(str + 'Z'));
const platform = require('./platform');
const scheduler = require('./scheduler');
const auth = require('./auth');

const DEST = path.join(os.homedir(), 'social-autoposter');
const LOG_DIR = path.join(DEST, 'skill', 'logs');
const SCHED_KIND = platform.scheduler();
const UNIT_DIR = path.join(DEST, SCHED_KIND === 'systemd' ? 'systemd' : 'launchd');
const AGENT_DIR = platform.agentsDir();
const driver = scheduler.driverFor();
const CONFIG_FILE = path.join(DEST, 'config.json');
const ENV_FILE = path.join(DEST, '.env');
const PORT = parseInt(process.env.PORT || '3141', 10);

function unitSrcPath(job) {
  return path.join(UNIT_DIR, driver.unitFileName(job.plist));
}

function agentPath(job) {
  return path.join(AGENT_DIR, driver.unitFileName(job.plist));
}

// Matrix: rows = job types, columns = platforms
// Each cell is a job (or null if that combo doesn't exist)
const PLATFORMS = ['Reddit', 'Twitter', 'LinkedIn', 'MoltBook', 'GitHub'];
const JOB_TYPES = ['Post', 'Engage', 'DM Outreach', 'DM Replies', 'Link Edit', 'Stats', 'Post Audit', 'Octolens'];

const JOBS = [
  // Post row
  { label: 'com.m13v.social-reddit-search', name: 'Reddit', type: 'Post', platform: 'Reddit', script: 'run-reddit-search.sh', logPrefix: 'run-reddit-search-', plist: 'com.m13v.social-reddit-search.plist' },
  { label: 'com.m13v.social-reddit-threads', name: 'Reddit Threads', type: 'Post', platform: 'Reddit', script: 'run-reddit-threads.sh', logPrefix: 'run-reddit-threads-', plist: 'com.m13v.social-reddit-threads.plist' },
  { label: 'com.m13v.social-twitter-cycle', name: 'Twitter', type: 'Post', platform: 'Twitter', script: 'run-twitter-cycle.sh', logPrefix: 'twitter-cycle-', plist: 'com.m13v.social-twitter-cycle.plist' },
  { label: 'com.m13v.social-linkedin', name: 'LinkedIn', type: 'Post', platform: 'LinkedIn', script: 'run-linkedin.sh', logPrefix: 'run-linkedin-', plist: 'com.m13v.social-linkedin.plist' },
  { label: 'com.m13v.social-moltbook', name: 'MoltBook', type: 'Post', platform: 'MoltBook', script: 'run-moltbook.sh', logPrefix: 'run-moltbook-', plist: 'com.m13v.social-moltbook.plist' },
  { label: 'com.m13v.social-github', name: 'GitHub', type: 'Post', platform: 'GitHub', script: 'run-github.sh', logPrefix: 'run-github-', plist: 'com.m13v.social-github.plist' },
  // Engage row (reply to comments on your posts)
  { label: 'com.m13v.social-engage-moltbook', name: 'Engage MoltBook', type: 'Engage', platform: 'MoltBook', script: 'engage-moltbook.sh', logPrefix: 'engage-moltbook-', plist: 'com.m13v.social-engage-moltbook.plist' },
  { label: 'com.m13v.social-engage-twitter', name: 'Engage Twitter', type: 'Engage', platform: 'Twitter', script: 'engage-twitter.sh', logPrefix: 'engage-twitter-', plist: 'com.m13v.social-engage-twitter.plist' },
  { label: 'com.m13v.social-engage-linkedin', name: 'Engage LinkedIn', type: 'Engage', platform: 'LinkedIn', script: 'engage-linkedin.sh', logPrefix: 'engage-linkedin-', plist: 'com.m13v.social-engage-linkedin.plist' },
  { label: 'com.m13v.social-github-engage', name: 'GitHub Engage', type: 'Engage', platform: 'GitHub', script: 'github-engage.sh', logPrefix: 'github-engage-', plist: 'com.m13v.social-github-engage.plist' },
  // Check Replies row (discover new inbound replies; feeds Engage)
  { label: 'com.m13v.social-scan-reddit-replies', name: 'Engage Reddit', type: 'Engage', platform: 'Reddit', script: 'run-scan-reddit-replies.sh', logPrefix: 'run-scan-reddit-replies-', plist: 'com.m13v.social-scan-reddit-replies.plist' },
  { label: 'com.m13v.social-scan-moltbook-replies', name: 'MoltBook Scan', type: 'Other', platform: 'MoltBook', script: 'run-scan-moltbook-replies.sh', logPrefix: 'run-scan-moltbook-replies-', plist: 'com.m13v.social-scan-moltbook-replies.plist' },
  { label: 'com.m13v.social-scan-twitter-followups', name: 'Twitter Thread Follow-ups', type: 'Other', platform: 'Twitter', script: 'scan-twitter-followups.sh', logPrefix: 'scan-twitter-followups-', plist: 'com.m13v.social-scan-twitter-followups.plist' },
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
  { label: 'com.m13v.social-link-edit-twitter', name: 'Link Edit Twitter', type: 'Link Edit', platform: 'Twitter', script: 'link-edit-twitter.sh', logPrefix: 'link-edit-twitter-', plist: 'com.m13v.social-link-edit-twitter.plist' },
  { label: 'com.m13v.social-link-edit-linkedin', name: 'Link Edit LinkedIn', type: 'Link Edit', platform: 'LinkedIn', script: 'link-edit-linkedin.sh', logPrefix: 'link-edit-linkedin-', plist: 'com.m13v.social-link-edit-linkedin.plist' },
  { label: 'com.m13v.social-link-edit-moltbook', name: 'Link Edit MoltBook', type: 'Link Edit', platform: 'MoltBook', script: 'link-edit-moltbook.sh', logPrefix: 'link-edit-moltbook-', plist: 'com.m13v.social-link-edit-moltbook.plist' },
  { label: 'com.m13v.social-link-edit-github', name: 'Link Edit GitHub', type: 'Link Edit', platform: 'GitHub', script: 'link-edit-github.sh', logPrefix: 'link-edit-github-', plist: 'com.m13v.social-link-edit-github.plist' },
  // Stats row
  { label: 'com.m13v.social-stats-reddit', name: 'Stats Reddit', type: 'Stats', platform: 'Reddit', script: 'stats-reddit.sh', logPrefix: 'stats-reddit-', plist: 'com.m13v.social-stats-reddit.plist' },
  { label: 'com.m13v.social-stats-twitter', name: 'Stats Twitter', type: 'Stats', platform: 'Twitter', script: 'stats-twitter.sh', logPrefix: 'stats-twitter-', plist: 'com.m13v.social-stats-twitter.plist' },
  { label: 'com.m13v.social-stats-linkedin', name: 'Stats LinkedIn', type: 'Stats', platform: 'LinkedIn', script: 'stats-linkedin.sh', logPrefix: 'stats-linkedin-', plist: 'com.m13v.social-stats-linkedin.plist' },
  { label: 'com.m13v.social-stats-moltbook', name: 'Stats MoltBook', type: 'Stats', platform: 'MoltBook', script: 'stats-moltbook.sh', logPrefix: 'stats-moltbook-', plist: 'com.m13v.social-stats-moltbook.plist' },
  // Post Audit row (verify posts still exist / API health)
  { label: 'com.m13v.social-audit-reddit', name: 'Post Audit Reddit', type: 'Post Audit', platform: 'Reddit', script: 'audit-reddit.sh', logPrefix: 'audit-reddit-', plist: 'com.m13v.social-audit-reddit.plist' },
  { label: 'com.m13v.social-audit-twitter', name: 'Post Audit Twitter', type: 'Post Audit', platform: 'Twitter', script: 'audit-twitter.sh', logPrefix: 'audit-twitter-', plist: 'com.m13v.social-audit-twitter.plist' },
  { label: 'com.m13v.social-audit-linkedin', name: 'Post Audit LinkedIn', type: 'Post Audit', platform: 'LinkedIn', script: 'audit-linkedin.sh', logPrefix: 'audit-linkedin-', plist: 'com.m13v.social-audit-linkedin.plist' },
  { label: 'com.m13v.social-audit-moltbook', name: 'Post Audit MoltBook', type: 'Post Audit', platform: 'MoltBook', script: 'audit-moltbook.sh', logPrefix: 'audit-moltbook-', plist: 'com.m13v.social-audit-moltbook.plist' },
  { label: 'com.m13v.social-audit-reddit-resurrect', name: 'Resurrect Reddit', type: 'Post Audit', platform: 'Reddit', script: 'audit-reddit-resurrect.sh', logPrefix: 'audit-reddit-resurrect-', plist: 'com.m13v.social-audit-reddit-resurrect.plist' },
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
  return driver.isLoaded(label);
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
let _batchSnapshotCache = { at: 0, value: null };
function buildBatchSnapshot() {
  const now = Date.now();
  if (_batchSnapshotCache.value && now - _batchSnapshotCache.at < 2000) {
    return _batchSnapshotCache.value;
  }
  const { loadedLabels, pidByLabel } = driver.list();

  const logFiles = (() => {
    try { return fs.readdirSync(LOG_DIR); } catch { return []; }
  })();

  const snap = { loadedLabels, pidByLabel, logFiles };
  _batchSnapshotCache = { at: now, value: snap };
  return snap;
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

function getPlistInterval(unitPath) {
  try {
    const text = fs.readFileSync(unitPath, 'utf8');
    const { intervalSecs } = driver.scheduleFromUnit(text);
    return intervalSecs;
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

function getBookingsDbUrl() {
  const env = loadEnv();
  return env.BOOKINGS_DATABASE_URL || process.env.BOOKINGS_DATABASE_URL || null;
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

let _pool = null;
function getPool() {
  if (_pool) return _pool;
  const dbUrl = getDbUrl();
  if (!dbUrl) return null;
  _pool = new Pool({
    connectionString: dbUrl,
    max: 5,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 10000,
  });
  _pool.on('error', (err) => {
    console.error('[pg.Pool] idle client error:', err.message);
  });
  return _pool;
}

async function pq(query, params) {
  const pool = getPool();
  if (!pool) return null;
  try {
    const r = await pool.query(query, params);
    return r.rows;
  } catch (e) {
    console.error('[pq] query failed:', e.message);
    return null;
  }
}

let _bookingsPool = null;
function getBookingsPool() {
  if (_bookingsPool) return _bookingsPool;
  const dbUrl = getBookingsDbUrl();
  if (!dbUrl) return null;
  _bookingsPool = new Pool({
    connectionString: dbUrl,
    max: 3,
    idleTimeoutMillis: 30000,
    connectionTimeoutMillis: 10000,
  });
  _bookingsPool.on('error', (err) => {
    console.error('[bookings pg.Pool] idle client error:', err.message);
  });
  return _bookingsPool;
}

async function pqBookings(query, params) {
  const pool = getBookingsPool();
  if (!pool) return null;
  try {
    const r = await pool.query(query, params);
    return r.rows;
  } catch (e) {
    console.error('[pqBookings] query failed:', e.message);
    return null;
  }
}

async function pqScalar(query, params) {
  const rows = await pq(query, params);
  if (!rows || !rows.length) return null;
  const row = rows[0];
  const keys = Object.keys(row);
  return keys.length ? row[keys[0]] : null;
}

function getLaunchAgentPath(unitFile) {
  return path.join(AGENT_DIR, driver.unitFileName(unitFile));
}

// --- Job history helpers ----------------------------------------------------
// run_monitor.log is the one-line-per-completed-run ledger written by
// scripts/log_run.py. Each line: `ISO_TS | script_name | posted=N skipped=N
// failed=N cost=$X elapsed=Ns`. WARNING lines are interleaved and skipped.

const RUN_MONITOR_PATH = path.join(LOG_DIR, 'run_monitor.log');
const RUN_LINE_RE = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\s*\|\s*(\S+)\s*\|\s*posted=(\d+)\s+skipped=(\d+)\s+failed=(\d+)\s+cost=\$([\d.]+)\s+elapsed=(\d+)s/;

// posts.platform is lowercase; UI labels are capitalized.
const PLATFORM_LABELS = {
  twitter: 'Twitter', reddit: 'Reddit', linkedin: 'LinkedIn',
  moltbook: 'MoltBook', github: 'GitHub', dev: 'Dev',
  hackernews: 'HackerNews', youtube: 'YouTube',
};

// Standalone jobs with no platform axis. script_name -> display label.
const STANDALONE_JOBS = {
  serp_seo: { job_type: 'seo', job_label: 'SERP SEO' },
  gsc_seo: { job_type: 'seo', job_label: 'GSC SEO' },
  seo_improve: { job_type: 'seo', job_label: 'SEO Improve' },
  seo_top_pages: { job_type: 'seo', job_label: 'SEO Top Pages' },
  seo_weekly_roundup: { job_type: 'seo', job_label: 'SEO Weekly Roundup' },
  seo_daily_report: { job_type: 'report', job_label: 'SEO Daily Report' },
  daily_report: { job_type: 'report', job_label: 'Daily Report' },
  deploy_status: { job_type: 'report', job_label: 'Deploy Status' },
  precompute_stats: { job_type: 'report', job_label: 'Precompute Stats' },
};

function classifyScript(script) {
  const norm = script.replace(/-/g, '_').toLowerCase();
  const standalone = STANDALONE_JOBS[norm];
  if (standalone) {
    return {
      job_type: standalone.job_type,
      job_label: standalone.job_label,
      platform: null,
      platform_key: null,
      human_name: standalone.job_label,
    };
  }
  const match = (re, type, label) => {
    const m = norm.match(re);
    if (!m) return null;
    const slug = m[1];
    const platform = PLATFORM_LABELS[slug] || slug;
    return {
      job_type: type,
      job_label: label,
      platform,
      platform_key: slug,
      human_name: `${label} · ${platform}`,
    };
  };
  return (
    match(/^link_edit_(\w+)$/, 'link-edit', 'Link Edit') ||
    match(/^engage_(\w+)$/, 'engage', 'Engage') ||
    match(/^post_(\w+)$/, 'post', 'Post') ||
    match(/^dm_outreach_(\w+)$/, 'dm-outreach', 'DM Outreach') ||
    match(/^dm_replies_(\w+)$/, 'dm-replies', 'DM Replies') ||
    match(/^scan_(\w+?)_(?:replies|followups|mentions)$/, 'check-replies', 'Check Replies') ||
    match(/^octolens_(\w+)$/, 'octolens', 'Octolens') ||
    match(/^stats_(\w+)$/, 'stats', 'Stats') ||
    match(/^audit[-_]([\w-]+)$/, 'audit', 'Audit') ||
    { job_type: 'other', job_label: script, platform: null, platform_key: null, human_name: script }
  );
}

function parseRunMonitorLog(maxLines) {
  let lines;
  try {
    lines = fs.readFileSync(RUN_MONITOR_PATH, 'utf8').split('\n');
  } catch { return []; }
  const runs = [];
  const tail = lines.slice(-maxLines * 2);
  for (const line of tail) {
    const m = line.match(RUN_LINE_RE);
    if (!m) continue;
    const [, ts, script, posted, skipped, failed, cost, elapsed] = m;
    // log_run.py writes naive local-wallclock time (strftime without tz), so
    // `new Date(ts)` in node interprets it as local on the server. That is
    // correct since the dashboard server runs on the same host.
    const finishedMs = new Date(ts).getTime();
    if (!Number.isFinite(finishedMs)) continue;
    const elapsedSec = parseInt(elapsed, 10);
    const startedMs = finishedMs - elapsedSec * 1000;
    const cls = classifyScript(script);
    runs.push({
      script,
      job_type: cls.job_type,
      job_label: cls.job_label,
      platform: cls.platform,
      platform_key: cls.platform_key,
      human_name: cls.human_name,
      started_at: new Date(startedMs).toISOString(),
      finished_at: new Date(finishedMs).toISOString(),
      elapsed_s: elapsedSec,
      result: {
        type: 'generic',
        posted: parseInt(posted, 10),
        skipped: parseInt(skipped, 10),
        failed: parseInt(failed, 10),
        cost_usd: parseFloat(cost),
      },
    });
  }
  return runs.reverse(); // newest first
}

async function enrichLinkEditRuns(runs) {
  const linkRuns = runs.filter(r => r.job_type === 'link-edit' && r.platform_key);
  if (!linkRuns.length) return;
  // Cheapest: one query for all link-edited posts since the oldest run
  // start (minus 2min buffer), bucket in JS.
  let oldestMs = Infinity;
  for (const r of linkRuns) {
    const ms = new Date(r.started_at).getTime();
    if (ms < oldestMs) oldestMs = ms;
  }
  const since = new Date(oldestMs - 2 * 60 * 1000).toISOString();
  const rows = await pq(
    "SELECT platform, link_edited_at, (link_edit_content LIKE 'SKIPPED:%') AS is_skip FROM posts WHERE link_edited_at >= $1::timestamp",
    [since]
  );
  if (!rows) return;
  // pg parses `timestamp without time zone` columns as local time, but
  // Neon stores these rows in UTC (session tz=UTC). The Date we get back has
  // epoch = UTC-for-local-at-those-digits, 7h ahead of true UTC in PDT. Subtract
  // the local offset to recover the true UTC epoch so the bucket comparison
  // against run.started_at (true UTC epoch from log_run.py local-time ISO) is
  // apples-to-apples.
  const normRows = rows.map(r => {
    const d = r.link_edited_at instanceof Date ? r.link_edited_at : new Date(r.link_edited_at);
    return {
      platform: (r.platform || '').toLowerCase(),
      editedMs: d.getTime() - d.getTimezoneOffset() * 60 * 1000,
      skip: !!r.is_skip,
    };
  });
  for (const run of linkRuns) {
    const startMs = new Date(run.started_at).getTime();
    const endMs = new Date(run.finished_at).getTime() + 60 * 1000; // 60s trailing buffer
    let total = 0, success = 0, skipped = 0;
    for (const p of normRows) {
      if (p.platform !== run.platform_key) continue;
      if (p.editedMs < startMs || p.editedMs > endMs) continue;
      total++;
      if (p.skip) skipped++; else success++;
    }
    run.result = { type: 'link-edit', total, success, skipped, cost_usd: run.result && run.result.cost_usd ? run.result.cost_usd : 0 };
  }
}

// engage_* runs: per-run counts from log_run.py are frequently wrong (twitter
// and linkedin shells log cumulative totals; reddit is accurate but shows
// blank when the queue was empty). Enrich from the `replies` table over the
// run's [started_at, finished_at] window so the Result column reflects what
// the run actually did, and include a pending-queue snapshot so "no work"
// runs are distinguishable from broken ones.
async function enrichEngageRuns(runs) {
  const engageRuns = runs.filter(r => r.job_type === 'engage' && r.platform_key);
  if (!engageRuns.length) return;
  let oldestMs = Infinity;
  for (const r of engageRuns) {
    const ms = new Date(r.started_at).getTime();
    if (ms < oldestMs) oldestMs = ms;
  }
  const since = new Date(oldestMs - 2 * 60 * 1000).toISOString();
  const rows = await pq(
    "SELECT platform, status, replied_at, processing_at FROM replies " +
    "WHERE (replied_at >= $1::timestamp OR processing_at >= $1::timestamp)",
    [since]
  );
  if (!rows) return;
  // Same UTC/local correction as enrichLinkEditRuns: pg parses `timestamp
  // without time zone` as local, but rows are stored UTC.
  const normRows = rows.map(r => {
    const toMs = (d) => {
      if (!d) return null;
      const dt = d instanceof Date ? d : new Date(d);
      return dt.getTime() - dt.getTimezoneOffset() * 60 * 1000;
    };
    // platform_key for engage_reddit is 'reddit'; DB platforms are
    // 'reddit', 'x', 'linkedin', 'github', 'moltbook'. Map twitter->x.
    return {
      platform: (r.platform || '').toLowerCase(),
      status: r.status,
      repliedMs: toMs(r.replied_at),
      processingMs: toMs(r.processing_at),
    };
  });
  const pendingByPlatform = {};
  const pendingRows = await pq(
    "SELECT platform, COUNT(*)::int AS n FROM replies WHERE status='pending' GROUP BY platform"
  );
  if (pendingRows) {
    for (const r of pendingRows) pendingByPlatform[(r.platform || '').toLowerCase()] = r.n;
  }
  for (const run of engageRuns) {
    const startMs = new Date(run.started_at).getTime();
    const endMs = new Date(run.finished_at).getTime() + 60 * 1000;
    // engage_twitter job → DB platform 'x'
    const dbPlatform = run.platform_key === 'twitter' ? 'x' : run.platform_key;
    let replied = 0, skipped = 0, errored = 0, processed = 0;
    for (const p of normRows) {
      if (p.platform !== dbPlatform) continue;
      const actedMs = p.repliedMs != null ? p.repliedMs : p.processingMs;
      if (actedMs == null || actedMs < startMs || actedMs > endMs) continue;
      processed++;
      if (p.status === 'replied') replied++;
      else if (p.status === 'skipped') skipped++;
      else if (p.status === 'error') errored++;
    }
    run.result = {
      type: 'engage',
      processed,
      replied,
      skipped,
      errored,
      pending_now: pendingByPlatform[dbPlatform] || 0,
      cost_usd: run.result && run.result.cost_usd ? run.result.cost_usd : 0,
    };
  }
}

// scan_*_replies / scan_*_followups / scan_*_mentions runs: the shell wrappers
// log `posted=FOUND` where FOUND is grepped from stdout, which is fragile and
// zero-by-default. Replace it with a direct count of rows inserted into the
// `replies` table during the run window, and surface what share of new rows
// were stale (already existed, filtered out, etc) via the pending queue size.
async function enrichCheckRepliesRuns(runs) {
  const scanRuns = runs.filter(r => r.job_type === 'check-replies' && r.platform_key);
  if (!scanRuns.length) return;
  let oldestMs = Infinity;
  for (const r of scanRuns) {
    const ms = new Date(r.started_at).getTime();
    if (ms < oldestMs) oldestMs = ms;
  }
  const since = new Date(oldestMs - 2 * 60 * 1000).toISOString();
  const rows = await pq(
    "SELECT platform, discovered_at FROM replies WHERE discovered_at >= $1::timestamp",
    [since]
  );
  if (!rows) return;
  const normRows = rows.map(r => {
    const d = r.discovered_at instanceof Date ? r.discovered_at : new Date(r.discovered_at);
    return {
      platform: (r.platform || '').toLowerCase(),
      discoveredMs: d.getTime() - d.getTimezoneOffset() * 60 * 1000,
    };
  });
  const pendingRows = await pq(
    "SELECT platform, COUNT(*)::int AS n FROM replies WHERE status='pending' GROUP BY platform"
  );
  const pendingByPlatform = {};
  if (pendingRows) {
    for (const r of pendingRows) pendingByPlatform[(r.platform || '').toLowerCase()] = r.n;
  }
  for (const run of scanRuns) {
    const startMs = new Date(run.started_at).getTime();
    const endMs = new Date(run.finished_at).getTime() + 60 * 1000;
    const dbPlatform = run.platform_key === 'twitter' ? 'x' : run.platform_key;
    let found = 0;
    for (const p of normRows) {
      if (p.platform !== dbPlatform) continue;
      if (p.discoveredMs < startMs || p.discoveredMs > endMs) continue;
      found++;
    }
    run.result = {
      type: 'check-replies',
      found,
      pending_now: pendingByPlatform[dbPlatform] || 0,
      cost_usd: run.result && run.result.cost_usd ? run.result.cost_usd : 0,
    };
  }
}

// 5s TTL cache so /api/status polling (typically every 1-2s) doesn't spawn
// a psql subprocess on every hit. Stale-by-5s is fine for the pending-reply
// counter since it only affects the dashboard badge.
let _pendingCache = { at: 0, value: null };
let _statusCache = { at: 0, value: null };
const activityStatsCache = new Map();
const styleStatsCache = new Map();
// Funnel stats: cached by days. Value shape: { at, value } or { at, pending: Promise }.
const funnelStatsCache = new Map();
// Views-per-day: cached by days. Value shape: { at, value }.
const viewsPerDayCache = new Map();
// Upvotes-per-day: cached by days. Value shape: { at, value }.
const upvotesPerDayCache = new Map();
// Comments-per-day: cached by days. Value shape: { at, value }.
const commentsPerDayCache = new Map();
// Bookings-per-day: cached by days. Value shape: { at, value }.
const bookingsPerDayCache = new Map();
// Funnel-per-day (PostHog-backed metrics): cached by days.
const funnelPerDayCache = new Map();

// On-disk snapshots written by scripts/precompute_dashboard_stats.py every
// ~5 min via launchd com.m13v.social-precompute-stats. When a snapshot is
// fresh enough, endpoints serve it instead of running the live query (which
// for funnel stats costs 15-30s of PostHog HogQL calls). Considered fresh
// if written within the last SNAPSHOT_FRESH_MS.
const SNAPSHOT_DIR = path.join(DEST, 'skill', 'cache');
const SNAPSHOT_FRESH_MS = 15 * 60 * 1000;

// In CLIENT_MODE the server runs on Cloud Run with no access to the
// operator's disk. The precompute script mirrors every snapshot to the
// Neon dashboard_cache table; we cache DB hits briefly per-cache_key so
// the same window served to many clients doesn't hammer Postgres.
const _dbSnapshotCache = new Map();
const DB_SNAPSHOT_CACHE_MS = 20 * 1000;
async function readSnapshotFromDb(key, maxAgeMs) {
  const fresh = maxAgeMs != null ? maxAgeMs : SNAPSHOT_FRESH_MS;
  const now = Date.now();
  const cached = _dbSnapshotCache.get(key);
  if (cached && now - cached.at < DB_SNAPSHOT_CACHE_MS) return cached.value;
  let rows;
  try {
    rows = await pq(
      "SELECT payload, EXTRACT(EPOCH FROM updated_at) * 1000 AS ts FROM dashboard_cache WHERE cache_key = $1",
      [key]
    );
  } catch { return null; }
  if (!rows || !rows.length) { _dbSnapshotCache.set(key, { at: now, value: null }); return null; }
  const ts = Number(rows[0].ts);
  if (now - ts > fresh) { _dbSnapshotCache.set(key, { at: now, value: null }); return null; }
  const value = { value: rows[0].payload, at: ts };
  _dbSnapshotCache.set(key, { at: now, value });
  return value;
}

// Sync call kept for existing callers. Tries disk first (fast, what the
// operator has), then falls back to the last value the DB returned if
// available. The async variant should be preferred in CLIENT_MODE paths.
function readSnapshot(filename, maxAgeMs) {
  try {
    const p = path.join(SNAPSHOT_DIR, filename);
    const st = fs.statSync(p);
    const age = Date.now() - st.mtimeMs;
    if (age > (maxAgeMs != null ? maxAgeMs : SNAPSHOT_FRESH_MS)) return null;
    return { value: JSON.parse(fs.readFileSync(p, 'utf8')), at: st.mtimeMs };
  } catch {}
  if (auth.CLIENT_MODE) {
    const key = filename.replace(/\.json$/, '');
    const cached = _dbSnapshotCache.get(key);
    if (cached && cached.value) {
      const fresh = maxAgeMs != null ? maxAgeMs : SNAPSHOT_FRESH_MS;
      if (Date.now() - cached.value.at <= fresh) return cached.value;
    }
  }
  return null;
}

// CLIENT_MODE-aware snapshot read. Call sites that already have an
// async context use this so a missing on-disk file still returns the
// Neon-backed snapshot.
async function readSnapshotCached(filename, maxAgeMs) {
  const disk = readSnapshot(filename, maxAgeMs);
  if (disk) return disk;
  if (!auth.CLIENT_MODE) return null;
  const key = filename.replace(/\.json$/, '');
  return await readSnapshotFromDb(key, maxAgeMs);
}

function invalidateStatusCache() {
  _statusCache = { at: 0, value: null };
  _batchSnapshotCache = { at: 0, value: null };
}
async function cachedPendingReplies() {
  const now = Date.now();
  if (now - _pendingCache.at < 5000) return _pendingCache.value;
  const n = await pqScalar("SELECT COUNT(*)::int AS n FROM replies WHERE status='pending'");
  _pendingCache = { at: now, value: (typeof n === 'number') ? n : (n == null ? null : parseInt(n, 10)) };
  return _pendingCache.value;
}

// Discover every social-autoposter scheduled job by scanning the repo's unit
// dir plus the user's agent dir and merging by label, so hand-installed jobs
// without a repo copy are covered. Field name `plist` is preserved for
// backwards compat with the rest of server.js; on Linux this holds the
// timer file name.
function discoverLaunchdJobs() {
  const discovered = driver.discoverJobs({
    repoUnitDir: UNIT_DIR,
    agentsDir: AGENT_DIR,
  });
  return discovered.map(d => ({
    label: d.label,
    plist: d.unitFile,
    scriptPath: d.scriptPath,
  }));
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
      try { driver.unload(job.label, agentLink); } catch {}
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
  fs.mkdirSync(AGENT_DIR, { recursive: true });
  const all = discoverLaunchdJobs();
  for (const job of all) {
    if (isJobLoaded(job.label)) continue;
    const agentLink = getLaunchAgentPath(job.plist);
    const unitSrc = path.join(UNIT_DIR, driver.unitFileName(job.plist));
    let loadPath = null;
    if (fs.existsSync(agentLink)) {
      // Already installed (real file or symlink) — load as-is so in-place
      // edits to the installed unit are preserved.
      loadPath = agentLink;
    } else if (fs.existsSync(unitSrc)) {
      const linked = driver.install(unitSrc, AGENT_DIR);
      if (linked) loadPath = agentLink;
    }
    if (loadPath) {
      try { driver.load(loadPath); } catch {}
    }
  }
}

function deriveName(label) {
  const acronyms = { seo: 'SEO' };
  return label.replace(/^com\.m13v\.(social-)?/, '')
    .split('-')
    .map(s => acronyms[s.toLowerCase()] || s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

// Returns the scheduler-tracked PID for a given job label, or null if the
// job is loaded-but-idle or not loaded. Uses the driver (launchctl/systemctl)
// rather than pgrep so it survives `exec` in wrapper scripts.
function getLaunchdPid(label) {
  return driver.pidFor(label);
}

// Returns a display string for a unit's schedule (e.g. "every 2h"). Returns
// null when the unit uses a calendar schedule that can't be expressed as a
// simple interval (for which we'd need driver-specific pretty-printing).
function getPlistSchedule(unitPath) {
  try {
    const text = fs.readFileSync(unitPath, 'utf8');
    const { intervalSecs, kind } = driver.scheduleFromUnit(text);
    if (intervalSecs == null) return null;
    const secs = intervalSecs;
    if (secs === 604800) return 'weekly';
    if (secs === 86400) return 'daily';
    if (secs % 86400 === 0) return `every ${secs / 86400}d`;
    if (secs % 3600 === 0) return `every ${secs / 3600}h`;
    if (secs % 60 === 0) return `every ${secs / 60}m`;
    if (kind === 'calendar') return `every ${secs}s (cal)`;
    return `every ${secs}s`;
  } catch { return null; }
}

// Returns the next scheduled fire time (ISO string in server/local tz) for a
// unit, or null when it can't be computed (no StartInterval, no calendar
// Hour/Minute).
function getPlistNextRun(unitPath) {
  try {
    const text = fs.readFileSync(unitPath, 'utf8');
    const d = driver.nextRunFromUnit && driver.nextRunFromUnit(text);
    return d ? d.toISOString() : null;
  } catch { return null; }
}

// Returns {hour, minute} if the unit uses a single-dict daily calendar
// schedule, else null. Used by the UI to pre-fill the time picker.
function getPlistStartTime(unitPath) {
  try {
    const text = fs.readFileSync(unitPath, 'utf8');
    return driver.startTimeFromUnit && driver.startTimeFromUnit(text);
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

// Strip cross-project data from a funnel_stats payload for non-admin users.
// Admin: passthrough. Non-admin: keep only projects in their claim and drop
// org-wide aggregates (overall) that would otherwise leak totals across
// every tenant on the dashboard.
function scopeFunnelStatsPayload(payload, user) {
  if (!payload || typeof payload !== 'object') return payload;
  if (!user || user.admin) return payload;
  const allowed = new Set(Array.isArray(user.projects) ? user.projects : []);
  const projects = Array.isArray(payload.projects)
    ? payload.projects.filter(p => p && allowed.has(p.name))
    : [];
  const { overall, ...rest } = payload;
  return { ...rest, projects };
}

async function handleApi(req, res) {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const p = url.pathname;

  // PUBLIC: short-link resolver. Called from each client website's /r/[code]
  // route to map a DM short code to a Cal.com URL with full UTM. No auth, no
  // admin gate — the blast radius is a noisy click counter, and the response
  // does not leak DM contents (only target_url + dm_id + project + platform).
  // The target_url is frozen on the dms row at mint time, so the container
  // doesn't need config.json or python (CLOUD RUN-friendly).
  if (p.startsWith('/api/short-links/') && req.method === 'GET') {
    const code = decodeURIComponent(p.slice('/api/short-links/'.length).split('/')[0] || '').trim();
    if (!/^[a-z0-9]{4,32}$/i.test(code)) {
      return json(res, { error: 'bad_code' }, 400);
    }
    const pool = getPool();
    if (!pool) return json(res, { error: 'no_db' }, 500);
    try {
      const r = await pool.query(
        `UPDATE dms SET
            short_link_clicks = short_link_clicks + 1,
            short_link_first_click_at = COALESCE(short_link_first_click_at, NOW()),
            short_link_last_click_at = NOW()
          WHERE short_link_code = $1
          RETURNING id AS dm_id, platform, target_project, project_name, short_link_target_url AS target_url`,
        [code]
      );
      if (!r.rows.length) {
        return json(res, { error: 'not_found', code }, 404);
      }
      const row = r.rows[0];
      if (!row.target_url) {
        return json(res, { error: 'no_target_url', dm_id: row.dm_id }, 404);
      }
      let platform = (row.platform || 'reddit').toLowerCase();
      if (platform === 'x') platform = 'twitter';
      return json(res, {
        dm_id: row.dm_id,
        platform,
        project: row.target_project || row.project_name || null,
        target_url: row.target_url,
      });
    } catch (e) {
      console.error('[short-links] resolver db error:', e.message);
      return json(res, { error: 'resolver_failed', detail: String(e.message).slice(0, 500) }, 500);
    }
  }

  // Auth: no-op when CLIENT_MODE is unset (local operator use).
  // When CLIENT_MODE=1, require a Firebase Bearer token and enforce admin/project claims.
  const av = await auth.verifyAuth(req, p);
  if (!av.ok) return json(res, { error: av.error, detail: av.detail || null }, av.status);
  req.user = av.user;

  // GET /api/me - who am I + what projects can I see
  if (p === '/api/me' && req.method === 'GET') {
    return json(res, { user: req.user, clientMode: auth.CLIENT_MODE });
  }

  // GET /api/status
  if (p === '/api/status' && req.method === 'GET') {
    if (_statusCache.value && Date.now() - _statusCache.at < 1500) {
      return json(res, _statusCache.value);
    }
    return (async () => {
    const snap = buildBatchSnapshot();
    const jobs = JOBS.map(job => {
      const plistPath = unitSrcPath(job);
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
    // Static JOBS with type 'Other' are also included here (they are excluded
    // from the matrix since 'Other' is not in JOB_TYPES).
    const matrixLabels = new Set(JOBS.map(j => j.label));
    const discovered = discoverLaunchdJobs();
    const staticOtherJobs = JOBS.filter(job => job.type === 'Other').map(job => {
      const loaded = snap.loadedLabels.has(job.label);
      const pids = pidsForLabelFromSnapshot(snap, job.label);
      const running = pids.length > 0;
      const status = running ? 'running' : loaded ? 'scheduled' : 'stopped';
      const lastLog = lastLogFromSnapshot(snap, job);
      const plistPath = unitSrcPath(job);
      const schedule = getPlistSchedule(plistPath);
      const nextRun = loaded ? getPlistNextRun(plistPath) : null;
      const startTime = getPlistStartTime(plistPath);
      return {
        label: job.label,
        name: job.name,
        script: job.script,
        loaded,
        running,
        pids,
        status,
        schedule,
        nextRun,
        startTime,
        lastRun: lastLog.time,
        lastLogFile: lastLog.file,
        plistFile: job.plist,
      };
    });
    const otherJobs = [
      ...staticOtherJobs,
      ...discovered
        .filter(d => !matrixLabels.has(d.label))
        .map(d => {
          const loaded = snap.loadedLabels.has(d.label);
          const pids = pidsForLabelFromSnapshot(snap, d.label);
          const running = pids.length > 0;
          const status = running ? 'running' : loaded ? 'scheduled' : 'stopped';
          const scriptBasename = d.scriptPath ? path.basename(d.scriptPath) : null;
          const logPrefix = scriptBasename ? scriptBasename.replace(/\.(sh|py|js)$/, '-') : null;
          const lastLog = logPrefix ? lastLogFromSnapshot(snap, { logPrefix }) : { file: null, time: null };
          // Prefer repo unit file for schedule; fall back to installed
          let plistPath = path.join(UNIT_DIR, driver.unitFileName(d.plist));
          if (!fs.existsSync(plistPath)) plistPath = getLaunchAgentPath(d.plist);
          const schedule = getPlistSchedule(plistPath);
          const nextRun = loaded ? getPlistNextRun(plistPath) : null;
          const startTime = getPlistStartTime(plistPath);
          return {
            label: d.label,
            name: deriveName(d.label),
            script: scriptBasename,
            loaded,
            running,
            pids,
            status,
            schedule,
            nextRun,
            startTime,
            lastRun: lastLog.time,
            lastLogFile: lastLog.file,
            plistFile: d.plist,
          };
        }),
    ].sort((a, b) => a.name.localeCompare(b.name));

    const pending = await cachedPendingReplies();
    const allDiscovered = discovered;
    const paused = allDiscovered.length > 0 && allDiscovered.every(j => !snap.loadedLabels.has(j.label));
    const payload = { jobs, otherJobs, pendingReplies: pending, paused };
    _statusCache = { at: Date.now(), value: payload };
    return json(res, payload);
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // POST /api/pause
  if (p === '/api/pause' && req.method === 'POST') {
    const killed = pauseAll();
    invalidateStatusCache();
    return json(res, { paused: true, killedPids: killed });
  }

  // POST /api/resume
  if (p === '/api/resume' && req.method === 'POST') {
    resumeAll();
    invalidateStatusCache();
    return json(res, { paused: false });
  }

  // POST /api/jobs/:label/toggle
  const toggleMatch = p.match(/^\/api\/jobs\/([^/]+)\/toggle$/);
  if (toggleMatch && req.method === 'POST') {
    const label = decodeURIComponent(toggleMatch[1]);
    const job = findJob(label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    const unitSrc = path.join(UNIT_DIR, driver.unitFileName(job.plist));
    const agentLink = getLaunchAgentPath(job.plist);
    const wasLoaded = isJobLoaded(label);
    const intent = !wasLoaded;
    let stderr = '';
    try {
      if (wasLoaded) {
        const r = driver.unload(label, agentLink);
        stderr = r.stderr;
        // Only unlink symlinks. Real-file installed units (e.g. daily-report)
        // must be preserved so the job can be toggled back on.
        try {
          const st = fs.lstatSync(agentLink);
          if (st.isSymbolicLink()) fs.unlinkSync(agentLink);
        } catch {}
      } else {
        fs.mkdirSync(path.dirname(agentLink), { recursive: true });
        if (!fs.existsSync(agentLink)) {
          if (!fs.existsSync(unitSrc)) return json(res, { error: 'No unit source' }, 404);
          driver.install(unitSrc, AGENT_DIR);
        }
        const r = driver.load(agentLink);
        stderr = r.stderr;
      }
    } catch (e) {
      return json(res, { error: e.message }, 500);
    }
    // Re-check actual state; the scheduler CLI may exit 0 while silently failing.
    const nowLoaded = isJobLoaded(label);
    invalidateStatusCache();
    const payload = { loaded: nowLoaded };
    if (nowLoaded !== intent) {
      payload.error = stderr || `${wasLoaded ? 'unload' : 'load'} reported success but state did not change`;
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
      const r = driver.kickstart(label);
      if (!r.ok) {
        return json(res, { error: r.stderr || 'kickstart failed' }, 500);
      }
      invalidateStatusCache();
      return json(res, { started: true, pid: r.pid });
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
    // SIGKILL (not SIGTERM) because some scripts trap TERM (e.g. lock.sh's
    // cleanup trap) and the trap fires but the outer bash keeps waiting on
    // its child, so SIGTERM alone doesn't reliably end the job.
    try { driver.killJob(label); } catch {}
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
      // Prefer editing the repo unit so git tracks the change; fall back to
      // the installed file if the repo doesn't have a copy.
      let unitPath = path.join(UNIT_DIR, driver.unitFileName(job.plist));
      if (!fs.existsSync(unitPath)) unitPath = getLaunchAgentPath(job.plist);
      let ok;
      try { ok = driver.updateInterval(unitPath, interval); }
      catch (e) { return json(res, { error: e.message }, 500); }
      if (!ok) {
        return json(res, { error: 'Job uses a calendar schedule; interval not settable here' }, 400);
      }
      // Reload if currently loaded so the new interval takes effect
      const agentLink = getLaunchAgentPath(job.plist);
      if (isJobLoaded(label)) {
        try {
          driver.unload(label, agentLink);
          driver.load(agentLink);
        } catch {}
      }
      return json(res, { interval });
    }).catch(e => json(res, { error: e.message }, 400));
  }

  // POST /api/jobs/:label/start-time - set a daily StartCalendarInterval at
  // {hour, minute}. Converts interval-based jobs and array-form calendar jobs
  // into a single-dict daily schedule. Reloads the job so launchd picks it up.
  const startTimeMatch = p.match(/^\/api\/jobs\/([^/]+)\/start-time$/);
  if (startTimeMatch && req.method === 'POST') {
    return readBody(req).then(body => {
      const { hour, minute } = JSON.parse(body);
      if (!Number.isFinite(hour) || hour < 0 || hour > 23 ||
          !Number.isFinite(minute) || minute < 0 || minute > 59) {
        return json(res, { error: 'hour must be 0-23 and minute 0-59' }, 400);
      }
      const label = decodeURIComponent(startTimeMatch[1]);
      const job = findJob(label);
      if (!job) return json(res, { error: 'Unknown job' }, 404);
      let unitPath = path.join(UNIT_DIR, driver.unitFileName(job.plist));
      if (!fs.existsSync(unitPath)) unitPath = getLaunchAgentPath(job.plist);
      let result;
      try { result = driver.updateStartTime(unitPath, hour, minute); }
      catch (e) { return json(res, { error: e.message }, 500); }
      if (!result || !result.ok) {
        return json(res, { error: (result && result.reason) || 'Could not rewrite plist schedule' }, 500);
      }
      const agentLink = getLaunchAgentPath(job.plist);
      if (isJobLoaded(label)) {
        try {
          driver.unload(label, agentLink);
          driver.load(agentLink);
        } catch {}
      }
      invalidateStatusCache();
      return json(res, { hour, minute, kind: result.kind, count: result.count });
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
        const unitPath = path.join(UNIT_DIR, driver.unitFileName(job.plist));
        try {
          const ok = driver.updateInterval(unitPath, interval);
          if (!ok) {
            results.push({ label: job.label, error: 'calendar schedule; not settable' });
            continue;
          }
          // Reload if currently loaded
          const agentLink = getLaunchAgentPath(job.plist);
          if (isJobLoaded(job.label)) {
            try {
              driver.unload(job.label, agentLink);
              try { fs.unlinkSync(agentLink); } catch {}
              driver.install(unitPath, AGENT_DIR);
              driver.load(agentLink);
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

  // GET /api/config. In CLIENT_MODE the container has no config.json
  // (Dockerfile ships only config.example.json), and editing it in prod
  // would write to ephemeral container disk anyway. Return an empty
  // payload so the Settings tab degrades gracefully instead of 500.
  if (p === '/api/config' && req.method === 'GET') {
    if (auth.CLIENT_MODE) return json(res, {});
    try {
      const config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
      return json(res, config);
    } catch (e) { return json(res, { error: e.message }, 500); }
  }

  // POST /api/config
  if (p === '/api/config' && req.method === 'POST') {
    if (auth.CLIENT_MODE) return json(res, { error: 'config_readonly_in_client_mode' }, 405);
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
    return (async () => {
      const q = "SELECT json_build_object(" +
          "'count', (SELECT COUNT(*)::int FROM replies WHERE status='pending'), " +
          "'byPlatform', COALESCE((SELECT json_agg(row_to_json(r)) FROM (SELECT platform, COUNT(*)::int as count FROM replies WHERE status='pending' GROUP BY platform) r), '[]'::json), " +
          "'recent', COALESCE((SELECT json_agg(row_to_json(r)) FROM (SELECT id, platform, their_author, their_content, status FROM replies WHERE status='pending' ORDER BY discovered_at DESC LIMIT 20) r), '[]'::json), " +
          "'statusCounts', COALESCE((SELECT json_agg(row_to_json(r)) FROM (SELECT status, COUNT(*)::int as count FROM replies GROUP BY status ORDER BY status) r), '[]'::json)" +
        ") AS result";
      const rows = await pq(q);
      const result = (rows && rows.length && rows[0].result) ? rows[0].result : { count: null, byPlatform: [], recent: [], statusCounts: [] };
      return json(res, result);
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/job-runs
  // General-purpose job history: last N completed runs of every pipeline that
  // writes to skill/logs/run_monitor.log. For link-edit-* runs the per-run
  // result (total touched / success / skipped) is computed from the `posts`
  // table over the run's [started_at, finished_at] window, since the
  // log_run.py counters for link-edit are not populated reliably.
  if (p === '/api/job-runs' && req.method === 'GET') {
    return (async () => {
      const hoursRaw = url.searchParams.get('hours');
      const hoursNum = hoursRaw != null ? parseInt(hoursRaw, 10) : NaN;
      const hours = Number.isFinite(hoursNum) && hoursNum > 0 ? Math.min(hoursNum, 24 * 90) : null;
      let runs;
      if (hours != null) {
        // run_monitor.log is small (a few thousand lines), so parsing the
        // whole file is cheap. Time-filter server-side so the client gets
        // exactly what the Status tab window asked for.
        const cutoffMs = Date.now() - hours * 3600 * 1000;
        runs = parseRunMonitorLog(100000).filter(r => {
          const t = r.started_at ? Date.parse(r.started_at) : NaN;
          return Number.isFinite(t) && t >= cutoffMs;
        });
      } else {
        const limitRaw = parseInt(url.searchParams.get('limit') || '100', 10);
        const limit = Math.min(Math.max(limitRaw, 1), 500);
        runs = parseRunMonitorLog(Math.max(limit * 3, 300)).slice(0, limit);
      }
      await enrichLinkEditRuns(runs);
      await enrichEngageRuns(runs);
      await enrichCheckRepliesRuns(runs);
      return json(res, { runs });
    })().catch(e => json(res, { error: e.message }, 500));
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
    return (async () => {
      const q = "SELECT json_build_object(" +
          "'count', (SELECT COUNT(*)::int FROM octolens_mentions WHERE status = 'pending'), " +
          "'mentions', COALESCE((SELECT json_agg(row_to_json(r)) FROM (SELECT id, platform, url, author, sentiment, tags, keywords, source_timestamp FROM octolens_mentions WHERE status = 'pending' ORDER BY source_timestamp DESC LIMIT 20) r), '[]'::json)" +
        ") AS result";
      const rows = await pq(q);
      const result = (rows && rows.length && rows[0].result) ? rows[0].result : { count: 0, mentions: [] };
      return json(res, result);
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/activity - unified recent-events feed across posts, replies, mentions, dms
  // cost_usd: a Claude session typically produces N activity rows; we split the
  // session's total cost evenly across them so the sum across the feed matches
  // what was actually spent. Sources without a session_id (mentions, SEO) project NULL.
  if (p === '/api/activity' && req.method === 'GET') {
    const q = "WITH src AS (" +
        "SELECT claude_session_id FROM posts WHERE claude_session_id IS NOT NULL AND posted_at IS NOT NULL " +
        "UNION ALL SELECT claude_session_id FROM replies WHERE claude_session_id IS NOT NULL AND status IN ('replied','skipped') " +
        "UNION ALL SELECT claude_session_id FROM dms WHERE claude_session_id IS NOT NULL AND status='sent' AND sent_at IS NOT NULL " +
        "UNION ALL SELECT m.claude_session_id FROM dm_messages m WHERE m.claude_session_id IS NOT NULL AND m.direction='outbound' " +
        "UNION ALL SELECT claude_session_id FROM posts WHERE claude_session_id IS NOT NULL AND resurrected_at IS NOT NULL " +
        "UNION ALL SELECT claude_session_id FROM seo_keywords WHERE claude_session_id IS NOT NULL AND completed_at IS NOT NULL AND page_url IS NOT NULL " +
        "UNION ALL SELECT claude_session_id FROM gsc_queries WHERE claude_session_id IS NOT NULL AND completed_at IS NOT NULL AND page_url IS NOT NULL " +
        "UNION ALL SELECT claude_session_id FROM seo_page_improvements WHERE claude_session_id IS NOT NULL AND completed_at IS NOT NULL AND status='committed'" +
      "), session_counts AS (" +
        "SELECT claude_session_id, COUNT(*)::int AS rows_in_session FROM src GROUP BY claude_session_id" +
      "), session_cost AS (" +
        "SELECT cs.session_id, (cs.total_cost_usd / NULLIF(sc.rows_in_session, 0))::numeric(10,6) AS per_row_cost " +
        "FROM claude_sessions cs JOIN session_counts sc ON sc.claude_session_id = cs.session_id" +
      ") " +
      "SELECT json_agg(row_to_json(r)) FROM (" +
      "SELECT * FROM (SELECT posted_at AS occurred_at, 'posted' AS type, platform, our_account AS actor, COALESCE(thread_title, LEFT(our_content, 140)) AS summary, engagement_style AS detail, our_url AS link, ('p' || posts.id) AS key, project_name AS project, sc.per_row_cost AS cost_usd FROM posts LEFT JOIN session_cost sc ON sc.session_id = posts.claude_session_id WHERE posted_at IS NOT NULL AND our_content <> '(mention - no original post)' ORDER BY posted_at DESC LIMIT 150) x1 " +
      "UNION ALL SELECT * FROM (SELECT r2.replied_at, 'replied', r2.platform, r2.their_author, COALESCE(LEFT(r2.our_reply_content, 140), LEFT(r2.their_content, 140)), CASE WHEN r2.is_recommendation THEN 'rec · ' || COALESCE(r2.engagement_style, '') ELSE r2.engagement_style END, r2.our_reply_url, ('r' || r2.id), p.project_name, sc.per_row_cost FROM replies r2 LEFT JOIN posts p ON p.id = r2.post_id LEFT JOIN session_cost sc ON sc.session_id = r2.claude_session_id WHERE r2.status='replied' AND r2.replied_at IS NOT NULL ORDER BY r2.replied_at DESC LIMIT 150) x2 " +
      "UNION ALL SELECT * FROM (SELECT COALESCE(r3.processing_at, r3.discovered_at), 'skipped', r3.platform, r3.their_author, LEFT(r3.their_content, 140), r3.skip_reason, r3.their_comment_url, ('s' || r3.id), p.project_name, sc.per_row_cost FROM replies r3 LEFT JOIN posts p ON p.id = r3.post_id LEFT JOIN session_cost sc ON sc.session_id = r3.claude_session_id WHERE r3.status='skipped' ORDER BY COALESCE(r3.processing_at, r3.discovered_at) DESC LIMIT 150) x3 " +
      "UNION ALL SELECT * FROM (SELECT COALESCE(source_timestamp, received_at), 'mention', platform, author, COALESCE(title, LEFT(body, 140)), sentiment, url, ('m' || id), NULL::text, NULL::numeric FROM octolens_mentions ORDER BY COALESCE(source_timestamp, received_at) DESC LIMIT 150) x4 " +
      "UNION ALL SELECT * FROM (SELECT sent_at, 'dm_sent', platform, their_author, LEFT(our_dm_content, 140), NULL::text, chat_url, ('d' || dms.id), NULL::text, sc.per_row_cost FROM dms LEFT JOIN session_cost sc ON sc.session_id = dms.claude_session_id WHERE status='sent' AND sent_at IS NOT NULL ORDER BY sent_at DESC LIMIT 150) x5 " +
      "UNION ALL SELECT * FROM (SELECT m.message_at, 'dm_reply_sent', d.platform, d.their_author, LEFT(m.content, 140), NULL::text, d.chat_url, ('dr' || m.id), NULL::text, sc.per_row_cost FROM dm_messages m JOIN dms d ON d.id = m.dm_id LEFT JOIN session_cost sc ON sc.session_id = m.claude_session_id WHERE m.direction = 'outbound' AND EXISTS (SELECT 1 FROM dm_messages m2 WHERE m2.dm_id = m.dm_id AND m2.direction = 'inbound' AND m2.message_at < m.message_at) ORDER BY m.message_at DESC LIMIT 150) x5b " +
      "UNION ALL SELECT * FROM (SELECT completed_at, 'page_published_serp', 'seo', product, keyword, slug, page_url, ('k' || sk.id), product, sc.per_row_cost FROM seo_keywords sk LEFT JOIN session_cost sc ON sc.session_id = sk.claude_session_id WHERE completed_at IS NOT NULL AND page_url IS NOT NULL AND COALESCE(source, '') NOT IN ('reddit', 'top_page', 'roundup') ORDER BY completed_at DESC LIMIT 150) x6 " +
      "UNION ALL SELECT * FROM (SELECT completed_at, 'page_published_gsc', 'seo', product, query, page_slug, page_url, ('g' || gq.id), product, sc.per_row_cost FROM gsc_queries gq LEFT JOIN session_cost sc ON sc.session_id = gq.claude_session_id WHERE completed_at IS NOT NULL AND page_url IS NOT NULL ORDER BY completed_at DESC LIMIT 150) x7 " +
      "UNION ALL SELECT * FROM (SELECT completed_at, 'page_published_reddit', 'seo', product, keyword, slug, page_url, ('kr' || sk2.id), product, sc.per_row_cost FROM seo_keywords sk2 LEFT JOIN session_cost sc ON sc.session_id = sk2.claude_session_id WHERE completed_at IS NOT NULL AND page_url IS NOT NULL AND source = 'reddit' ORDER BY completed_at DESC LIMIT 150) x8 " +
      "UNION ALL SELECT * FROM (SELECT completed_at, 'page_published_top', 'seo', product, keyword, slug, page_url, ('kt' || sk3.id), product, sc.per_row_cost FROM seo_keywords sk3 LEFT JOIN session_cost sc ON sc.session_id = sk3.claude_session_id WHERE completed_at IS NOT NULL AND page_url IS NOT NULL AND source = 'top_page' ORDER BY completed_at DESC LIMIT 150) x8b " +
      "UNION ALL SELECT * FROM (SELECT completed_at, 'page_published_roundup', 'seo', product, keyword, slug, page_url, ('kru' || sk4.id), product, sc.per_row_cost FROM seo_keywords sk4 LEFT JOIN session_cost sc ON sc.session_id = sk4.claude_session_id WHERE completed_at IS NOT NULL AND page_url IS NOT NULL AND source = 'roundup' ORDER BY completed_at DESC LIMIT 150) x8r " +
      "UNION ALL SELECT * FROM (SELECT completed_at, 'page_improved', 'seo', product, LEFT(COALESCE(rationale, diff_summary, page_path), 140), page_path, page_url, ('pi' || spi.id), product, sc.per_row_cost FROM seo_page_improvements spi LEFT JOIN session_cost sc ON sc.session_id = spi.claude_session_id WHERE completed_at IS NOT NULL AND status = 'committed' ORDER BY completed_at DESC LIMIT 150) x8c " +
      "UNION ALL SELECT * FROM (SELECT resurrected_at AS occurred_at, 'resurrected' AS type, platform, our_account AS actor, COALESCE(thread_title, LEFT(our_content, 140)) AS summary, NULL::text AS detail, our_url AS link, ('rr' || posts.id) AS key, project_name AS project, sc.per_row_cost AS cost_usd FROM posts LEFT JOIN session_cost sc ON sc.session_id = posts.claude_session_id WHERE resurrected_at IS NOT NULL AND our_content <> '(mention - no original post)' ORDER BY resurrected_at DESC LIMIT 150) x9 " +
      "ORDER BY 1 DESC LIMIT 500) r";
    return (async () => {
      const rows = await pq(q);
      let events = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      // Non-admin: drop events not tagged with an allowed project (including
      // octolens mentions, which have no project column).
      if (!req.user.admin) {
        const allowed = new Set(req.user.projects);
        events = events.filter(e => e.project && allowed.has(e.project));
      }
      return json(res, { events });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/style/stats - posts grouped by engagement_style over a trailing window (default 24h)
  if (p === '/api/style/stats' && req.method === 'GET') {
    const url = new URL(req.url, 'http://localhost');
    const windowHours = Math.max(1, Math.min(720, parseInt(url.searchParams.get('hours') || '24', 10) || 24));
    // Normalize platform: accept 'x' as alias for 'twitter', empty/all = no filter.
    const rawPlatform = (url.searchParams.get('platform') || '').trim().toLowerCase();
    const platform = (rawPlatform === '' || rawPlatform === 'all') ? '' :
                     (rawPlatform === 'x' ? 'twitter' : rawPlatform);
    const platformOk = platform === '' || /^[a-z0-9_]{1,32}$/.test(platform);
    if (!platformOk) return json(res, { error: 'invalid platform' }, 400);
    // Project is case-sensitive (stored as 'Assrt', 'Cyrano', 'fazm', etc.).
    const rawProject = (url.searchParams.get('project') || '').trim();
    const project = (rawProject === '' || rawProject.toLowerCase() === 'all') ? '' : rawProject;
    const projectOk = project === '' || /^[A-Za-z0-9_\-]{1,64}$/.test(project);
    if (!projectOk) return json(res, { error: 'invalid project' }, 400);
    // Non-admin clients can only see projects in their claim. Reject if the
    // requested project isn't allowed, and force-filter the default "all" view.
    const stylePc = auth.projectClause(req.user, 'project_name', project || null);
    if (!stylePc.ok) return json(res, { windowHours, platform: platform || 'all', project: project || 'all', rows: [], platforms: [], projects: [] });
    const cacheKey = windowHours + '|' + platform + '|' + project;
    const cached = styleStatsCache.get(cacheKey);
    if (cached && Date.now() - cached.at < 300000) {
      return json(res, { windowHours, platform: platform || 'all', project: project || 'all',
        rows: cached.value, platforms: cached.platforms, projects: cached.projects, cachedAt: cached.at });
    }
    // Precomputed snapshot, default all/all filter only (the dashboard's
    // initial load). Specific platform/project filters still run live.
    // Non-admin users bypass the snapshot because it aggregates across all projects.
    if (!platform && !project && req.user.admin) {
      const sSnap = await readSnapshotCached(`style_stats_${windowHours}h.json`);
      if (sSnap && sSnap.value && Array.isArray(sSnap.value.rows)) {
        const v = sSnap.value;
        styleStatsCache.set(cacheKey, { at: sSnap.at, value: v.rows, platforms: v.platforms || [], projects: v.projects || [] });
        return json(res, { windowHours, platform: 'all', project: 'all',
          rows: v.rows, platforms: v.platforms || [], projects: v.projects || [], cachedAt: sSnap.at });
      }
    }
    const platformFilter = platform
      ? "AND LOWER(CASE WHEN LOWER(platform)='x' THEN 'twitter' ELSE platform END) = '" + platform + "' "
      : '';
    const projectFilter = stylePc.clause ? stylePc.clause + ' '
      : (project
        ? "AND project_name = '" + project.replace(/'/g, "''") + "' "
        : '');
    // Moltbook and GitHub have no views metric; keep those rows in posts/upvotes/comments
    // totals but exclude them from views sum AND the views-per-post denominator so they
    // don't dilute other styles' averages.
    // upvotes_discounted applies the Reddit/Moltbook -1 clamp per row (OP self-upvote
    // is on by default for both platforms) before summing, matching top_performers.SCORE_SQL
    // so the UI score aligns with the Python feedback report. Per-post score is computed client-side.
    const q = "SELECT json_agg(row_to_json(r)) FROM (" +
      "SELECT COALESCE(engagement_style, '(none)') AS style, COUNT(*)::int AS posts, " +
        "COUNT(*) FILTER (WHERE LOWER(platform) NOT IN ('moltbook', 'github', 'github_issues'))::int AS views_posts, " +
        "COALESCE(SUM(upvotes), 0)::int AS upvotes, " +
        "COALESCE(SUM(CASE WHEN LOWER(platform) IN ('reddit', 'moltbook') " +
          "THEN GREATEST(0, COALESCE(upvotes,0) - 1) " +
          "ELSE COALESCE(upvotes,0) END), 0)::int AS upvotes_discounted, " +
        "COALESCE(SUM(comments_count), 0)::int AS comments, " +
        "COALESCE(SUM(views) FILTER (WHERE LOWER(platform) NOT IN ('moltbook', 'github', 'github_issues')), 0)::int AS views, " +
        // Intent dimension (is_recommendation) is independent of tone (engagement_style).
        // This sum tells us "of N posts in this tone, how many carried a project mention".
        "COALESCE(SUM(CASE WHEN is_recommendation THEN 1 ELSE 0 END), 0)::int AS recommendations " +
      "FROM posts WHERE posted_at >= NOW() - INTERVAL '" + windowHours + " hours' " +
      "AND our_content <> '(mention - no original post)' " +
      platformFilter + projectFilter +
      "GROUP BY engagement_style ORDER BY posts DESC) r";
    // Return the full list of active platforms/projects in the window so the pill
    // rows reflect current reality regardless of the current filter selection.
    const qp = "SELECT json_agg(p) FROM (" +
      "SELECT DISTINCT LOWER(CASE WHEN LOWER(platform)='x' THEN 'twitter' ELSE platform END) AS p " +
      "FROM posts WHERE posted_at >= NOW() - INTERVAL '" + windowHours + " hours' " +
      "AND platform IS NOT NULL ORDER BY p) s";
    const qpr = "SELECT json_agg(p) FROM (" +
      "SELECT DISTINCT project_name AS p FROM posts " +
      "WHERE posted_at >= NOW() - INTERVAL '" + windowHours + " hours' " +
      "AND project_name IS NOT NULL" + stylePc.clause + " ORDER BY p) s";
    return (async () => {
      const [rows, prows, prjRows] = await Promise.all([pq(q), pq(qp), pq(qpr)]);
      const value = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      const platforms = (prows && prows.length && prows[0].json_agg) ? prows[0].json_agg : [];
      const projects = (prjRows && prjRows.length && prjRows[0].json_agg) ? prjRows[0].json_agg : [];
      styleStatsCache.set(cacheKey, { at: Date.now(), value, platforms, projects });
      return json(res, { windowHours, platform: platform || 'all', project: project || 'all',
        rows: value, platforms, projects });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/activity/stats - per-type, per-platform counts over a trailing window (default 24h)
  if (p === '/api/activity/stats' && req.method === 'GET') {
    const url = new URL(req.url, 'http://localhost');
    const windowHours = Math.max(1, Math.min(720, parseInt(url.searchParams.get('hours') || '24', 10) || 24));
    const rawProject = (url.searchParams.get('project') || '').trim();
    // Top-of-tab platform filter. Normalize 'x' → 'twitter' so reddit/twitter/
    // linkedin/moltbook/github/seo all map cleanly. 'all' or empty = no filter.
    const rawPlatform = (url.searchParams.get('platform') || '').trim().toLowerCase();
    const platform = (rawPlatform === '' || rawPlatform === 'all') ? '' :
                     (rawPlatform === 'x' ? 'twitter' : rawPlatform);
    const platformOk = platform === '' || /^[a-z0-9_]{1,32}$/.test(platform);
    if (!platformOk) return json(res, { error: 'invalid platform' }, 400);
    // Resolve project scope: null = admin + all, [] = non-admin with no matching projects, otherwise a list.
    const scopeList = auth.scopedProjects(req.user, rawProject || null);
    if (scopeList !== null && scopeList.length === 0) {
      return json(res, { windowHours, rows: [] });
    }
    // Cache key varies by scope so scoped users don't see admin's cached aggregate.
    const scopeKey = scopeList === null ? 'all' : scopeList.slice().sort().join(',');
    const cacheKey = windowHours + '|' + scopeKey + '|' + platform;
    const cached = activityStatsCache.get(cacheKey);
    // 5-min TTL. The 9-way UNION runs via execSync(psql), blocking Node's
    // event loop; caching prevents dashboard polling from stalling /api/status
    // and /api/activity behind it. 24h counts barely shift in 5 minutes.
    if (cached && Date.now() - cached.at < 300000) {
      return json(res, { windowHours, rows: cached.value, cachedAt: cached.at });
    }
    // Precomputed snapshot is a cross-project aggregate with no platform filter;
    // only valid for admin + all projects + all platforms.
    if (scopeList === null && !platform) {
      const aSnap = await readSnapshotCached(`activity_stats_${windowHours}h.json`);
      if (aSnap && aSnap.value && Array.isArray(aSnap.value.rows)) {
        activityStatsCache.set(cacheKey, { at: aSnap.at, value: aSnap.value.rows });
        return json(res, { windowHours, rows: aSnap.value.rows, cachedAt: aSnap.at });
      }
    }
    // Per-subquery project filter clauses. Each uses the same scope but with the
    // right column name for its table. octolens_mentions has no project column,
    // so mentions are omitted when the user is scope-restricted (scopeList !== null).
    const postsPc       = auth.projectClause(req.user, 'project_name',     rawProject || null);
    const repliesPc     = auth.projectClause(req.user, 'project_name',     rawProject || null);
    const dmsPc         = auth.projectClause(req.user, 'target_project',   rawProject || null);
    const dmsAliasedPc  = auth.projectClause(req.user, 'd.target_project', rawProject || null);
    const seoProdPc     = auth.projectClause(req.user, 'product',          rawProject || null);
    const win = `INTERVAL '${windowHours} hours'`;
    const norm = "CASE WHEN LOWER(pl) = 'x' THEN 'twitter' ELSE LOWER(pl) END";
    const parts = [
      "SELECT 'posted' AS type, platform AS pl FROM posts WHERE posted_at >= NOW() - " + win + postsPc.clause,
      "SELECT 'replied' AS type, platform AS pl FROM replies WHERE status='replied' AND replied_at >= NOW() - " + win + repliesPc.clause,
      "SELECT 'skipped' AS type, platform AS pl FROM replies WHERE status='skipped' AND COALESCE(processing_at, discovered_at) >= NOW() - " + win + repliesPc.clause,
    ];
    if (scopeList === null) {
      parts.push("SELECT 'mention' AS type, platform AS pl FROM octolens_mentions WHERE COALESCE(source_timestamp, received_at) >= NOW() - " + win);
    }
    parts.push("SELECT 'dm_sent' AS type, platform AS pl FROM dms WHERE status='sent' AND sent_at >= NOW() - " + win + dmsPc.clause);
    parts.push("SELECT 'dm_reply_sent' AS type, d.platform AS pl FROM dm_messages m JOIN dms d ON d.id = m.dm_id WHERE m.direction='outbound' AND m.message_at >= NOW() - " + win + " AND EXISTS (SELECT 1 FROM dm_messages m2 WHERE m2.dm_id = m.dm_id AND m2.direction='inbound' AND m2.message_at < m.message_at)" + dmsAliasedPc.clause);
    parts.push("SELECT 'page_published_serp' AS type, 'seo' AS pl FROM seo_keywords WHERE completed_at >= NOW() - " + win + " AND page_url IS NOT NULL AND COALESCE(source, '') NOT IN ('reddit', 'top_page', 'roundup')" + seoProdPc.clause);
    parts.push("SELECT 'page_published_gsc' AS type, 'seo' AS pl FROM gsc_queries WHERE completed_at >= NOW() - " + win + " AND page_url IS NOT NULL" + seoProdPc.clause);
    parts.push("SELECT 'page_published_reddit' AS type, 'seo' AS pl FROM seo_keywords WHERE completed_at >= NOW() - " + win + " AND page_url IS NOT NULL AND source='reddit'" + seoProdPc.clause);
    parts.push("SELECT 'page_published_top' AS type, 'seo' AS pl FROM seo_keywords WHERE completed_at >= NOW() - " + win + " AND page_url IS NOT NULL AND source='top_page'" + seoProdPc.clause);
    parts.push("SELECT 'page_published_roundup' AS type, 'seo' AS pl FROM seo_keywords WHERE completed_at >= NOW() - " + win + " AND page_url IS NOT NULL AND source='roundup'" + seoProdPc.clause);
    parts.push("SELECT 'page_improved' AS type, 'seo' AS pl FROM seo_page_improvements WHERE completed_at >= NOW() - " + win + " AND status='committed'" + seoProdPc.clause);
    parts.push("SELECT 'resurrected' AS type, platform AS pl FROM posts WHERE resurrected_at >= NOW() - " + win + postsPc.clause);
    // Platform filter runs after the UNION so 'x' (raw) and 'twitter' (SEO etc)
    // both fold into the same bucket via the same CASE normalization used below.
    const platformWhere = platform
      ? " WHERE CASE WHEN LOWER(pl) = 'x' THEN 'twitter' ELSE LOWER(pl) END = '" + platform + "'"
      : '';
    const q = "SELECT json_agg(row_to_json(r)) FROM (" +
      "SELECT type, " + norm + " AS platform, COUNT(*)::int AS count FROM (" +
        parts.join(' UNION ALL ') +
      ") u" + platformWhere + " GROUP BY type, platform ORDER BY type, platform) r";
    return (async () => {
      const rows = await pq(q);
      const value = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      activityStatsCache.set(cacheKey, { at: Date.now(), value });
      return json(res, { windowHours, rows: value });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/views|upvotes|comments/per-day?days=N - total per-post metric
  // earned each day across all platforms. Two data sources merged:
  //   1) post_views_daily LAG delta (per-post snapshots, live from 2026-04-24
  //      onward). Each post's FIRST snapshot is excluded (prev IS NULL).
  //   2) aggregate_stats_daily (cross-platform audit-log backfill covering
  //      2026-04-19 -> 2026-04-24). Pre-existence-of-post_views_daily
  //      history, reconstructed from daily-audit cumulative totals.
  // UNION ALL + SUM by day so overlapping 2026-04-24 row totals correctly:
  // aggregate has a non-zero value, per-post yields 0 (cold start), sum is
  // the aggregate value.
  //
  // Platform/project filter: the aggregate table is cross-platform/cross-
  // project and cannot honor these filters. When either filter is active,
  // the aggregate UNION branch is skipped entirely so reconstructed days
  // return 0 rather than misleading totals.
  const perDayMetric = (metricCol, cache, gainedKey, aggregateCol) => {
    if (!req.user.admin) return json(res, { error: 'forbidden' }, 403);
    const url = new URL(req.url, 'http://localhost');
    const days = Math.max(1, Math.min(365, parseInt(url.searchParams.get('days') || '30', 10) || 30));
    const rawPlatform = (url.searchParams.get('platform') || '').trim().toLowerCase();
    const platform = (rawPlatform === '' || rawPlatform === 'all') ? '' :
                     (rawPlatform === 'x' ? 'twitter' : rawPlatform);
    const platformOk = platform === '' || /^[a-z0-9_]{1,32}$/.test(platform);
    if (!platformOk) return json(res, { error: 'invalid platform' }, 400);
    const rawProject = (url.searchParams.get('project') || '').trim();
    const project = (rawProject === '' || rawProject.toLowerCase() === 'all') ? '' : rawProject;
    const projectOk = project === '' || /^[A-Za-z0-9_\-]{1,64}$/.test(project);
    if (!projectOk) return json(res, { error: 'invalid project' }, 400);
    const cacheKey = days + '|' + platform + '|' + project;
    const cached = cache.get(cacheKey);
    if (cached && Date.now() - cached.at < 300000) {
      return json(res, { days, rows: cached.value, cachedAt: cached.at });
    }
    const platformFilter = platform
      ? " AND CASE WHEN LOWER(p.platform) = 'x' THEN 'twitter' ELSE LOWER(p.platform) END = '" + platform + "'"
      : '';
    const projectFilter = project
      ? " AND p.project_name = '" + project.replace(/'/g, "''") + "'"
      : '';
    const includeAggregate = !platform && !project;
    const aggregateUnion = includeAggregate
      ? "UNION ALL SELECT day, " + aggregateCol + "::bigint AS metric_gained " +
        "FROM aggregate_stats_daily " +
        "WHERE day >= CURRENT_DATE - INTERVAL '" + days + " days'"
      : '';
    const q =
      "WITH per_post_daily AS (" +
        "SELECT pvd.post_id, pvd.day, pvd." + metricCol + " AS metric, " +
          "LAG(pvd." + metricCol + ") OVER (PARTITION BY pvd.post_id ORDER BY pvd.day) AS prev_metric " +
        "FROM post_views_daily pvd " +
        "JOIN posts p ON p.id = pvd.post_id " +
        "WHERE LOWER(p.platform) NOT IN ('moltbook', 'github', 'github_issues') " +
          "AND pvd." + metricCol + " IS NOT NULL " +
          "AND pvd.day >= CURRENT_DATE - INTERVAL '" + days + " days'" +
          platformFilter + projectFilter +
      "), per_post AS (" +
        "SELECT day, SUM(GREATEST(metric - prev_metric, 0))::bigint AS metric_gained " +
        "FROM per_post_daily WHERE prev_metric IS NOT NULL GROUP BY day" +
      "), merged AS (" +
        "SELECT day, metric_gained FROM per_post " +
        aggregateUnion +
      ") " +
      "SELECT json_agg(row_to_json(r)) FROM (" +
        "SELECT day::text AS day, " +
          "SUM(metric_gained)::bigint AS " + gainedKey + " " +
        "FROM merged GROUP BY day ORDER BY day ASC" +
      ") r";
    return (async () => {
      const rows = await pq(q);
      const value = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      cache.set(cacheKey, { at: Date.now(), value });
      return json(res, { days, rows: value });
    })().catch(e => json(res, { error: e.message }, 500));
  };
  if (p === '/api/views/per-day' && req.method === 'GET') {
    return perDayMetric('views', viewsPerDayCache, 'views_gained', 'views_gained');
  }
  if (p === '/api/upvotes/per-day' && req.method === 'GET') {
    return perDayMetric('upvotes', upvotesPerDayCache, 'upvotes_gained', 'upvotes_gained');
  }
  if (p === '/api/comments/per-day' && req.method === 'GET') {
    return perDayMetric('comments', commentsPerDayCache, 'comments_gained', 'comments_gained');
  }

  // GET /api/bookings/per-day?days=N - real Cal.com bookings per day from
  // the separate BOOKINGS_DATABASE_URL Neon DB. Filters out test bookings
  // the same way project_stats_json.py does (attendee_email NOT ILIKE
  // '%test%'). Grouped by the local date of created_at. When project is
  // passed, filters by client_slug = <project>.
  if (p === '/api/bookings/per-day' && req.method === 'GET') {
    if (!req.user.admin) return json(res, { error: 'forbidden' }, 403);
    const url = new URL(req.url, 'http://localhost');
    const days = Math.max(1, Math.min(365, parseInt(url.searchParams.get('days') || '30', 10) || 30));
    const rawProject = (url.searchParams.get('project') || '').trim();
    const project = (rawProject === '' || rawProject.toLowerCase() === 'all') ? '' : rawProject;
    const projectOk = project === '' || /^[A-Za-z0-9_\-]{1,64}$/.test(project);
    if (!projectOk) return json(res, { error: 'invalid project' }, 400);
    const cacheKey = days + '|' + project;
    const cached = bookingsPerDayCache.get(cacheKey);
    if (cached && Date.now() - cached.at < 300000) {
      return json(res, { days, rows: cached.value, cachedAt: cached.at });
    }
    const projectFilter = project
      ? " AND client_slug = '" + project.replace(/'/g, "''") + "'"
      : '';
    const q =
      "SELECT json_agg(row_to_json(r)) FROM (" +
        "SELECT to_char(created_at::date, 'YYYY-MM-DD') AS day, " +
          "COUNT(*)::int AS bookings_gained " +
        "FROM cal_bookings " +
        "WHERE created_at >= CURRENT_DATE - INTERVAL '" + days + " days' " +
          "AND COALESCE(attendee_email, '') NOT ILIKE '%test%'" +
          projectFilter +
        " GROUP BY created_at::date ORDER BY created_at::date ASC" +
      ") r";
    return (async () => {
      const rows = await pqBookings(q);
      const value = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      bookingsPerDayCache.set(cacheKey, { at: Date.now(), value });
      return json(res, { days, rows: value });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/funnel/per-day?days=N - PostHog-backed per-day metrics aggregated
  // across every project's domains: pageviews, email_signups, schedule_clicks,
  // get_started_clicks, cross_product_clicks, cta_clicks. Shells out to
  // scripts/funnel_per_day.py which issues one HogQL query per metric per
  // PostHog bucket. Cached 5 min (PostHog calls are slow and rate-limited).
  // Admin-only because the underlying data isn't project-scoped.
  if (p === '/api/funnel/per-day' && req.method === 'GET') {
    if (!req.user.admin) return json(res, { error: 'forbidden' }, 403);
    const url = new URL(req.url, 'http://localhost');
    const days = Math.max(1, Math.min(90, parseInt(url.searchParams.get('days') || '30', 10) || 30));
    const rawProject = (url.searchParams.get('project') || '').trim();
    const project = (rawProject === '' || rawProject.toLowerCase() === 'all') ? '' : rawProject;
    const projectOk = project === '' || /^[A-Za-z0-9_\-]{1,64}$/.test(project);
    if (!projectOk) return json(res, { error: 'invalid project' }, 400);
    const cacheKey = days + '|' + project;
    const cached = funnelPerDayCache.get(cacheKey);
    if (cached && cached.value && Date.now() - cached.at < 300000) {
      return json(res, { days, rows: cached.value.rows || [], cachedAt: cached.at, error: cached.value.error });
    }
    if (auth.CLIENT_MODE) {
      return json(res, { days, rows: [], error: 'snapshot_missing' }, 503);
    }
    const scriptPath = path.join(DEST, 'scripts', 'funnel_per_day.py');
    const argv = [scriptPath, '--days', String(days)];
    if (project) argv.push('--project', project);
    const pending = new Promise((resolve, reject) => {
      const child = spawn('python3', argv, { env: process.env, cwd: DEST });
      let out = '', err = '';
      child.stdout.on('data', d => out += d);
      child.stderr.on('data', d => err += d);
      child.on('error', reject);
      child.on('close', code => {
        if (code !== 0) return reject(new Error(err || ('exit ' + code)));
        try { resolve(JSON.parse(out)); } catch (e) { reject(e); }
      });
    });
    pending.then(val => {
      funnelPerDayCache.set(cacheKey, { at: Date.now(), value: val });
      json(res, { days, rows: val.rows || [], cachedAt: Date.now(), error: val.error });
    }).catch(err => {
      json(res, { error: String(err && err.message || err) }, 500);
    });
    return;
  }

  // GET /api/cost/stats - per-activity-type count + total cost over a trailing
  // window. Types: thread (posts.posted_at), comment (replies.replied),
  // page (seo_keywords + gsc_queries), dm_thread (dms.sent_at). Cost is
  // session.total_cost_usd split evenly across rows_in_session, same model
  // as /api/activity. Admin-only: cost is operator-internal, not exposed to
  // scoped clients.
  if (p === '/api/cost/stats' && req.method === 'GET') {
    if (!req.user.admin) return json(res, { error: 'forbidden' }, 403);
    const url = new URL(req.url, 'http://localhost');
    const windowHours = Math.max(1, Math.min(720, parseInt(url.searchParams.get('hours') || '24', 10) || 24));
    const rawProject = (url.searchParams.get('project') || '').trim();
    const ALLOWED_COST_PLATFORMS = new Set(['reddit', 'twitter', 'linkedin', 'moltbook', 'github', 'seo', 'email']);
    let rawPlat = String(url.searchParams.get('platform') || '').toLowerCase().trim();
    if (rawPlat === 'x') rawPlat = 'twitter';
    const plat = ALLOWED_COST_PLATFORMS.has(rawPlat) ? rawPlat : '';
    const postsPc    = auth.projectClause(req.user, 'project_name',   rawProject || null);
    const repliesPc  = auth.projectClause(req.user, 'project_name',   rawProject || null);
    const dmsPc      = auth.projectClause(req.user, 'target_project', rawProject || null);
    const seoProdPc  = auth.projectClause(req.user, 'product',        rawProject || null);
    const win = "INTERVAL '" + windowHours + " hours'";
    const platNorm = col => "CASE WHEN LOWER(" + col + ") = 'x' THEN 'twitter' ELSE LOWER(" + col + ") END";
    const platClause = col => plat ? (" AND " + platNorm(col) + " = '" + plat + "'") : '';
    const includeThread  = !plat || plat !== 'seo';
    const includeComment = !plat || plat !== 'seo';
    const includePage    = !plat || plat === 'seo';
    const includeDm      = !plat || plat !== 'seo';
    const parts = [
      "SELECT claude_session_id FROM posts WHERE claude_session_id IS NOT NULL AND posted_at IS NOT NULL",
      "SELECT claude_session_id FROM replies WHERE claude_session_id IS NOT NULL AND status IN ('replied','skipped')",
      "SELECT claude_session_id FROM dms WHERE claude_session_id IS NOT NULL AND status='sent' AND sent_at IS NOT NULL",
      "SELECT m.claude_session_id FROM dm_messages m WHERE m.claude_session_id IS NOT NULL AND m.direction='outbound'",
      "SELECT claude_session_id FROM posts WHERE claude_session_id IS NOT NULL AND resurrected_at IS NOT NULL",
      "SELECT claude_session_id FROM seo_keywords WHERE claude_session_id IS NOT NULL AND completed_at IS NOT NULL AND page_url IS NOT NULL",
      "SELECT claude_session_id FROM gsc_queries WHERE claude_session_id IS NOT NULL AND completed_at IS NOT NULL AND page_url IS NOT NULL",
    ];
    const rowQueries = [];
    if (includeThread) {
      rowQueries.push(
        "SELECT 'thread' AS type, COUNT(*)::int AS count, COALESCE(SUM(sc.per_row_cost), 0)::numeric(12,4) AS total_cost_usd " +
        "FROM posts LEFT JOIN session_cost sc ON sc.session_id = posts.claude_session_id " +
        "WHERE posted_at >= NOW() - " + win + platClause('posts.platform') + postsPc.clause
      );
    }
    if (includeComment) {
      rowQueries.push(
        "SELECT 'comment' AS type, COUNT(*)::int AS count, COALESCE(SUM(sc.per_row_cost), 0)::numeric(12,4) AS total_cost_usd " +
        "FROM replies LEFT JOIN session_cost sc ON sc.session_id = replies.claude_session_id " +
        "WHERE replies.status='replied' AND replies.replied_at >= NOW() - " + win + platClause('replies.platform') + repliesPc.clause
      );
    }
    if (includePage) {
      rowQueries.push(
        "SELECT 'page' AS type, COUNT(*)::int AS count, COALESCE(SUM(sc.per_row_cost), 0)::numeric(12,4) AS total_cost_usd " +
        "FROM (" +
          "SELECT claude_session_id FROM seo_keywords WHERE completed_at >= NOW() - " + win + " AND page_url IS NOT NULL" + seoProdPc.clause +
          " UNION ALL " +
          "SELECT claude_session_id FROM gsc_queries WHERE completed_at >= NOW() - " + win + " AND page_url IS NOT NULL" + seoProdPc.clause +
        ") pg LEFT JOIN session_cost sc ON sc.session_id = pg.claude_session_id"
      );
    }
    if (includeDm) {
      rowQueries.push(
        "SELECT 'dm_thread' AS type, COUNT(*)::int AS count, COALESCE(SUM(sc.per_row_cost), 0)::numeric(12,4) AS total_cost_usd " +
        "FROM dms LEFT JOIN session_cost sc ON sc.session_id = dms.claude_session_id " +
        "WHERE dms.status='sent' AND dms.sent_at >= NOW() - " + win + platClause('dms.platform') + dmsPc.clause
      );
    }
    if (!rowQueries.length) {
      return json(res, { windowHours, platform: plat || 'all', rows: [] });
    }
    const q =
      "WITH src AS (" + parts.join(' UNION ALL ') + "), " +
      "session_counts AS (SELECT claude_session_id, COUNT(*)::int AS rows_in_session FROM src GROUP BY claude_session_id), " +
      "session_cost AS (SELECT cs.session_id, (cs.total_cost_usd / NULLIF(sc.rows_in_session, 0))::numeric(12,6) AS per_row_cost " +
        "FROM claude_sessions cs JOIN session_counts sc ON sc.claude_session_id = cs.session_id) " +
      "SELECT json_agg(row_to_json(r)) FROM (" + rowQueries.join(' UNION ALL ') + ") r";
    return (async () => {
      const dbRows = await pq(q);
      const value = (dbRows && dbRows.length && dbRows[0].json_agg) ? dbRows[0].json_agg : [];
      return json(res, { windowHours, platform: plat || 'all', rows: value });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/top/dms - DM threads ranked by hotness.
  // Ordering: needs_human first (human escalation), then by interest_level
  // (hot > warm > general > cold). Threads we've effectively stopped engaging
  // (converted/closed/declined/not_our_prospect/stale-without-warm-interest)
  // sink to the bottom. Tie-break on last_message_at DESC.
  //
  // A stale thread keeps its interest bucket if interest_level is hot/warm/
  // general (the CASE hits the interest WHENs first), so a still-hot lead
  // that just went quiet stays visible at the top. Only "stale + nothing
  // interesting" gets sunk.
  if (p === '/api/top/dms' && req.method === 'GET') {
    const url = new URL(req.url, 'http://localhost');
    // 2000-row hard cap (was 500). Even with full-history queries, the JSON
    // payload stays under a few MB and the SQL plan still uses the sort_bucket
    // index. Frontend default stays at 200; "Load more" pages by offset.
    const limit = Math.max(1, Math.min(2000, parseInt(url.searchParams.get('limit') || '200', 10) || 200));
    const offset = Math.max(0, parseInt(url.searchParams.get('offset') || '0', 10) || 0);
    const WINDOW_HOURS = { '24h': 24, '7d': 24*7, '14d': 24*14, '30d': 24*30, '90d': 24*90, 'all': null };
    const rawWindow = String(url.searchParams.get('window') || '7d').toLowerCase();
    const windowKey = Object.prototype.hasOwnProperty.call(WINDOW_HOURS, rawWindow) ? rawWindow : '7d';
    const windowHours = WINDOW_HOURS[windowKey];
    const rawPlatform = String(url.searchParams.get('platform') || '').toLowerCase().trim();
    const ALLOWED_PLATFORMS = new Set(['reddit', 'twitter', 'x', 'linkedin', 'moltbook', 'email']);
    const platformFilter = ALLOWED_PLATFORMS.has(rawPlatform) ? rawPlatform : '';
    // Free-text search (their_author or last message) and id lookup. When
    // either is present we drop the time-window filter so callers can find
    // arbitrarily old threads without iterating windows.
    const rawSearch = String(url.searchParams.get('q') || '').trim();
    const searchTerm = rawSearch.slice(0, 100);
    const idLookupRaw = String(url.searchParams.get('id') || '').trim();
    const idLookup = /^\d+$/.test(idLookupRaw) ? parseInt(idLookupRaw, 10) : null;
    const isLookup = !!searchTerm || idLookup != null;
    const whereParts = [];
    if (windowHours != null && !isLookup) {
      whereParts.push("COALESCE(tlm.last_at, d.last_message_at, d.discovered_at) >= NOW() - INTERVAL '" + windowHours + " hours'");
    }
    if (platformFilter) {
      whereParts.push("LOWER(d.platform) = '" + platformFilter + "'");
    }
    if (idLookup != null) {
      whereParts.push("d.id = " + idLookup);
    }
    if (searchTerm) {
      // Escape single quotes for the LIKE literal (no params used elsewhere
      // in this endpoint; matches existing pattern). The 100-char cap above
      // bounds payload size.
      const safe = searchTerm.replace(/'/g, "''");
      whereParts.push(
        "(d.their_author ILIKE '%" + safe + "%' " +
        "OR EXISTS (SELECT 1 FROM dm_messages mm WHERE mm.dm_id = d.id AND mm.content ILIKE '%" + safe + "%'))"
      );
    }
    // Scope DMs to the user's project claim. DM project can come from three sources
    // (direct post join, via reply post, or explicit d.target_project); include all.
    const dmPc = auth.projectClause(req.user, 'COALESCE(p_direct.project_name, p_via_reply.project_name, d.target_project)', url.searchParams.get('project'));
    if (!dmPc.ok) return json(res, { dms: [], total: 0, offset, limit, window: windowKey, platform: platformFilter || 'all' });
    if (dmPc.clause) whereParts.push(dmPc.clause.replace(/^\s*AND\s+/, ''));
    const whereSql = whereParts.length ? ('WHERE ' + whereParts.join(' AND ')) : '';
    const q =
      "SELECT json_agg(row_to_json(r)) FROM (" +
        "SELECT d.id, d.platform, d.their_author, d.chat_url, " +
          "d.tier, d.message_count, " +
          "COALESCE(tlm.last_at, d.last_message_at) AS last_message_at, " +
          "d.discovered_at, " +
          "d.conversation_status, d.interest_level, d.mode, " +
          "d.human_reason, d.flagged_at, " +
          "d.target_project, d.icp_precheck, d.icp_matches, d.qualification_status, " +
          "d.qualification_notes, d.booking_link_sent_at, " +
          "d.short_link_code, d.short_link_clicks, " +
          "d.short_link_first_click_at, d.short_link_last_click_at, " +
          "COALESCE(p_direct.project_name, p_via_reply.project_name) AS project_name, " +
          "pr.headline AS prospect_headline, pr.bio AS prospect_bio, " +
          "pr.company AS prospect_company, pr.role AS prospect_role, " +
          "pr.follower_count AS prospect_follower_count, " +
          "pr.recent_activity AS prospect_recent_activity, " +
          "pr.notes AS prospect_notes, pr.profile_url AS prospect_profile_url, " +
          "pr.profile_fetched_at AS prospect_fetched_at, " +
          // Context fields: the public post/thread and comment that preceded this DM.
          // We COALESCE three sources: (1) direct (d.post_id), (2) via-reply
          // (d.reply_id -> replies.post_id), (3) fallback — most recent replies
          // row from the same (platform, author) when both post_id/reply_id are
          // NULL. About 7% of reddit DMs have this orphan pattern (bug where
          // the DM was stored without its reply_id link), and the fallback
          // surfaces the prior public comment thread for them.
          "COALESCE(p_direct.thread_title,   p_via_reply.thread_title,   p_via_fb.thread_title)   AS context_thread_title, " +
          "COALESCE(p_direct.thread_url,     p_via_reply.thread_url,     p_via_fb.thread_url)     AS context_thread_url, " +
          "COALESCE(p_direct.thread_content, p_via_reply.thread_content, p_via_fb.thread_content) AS context_thread_content, " +
          "COALESCE(p_direct.thread_author,  p_via_reply.thread_author,  p_via_fb.thread_author)  AS context_thread_author, " +
          "COALESCE(p_direct.our_content,    p_via_reply.our_content,    p_via_fb.our_content)    AS context_our_content, " +
          "COALESCE(p_direct.our_url,        p_via_reply.our_url,        p_via_fb.our_url)        AS context_our_url, " +
          "COALESCE(p_direct.posted_at,      p_via_reply.posted_at,      p_via_fb.posted_at)      AS context_posted_at, " +
          "COALESCE(r_link.their_content,     r_fallback.their_content)     AS trigger_comment_content, " +
          "COALESCE(r_link.their_comment_url, r_fallback.their_comment_url) AS trigger_comment_url, " +
          "COALESCE(r_link.their_author,      r_fallback.their_author)      AS trigger_comment_author, " +
          "COALESCE(r_link.our_reply_content, r_fallback.our_reply_content) AS trigger_our_reply_content, " +
          "COALESCE(r_link.our_reply_url,     r_fallback.our_reply_url)     AS trigger_our_reply_url, " +
          "COALESCE(r_link.replied_at,        r_fallback.replied_at)        AS trigger_our_reply_at, " +
          "CASE WHEN r_fallback.id IS NOT NULL AND d.reply_id IS NULL AND d.post_id IS NULL THEN TRUE ELSE FALSE END AS context_is_fallback, " +
          "d.comment_context  AS seed_comment_context, " +
          "d.their_content    AS seed_their_content, " +
          "d.our_dm_content   AS seed_our_dm_content, " +
          "(SELECT content   FROM dm_messages WHERE dm_id = d.id ORDER BY message_at DESC LIMIT 1) AS last_msg, " +
          "(SELECT direction FROM dm_messages WHERE dm_id = d.id ORDER BY message_at DESC LIMIT 1) AS last_dir, " +
          "(SELECT COALESCE(json_agg(json_build_object('id', m.id, 'direction', m.direction, 'author', m.author, 'content', m.content, 'message_at', m.message_at) ORDER BY m.message_at ASC), '[]'::json) FROM dm_messages m WHERE m.dm_id = d.id) AS messages, " +
          // Human-authored instructions queued or sent for this DM. Sourced
          // from either Gmail replies to escalation emails (resend_email_id
          // NOT NULL) or the dashboard /api/dm/:id/instructions endpoint
          // (resend_email_id IS NULL). Phase 0 of engage-dm-replies.sh
          // consumes status='pending' rows and crafts DMs from them.
          //
          // generated_reply heuristic: engage-dm-replies.sh logs the outbound
          // DM via dm_conversation.py log-outbound (creates dm_messages row)
          // immediately before UPDATE human_dm_replies SET status='sent',
          // sent_at=NOW(). So the matching outbound dm_messages row has
          // message_at within seconds of hr.sent_at. We pick the closest
          // outbound message in a (-2 min, +30 sec) window. No FK exists
          // because engage-dm-replies.sh is uchg-locked; this is the
          // cleanest pairing we can do without a schema change.
          "(SELECT COALESCE(json_agg(json_build_object(" +
              "'id', hr.id, 'status', hr.status, 'instructions', hr.instructions, " +
              "'created_at', hr.created_at, 'sent_at', hr.sent_at, " +
              "'attempts', hr.attempts, 'last_error', hr.last_error, " +
              "'source', CASE WHEN hr.resend_email_id IS NOT NULL THEN 'gmail' ELSE 'dashboard' END, " +
              "'generated_reply', (" +
                "SELECT m.content FROM dm_messages m " +
                "WHERE m.dm_id = d.id AND m.direction = 'outbound' " +
                  "AND hr.sent_at IS NOT NULL " +
                  "AND m.message_at >= hr.sent_at - interval '5 minutes' " +
                  "AND m.message_at <= hr.sent_at + interval '5 minutes' " +
                "ORDER BY ABS(EXTRACT(EPOCH FROM (m.message_at - hr.sent_at))) ASC " +
                "LIMIT 1" +
              "), " +
              "'generated_reply_at', (" +
                "SELECT m.message_at FROM dm_messages m " +
                "WHERE m.dm_id = d.id AND m.direction = 'outbound' " +
                  "AND hr.sent_at IS NOT NULL " +
                  "AND m.message_at >= hr.sent_at - interval '5 minutes' " +
                  "AND m.message_at <= hr.sent_at + interval '5 minutes' " +
                "ORDER BY ABS(EXTRACT(EPOCH FROM (m.message_at - hr.sent_at))) ASC " +
                "LIMIT 1" +
              ")" +
            ") ORDER BY hr.created_at ASC), '[]'::json) " +
            "FROM human_dm_replies hr WHERE hr.dm_id = d.id) AS human_instructions, " +
          "CASE WHEN d.conversation_status = 'needs_human' THEN 0 " +
               "WHEN d.conversation_status IN ('converted','closed') THEN 90 " +
               "WHEN d.interest_level = 'hot' THEN 10 " +
               "WHEN d.interest_level = 'warm' THEN 20 " +
               "WHEN d.interest_level = 'general_discussion' THEN 30 " +
               "WHEN d.interest_level = 'cold' THEN 40 " +
               "WHEN d.interest_level = 'declined' THEN 80 " +
               "WHEN d.interest_level = 'not_our_prospect' THEN 85 " +
               "WHEN d.conversation_status = 'stale' THEN 70 " +
               "ELSE 50 END AS sort_bucket " +
        "FROM dms d " +
        "LEFT JOIN posts     p_direct    ON p_direct.id    = d.post_id " +
        "LEFT JOIN replies   r_link      ON r_link.id      = d.reply_id " +
        "LEFT JOIN posts     p_via_reply ON p_via_reply.id = r_link.post_id " +
        // Fallback: when a DM has neither post_id nor reply_id linked, try to
        // find the most recent replies row from the same (platform, author).
        // Materialized as a LATERAL join so the subquery can reference d.
        "LEFT JOIN LATERAL (" +
          "SELECT r2.* FROM replies r2 " +
          "WHERE d.reply_id IS NULL AND d.post_id IS NULL " +
            "AND r2.platform = d.platform AND r2.their_author = d.their_author " +
          "ORDER BY r2.discovered_at DESC LIMIT 1" +
        ") r_fallback ON TRUE " +
        "LEFT JOIN posts     p_via_fb    ON p_via_fb.id    = r_fallback.post_id " +
        "LEFT JOIN prospects pr          ON pr.id          = d.prospect_id " +
        // True last-message timestamp from dm_messages. dms.last_message_at is
        // set to NOW() on ingest (see scripts/dm_conversation.py), so it drifts
        // from the real platform message_at whenever we backfill or batch-poll.
        // Use this for the UI "Last message" column, the window filter, and the
        // final ORDER BY tie-breaker.
        "LEFT JOIN LATERAL (" +
          "SELECT MAX(message_at) AS last_at FROM dm_messages WHERE dm_id = d.id" +
        ") tlm ON TRUE " +
        whereSql + " " +
        "ORDER BY sort_bucket ASC, " +
          "CASE WHEN d.conversation_status = 'needs_human' THEN d.flagged_at END DESC NULLS LAST, " +
          "COALESCE(tlm.last_at, d.last_message_at) DESC NULLS LAST, " +
          "d.id DESC " +
        "LIMIT " + limit + " OFFSET " + offset +
      ") r";
    // Cheap COUNT(*) over the same WHERE so the UI can show "X of Y" and
    // know when "Load more" should disappear. Re-uses the LATERAL joins so
    // expressions in whereSql (tlm.last_at, p_direct.*, etc.) resolve.
    const countQ =
      "SELECT COUNT(*)::bigint AS n FROM dms d " +
      "LEFT JOIN posts p_direct ON p_direct.id = d.post_id " +
      "LEFT JOIN replies r_link ON r_link.id = d.reply_id " +
      "LEFT JOIN posts p_via_reply ON p_via_reply.id = r_link.post_id " +
      "LEFT JOIN LATERAL (SELECT MAX(message_at) AS last_at FROM dm_messages WHERE dm_id = d.id) tlm ON TRUE " +
      whereSql;
    return (async () => {
      const [rows, countRows] = await Promise.all([pq(q), pq(countQ)]);
      const dms = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      const total = (countRows && countRows.length) ? Number(countRows[0].n) || 0 : 0;

      // Bookings attributed back to specific DMs via metadata.utm_content =
      // 'dm_<id>'. The cal-webhook stores the full Cal payload under
      // cal_bookings.metadata, so the original UTM lives at
      // metadata.payload.metadata.utm_content. We pull every dm_<id> seen for
      // the DM ids in this page and group by status so the UI can show
      // "booked" + "cancelled" badges per row.
      const dmIds = (dms || []).map(d => Number(d.id)).filter(n => Number.isFinite(n));
      const bookingMap = new Map();
      if (dmIds.length) {
        try {
          const bp = getBookingsPool();
          if (bp) {
            const idList = dmIds.map(n => `'dm_${n}'`).join(',');
            const bq =
              "SELECT metadata#>>'{payload,metadata,utm_content}' AS utm_content, " +
                     "status, attendee_email, start_time, created_at " +
              "FROM cal_bookings " +
              "WHERE metadata#>>'{payload,metadata,utm_content}' IN (" + idList + ") " +
              "AND COALESCE(attendee_email, '') NOT ILIKE '%test%'";
            const br = await bp.query(bq);
            for (const row of br.rows) {
              const m = /^dm_(\d+)$/.exec(row.utm_content || '');
              if (!m) continue;
              const id = Number(m[1]);
              if (!bookingMap.has(id)) bookingMap.set(id, { total: 0, booked: 0, cancelled: 0, last_booking_at: null, recent: [] });
              const e = bookingMap.get(id);
              e.total += 1;
              if (row.status === 'cancelled') e.cancelled += 1;
              else e.booked += 1;
              const at = row.start_time || row.created_at || null;
              if (at && (!e.last_booking_at || at > e.last_booking_at)) e.last_booking_at = at;
              if (e.recent.length < 3) {
                e.recent.push({ email: row.attendee_email, status: row.status, start_time: row.start_time, created_at: row.created_at });
              }
            }
          }
        } catch (e) {
          console.error('[/api/top/dms] bookings lookup failed:', e.message);
        }
      }
      for (const d of (dms || [])) {
        const b = bookingMap.get(Number(d.id));
        d.bookings_count = b ? b.total : 0;
        d.bookings_booked = b ? b.booked : 0;
        d.bookings_cancelled = b ? b.cancelled : 0;
        d.last_booking_at = b ? b.last_booking_at : null;
        d.recent_bookings = b ? b.recent : [];
      }

      return json(res, {
        dms,
        total,
        offset,
        limit,
        window: windowKey,
        platform: platformFilter || 'all',
        lookup: isLookup,
      });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // POST /api/dm/:id/instructions - queue a human-authored instruction for the
  // DM-reply agent. Mirrors what scripts/ingest_human_dm_replies.py does for
  // Gmail-sourced replies, but inserts directly from the dashboard.
  // Phase 0 of skill/engage-dm-replies.sh consumes status='pending' rows on
  // the next platform-specific launchd tick (reddit :13/:43, linkedin :09/:39,
  // twitter :14) and the LLM there crafts the actual DM from the instructions.
  const instructionsMatch = p.match(/^\/api\/dm\/(\d+)\/instructions$/);
  if (instructionsMatch && req.method === 'POST') {
    const dmId = parseInt(instructionsMatch[1], 10);
    return readBody(req).then(async (body) => {
      let payload;
      try { payload = JSON.parse(body || '{}'); } catch { return json(res, { error: 'invalid_json' }, 400); }
      const instructions = String(payload && payload.instructions || '').trim();
      if (instructions.length < 5) return json(res, { error: 'instructions_too_short' }, 400);
      if (instructions.length > 4000) return json(res, { error: 'instructions_too_long' }, 400);
      const dmRows = await pq(
        "SELECT d.id, d.platform, d.their_author, " +
          "COALESCE(p_direct.project_name, p_via_reply.project_name, d.target_project) AS project_name " +
        "FROM dms d " +
        "LEFT JOIN posts   p_direct    ON p_direct.id    = d.post_id " +
        "LEFT JOIN replies r_link      ON r_link.id      = d.reply_id " +
        "LEFT JOIN posts   p_via_reply ON p_via_reply.id = r_link.post_id " +
        "WHERE d.id = $1",
        [dmId]
      );
      if (!dmRows || !dmRows.length) return json(res, { error: 'dm_not_found' }, 404);
      const dm = dmRows[0];
      // Per-row auth: admin always allowed; non-admin must have the DM's
      // project name in their claim. (auth.projectClause is for SQL list
      // filters and rejects names with spaces, so it's the wrong tool here.)
      if (!req.user || !req.user.admin) {
        const projName = dm.project_name || '';
        const claims = (req.user && Array.isArray(req.user.projects)) ? req.user.projects : [];
        if (!projName || !claims.includes(projName)) {
          return json(res, { error: 'forbidden' }, 403);
        }
      }
      const ins = await pq(
        "INSERT INTO human_dm_replies (dm_id, platform, their_author, project_name, " +
          "instructions, email_subject, resend_email_id, status) " +
        "VALUES ($1, $2, $3, $4, $5, $6, NULL, 'pending') " +
        "RETURNING id, dm_id, status, instructions, created_at, attempts",
        [dmId, dm.platform, dm.their_author, dm.project_name, instructions, '[DM #' + dmId + '] (dashboard)']
      );
      if (!ins || !ins.length) return json(res, { error: 'insert_failed' }, 500);
      const row = ins[0];
      return json(res, {
        ok: true,
        instruction: {
          id: row.id,
          status: row.status,
          instructions: row.instructions,
          created_at: row.created_at,
          attempts: row.attempts,
          source: 'dashboard',
        },
      }, 201);
    }).catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/top - top-performing posts by engagement
  // Mirrors scripts/top_performers.py: active posts, non-trivial content,
  // excludes platforms we don't score. Default ranking is upvotes DESC (that's
  // what the feedback-loop pipeline uses); a composite score is also returned
  // so the UI can sort by it.
  if (p === '/api/top' && req.method === 'GET') {
    const url = new URL(req.url, 'http://localhost');
    const limit = Math.max(1, Math.min(500, parseInt(url.searchParams.get('limit') || '150', 10) || 150));
    const WINDOW_HOURS = { '24h': 24, '7d': 24*7, '14d': 24*14, '30d': 24*30, '90d': 24*90, 'all': null };
    const rawWindow = String(url.searchParams.get('window') || '7d').toLowerCase();
    const windowKey = Object.prototype.hasOwnProperty.call(WINDOW_HOURS, rawWindow) ? rawWindow : '7d';
    const windowHours = WINDOW_HOURS[windowKey];
    const rawPlatform = String(url.searchParams.get('platform') || '').toLowerCase().trim();
    const ALLOWED_PLATFORMS = new Set(['reddit', 'twitter', 'x', 'linkedin', 'moltbook']);
    const platformFilter = ALLOWED_PLATFORMS.has(rawPlatform) ? rawPlatform : '';
    const rawKind = String(url.searchParams.get('kind') || 'all').toLowerCase().trim();
    const kindFilter = (rawKind === 'threads' || rawKind === 'comments') ? rawKind : 'all';
    const whereParts = [
      "status = 'active'",
      "our_content IS NOT NULL AND LENGTH(our_content) >= 30",
      "platform NOT IN ('github_issues')",
      "(upvotes IS NOT NULL OR comments_count IS NOT NULL OR views IS NOT NULL)",
    ];
    if (windowHours != null) {
      whereParts.push("posted_at >= NOW() - INTERVAL '" + windowHours + " hours'");
    }
    if (platformFilter) {
      whereParts.push("LOWER(platform) = '" + platformFilter + "'");
    }
    if (kindFilter === 'threads') {
      whereParts.push("thread_url = our_url");
    } else if (kindFilter === 'comments') {
      whereParts.push("(our_url IS NULL OR thread_url <> our_url)");
    }
    const pc = auth.projectClause(req.user, 'project_name', url.searchParams.get('project'));
    if (!pc.ok) return json(res, { posts: [], window: windowKey, platform: platformFilter || 'all', kind: kindFilter });
    if (pc.clause) whereParts.push(pc.clause.replace(/^\s*AND\s+/, ''));
    // Moltbook and GitHub have no views metric; return NULL for those so the UI can
    // render a dash instead of a misleading 0. Score still uses COALESCE so they
    // rank alongside other platforms based on upvotes + comments only.
    const q = "SELECT json_agg(row_to_json(r)) FROM (" +
      "SELECT id, platform, " +
        "COALESCE(upvotes, 0)::int AS upvotes, " +
        "COALESCE(comments_count, 0)::int AS comments_count, " +
        "CASE WHEN LOWER(platform) IN ('moltbook', 'github', 'github_issues') " +
          "THEN NULL ELSE COALESCE(views, 0)::int END AS views, " +
        // Score weights comments and upvotes equally (5 each); views are 1/100.
        // Reddit bakes the OP's self-upvote into the API's `score` field, and our
        // moltbook_post.py self_upvote() call does the same for Moltbook, so a fresh
        // post on either platform shows upvotes=1; discount 1, clamped at 0 so
        // downvoted posts don't go negative.
        "(COALESCE(comments_count,0) * 5 " +
          "+ CASE WHEN LOWER(platform) IN ('reddit', 'moltbook') " +
            "THEN GREATEST(0, COALESCE(upvotes,0) - 1) * 5 " +
            "ELSE COALESCE(upvotes,0) * 5 END " +
          "+ COALESCE(views,0) / 100)::int AS score, " +
        "(our_url IS NOT NULL AND thread_url = our_url) AS is_thread, " +
        "posted_at, engagement_updated_at, our_content, our_url, thread_url, thread_title, " +
        "LEFT(COALESCE(thread_content, ''), 400) AS thread_content, " +
        "our_account, project_name, engagement_style, is_recommendation " +
      "FROM posts " +
      "WHERE " + whereParts.join(' AND ') + " " +
      "ORDER BY upvotes DESC NULLS LAST, comments_count DESC NULLS LAST, views DESC NULLS LAST " +
      "LIMIT " + limit +
      ") r";
    return (async () => {
      const rows = await pq(q);
      const posts = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      return json(res, { posts, window: windowKey, platform: platformFilter || 'all', kind: kindFilter });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/funnel/stats - per-project funnel (posts -> pageviews -> CTAs -> bookings)
  // Shells out to scripts/project_stats_json.py. PostHog API calls make this
  // slow (~15-30s), so we cache for 10 min and dedupe concurrent callers.
  // A launchd timer (com.m13v.social-precompute-stats) also writes fresh
  // snapshots to skill/cache/funnel_stats_<N>d.json so cold starts are instant.
  if (p === '/api/funnel/stats' && req.method === 'GET') {
    const url = new URL(req.url, 'http://localhost');
    const days = Math.max(1, Math.min(90, parseInt(url.searchParams.get('days') || '1', 10) || 1));
    const entry = funnelStatsCache.get(days);
    const TTL_MS = 600000;
    if (entry && entry.value && Date.now() - entry.at < TTL_MS) {
      return json(res, scopeFunnelStatsPayload({ days, ...entry.value, cachedAt: entry.at }, req.user));
    }
    const snap = await readSnapshotCached(`funnel_stats_${days}d.json`);
    if (snap && snap.value && !snap.value.error) {
      // Warm the in-memory cache so subsequent hits skip the disk read too.
      funnelStatsCache.set(days, { at: snap.at, value: snap.value });
      return json(res, scopeFunnelStatsPayload({ ...snap.value, cachedAt: snap.at }, req.user));
    }
    if (entry && entry.pending) {
      entry.pending.then(val => json(res, scopeFunnelStatsPayload({ days, ...val, cachedAt: Date.now() }, req.user)))
                   .catch(err => json(res, { error: String(err && err.message || err) }, 500));
      return;
    }
    // Cloud Run has no python runtime and no PostHog creds; only the
    // operator's local server can run the live pipeline. Return whatever
    // we've got (empty snapshot if nothing) rather than hanging.
    if (auth.CLIENT_MODE) {
      return json(res, { days, error: 'snapshot_missing', cachedAt: null }, 503);
    }
    const scriptPath = path.join(DEST, 'scripts', 'project_stats_json.py');
    const pending = new Promise((resolve, reject) => {
      const child = spawn('python3', [scriptPath, '--days', String(days)], {
        env: process.env, cwd: DEST,
      });
      let out = '', err = '';
      child.stdout.on('data', d => out += d);
      child.stderr.on('data', d => err += d);
      child.on('error', reject);
      child.on('close', code => {
        if (code !== 0) return reject(new Error(err || ('exit ' + code)));
        try { resolve(JSON.parse(out)); } catch (e) { reject(e); }
      });
    });
    funnelStatsCache.set(days, { at: Date.now(), pending });
    pending.then(val => {
      funnelStatsCache.set(days, { at: Date.now(), value: val });
      json(res, scopeFunnelStatsPayload({ days, ...val, cachedAt: Date.now() }, req.user));
    }).catch(err => {
      funnelStatsCache.delete(days);
      json(res, { error: String(err && err.message || err) }, 500);
    });
    return;
  }

  // GET /api/dm/stats - per-project DM funnel (outreach, replies, interest tiers,
  // qualification, bookings, conversions). Window is "active in last N days"
  // (COALESCE(last_message_at, discovered_at)) to match /api/top/dms semantics.
  if (p === '/api/dm/stats' && req.method === 'GET') {
    const url = new URL(req.url, 'http://localhost');
    const days = Math.max(1, Math.min(90, parseInt(url.searchParams.get('days') || '1', 10) || 1));
    const windowHours = days * 24;
    // Normalize platform: 'x' folds into 'twitter' to match dms.platform storage.
    const rawPlatform = (url.searchParams.get('platform') || '').trim().toLowerCase();
    const platform = (rawPlatform === '' || rawPlatform === 'all') ? '' :
                     (rawPlatform === 'x' ? 'twitter' : rawPlatform);
    const platformOk = platform === '' || /^[a-z0-9_]{1,32}$/.test(platform);
    if (!platformOk) return json(res, { error: 'invalid platform' }, 400);
    const dmPc = auth.projectClause(req.user, "COALESCE(p_direct.project_name, p_via_reply.project_name, d.target_project)", url.searchParams.get('project'));
    if (!dmPc.ok) return json(res, { days, projects: [] });
    const whereParts = [
      "COALESCE(d.last_message_at, d.discovered_at) >= NOW() - INTERVAL '" + windowHours + " hours'",
      "COALESCE(p_direct.project_name, p_via_reply.project_name, d.target_project) IS NOT NULL",
    ];
    if (platform) {
      whereParts.push("CASE WHEN LOWER(d.platform) = 'x' THEN 'twitter' ELSE LOWER(d.platform) END = '" + platform + "'");
    }
    if (dmPc.clause) whereParts.push(dmPc.clause.replace(/^\s*AND\s+/, ''));
    const whereSql = 'WHERE ' + whereParts.join(' AND ');
    const q =
      "SELECT json_agg(row_to_json(r)) FROM (" +
        "SELECT COALESCE(p_direct.project_name, p_via_reply.project_name, d.target_project) AS name, " +
          "COUNT(*)::int AS dms, " +
          "COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM dm_messages m WHERE m.dm_id = d.id AND m.direction = 'inbound'))::int AS replied, " +
          "COUNT(*) FILTER (WHERE d.interest_level = 'hot')::int AS hot, " +
          "COUNT(*) FILTER (WHERE d.interest_level = 'warm')::int AS warm, " +
          "COUNT(*) FILTER (WHERE d.interest_level = 'general_discussion')::int AS general_discussion, " +
          "COUNT(*) FILTER (WHERE d.interest_level = 'cold')::int AS cold, " +
          "COUNT(*) FILTER (WHERE d.interest_level = 'not_our_prospect')::int AS not_our_prospect, " +
          "COUNT(*) FILTER (WHERE d.interest_level = 'declined')::int AS declined, " +
          "COUNT(*) FILTER (WHERE d.interest_level = 'no_response')::int AS no_response, " +
          "COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(d.icp_matches) e WHERE e->>'project' = d.target_project AND e->>'label' = 'icp_match'))::int AS icp_match, " +
          "COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(d.icp_matches) e WHERE e->>'project' = d.target_project AND e->>'label' = 'icp_miss'))::int AS icp_miss, " +
          "COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(d.icp_matches) e WHERE e->>'project' = d.target_project AND e->>'label' = 'disqualified'))::int AS icp_disqualified, " +
          "COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(d.icp_matches) e WHERE e->>'project' = d.target_project AND e->>'label' = 'unknown'))::int AS icp_unknown, " +
          "COUNT(*) FILTER (WHERE d.qualification_status = 'asked')::int AS asked, " +
          "COUNT(*) FILTER (WHERE d.qualification_status = 'answered')::int AS answered, " +
          "COUNT(*) FILTER (WHERE d.qualification_status = 'qualified')::int AS qualified, " +
          "COUNT(*) FILTER (WHERE d.qualification_status = 'disqualified')::int AS q_disqualified, " +
          "COUNT(*) FILTER (WHERE d.booking_link_sent_at IS NOT NULL)::int AS booking_sent, " +
          "COUNT(*) FILTER (WHERE d.conversation_status = 'converted')::int AS converted, " +
          "COUNT(*) FILTER (WHERE d.conversation_status = 'needs_human')::int AS needs_human " +
        "FROM dms d " +
        "LEFT JOIN posts   p_direct    ON p_direct.id    = d.post_id " +
        "LEFT JOIN replies r_link      ON r_link.id      = d.reply_id " +
        "LEFT JOIN posts   p_via_reply ON p_via_reply.id = r_link.post_id " +
        whereSql + " " +
        "GROUP BY name " +
        "ORDER BY dms DESC, replied DESC" +
      ") r";
    return (async () => {
      const rows = await pq(q);
      const projects = (rows && rows.length && rows[0].json_agg) ? rows[0].json_agg : [];
      return json(res, { days, projects });
    })().catch(e => json(res, { error: e.message }, 500));
  }

  // GET /api/project/status - per-project weight + target share + posts-by-platform
  // in the last N hours, with actual share and deficit (matches pick_project.py logic).
  // Cheap Postgres-only query so it's safe to expose without caching.
  if (p === '/api/project/status' && req.method === 'GET') {
    const url = new URL(req.url, 'http://localhost');
    const hours = Math.max(1, Math.min(24 * 30, parseInt(url.searchParams.get('hours') || '24', 10) || 24));
    let config = {};
    try { config = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8')); } catch {}
    const configuredProjects = Array.isArray(config.projects) ? config.projects : [];
    const weighted = configuredProjects.filter(p => (p.weight || 0) > 0);
    const totalWeight = weighted.reduce((a, p) => a + (p.weight || 0), 0) || 1;
    const platforms = ['reddit', 'twitter', 'linkedin', 'moltbook', 'github'];
    // Per-platform eligibility: a project is eligible to be picked for a
    // platform only if it has the data that platform's picker needs. Mirrors
    // scripts/pick_project.py and scripts/pick_thread_target.py. Projects
    // ineligible for a platform get target_share=null (shown as "NA" in the
    // dashboard) and are excluded from that platform's target-weight denom.
    // platforms_disabled is an explicit deny list (e.g. paperback-expert opts
    // out of moltbook because the HN audience isn't its ICP).
    const isDisabled = (p, plat) => Array.isArray(p.platforms_disabled) && p.platforms_disabled.includes(plat);
    const platformEligible = {
      github:   p => !isDisabled(p, 'github')   && Array.isArray(p.github_search_topics) && p.github_search_topics.length > 0,
      twitter:  p => !isDisabled(p, 'twitter')  && Array.isArray(p.twitter_topics)       && p.twitter_topics.length > 0,
      linkedin: p => !isDisabled(p, 'linkedin') && Array.isArray(p.linkedin_topics)      && p.linkedin_topics.length > 0,
      reddit:   p => !isDisabled(p, 'reddit'),
      moltbook: p => !isDisabled(p, 'moltbook'),
    };
    const totalWeightByPlatform = {};
    for (const plat of platforms) {
      totalWeightByPlatform[plat] = weighted
        .filter(p => platformEligible[plat](p))
        .reduce((a, p) => a + (p.weight || 0), 0);
    }
    const rows = await pq(
      "SELECT COALESCE(project_name, '(none)') AS project_name, platform, COUNT(*)::int AS n " +
      "FROM posts WHERE posted_at >= NOW() - INTERVAL '" + hours + " hours' " +
      "AND our_content <> '(mention - no original post)' " +
      "GROUP BY project_name, platform"
    ) || [];
    const byProject = {};
    let grandTotal = 0;
    const platformTotals = Object.fromEntries(platforms.map(p => [p, 0]));
    platformTotals['(other)'] = 0;
    rows.forEach(r => {
      const name = r.project_name || '(none)';
      const plat = (r.platform || '').toLowerCase();
      const n = Number(r.n) || 0;
      grandTotal += n;
      if (plat in platformTotals) platformTotals[plat] += n;
      else platformTotals['(other)'] += n;
      if (!byProject[name]) byProject[name] = { total: 0, by_platform: {} };
      byProject[name].total += n;
      byProject[name].by_platform[plat] = (byProject[name].by_platform[plat] || 0) + n;
    });
    const projects = weighted.map(p => {
      const name = p.name;
      const stats = byProject[name] || { total: 0, by_platform: {} };
      const target_share = (p.weight || 0) / totalWeight;
      const actual_share = grandTotal > 0 ? stats.total / grandTotal : 0;
      const per_platform = {};
      const target_share_by_platform = {};
      for (const plat of platforms) {
        per_platform[plat] = stats.by_platform[plat] || 0;
        if (!platformEligible[plat](p)) {
          target_share_by_platform[plat] = null;
        } else {
          const denom = totalWeightByPlatform[plat] || 0;
          target_share_by_platform[plat] = denom > 0 ? (p.weight || 0) / denom : 0;
        }
      }
      return {
        name,
        weight: p.weight || 0,
        target_share,
        target_share_by_platform,
        total: stats.total,
        actual_share,
        deficit: target_share - actual_share,
        by_platform: per_platform,
        website: p.website || null,
      };
    }).sort((a, b) => b.weight - a.weight || a.name.localeCompare(b.name));
    // Surface any posts that didn't match a weighted project, so the matrix adds up.
    const knownNames = new Set(weighted.map(p => p.name));
    const unassigned = Object.entries(byProject)
      .filter(([name]) => !knownNames.has(name))
      .map(([name, stats]) => ({
        name,
        weight: 0,
        target_share: 0,
        total: stats.total,
        actual_share: grandTotal > 0 ? stats.total / grandTotal : 0,
        deficit: -(grandTotal > 0 ? stats.total / grandTotal : 0),
        by_platform: Object.fromEntries(platforms.map(pl => [pl, stats.by_platform[pl] || 0])),
        website: null,
        unassigned: true,
      }));
    return json(res, {
      hours,
      generated_at_ms: Date.now(),
      total_weight: totalWeight,
      total_weight_by_platform: totalWeightByPlatform,
      grand_total: grandTotal,
      platform_totals: platformTotals,
      projects,
      unassigned,
    });
  }

  // GET /api/deploy/status - latest Vercel production deploy per project.
  // Written every ~5 min to skill/cache/deploy_status.json by launchd
  // com.m13v.social-deploy-status (scripts/project_deploy_status.py). If the
  // snapshot is missing or >20 min stale, we run the scraper synchronously.
  if (p === '/api/deploy/status' && req.method === 'GET') {
    const snap = await readSnapshotCached('deploy_status.json', 20 * 60 * 1000);
    if (snap && snap.value) {
      return json(res, { ...snap.value, cachedAt: snap.at });
    }
    if (auth.CLIENT_MODE) {
      return json(res, { error: 'snapshot_missing', cachedAt: null }, 503);
    }
    const scriptPath = path.join(DEST, 'scripts', 'project_deploy_status.py');
    const child = spawn('python3', [scriptPath], { env: process.env, cwd: DEST });
    let err = '';
    child.stderr.on('data', d => err += d);
    child.on('close', code => {
      if (code !== 0) return json(res, { error: err || ('exit ' + code) }, 500);
      const fresh = readSnapshot('deploy_status.json', 60 * 60 * 1000);
      if (!fresh) return json(res, { error: 'snapshot missing after refresh' }, 500);
      json(res, { ...fresh.value, cachedAt: fresh.at });
    });
    child.on('error', e => json(res, { error: String(e.message || e) }, 500));
    return;
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
<script>
  // Apply persisted theme before first paint to avoid FOUC.
  (function() {
    try {
      var t = localStorage.getItem('sa_theme');
      if (t !== 'dark' && t !== 'light') t = 'light';
      document.documentElement.setAttribute('data-theme', t);
    } catch (e) {
      document.documentElement.setAttribute('data-theme', 'light');
    }
  })();
</script>
<style>
  /* ===== Theme tokens ===== */
  :root {
    /* Light theme (default) */
    --bg: #fafafa;
    --bg-card: #ffffff;
    --bg-subtle: #f4f4f5;
    --bg-inset: #f9fafb;
    --bg-hover: #f3f4f6;
    --bg-button: #f4f4f5;
    --bg-button-hover: #e5e7eb;
    --bg-chip: #f4f4f5;
    --text: #111827;
    --text-strong: #000000;
    --text-secondary: #4b5563;
    --text-muted: #6b7280;
    --text-faint: #9ca3af;
    --text-very-faint: #d1d5db;
    --border: #e5e7eb;
    --border-strong: #d1d5db;
    --border-hover: #9ca3af;
    --border-input: #d4d4d8;
    --divider: #f3f4f6;
    --link: #2563eb;
    --accent: #7c3aed;
    --accent-hover: #6d28d9;
    --accent-soft: #7c3aed;
    --accent-soft-hover: #6d28d9;
    --accent-on: #ffffff;
    --accent-panel-bg: #faf5ff;
    --accent-panel-border: #e9d5ff;
    --cyan: #0891b2;
    --cyan-soft: rgba(8, 145, 178, 0.18);
    --pill-inverse-bg: #000000;
    --pill-inverse-text: #ffffff;
    --shadow-modal: rgba(0,0,0,0.35);
    --shadow-dropdown: rgba(0,0,0,0.1);
    --row-flash-bg: rgba(8, 145, 178, 0.14);
    --toggle-knob: #ffffff;
  }
  [data-theme="dark"] {
    --bg: #0a0a0a;
    --bg-card: #171717;
    --bg-subtle: #0f0f0f;
    --bg-inset: #0d0d0d;
    --bg-hover: #1c1c1c;
    --bg-button: #262626;
    --bg-button-hover: #333;
    --bg-chip: #262626;
    --text: #e5e5e5;
    --text-strong: #fafafa;
    --text-secondary: #a3a3a3;
    --text-muted: #737373;
    --text-faint: #525252;
    --text-very-faint: #3f3f46;
    --border: #262626;
    --border-strong: #404040;
    --border-hover: #525252;
    --border-input: #404040;
    --divider: #1f1f1f;
    --link: #60a5fa;
    --accent: #7c3aed;
    --accent-hover: #6d28d9;
    --accent-soft: #8b5cf6;
    --accent-soft-hover: #a78bfa;
    --accent-on: #ffffff;
    --accent-panel-bg: #1a1625;
    --accent-panel-border: #3b2d63;
    --cyan: #22d3ee;
    --cyan-soft: rgba(34, 211, 238, 0.22);
    --pill-inverse-bg: #ffffff;
    --pill-inverse-text: #000000;
    --shadow-modal: rgba(0,0,0,0.72);
    --shadow-dropdown: rgba(0,0,0,0.5);
    --row-flash-bg: rgba(34, 211, 238, 0.22);
    --toggle-knob: #e5e5e5;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
  .header { padding: 20px 24px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 20px; font-weight: 600; }
  .header .pending { background: var(--accent); color: var(--accent-on); padding: 4px 12px; border-radius: 12px; font-size: 13px; }

  .theme-toggle { background: var(--bg-button); border: 1px solid var(--border); color: var(--text); padding: 6px 10px; border-radius: 8px; cursor: pointer; font-size: 13px; display: inline-flex; align-items: center; gap: 6px; font-family: inherit; line-height: 1; }
  .theme-toggle:hover { background: var(--bg-button-hover); border-color: var(--border-strong); }
  .theme-toggle .theme-icon { font-size: 14px; line-height: 1; }
  .theme-toggle .sun-icon { display: none; }
  .theme-toggle .moon-icon { display: inline; }
  [data-theme="dark"] .theme-toggle .sun-icon { display: inline; }
  [data-theme="dark"] .theme-toggle .moon-icon { display: none; }

  .tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); padding: 0 24px; }
  .tab { padding: 12px 20px; cursor: pointer; color: var(--text); font-size: 14px; border-bottom: 2px solid transparent; transition: all 0.15s; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--text); border-bottom-color: var(--accent); }
  .content { padding: 24px; }
  .matrix-wrapper { overflow-x: auto; }
  .matrix-table { width: 100%; border-collapse: collapse; background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .matrix-table th { text-align: center; padding: 12px 16px; font-size: 12px; font-weight: 500; color: var(--text); text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); background: var(--bg-subtle); }
  .matrix-table th.row-header { width: 90px; }
  .matrix-table th.freq-header { width: 90px; }
  .freq-cell { text-align: center; vertical-align: middle; background: var(--bg-subtle); }
  .freq-cell select { font-size: 12px; padding: 4px 6px; }
  .matrix-table td { padding: 10px 8px; font-size: 13px; border-bottom: 1px solid var(--divider); vertical-align: middle; text-align: center; }
  .matrix-table td.row-label { text-align: left; padding-left: 16px; font-weight: 600; font-size: 14px; color: var(--text); background: var(--bg-subtle); width: 100px; }
  .matrix-table tr:last-child td { border-bottom: none; }
  .matrix-cell { display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .matrix-cell .badge { font-size: 11px; padding: 2px 8px; cursor: pointer; }
  .matrix-cell .badge:hover { filter: brightness(1.3); }
  .matrix-cell .cell-info { font-size: 11px; color: var(--text); }
  .matrix-cell .cell-actions { display: flex; gap: 4px; margin-top: 2px; }
  .matrix-cell .cell-actions .btn { padding: 3px 8px; font-size: 11px; }
  .matrix-cell-empty { color: var(--text-very-faint); font-size: 20px; }
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
  .badge.stopped { background: var(--bg-button); color: var(--text); }
  .toggle-switch { position: relative; display: inline-block; width: 40px; height: 22px; cursor: pointer; flex-shrink: 0; }
  .toggle-switch input { opacity: 0; width: 0; height: 0; position: absolute; }
  .toggle-slider { position: absolute; top: 0; left: 0; right: 0; bottom: 0; background: var(--border-strong); border: 1px solid var(--border-hover); border-radius: 22px; transition: background 0.15s, border-color 0.15s; }
  .toggle-slider::before { content: ''; position: absolute; height: 16px; width: 16px; left: 2px; top: 2px; background: var(--toggle-knob); border-radius: 50%; transition: transform 0.15s, background 0.15s; box-shadow: 0 1px 3px rgba(0,0,0,0.4); }
  .toggle-switch input:checked + .toggle-slider { background: #10b981; border-color: #10b981; }
  .toggle-switch input:checked + .toggle-slider::before { transform: translateX(18px); background: #ffffff; }
  .toggle-switch:hover .toggle-slider { filter: brightness(1.15); }
  .toggle-switch input:disabled + .toggle-slider { opacity: 0.5; cursor: not-allowed; }
  .toggle-label { font-size: 10px; font-weight: 700; letter-spacing: 0.05em; color: var(--text); margin-left: 6px; }
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
  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; }
  .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .card-title { font-size: 16px; font-weight: 600; }
  .card-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; font-size: 13px; color: var(--text); }
  .card-row span:last-child { color: var(--text); }
  .btn { padding: 8px 16px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--bg-button); color: var(--text); cursor: pointer; font-size: 13px; transition: all 0.15s; }
  .btn:hover { background: var(--bg-button-hover); border-color: var(--border-hover); }
  .btn.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-on); }
  .btn.primary:hover { background: var(--accent-hover); }
  .btn.danger { background: #991b1b; border-color: #991b1b; color: #ffffff; }
  .btn.danger:hover { background: #7f1d1d; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  select { padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--bg-button); color: var(--text); font-size: 13px; cursor: pointer; }
  .log-viewer { background: var(--bg-inset); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-top: 16px; }
  .log-controls { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .log-content { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; max-height: 500px; overflow-y: auto; margin-top: 12px; color: var(--text); padding: 12px; background: var(--bg); border-radius: 8px; }
  .settings-section { margin-bottom: 24px; }
  .settings-section h3 { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: var(--text); }
  .field { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
  .field label { min-width: 140px; font-size: 13px; color: var(--text); }
  .field input { flex: 1; padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border-strong); background: var(--bg-card); color: var(--text); font-size: 13px; }
  .toast { position: fixed; bottom: 24px; right: 24px; background: #065f46; color: #6ee7b7; padding: 12px 20px; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 100; }
  .toast.show { opacity: 1; }
  .toast.error { background: #7f1d1d; color: #fca5a5; }
  .pending-card { background: var(--accent-panel-bg); border-color: var(--accent-panel-border); }
  .reply-item { padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
  .reply-item:last-child { border-bottom: none; }
  .reply-author { color: var(--accent-soft); font-weight: 500; }
  .reply-platform { color: var(--text); font-size: 11px; text-transform: uppercase; }
  .reply-text { color: var(--text); margin-top: 2px; }
  .hidden { display: none; }

  /* Activity tab */
  .activity-controls { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
  .activity-filter-group { display: flex; gap: 6px; flex-wrap: wrap; }
  .activity-chip {
    padding: 4px 10px; border-radius: 999px; font-size: 12px; cursor: pointer;
    border: 1px solid var(--border); background: var(--bg-card); color: var(--text);
    transition: all 0.15s; user-select: none;
  }
  .activity-chip:hover { border-color: var(--border-hover); color: var(--text); }
  .activity-chip.active { background: var(--bg-chip); border-color: var(--border-hover); color: var(--text); }
  .activity-chip.active.ev-posted   { background: #064e3b; border-color: #10b981; color: #6ee7b7; }
  .activity-chip.active.ev-replied  { background: #0c4a6e; border-color: #0ea5e9; color: #7dd3fc; }
  .activity-chip.active.ev-skipped  { background: #422006; border-color: #d97706; color: #fbbf24; }
  .activity-chip.active.ev-mention  { background: var(--bg-chip); border-color: var(--text-muted); color: var(--text); }
  .activity-chip.active.ev-dm_sent  { background: #3b0764; border-color: #a855f7; color: #d8b4fe; }
  .activity-chip.active.ev-dm_reply_sent { background: #500724; border-color: #ec4899; color: #f9a8d4; }
  .activity-chip.active.ev-page_published_serp   { background: #422006; border-color: #f59e0b; color: #fcd34d; }
  .activity-chip.active.ev-page_published_gsc    { background: #134e4a; border-color: #14b8a6; color: #5eead4; }
  .activity-chip.active.ev-page_published_reddit { background: #7c2d12; border-color: #f97316; color: #fdba74; }
  .activity-chip.active.ev-page_published_top    { background: #4a044e; border-color: #d946ef; color: #f5d0fe; }
  .activity-chip.active.ev-page_published_roundup { background: #881337; border-color: #f43f5e; color: #fda4af; }
  .activity-chip.active.ev-page_improved         { background: #365314; border-color: #84cc16; color: #bef264; }
  .activity-chip.active.ev-resurrected { background: #1e3a8a; border-color: #3b82f6; color: #93c5fd; }

  .activity-status { display: flex; align-items: center; gap: 6px; margin-left: auto; font-size: 12px; color: var(--cyan); }
  .activity-live-dot {
    width: 8px; height: 8px; border-radius: 50%; background: var(--cyan);
    box-shadow: 0 0 8px var(--cyan);
    animation: activityHeartbeat 1.4s ease-in-out infinite;
  }
  @keyframes activityHeartbeat {
    0%, 100% { opacity: 1; transform: scale(1); }
    50%      { opacity: 0.5; transform: scale(0.7); }
  }

  .activity-wrapper { overflow-x: auto; }
  .activity-table { width: 100%; border-collapse: collapse; background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }
  .activity-table th {
    text-align: left; padding: 10px 14px; font-size: 11px; font-weight: 500;
    color: var(--text); text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 1px solid var(--border); background: var(--bg-subtle);
  }
  .activity-table td {
    padding: 10px 14px; font-size: 13px; border-bottom: 1px solid var(--divider);
    vertical-align: top; color: var(--text);
  }
  .activity-table tr:last-child td { border-bottom: none; }
  .activity-table tr:hover td { background: var(--bg-hover); }
  .activity-event-cell { display: flex; flex-direction: column; gap: 4px; white-space: nowrap; }
  .activity-time { color: var(--text); font-size: 12px; font-variant-numeric: tabular-nums; }
  .activity-platform { display: inline-flex; align-items: center; justify-content: center; gap: 6px; color: var(--text); font-size: 24px; text-transform: lowercase; }
  .activity-platform svg { height: 1em; width: 1em; flex-shrink: 0; fill: currentColor; }
  .activity-platform .plat-mono { display: inline-flex; align-items: center; justify-content: center; height: 1em; width: 1em; border-radius: 4px; background: var(--bg-chip); color: var(--text); font-size: 0.7em; font-weight: 700; letter-spacing: 0; line-height: 1; }
  .activity-platform-cell { text-align: center; vertical-align: middle; }
  .activity-summary-url { color: var(--link); text-decoration: none; word-break: break-all; }
  .activity-summary-url:hover { text-decoration: underline; }
  .activity-project-cell { display: flex; flex-direction: column; gap: 3px; }
  .activity-project { color: var(--text); font-size: 13px; font-weight: 500; word-break: break-all; }
  .activity-detail { color: var(--text); font-size: 11px; font-family: 'SF Mono', monospace; word-break: break-word; }
  .activity-summary { color: var(--text); line-height: 1.4; }
  .activity-summary-link { color: var(--link); text-decoration: none; font-size: 12px; opacity: 0.7; }
  .activity-summary-link:hover { opacity: 1; text-decoration: underline; }
  .activity-link { color: var(--link); text-decoration: none; font-size: 14px; opacity: 0.7; }
  .activity-link:hover { opacity: 1; }

  .ev-pill {
    display: inline-block; padding: 2px 8px; border-radius: 6px;
    font-size: 11px; font-weight: 600; letter-spacing: 0.02em; text-transform: lowercase;
  }
  .ev-pill.ev-posted  { background: #064e3b; color: #6ee7b7; }
  .ev-pill.ev-replied { background: #0c4a6e; color: #7dd3fc; }
  .ev-pill.ev-skipped { background: #422006; color: #fbbf24; }
  .ev-pill.ev-mention { background: var(--bg-chip); color: var(--text); }
  .ev-pill.ev-dm_sent { background: #3b0764; color: #d8b4fe; }
  .ev-pill.ev-dm_reply_sent { background: #500724; color: #f9a8d4; }
  .ev-pill.ev-page_published_serp   { background: #422006; color: #fcd34d; border: 1px solid #f59e0b; }
  .ev-pill.ev-page_published_gsc    { background: #134e4a; color: #5eead4; border: 1px solid #14b8a6; }
  .ev-pill.ev-page_published_reddit { background: #7c2d12; color: #fdba74; border: 1px solid #f97316; }
  .ev-pill.ev-page_published_top    { background: #4a044e; color: #f5d0fe; border: 1px solid #d946ef; }
  .ev-pill.ev-page_published_roundup { background: #881337; color: #fda4af; border: 1px solid #f43f5e; }
  .ev-pill.ev-page_improved         { background: #365314; color: #bef264; border: 1px solid #84cc16; }
  .ev-pill.ev-resurrected { background: #1e3a8a; color: #93c5fd; border: 1px solid #3b82f6; }

  .activity-search {
    flex: 1; min-width: 220px; max-width: 420px; background: var(--bg-subtle); border: 1px solid var(--border);
    border-radius: 8px; padding: 7px 12px; font-size: 13px; color: var(--text); outline: none;
    transition: border-color 0.15s;
  }
  .activity-search:focus { border-color: var(--border-hover); }
  .activity-search::placeholder { color: var(--text-faint); }
  .activity-sortable { cursor: pointer; user-select: none; }
  .activity-sortable:hover .activity-header-label { color: var(--text); }
  .activity-header-label { display: inline-flex; align-items: center; gap: 4px; }
  .activity-sort-arrow { font-size: 10px; color: var(--text-faint); min-width: 8px; }
  .activity-sort-arrow.active { color: var(--text); }
  .activity-filter-row th {
    padding: 6px 14px; background: var(--bg); border-bottom: 1px solid var(--border);
    text-transform: none; letter-spacing: 0; font-weight: 400;
  }
  .activity-filter-stack { display: flex; flex-direction: column; gap: 4px; }
  .activity-filter-stack .activity-filter-group { gap: 4px; }
  .activity-filter-dropdown { position: relative; display: inline-block; }
  .activity-filter-dropdown > summary {
    list-style: none; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
    background: var(--bg-subtle); border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 10px; font-size: 12px; color: var(--text); user-select: none;
  }
  .activity-filter-dropdown > summary::-webkit-details-marker { display: none; }
  .activity-filter-dropdown > summary::after {
    content: '\u25BE'; font-size: 10px; color: var(--text); margin-left: 2px;
  }
  .activity-filter-dropdown[open] > summary { border-color: var(--border-hover); color: var(--text); }
  .activity-filter-dropdown[open] > summary::after { color: var(--text); }
  .activity-filter-dropdown:hover > summary { border-color: var(--border-hover); }
  .activity-filter-menu {
    position: absolute; top: calc(100% + 4px); left: 0; z-index: 20;
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px; min-width: 180px; box-shadow: 0 8px 24px var(--shadow-dropdown);
    display: flex; flex-direction: column; gap: 6px;
  }
  .activity-filter-menu .activity-filter-group { display: flex; flex-wrap: wrap; gap: 4px; }
  .activity-filter-menu-actions { display: flex; gap: 4px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
  .activity-filter-menu-btn {
    background: transparent; border: 1px solid var(--border); color: var(--text);
    padding: 3px 8px; font-size: 11px; border-radius: 4px; cursor: pointer;
  }
  .activity-filter-menu-btn:hover { border-color: var(--border-hover); color: var(--text); }
  .activity-col-filter {
    width: 100%; background: var(--bg-subtle); border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 8px; font-size: 12px; color: var(--text); outline: none;
  }
  .activity-col-filter:focus { border-color: var(--border-hover); }
  .activity-col-filter::placeholder { color: var(--text-faint); }
  .activity-pagination {
    display: flex; align-items: center; justify-content: flex-end; gap: 10px;
    margin-top: 12px; font-size: 12px; color: var(--text);
  }
  .activity-pagination .pager-btn {
    background: var(--bg-card); border: 1px solid var(--border); color: var(--text);
    padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 12px;
  }
  .activity-pagination .pager-btn:hover:not(:disabled) { border-color: var(--border-hover); }
  .activity-pagination .pager-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .activity-pagination select {
    background: var(--bg-card); border: 1px solid var(--border); color: var(--text);
    padding: 3px 6px; border-radius: 6px; font-size: 12px; cursor: pointer;
  }
  .activity-row-new { animation: activityRowFlash 2.6s ease-out; }
  @keyframes activityRowFlash {
    0%   { background: var(--row-flash-bg); box-shadow: inset 3px 0 0 var(--cyan); }
    60%  { background: var(--cyan-soft); box-shadow: inset 3px 0 0 var(--cyan); }
    100% { background: transparent; box-shadow: inset 3px 0 0 transparent; }
  }

  .activity-filters { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
  .activity-filters .style-stats-pill-row { align-items: center; }
  .activity-filters .style-stats-pill-row .label { min-width: 70px; }
  .activity-filters .activity-filter-group { display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .activity-filters .activity-filter-menu-btn { background: var(--bg-subtle); }
  .activity-filters .activity-filter-menu-btn:hover { background: var(--bg-hover); }

  /* Top tab */
  .top-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 8px; gap: 12px; flex-wrap: wrap; }
  .top-title { font-size: 13px; font-weight: 600; color: var(--text); text-transform: uppercase; letter-spacing: 0.05em; }
  .top-subtabs { display: inline-flex; gap: 4px; background: var(--bg-subtle); border: 1px solid var(--border); border-radius: 8px; padding: 4px; }
  .top-subtab { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px; cursor: pointer; color: var(--text-secondary); font-size: 12px; font-weight: 600; text-transform: none; letter-spacing: 0; border-radius: 6px; transition: background 0.15s, color 0.15s; user-select: none; }
  .top-subtab:hover { color: var(--text); background: var(--bg-hover); }
  .top-subtab.active { background: var(--accent); color: var(--accent-on); }
  .top-subtab.active:hover { background: var(--accent); }
  .top-subtab-icon { font-size: 15px; line-height: 1; }
  .top-subtab-label { font-size: 12px; font-weight: 700; letter-spacing: 0.02em; }
  .top-subtab-sub { font-size: 10px; font-weight: 500; opacity: 0.75; text-transform: lowercase; letter-spacing: 0.02em; }
  .top-subtab.active .top-subtab-sub { opacity: 0.9; }
  .top-subtab-help { margin: 0 0 12px; font-size: 12px; color: var(--text-secondary); line-height: 1.45; padding: 0 2px; }
  .top-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .top-filters { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
  .top-filters .style-stats-pill-row .label { min-width: 70px; }
  .top-search {
    background: var(--bg-subtle); color: var(--text); border: 1px solid var(--border); border-radius: 6px;
    padding: 4px 10px; font-size: 12px; font-family: inherit; min-width: 220px;
  }
  .top-search:hover { border-color: var(--border-strong); }
  .top-search:focus { outline: none; border-color: var(--accent-soft); }
  .top-total { font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; margin-left: 4px; }
  .top-post-content { display: flex; flex-direction: column; gap: 4px; max-width: 100%; }
  .top-post-text { color: var(--text); font-size: 13px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; }
  .top-post-link { color: var(--accent-soft); font-size: 12px; font-family: 'SF Mono', 'Fira Code', monospace; word-break: break-all; }
  .top-post-link:hover { color: var(--accent-soft-hover); text-decoration: underline; }
  .top-pages-header { color: var(--text); font-size: 13px; font-weight: 600; font-family: 'SF Mono', 'Fira Code', monospace; word-break: break-all; line-height: 1.4; }
  .top-pages-url { color: var(--accent-soft); font-size: 11px; font-family: 'SF Mono', 'Fira Code', monospace; word-break: break-all; line-height: 1.4; margin-top: 2px; }
  a.top-post-link:has(.top-pages-header) { display: block; text-decoration: none; }
  .top-post-meta { color: var(--text-muted); font-size: 11px; font-family: 'SF Mono', 'Fira Code', monospace; line-height: 1.4; }
  .top-post-meta a { color: var(--text-muted); text-decoration: none; }
  .top-post-meta a:hover { color: var(--text-secondary); text-decoration: underline; }
  .top-post-parent-title { color: var(--text-secondary); font-style: italic; }
  .top-project-cell { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; min-width: 0; }
  .top-project-name { color: var(--text); font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
  .top-kind-pill { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; border: 1px solid var(--border); line-height: 1.5; }
  .top-kind-pill--thread { background: rgba(96, 165, 250, 0.12); color: #60a5fa; border-color: rgba(96, 165, 250, 0.35); }
  .top-kind-pill--comment { background: rgba(167, 139, 250, 0.12); color: #a78bfa; border-color: rgba(167, 139, 250, 0.35); }
  .top-kind-pill--rec { background: rgba(251, 191, 36, 0.14); color: #fbbf24; border-color: rgba(251, 191, 36, 0.4); margin-left: 4px; }
  .top-stats-cell { display: flex; flex-direction: column; gap: 2px; font-variant-numeric: tabular-nums; font-size: 12px; }
  .top-stats-bit { color: var(--text); white-space: nowrap; }
  .top-stats-k { color: var(--text-muted); font-weight: 600; margin-right: 4px; }
  /* Top tab table: fixed layout so Content gets 50% and small columns truncate their headers */
  #top-table-container .style-stats-table { table-layout: fixed; }
  #top-table-container .style-stats-table th,
  #top-table-container .style-stats-table td { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 10px 10px; }
  #top-table-container .style-stats-table td[data-col-key="our_content"] { white-space: normal; overflow: visible; text-overflow: clip; }
  #top-table-container .style-stats-table td[data-col-key="project_name"],
  #top-table-container .style-stats-table td[data-col-key="score"] { white-space: normal; overflow: visible; text-overflow: clip; vertical-align: top; }
  #top-table-container .style-stats-table th .activity-header-label { overflow: hidden; text-overflow: ellipsis; display: inline-block; max-width: 100%; vertical-align: bottom; }
  /* Inline header stack: sortable label on top, filter dropdown below */
  .activity-th-stack { display: flex; flex-direction: column; align-items: stretch; gap: 4px; min-width: 0; }
  .style-stats-table th[style*="text-align:right"] .activity-th-stack { align-items: flex-end; }
  .activity-col-filter-inline {
    background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 4px; font-size: 11px; font-family: inherit; cursor: pointer;
    width: 100%; max-width: 100%; min-width: 0; font-weight: 400;
  }
  .activity-col-filter-inline:hover { border-color: var(--border-strong); }
  .activity-col-filter-inline:focus { outline: none; border-color: var(--accent-soft); }
  .activity-col-filter-placeholder { visibility: hidden; }
  #top-pages-container .style-stats-table { table-layout: fixed; }
  #top-pages-container .style-stats-table th,
  #top-pages-container .style-stats-table td { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 10px 10px; }
  #top-pages-container .style-stats-table td[data-col-key="path"] { white-space: normal; overflow: visible; text-overflow: clip; word-break: break-all; }
  /* DMs sub-tab */
  #top-dms-container .style-stats-table { table-layout: fixed; }
  #top-dms-container .style-stats-table th,
  #top-dms-container .style-stats-table td { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding: 10px 10px; }
  #top-dms-container .style-stats-table td[data-col-key="last_msg"] { white-space: normal; overflow: visible; text-overflow: clip; word-break: break-word; color: var(--text-secondary); font-size: 12px; }
  #top-dms-container .style-stats-table td[data-col-key="last_ts"] { white-space: normal; overflow: visible; text-overflow: clip; vertical-align: top; }
  .dm-class-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }
  .dm-class-human    { background: #7f1d1d; color: #fecaca; }
  .dm-class-hot      { background: #b91c1c; color: #fff; }
  .dm-class-warm     { background: #b45309; color: #fff; }
  .dm-class-general  { background: var(--bg-chip); color: var(--text); border: 1px solid var(--border); }
  .dm-class-cold     { background: #1e3a8a; color: #bfdbfe; }
  .dm-class-declined { background: var(--bg-chip); color: var(--text-secondary); border: 1px solid var(--border); }
  .dm-class-notours  { background: var(--bg-chip); color: var(--text-muted); border: 1px solid var(--border); }
  .dm-class-converted{ background: #14532d; color: #bbf7d0; }
  .dm-class-closed   { background: var(--bg-chip); color: var(--text-muted); border: 1px solid var(--border); }
  .dm-class-none     { background: var(--bg-chip); color: var(--text-secondary); border: 1px solid var(--border); }
  .dm-class-sub      { color: var(--text-muted); font-size: 10px; margin-top: 2px; text-transform: lowercase; }
  .dm-thread-author  { color: var(--text); font-weight: 600; font-size: 13px; }
  .dm-thread-tier    { color: var(--text-muted); font-size: 11px; font-family: 'SF Mono', 'Fira Code', monospace; }
  .dm-last-dir       { display: inline-block; background: var(--pill-inverse-bg); color: var(--pill-inverse-text); font-weight: 700; font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; padding: 1px 6px; border-radius: 3px; margin-right: 6px; vertical-align: baseline; }
  .dm-thread-subline { margin-top: 4px; }
  .dm-last-ts        { display: flex; flex-direction: column; align-items: flex-end; line-height: 1.25; }
  .dm-last-ts-rel    { font-weight: 500; }
  .dm-last-ts-abs    { font-size: 11px; color: var(--text-muted); font-family: 'SF Mono', 'Fira Code', monospace; margin-top: 2px; white-space: nowrap; }
  .dm-prospect-pill  { display: inline-block; max-width: 100%; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg-subtle); color: var(--link); font-size: 10px; line-height: 1.3; cursor: pointer; text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: inherit; }
  .dm-prospect-pill:hover { background: var(--bg-hover); border-color: var(--link); color: var(--link); }
  .dm-meta-row       { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
  .dm-meta-chip      { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 9.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; background: var(--bg-chip); color: var(--text-secondary); border: 1px solid var(--border); }
  .dm-icp-icp_match    { background: #14532d; color: #bbf7d0; border-color: #166534; }
  .dm-icp-icp_miss     { background: #3f2e1e; color: #fde68a; border-color: #78350f; }
  .dm-icp-disqualified { background: #3f1d1d; color: #fecaca; border-color: #7f1d1d; }
  .dm-icp-unknown      { background: var(--bg-chip); color: var(--text-muted); border-color: var(--border); }
  .dm-qual-qualified    { background: #14532d; color: #bbf7d0; border-color: #166534; }
  .dm-qual-disqualified { background: #3f1d1d; color: #fecaca; border-color: #7f1d1d; }
  .dm-qual-asked        { background: #1e3a8a; color: #bfdbfe; border-color: #1d4ed8; }
  .dm-qual-answered     { background: #312e81; color: #c7d2fe; border-color: #4338ca; }
  .dm-qual-pending      { background: var(--bg-chip); color: var(--text-secondary); border-color: var(--border); }
  .dm-qual-note        { color: var(--text-muted); font-size: 10px; margin-top: 3px; white-space: normal; word-break: break-word; line-height: 1.3; }

  /* DM thread expansion (inline row under a clicked DM) */
  #top-dms-container tr[data-row-id] { cursor: pointer; }
  #top-dms-container tr[data-row-id]:hover td { background: var(--bg-hover); }
  #top-dms-container tr.dm-row-expanded td { background: var(--bg-subtle); border-bottom-color: transparent; }
  .dm-load-more { display: flex; justify-content: center; padding: 14px 0 8px; }
  .dm-load-more .btn { font-size: 12px; }
  #top-dms-container tr.dm-exp-row { cursor: default; }
  #top-dms-container tr.dm-exp-row > td.dm-exp-cell { padding: 0; background: transparent; border-top: none; white-space: normal; overflow: visible; text-overflow: clip; }
  #top-dms-container tr.dm-exp-row:hover > td.dm-exp-cell { background: transparent; }
  .dm-exp-inner { margin: 8px 48px 16px; padding: 14px 18px 16px; background: var(--bg-chip); border: 1px solid var(--border); border-left: 3px solid var(--accent); border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
  .dm-exp-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 10px; }
  .dm-exp-meta-chip { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: 600; text-transform: lowercase; letter-spacing: 0.03em; background: var(--bg-chip); color: var(--text-secondary); border: 1px solid var(--border); }
  .dm-exp-meta-link { font-size: 11px; color: var(--link); text-decoration: none; margin-left: auto; }
  .dm-exp-meta-link:hover { text-decoration: underline; }
  .dm-exp-thread { display: flex; flex-direction: column; gap: 6px; }
  .dm-exp-empty { color: var(--text-muted); font-size: 12px; font-style: italic; padding: 6px 0; }
  .dm-exp-msg { max-width: 72%; padding: 8px 12px; border-radius: 10px; font-size: 12.5px; line-height: 1.45; border: 1px solid var(--border); }
  .dm-exp-msg-inbound  { align-self: flex-start; background: var(--bg-card); color: var(--text); border-top-left-radius: 3px; }
  .dm-exp-msg-outbound { align-self: flex-end;   background: var(--accent); color: var(--accent-on); border-color: var(--accent); border-top-right-radius: 3px; }
  .dm-exp-msg-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 3px; font-size: 10px; text-transform: lowercase; letter-spacing: 0.04em; opacity: 0.8; }
  .dm-exp-msg-outbound .dm-exp-msg-head { color: var(--accent-on); }
  .dm-exp-msg-inbound  .dm-exp-msg-head { color: var(--text-muted); }
  .dm-exp-msg-author { font-weight: 600; }
  .dm-exp-msg-time   { font-variant-numeric: tabular-nums; }
  .dm-exp-msg-body   { white-space: pre-wrap; word-break: break-word; }
  .dm-exp-ctx        { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; padding-bottom: 10px; border-bottom: 1px dashed var(--border); }
  .dm-exp-ctx-section { background: var(--bg-card); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 12px; line-height: 1.45; }
  .dm-exp-ctx-head   { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; font-size: 10px; text-transform: lowercase; letter-spacing: 0.06em; color: var(--text-muted); font-weight: 600; }
  .dm-exp-ctx-label  { color: var(--text-secondary); }
  .dm-exp-ctx-author { color: var(--text-muted); font-weight: 500; }
  .dm-exp-ctx-link   { margin-left: auto; color: var(--link); text-decoration: none; font-weight: 500; }
  .dm-exp-ctx-link:hover { text-decoration: underline; }
  .dm-exp-ctx-body   { white-space: pre-wrap; word-break: break-word; color: var(--text); }
  .dm-exp-ctx-title  { font-weight: 600; color: var(--text); margin-bottom: 3px; }
  .dm-exp-ctx-fallback { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 9px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; background: #fef3c7; color: #92400e; border: 1px solid #fde68a; margin-left: 4px; cursor: help; }

  /* Escalation card — surfaces the human-handoff request and the queue of
     instructions written for the DM-reply agent. Sits above the context block. */
  .dm-esc-card { background: var(--bg-card); border: 1px solid #fbbf24; border-left: 3px solid #f59e0b; border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; font-size: 12px; line-height: 1.45; display: flex; flex-direction: column; gap: 10px; }
  .dm-esc-head { display: flex; align-items: baseline; gap: 8px; }
  .dm-esc-tag  { display: inline-block; padding: 2px 7px; border-radius: 3px; font-size: 9px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
  .dm-esc-reason { color: var(--text); white-space: pre-wrap; word-break: break-word; }
  .dm-esc-list { display: flex; flex-direction: column; gap: 6px; }
  .dm-esc-item { background: var(--bg); border: 1px solid var(--border); border-radius: 5px; padding: 6px 8px; }
  .dm-esc-item-meta { display: flex; align-items: center; gap: 6px; font-size: 10px; text-transform: lowercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 4px; font-weight: 600; }
  .dm-esc-item-label { font-size: 9px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-muted); font-weight: 700; margin-top: 6px; margin-bottom: 2px; }
  .dm-esc-item-label-reply { color: #047857; }
  .dm-esc-item-body { white-space: pre-wrap; word-break: break-word; color: var(--text); font-size: 12px; }
  .dm-esc-item-reply { white-space: pre-wrap; word-break: break-word; color: var(--text); font-size: 12px; padding: 6px 8px; background: rgba(16, 185, 129, 0.08); border-left: 2px solid #10b981; border-radius: 0 4px 4px 0; }
  .dm-esc-item-reply-missing { color: var(--text-muted); font-style: italic; background: transparent; border-left-color: var(--border); }
  .dm-esc-status { display: inline-block; padding: 1px 5px; border-radius: 3px; font-size: 9px; font-weight: 700; }
  .dm-esc-status-pending { background: #fef3c7; color: #92400e; }
  .dm-esc-status-sent    { background: #d1fae5; color: #065f46; }
  .dm-esc-status-failed  { background: #fee2e2; color: #991b1b; }
  .dm-esc-source { padding: 1px 5px; border: 1px solid var(--border); border-radius: 3px; }
  .dm-esc-compose { display: flex; flex-direction: column; gap: 6px; }
  .dm-esc-textarea { width: 100%; box-sizing: border-box; min-height: 64px; padding: 8px 10px; font-family: inherit; font-size: 12px; line-height: 1.5; color: var(--text); background: var(--bg); border: 1px solid var(--border); border-radius: 5px; resize: vertical; }
  .dm-esc-textarea:focus { outline: none; border-color: var(--link); }
  .dm-esc-bar { display: flex; align-items: center; gap: 8px; }
  .dm-esc-hint { font-size: 10px; color: var(--text-muted); margin-right: auto; }
  .dm-esc-submit { padding: 5px 12px; font-size: 11px; font-weight: 600; color: #fff; background: #f59e0b; border: 1px solid #d97706; border-radius: 4px; cursor: pointer; }
  .dm-esc-submit:hover { background: #d97706; }
  .dm-esc-submit:disabled { opacity: 0.6; cursor: not-allowed; }
  .dm-esc-feedback { font-size: 11px; padding: 4px 0; }
  .dm-esc-feedback-ok  { color: #047857; }
  .dm-esc-feedback-err { color: #b91c1c; }

  .prospect-modal-overlay { position: fixed; inset: 0; background: var(--shadow-modal); display: flex; align-items: flex-start; justify-content: center; z-index: 9999; padding: 60px 20px 20px; overflow-y: auto; }
  .prospect-modal { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; max-width: 640px; width: 100%; padding: 24px 28px; color: var(--text); font-size: 13px; line-height: 1.5; }
  .prospect-modal h3 { margin: 0 0 4px; font-size: 16px; color: var(--text-strong); }
  .prospect-modal .prospect-sub { color: var(--text-secondary); font-size: 12px; margin-bottom: 16px; }
  .prospect-modal .prospect-row { margin-bottom: 12px; }
  .prospect-modal .prospect-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 3px; }
  .prospect-modal .prospect-val { color: var(--text); white-space: pre-wrap; word-break: break-word; }
  .prospect-modal .prospect-close { float: right; background: transparent; border: 1px solid var(--border); color: var(--text-secondary); border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; font-family: inherit; }
  .prospect-modal .prospect-close:hover { color: var(--text-strong); border-color: var(--border-hover); }
  .prospect-modal a { color: var(--link); }

  /* Status tab: 24h activity stats */
  .stats-top-filters { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
  .stats-wrapper { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 16px 20px; margin-bottom: 20px; }
  .stats-header { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 12px; }
  .stats-title { font-size: 13px; font-weight: 600; color: var(--text); text-transform: uppercase; letter-spacing: 0.05em; }
  .stats-total { font-size: 12px; color: var(--text); font-variant-numeric: tabular-nums; }
  .stats-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px; }
  .stat-card {
    background: var(--bg-subtle); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px;
    display: flex; flex-direction: column; gap: 6px;
  }
  .stat-card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
  .stat-card-label { font-size: 11px; color: var(--text); text-transform: lowercase; letter-spacing: 0.02em; display: inline-flex; align-items: center; gap: 5px; }
  .stat-card-info { display: inline-flex; align-items: center; justify-content: center; width: 12px; height: 12px; border-radius: 50%; border: 1px solid var(--border-strong, var(--border)); color: var(--text-muted); font-size: 9px; font-weight: 600; font-style: italic; font-family: Georgia, serif; line-height: 1; cursor: help; user-select: none; opacity: 0.7; transition: opacity 0.1s, color 0.1s, border-color 0.1s; }
  .stat-card-info:hover { opacity: 1; color: var(--text); border-color: var(--text-muted); }
  /* Per-column info icon used by mountSortableTable when a column has helpText.
     The popover itself is rendered by the global .sa-tooltip handler; this
     class only styles the icon. */
  .col-info { display: inline-flex; align-items: center; justify-content: center; width: 12px; height: 12px; border-radius: 50%; border: 1px solid var(--border-strong, var(--border)); color: var(--text-muted); font-size: 9px; font-weight: 600; font-style: italic; font-family: Georgia, serif; line-height: 1; cursor: help; user-select: none; opacity: 0.6; margin-left: 4px; vertical-align: middle; }
  .col-info:hover { opacity: 1; color: var(--text); border-color: var(--text-muted); }
  /* ===== Global instant-hover tooltip =====
     Single shared element appended to <body> by the JS handler. Any element
     with [data-tooltip] or [title] uses this; native title attributes are
     auto-migrated so the OS-level hover delay never fires. To opt in from
     code, set data-tooltip="..." (preferred) or title="..." on the element. */
  .sa-tooltip { position: fixed; display: none; background: var(--bg-panel, #fff); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 12px; font-weight: 400; font-style: normal; font-family: inherit; text-transform: none; letter-spacing: normal; color: var(--text); white-space: pre-line; max-width: 320px; z-index: 10000; box-shadow: 0 4px 12px rgba(0,0,0,0.12); pointer-events: none; text-align: left; line-height: 1.45; }
  .sa-tooltip.visible { display: block; }
  .stat-card-count { font-size: 22px; font-weight: 700; color: var(--text); font-variant-numeric: tabular-nums; line-height: 1; }
  .stat-card.zero .stat-card-count { color: var(--text-very-faint); }
  .stat-card.ev-posted              { border-left: 3px solid #10b981; }
  .stat-card.ev-replied             { border-left: 3px solid #0ea5e9; }
  .stat-card.ev-skipped             { border-left: 3px solid #d97706; }
  .stat-card.ev-mention             { border-left: 3px solid var(--text-muted); }
  .stat-card.ev-dm_sent             { border-left: 3px solid #a855f7; }
  .stat-card.ev-dm_reply_sent       { border-left: 3px solid #ec4899; }
  .stat-card.ev-page_published_serp   { border-left: 3px solid #f59e0b; }
  .stat-card.ev-page_published_gsc    { border-left: 3px solid #14b8a6; }
  .stat-card.ev-page_published_reddit { border-left: 3px solid #f97316; }
  .stat-card.ev-page_published_top    { border-left: 3px solid #d946ef; }
  .stat-card.ev-page_published_roundup { border-left: 3px solid #f43f5e; }
  .stat-card.ev-page_improved         { border-left: 3px solid #84cc16; }
  .stat-card.ev-resurrected         { border-left: 3px solid #3b82f6; }
  .stat-card-breakdown { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 11px; color: var(--text); }
  .stat-plat { display: inline-flex; align-items: center; gap: 4px; font-variant-numeric: tabular-nums; }
  .stat-plat svg { height: 11px; width: 11px; fill: currentColor; }
  .stat-plat .plat-mono { height: 11px; width: 11px; border-radius: 2px; background: var(--bg-chip); color: var(--text); font-size: 9px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; }
  .stat-plat-count { color: var(--text); }

  /* Status tab: engagement style breakdown (collapsed by default) */
  .style-stats-section { background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; margin-bottom: 20px; overflow: hidden; }
  .style-stats-section > summary { list-style: none; cursor: pointer; padding: 14px 20px; display: flex; align-items: baseline; justify-content: space-between; gap: 12px; user-select: none; }
  .style-stats-section > summary::-webkit-details-marker { display: none; }
  .style-stats-section > summary:hover { background: var(--bg-hover); }
  .style-stats-title { font-size: 13px; font-weight: 600; color: var(--text); text-transform: uppercase; letter-spacing: 0.05em; display: flex; align-items: center; gap: 8px; }
  .style-stats-caret { display: inline-block; width: 10px; font-size: 10px; color: var(--text-muted); transition: transform 0.15s; }
  .style-stats-section[open] .style-stats-caret { transform: rotate(90deg); }
  .style-stats-total { font-size: 12px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
  /* Views-per-day bar chart in the Stats tab */
  .views-chart { padding: 16px 20px 20px; display: flex; flex-direction: column; gap: 10px; border-top: 1px solid var(--border); }
  .views-chart-bars { display: flex; align-items: flex-end; gap: 2px; height: 140px; min-height: 140px; }
  .views-chart-bar { flex: 1 1 0; min-width: 4px; background: var(--accent, #3b82f6); border-radius: 2px 2px 0 0; position: relative; transition: background 0.1s; }
  .views-chart-bar:hover { background: var(--accent-hover, #2563eb); }
  .views-chart-bar.empty { background: var(--bg-subtle); }
  .views-chart-axis { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
  .views-chart-axis span { white-space: nowrap; }
  .views-chart-empty { padding: 24px 20px; color: var(--text-secondary); font-size: 13px; text-align: center; }

  /* Combined daily-metrics line chart (stats tab, above filters). Legend
     pills double as series toggles: click to hide/show a line, Y-axis
     auto-rescales to the max of currently-visible series. */
  #daily-metrics { margin-bottom: 16px; }
  .daily-metrics-legend { display: flex; flex-wrap: wrap; gap: 6px 8px; padding: 14px 20px 8px; }
  .daily-metrics-legend-pill { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border); background: var(--bg-subtle); color: var(--text); font-size: 12px; font-family: inherit; cursor: pointer; user-select: none; transition: background 0.1s, border-color 0.1s, opacity 0.1s; }
  .daily-metrics-legend-pill:hover { border-color: var(--border-strong); background: var(--bg-hover); }
  .daily-metrics-legend-pill .swatch { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
  .daily-metrics-legend-pill .count { color: var(--text-muted); font-variant-numeric: tabular-nums; font-size: 11px; }
  .daily-metrics-legend-pill.off { opacity: 0.4; }
  .daily-metrics-legend-pill.off .swatch { background: var(--border) !important; }
  .daily-metrics-chart { padding: 4px 20px 16px; position: relative; }
  .daily-metrics-chart svg { display: block; width: 100%; height: 260px; overflow: visible; }
  .daily-metrics-chart .gridline { stroke: var(--border); stroke-width: 1; stroke-dasharray: 2 3; }
  .daily-metrics-chart .axis-text { fill: var(--text-secondary); font-size: 10px; font-variant-numeric: tabular-nums; }
  .daily-metrics-chart .series-line { fill: none; stroke-width: 1.75; stroke-linejoin: round; stroke-linecap: round; }
  .daily-metrics-chart .hover-line { stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0; pointer-events: none; }
  .daily-metrics-tooltip { position: absolute; pointer-events: none; background: var(--bg-panel, #fff); border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); min-width: 180px; opacity: 0; transform: translate(-50%, -100%); transition: opacity 0.08s; z-index: 5; }
  .daily-metrics-tooltip .tt-day { font-weight: 600; margin-bottom: 4px; color: var(--text); }
  .daily-metrics-tooltip .tt-row { display: flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; color: var(--text); }
  .daily-metrics-tooltip .tt-row .swatch { width: 8px; height: 8px; border-radius: 2px; display: inline-block; }
  .daily-metrics-tooltip .tt-row .val { margin-left: auto; }
  /* Deploy Health: slim inline bar when collapsed, alert colors when there is something worth attention */
  #deploy-health:not([open]) { margin-bottom: 10px; border-radius: 8px; }
  #deploy-health:not([open]) > summary { padding: 6px 14px; }
  #deploy-health:not([open]) .style-stats-title { font-size: 11px; text-transform: none; letter-spacing: normal; font-weight: 600; }
  #deploy-health:not([open]) .style-stats-total { font-size: 11px; }
  #deploy-health[data-alert="warn"] { border-color: #fcd34d; }
  #deploy-health[data-alert="warn"] > summary { background: #fffbeb; }
  #deploy-health[data-alert="warn"] .style-stats-title,
  #deploy-health[data-alert="warn"] .style-stats-total { color: #b45309; }
  #deploy-health[data-alert="error"] { border-color: #fca5a5; }
  #deploy-health[data-alert="error"] > summary { background: #fef2f2; }
  #deploy-health[data-alert="error"] .style-stats-title,
  #deploy-health[data-alert="error"] .style-stats-total { color: #b91c1c; }
  .style-stats-table-wrapper { border-top: 1px solid var(--border); overflow-x: auto; }
  .style-stats-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .style-stats-table th, .style-stats-table td { padding: 10px 16px; text-align: right; font-variant-numeric: tabular-nums; border-bottom: 1px solid var(--divider); }
  .style-stats-table th { font-size: 11px; font-weight: 500; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.04em; background: var(--bg-subtle); }
  .style-stats-table th:first-child, .style-stats-table td:first-child { text-align: left; color: var(--text); font-weight: 600; }
  .style-stats-table th:nth-child(1), .style-stats-table td:nth-child(1),
  .style-stats-table th:nth-child(2), .style-stats-table td:nth-child(2) { white-space: nowrap; }
  .style-stats-table tbody tr:last-child td { border-bottom: none; }
  .style-stats-table tbody tr:hover td { background: var(--bg-hover); }
  .style-stats-table tfoot td { border-top: 2px solid var(--border-strong, var(--border)); border-bottom: none; background: var(--bg-subtle); font-weight: 600; color: var(--text); }
  .style-stats-table tfoot td:first-child { text-transform: uppercase; font-size: 11px; letter-spacing: 0.04em; color: var(--text-secondary); }
  .style-stats-empty { padding: 16px 20px; color: var(--text-muted); font-size: 13px; border-top: 1px solid var(--border); }
  .style-stats-controls { padding: 10px 20px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: var(--text-secondary); }
  .style-stats-pill-row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
  .style-stats-pill-row .label { color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; font-size: 11px; margin-right: 4px; }
  .style-stats-pill { background: var(--bg-subtle); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 3px 10px; font-size: 12px; font-family: inherit; cursor: pointer; user-select: none; transition: background 0.1s, border-color 0.1s; }
  .style-stats-pill:hover { border-color: var(--border-strong); background: var(--bg-hover); }
  .style-stats-pill.active { background: var(--accent-panel-bg); border-color: #3b82f6; color: var(--text); }

  @media (max-width: 600px) { .cards { grid-template-columns: 1fr; } .content { padding: 16px; } }

  /* Client-mode auth overlay. Non-admin users see the app with admin-only
     sections hidden via body.sa-non-admin; unauthenticated users see only
     the login card. */
  .sa-login-overlay { position: fixed; inset: 0; background: var(--bg); display: none; align-items: center; justify-content: center; z-index: 9999; }
  .sa-login-card { background: var(--bg-panel, #fff); border: 1px solid var(--border, #e5e7eb); border-radius: 12px; padding: 32px; width: 360px; max-width: 90vw; box-shadow: 0 8px 24px rgba(0,0,0,0.08); }
  .sa-login-card h1 { margin: 0 0 4px; font-size: 20px; }
  .sa-login-card p { color: var(--text-muted, #6b7280); margin: 0 0 20px; font-size: 13px; }
  .sa-login-card input { width: 100%; padding: 10px 12px; border: 1px solid var(--border, #e5e7eb); border-radius: 6px; background: var(--bg-subtle, #f9fafb); color: var(--text); font: inherit; margin-bottom: 10px; box-sizing: border-box; }
  .sa-login-card button { width: 100%; padding: 10px; background: #2563eb; color: #fff; border: none; border-radius: 6px; font: inherit; font-weight: 600; cursor: pointer; }
  .sa-login-card button:hover { background: #1d4ed8; }
  .sa-login-error { color: #dc2626; font-size: 13px; min-height: 18px; margin-top: 6px; }
  body.sa-non-admin .sa-admin-only { display: none !important; }
  body.sa-cloud .sa-local-only { display: none !important; }
  body.sa-authed-pending .header, body.sa-authed-pending .tabs, body.sa-authed-pending .content { visibility: hidden; }
</style>
<script>
  window.SA_CONFIG = { clientMode: __SA_CLIENT_MODE_PLACEHOLDER__, firebase: __SA_FIREBASE_CONFIG_PLACEHOLDER__ };
  // Install fetch wrapper upfront so any /api/ call picks up the token
  // once auth resolves. Missing-token calls will 401 server-side in CLIENT_MODE.
  (function() {
    var origFetch = window.fetch.bind(window);
    window.fetch = function(url, opts) {
      opts = opts || {};
      try {
        var isApi = typeof url === 'string' && url.startsWith('/api/');
        if (isApi && window.SA_ID_TOKEN) {
          opts.headers = Object.assign({}, opts.headers || {}, { Authorization: 'Bearer ' + window.SA_ID_TOKEN });
        }
      } catch (e) {}
      return origFetch(url, opts);
    };
  })();
</script>
<script src="https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js"></script>
<script src="https://www.gstatic.com/firebasejs/10.14.1/firebase-auth-compat.js"></script>
</head>
<body class="sa-authed-pending">

<div class="sa-login-overlay" id="sa-login-overlay">
  <div class="sa-login-card">
    <h1>Sign in</h1>
    <p id="sa-login-desc">Enter your email and we'll send you a sign-in link.</p>
    <form id="sa-login-form">
      <input type="email" id="sa-login-email" placeholder="Email" autocomplete="username" required>
      <button type="submit" id="sa-login-submit">Send sign-in link</button>
      <div class="sa-login-error" id="sa-login-error"></div>
    </form>
  </div>
</div>

<div class="header">
  <h1>Social Autoposter</h1>
  <div style="display:flex;align-items:center;gap:12px;">
    <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" title="Toggle light/dark theme" aria-label="Toggle theme">
      <span class="theme-icon moon-icon">\u{1F319}</span>
      <span class="theme-icon sun-icon">\u2600\uFE0F</span>
    </button>
    <button class="btn sa-local-only" id="pause-btn" onclick="togglePause()" style="font-weight:600;"></button>
    <span class="pending sa-local-only" id="pending-badge">-- pending</span>
    <span class="sa-user-badge" id="sa-user-badge" style="display:none;font-size:12px;color:var(--text-muted);padding:4px 10px;border:1px solid var(--border);border-radius:999px;background:var(--bg-subtle);"></span>
    <button class="btn sa-client-only" id="sa-signout-btn" onclick="saSignOut()" style="font-weight:600;display:none;">Sign out</button>
  </div>
</div>

<div class="tabs">
  <div class="tab sa-local-only" data-tab="status">Status</div>
  <div class="tab active" data-tab="stats">Stats</div>
  <div class="tab" data-tab="activity">Activity</div>
  <div class="tab" data-tab="top">Top</div>
  <div class="tab sa-admin-only" data-tab="logs">Logs</div>
  <div class="tab sa-admin-only" data-tab="settings">Settings</div>
</div>

<div class="content hidden sa-local-only" id="tab-status">
  <div class="stats-top-filters">
    <div class="style-stats-pill-row" id="status-window-pills" data-selected="7d">
      <span class="label">Window</span>
      <button type="button" class="style-stats-pill" data-value="24h">Last 24h</button>
      <button type="button" class="style-stats-pill active" data-value="7d">Last 7d</button>
      <button type="button" class="style-stats-pill" data-value="14d">Last 14d</button>
      <button type="button" class="style-stats-pill" data-value="30d">Last 30d</button>
    </div>
  </div>
  <details class="style-stats-section" id="cost-stats" open>
    <summary>
      <span class="style-stats-title"><span class="style-stats-caret">&#9654;</span><span id="cost-stats-heading">Cost per Activity (last 24 hours)</span></span>
      <span class="style-stats-total" id="cost-stats-total"></span>
    </summary>
    <div class="style-stats-controls">
      <div class="style-stats-pill-row" id="cost-stats-platform-pills" data-selected="all">
        <span class="label">Platform</span>
        <button type="button" class="style-stats-pill active" data-value="all">All</button>
        <button type="button" class="style-stats-pill" data-value="reddit">Reddit</button>
        <button type="button" class="style-stats-pill" data-value="twitter">Twitter / X</button>
        <button type="button" class="style-stats-pill" data-value="linkedin">LinkedIn</button>
        <button type="button" class="style-stats-pill" data-value="moltbook">MoltBook</button>
        <button type="button" class="style-stats-pill" data-value="github">GitHub</button>
        <button type="button" class="style-stats-pill" data-value="seo">SEO</button>
        <button type="button" class="style-stats-pill" data-value="email">Email</button>
      </div>
    </div>
    <div id="cost-stats-body">
      <div class="style-stats-empty">Loading&hellip;</div>
    </div>
  </details>
  <details class="style-stats-section" id="project-status" open>
    <summary>
      <span class="style-stats-title"><span class="style-stats-caret">▶</span><span id="project-status-heading">Project Status (last 24h)</span></span>
      <span class="style-stats-total" id="project-status-total"></span>
    </summary>
    <div id="project-status-body">
      <div class="style-stats-empty">Loading…</div>
    </div>
  </details>
  <details class="style-stats-section" id="deploy-health">
    <summary>
      <span class="style-stats-title"><span class="style-stats-caret">\u25B6</span>Deploy Health</span>
      <span class="style-stats-total" id="deploy-health-total"></span>
    </summary>
    <div id="deploy-health-body">
      <div class="style-stats-empty">Loading\u2026</div>
    </div>
  </details>
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
  <div id="jobs-history-section" style="margin-top: 24px;">
    <div class="card">
      <div class="card-header" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <span class="card-title">Job History</span>
      </div>
      <div style="padding:10px 16px;border-bottom:1px solid var(--accent-panel-border);display:flex;flex-direction:column;gap:8px;">
        <div class="style-stats-pill-row" id="jobs-history-platform-pills" data-selected="all" style="margin:0;">
          <span class="label" style="font-size:11px;color:var(--muted);margin-right:4px;">Platform</span>
          <button type="button" class="style-stats-pill active" data-value="all">All</button>
          <button type="button" class="style-stats-pill" data-value="reddit">Reddit</button>
          <button type="button" class="style-stats-pill" data-value="twitter">Twitter</button>
          <button type="button" class="style-stats-pill" data-value="linkedin">LinkedIn</button>
          <button type="button" class="style-stats-pill" data-value="moltbook">MoltBook</button>
          <button type="button" class="style-stats-pill" data-value="github">GitHub</button>
        </div>
        <div class="style-stats-pill-row" id="jobs-history-type-pills" data-selected="all" style="margin:0;">
          <span class="label" style="font-size:11px;color:var(--muted);margin-right:4px;">Job</span>
          <button type="button" class="style-stats-pill active" data-value="all">All</button>
          <button type="button" class="style-stats-pill" data-value="post">Post</button>
          <button type="button" class="style-stats-pill" data-value="engage">Engage</button>
          <button type="button" class="style-stats-pill" data-value="link-edit">Link Edit</button>
          <button type="button" class="style-stats-pill" data-value="dm-outreach">DM Outreach</button>
          <button type="button" class="style-stats-pill" data-value="dm-replies">DM Replies</button>
          <button type="button" class="style-stats-pill" data-value="octolens">Octolens</button>
          <button type="button" class="style-stats-pill" data-value="stats">Stats</button>
          <button type="button" class="style-stats-pill" data-value="audit">Audit</button>
          <button type="button" class="style-stats-pill" data-value="seo">SEO</button>
          <button type="button" class="style-stats-pill" data-value="report">Report</button>
          <button type="button" class="style-stats-pill" data-value="other">Other</button>
        </div>
      </div>
      <div id="jobs-history-body">
        <div class="style-stats-empty" style="padding:16px;">Loading…</div>
      </div>
    </div>
  </div>
  <div id="pending-section" style="margin-top: 16px;"></div>
</div>

<div class="content" id="tab-stats">
  <details class="style-stats-section" id="daily-metrics" open>
    <summary>
      <span class="style-stats-title"><span class="style-stats-caret">▶</span><span>Daily Metrics (last 30 days)</span></span>
      <span class="style-stats-total" id="daily-metrics-status"></span>
    </summary>
    <div id="daily-metrics-body">
      <div id="daily-metrics-legend" class="daily-metrics-legend"></div>
      <div id="daily-metrics-chart" class="daily-metrics-chart">
        <div class="views-chart-empty">Loading…</div>
      </div>
    </div>
  </details>
  <div class="stats-top-filters">
    <div class="style-stats-pill-row" id="stats-window-pills" data-selected="7d">
      <span class="label">Window</span>
      <button type="button" class="style-stats-pill" data-value="24h">Last 24h</button>
      <button type="button" class="style-stats-pill active" data-value="7d">Last 7d</button>
      <button type="button" class="style-stats-pill" data-value="14d">Last 14d</button>
      <button type="button" class="style-stats-pill" data-value="30d">Last 30d</button>
    </div>
    <div class="style-stats-pill-row" id="style-stats-platform-pills" data-selected="all">
      <span class="label">Platform</span>
    </div>
    <div class="style-stats-pill-row" id="style-stats-project-pills" data-selected="all">
      <span class="label">Project</span>
    </div>
  </div>
  <div class="stats-wrapper">
    <div class="stats-header">
      <span class="stats-title" id="stats-title">Last 24 hours</span>
      <span class="stats-total" id="stats-total"></span>
    </div>
    <div class="stats-grid" id="stats-grid"></div>
  </div>
  <details class="style-stats-section" id="style-stats" open>
    <summary>
      <span class="style-stats-title"><span class="style-stats-caret">\u25B6</span><span id="style-stats-heading">Posts by Engagement Style (24h)</span></span>
      <span class="style-stats-total" id="style-stats-total"></span>
    </summary>
    <div id="style-stats-body">
      <div class="style-stats-empty">Loading\u2026</div>
    </div>
  </details>
  <details class="style-stats-section" id="funnel-stats" open>
    <summary>
      <span class="style-stats-title"><span class="style-stats-caret">\u25B6</span><span id="funnel-stats-heading">Project Funnel Stats (last 24 hours)</span></span>
      <span class="style-stats-total" id="funnel-stats-total"></span>
    </summary>
    <div id="funnel-stats-body">
      <div class="style-stats-empty">Click to load\u2026</div>
    </div>
  </details>
  <details class="style-stats-section" id="dm-stats" open>
    <summary>
      <span class="style-stats-title"><span class="style-stats-caret">\u25B6</span><span id="dm-stats-heading">DM Funnel Stats (last 24 hours)</span></span>
      <span class="style-stats-total" id="dm-stats-total"></span>
    </summary>
    <div id="dm-stats-body">
      <div class="style-stats-empty">Loading\u2026</div>
    </div>
  </details>
</div>

<div class="content hidden" id="tab-activity">
  <div class="activity-controls">
    <input type="text" id="activity-search" placeholder="Search all fields&hellip;" class="activity-search" />
    <div class="activity-status">
      <span class="activity-live-dot"></span>
      <span id="activity-status-text">live</span>
      <span id="activity-count" style="color:var(--text);margin-left:8px;"></span>
    </div>
  </div>
  <div class="activity-filters">
    <div class="style-stats-pill-row" id="activity-type-pills">
      <span class="label">Event</span>
      <button type="button" class="activity-filter-menu-btn" data-filter-action="type-all">All</button>
      <button type="button" class="activity-filter-menu-btn" data-filter-action="type-none">None</button>
      <span class="activity-filter-group" id="activity-type-filters"></span>
    </div>
    <div class="style-stats-pill-row" id="activity-platform-pills">
      <span class="label">Platform</span>
      <button type="button" class="activity-filter-menu-btn" data-filter-action="platform-all">All</button>
      <button type="button" class="activity-filter-menu-btn" data-filter-action="platform-none">None</button>
      <span class="activity-filter-group" id="activity-platform-filters"></span>
    </div>
    <div class="style-stats-pill-row" id="activity-project-pills">
      <span class="label">Project</span>
      <button type="button" class="activity-filter-menu-btn" data-filter-action="project-all">All</button>
      <button type="button" class="activity-filter-menu-btn" data-filter-action="project-none">None</button>
      <span class="activity-filter-group" id="activity-project-filters"></span>
    </div>
  </div>
  <div class="activity-wrapper">
    <table class="activity-table">
      <thead>
        <tr>
          <th style="width:140px;" class="activity-sortable" data-sort="occurred_at">
            <span class="activity-header-label">Event <span class="activity-sort-arrow" data-sort-arrow="occurred_at"></span></span>
          </th>
          <th style="width:56px;" class="activity-sortable" data-sort="platform">
            <span class="activity-header-label">Platform <span class="activity-sort-arrow" data-sort-arrow="platform"></span></span>
          </th>
          <th style="width:220px;" class="activity-sortable" data-sort="project">
            <span class="activity-header-label">Project <span class="activity-sort-arrow" data-sort-arrow="project"></span></span>
          </th>
          <th class="activity-sortable" data-sort="summary">
            <span class="activity-header-label">What <span class="activity-sort-arrow" data-sort-arrow="summary"></span></span>
          </th>
          <th style="width:90px;text-align:right;" class="activity-sortable" data-sort="cost_usd">
            <span class="activity-header-label">Cost <span class="activity-sort-arrow" data-sort-arrow="cost_usd"></span></span>
          </th>
        </tr>
      </thead>
      <tbody id="activity-body">
        <tr><td colspan="5" style="text-align:center;color:var(--text);padding:40px;">Loading&hellip;</td></tr>
      </tbody>
    </table>
  </div>
  <div class="activity-pagination" id="activity-pagination"></div>
</div>

<div class="content hidden" id="tab-top">
  <div class="top-header">
    <div class="top-subtabs" role="tablist" aria-label="Top tab sections">
      <span class="top-subtab active" data-subtab="threads" role="tab" aria-selected="true" title="Top original posts/threads your accounts have published">
        <span class="top-subtab-icon" aria-hidden="true">\ud83d\udce2</span>
        <span class="top-subtab-label">Threads</span>
        <span class="top-subtab-sub">your posts</span>
      </span>
      <span class="top-subtab" data-subtab="comments" role="tab" aria-selected="false" title="Top comments your accounts have left under other people\u2019s threads">
        <span class="top-subtab-icon" aria-hidden="true">\ud83d\udcac</span>
        <span class="top-subtab-label">Comments</span>
        <span class="top-subtab-sub">your replies</span>
      </span>
      <span class="top-subtab" data-subtab="pages" role="tab" aria-selected="false" title="Top landing/SEO pages on your sites by pageviews">
        <span class="top-subtab-icon" aria-hidden="true">\ud83d\udcc4</span>
        <span class="top-subtab-label">Pages</span>
        <span class="top-subtab-sub">SEO traffic</span>
      </span>
      <span class="top-subtab" data-subtab="dms" role="tab" aria-selected="false" title="Direct message conversations with prospects">
        <span class="top-subtab-icon" aria-hidden="true">\u2709\ufe0f</span>
        <span class="top-subtab-label">DMs</span>
        <span class="top-subtab-sub">prospect chats</span>
      </span>
    </div>
    <div class="top-controls">
      <input id="top-search" class="top-search" type="search" placeholder="Search posts\u2026" />
      <span class="top-total" id="top-total"></span>
    </div>
  </div>
  <div class="top-subtab-help" id="top-subtab-help">Top original posts/threads your accounts have published, ranked by reach and reactions.</div>
  <div class="top-filters">
    <div class="style-stats-pill-row" id="top-window-pills" data-selected="7d">
      <span class="label">Window</span>
      <button type="button" class="style-stats-pill" data-value="24h">Last 24h</button>
      <button type="button" class="style-stats-pill active" data-value="7d">Last 7d</button>
      <button type="button" class="style-stats-pill" data-value="14d">Last 14d</button>
      <button type="button" class="style-stats-pill" data-value="30d">Last 30d</button>
      <button type="button" class="style-stats-pill" data-value="90d">Last 90d</button>
      <button type="button" class="style-stats-pill" data-value="all">All time</button>
    </div>
    <div class="style-stats-pill-row" id="top-platform-pills" data-selected="all">
      <span class="label">Platform</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
      <button type="button" class="style-stats-pill" data-value="reddit">Reddit</button>
      <button type="button" class="style-stats-pill" data-value="twitter">Twitter / X</button>
      <button type="button" class="style-stats-pill" data-value="linkedin">LinkedIn</button>
      <button type="button" class="style-stats-pill" data-value="moltbook">Moltbook</button>
    </div>
    <div class="style-stats-pill-row" id="top-project-pills" data-selected="all">
      <span class="label">Project</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
    </div>
    <div class="style-stats-pill-row hidden" id="top-pages-source-pills" data-selected="seo">
      <span class="label">Source</span>
      <button type="button" class="style-stats-pill active" data-value="seo">SEO only</button>
      <button type="button" class="style-stats-pill" data-value="all">All</button>
    </div>
    <div class="style-stats-pill-row hidden" id="top-dm-dir-pills" data-selected="all">
      <span class="label">Direction</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
      <button type="button" class="style-stats-pill" data-value="in">IN</button>
      <button type="button" class="style-stats-pill" data-value="out">OUT</button>
    </div>
    <div class="style-stats-pill-row hidden" id="top-dm-interest-pills" data-selected="all">
      <span class="label">Interest</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
      <button type="button" class="style-stats-pill" data-value="hot">Hot</button>
      <button type="button" class="style-stats-pill" data-value="warm">Warm</button>
      <button type="button" class="style-stats-pill" data-value="general_discussion">General</button>
      <button type="button" class="style-stats-pill" data-value="cold">Cold</button>
      <button type="button" class="style-stats-pill" data-value="not_our_prospect">Not ours</button>
      <button type="button" class="style-stats-pill" data-value="declined">Declined</button>
    </div>
    <div class="style-stats-pill-row hidden" id="top-dm-mode-pills" data-selected="all">
      <span class="label">Mode</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
      <button type="button" class="style-stats-pill" data-value="rapport">Rapport</button>
      <button type="button" class="style-stats-pill" data-value="pitch">Pitch</button>
    </div>
    <div class="style-stats-pill-row hidden" id="top-dm-tier-pills" data-selected="all">
      <span class="label">Tier</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
      <button type="button" class="style-stats-pill" data-value="1">1</button>
      <button type="button" class="style-stats-pill" data-value="2">2</button>
      <button type="button" class="style-stats-pill" data-value="3">3</button>
    </div>
    <div class="style-stats-pill-row hidden" id="top-dm-qual-pills" data-selected="all">
      <span class="label">Qualification</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
      <button type="button" class="style-stats-pill" data-value="pending">Pending</button>
      <button type="button" class="style-stats-pill" data-value="asked">Asked</button>
      <button type="button" class="style-stats-pill" data-value="answered">Answered</button>
      <button type="button" class="style-stats-pill" data-value="qualified">Qualified</button>
      <button type="button" class="style-stats-pill" data-value="disqualified">Disqualified</button>
    </div>
    <div class="style-stats-pill-row hidden" id="top-dm-status-pills" data-selected="all">
      <span class="label">Status</span>
      <button type="button" class="style-stats-pill active" data-value="all">All</button>
      <button type="button" class="style-stats-pill" data-value="active">Active</button>
      <button type="button" class="style-stats-pill" data-value="needs_reply">Needs reply</button>
      <button type="button" class="style-stats-pill" data-value="stale">Stale</button>
      <button type="button" class="style-stats-pill" data-value="needs_human">Needs human</button>
    </div>
  </div>
  <div id="top-table-container">
    <div class="style-stats-empty">Loading\u2026</div>
  </div>
  <div id="top-pages-container" class="hidden">
    <div class="style-stats-empty">Loading\u2026 (first call can take 15\u201330s)</div>
  </div>
  <div id="top-pages-unknown-container" class="hidden"></div>
  <div id="top-dms-container" class="hidden">
    <div class="style-stats-empty">Loading\u2026</div>
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
  { label: '5 min', value: 300 },
  { label: '10 min', value: 600 },
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

// ===== Global instant-hover tooltip =====
// Single shared tooltip element used by every element with data-tooltip or
// title. On first hover the title is migrated into data-tooltip and removed
// so the native browser tooltip (with its OS-level delay) never fires.
// aria-label is mirrored so screen readers still expose the text.
//
// Standard going forward: use data-tooltip="..." on any element that needs a
// tooltip. Existing title="..." attributes also work; they get upgraded
// automatically on first hover.
(function() {
  let tipEl = null;
  let currentHost = null;
  function ensureTip() {
    if (tipEl) return tipEl;
    tipEl = document.createElement('div');
    tipEl.className = 'sa-tooltip';
    tipEl.setAttribute('role', 'tooltip');
    document.body.appendChild(tipEl);
    return tipEl;
  }
  function getText(el) {
    // Prefer title when present: a re-render may have just written a fresh
    // value, and our previously migrated data-tooltip could be stale. Migrate
    // and remove the title so the OS-level native tooltip never fires.
    const nt = el.getAttribute('title');
    if (nt) {
      el.setAttribute('data-tooltip', nt);
      if (!el.getAttribute('aria-label')) el.setAttribute('aria-label', nt);
      el.removeAttribute('title');
      return nt;
    }
    return el.getAttribute('data-tooltip') || '';
  }
  function position(host) {
    if (!tipEl) return;
    const r = host.getBoundingClientRect();
    const tipR = tipEl.getBoundingClientRect();
    const margin = 6;
    let left = r.left + (r.width / 2) - (tipR.width / 2);
    let top = r.bottom + margin;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    if (left + tipR.width > vw - 4) left = vw - tipR.width - 4;
    if (left < 4) left = 4;
    if (top + tipR.height > vh - 4) top = r.top - tipR.height - margin;
    tipEl.style.left = left + 'px';
    tipEl.style.top  = top + 'px';
  }
  function show(host) {
    const text = getText(host);
    if (!text) return;
    const el = ensureTip();
    el.textContent = text;
    el.classList.add('visible');
    position(host);
    currentHost = host;
  }
  function hide() {
    if (!tipEl) return;
    tipEl.classList.remove('visible');
    currentHost = null;
  }
  document.addEventListener('mouseover', function(e) {
    const host = e.target && e.target.closest && e.target.closest('[data-tooltip], [title]');
    if (host && host !== currentHost) show(host);
  });
  document.addEventListener('mouseout', function(e) {
    const host = e.target && e.target.closest && e.target.closest('[data-tooltip], [title]');
    if (!host) return;
    if (e.relatedTarget && host.contains(e.relatedTarget)) return;
    hide();
  });
  document.addEventListener('focusin', function(e) {
    const host = e.target && e.target.closest && e.target.closest('[data-tooltip], [title]');
    if (host) show(host);
  });
  document.addEventListener('focusout', function(e) {
    const host = e.target && e.target.closest && e.target.closest('[data-tooltip], [title]');
    if (host) hide();
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') hide();
  });
  document.addEventListener('scroll', hide, true);
  window.addEventListener('blur', hide);
})();

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

// Postgres "timestamp without time zone" columns (dms.last_message_at,
// dm_messages.message_at) serialize as ISO strings with NO offset suffix.
// JS parses those as local time, but our servers write UTC via NOW(). Append
// a Z so they parse as UTC and render correctly in the viewer's timezone.
function parseServerUtcTs(iso) {
  if (!iso) return null;
  const s = String(iso);
  const last = s.charAt(s.length - 1);
  const hasZ = last === 'Z' || last === 'z';
  const d = new Date(hasZ ? s : s + 'Z');
  return isNaN(d.getTime()) ? null : d;
}

function fmtDmTs(d) {
  if (!d) return '';
  const now = new Date();
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return time;
  const sameYear = d.getFullYear() === now.getFullYear();
  const dateOpts = sameYear
    ? { month: 'short', day: 'numeric' }
    : { month: 'short', day: 'numeric', year: 'numeric' };
  return d.toLocaleDateString([], dateOpts) + ' ' + time;
}

function formatNextRun(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startTarget = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const dayDelta = Math.round((startTarget.getTime() - startToday.getTime()) / 86400000);
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (dayDelta === 0) return 'today at ' + time;
  if (dayDelta === 1) return 'tomorrow at ' + time;
  const mon = d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return mon + ' at ' + time;
}

function nextRunTime(lastRunIso, intervalSecs) {
  if (!intervalSecs) return '--';
  if (!lastRunIso) return 'soon';
  const diffMs = new Date(lastRunIso).getTime() + intervalSecs * 1000 - Date.now();
  if (diffMs <= 0) return 'due now';
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'in <1m';
  if (mins < 60) return 'in ' + mins + 'm';
  const hrs = Math.floor(mins / 60);
  return 'in ' + hrs + 'h ' + (mins % 60) + 'm';
}

function fmtInterval(secs) {
  if (!secs) return '--';
  const found = INTERVALS.find(i => i.value === secs);
  return found ? found.label : Math.round(secs / 3600) + 'h';
}

let _initialized = false;
const PLATFORMS = ['Reddit', 'Twitter', 'LinkedIn', 'MoltBook', 'GitHub'];
const JOB_TYPES = ['Post', 'Engage', 'DM Outreach', 'DM Replies', 'Link Edit', 'Stats', 'Post Audit', 'Octolens'];

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

function phaseInterval(rowJobs) {
  const vals = rowJobs.map(j => j.interval).filter(v => typeof v === 'number' && v > 0);
  if (!vals.length) return null;
  const counts = new Map();
  for (const v of vals) counts.set(v, (counts.get(v) || 0) + 1);
  let best = null;
  for (const [v, c] of counts) {
    if (!best || c > best.c || (c === best.c && v < best.v)) best = { v, c };
  }
  return best.v;
}

function formatIntervalSecs(secs) {
  if (!Number.isFinite(secs) || secs <= 0) return secs + 's';
  if (secs % 86400 === 0) { const d = secs / 86400; return d + (d === 1 ? ' day' : ' days'); }
  if (secs % 3600 === 0)  { const h = secs / 3600;  return h + (h === 1 ? ' hour' : ' hours'); }
  if (secs % 60 === 0)    return (secs / 60) + ' min';
  return secs + 's';
}

function renderFreqCell(jobType, interval, jobs) {
  // If the plist's real interval isn't in the canonical list (e.g. 15 min /
  // 900s calendar jobs), synthesize an option so the select displays the
  // true cadence instead of silently falling back to the first option.
  const hasCanonical = INTERVALS.some(i => i.value === interval);
  const extra = (interval != null && !hasCanonical)
    ? '<option value="' + interval + '" selected>' + formatIntervalSecs(interval) + '</option>'
    : '';
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
    '<div class="cell-info" data-field="freq-lastrun">' + nextRunTime(latestRun, interval) + '</div>' +
    '<select onchange="setPhaseInterval(\\'' + jobType + '\\', this.value)">' + extra + intervalOptions + '</select>' +
  '</td>';
}

function buildMatrix(jobs) {
  const map = {};
  jobs.forEach(j => { map[j.type + ':' + j.platform] = j; });

  let html = '';
  for (const jobType of JOB_TYPES) {
    const rowJobs = jobs.filter(j => j.type === jobType);
    const interval = phaseInterval(rowJobs);

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
  const nextLine = job.nextRun ? formatNextRun(job.nextRun) : '';
  const timeValue = job.startTime
    ? String(job.startTime.hour).padStart(2, '0') + ':' + String(job.startTime.minute).padStart(2, '0')
    : '';
  const timeInput =
    '<input type="time" data-field="starttime" value="' + timeValue + '"' +
    ' title="Set daily start time (converts to calendar schedule)"' +
    ' onchange="setStartTime(\\'' + job.label + '\\', this.value)"' +
    ' style="font-size:11px;padding:1px 2px;margin-left:4px;background:transparent;color:var(--text);border:1px solid var(--border);border-radius:3px;">';
  return '<tr data-other-job="' + job.label + '">' +
    '<td style="text-align:left;padding-left:16px;">' + job.name + '</td>' +
    '<td style="color:var(--text);font-size:12px;">' +
      '<div style="display:flex;align-items:center;justify-content:center;gap:8px;">' +
        renderToggle(job.label, job.loaded) +
        '<div style="display:flex;flex-direction:column;line-height:1.3;">' +
          '<span>' + (job.schedule || '--') + timeInput + '</span>' +
          '<span data-field="nextrun" style="color:var(--muted);font-size:11px;">' + nextLine + '</span>' +
        '</div>' +
      '</div>' +
    '</td>' +
    '<td style="color:var(--text);font-size:12px;" data-field="lastrun">' + relTime(job.lastRun) + '</td>' +
    '<td><span class="badge ' + job.status + '" data-field="status">' + statusLabel + '</span></td>' +
    '<td><div class="cell-actions" style="justify-content:center;">' + runStopBtn + '</div></td>' +
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
    const nextrun = tr.querySelector('[data-field="nextrun"]');
    if (nextrun) nextrun.textContent = job.nextRun ? formatNextRun(job.nextRun) : '';
    const toggleInput = tr.querySelector('[data-field="toggle"] input');
    if (toggleInput && toggleInput.checked !== !!job.loaded) toggleInput.checked = !!job.loaded;
    const timeInput = tr.querySelector('[data-field="starttime"]');
    if (timeInput && document.activeElement !== timeInput) {
      const nextValue = job.startTime
        ? String(job.startTime.hour).padStart(2, '0') + ':' + String(job.startTime.minute).padStart(2, '0')
        : '';
      if (timeInput.value !== nextValue) timeInput.value = nextValue;
    }
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
    const interval = phaseInterval(rowJobs);
    const el = td.querySelector('[data-field="freq-lastrun"]');
    if (el) el.textContent = nextRunTime(latestRun, interval);
    const sel = td.querySelector('select');
    if (sel && interval != null) {
      // If the real interval isn't in the canonical INTERVALS list, make sure
      // the select has a matching option so sel.value assignment sticks.
      const hasOpt = Array.from(sel.options).some(o => Number(o.value) === interval);
      if (!hasOpt) {
        const opt = document.createElement('option');
        opt.value = String(interval);
        opt.textContent = formatIntervalSecs(interval);
        sel.insertBefore(opt, sel.firstChild);
      }
      if (String(sel.value) !== String(interval)) sel.value = String(interval);
    }
  }
}

// Job history state ---------------------------------------------------------
let _jobHistoryRuns = [];
let _jobHistoryPlatformFilter = 'all';
let _jobHistoryTypeFilter = 'all';

function fmtRelTime(iso) {
  if (!iso) return '—';
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const s = Math.floor(ms / 1000);
  if (s < 60) return s + 's ago';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 48) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

function fmtLocalTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return '—';
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const da = String(d.getDate()).padStart(2, '0');
  return mo + '-' + da + ' ' + hh + ':' + mm;
}

function fmtElapsed(s) {
  if (!Number.isFinite(s) || s <= 0) return '—';
  if (s < 60) return s + 's';
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return m + 'm' + (rs ? ' ' + rs + 's' : '');
  const h = Math.floor(m / 60);
  return h + 'h ' + (m % 60) + 'm';
}

function renderResult(run) {
  const r = run.result || {};
  const pill = (label, n, color) =>
    '<span style="display:inline-block;margin-right:10px;font-size:12px;color:var(--muted);">' +
    label + ' <span style="color:' + color + ';font-weight:600;">' + n + '</span></span>';
  if (r.type === 'link-edit') {
    return (
      pill('touched', r.total, 'var(--text)') +
      pill('success', r.success, '#22c55e') +
      pill('skipped', r.skipped, '#eab308')
    );
  }
  if (r.type === 'check-replies') {
    const found = r.found || 0;
    const pending = r.pending_now || 0;
    if (!found && !pending) {
      return '<span style="color:var(--muted);font-size:12px;">no new replies</span>';
    }
    return (
      pill('found', found, found > 0 ? '#22c55e' : 'var(--text)') +
      (pending ? pill('queue', pending, 'var(--muted)') : '')
    );
  }
  if (r.type === 'engage') {
    // Per-run DB-derived counts (see enrichEngageRuns in server.js).
    // Empty-queue runs show "queue empty" so the operator can tell a
    // successful no-op apart from a broken run.
    const processed = r.processed || 0;
    const replied = r.replied || 0;
    const skipped = r.skipped || 0;
    const errored = r.errored || 0;
    const pending = r.pending_now || 0;
    const cost = r.cost_usd || 0;
    if (!processed && !pending) {
      return '<span style="color:var(--muted);font-size:12px;">queue empty</span>';
    }
    if (!processed) {
      return pill('queue', pending, 'var(--text)') +
        '<span style="color:var(--muted);font-size:12px;">nothing processed</span>';
    }
    return (
      pill('replied', replied, '#22c55e') +
      (skipped ? pill('skipped', skipped, '#eab308') : '') +
      (errored ? pill('errored', errored, '#ef4444') : '') +
      (pending ? pill('queue', pending, 'var(--muted)') : '') +
      (cost ? '<span style="font-size:12px;color:var(--muted);">$' + cost.toFixed(2) + '</span>' : '')
    );
  }
  // Generic fallback: posted/skipped/failed from run_monitor.log
  const posted = r.posted || 0, skipped = r.skipped || 0, failed = r.failed || 0;
  if (!posted && !skipped && !failed) return '<span style="color:var(--muted);font-size:12px;">—</span>';
  return (
    pill('posted', posted, '#22c55e') +
    pill('skipped', skipped, '#eab308') +
    (failed ? pill('failed', failed, '#ef4444') : '')
  );
}

function buildJobsHistoryTable(runs) {
  if (!runs || !runs.length) {
    return '<div class="style-stats-empty" style="padding:16px;">No runs match the current filters.</div>';
  }
  const rows = runs.slice(0, 300).map(r => {
    const cost = r.result && r.result.cost_usd;
    const costCell = cost ? fmtCost(cost) : '<span style="color:var(--muted);">—</span>';
    return (
      '<tr>' +
        '<td style="text-align:left;padding-left:16px;">' + (r.job_label || r.script) + '</td>' +
        '<td>' + (r.platform || '<span style="color:var(--muted);">—</span>') + '</td>' +
        '<td>' + fmtLocalTime(r.started_at) + ' <span style="color:var(--muted);font-size:11px;">(' + fmtRelTime(r.started_at) + ')</span></td>' +
        '<td>' + fmtLocalTime(r.finished_at) + ' <span style="color:var(--muted);font-size:11px;">(' + fmtElapsed(r.elapsed_s) + ')</span></td>' +
        '<td style="text-align:left;">' + renderResult(r) + '</td>' +
        '<td style="color:var(--muted);font-size:12px;">' + costCell + '</td>' +
      '</tr>'
    );
  }).join('');
  return (
    '<table class="matrix-table" style="margin-top:0;">' +
      '<thead><tr>' +
        '<th style="text-align:left;padding-left:16px;">Job</th>' +
        '<th>Platform</th>' +
        '<th>Kicked off</th>' +
        '<th>Finished</th>' +
        '<th style="text-align:left;">Result</th>' +
        '<th>Cost</th>' +
      '</tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>'
  );
}

function applyJobsHistoryFilter() {
  const body = document.getElementById('jobs-history-body');
  if (!body) return;
  const pf = _jobHistoryPlatformFilter, tf = _jobHistoryTypeFilter;
  // Window is applied server-side (see loadJobsHistory), so this filter only
  // narrows on platform/type.
  const filtered = _jobHistoryRuns.filter(r =>
    (pf === 'all' || r.platform_key === pf) &&
    (tf === 'all' || r.job_type === tf)
  );
  body.innerHTML = buildJobsHistoryTable(filtered);
}

function wirePillRow(rowId, onSelect) {
  const pillRow = document.getElementById(rowId);
  if (!pillRow || pillRow._wired) return;
  pillRow._wired = true;
  pillRow.addEventListener('click', (e) => {
    const btn = e.target.closest('.style-stats-pill');
    if (!btn) return;
    pillRow.querySelectorAll('.style-stats-pill').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const v = btn.getAttribute('data-value');
    pillRow.setAttribute('data-selected', v);
    onSelect(v);
  });
}

function initJobsHistoryPills() {
  wirePillRow('jobs-history-platform-pills', (v) => {
    _jobHistoryPlatformFilter = v;
    applyJobsHistoryFilter();
  });
  wirePillRow('jobs-history-type-pills', (v) => {
    _jobHistoryTypeFilter = v;
    applyJobsHistoryFilter();
  });
}

let _jobHistoryLoadedAt = 0;
let _jobHistoryLoadedForHours = null;
async function loadJobsHistory(force) {
  // Throttle to once per 20s. Job history changes on the order of minutes,
  // but loadStatus() polls every 5s. Bypass the throttle when the Status
  // tab window changes so the operator sees the new range immediately.
  const hours = currentStatusWindow().hours;
  const sameHours = _jobHistoryLoadedForHours === hours;
  if (!force && sameHours && Date.now() - _jobHistoryLoadedAt < 20000) return;
  _jobHistoryLoadedAt = Date.now();
  _jobHistoryLoadedForHours = hours;
  try {
    const qs = hours ? ('?hours=' + hours) : '?limit=500';
    const res = await fetch('/api/job-runs' + qs);
    const data = await res.json();
    _jobHistoryRuns = Array.isArray(data.runs) ? data.runs : [];
    initJobsHistoryPills();
    applyJobsHistoryFilter();
  } catch (e) {
    const body = document.getElementById('jobs-history-body');
    if (body) body.innerHTML = '<div class="style-stats-empty" style="padding:16px;color:#ef4444;">Failed: ' + e.message + '</div>';
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
    loadJobsHistory();

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
          const colors = { pending: '#eab308', replied: '#22c55e', skipped: 'var(--text)', error: '#ef4444' };
          return '<span style="margin-right:16px;font-size:13px;"><span style="color:' + (colors[s.status] || 'var(--text)') + ';">' + s.status + '</span> ' + s.count + '</span>';
        }).join('');

      pendingSection.innerHTML = '<div class="card pending-card">' +
        '<div class="card-header"><span class="card-title">Pending Replies</span><span class="badge" style="background:#4c1d95;color:#c4b5fd;">' + pending.count + '</span></div>' +
        '<div class="card-row" style="justify-content:flex-start;padding:8px 16px;border-bottom:1px solid var(--accent-panel-border);">' + statusBreakdown + '</div>' +
        platformBreakdown +
        (recentReplies ? '<div style="margin-top:12px;border-top:1px solid var(--accent-panel-border);padding-top:12px;">' + recentReplies + '</div>' : '') +
      '</div>';
    }
  } catch(e) { toast('Failed to load status: ' + e.message, true); }
}

let _paused = false;

function updatePauseBtn() {
  const btn = document.getElementById('pause-btn');
  if (_paused) {
    btn.textContent = '\\u25B6 Resume All';
    btn.className = 'btn primary sa-local-only';
  } else {
    btn.textContent = '\\u23F8 Pause All';
    btn.className = 'btn danger sa-local-only';
  }
}

function toggleTheme() {
  try {
    const cur = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('sa_theme', next);
  } catch (e) { /* ignore */ }
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

async function setStartTime(label, value) {
  if (!value) return;
  const parts = value.split(':');
  const hour = parseInt(parts[0], 10);
  const minute = parseInt(parts[1], 10);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) {
    toast('Invalid time', true);
    return;
  }
  try {
    const res = await fetch('/api/jobs/' + encodeURIComponent(label) + '/start-time', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hour, minute }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'request failed');
    toast('Start time set to ' + value);
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
const EVENT_TYPES = ['posted', 'replied', 'skipped', 'mention', 'dm_sent', 'dm_reply_sent', 'page_published_serp', 'page_published_gsc', 'page_published_reddit', 'page_published_top', 'page_published_roundup', 'page_improved', 'resurrected'];
const EVENT_LABELS = { posted: 'posted', replied: 'replied', skipped: 'skipped', mention: 'mention', dm_sent: 'dm sent', dm_reply_sent: 'dm reply', page_published_serp: 'page (serp)', page_published_gsc: 'page (gsc)', page_published_reddit: 'page (reddit)', page_published_top: 'page (top)', page_published_roundup: 'page (roundup)', page_improved: 'page (improved)', resurrected: 'resurrected' };
const EVENT_DESCRIPTIONS = {
  posted: 'Original post the bot created on a platform (new thread, tweet, LinkedIn post, Reddit submission, etc.).',
  replied: 'Comment or reply the bot left on someone else’s thread, tweet, or post.',
  skipped: 'Candidate thread the bot reviewed but chose not to engage with (off-topic, already answered, low value, or filtered out by rules).',
  mention: 'Someone mentioned one of our products on a tracked platform. Detection only, no engagement action.',
  dm_sent: 'New direct-message conversation the bot started with a prospect.',
  dm_reply_sent: 'Follow-up message sent inside an existing DM conversation.',
  page_published_serp: 'SEO landing page generated from the SERP pipeline (based on ranked search results for target keywords).',
  page_published_gsc: 'SEO page generated from a Google Search Console query the site already gets impressions for.',
  page_published_reddit: 'SEO page generated from a high-intent Reddit thread.',
  page_published_top: 'SEO page generated for a top-of-funnel ranking opportunity.',
  page_published_roundup: 'Roundup or list-style SEO page (comparisons, best-of, alternatives).',
  page_improved: 'Existing SEO page that was updated or rewritten to improve rankings.',
  resurrected: 'Previously archived or unavailable item brought back into rotation (e.g., a removed post restored after reappearing).',
};
const ACTIVITY_PLATFORMS = ['reddit', 'twitter', 'linkedin', 'moltbook', 'github', 'seo'];
const PROJECT_LABELS = { tenxats: '10xats' };
const ACTIVITY_PROJECT_NONE = '(none)';
let _activitySeen = new Set();
let _activityFirstLoad = true;
let _activityTypeFilter = new Set(EVENT_TYPES);
let _activityPlatformFilter = new Set(ACTIVITY_PLATFORMS);
let _activityProjectFilter = new Set();
let _activityKnownProjects = [];
let _activitySearch = '';
let _activitySortField = 'occurred_at';
let _activitySortDir = 'desc';
let _activityPage = 0;
let _activityPageSize = (() => {
  try {
    const v = parseInt(localStorage.getItem('activityPageSize'), 10);
    return [10, 25, 50, 100].includes(v) ? v : 100;
  } catch { return 100; }
})();
let _activityTimer = null;
let _activityControlsWired = false;

function activityProjectKey(e) {
  const p = String((e && e.project) || '').trim();
  return p || ACTIVITY_PROJECT_NONE;
}

function refreshActivityProjectPills(events) {
  const projEl = document.getElementById('activity-project-filters');
  if (!projEl) return;
  const seen = new Set(_activityKnownProjects);
  let added = false;
  for (const e of events || []) {
    const p = activityProjectKey(e);
    if (!seen.has(p)) { seen.add(p); _activityKnownProjects.push(p); _activityProjectFilter.add(p); added = true; }
  }
  if (!added && projEl.children.length) {
    projEl.querySelectorAll('[data-project]').forEach(el => {
      el.classList.toggle('active', _activityProjectFilter.has(el.dataset.project));
    });
    return;
  }
  _activityKnownProjects.sort((a, b) => a.localeCompare(b));
  projEl.innerHTML = _activityKnownProjects.map(p =>
    '<span class="activity-chip' + (_activityProjectFilter.has(p) ? ' active' : '') + '" data-project="' + escapeHtml(p) + '" title="' + escapeHtml(p) + '">' + escapeHtml(PROJECT_LABELS[p] || p) + '</span>'
  ).join('');
}

function buildActivityFilters() {
  const tEl = document.getElementById('activity-type-filters');
  const pEl = document.getElementById('activity-platform-filters');
  const projEl = document.getElementById('activity-project-filters');
  if (!tEl || tEl.children.length) return;
  tEl.innerHTML = EVENT_TYPES.map(t =>
    '<span class="activity-chip ev-' + t + ' active" data-type="' + t + '">' + EVENT_LABELS[t] + '</span>'
  ).join('');
  pEl.innerHTML = ACTIVITY_PLATFORMS.map(p =>
    '<span class="activity-chip active" data-platform="' + p + '" title="' + p + '">' + (PLATFORM_ICONS[p] || p) + '</span>'
  ).join('');
  tEl.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-type]');
    if (!el || !tEl.contains(el)) return;
    const t = el.dataset.type;
    if (_activityTypeFilter.has(t)) { _activityTypeFilter.delete(t); el.classList.remove('active'); }
    else { _activityTypeFilter.add(t); el.classList.add('active'); }
    _activityPage = 0;
    renderActivity(_lastActivityEvents || []);
  });
  pEl.addEventListener('click', (ev) => {
    const el = ev.target.closest('[data-platform]');
    if (!el || !pEl.contains(el)) return;
    const p = el.dataset.platform;
    if (_activityPlatformFilter.has(p)) { _activityPlatformFilter.delete(p); el.classList.remove('active'); }
    else { _activityPlatformFilter.add(p); el.classList.add('active'); }
    _activityPage = 0;
    renderActivity(_lastActivityEvents || []);
  });
  if (projEl) {
    projEl.addEventListener('click', (ev) => {
      const el = ev.target.closest('[data-project]');
      if (!el || !projEl.contains(el)) return;
      const p = el.dataset.project;
      if (_activityProjectFilter.has(p)) { _activityProjectFilter.delete(p); el.classList.remove('active'); }
      else { _activityProjectFilter.add(p); el.classList.add('active'); }
      _activityPage = 0;
      renderActivity(_lastActivityEvents || []);
    });
  }
  document.querySelectorAll('#tab-activity [data-filter-action]').forEach(btn => {
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      const a = btn.dataset.filterAction;
      if (a === 'type-all') {
        _activityTypeFilter = new Set(EVENT_TYPES);
        tEl.querySelectorAll('[data-type]').forEach(c => c.classList.add('active'));
      } else if (a === 'type-none') {
        _activityTypeFilter = new Set();
        tEl.querySelectorAll('[data-type]').forEach(c => c.classList.remove('active'));
      } else if (a === 'platform-all') {
        _activityPlatformFilter = new Set(ACTIVITY_PLATFORMS);
        pEl.querySelectorAll('[data-platform]').forEach(c => c.classList.add('active'));
      } else if (a === 'platform-none') {
        _activityPlatformFilter = new Set();
        pEl.querySelectorAll('[data-platform]').forEach(c => c.classList.remove('active'));
      } else if (a === 'project-all') {
        _activityProjectFilter = new Set(_activityKnownProjects);
        if (projEl) projEl.querySelectorAll('[data-project]').forEach(c => c.classList.add('active'));
      } else if (a === 'project-none') {
        _activityProjectFilter = new Set();
        if (projEl) projEl.querySelectorAll('[data-project]').forEach(c => c.classList.remove('active'));
      }
      _activityPage = 0;
      renderActivity(_lastActivityEvents || []);
    });
  });
  if (!_activityControlsWired) {
    _activityControlsWired = true;
    const search = document.getElementById('activity-search');
    if (search) search.addEventListener('input', () => { _activitySearch = search.value.trim().toLowerCase(); _activityPage = 0; renderActivity(_lastActivityEvents || []); });
    document.querySelectorAll('.activity-sortable').forEach(el => {
      el.addEventListener('click', () => {
        const field = el.dataset.sort;
        if (_activitySortField === field) _activitySortDir = _activitySortDir === 'asc' ? 'desc' : 'asc';
        else { _activitySortField = field; _activitySortDir = field === 'occurred_at' ? 'desc' : 'asc'; }
        _activityPage = 0;
        renderActivity(_lastActivityEvents || []);
      });
    });
  }
}

function activityMatchesSearch(e, q) {
  if (!q) return true;
  const hay = [e.type, e.platform, e.project, e.detail, e.summary, e.link, e.actor, e.occurred_at]
    .map(v => String(v || '').toLowerCase()).join(' | ');
  return hay.indexOf(q) !== -1;
}

function sortActivity(events, field, dir) {
  const mult = dir === 'asc' ? 1 : -1;
  return events.slice().sort((a, b) => {
    let av = a[field], bv = b[field];
    if (field === 'occurred_at') { av = av ? new Date(av).getTime() : 0; bv = bv ? new Date(bv).getTime() : 0; }
    else if (field === 'cost_usd') { av = av == null ? -1 : Number(av); bv = bv == null ? -1 : Number(bv); }
    else { av = String(av == null ? '' : av).toLowerCase(); bv = String(bv == null ? '' : bv).toLowerCase(); }
    if (av < bv) return -1 * mult;
    if (av > bv) return 1 * mult;
    return 0;
  });
}

function fmtCost(c) {
  if (c == null) return '';
  const n = Number(c);
  if (!isFinite(n)) return '';
  if (n === 0) return '$0';
  if (n < 0.01) return '$' + n.toFixed(4);
  return '$' + n.toFixed(2);
}

function renderSortArrows() {
  document.querySelectorAll('.activity-sort-arrow').forEach(el => {
    const field = el.dataset.sortArrow;
    if (field === _activitySortField) {
      el.textContent = _activitySortDir === 'asc' ? '▲' : '▼';
      el.classList.add('active');
    } else {
      el.textContent = '↕';
      el.classList.remove('active');
    }
  });
}

function renderPagination(totalFiltered) {
  const el = document.getElementById('activity-pagination');
  if (!el) return;
  const totalPages = Math.max(1, Math.ceil(totalFiltered / _activityPageSize));
  if (_activityPage >= totalPages) _activityPage = totalPages - 1;
  if (_activityPage < 0) _activityPage = 0;
  const from = totalFiltered === 0 ? 0 : _activityPage * _activityPageSize + 1;
  const to = Math.min(totalFiltered, (_activityPage + 1) * _activityPageSize);
  el.innerHTML =
    '<span>Rows per page:</span>' +
    '<select id="activity-page-size">' +
      [10, 25, 50, 100].map(n => '<option value="' + n + '"' + (n === _activityPageSize ? ' selected' : '') + '>' + n + '</option>').join('') +
    '</select>' +
    '<span>' + from + '-' + to + ' of ' + totalFiltered + '</span>' +
    '<button class="pager-btn" id="activity-prev"' + (_activityPage <= 0 ? ' disabled' : '') + '>Prev</button>' +
    '<button class="pager-btn" id="activity-next"' + (_activityPage >= totalPages - 1 ? ' disabled' : '') + '>Next</button>';
  const ps = document.getElementById('activity-page-size');
  if (ps) ps.addEventListener('change', () => {
    _activityPageSize = parseInt(ps.value, 10) || 100;
    _activityPage = 0;
    try { localStorage.setItem('activityPageSize', String(_activityPageSize)); } catch {}
    renderActivity(_lastActivityEvents || []);
  });
  const prev = document.getElementById('activity-prev');
  if (prev) prev.addEventListener('click', () => { _activityPage -= 1; renderActivity(_lastActivityEvents || []); });
  const next = document.getElementById('activity-next');
  if (next) next.addEventListener('click', () => { _activityPage += 1; renderActivity(_lastActivityEvents || []); });
}

function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}

const PLATFORM_ICONS = {
  reddit:   '<svg viewBox="0 0 24 24" aria-label="reddit"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm6.436 13.158c.023.16.034.323.034.49 0 2.498-2.908 4.522-6.494 4.522-3.587 0-6.494-2.024-6.494-4.523 0-.167.011-.33.033-.489a1.44 1.44 0 01-.822-1.297 1.444 1.444 0 012.448-1.036 7.967 7.967 0 014.337-1.374l.82-3.865a.277.277 0 01.328-.215l2.69.57a1.004 1.004 0 011.813.068 1.005 1.005 0 01-1.813.875l-2.406-.51-.736 3.47a7.98 7.98 0 014.298 1.379 1.44 1.44 0 011.996.432c.35.56.2 1.29-.332 1.652-.02.013-.04.025-.06.037zM9.17 13.14a1.02 1.02 0 100-2.041 1.02 1.02 0 000 2.041zm6.69-1.02a1.02 1.02 0 10-2.04 0 1.02 1.02 0 002.04 0zm-1.01 3.32a.33.33 0 00-.467 0c-.56.56-1.63.605-1.944.605s-1.384-.046-1.944-.606a.33.33 0 00-.467.467c.887.887 2.587.957 2.411.957.176 0 1.524-.07 2.411-.957a.33.33 0 000-.466z"/></svg>',
  twitter:  '<svg viewBox="0 0 24 24" aria-label="twitter"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>',
  linkedin: '<svg viewBox="0 0 24 24" aria-label="linkedin"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.063 2.063 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z"/></svg>',
  github:   '<svg viewBox="0 0 24 24" aria-label="github"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>',
  moltbook: '<span class="plat-mono" aria-label="moltbook">M</span>',
  seo:      '<svg viewBox="0 0 24 24" aria-label="seo"><path d="M12 2a10 10 0 100 20 10 10 0 000-20zm0 2c1.657 0 3 3.582 3 8s-1.343 8-3 8-3-3.582-3-8 1.343-8 3-8zm-6.708 5h13.416a7.99 7.99 0 010 6H5.292a7.99 7.99 0 010-6z"/></svg>',
};
function platformIconHtml(name) {
  const key = String(name || '').toLowerCase();
  const icon = PLATFORM_ICONS[key] || '<span class="plat-mono" aria-label="' + key + '">' + (key[0] || '?').toUpperCase() + '</span>';
  return '<span class="activity-platform" title="' + key + '">' + icon + '</span>';
}

// Top-of-Stats-tab window selector. Controls all three sections
// (activity counts, style stats, project funnel). Mapping matches the
// windows precompute_dashboard_stats.py generates snapshots for.
const STATS_WINDOWS = {
  '24h': { hours: 24,  days: 1,  labelLong: 'last 24 hours', labelShort: '24h' },
  '7d':  { hours: 168, days: 7,  labelLong: 'last 7 days',   labelShort: '7d'  },
  '14d': { hours: 336, days: 14, labelLong: 'last 14 days',  labelShort: '14d' },
  '30d': { hours: 720, days: 30, labelLong: 'last 30 days',  labelShort: '30d' },
};
// Persist the user's window selection so picking 7d on Stats also applies to
// Status and Top on next visit (and survives reloads). Default is 7d.
const DASHBOARD_WINDOW_KEY = 'sa_dashboard_window';
const TOP_WINDOW_VALUES = new Set(['24h', '7d', '14d', '30d', '90d', 'all']);
function loadSavedDashboardWindow() {
  try {
    const v = localStorage.getItem(DASHBOARD_WINDOW_KEY);
    if (v) return v;
  } catch (e) {}
  return '7d';
}
function saveDashboardWindow(v) {
  try { localStorage.setItem(DASHBOARD_WINDOW_KEY, v || '7d'); } catch (e) {}
}
function coerceStatsWindow(v) { return STATS_WINDOWS[v] ? v : '7d'; }
function coerceTopWindow(v) { return TOP_WINDOW_VALUES.has(v) ? v : '7d'; }
let _statsWindow = coerceStatsWindow(loadSavedDashboardWindow());
function currentStatsWindow() {
  return STATS_WINDOWS[_statsWindow] || STATS_WINDOWS['7d'];
}
// Status-tab has its own window selector, independent of Stats-tab. Drives
// Cost per Activity, Project Status, and Job History filtering.
let _statusWindow = coerceStatsWindow(loadSavedDashboardWindow());
function currentStatusWindow() {
  return STATS_WINDOWS[_statusWindow] || STATS_WINDOWS['7d'];
}
// Top-of-Stats-tab platform and project selection. Same contract as the window
// filter: a change re-fetches every section on the page so the whole tab
// reflects the chosen scope.
function currentStatsPlatform() {
  const row = document.getElementById('style-stats-platform-pills');
  return (row && row.dataset.selected) || 'all';
}
function currentStatsProject() {
  const row = document.getElementById('style-stats-project-pills');
  return (row && row.dataset.selected) || 'all';
}
function reloadStatsTabSections() {
  loadActivityStats();
  loadStyleStats();
  // daily-metrics chart is intentionally NOT reloaded on window/platform/
  // project changes — it's fixed to a 30-day rolling window, independent
  // of the filter bar.
  const funnelEl = document.getElementById('funnel-stats');
  if (funnelEl && funnelEl.open) {
    if (_lastFunnelPayload) renderFunnelStats(_lastFunnelPayload);
    else loadFunnelStats(true);
  }
  const dmEl = document.getElementById('dm-stats');
  if (dmEl && dmEl.open) loadDmStats(true);
}
function syncStatsHeadings() {
  const win = currentStatsWindow();
  const titleCased = win.labelLong.charAt(0).toUpperCase() + win.labelLong.slice(1);
  const top = document.getElementById('stats-title');
  if (top) top.textContent = titleCased;
  const style = document.getElementById('style-stats-heading');
  if (style) style.textContent = 'Posts by Engagement Style (' + win.labelShort + ')';
  const funnel = document.getElementById('funnel-stats-heading');
  if (funnel) funnel.textContent = 'Project Funnel Stats (' + win.labelLong + ')';
  const dm = document.getElementById('dm-stats-heading');
  if (dm) dm.textContent = 'DM Funnel Stats (' + win.labelLong + ')';
}
function syncStatusHeadings() {
  const win = currentStatusWindow();
  const cost = document.getElementById('cost-stats-heading');
  if (cost) cost.textContent = 'Cost per Activity (' + win.labelLong + ')';
  const proj = document.getElementById('project-status-heading');
  if (proj) proj.textContent = 'Project Status (last ' + win.hours + 'h)';
}

function renderActivityStats(payload) {
  const grid = document.getElementById('stats-grid');
  const totalEl = document.getElementById('stats-total');
  if (!grid) return;
  const rows = (payload && payload.rows) || [];
  const hours = (payload && payload.windowHours) || 24;
  const byType = {};
  EVENT_TYPES.forEach(t => { byType[t] = { total: 0, platforms: {} }; });
  let grandTotal = 0;
  rows.forEach(r => {
    const t = r.type;
    const pKey = String(r.platform || '').toLowerCase() || 'unknown';
    const n = Number(r.count) || 0;
    if (!byType[t]) byType[t] = { total: 0, platforms: {} };
    byType[t].total += n;
    byType[t].platforms[pKey] = (byType[t].platforms[pKey] || 0) + n;
    grandTotal += n;
  });
  if (totalEl) totalEl.textContent = grandTotal + ' events in ' + currentStatsWindow().labelLong;
  grid.innerHTML = EVENT_TYPES.map(t => {
    const bucket = byType[t];
    const total = bucket.total;
    const plats = Object.keys(bucket.platforms).sort((a, b) => bucket.platforms[b] - bucket.platforms[a]);
    const platHtml = plats.length
      ? plats.map(p => {
          const icon = PLATFORM_ICONS[p] || '<span class="plat-mono">' + escapeHtml((p[0] || '?').toUpperCase()) + '</span>';
          return '<span class="stat-plat" title="' + escapeHtml(p) + '">' + icon + '<span class="stat-plat-count">' + bucket.platforms[p] + '</span></span>';
        }).join('')
      : '<span style="color:var(--text-very-faint);">\u2014</span>';
    const desc = EVENT_DESCRIPTIONS[t] || '';
    const infoIcon = desc
      ? '<span class="stat-card-info" title="' + escapeHtml(desc) + '" aria-label="' + escapeHtml(desc) + '">i</span>'
      : '';
    return '<div class="stat-card ev-' + escapeHtml(t) + (total === 0 ? ' zero' : '') + '">' +
      '<div class="stat-card-head">' +
        '<span class="stat-card-label">' + escapeHtml(EVENT_LABELS[t] || t) + infoIcon + '</span>' +
        '<span class="stat-card-count">' + total + '</span>' +
      '</div>' +
      '<div class="stat-card-breakdown">' + platHtml + '</div>' +
    '</div>';
  }).join('');
}

async function loadActivityStats() {
  try {
    const hours = currentStatsWindow().hours;
    const plat = currentStatsPlatform();
    const proj = currentStatsProject();
    const params = ['hours=' + hours];
    if (plat && plat !== 'all') params.push('platform=' + encodeURIComponent(plat));
    if (proj && proj !== 'all') params.push('project='  + encodeURIComponent(proj));
    const res = await fetch('/api/activity/stats?' + params.join('&'));
    const data = await res.json();
    renderActivityStats(data);
  } catch {}
}

// Combined daily-metrics line chart. Fetches 4 endpoints (2 post-series
// endpoints, bookings, and a batched funnel PostHog endpoint covering 5
// metrics) and renders one SVG with a toggleable colored line per metric.
// The chart is fixed to a 30-day window and ignores the stats tab's
// top window/platform/project filters by design.
//
// Three post-derived metrics (views, upvotes, comments) exclude each
// post's first-ever snapshot so day 1 never attributes lifetime counts
// to a capture day; expect those lines to sit at 0 until at least two
// consecutive days of snapshots have accumulated per post.
const DAILY_METRICS = [
  { id: 'views',           label: 'Views',             color: '#6366f1', endpoint: '/api/views/per-day',    valueKey: 'views_gained' },
  { id: 'upvotes',         label: 'Upvotes',           color: '#f97316', endpoint: '/api/upvotes/per-day',  valueKey: 'upvotes_gained' },
  { id: 'comments',        label: 'Comments',          color: '#14b8a6', endpoint: '/api/comments/per-day', valueKey: 'comments_gained' },
  { id: 'bookings',        label: 'Bookings',          color: '#ef4444', endpoint: '/api/bookings/per-day', valueKey: 'bookings_gained' },
  { id: 'pageviews',       label: 'Pageviews',         color: '#8b5cf6', funnel: true, valueKey: 'pageviews' },
  { id: 'email_signups',   label: 'Email Signups',     color: '#10b981', funnel: true, valueKey: 'email_signups' },
  { id: 'schedule_clicks', label: 'Schedule Clicks',   color: '#f59e0b', funnel: true, valueKey: 'schedule_clicks' },
  { id: 'get_started',     label: 'Get Started',       color: '#06b6d4', funnel: true, valueKey: 'get_started_clicks' },
  { id: 'cross_product',   label: 'Cross Product',     color: '#ec4899', funnel: true, valueKey: 'cross_product_clicks' },
];
const DAILY_METRICS_DAYS = 30;

// series: { [metricId]: { [dayISO]: number } }. Rebuilt by loadDailyMetrics,
// read by renderDailyMetrics. Persisted selection lives in localStorage.
let _dailyMetricsSeries = null;
let _dailyMetricsDays = [];
let _dailyMetricsActive = null;

function _loadDailyMetricsActive() {
  if (_dailyMetricsActive) return _dailyMetricsActive;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('dailyMetricsActive') || 'null'); } catch {}
  const set = new Set(Array.isArray(saved) ? saved : DAILY_METRICS.map(m => m.id));
  _dailyMetricsActive = set;
  return set;
}
function _saveDailyMetricsActive() {
  try { localStorage.setItem('dailyMetricsActive', JSON.stringify(Array.from(_dailyMetricsActive))); } catch {}
}

function _fmtShort(n) {
  if (n == null) return '—';
  n = Number(n);
  if (!isFinite(n)) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(n >= 10_000 ? 0 : 1) + 'K';
  return String(n);
}
function _fmtDay(dayIso) {
  const d = new Date(dayIso + 'T00:00:00');
  return (d.getMonth() + 1) + '/' + d.getDate();
}
function _niceMax(v) {
  // Round up to a "nice" axis cap (1/2/5 * 10^k) so Y-axis ticks line up on round numbers.
  if (!v || v <= 0) return 1;
  const exp = Math.floor(Math.log10(v));
  const base = Math.pow(10, exp);
  const m = v / base;
  let nice;
  if (m <= 1)       nice = 1;
  else if (m <= 2)  nice = 2;
  else if (m <= 5)  nice = 5;
  else              nice = 10;
  return nice * base;
}

function renderDailyMetrics() {
  const legendEl = document.getElementById('daily-metrics-legend');
  const chartEl = document.getElementById('daily-metrics-chart');
  const statusEl = document.getElementById('daily-metrics-status');
  if (!legendEl || !chartEl) return;
  if (!_dailyMetricsSeries) {
    chartEl.innerHTML = '<div class="views-chart-empty">Loading…</div>';
    return;
  }
  const active = _loadDailyMetricsActive();
  const days = _dailyMetricsDays;
  // Legend pills: always render all metrics; off ones get .off.
  const totals = {};
  DAILY_METRICS.forEach(m => {
    const byDay = _dailyMetricsSeries[m.id] || {};
    totals[m.id] = days.reduce((acc, d) => acc + (Number(byDay[d]) || 0), 0);
  });
  legendEl.innerHTML = DAILY_METRICS.map(m => {
    const off = !active.has(m.id);
    return '<button type="button" class="daily-metrics-legend-pill' + (off ? ' off' : '') +
      '" data-metric="' + escapeHtml(m.id) + '" aria-pressed="' + (off ? 'false' : 'true') + '">' +
      '<span class="swatch" style="background:' + m.color + ';"></span>' +
      '<span class="label">' + escapeHtml(m.label) + '</span>' +
      '<span class="count">' + _fmtShort(totals[m.id]) + '</span>' +
      '</button>';
  }).join('');
  legendEl.querySelectorAll('.daily-metrics-legend-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.metric;
      if (active.has(id)) active.delete(id); else active.add(id);
      _saveDailyMetricsActive();
      renderDailyMetrics();
    });
  });

  // Chart. Each metric gets its own independent Y-axis — every line is
  // normalized to its own 30-day peak so all nine shapes are comparable
  // regardless of raw magnitude (pageviews at 2K vs bookings at 5 both
  // use the full canvas height). No numeric Y-axis is rendered; peak
  // values live in the legend + tooltip instead.
  const visibleMetrics = DAILY_METRICS.filter(m => active.has(m.id));
  if (!visibleMetrics.length) {
    chartEl.innerHTML = '<div class="views-chart-empty">Select at least one metric above to render the chart.</div>';
    if (statusEl) statusEl.textContent = '0 of ' + DAILY_METRICS.length + ' series';
    return;
  }
  // Per-series peak over the 30-day window; used for independent scaling.
  const seriesPeak = {};
  DAILY_METRICS.forEach(m => {
    const byDay = _dailyMetricsSeries[m.id] || {};
    let p = 0;
    days.forEach(d => { p = Math.max(p, Number(byDay[d]) || 0); });
    seriesPeak[m.id] = p;
  });
  const width = 960;
  const height = 260;
  const padL = 12, padR = 12, padT = 12, padB = 24;
  const plotW = width - padL - padR;
  const plotH = height - padT - padB;
  const xStep = days.length > 1 ? plotW / (days.length - 1) : 0;
  const xOf = i => padL + xStep * i;
  // A baseline at the bottom + three subtle unlabeled gridlines for visual
  // grounding. No numeric labels because the scale differs per line.
  const yGrid = [0, 0.25, 0.5, 0.75, 1].map(t => {
    const y = padT + plotH - t * plotH;
    return '<line class="gridline" x1="' + padL + '" x2="' + (width - padR) + '" y1="' + y + '" y2="' + y + '"/>';
  }).join('');
  // X-axis day labels: first, ~25%, mid, ~75%, last.
  const xLabelIdxs = days.length <= 1
    ? [0]
    : [0, Math.floor(days.length * 0.25), Math.floor(days.length / 2), Math.floor(days.length * 0.75), days.length - 1];
  const xLabels = Array.from(new Set(xLabelIdxs)).map(i => {
    const x = xOf(i);
    return '<text class="axis-text" x="' + x + '" y="' + (height - 6) + '" text-anchor="middle">' + escapeHtml(_fmtDay(days[i])) + '</text>';
  }).join('');
  // One polyline per visible metric, each normalized to its own peak so
  // every series fills the canvas vertically.
  const lines = visibleMetrics.map(m => {
    const byDay = _dailyMetricsSeries[m.id] || {};
    const peak = seriesPeak[m.id] || 0;
    const yOf = v => padT + plotH - (peak > 0 ? (v / peak) * plotH : 0);
    const pts = days.map((d, i) => xOf(i) + ',' + yOf(Number(byDay[d]) || 0)).join(' ');
    return '<polyline class="series-line" data-metric="' + escapeHtml(m.id) + '" stroke="' + m.color + '" points="' + pts + '"/>';
  }).join('');
  // Transparent rect captures pointer events for the tooltip.
  const svg =
    '<svg viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="none" role="img" aria-label="Daily metrics line chart">' +
      yGrid + xLabels + lines +
      '<line class="hover-line" id="daily-metrics-hover-line" x1="0" y1="' + padT + '" x2="0" y2="' + (padT + plotH) + '"/>' +
      '<rect id="daily-metrics-hover-rect" x="' + padL + '" y="' + padT + '" width="' + plotW + '" height="' + plotH + '" fill="transparent"/>' +
    '</svg>' +
    '<div class="daily-metrics-tooltip" id="daily-metrics-tooltip"></div>';
  chartEl.innerHTML = svg;
  if (statusEl) statusEl.textContent = visibleMetrics.length + ' of ' + DAILY_METRICS.length + ' series';

  // Hover interactions — snap to nearest day index, move dashed line,
  // populate tooltip. Positioned relative to the chart container so the
  // tooltip can use absolute CSS coords off the DOM rect.
  const rect = chartEl.querySelector('#daily-metrics-hover-rect');
  const hoverLine = chartEl.querySelector('#daily-metrics-hover-line');
  const tip = chartEl.querySelector('#daily-metrics-tooltip');
  if (rect && hoverLine && tip) {
    const show = e => {
      const svgEl = chartEl.querySelector('svg');
      const box = svgEl.getBoundingClientRect();
      const relX = e.clientX - box.left;
      const scale = width / box.width;
      const svgX = relX * scale;
      const idxRaw = (svgX - padL) / (xStep || 1);
      const idx = Math.max(0, Math.min(days.length - 1, Math.round(idxRaw)));
      const snapX = xOf(idx);
      hoverLine.setAttribute('x1', snapX);
      hoverLine.setAttribute('x2', snapX);
      hoverLine.style.opacity = '1';
      const day = days[idx];
      const rows = visibleMetrics.map(m => {
        const v = Number((_dailyMetricsSeries[m.id] || {})[day]) || 0;
        return '<div class="tt-row"><span class="swatch" style="background:' + m.color + ';"></span>' +
               '<span>' + escapeHtml(m.label) + '</span>' +
               '<span class="val">' + escapeHtml(v.toLocaleString()) + '</span></div>';
      }).join('');
      tip.innerHTML = '<div class="tt-day">' + escapeHtml(_fmtDay(day)) + '</div>' + rows;
      // Position the tooltip in CSS px relative to the chart container.
      const cssX = snapX / scale;
      tip.style.left = (cssX) + 'px';
      tip.style.top = (padT - 10) + 'px';
      tip.style.opacity = '1';
    };
    const hide = () => { hoverLine.style.opacity = '0'; tip.style.opacity = '0'; };
    rect.addEventListener('mousemove', show);
    rect.addEventListener('mouseleave', hide);
  }
}

async function loadDailyMetrics() {
  const chartEl = document.getElementById('daily-metrics-chart');
  const series = {};
  // Prebuild the 30-day axis (end-exclusive so last entry is today UTC).
  const today = new Date();
  const axis = [];
  for (let i = DAILY_METRICS_DAYS - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    axis.push(d.toISOString().slice(0, 10));
  }
  _dailyMetricsDays = axis;

  const fetchOne = async (url, mapRow) => {
    const res = await fetch(url);
    if (!res.ok) throw new Error('status ' + res.status);
    return (await res.json()).rows || [];
  };
  try {
    const qs = 'days=' + DAILY_METRICS_DAYS;
    const [viewsRows, upvotesRows, commentsRows, bookingsRows, funnelRows] = await Promise.all([
      fetchOne('/api/views/per-day?' + qs),
      fetchOne('/api/upvotes/per-day?' + qs),
      fetchOne('/api/comments/per-day?' + qs),
      fetchOne('/api/bookings/per-day?' + qs),
      fetchOne('/api/funnel/per-day?' + qs),
    ]);
    const intoSeries = (id, rows, key) => {
      const map = {};
      rows.forEach(r => { if (r && r.day) map[r.day] = Number(r[key]) || 0; });
      series[id] = map;
    };
    intoSeries('views',    viewsRows,    'views_gained');
    intoSeries('upvotes',  upvotesRows,  'upvotes_gained');
    intoSeries('comments', commentsRows, 'comments_gained');
    intoSeries('bookings', bookingsRows, 'bookings_gained');
    DAILY_METRICS.filter(m => m.funnel).forEach(m => {
      intoSeries(m.id, funnelRows, m.valueKey);
    });
    _dailyMetricsSeries = series;
    renderDailyMetrics();
  } catch (e) {
    if (chartEl) chartEl.innerHTML = '<div class="views-chart-empty">Unable to load daily metrics (' + escapeHtml(String(e.message || e)) + ').</div>';
  }
}

// Back-compat shim: earlier versions of this file exposed loadAllPerDayCharts
// for the 9-card layout. The tab wiring still calls that name — keep it as
// an alias so there's one refresh entry point.
function loadAllPerDayCharts() { return loadDailyMetrics(); }

// Shared helper: mount a sortable + per-column-filterable table into containerId.
// Only tbody and the sort-arrow glyphs are rewritten on state changes, so the
// filter <input> elements keep their focus and caret position while typing.
function mountSortableTable(opts) {
  const container = document.getElementById(opts.containerId);
  if (!container) return null;
  const cols = opts.columns;
  const rows = opts.rows || [];
  if (!rows.length) {
    container.innerHTML = '<div class="style-stats-empty">' + escapeHtml(opts.emptyMessage || 'No data.') + '</div>';
    return null;
  }
  const state = opts.state;
  state.filters = state.filters || {};
  const inlineFilters = !!opts.inlineFilters;
  const alignAttr = c => (c.align === 'right' ? ' style="text-align:right;"' : (c.align === 'left' ? ' style="text-align:left;"' : ''));
  const hasWidths = cols.some(c => c.widthPct != null);
  const colgroup = hasWidths
    ? '<colgroup>' + cols.map(c => '<col' + (c.widthPct != null ? ' style="width:' + c.widthPct + '%;"' : '') + ' />').join('') + '</colgroup>'
    : '';

  function buildInlineFilterHtml(c) {
    const mode = c.filterMode || 'text';
    if (mode === 'none') return '<span class="activity-col-filter-placeholder activity-col-filter-inline">\u00a0</span>';
    if (mode === 'dropdown') {
      const options = c.filterOptions || [];
      return '<select class="activity-col-filter activity-col-filter-inline" data-filter-key="' + escapeHtml(c.key) + '">' +
        options.map(o => '<option value="' + escapeHtml(o.value != null ? String(o.value) : '') + '">' + escapeHtml(o.label) + '</option>').join('') +
        '</select>';
    }
    return '<input type="text" class="activity-col-filter activity-col-filter-inline" data-filter-key="' + escapeHtml(c.key) + '" placeholder="filter\u2026" />';
  }

  const headerCells = cols.map(c => {
    const helpHtml = c.helpText
      ? ' <span class="col-info" tabindex="0" data-tooltip="' + escapeHtml(c.helpText) + '" aria-label="' + escapeHtml(c.helpText) + '">i</span>'
      : '';
    const labelHtml =
      '<span class="activity-header-label">' + escapeHtml(c.label) + helpHtml +
      ' <span class="activity-sort-arrow" data-sort-arrow-key="' + escapeHtml(c.key) + '"></span>' +
      '</span>';
    const innerHtml = inlineFilters
      ? '<div class="activity-th-stack">' + labelHtml + buildInlineFilterHtml(c) + '</div>'
      : labelHtml;
    // The global tooltip handler picks up data-tooltip when helpText is set;
    // otherwise fall back to the column label as a redundancy hint when text
    // gets ellipsized.
    const titleAttr = c.helpText ? '' : ' data-tooltip="' + escapeHtml(c.label) + '"';
    return '<th class="activity-sortable" data-sort-key="' + escapeHtml(c.key) + '"' + alignAttr(c) + titleAttr + '>' + innerHtml + '</th>';
  }).join('');
  const filterRowHtml = inlineFilters ? '' : (
    '<tr class="activity-filter-row">' +
    cols.map(c => (
      '<th' + alignAttr(c) + '>' +
        '<input type="text" class="activity-col-filter" data-filter-key="' + escapeHtml(c.key) + '" placeholder="filter\u2026" />' +
      '</th>'
    )).join('') +
    '</tr>'
  );
  const showTotals = !!opts.showTotals;
  const footerRowHtml = showTotals
    ? '<tr class="activity-total-row">' + cols.map(c => '<td data-footer-key="' + escapeHtml(c.key) + '"' + alignAttr(c) + '></td>').join('') + '</tr>'
    : '';
  container.innerHTML =
    '<div class="style-stats-table-wrapper">' +
      '<table class="style-stats-table">' +
        colgroup +
        '<thead>' +
          '<tr>' + headerCells + '</tr>' +
          filterRowHtml +
        '</thead>' +
        '<tbody></tbody>' +
        (showTotals ? '<tfoot>' + footerRowHtml + '</tfoot>' : '') +
      '</table>' +
    '</div>';
  const tbody = container.querySelector('tbody');
  const tfoot = showTotals ? container.querySelector('tfoot') : null;
  function cellValue(c, r) { return c.accessor ? c.accessor(r) : r[c.key]; }
  function cellDisplay(c, r) {
    const v = cellValue(c, r);
    if (c.formatter) return c.formatter(v, r);
    return v == null ? '' : escapeHtml(String(v));
  }
  function stripHtml(s) { return String(s).replace(/<[^>]*>/g, ''); }
  function matchesColumnFilter(c, fv, row) {
    const raw = cellValue(c, row);
    if (c.filterPredicate) return c.filterPredicate(fv, row, raw);
    const disp = c.formatter ? c.formatter(raw, row) : (raw == null ? '' : String(raw));
    return stripHtml(disp).toLowerCase().indexOf(String(fv).toLowerCase().trim()) !== -1;
  }
  function apply() {
    let filtered = rows;
    for (const c of cols) {
      const fv = state.filters[c.key];
      if (fv == null || fv === '') continue;
      filtered = filtered.filter(r => matchesColumnFilter(c, fv, r));
    }
    const gq = String(state.globalQuery || '').trim().toLowerCase();
    if (gq) {
      filtered = filtered.filter(r => {
        for (const c of cols) {
          const disp = cellDisplay(c, r);
          if (stripHtml(disp).toLowerCase().indexOf(gq) !== -1) return true;
        }
        return false;
      });
    }
    const sortCol = cols.find(c => c.key === state.sortField) || cols[0];
    const dir = state.sortDir === 'asc' ? 1 : -1;
    const sorted = filtered.slice().sort((a, b) => {
      const va = cellValue(sortCol, a);
      const vb = cellValue(sortCol, b);
      if (sortCol.type === 'numeric') {
        const na = Number(va); const nb = Number(vb);
        const aMissing = !Number.isFinite(na); const bMissing = !Number.isFinite(nb);
        if (aMissing && bMissing) return 0;
        if (aMissing) return 1;
        if (bMissing) return -1;
        return (na - nb) * dir;
      }
      return String(va == null ? '' : va).localeCompare(String(vb == null ? '' : vb)) * dir;
    });
    tbody.innerHTML = sorted.map(r => {
      const rid = opts.rowId ? opts.rowId(r) : null;
      const ridAttr = (rid !== null && rid !== undefined && rid !== '') ? ' data-row-id="' + escapeHtml(String(rid)) + '"' : '';
      return '<tr' + ridAttr + '>' + cols.map(c => '<td data-col-key="' + escapeHtml(c.key) + '"' + alignAttr(c) + '>' + cellDisplay(c, r) + '</td>').join('') + '</tr>';
    }).join('') || '<tr><td colspan="' + cols.length + '" style="text-align:center;color:var(--text-muted);padding:14px;">No rows match filters.</td></tr>';
    if (tfoot) {
      if (!sorted.length) {
        tfoot.style.display = 'none';
      } else {
        tfoot.style.display = '';
        // Build a synthetic "totals" row: sum each numeric field across all
        // filtered rows. Formatters that read sibling keys (e.g. makeFunnelFmt
        // reading r.domain_pageviews, makeFmtPerPost reading r.posts) then
        // work uniformly against the summed totals.
        const synth = {};
        const numericKeys = new Set();
        for (const c of cols) if (c.type === 'numeric') numericKeys.add(c.key);
        for (const r of sorted) {
          for (const k of Object.keys(r)) {
            const n = Number(r[k]);
            if (Number.isFinite(n)) synth[k] = (synth[k] || 0) + n;
          }
        }
        let firstTextDone = false;
        cols.forEach(c => {
          const cell = tfoot.querySelector('td[data-footer-key="' + c.key.replace(/"/g, '\\"') + '"]');
          if (!cell) return;
          let html = '';
          if (typeof c.footer === 'function') {
            html = c.footer(sorted, synth);
          } else if (c.type === 'numeric') {
            const sum = synth[c.key];
            if (c.formatter) html = c.formatter(Number.isFinite(sum) ? sum : 0, synth);
            else html = String(Number.isFinite(sum) ? sum : 0);
          } else if (!firstTextDone) {
            html = 'Total';
            firstTextDone = true;
          }
          cell.innerHTML = html == null ? '' : html;
        });
      }
    }
    if (opts.onAfterRender) opts.onAfterRender(tbody, sorted);
    container.querySelectorAll('[data-sort-arrow-key]').forEach(el => {
      const k = el.getAttribute('data-sort-arrow-key');
      if (k === state.sortField) {
        el.textContent = state.sortDir === 'asc' ? '\u25B2' : '\u25BC';
        el.classList.add('active');
      } else {
        el.textContent = '';
        el.classList.remove('active');
      }
    });
  }
  container.querySelectorAll('.activity-sortable').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target && (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT' || e.target.tagName === 'OPTION')) return;
      // Clicks on the per-column info icon must not trigger sort.
      if (e.target && e.target.closest && e.target.closest('.col-info')) return;
      const key = el.getAttribute('data-sort-key');
      if (state.sortField === key) {
        state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
      } else {
        state.sortField = key;
        const col = cols.find(c => c.key === key);
        state.sortDir = (col && col.type === 'numeric') ? 'desc' : 'asc';
      }
      apply();
    });
  });
  container.querySelectorAll('.activity-col-filter').forEach(el => {
    const k = el.getAttribute('data-filter-key');
    if (state.filters[k] != null) el.value = state.filters[k];
    const evt = el.tagName === 'SELECT' ? 'change' : 'input';
    el.addEventListener(evt, () => {
      state.filters[k] = el.value;
      apply();
    });
    el.addEventListener('click', e => e.stopPropagation());
    el.addEventListener('mousedown', e => e.stopPropagation());
  });
  apply();
  return { apply, container };
}

let _styleStatsTableState = { sortField: 'score', sortDir: 'desc', filters: {} };
// Per-column help text. Attached to each column via a helpText property and
// rendered as an info icon by mountSortableTable. The tooltip uses a custom
// CSS popover (.col-info-tip) so it appears immediately on hover without the
// OS-level title-attribute delay.
const STYLE_STATS_HELP = {
  style:    'Engagement tone Claude used to draft this first-touch comment/post (slug from scripts/engagement_styles.py). The A/B testing system uses these stats to decide which tones to imitate next. Note: a row in the posts table = our FIRST-TOUCH engagement on a thread, not (usually) an original thread we authored. Reddit/Moltbook/GitHub = our top-level comment on someone else’s thread; X = our reply; LinkedIn = our comment. Subsequent back-and-forth replies live in a separate replies pipeline and are not counted here.',
  score:    'Per-post quality signal computed on engagement that landed on OUR comment/post (replies to it, upvotes on it), not on the underlying third-party thread. Formula: (comments * 3 + upvotes_discounted) / posts. upvotes_discounted subtracts the OP self-upvote on Reddit and Moltbook so those platforms compare fairly with X/LinkedIn. Views are deliberately excluded so low-volume styles compare fairly with high-volume ones. Same signal the feedback report uses.',
  posts:    'Count of first-touch comments/posts published in this style during the selected window. (Reddit comments on others’ threads, X replies, LinkedIn comments, etc. The rare run-reddit-threads.sh original-thread rows are also counted.)',
  upvotes:  'Sum of upvotes/likes received by OUR comment (or our thread, in the rare original-thread case). Per-post average in parentheses, raw and not discounted.',
  comments: 'Sum of replies received by OUR comment (or comments under our thread). Per-post average in parentheses. Tracked in the posts.comments_count column, independent of the separate replies pipeline that records replies WE author.',
  views:    'Sum of impressions on OUR comment/post. Per-post average in parentheses. Moltbook and GitHub are excluded from both the total and the per-post denominator since neither platform exposes a views metric.',
  recommendations: 'Number of posts in this tone that ALSO carried a project recommendation (is_recommendation = true). Independent dimension from style: tells you how often this tone was used to deliver a product mention.',
};
function renderStyleStatsPills(containerId, values, selected, labelAll) {
  const row = document.getElementById(containerId);
  if (!row) return;
  row.dataset.selected = selected || 'all';
  // Preserve a user-picked value that has no rows in the current window.
  const want = ['all'].concat(values || []);
  if (selected && selected !== 'all' && !want.includes(selected)) want.push(selected);
  const labelEl = row.querySelector('.label');
  const labelHtml = labelEl ? labelEl.outerHTML : '';
  const pillsHtml = want.map(v => (
    '<button type="button" class="style-stats-pill' + (v === (selected || 'all') ? ' active' : '') +
    '" data-value="' + escapeHtml(v) + '">' +
    escapeHtml(v === 'all' ? labelAll : v) + '</button>'
  )).join('');
  row.innerHTML = labelHtml + pillsHtml;
  row.querySelectorAll('.style-stats-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      row.dataset.selected = btn.getAttribute('data-value') || 'all';
      // Reflect selection immediately so later clicks on stale siblings don't
      // re-toggle before the style-stats refetch returns and re-renders pills.
      row.querySelectorAll('.style-stats-pill').forEach(b => {
        b.classList.toggle('active', b === btn);
      });
      reloadStatsTabSections();
    });
  });
}
function renderStyleStats(payload) {
  const body = document.getElementById('style-stats-body');
  const totalEl = document.getElementById('style-stats-total');
  if (!body) return;
  const selectedPlatform = (payload && payload.platform) || 'all';
  const selectedProject  = (payload && payload.project)  || 'all';
  renderStyleStatsPills('style-stats-platform-pills', (payload && payload.platforms) || [], selectedPlatform, 'All');
  renderStyleStatsPills('style-stats-project-pills',  (payload && payload.projects)  || [], selectedProject,  'All');
  const rows = (payload && payload.rows) || [];
  if (!rows.length) {
    if (totalEl) totalEl.textContent = '0 posts';
    const scope = [
      selectedPlatform !== 'all' ? selectedPlatform : '',
      selectedProject  !== 'all' ? selectedProject  : '',
    ].filter(Boolean).join(' / ');
    const winLabel = currentStatsWindow().labelLong;
    const label = scope ? 'No ' + scope + ' posts in the ' + winLabel + '.' : 'No posts in the ' + winLabel + '.';
    body.innerHTML = '<div class="style-stats-empty">' + escapeHtml(label) + '</div>';
    return;
  }
  const totalPosts = rows.reduce((a, r) => a + (Number(r.posts) || 0), 0);
  if (totalEl) totalEl.textContent = totalPosts.toLocaleString() + ' post' + (totalPosts === 1 ? '' : 's');
  const fmt = n => (Number(n) || 0).toLocaleString();
  const perPostStr = v => {
    if (!Number.isFinite(v)) return '0';
    if (v >= 100) return Math.round(v).toLocaleString();
    if (v >= 10)  return v.toFixed(1);
    return v.toFixed(2);
  };
  // Views uses views_posts (excludes Moltbook and GitHub rows) as the denominator because
  // neither platform exposes views. Upvotes/comments use the full posts count.
  const denomFor = (field, r) => {
    if (field === 'views') return Number(r && r.views_posts) || 0;
    return Number(r && r.posts) || 0;
  };
  const perPostAccessor = field => r => {
    const denom = denomFor(field, r);
    if (denom <= 0) return -1;
    return (Number(r[field]) || 0) / denom;
  };
  const makeFmtPerPost = field => (_v, r) => {
    const denom = denomFor(field, r);
    if (denom <= 0) return '\u2014';
    const total = Number(r && r[field]) || 0;
    const per   = total / denom;
    return fmt(total) + ' <span style="color:var(--text-muted);">(' + perPostStr(per) + ')</span>';
  };
  // Per-post score matches top_performers.SCORE_SQL (comments*3 + upvotes, Reddit
  // self-upvote discounted at SQL layer). Views deliberately excluded so this is
  // the same signal Claude uses for imitation; comparing by per-post keeps low-
  // volume styles on equal footing with high-volume ones.
  const normalized = rows.map(r => {
    const posts            = Number(r.posts)             || 0;
    const comments         = Number(r.comments)          || 0;
    const upvotesDiscounted = Number(r.upvotes_discounted) || 0;
    const score = posts > 0 ? (comments * 3 + upvotesDiscounted) / posts : 0;
    return {
      style:       r.style || '(none)',
      posts,
      views_posts: Number(r.views_posts) || 0,
      upvotes:     Number(r.upvotes)     || 0,
      comments,
      views:       Number(r.views)       || 0,
      recommendations: Number(r.recommendations) || 0,
      score,
    };
  });
  const scoreFmt = (_v, r) => {
    if (!r || r.posts <= 0) return '\u2014';
    const v = Number(r.score) || 0;
    if (v >= 100) return Math.round(v).toLocaleString();
    if (v >= 10)  return v.toFixed(1);
    return v.toFixed(2);
  };
  mountSortableTable({
    containerId: 'style-stats-body',
    rows: normalized,
    state: _styleStatsTableState,
    showTotals: true,
    columns: [
      { key: 'style',    label: 'Style',    type: 'text',    align: 'left',  formatter: v => escapeHtml(v), helpText: STYLE_STATS_HELP.style },
      // Score isn't summable across styles: it's a per-post ratio derived
      // from upvotes_discounted (which isn't available in the normalized
      // rows), so blank the footer rather than show a misleading aggregate.
      { key: 'score',    label: 'Score',    type: 'numeric', align: 'right', formatter: scoreFmt, footer: () => '', helpText: STYLE_STATS_HELP.score },
      { key: 'posts',    label: 'Posts',    type: 'numeric', align: 'right', formatter: fmt, helpText: STYLE_STATS_HELP.posts },
      // makeFmtPerPost reads r.posts / r.views_posts as denominators. The
      // synthetic footer row has summed posts and views_posts, so the same
      // formatter computes sum(upvotes)/sum(posts) etc. automatically.
      { key: 'upvotes',  label: 'Upvotes',  type: 'numeric', align: 'right', accessor: perPostAccessor('upvotes'),  formatter: makeFmtPerPost('upvotes'),  footer: (_rows, synth) => makeFmtPerPost('upvotes')(null, synth), helpText: STYLE_STATS_HELP.upvotes },
      { key: 'comments', label: 'Comments', type: 'numeric', align: 'right', accessor: perPostAccessor('comments'), formatter: makeFmtPerPost('comments'), footer: (_rows, synth) => makeFmtPerPost('comments')(null, synth), helpText: STYLE_STATS_HELP.comments },
      { key: 'views',    label: 'Views',    type: 'numeric', align: 'right', accessor: perPostAccessor('views'),    formatter: makeFmtPerPost('views'),    footer: (_rows, synth) => makeFmtPerPost('views')(null, synth), helpText: STYLE_STATS_HELP.views },
      // Intent column: count of posts in this tone that were ALSO flagged as a
      // project recommendation. Independent dimension from style.
      { key: 'recommendations', label: 'Recs', type: 'numeric', align: 'right', formatter: fmt, helpText: STYLE_STATS_HELP.recommendations },
    ],
  });
}

async function loadStyleStats() {
  try {
    const platformRow = document.getElementById('style-stats-platform-pills');
    const projectRow  = document.getElementById('style-stats-project-pills');
    const platform = (platformRow && platformRow.dataset.selected) || 'all';
    const project  = (projectRow  && projectRow.dataset.selected)  || 'all';
    const hours = currentStatsWindow().hours;
    const params = ['hours=' + hours];
    if (platform && platform !== 'all') params.push('platform=' + encodeURIComponent(platform));
    if (project  && project  !== 'all') params.push('project='  + encodeURIComponent(project));
    const res = await fetch('/api/style/stats?' + params.join('&'));
    const data = await res.json();
    renderStyleStats(data);
  } catch {}
}

let _funnelStatsTableState = { sortField: 'posts', sortDir: 'desc', filters: {} };
function renderFunnelStats(payload) {
  const body = document.getElementById('funnel-stats-body');
  const totalEl = document.getElementById('funnel-stats-total');
  if (!body) return;
  if (payload && payload.error) {
    if (totalEl) totalEl.textContent = 'error';
    body.innerHTML = '<div class="style-stats-empty">' + escapeHtml(payload.error) + '</div>';
    return;
  }
  const _projRow = document.getElementById('style-stats-project-pills');
  const _selProj = (_projRow && _projRow.dataset.selected) || 'all';
  const projects = ((payload && payload.projects) || []).filter(
    p => _selProj === 'all' || p.name === _selProj
  );
  if (!projects.length) {
    if (totalEl) totalEl.textContent = '0 projects';
    body.innerHTML = '<div class="style-stats-empty">No project data.</div>';
    return;
  }
  const fmt = n => (Number(n) || 0).toLocaleString();
  const totals = projects.reduce((a, p) => {
    const f = p.funnel || {};
    a.posts            += (p.posts && p.posts.recent)             || 0;
    a.seo              += (p.seo && p.seo.pages_recent)           || 0;
    a.pageviews        += Number(f.pageviews)        || 0;
    a.email_signups    += Number(f.email_signups)    || 0;
    a.schedule_clicks  += Number(f.schedule_clicks)  || 0;
    a.get_started_clicks += Number(f.get_started_clicks) || 0;
    a.cross_product_clicks += Number(f.cross_product_clicks) || 0;
    a.d_pageviews      += Number(f.domain_pageviews) || 0;
    a.d_email_signups  += Number(f.domain_email_signups) || 0;
    a.d_schedule_clicks += Number(f.domain_schedule_clicks) || 0;
    a.d_get_started_clicks += Number(f.domain_get_started_clicks) || 0;
    a.bookings         += Number(f.real_bookings)    || 0;
    a.dm_clicks        += Number(f.dm_clicks)        || 0;
    a.dm_bookings      += Number(f.dm_bookings)      || 0;
    return a;
  }, { posts: 0, seo: 0, pageviews: 0, email_signups: 0, schedule_clicks: 0, get_started_clicks: 0, cross_product_clicks: 0, d_pageviews: 0, d_email_signups: 0, d_schedule_clicks: 0, d_get_started_clicks: 0, bookings: 0, dm_clicks: 0, dm_bookings: 0 });
  // Compact cell: "<scoped> (<domain>)" when they differ, just "<scoped>"
  // when equal. Keeps the table scannable while still exposing domain-wide
  // traffic that doesn't happen to land on pages generated this window.
  const pair = (scoped, domain) => {
    const s = Number(scoped) || 0;
    const d = Number(domain) || 0;
    if (d === s) return fmt(s);
    return fmt(s) + ' (' + fmt(d) + ')';
  };
  if (totalEl) totalEl.textContent = projects.length + ' project' + (projects.length === 1 ? '' : 's');
  // pair() is kept for potential future use but no longer emitted in the
  // header; the footer row now carries per-column totals.
  void pair;
  const normalized = projects.map(p => {
    const pst = p.posts || {};
    const seo = p.seo || {};
    const f = p.funnel || {};
    // When the PostHog fetch failed, the backend sends analytics_error
    // plus null funnel counters. Preserve null so we can render 'err' on
    // those cells instead of silently reporting 0.
    const asNum = v => (v == null ? null : (Number(v) || 0));
    return {
      name:             p.name || '',
      analytics_suspected_broken: !!p.analytics_suspected_broken,
      analytics_error:  p.analytics_error || null,
      posts:            Number(pst.recent) || 0,
      upvotes:          Number(pst.upvotes_recent)  || 0,
      comments:         Number(pst.comments_recent) || 0,
      views:            pst.views_recent == null ? null : Number(pst.views_recent),
      seo_pages:        Number(seo.pages_recent)    || 0,
      pageviews:        asNum(f.pageviews),
      email_signups:    asNum(f.email_signups),
      schedule_clicks:  asNum(f.schedule_clicks),
      get_started_clicks: asNum(f.get_started_clicks),
      cross_product_clicks: asNum(f.cross_product_clicks),
      // Domain-wide counterparts, rendered in parens next to the scoped value.
      domain_pageviews:        asNum(f.domain_pageviews),
      domain_email_signups:    asNum(f.domain_email_signups),
      domain_schedule_clicks:  asNum(f.domain_schedule_clicks),
      domain_get_started_clicks: asNum(f.domain_get_started_clicks),
      bookings:         Number(f.real_bookings)     || 0,
      dm_clicks:        Number(f.dm_clicks)         || 0,
      dm_bookings:      Number(f.dm_bookings)       || 0,
    };
  });
  const fmtProjectName = (v, r) => {
    const name = escapeHtml(v);
    if (r && r.analytics_error) {
      const tip = escapeHtml('PostHog fetch failed: ' + String(r.analytics_error));
      return name + ' <span title="' + tip + '" style="color:#dc2626;cursor:help;margin-left:4px;" aria-label="analytics fetch error">\u26A0</span>';
    }
    if (r && r.analytics_suspected_broken) {
      const tip = escapeHtml('High pageviews but zero tracked signups, schedule clicks, or get-started clicks; posthog likely not wired on this site. See https://github.com/m13v/seo-components#posthog-setup');
      return name + ' <span title="' + tip + '" style="color:#dc2626;cursor:help;margin-left:4px;" aria-label="analytics suspected broken">\u26A0</span>';
    }
    return name;
  };
  // Funnel cell formatter factory: takes the sibling domain-wide field
  // name and returns a (value, row) formatter. Renders "<scoped>
  // (<domain>)" when they differ, "err" on fetch failure, and just
  // "<scoped>" when the two match. Keeps a genuine 0 distinguishable
  // from a missing-analytics 0.
  const makeFunnelFmt = domainKey => (v, r) => {
    if (r && r.analytics_error) {
      const tip = escapeHtml('PostHog fetch failed: ' + String(r.analytics_error));
      return '<span title="' + tip + '" style="color:#dc2626;cursor:help;" aria-label="analytics fetch error">err</span>';
    }
    if (v == null) return '\u2014';
    const d = r && r[domainKey];
    if (d != null && Number(d) !== Number(v)) {
      return fmt(v) + ' <span style="color:var(--text-muted);">(' + fmt(d) + ')</span>';
    }
    return fmt(v);
  };
  mountSortableTable({
    containerId: 'funnel-stats-body',
    rows: normalized,
    state: _funnelStatsTableState,
    showTotals: true,
    columns: [
      { key: 'name',             label: 'Project',         type: 'text',    align: 'left',  formatter: fmtProjectName },
      { key: 'posts',            label: 'Posts',           type: 'numeric', align: 'right', formatter: fmt },
      { key: 'upvotes',          label: 'Upvotes',         type: 'numeric', align: 'right', formatter: fmt },
      { key: 'comments',         label: 'Comments',        type: 'numeric', align: 'right', formatter: fmt },
      { key: 'views',            label: 'Views',           type: 'numeric', align: 'right', formatter: v => v == null ? '\u2014' : fmt(v) },
      { key: 'seo_pages',        label: 'SEO Pages',       type: 'numeric', align: 'right', formatter: fmt },
      // Funnel cells use makeFunnelFmt, which reads a sibling "domain_*"
      // field off the row. The synthetic footer row carries summed
      // domain_* totals too, so the same formatter renders "<scoped> (<domain>)".
      { key: 'pageviews',        label: 'Pageviews',       type: 'numeric', align: 'right', formatter: makeFunnelFmt('domain_pageviews') },
      { key: 'email_signups',    label: 'Email Signups',   type: 'numeric', align: 'right', formatter: makeFunnelFmt('domain_email_signups') },
      { key: 'schedule_clicks',  label: 'Schedule Clicks', type: 'numeric', align: 'right', formatter: makeFunnelFmt('domain_schedule_clicks') },
      { key: 'get_started_clicks', label: 'Get Started',   type: 'numeric', align: 'right', formatter: makeFunnelFmt('domain_get_started_clicks') },
      { key: 'bookings',         label: 'Bookings',        type: 'numeric', align: 'right', formatter: fmt },
      // DM Clicks: SUM(dms.short_link_clicks) for DMs targeting this project
      // in the window. Counts every click on a /r/code short link that
      // resolves to one of this project's DMs. NOT the same as Schedule
      // Clicks (which is on-page CTA taps via withBookingAttribution).
      { key: 'dm_clicks',        label: 'DM Clicks',       type: 'numeric', align: 'right',
        formatter: (v, r) => {
          const n = Number(v) || 0;
          if (!n) return '<span style="color:var(--text-faint);">\u2014</span>';
          return '<span data-tooltip="Clicks on short links sent in DMs targeting this project" style="font-variant-numeric:tabular-nums;">' + fmt(n) + '</span>';
        } },
      // DM Bookings: subset of the Bookings column whose utm_content matches
      // dm_<id> and the DM targets this project. Tells you of the bookings
      // this project got, how many were attributable to a DM we sent.
      { key: 'dm_bookings',      label: 'DM Bookings',     type: 'numeric', align: 'right',
        formatter: (v, r) => {
          const n = Number(v) || 0;
          if (!n) return '<span style="color:var(--text-faint);">\u2014</span>';
          const total = Number(r && r.bookings) || 0;
          const tip = total ? (n + ' of ' + total + ' bookings came from DMs') : (n + ' DM-attributed booking' + (n === 1 ? '' : 's'));
          return '<span data-tooltip="' + escapeHtml(tip) + '" style="color:var(--success);font-weight:600;font-variant-numeric:tabular-nums;">' + fmt(n) + '</span>';
        } },
      // Cross-product: clicks on CTAs that promote a sibling product
      // (e.g. Claude Meter CTA on Fazm blog posts). Fires the
      // cross_product_click event via trackCrossProductClick.
      { key: 'cross_product_clicks', label: 'Cross Product', type: 'numeric', align: 'right', formatter: v => v == null ? '\u2014' : fmt(v) },
    ],
  });
  // Inline legend below the table explaining the "N (M)" cell format.
  // Must come after mountSortableTable, which replaces container innerHTML.
  body.insertAdjacentHTML('beforeend',
    '<div style="font-size:11px;color:var(--text-muted);padding:6px 2px 2px;">' +
      'Pageviews, email signups, schedule, and get-started cells show ' +
      '<b>scoped</b> (only traffic on pages generated in the selected window), ' +
      'followed by <b>(domain-wide)</b> totals in parentheses when the two differ.' +
    '</div>');
}

let _dmStatsTableState = { sortField: 'dms', sortDir: 'desc', filters: {} };
function renderDmStats(payload) {
  const body = document.getElementById('dm-stats-body');
  const totalEl = document.getElementById('dm-stats-total');
  if (!body) return;
  if (payload && payload.error) {
    if (totalEl) totalEl.textContent = 'error';
    body.innerHTML = '<div class="style-stats-empty">' + escapeHtml(payload.error) + '</div>';
    return;
  }
  const projects = (payload && payload.projects) || [];
  if (!projects.length) {
    if (totalEl) totalEl.textContent = '0 projects';
    body.innerHTML = '<div class="style-stats-empty">No DM activity in this window.</div>';
    return;
  }
  const fmt = n => (Number(n) || 0).toLocaleString();
  const totals = projects.reduce((a, p) => {
    a.dms                += Number(p.dms)                || 0;
    a.replied            += Number(p.replied)            || 0;
    a.hot                += Number(p.hot)                || 0;
    a.warm               += Number(p.warm)               || 0;
    a.general_discussion += Number(p.general_discussion) || 0;
    a.cold               += Number(p.cold)               || 0;
    a.not_our_prospect   += Number(p.not_our_prospect)   || 0;
    a.declined           += Number(p.declined)           || 0;
    a.no_response        += Number(p.no_response)        || 0;
    a.icp_match          += Number(p.icp_match)          || 0;
    a.icp_miss           += Number(p.icp_miss)           || 0;
    a.icp_disqualified   += Number(p.icp_disqualified)   || 0;
    a.icp_unknown        += Number(p.icp_unknown)        || 0;
    a.asked              += Number(p.asked)              || 0;
    a.answered           += Number(p.answered)           || 0;
    a.qualified          += Number(p.qualified)          || 0;
    a.q_disqualified     += Number(p.q_disqualified)     || 0;
    a.booking_sent       += Number(p.booking_sent)       || 0;
    a.converted          += Number(p.converted)          || 0;
    a.needs_human        += Number(p.needs_human)        || 0;
    return a;
  }, { dms: 0, replied: 0, hot: 0, warm: 0, general_discussion: 0, cold: 0, not_our_prospect: 0, declined: 0, no_response: 0, icp_match: 0, icp_miss: 0, icp_disqualified: 0, icp_unknown: 0, asked: 0, answered: 0, qualified: 0, q_disqualified: 0, booking_sent: 0, converted: 0, needs_human: 0 });
  if (totalEl) totalEl.textContent = projects.length + ' project' + (projects.length === 1 ? '' : 's');
  const normalized = projects.map(p => ({
    name:               p.name || '',
    dms:                Number(p.dms)                || 0,
    replied:            Number(p.replied)            || 0,
    reply_rate:         (Number(p.dms) || 0) > 0 ? (Number(p.replied) || 0) / Number(p.dms) : 0,
    hot:                Number(p.hot)                || 0,
    warm:               Number(p.warm)               || 0,
    general_discussion: Number(p.general_discussion) || 0,
    cold:               Number(p.cold)               || 0,
    not_our_prospect:   Number(p.not_our_prospect)   || 0,
    declined:           Number(p.declined)           || 0,
    no_response:        Number(p.no_response)        || 0,
    icp_match:          Number(p.icp_match)          || 0,
    icp_miss:           Number(p.icp_miss)           || 0,
    icp_disqualified:   Number(p.icp_disqualified)   || 0,
    icp_unknown:        Number(p.icp_unknown)        || 0,
    asked:              Number(p.asked)              || 0,
    answered:           Number(p.answered)           || 0,
    qualified:          Number(p.qualified)          || 0,
    q_disqualified:     Number(p.q_disqualified)     || 0,
    booking_sent:       Number(p.booking_sent)       || 0,
    converted:          Number(p.converted)          || 0,
    needs_human:        Number(p.needs_human)        || 0,
  }));
  const pct = v => (Number(v) * 100).toFixed(0) + '%';
  // Reply % must not sum per-row rates (that would produce nonsense like
  // 380%). Recompute from the summed dms/replied in the synthetic totals row.
  const replyRateFooter = (_rows, synth) => pct((synth.dms || 0) > 0 ? (synth.replied || 0) / synth.dms : 0);
  mountSortableTable({
    containerId: 'dm-stats-body',
    rows: normalized,
    state: _dmStatsTableState,
    showTotals: true,
    columns: [
      { key: 'name',               label: 'Project',      type: 'text',    align: 'left',  formatter: v => escapeHtml(PROJECT_LABELS[v] || v) },
      { key: 'dms',                label: 'DMs',          type: 'numeric', align: 'right', formatter: fmt },
      { key: 'replied',            label: 'Replied',      type: 'numeric', align: 'right', formatter: fmt },
      { key: 'reply_rate',         label: 'Reply %',      type: 'numeric', align: 'right', formatter: pct, footer: replyRateFooter },
      { key: 'hot',                label: 'Hot',          type: 'numeric', align: 'right', formatter: fmt },
      { key: 'warm',               label: 'Warm',         type: 'numeric', align: 'right', formatter: fmt },
      { key: 'general_discussion', label: 'General',      type: 'numeric', align: 'right', formatter: fmt },
      { key: 'cold',               label: 'Cold',         type: 'numeric', align: 'right', formatter: fmt },
      { key: 'not_our_prospect',   label: 'NotProsp',     type: 'numeric', align: 'right', formatter: fmt },
      { key: 'declined',           label: 'Declined',     type: 'numeric', align: 'right', formatter: fmt },
      { key: 'no_response',        label: 'NoResp',       type: 'numeric', align: 'right', formatter: fmt },
      { key: 'icp_match',          label: 'ICP Match',    type: 'numeric', align: 'right', formatter: fmt },
      { key: 'icp_miss',           label: 'ICP Miss',     type: 'numeric', align: 'right', formatter: fmt },
      { key: 'icp_disqualified',   label: 'ICP Disq',     type: 'numeric', align: 'right', formatter: fmt },
      { key: 'icp_unknown',        label: 'ICP Unk',      type: 'numeric', align: 'right', formatter: fmt },
      { key: 'asked',              label: 'Asked',        type: 'numeric', align: 'right', formatter: fmt },
      { key: 'answered',           label: 'Answered',     type: 'numeric', align: 'right', formatter: fmt },
      { key: 'qualified',          label: 'Qualified',    type: 'numeric', align: 'right', formatter: fmt },
      { key: 'q_disqualified',     label: 'Disqual',      type: 'numeric', align: 'right', formatter: fmt },
      { key: 'booking_sent',       label: 'Booking Sent', type: 'numeric', align: 'right', formatter: fmt },
      { key: 'converted',          label: 'Converted',    type: 'numeric', align: 'right', formatter: fmt },
      { key: 'needs_human',        label: 'Needs Human',  type: 'numeric', align: 'right', formatter: fmt },
    ],
  });
}

// Cost Stats: per-activity-type count + total cost + cost-per-activity, driven
// by /api/cost/stats. Types are the four the user cares about: thread (posts),
// comment (replies), page (SEO pages), dm_thread (DMs). Section is closed by
// default and lazy-loaded on first open; platform pills and window pills
// trigger refetches. Admin-only (hidden via sa-local-only on cloud).
const COST_TYPE_LABELS = { thread: 'Thread', comment: 'Comment', page: 'Page', dm_thread: 'DM Thread' };
const COST_TYPE_ORDER = ['thread', 'comment', 'page', 'dm_thread'];
function renderCostStats(payload) {
  const body = document.getElementById('cost-stats-body');
  const totalEl = document.getElementById('cost-stats-total');
  if (!body) return;
  if (payload && payload.error) {
    if (totalEl) totalEl.textContent = 'error';
    body.innerHTML = '<div class="style-stats-empty">' + escapeHtml(payload.error) + '</div>';
    return;
  }
  const rows = (payload && payload.rows) || [];
  const byType = {};
  rows.forEach(r => { byType[r.type] = r; });
  const merged = COST_TYPE_ORDER.map(t => {
    const r = byType[t] || { count: 0, total_cost_usd: 0 };
    const count = Number(r.count) || 0;
    const total = Number(r.total_cost_usd) || 0;
    return { type: t, label: COST_TYPE_LABELS[t], count: count, total: total, avg: count > 0 ? total / count : 0 };
  });
  const totalCount = merged.reduce(function (a, r) { return a + r.count; }, 0);
  const totalCost  = merged.reduce(function (a, r) { return a + r.total; }, 0);
  if (totalEl) {
    totalEl.textContent = '$' + totalCost.toFixed(2) + ' · ' + totalCount.toLocaleString() + ' activit' + (totalCount === 1 ? 'y' : 'ies');
  }
  function fmtMoney(v) {
    var n = Number(v) || 0;
    if (n === 0) return '$0.00';
    if (n < 1) return '$' + n.toFixed(4);
    return '$' + n.toFixed(2);
  }
  function fmtCount(v) { return (Number(v) || 0).toLocaleString(); }
  const rowsHtml = merged.map(function (r) {
    return '<tr>' +
      '<td>' + escapeHtml(r.label) + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + fmtCount(r.count) + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + fmtMoney(r.total) + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;color:var(--text-muted);">' + (r.count > 0 ? fmtMoney(r.avg) : '&mdash;') + '</td>' +
    '</tr>';
  }).join('');
  const footerHtml =
    '<tr style="border-top:2px solid var(--border);font-weight:600;background:var(--bg-subtle);">' +
      '<td>Total</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + fmtCount(totalCount) + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + fmtMoney(totalCost) + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + (totalCount > 0 ? fmtMoney(totalCost / totalCount) : '&mdash;') + '</td>' +
    '</tr>';
  body.innerHTML =
    '<table class="style-stats-table">' +
      '<thead><tr>' +
        '<th style="text-align:left;">Type</th>' +
        '<th style="text-align:right;">Activities</th>' +
        '<th style="text-align:right;">Total Cost</th>' +
        '<th style="text-align:right;">Cost per Activity</th>' +
      '</tr></thead>' +
      '<tbody>' + rowsHtml + footerHtml + '</tbody>' +
    '</table>' +
    '<div style="font-size:11px;color:var(--text-muted);padding:8px 2px 2px;">' +
      'Cost is Claude session spend split evenly across the activity rows each session produced. ' +
      'Totals here exclude skipped replies, resurrected posts, DM replies, and mentions.' +
    '</div>';
}

let _costStatsLoadedFor = null;
let _costStatsLoading = false;
async function loadCostStats(force) {
  if (_costStatsLoading) return;
  const hours = currentStatusWindow().hours;
  const row = document.getElementById('cost-stats-platform-pills');
  const platform = (row && row.dataset.selected) || 'all';
  const key = hours + '|' + platform;
  if (_costStatsLoadedFor === key && !force) return;
  _costStatsLoading = true;
  const totalEl = document.getElementById('cost-stats-total');
  const body = document.getElementById('cost-stats-body');
  if (totalEl) totalEl.textContent = 'loading…';
  if (body) body.innerHTML = '<div class="style-stats-empty">Loading…</div>';
  try {
    const params = ['hours=' + hours];
    if (platform && platform !== 'all') params.push('platform=' + encodeURIComponent(platform));
    const res = await fetch('/api/cost/stats?' + params.join('&'));
    const data = await res.json();
    renderCostStats(data);
    _costStatsLoadedFor = key;
  } catch (e) {
    if (body) body.innerHTML = '<div class="style-stats-empty">Failed to load.</div>';
  } finally {
    _costStatsLoading = false;
  }
}

let _topTableState = { sortField: 'score', sortDir: 'desc', filters: {}, globalQuery: '' };
let _topTableHandle = null;
let _topLoaded = false;
let _topLoading = false;
let _topWindow = coerceTopWindow(loadSavedDashboardWindow());
let _topPlatform = 'all';
let _topSubtab = 'threads';
let _topProject = 'all';
let _topPagesTableState = { sortField: 'pageviews', sortDir: 'desc', filters: {} };
let _topPagesLoaded = false;
let _topPagesLoading = false;
let _topPagesSource = 'seo';
let _topDmsTableState = { sortField: 'rank', sortDir: 'asc', filters: {} };
let _topDmsLoaded = false;
let _topDmsLoading = false;
let _topDmsPayload = null;
let _topDmDir = 'all';
let _topDmInterest = 'all';
let _topDmMode = 'all';
let _topDmTier = 'all';
let _topDmQual = 'all';
let _topDmStatus = 'all';
// Server-side filtering for DMs sub-tab. When _topDmSearch is non-empty the
// API drops its time-window filter so old threads can be located by author or
// message text. _topDmOffset drives the "Load more" button.
let _topDmSearch = '';
let _topDmOffset = 0;
let _topDmSearchTimer = null;
const TOP_DM_PAGE_SIZE = 200;
// Wider page size when window=all/90d so deprioritized threads (sort_bucket
// 80-90: declined / not_our_prospect) are reachable in one fetch.
function topDmPageSize() {
  return (_topWindow === 'all' || _topWindow === '90d') ? 1000 : TOP_DM_PAGE_SIZE;
}
let _topPostsPayload = null;
const _topProjectNames = new Set();

function refreshTopProjectPills(newNames) {
  const projRow = document.getElementById('top-project-pills');
  if (!projRow) return;
  if (Array.isArray(newNames)) {
    for (const n of newNames) {
      if (n && typeof n === 'string') _topProjectNames.add(n);
    }
  }
  const sorted = Array.from(_topProjectNames).sort((a, b) => a.localeCompare(b));
  const wanted = ['all', ...sorted];
  const existing = Array.from(projRow.querySelectorAll('.style-stats-pill'))
    .map(b => b.getAttribute('data-value') || '');
  const same = wanted.length === existing.length && wanted.every((v, i) => v === existing[i]);
  if (same) return;
  const current = projRow.dataset.selected || 'all';
  const selected = (current === 'all' || sorted.includes(current)) ? current : 'all';
  if (selected !== current) _topProject = selected;
  projRow.dataset.selected = selected;
  const labelHtml = '<span class="label">Project</span>';
  const pillsHtml = wanted.map(v => (
    '<button type="button" class="style-stats-pill' + (v === selected ? ' active' : '') +
    '" data-value="' + escapeHtml(v) + '">' +
    escapeHtml(v === 'all' ? 'All' : v) + '</button>'
  )).join('');
  projRow.innerHTML = labelHtml + pillsHtml;
}

function parentLabel(post) {
  const plat = String(post.platform || '').toLowerCase();
  if (plat === 'reddit') {
    const m = String(post.thread_url || '').match(/reddit\\.com\\/r\\/([^/]+)/i);
    return m ? 'r/' + m[1] : 'reddit';
  }
  if (plat === 'twitter' || plat === 'x') {
    const m = String(post.thread_url || '').match(/(?:twitter|x)\\.com\\/([^/]+)/i);
    return m ? '@' + m[1] : (post.our_account ? '@' + String(post.our_account).replace(/^@/, '') : 'x');
  }
  if (plat === 'linkedin') return 'linkedin';
  return plat || '';
}

function fmtDateShort(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toISOString().slice(0, 10);
}

function renderTopContentCell(_v, post) {
  const text     = escapeHtml(post.our_content || '');
  const link     = post.our_url ? escapeHtml(post.our_url) : '';
  const date     = fmtDateShort(post.posted_at);
  const parent   = escapeHtml(parentLabel(post));
  const threadTx = String(post.thread_title || post.thread_content || '').trim();
  const threadHt = threadTx ? escapeHtml(threadTx.slice(0, 140) + (threadTx.length > 140 ? '\u2026' : '')) : '';
  const linkHtml = link
    ? '<a href="' + link + '" target="_blank" rel="noopener" class="top-post-link">' + link + '</a>'
    : '';
  const metaBits = [];
  if (date) metaBits.push(escapeHtml(date));
  if (parent) {
    const threadUrl = post.thread_url ? escapeHtml(post.thread_url) : '';
    metaBits.push(threadUrl ? '<a href="' + threadUrl + '" target="_blank" rel="noopener">' + parent + '</a>' : parent);
  }
  if (threadHt) metaBits.push('<span class="top-post-parent-title">\u201c' + threadHt + '\u201d</span>');
  const metaHtml = metaBits.length ? '<div class="top-post-meta">(' + metaBits.join(' \u00b7 ') + ')</div>' : '';
  return '<div class="top-post-content">' +
    '<div class="top-post-text">' + text + (date ? ' <span style="color:var(--text-muted);">\u00b7 ' + escapeHtml(date) + '</span>' : '') + '</div>' +
    linkHtml +
    metaHtml +
  '</div>';
}

function distinctOptions(rows, key, labelAll) {
  const set = new Set();
  for (const r of rows) {
    const v = r[key];
    if (v == null || v === '') continue;
    set.add(String(v));
  }
  const vals = Array.from(set).sort();
  const opts = [{ label: labelAll || 'All', value: '' }];
  for (const v of vals) opts.push({ label: v, value: '=' + v });
  return opts;
}

function numericThresholdOptions(rows, key) {
  const vals = rows.map(r => Number(r[key])).filter(v => Number.isFinite(v) && v > 0);
  if (!vals.length) return [{ label: 'Any', value: '' }];
  const max = Math.max.apply(null, vals);
  const ladder = [10, 100, 1000, 10000, 100000, 1000000];
  const opts = [{ label: 'Any', value: '' }];
  for (const t of ladder) {
    if (t <= max) opts.push({ label: '\u2265 ' + t.toLocaleString(), value: '>=' + t });
  }
  return opts;
}

function ageThresholdOptions() {
  return [
    { label: 'Any',         value: '' },
    { label: '< 1 day',     value: 'age<=1' },
    { label: '< 3 days',    value: 'age<=3' },
    { label: '< 7 days',    value: 'age<=7' },
    { label: '< 14 days',   value: 'age<=14' },
    { label: '< 30 days',   value: 'age<=30' },
    { label: '< 90 days',   value: 'age<=90' },
  ];
}

function filterPredicateExact(fv, _row, rowValue) {
  if (!fv || !fv.startsWith('=')) return true;
  return String(rowValue == null ? '' : rowValue) === fv.slice(1);
}

function filterPredicateGte(fv, _row, rowValue) {
  if (!fv || !fv.startsWith('>=')) return true;
  return Number(rowValue) >= Number(fv.slice(2));
}

function filterPredicateAge(fv, _row, rowValue) {
  if (!fv || !fv.startsWith('age<=')) return true;
  const days = Number(fv.slice(5));
  if (!Number.isFinite(days) || !rowValue) return false;
  return (Date.now() - Number(rowValue)) <= days * 86400000;
}

function renderTopPosts(payload) {
  const totalEl = document.getElementById('top-total');
  const container = document.getElementById('top-table-container');
  if (!container) return;
  if (payload && payload.error) {
    container.innerHTML = '<div class="style-stats-empty">' + escapeHtml(payload.error) + '</div>';
    if (totalEl) totalEl.textContent = '';
    _topTableHandle = null;
    return;
  }
  _topPostsPayload = payload;
  const allPosts = (payload && payload.posts) || [];
  refreshTopProjectPills(allPosts.map(p => p.project_name).filter(Boolean));
  const posts = (_topProject && _topProject !== 'all')
    ? allPosts.filter(p => (p.project_name || '') === _topProject)
    : allPosts;
  if (totalEl) totalEl.textContent = posts.length + ' post' + (posts.length === 1 ? '' : 's');
  if (!posts.length) {
    container.innerHTML = '<div class="style-stats-empty">No posts with engagement yet.</div>';
    _topTableHandle = null;
    return;
  }
  const fmt = n => (Number(n) || 0).toLocaleString();
  const normalized = posts.map(p => ({
    id:            p.id,
    platform:      String(p.platform || '').toLowerCase(),
    upvotes:       Number(p.upvotes)        || 0,
    comments_count:Number(p.comments_count) || 0,
    views:         p.views == null ? null : Number(p.views),
    score:         Number(p.score)          || 0,
    is_thread:     !!p.is_thread,
    posted_at:     p.posted_at || null,
    posted_ts:     p.posted_at ? new Date(p.posted_at).getTime() : 0,
    engagement_updated_at: p.engagement_updated_at || null,
    engagement_ts: p.engagement_updated_at ? new Date(p.engagement_updated_at).getTime() : 0,
    our_content:   p.our_content || '',
    our_url:       p.our_url || '',
    thread_url:    p.thread_url || '',
    thread_title:  p.thread_title || '',
    thread_content:p.thread_content || '',
    our_account:   p.our_account || '',
    project_name:  p.project_name || '',
    is_recommendation: !!p.is_recommendation,
  }));
  _topTableHandle = mountSortableTable({
    containerId: 'top-table-container',
    rows: normalized,
    state: _topTableState,
    inlineFilters: true,
    columns: [
      { key: 'platform',       label: 'Platform', type: 'text',    align: 'left',  widthPct: 6,
        formatter: v => platformIconHtml(v),
        filterMode: 'dropdown',
        filterOptions: distinctOptions(normalized, 'platform', 'All'),
        filterPredicate: filterPredicateExact },
      { key: 'project_name',   label: 'Project',  type: 'text',    align: 'left',  widthPct: 12,
        formatter: (v, r) => {
          const name = v ? escapeHtml(String(v)) : '';
          const kind = r.is_thread ? 'thread' : 'comment';
          const pill = '<span class="top-kind-pill top-kind-pill--' + kind + '">' + kind + '</span>';
          const recPill = r.is_recommendation
            ? '<span class="top-kind-pill top-kind-pill--rec" title="flagged as project recommendation (is_recommendation=true)">rec</span>'
            : '';
          return '<div class="top-project-cell">' + (name ? '<div class="top-project-name">' + name + '</div>' : '') + pill + recPill + '</div>';
        },
        filterMode: 'dropdown',
        filterOptions: distinctOptions(normalized, 'project_name', 'All'),
        filterPredicate: filterPredicateExact },
      { key: 'score',          label: 'Stats',    type: 'numeric', align: 'left',  widthPct: 18,
        formatter: (_v, r) => {
          const parts = [
            '<span class="top-stats-bit"><span class="top-stats-k">score</span>' + fmt(r.score) + '</span>',
            '<span class="top-stats-bit"><span class="top-stats-k">upvotes</span>' + fmt(r.upvotes) + '</span>',
            '<span class="top-stats-bit"><span class="top-stats-k">comments</span>' + fmt(r.comments_count) + '</span>',
            '<span class="top-stats-bit"><span class="top-stats-k">views</span>' + (r.views == null ? '\u2014' : fmt(r.views)) + '</span>',
          ];
          return '<div class="top-stats-cell">' + parts.join('') + '</div>';
        },
        filterMode: 'dropdown',
        filterOptions: numericThresholdOptions(normalized, 'score'),
        filterPredicate: filterPredicateGte },
      { key: 'posted_ts',      label: 'Posted',   type: 'numeric', align: 'right', widthPct: 6,
        formatter: (_v, r) => {
          const abs = r.posted_at ? new Date(r.posted_at).toLocaleString() : '';
          return '<span title="' + escapeHtml(abs) + '">' + escapeHtml(relTime(r.posted_at)) + '</span>';
        },
        filterMode: 'dropdown',
        filterOptions: ageThresholdOptions(),
        filterPredicate: filterPredicateAge },
      { key: 'our_content',    label: 'Content',  type: 'text',    align: 'left',  widthPct: 58,
        formatter: renderTopContentCell,
        filterMode: 'none' },
    ],
  });
}

async function loadTopPosts(force) {
  if (_topLoading) return;
  if (_topLoaded && !force) return;
  _topLoading = true;
  try {
    const params = new URLSearchParams({ limit: '200', window: _topWindow });
    if (_topPlatform && _topPlatform !== 'all') params.set('platform', _topPlatform);
    if (_topSubtab === 'threads' || _topSubtab === 'comments') params.set('kind', _topSubtab);
    const container = document.getElementById('top-table-container');
    if (container && force) container.innerHTML = '<div class="style-stats-empty">Loading\u2026</div>';
    const res = await fetch('/api/top?' + params.toString());
    const data = await res.json();
    renderTopPosts(data);
    _topLoaded = true;
  } catch (e) {
    const container = document.getElementById('top-table-container');
    if (container) container.innerHTML = '<div class="style-stats-empty">Failed to load.</div>';
  } finally {
    _topLoading = false;
  }
}

function setTopPillActive(row, value) {
  if (!row) return;
  row.dataset.selected = value || 'all';
  row.querySelectorAll('.style-stats-pill').forEach(btn => {
    btn.classList.toggle('active', (btn.getAttribute('data-value') || '') === (value || 'all'));
  });
}

const TOP_SUBTAB_HELP = {
  threads: 'Top original posts/threads your accounts have published, ranked by reach and reactions.',
  comments: 'Top comments your accounts have left under other people’s threads, ranked by reach and reactions.',
  pages: 'Top landing/SEO pages on your sites this period, ranked by pageviews.',
  dms: 'Direct message conversations with prospects, ranked by recent activity.',
};
function syncTopSubtabHelp() {
  const el = document.getElementById('top-subtab-help');
  if (!el) return;
  el.textContent = TOP_SUBTAB_HELP[_topSubtab] || '';
}

function wireTopPillRow(rowId, onSelect) {
  const row = document.getElementById(rowId);
  if (!row || row._wired) return;
  row.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.style-stats-pill');
    if (!btn || !row.contains(btn)) return;
    const value = btn.getAttribute('data-value') || 'all';
    if ((row.dataset.selected || 'all') === value) return;
    setTopPillActive(row, value);
    onSelect(value);
  });
  row._wired = true;
}

function initTopFilters() {
  const winRow  = document.getElementById('top-window-pills');
  const platRow = document.getElementById('top-platform-pills');
  const projRow = document.getElementById('top-project-pills');
  const srcRow  = document.getElementById('top-pages-source-pills');
  const dirRow  = document.getElementById('top-dm-dir-pills');
  const intRow  = document.getElementById('top-dm-interest-pills');
  const modeRow = document.getElementById('top-dm-mode-pills');
  const tierRow = document.getElementById('top-dm-tier-pills');
  const qualRow = document.getElementById('top-dm-qual-pills');
  const statRow = document.getElementById('top-dm-status-pills');
  if (winRow) setTopPillActive(winRow, _topWindow);
  if (platRow) setTopPillActive(platRow, _topPlatform);
  if (projRow) setTopPillActive(projRow, _topProject);
  if (srcRow) setTopPillActive(srcRow, _topPagesSource);
  if (dirRow) setTopPillActive(dirRow, _topDmDir);
  if (intRow) setTopPillActive(intRow, _topDmInterest);
  if (modeRow) setTopPillActive(modeRow, _topDmMode);
  if (tierRow) setTopPillActive(tierRow, _topDmTier);
  if (qualRow) setTopPillActive(qualRow, _topDmQual);
  if (statRow) setTopPillActive(statRow, _topDmStatus);
  wireTopPillRow('top-window-pills', (v) => {
    _topWindow = coerceTopWindow(v);
    saveDashboardWindow(_topWindow);
    if (_topSubtab === 'pages') loadTopPages(true);
    else if (_topSubtab === 'dms') { _topDmOffset = 0; loadTopDms(true); }
    else loadTopPosts(true);
  });
  wireTopPillRow('top-platform-pills', (v) => {
    _topPlatform = v || 'all';
    if (_topSubtab === 'dms') { _topDmOffset = 0; loadTopDms(true); }
    else loadTopPosts(true);
  });
  wireTopPillRow('top-project-pills', (v) => {
    _topProject = v || 'all';
    if (_topSubtab === 'pages') renderTopPagesFromCache();
    else if (_topSubtab === 'dms') { if (_topDmsPayload) renderTopDms(_topDmsPayload); }
    else { if (_topPostsPayload) renderTopPosts(_topPostsPayload); }
  });
  wireTopPillRow('top-pages-source-pills', (v) => {
    _topPagesSource = v || 'seo';
    renderTopPagesFromCache();
  });
  wireTopPillRow('top-dm-dir-pills', (v) => {
    _topDmDir = v || 'all';
    if (_topDmsPayload) renderTopDms(_topDmsPayload);
  });
  wireTopPillRow('top-dm-interest-pills', (v) => {
    _topDmInterest = v || 'all';
    if (_topDmsPayload) renderTopDms(_topDmsPayload);
  });
  wireTopPillRow('top-dm-mode-pills', (v) => {
    _topDmMode = v || 'all';
    if (_topDmsPayload) renderTopDms(_topDmsPayload);
  });
  wireTopPillRow('top-dm-tier-pills', (v) => {
    _topDmTier = v || 'all';
    if (_topDmsPayload) renderTopDms(_topDmsPayload);
  });
  wireTopPillRow('top-dm-qual-pills', (v) => {
    _topDmQual = v || 'all';
    if (_topDmsPayload) renderTopDms(_topDmsPayload);
  });
  wireTopPillRow('top-dm-status-pills', (v) => {
    _topDmStatus = v || 'all';
    if (_topDmsPayload) renderTopDms(_topDmsPayload);
  });
  const searchEl = document.getElementById('top-search');
  if (searchEl && !searchEl._wired) {
    searchEl.value = _topSubtab === 'dms' ? _topDmSearch : (_topTableState.globalQuery || '');
    searchEl.addEventListener('input', () => {
      if (_topSubtab === 'dms') {
        // Server-side search across all DMs (drops the time-window filter on
        // the API). Debounce so we don't fire a query on every keystroke.
        _topDmSearch = searchEl.value || '';
        _topDmOffset = 0;
        if (_topDmSearchTimer) clearTimeout(_topDmSearchTimer);
        _topDmSearchTimer = setTimeout(() => loadTopDms(true), 300);
      } else {
        _topTableState.globalQuery = searchEl.value;
        if (_topTableHandle && _topTableHandle.apply) _topTableHandle.apply();
      }
    });
    searchEl._wired = true;
  }
  syncTopSubtabHelp();
  document.querySelectorAll('.top-subtab').forEach(el => {
    if (el._wired) return;
    el.addEventListener('click', () => {
      const sub = el.dataset.subtab || 'threads';
      if (sub === _topSubtab) return;
      _topSubtab = sub;
      document.querySelectorAll('.top-subtab').forEach(s => {
        const isActive = s.dataset.subtab === sub;
        s.classList.toggle('active', isActive);
        s.setAttribute('aria-selected', isActive ? 'true' : 'false');
      });
      syncTopSubtabHelp();
      const postsC = document.getElementById('top-table-container');
      const pagesC = document.getElementById('top-pages-container');
      const pagesUnknownC = document.getElementById('top-pages-unknown-container');
      const dmsC   = document.getElementById('top-dms-container');
      const platRowEl = document.getElementById('top-platform-pills');
      const projRowEl = document.getElementById('top-project-pills');
      const srcRowEl  = document.getElementById('top-pages-source-pills');
      const dirRowEl  = document.getElementById('top-dm-dir-pills');
      const dmOnlyRowIds = ['top-dm-dir-pills', 'top-dm-interest-pills', 'top-dm-mode-pills', 'top-dm-tier-pills', 'top-dm-qual-pills', 'top-dm-status-pills'];
      const setDmRowsHidden = (hidden) => {
        dmOnlyRowIds.forEach(id => {
          const el = document.getElementById(id);
          if (el) el.classList.toggle('hidden', hidden);
        });
      };
      const totalEl = document.getElementById('top-total');
      if (projRowEl) projRowEl.classList.remove('hidden');
      if (sub === 'pages') {
        postsC.classList.add('hidden');
        if (dmsC) dmsC.classList.add('hidden');
        pagesC.classList.remove('hidden');
        if (pagesUnknownC) pagesUnknownC.classList.remove('hidden');
        if (platRowEl) platRowEl.classList.add('hidden');
        if (srcRowEl) srcRowEl.classList.remove('hidden');
        setDmRowsHidden(true);
        if (totalEl) totalEl.textContent = '';
        loadTopPages();
      } else if (sub === 'dms') {
        postsC.classList.add('hidden');
        pagesC.classList.add('hidden');
        if (pagesUnknownC) pagesUnknownC.classList.add('hidden');
        if (dmsC) dmsC.classList.remove('hidden');
        if (platRowEl) platRowEl.classList.remove('hidden');
        if (srcRowEl) srcRowEl.classList.add('hidden');
        setDmRowsHidden(false);
        if (totalEl) totalEl.textContent = '';
        const searchElDm = document.getElementById('top-search');
        if (searchElDm) {
          searchElDm.placeholder = 'Search DMs by author or message…';
          searchElDm.value = _topDmSearch || '';
        }
        loadTopDms(true);
      } else {
        pagesC.classList.add('hidden');
        if (pagesUnknownC) pagesUnknownC.classList.add('hidden');
        if (dmsC) dmsC.classList.add('hidden');
        postsC.classList.remove('hidden');
        if (platRowEl) platRowEl.classList.remove('hidden');
        if (srcRowEl) srcRowEl.classList.add('hidden');
        setDmRowsHidden(true);
        if (totalEl) totalEl.textContent = '';
        const searchElPosts = document.getElementById('top-search');
        if (searchElPosts) {
          searchElPosts.placeholder = 'Search posts…';
          searchElPosts.value = _topTableState.globalQuery || '';
        }
        loadTopPosts(true);
      }
    });
    el._wired = true;
  });
}

const _TOP_PAGES_WINDOW_DAYS = { '24h': 1, '7d': 7, '14d': 14, '30d': 30, '90d': 90, 'all': 90 };
let _topPagesPayload = null;

function renderTopPagesFromCache() {
  if (_topPagesPayload) renderTopPages(_topPagesPayload);
}

function renderTopPages(payload) {
  const container = document.getElementById('top-pages-container');
  const unknownContainer = document.getElementById('top-pages-unknown-container');
  const totalEl = document.getElementById('top-total');
  if (!container) return;
  if (payload && payload.error) {
    container.innerHTML = '<div class="style-stats-empty">' + escapeHtml(payload.error) + '</div>';
    if (unknownContainer) unknownContainer.innerHTML = '';
    if (totalEl) totalEl.textContent = '';
    return;
  }
  const projects = (payload && payload.projects) || [];
  refreshTopProjectPills(projects.map(p => p.name).filter(Boolean));
  const fmt = n => (Number(n) || 0).toLocaleString();
  const normPath = (p) => {
    let s = String(p || '/');
    if (!s.startsWith('/')) s = '/' + s;
    while (s.length > 1 && s.endsWith('/')) s = s.slice(0, -1);
    return s;
  };
  const createdRows = [];
  const unknownRows = [];
  for (const p of projects) {
    if (_topProject && _topProject !== 'all' && p.name !== _topProject) continue;
    const details = (p.posthog && p.posthog.pageview_details) || {};
    const projectBookings = Number(p.funnel && p.funnel.real_bookings) || 0;
    for (const domain of Object.keys(details)) {
      const d = details[domain] || {};
      const top = d.top_pages || {};
      const signupsByPath = d.top_pages_signups || {};
      const schedByPath = d.top_pages_schedule || {};
      const getStartedByPath = d.top_pages_get_started || {};
      const created = new Set((d.created_paths || []).map(normPath));
      const seenPaths = new Set([
        ...Object.keys(top),
        ...Object.keys(signupsByPath),
        ...Object.keys(schedByPath),
        ...Object.keys(getStartedByPath),
      ].map(normPath));
      const mkRow = (path) => {
        const pv = Number(top[path]) || 0;
        const signups = Number(signupsByPath[path]) || 0;
        const sched = Number(schedByPath[path]) || 0;
        const dl = Number(getStartedByPath[path]) || 0;
        const url = 'https://' + domain + path;
        return {
          project: p.name || '',
          domain: domain || '',
          path,
          url,
          pageviews: pv,
          email_signups: signups,
          schedule_clicks: sched,
          get_started_clicks: dl,
          bookings: projectBookings,
        };
      };
      for (const path of created) createdRows.push(mkRow(path));
      for (const path of seenPaths) {
        if (created.has(path)) continue;
        const pv = Number(top[path]) || 0;
        const signups = Number(signupsByPath[path]) || 0;
        const sched = Number(schedByPath[path]) || 0;
        const dl = Number(getStartedByPath[path]) || 0;
        if (pv <= 0 && signups <= 0 && sched <= 0 && dl <= 0) continue;
        unknownRows.push(mkRow(path));
      }
    }
  }
  const showAll = _topPagesSource === 'all';
  const mainRows = showAll ? createdRows.concat(unknownRows) : createdRows;
  if (totalEl) {
    const totalPv = mainRows.reduce((a, r) => a + r.pageviews, 0);
    totalEl.textContent = mainRows.length + ' page' + (mainRows.length === 1 ? '' : 's') + '\u00b7 ' + fmt(totalPv) + ' pv';
  }
  const humanizeSlug = (pth) => {
    let raw = String(pth || '/').split('?')[0].split('#')[0];
    while (raw.length > 1 && raw.endsWith('/')) raw = raw.slice(0, -1);
    const segs = raw.split('/').filter(Boolean);
    const slug = segs.length ? segs[segs.length - 1] : '';
    if (!slug) return 'Home';
    return slug.replace(/[-_]+/g, ' ').split(' ').map(w => w ? w[0].toUpperCase() + w.slice(1) : w).join(' ');
  };
  const fmtContent = (_v, r) => {
    const url = escapeHtml(r.url);
    const header = escapeHtml(humanizeSlug(r.path));
    return '<a href="' + url + '" target="_blank" rel="noopener" class="top-post-link">'
      + '<div class="top-pages-header">' + header + '</div>'
      + '<div class="top-pages-url">(' + url + ')</div>'
      + '</a>';
  };
  const columns = [
    { key: 'project',         label: 'Project',         type: 'text',    align: 'left',  widthPct: 12, formatter: v => escapeHtml(v) },
    { key: 'path',            label: 'Content',         type: 'text',    align: 'left',  widthPct: 42, formatter: fmtContent },
    { key: 'pageviews',       label: 'Pageviews',       type: 'numeric', align: 'right', widthPct: 9,  formatter: fmt },
    { key: 'email_signups',   label: 'Email Signups',   type: 'numeric', align: 'right', widthPct: 9,  formatter: fmt },
    { key: 'schedule_clicks', label: 'Schedule Clicks', type: 'numeric', align: 'right', widthPct: 9,  formatter: fmt },
    { key: 'get_started_clicks', label: 'Get Started', type: 'numeric', align: 'right', widthPct: 9,  formatter: fmt },
    { key: 'bookings',        label: 'Bookings',        type: 'numeric', align: 'right', widthPct: 8,  formatter: fmt },
  ];
  if (!mainRows.length) {
    container.innerHTML = '<div class="style-stats-empty">No pages found in this project.</div>';
  } else {
    mountSortableTable({
      containerId: 'top-pages-container',
      rows: mainRows,
      state: _topPagesTableState,
      columns,
    });
  }
  if (unknownContainer) {
    if (showAll || !unknownRows.length) {
      unknownContainer.innerHTML = '';
    } else {
      const unkPv = unknownRows.reduce((a, r) => a + r.pageviews, 0);
      unknownContainer.innerHTML =
        '<details class="style-stats-section" style="margin-top:16px;">'
        + '<summary>'
        + '<span class="style-stats-title"><span class="style-stats-caret">\u25B6</span>Unknown / 404 inbound traffic</span>'
        + '<span class="style-stats-total">' + unknownRows.length + ' path' + (unknownRows.length === 1 ? '' : 's') + ' \u00b7 ' + fmt(unkPv) + ' pv</span>'
        + '</summary>'
        + '<div id="top-pages-unknown-table"></div>'
        + '</details>';
      mountSortableTable({
        containerId: 'top-pages-unknown-table',
        rows: unknownRows,
        state: { sortField: 'pageviews', sortDir: 'desc', filters: {} },
        columns,
      });
    }
  }
}

async function loadTopPages(force) {
  if (_topPagesLoading) return;
  const days = _TOP_PAGES_WINDOW_DAYS[_topWindow] || 7;
  const container = document.getElementById('top-pages-container');
  if (!_topPagesPayload && container) {
    container.innerHTML = '<div class="style-stats-empty">Loading\u2026 (first call can take 15\u201330s)</div>';
  }
  _topPagesLoading = true;
  try {
    const res = await fetch('/api/funnel/stats?days=' + days);
    const data = await res.json();
    _topPagesPayload = data;
    renderTopPages(data);
    _topPagesLoaded = true;
  } catch (e) {
    if (container) container.innerHTML = '<div class="style-stats-empty">Failed to load.</div>';
  } finally {
    _topPagesLoading = false;
  }
}

function dmClassBadge(dm) {
  const status = String(dm.conversation_status || '').toLowerCase();
  const interest = String(dm.interest_level || '').toLowerCase();
  let cls = 'dm-class-none';
  let label = 'unclassified';
  if (status === 'needs_human') { cls = 'dm-class-human'; label = 'HUMAN'; }
  else if (status === 'converted') { cls = 'dm-class-converted'; label = 'converted'; }
  else if (status === 'closed')    { cls = 'dm-class-closed';    label = 'closed'; }
  else if (interest === 'hot')                { cls = 'dm-class-hot';      label = 'hot'; }
  else if (interest === 'warm')               { cls = 'dm-class-warm';     label = 'warm'; }
  else if (interest === 'general_discussion') { cls = 'dm-class-general';  label = 'general'; }
  else if (interest === 'cold')               { cls = 'dm-class-cold';     label = 'cold'; }
  else if (interest === 'declined')           { cls = 'dm-class-declined'; label = 'declined'; }
  else if (interest === 'not_our_prospect')   { cls = 'dm-class-notours';  label = 'not ours'; }
  else if (status) { label = status; }
  const badge = '<span class="dm-class-badge ' + cls + '">' + escapeHtml(label) + '</span>';
  const subBits = [];
  if (status && status !== 'needs_human' && status !== 'converted' && status !== 'closed' && status !== 'active') {
    subBits.push(status);
  }
  if (status === 'needs_human' && dm.human_reason) {
    subBits.push(String(dm.human_reason).slice(0, 40));
  }
  const sub = subBits.length ? '<div class="dm-class-sub">' + escapeHtml(subBits.join(' \u00b7 ')) + '</div>' : '';
  const extras = [];
  const matches = Array.isArray(dm.icp_matches) ? dm.icp_matches : [];
  const target = dm.target_project || null;
  let primaryLabel = null;
  if (target) {
    const hit = matches.find(m => m && m.project === target);
    if (hit && hit.label) primaryLabel = hit.label;
  }
  if (!primaryLabel && dm.icp_precheck) primaryLabel = String(dm.icp_precheck);
  if (primaryLabel) {
    const icpCls = 'dm-icp-' + primaryLabel.replace(/[^a-z_]/gi, '');
    const matchTitle = matches.length
      ? matches.map(m => String(m.project) + ': ' + String(m.label)).join('\\n')
      : '';
    const extra = (matches.length > 1) ? ' +' + (matches.length - 1) : '';
    extras.push('<span class="dm-meta-chip ' + icpCls + '" title="' + escapeHtml(matchTitle) + '">icp: ' + escapeHtml(primaryLabel) + extra + '</span>');
  }
  if (dm.qualification_status && dm.qualification_status !== 'pending') {
    const qCls = 'dm-qual-' + String(dm.qualification_status).replace(/[^a-z_]/gi, '');
    extras.push('<span class="dm-meta-chip ' + qCls + '">qual: ' + escapeHtml(String(dm.qualification_status)) + '</span>');
  }
  let qNote = '';
  if (dm.qualification_notes) {
    const n = String(dm.qualification_notes);
    qNote = '<div class="dm-qual-note" title="' + escapeHtml(n) + '">' + escapeHtml(n.length > 90 ? n.slice(0, 90) + '\u2026' : n) + '</div>';
  }
  const extrasHtml = extras.length ? '<div class="dm-meta-row">' + extras.join(' ') + '</div>' : '';
  return badge + sub + extrasHtml + qNote;
}

function __closeProspect() {
  const ov = document.getElementById('prospect-modal-overlay');
  if (ov) ov.remove();
}
function __showProspect(dmId) {
  const dm = (window.__dmsById || {})[dmId];
  if (!dm) return;
  __closeProspect();
  const row = (label, val) => {
    if (val === null || val === undefined || val === '') return '';
    return '<div class="prospect-row"><div class="prospect-label">' + escapeHtml(label) + '</div><div class="prospect-val">' + escapeHtml(String(val)) + '</div></div>';
  };
  const urlRow = (label, url) => {
    if (!url) return '';
    const safe = escapeHtml(url);
    return '<div class="prospect-row"><div class="prospect-label">' + escapeHtml(label) + '</div><div class="prospect-val"><a href="' + safe + '" target="_blank" rel="noopener">' + safe + '</a></div></div>';
  };
  const fetchedRel = dm.prospect_fetched_at ? relTime(dm.prospect_fetched_at) : '';
  const followers = (dm.prospect_follower_count !== null && dm.prospect_follower_count !== undefined && dm.prospect_follower_count !== '') ? Number(dm.prospect_follower_count).toLocaleString() : '';
  const subParts = [];
  if (dm.platform) subParts.push(String(dm.platform));
  if (dm.their_author) subParts.push('@' + String(dm.their_author));
  if (fetchedRel) subParts.push('profile fetched ' + fetchedRel);
  const html =
    '<div id="prospect-modal-overlay" class="prospect-modal-overlay" onclick="if(event.target===this)__closeProspect()">' +
      '<div class="prospect-modal" role="dialog" aria-modal="true">' +
        '<button class="prospect-close" type="button" onclick="__closeProspect()">close</button>' +
        '<h3>' + escapeHtml(dm.prospect_headline || dm.prospect_role || dm.their_author || 'Prospect') + '</h3>' +
        '<div class="prospect-sub">' + escapeHtml(subParts.join(' \u00b7 ')) + '</div>' +
        row('Company', dm.prospect_company) +
        row('Role', dm.prospect_role) +
        row('Headline', dm.prospect_headline) +
        row('Bio', dm.prospect_bio) +
        row('Followers', followers) +
        row('Recent activity', dm.prospect_recent_activity) +
        row('Notes', dm.prospect_notes) +
        row('Target project', dm.target_project || dm.project_name) +
        row('ICP pre-check', dm.icp_precheck) +
        row('Qualification status', dm.qualification_status) +
        row('Qualification notes', dm.qualification_notes) +
        urlRow('Profile URL', dm.prospect_profile_url) +
        urlRow('Chat URL', dm.chat_url) +
      '</div>' +
    '</div>';
  document.body.insertAdjacentHTML('beforeend', html);
}
window.__showProspect = __showProspect;
window.__closeProspect = __closeProspect;

function dmOpenUrl(dm) {
  const raw = String((dm && dm.chat_url) || '').trim();
  if (!raw) return null;
  const p = String((dm && dm.platform) || '').toLowerCase();
  if (p === 'reddit') {
    if (raw.indexOf('/chat/room/') !== -1) return { url: raw, label: 'open chat' };
    if (raw.indexOf('/message/messages/') !== -1) return { url: raw, label: 'open DM' };
    return null;
  }
  if (p === 'twitter' || p === 'x') {
    if (raw.indexOf('/i/chat/') !== -1 || raw.indexOf('/messages/') !== -1) return { url: raw, label: 'open chat' };
    return null;
  }
  if (p === 'linkedin') {
    if (raw.indexOf('/messaging/thread/') !== -1) return { url: raw, label: 'open chat' };
    return null;
  }
  return null;
}

function renderDmThreadCell(dm) {
  const author = escapeHtml(dm.their_author || '');
  const tier = dm.tier ? '<span class="dm-thread-tier">T' + Number(dm.tier) + '</span>' : '';
  const linkInfo = dmOpenUrl(dm);
  const url = linkInfo ? escapeHtml(linkInfo.url) : '';
  const nameHtml = url
    ? '<a class="dm-thread-author top-post-link" href="' + url + '" target="_blank" rel="noopener">' + author + '</a>'
    : '<span class="dm-thread-author">' + author + '</span>';
  let pillHtml = '';
  const hasProspect = dm.prospect_headline || dm.prospect_company || dm.prospect_role || dm.prospect_bio || dm.prospect_recent_activity || dm.prospect_notes;
  if (hasProspect) {
    const headlineRaw = String(dm.prospect_headline || dm.prospect_role || dm.prospect_company || dm.prospect_notes || 'view profile').trim();
    const pillText = headlineRaw.length > 48 ? headlineRaw.slice(0, 48) + '\u2026' : headlineRaw;
    pillHtml = '<button class="dm-prospect-pill" type="button" onclick="__showProspect(' + Number(dm.id) + ')" title="' + escapeHtml(headlineRaw) + '">' + escapeHtml(pillText) + '</button>';
  }
  return '<div class="top-post-content">' + nameHtml + (tier ? ' ' + tier : '') + (pillHtml ? '<div class="dm-thread-subline">' + pillHtml + '</div>' : '') + '</div>';
}

function renderDmLastMsgCell(dm) {
  const msg = String(dm.last_msg || '');
  if (!msg) return '<span style="color:var(--text-faint);">(no messages)</span>';
  const trimmed = msg.length > 300 ? msg.slice(0, 300) + '\u2026' : msg;
  const dirLabel = dm.last_dir === 'inbound' ? 'IN' : (dm.last_dir === 'outbound' ? 'OUT' : '');
  const dirHtml = dirLabel ? '<span class="dm-last-dir">' + dirLabel + '</span>' : '';
  return dirHtml + escapeHtml(trimmed);
}

function renderTopDms(payload) {
  const totalEl = document.getElementById('top-total');
  const container = document.getElementById('top-dms-container');
  if (!container) return;
  if (payload && payload.error) {
    container.innerHTML = '<div class="style-stats-empty">' + escapeHtml(payload.error) + '</div>';
    if (totalEl) totalEl.textContent = '';
    return;
  }
  const allDms = (payload && payload.dms) || [];
  const dmProjectName = d => d.target_project || d.project_name || '';
  refreshTopProjectPills(allDms.map(dmProjectName).filter(Boolean));
  const projectScoped = (_topProject && _topProject !== 'all')
    ? allDms.filter(d => dmProjectName(d) === _topProject)
    : allDms;
  const dirScoped = _topDmDir === 'in'
    ? projectScoped.filter(d => d.last_dir === 'inbound')
    : (_topDmDir === 'out'
      ? projectScoped.filter(d => d.last_dir === 'outbound')
      : projectScoped);
  const dms = dirScoped.filter(d => {
    if (_topDmInterest !== 'all' && (d.interest_level || '') !== _topDmInterest) return false;
    if (_topDmMode !== 'all' && (d.mode || 'rapport') !== _topDmMode) return false;
    if (_topDmTier !== 'all' && String(Number(d.tier) || 1) !== _topDmTier) return false;
    if (_topDmQual !== 'all' && (d.qualification_status || '') !== _topDmQual) return false;
    if (_topDmStatus !== 'all' && (d.conversation_status || '') !== _topDmStatus) return false;
    return true;
  });
  const serverTotal = (payload && Number.isFinite(Number(payload.total))) ? Number(payload.total) : null;
  const isLookup = !!(payload && payload.lookup);
  const loadedCount = allDms.length;
  if (totalEl) {
    const suffix = _topDmDir === 'in' ? ' (IN)' : (_topDmDir === 'out' ? ' (OUT)' : '');
    const filteredNote = (dms.length !== loadedCount)
      ? (' / ' + dms.length + ' shown')
      : '';
    if (serverTotal != null && serverTotal > loadedCount) {
      totalEl.textContent = loadedCount + ' of ' + serverTotal + ' threads loaded' + filteredNote + suffix;
    } else {
      totalEl.textContent = loadedCount + ' thread' + (loadedCount === 1 ? '' : 's') + filteredNote + suffix;
    }
  }
  if (!dms.length) {
    const emptyMsg = _topDmDir === 'in'
      ? 'No threads where the last message was inbound.'
      : (_topDmDir === 'out'
        ? 'No threads where the last message was outbound.'
        : 'No DM threads in this window.');
    container.innerHTML = '<div class="style-stats-empty">' + emptyMsg + '</div>';
    return;
  }
  const fmt = n => (Number(n) || 0).toLocaleString();
  const normalized = dms.map((d, i) => ({
    id: d.id,
    rank: i,
    platform: String(d.platform || '').toLowerCase(),
    their_author: d.their_author || '',
    chat_url: d.chat_url || '',
    tier: Number(d.tier) || 1,
    message_count: Number(d.message_count) || 0,
    last_message_at: d.last_message_at || d.discovered_at || null,
    last_ts: (() => { const p = parseServerUtcTs(d.last_message_at || d.discovered_at); return p ? p.getTime() : 0; })(),
    conversation_status: d.conversation_status || '',
    interest_level: d.interest_level || '',
    mode: d.mode || 'rapport',
    human_reason: d.human_reason || '',
    project_name: d.project_name || '',
    target_project: d.target_project || '',
    project_display: d.target_project || d.project_name || '',
    icp_precheck: d.icp_precheck || '',
    icp_matches: Array.isArray(d.icp_matches) ? d.icp_matches : [],
    qualification_status: d.qualification_status || '',
    qualification_notes: d.qualification_notes || '',
    booking_link_sent_at: d.booking_link_sent_at || null,
    short_link_code: d.short_link_code || '',
    short_link_clicks: Number(d.short_link_clicks) || 0,
    short_link_first_click_at: d.short_link_first_click_at || null,
    short_link_last_click_at: d.short_link_last_click_at || null,
    bookings_count: Number(d.bookings_count) || 0,
    bookings_booked: Number(d.bookings_booked) || 0,
    bookings_cancelled: Number(d.bookings_cancelled) || 0,
    last_booking_at: d.last_booking_at || null,
    recent_bookings: Array.isArray(d.recent_bookings) ? d.recent_bookings : [],
    prospect_headline: d.prospect_headline || '',
    prospect_bio: d.prospect_bio || '',
    prospect_company: d.prospect_company || '',
    prospect_role: d.prospect_role || '',
    prospect_follower_count: d.prospect_follower_count,
    prospect_recent_activity: d.prospect_recent_activity || '',
    prospect_notes: d.prospect_notes || '',
    prospect_profile_url: d.prospect_profile_url || '',
    prospect_fetched_at: d.prospect_fetched_at || null,
    last_msg: d.last_msg || '',
    last_dir: d.last_dir || '',
    messages: Array.isArray(d.messages) ? d.messages : [],
    // Escalation card needs this whole array (instructions text + agent's
    // generated reply per item) so it can render the paired blocks. Was
    // missing here, which is why the escalation card never showed any
    // history despite the SQL aggregating it correctly.
    human_instructions: Array.isArray(d.human_instructions) ? d.human_instructions : [],
    flagged_at: d.flagged_at || null,
  }));
  window.__dmsById = Object.create(null);
  for (const r of normalized) { window.__dmsById[r.id] = r; }
  window.__dmExpandedIds = Object.create(null);
  const columns = [
    { key: 'rank',           label: '#',        type: 'numeric', align: 'right', widthPct: 3,
      formatter: v => '<span style="color:var(--text-muted);">' + (Number(v) + 1) + '</span>' },
    { key: 'platform',       label: 'Platform', type: 'text',    align: 'left',  widthPct: 5,
      formatter: v => platformIconHtml(v) },
    { key: 'project_display', label: 'Project', type: 'text',    align: 'left',  widthPct: 9,
      formatter: (_v, r) => r.project_display ? escapeHtml(PROJECT_LABELS[String(r.project_display)] || String(r.project_display)) : '<span style="color:var(--text-faint);">\u2014</span>' },
    { key: 'their_author',   label: 'Thread',   type: 'text',    align: 'left',  widthPct: 13,
      formatter: (_v, r) => renderDmThreadCell(r) },
    { key: 'last_msg',       label: 'Last message', type: 'text', align: 'left', widthPct: 32,
      formatter: (_v, r) => renderDmLastMsgCell(r) },
    { key: 'message_count',  label: 'Msgs',     type: 'numeric', align: 'right', widthPct: 5,  formatter: fmt },
    { key: 'short_link_clicks', label: 'Clicks', type: 'numeric', align: 'right', widthPct: 5,
      formatter: (v, r) => {
        const n = Number(v) || 0;
        if (!r.short_link_code) return '<span style="color:var(--text-faint);">—</span>';
        const code = String(r.short_link_code);
        const lastAt = r.short_link_last_click_at ? new Date(r.short_link_last_click_at).toLocaleString() : 'never';
        const tip = '/r/' + code + (n ? (' • last click: ' + lastAt) : ' • no clicks yet');
        const color = n > 0 ? 'var(--accent)' : 'var(--text-muted)';
        return '<span data-tooltip="' + escapeHtml(tip) + '" style="color:' + color + ';font-variant-numeric:tabular-nums;">' + fmt(n) + '</span>';
      } },
    { key: 'bookings_count', label: 'Booked', type: 'numeric', align: 'right', widthPct: 5,
      formatter: (v, r) => {
        const total = Number(v) || 0;
        if (!total) return '<span style="color:var(--text-faint);">—</span>';
        const booked = Number(r.bookings_booked) || 0;
        const cancelled = Number(r.bookings_cancelled) || 0;
        const lastAt = r.last_booking_at ? new Date(r.last_booking_at).toLocaleString() : '';
        const tip = booked + ' booked' + (cancelled ? (' • ' + cancelled + ' cancelled') : '') + (lastAt ? (' • last: ' + lastAt) : '');
        return '<span data-tooltip="' + escapeHtml(tip) + '" style="color:var(--success);font-weight:600;font-variant-numeric:tabular-nums;">' + fmt(booked) + '</span>';
      } },
    { key: 'interest_level', label: 'Class',    type: 'text',    align: 'left',  widthPct: 11,
      formatter: (_v, r) => dmClassBadge(r) },
    { key: 'last_ts',        label: 'Last message', type: 'numeric', align: 'right', widthPct: 10,
      formatter: (_v, r) => {
        const d = parseServerUtcTs(r.last_message_at);
        if (!d) return '<span style="color:var(--text-faint);">—</span>';
        const shown = fmtDmTs(d);
        const full = d.toLocaleString();
        return '<div class="dm-last-ts-abs" title="' + escapeHtml(full) + '">' + escapeHtml(shown) + '</div>';
      } },
  ];
  const colCount = columns.length;
  mountSortableTable({
    containerId: 'top-dms-container',
    rows: normalized,
    state: _topDmsTableState,
    rowId: r => r.id,
    onAfterRender: tbody => {
      if (!window.__dmExpandedIds) window.__dmExpandedIds = Object.create(null);
      const stillExpanded = Object.create(null);
      tbody.querySelectorAll('tr[data-row-id]').forEach(tr => {
        const idStr = tr.getAttribute('data-row-id');
        const idNum = Number(idStr);
        if (window.__dmExpandedIds[idNum]) {
          const dm = (window.__dmsById || {})[idNum];
          if (dm) {
            const expRow = buildDmExpansionRow(dm, colCount);
            tr.insertAdjacentHTML('afterend', expRow);
            tr.classList.add('dm-row-expanded');
            stillExpanded[idNum] = true;
          }
        }
      });
      window.__dmExpandedIds = stillExpanded;
    },
    columns,
  });
  if (container && !container.__dmExpandWired) {
    container.__dmExpandWired = true;
    container.addEventListener('click', ev => {
      const tgt = ev.target;
      if (!tgt) return;
      if (tgt.closest && tgt.closest('a, button, input, select, textarea, label')) return;
      const tr = tgt.closest ? tgt.closest('tr[data-row-id]') : null;
      if (!tr) return;
      if (!container.contains(tr)) return;
      const idStr = tr.getAttribute('data-row-id');
      const idNum = Number(idStr);
      if (!idNum) return;
      toggleDmExpansion(tr, idNum, colCount);
    });
  }
  // "Load more" footer: visible whenever the server reports more rows than
  // we've fetched. With sort_bucket=85 threads (not_our_prospect / declined)
  // sitting past row 200, this is the only way to surface them when no
  // search query narrows the set.
  if (container) {
    const old = container.querySelector('.dm-load-more');
    if (old) old.remove();
    if (serverTotal != null && loadedCount < serverTotal && !isLookup) {
      const remaining = serverTotal - loadedCount;
      const btn = document.createElement('div');
      btn.className = 'dm-load-more';
      btn.innerHTML =
        '<button type="button" class="btn btn-secondary" id="dm-load-more-btn">' +
          'Load ' + Math.min(remaining, topDmPageSize()) + ' more (of ' + remaining + ' remaining)' +
        '</button>';
      container.appendChild(btn);
      const moreBtn = btn.querySelector('#dm-load-more-btn');
      if (moreBtn) {
        moreBtn.addEventListener('click', () => {
          if (_topDmsLoading) return;
          _topDmOffset = loadedCount;
          moreBtn.textContent = 'Loading…';
          moreBtn.disabled = true;
          loadTopDms(true, { append: true });
        });
      }
    }
  }
}

function buildDmExpansionRow(dm, colCount) {
  const msgs = Array.isArray(dm.messages) ? dm.messages : [];
  const total = msgs.length;
  const metaParts = [];
  if (dm.platform) metaParts.push('<span class="dm-exp-meta-chip">' + escapeHtml(String(dm.platform)) + '</span>');
  if (dm.their_author) metaParts.push('<span class="dm-exp-meta-chip">@' + escapeHtml(dm.their_author) + '</span>');
  metaParts.push('<span class="dm-exp-meta-chip">' + total + ' message' + (total === 1 ? '' : 's') + '</span>');
  if (dm.conversation_status) metaParts.push('<span class="dm-exp-meta-chip">' + escapeHtml(dm.conversation_status) + '</span>');
  if (dm.interest_level) metaParts.push('<span class="dm-exp-meta-chip">' + escapeHtml(dm.interest_level) + '</span>');
  const linkInfo = dmOpenUrl(dm);
  if (linkInfo) metaParts.push('<a class="dm-exp-meta-link" href="' + escapeHtml(linkInfo.url) + '" target="_blank" rel="noopener">' + escapeHtml(linkInfo.label) + '</a>');
  const metaHtml = '<div class="dm-exp-meta">' + metaParts.join('') + '</div>';
  const escHtml = renderDmEscalationCard(dm);
  const contextHtml = renderDmContextBlock(dm);
  let bodyHtml;
  if (!total) {
    bodyHtml = '<div class="dm-exp-empty">(no messages recorded)</div>';
  } else {
    bodyHtml = '<div class="dm-exp-thread">' +
      msgs.map(m => renderDmExpansionMsg(m)).join('') +
      '</div>';
  }
  return '<tr class="dm-exp-row" data-exp-for="' + Number(dm.id) + '">' +
    '<td colspan="' + colCount + '" class="dm-exp-cell">' +
      '<div class="dm-exp-inner">' + metaHtml + escHtml + contextHtml + bodyHtml + '</div>' +
    '</td>' +
  '</tr>';
}

// Renders the pre-DM context chain for any platform: the post we made, the
// thread it lived in, the comment of theirs that triggered outreach, and our
// public reply to that comment (if any). Each section is rendered only when
// data exists, so cold DMs with no public-engagement trail stay empty.
function renderDmContextBlock(dm) {
  if (!dm) return '';
  const sections = [];
  const ourContent   = dm.context_our_content || '';
  const ourUrl       = dm.context_our_url || '';
  const threadTitle  = dm.context_thread_title || '';
  const threadUrl    = dm.context_thread_url || '';
  const threadText   = dm.context_thread_content || '';
  const threadAuthor = dm.context_thread_author || '';
  const theirComment = dm.trigger_comment_content || '';
  const theirCommentUrl = dm.trigger_comment_url || '';
  const theirCommentAuthor = dm.trigger_comment_author || dm.their_author || '';
  const ourReply = dm.trigger_our_reply_content || '';
  const ourReplyUrl = dm.trigger_our_reply_url || '';
  const ourReplyAt = dm.trigger_our_reply_at || '';
  const rawCommentCtx = dm.seed_comment_context || '';
  const seedOurDm = dm.seed_our_dm_content || '';
  const isFallback = !!dm.context_is_fallback;
  const fbChip = isFallback
    ? '<span class="dm-exp-ctx-fallback" title="DM row had no reply_id/post_id link; context inferred from most recent matching replies row for this (platform, author)">inferred</span>'
    : '';

  if (threadTitle || threadText) {
    const head = '<span class="dm-exp-ctx-label">thread</span>' + fbChip +
      (threadAuthor ? '<span class="dm-exp-ctx-author">@' + escapeHtml(threadAuthor) + '</span>' : '') +
      (threadUrl ? '<a class="dm-exp-ctx-link" href="' + escapeHtml(threadUrl) + '" target="_blank" rel="noopener">open thread</a>' : '');
    const title = threadTitle ? '<div class="dm-exp-ctx-title">' + escapeHtml(threadTitle) + '</div>' : '';
    const body  = threadText ? '<div class="dm-exp-ctx-body">' + escapeHtml(threadText) + '</div>' : '';
    sections.push('<div class="dm-exp-ctx-section">' +
      '<div class="dm-exp-ctx-head">' + head + '</div>' +
      title + body +
    '</div>');
  }

  if (ourContent) {
    const head = '<span class="dm-exp-ctx-label">our post / reply</span>' +
      (ourUrl ? '<a class="dm-exp-ctx-link" href="' + escapeHtml(ourUrl) + '" target="_blank" rel="noopener">open</a>' : '');
    sections.push('<div class="dm-exp-ctx-section">' +
      '<div class="dm-exp-ctx-head">' + head + '</div>' +
      '<div class="dm-exp-ctx-body">' + escapeHtml(ourContent) + '</div>' +
    '</div>');
  }

  if (theirComment) {
    const head = '<span class="dm-exp-ctx-label">their comment</span>' +
      (theirCommentAuthor ? '<span class="dm-exp-ctx-author">@' + escapeHtml(theirCommentAuthor) + '</span>' : '') +
      (theirCommentUrl ? '<a class="dm-exp-ctx-link" href="' + escapeHtml(theirCommentUrl) + '" target="_blank" rel="noopener">open comment</a>' : '');
    sections.push('<div class="dm-exp-ctx-section">' +
      '<div class="dm-exp-ctx-head">' + head + '</div>' +
      '<div class="dm-exp-ctx-body">' + escapeHtml(theirComment) + '</div>' +
    '</div>');
  } else if (rawCommentCtx) {
    sections.push('<div class="dm-exp-ctx-section">' +
      '<div class="dm-exp-ctx-head"><span class="dm-exp-ctx-label">comment context</span></div>' +
      '<div class="dm-exp-ctx-body">' + escapeHtml(rawCommentCtx) + '</div>' +
    '</div>');
  }

  if (ourReply) {
    const head = '<span class="dm-exp-ctx-label">our public reply</span>' +
      (ourReplyAt ? '<span class="dm-exp-ctx-author">' + escapeHtml(relTime(ourReplyAt)) + '</span>' : '') +
      (ourReplyUrl ? '<a class="dm-exp-ctx-link" href="' + escapeHtml(ourReplyUrl) + '" target="_blank" rel="noopener">open reply</a>' : '');
    sections.push('<div class="dm-exp-ctx-section">' +
      '<div class="dm-exp-ctx-head">' + head + '</div>' +
      '<div class="dm-exp-ctx-body">' + escapeHtml(ourReply) + '</div>' +
    '</div>');
  }

  // Only show the seed "our first DM" block if dm_messages is empty, otherwise
  // it duplicates the thread view.
  const hasMessages = Array.isArray(dm.messages) && dm.messages.length > 0;
  if (!hasMessages && seedOurDm) {
    sections.push('<div class="dm-exp-ctx-section">' +
      '<div class="dm-exp-ctx-head"><span class="dm-exp-ctx-label">our opening dm (seed)</span></div>' +
      '<div class="dm-exp-ctx-body">' + escapeHtml(seedOurDm) + '</div>' +
    '</div>');
  }

  if (!sections.length) return '';
  return '<div class="dm-exp-ctx">' + sections.join('') + '</div>';
}

// Renders the escalation card: visible when this DM was flagged for human
// handoff (conversation_status='needs_human' or human_reason is set) or when
// any human-authored instructions are queued/sent for it. Composing a new
// instruction inserts a row into human_dm_replies (status='pending'); Phase 0
// of engage-dm-replies.sh consumes it on the next platform tick and the LLM
// there crafts the actual DM from the instructions text.
function renderDmEscalationCard(dm) {
  if (!dm) return '';
  const reason = dm.human_reason ? String(dm.human_reason) : '';
  const list = Array.isArray(dm.human_instructions) ? dm.human_instructions : [];
  const isFlagged = dm.conversation_status === 'needs_human';
  if (!reason && !list.length && !isFlagged) return '';

  const head =
    '<div class="dm-esc-head">' +
      '<span class="dm-esc-tag">escalation</span>' +
      (dm.flagged_at ? '<span class="dm-exp-ctx-author">flagged ' + escapeHtml(relTime(dm.flagged_at)) + '</span>' : '') +
    '</div>';

  const reasonHtml = reason
    ? '<div class="dm-esc-reason">' + escapeHtml(reason) + '</div>'
    : '';

  const itemsHtml = list.length
    ? '<div class="dm-esc-list">' + list.map(it => {
        const status = String(it && it.status || 'pending');
        const source = String(it && it.source || 'gmail');
        const created = it && it.created_at ? relTime(it.created_at) : '';
        const sent = it && it.sent_at ? relTime(it.sent_at) : '';
        const attempts = it && Number(it.attempts) || 0;
        const lastErr = it && it.last_error ? String(it.last_error) : '';
        const instructionsText = String(it && it.instructions || '');
        const replyText = String(it && it.generated_reply || '');
        const meta =
          '<span class="dm-esc-status dm-esc-status-' + escapeHtml(status) + '">' + escapeHtml(status) + '</span>' +
          '<span class="dm-esc-source">' + escapeHtml(source) + '</span>' +
          (created ? '<span>' + escapeHtml(created) + '</span>' : '') +
          (sent ? '<span>· sent ' + escapeHtml(sent) + '</span>' : '') +
          (attempts > 0 ? '<span>· ' + attempts + ' attempt' + (attempts === 1 ? '' : 's') + '</span>' : '') +
          (lastErr ? '<span title="' + escapeHtml(lastErr) + '">· error</span>' : '');
        // Each instruction renders as two paired blocks: the human's
        // instructions (always shown) and the agent's crafted reply (shown
        // when status='sent' and we matched an outbound dm_messages row
        // within the heuristic time window).
        const replyBlock = replyText
          ? '<div class="dm-esc-item-label dm-esc-item-label-reply">agent reply</div>' +
            '<div class="dm-esc-item-reply">' + escapeHtml(replyText) + '</div>'
          : (status === 'sent'
            ? '<div class="dm-esc-item-label dm-esc-item-label-reply">agent reply</div>' +
              '<div class="dm-esc-item-reply dm-esc-item-reply-missing">(could not match outbound DM, see message thread above)</div>'
            : '');
        return '<div class="dm-esc-item">' +
          '<div class="dm-esc-item-meta">' + meta + '</div>' +
          '<div class="dm-esc-item-label">your instructions</div>' +
          '<div class="dm-esc-item-body">' + escapeHtml(instructionsText) + '</div>' +
          replyBlock +
        '</div>';
      }).join('') + '</div>'
    : '';

  const composeId = 'dm-esc-ta-' + Number(dm.id);
  const feedbackId = 'dm-esc-fb-' + Number(dm.id);
  const compose =
    '<div class="dm-esc-compose">' +
      '<textarea id="' + composeId + '" class="dm-esc-textarea" placeholder="Briefly, what should we say back? The agent will craft the actual DM from these instructions."></textarea>' +
      '<div class="dm-esc-bar">' +
        '<span class="dm-esc-hint">Cmd/Ctrl+Enter to send</span>' +
        '<button type="button" class="dm-esc-submit" onclick="submitDmInstructions(this, ' + Number(dm.id) + ')">Send instructions</button>' +
      '</div>' +
      '<div id="' + feedbackId + '" class="dm-esc-feedback"></div>' +
    '</div>';

  return '<div class="dm-esc-card" data-esc-for="' + Number(dm.id) + '">' +
    head + reasonHtml + itemsHtml + compose +
  '</div>';
}

function renderDmExpansionMsg(m) {
  const dir = m && m.direction === 'outbound' ? 'outbound' : 'inbound';
  const author = m && m.author ? m.author : (dir === 'outbound' ? 'us' : 'them');
  const content = m && m.content ? String(m.content) : '';
  const ts = m && m.message_at ? m.message_at : '';
  const rel = ts ? relTime(ts) : '';
  return '<div class="dm-exp-msg dm-exp-msg-' + dir + '">' +
    '<div class="dm-exp-msg-head">' +
      '<span class="dm-exp-msg-author">' + escapeHtml(dir === 'outbound' ? 'us' : author) + '</span>' +
      '<span class="dm-exp-msg-time">' + escapeHtml(rel) + '</span>' +
    '</div>' +
    '<div class="dm-exp-msg-body">' + escapeHtml(content) + '</div>' +
  '</div>';
}

function toggleDmExpansion(tr, dmId, colCount) {
  if (!window.__dmExpandedIds) window.__dmExpandedIds = Object.create(null);
  const next = tr.nextElementSibling;
  if (next && next.classList && next.classList.contains('dm-exp-row') && Number(next.getAttribute('data-exp-for')) === dmId) {
    next.remove();
    tr.classList.remove('dm-row-expanded');
    delete window.__dmExpandedIds[dmId];
    return;
  }
  const dm = (window.__dmsById || {})[dmId];
  if (!dm) return;
  const html = buildDmExpansionRow(dm, colCount);
  tr.insertAdjacentHTML('afterend', html);
  tr.classList.add('dm-row-expanded');
  window.__dmExpandedIds[dmId] = true;
}

// POST a new human-authored instruction for this DM. Inserts into
// human_dm_replies (status='pending'). The next launchd tick of
// engage-dm-replies-<platform> picks it up and the LLM there crafts the DM.
async function submitDmInstructions(btn, dmId) {
  const ta = document.getElementById('dm-esc-ta-' + dmId);
  const fb = document.getElementById('dm-esc-fb-' + dmId);
  if (!ta || !fb) return;
  const txt = (ta.value || '').trim();
  if (txt.length < 5) {
    fb.className = 'dm-esc-feedback dm-esc-feedback-err';
    fb.textContent = 'Please write at least 5 characters of instructions.';
    return;
  }
  btn.disabled = true;
  fb.className = 'dm-esc-feedback';
  fb.textContent = 'Sending...';
  try {
    const resp = await fetch('/api/dm/' + dmId + '/instructions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instructions: txt }),
    });
    let data = {};
    try { data = await resp.json(); } catch (_) {}
    if (!resp.ok) {
      fb.className = 'dm-esc-feedback dm-esc-feedback-err';
      fb.textContent = (data && data.error) ? ('Failed: ' + data.error) : ('Failed (HTTP ' + resp.status + ')');
      btn.disabled = false;
      return;
    }
    const ins = data.instruction || {};
    const dm = (window.__dmsById || {})[dmId];
    if (dm) {
      if (!Array.isArray(dm.human_instructions)) dm.human_instructions = [];
      dm.human_instructions.push(ins);
    }
    ta.value = '';
    fb.className = 'dm-esc-feedback dm-esc-feedback-ok';
    fb.textContent = 'Queued. The agent will send the DM on its next run (~every 30 min for this platform).';
    btn.disabled = false;
  } catch (e) {
    fb.className = 'dm-esc-feedback dm-esc-feedback-err';
    fb.textContent = 'Network error: ' + ((e && e.message) || 'unknown');
    btn.disabled = false;
  }
}

// Cmd/Ctrl+Enter inside any escalation textarea triggers send.
if (!window.__dmEscKeydownInstalled) {
  window.__dmEscKeydownInstalled = true;
  document.addEventListener('keydown', function(e) {
    const t = e.target;
    if (!t || !t.classList || !t.classList.contains('dm-esc-textarea')) return;
    if (!((e.metaKey || e.ctrlKey) && e.key === 'Enter')) return;
    e.preventDefault();
    const idStr = (t.id || '').replace('dm-esc-ta-', '');
    const dmId = parseInt(idStr, 10);
    if (!dmId) return;
    const card = t.closest('.dm-esc-card');
    const btn = card ? card.querySelector('.dm-esc-submit') : null;
    if (btn && !btn.disabled) submitDmInstructions(btn, dmId);
  });
}

async function loadTopDms(force, opts) {
  if (_topDmsLoading) return;
  const append = !!(opts && opts.append);
  if (!append && _topDmsLoaded && !force) return;
  _topDmsLoading = true;
  try {
    if (!append) _topDmOffset = 0;
    const params = new URLSearchParams({
      limit: String(topDmPageSize()),
      window: _topWindow,
      offset: String(_topDmOffset),
    });
    if (_topPlatform && _topPlatform !== 'all') params.set('platform', _topPlatform);
    if (_topDmSearch) params.set('q', _topDmSearch);
    const container = document.getElementById('top-dms-container');
    if (container && force && !append) container.innerHTML = '<div class="style-stats-empty">Loading\u2026</div>';
    const res = await fetch('/api/top/dms?' + params.toString());
    const data = await res.json();
    if (append && _topDmsPayload && Array.isArray(_topDmsPayload.dms) && Array.isArray(data.dms)) {
      data.dms = _topDmsPayload.dms.concat(data.dms);
    }
    _topDmsPayload = data;
    renderTopDms(data);
    _topDmsLoaded = true;
  } catch (e) {
    const container = document.getElementById('top-dms-container');
    if (container) container.innerHTML = '<div class="style-stats-empty">Failed to load.</div>';
  } finally {
    _topDmsLoading = false;
  }
}

let _deployHealthLoading = false;
let _deployHealthTimer = null;
function renderDeployHealth(data) {
  const body = document.getElementById('deploy-health-body');
  const totalEl = document.getElementById('deploy-health-total');
  const section = document.getElementById('deploy-health');
  if (!body) return;
  if (data && data.error) {
    body.innerHTML = '<div class="style-stats-empty">Failed: ' + escapeHtml(data.error) + '</div>';
    if (totalEl) totalEl.textContent = 'error';
    if (section) section.setAttribute('data-alert', 'error');
    return;
  }
  const rows = (data && data.projects) || [];
  const counts = (data && data.counts) || {};
  const warnStates = { CANCELED: 1, API_ERROR: 1, UNMATCHED: 1, NO_DEPLOY: 1 };
  const errorCount = counts.error || rows.filter(r => r.state === 'ERROR').length;
  const warnCount = rows.filter(r => warnStates[r.state]).length;
  if (section) {
    if (errorCount) section.setAttribute('data-alert', 'error');
    else if (warnCount) section.setAttribute('data-alert', 'warn');
    else section.removeAttribute('data-alert');
  }
  if (totalEl) {
    const pieces = [];
    if (errorCount) pieces.push(errorCount + ' error');
    if (warnCount) pieces.push(warnCount + ' warn');
    if (counts.building) pieces.push(counts.building + ' building');
    if (counts.ready) pieces.push(counts.ready + ' ready');
    if (!pieces.length) pieces.push('all ready');
    totalEl.textContent = pieces.join(' \u00b7 ');
  }
  if (!rows.length) {
    body.innerHTML = '<div class="style-stats-empty">No projects.</div>';
    return;
  }
  const stateColor = {
    'ERROR':        { bg: '#fef2f2', fg: '#b91c1c', border: '#fecaca' },
    'CANCELED':     { bg: '#fff7ed', fg: '#c2410c', border: '#fed7aa' },
    'API_ERROR':    { bg: '#fff7ed', fg: '#c2410c', border: '#fed7aa' },
    'UNMATCHED':    { bg: '#f4f4f5', fg: '#6b7280', border: '#e5e7eb' },
    'NO_DEPLOY':    { bg: '#f4f4f5', fg: '#6b7280', border: '#e5e7eb' },
    'BUILDING':     { bg: '#eff6ff', fg: '#1d4ed8', border: '#bfdbfe' },
    'QUEUED':       { bg: '#eff6ff', fg: '#1d4ed8', border: '#bfdbfe' },
    'INITIALIZING': { bg: '#eff6ff', fg: '#1d4ed8', border: '#bfdbfe' },
    'READY':        { bg: '#f0fdf4', fg: '#15803d', border: '#bbf7d0' },
  };
  function humanAge(sec) {
    if (sec == null) return '';
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec/60) + 'm ago';
    if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
    return Math.floor(sec/86400) + 'd ago';
  }
  const cells = rows.map(r => {
    const s = r.state || 'UNKNOWN';
    const c = stateColor[s] || { bg: '#f4f4f5', fg: '#6b7280', border: '#e5e7eb' };
    const host = r.host ? ('<a href="https://' + escapeHtml(r.host) + '" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;">' + escapeHtml(r.host) + '</a>') : '';
    const inspectorLink = r.inspector_url
      ? ' <a href="' + escapeHtml(r.inspector_url) + '" target="_blank" rel="noopener" style="color:var(--link);text-decoration:none;">logs</a>'
      : '';
    const commit = r.commit_sha ? '<code style="font-size:11px;color:var(--text-muted);">' + escapeHtml(r.commit_sha) + '</code>' : '';
    const age = r.age_sec != null ? '<span style="color:var(--text-muted);font-size:11px;">' + humanAge(r.age_sec) + '</span>' : '';
    const msg = r.commit_message ? '<span style="color:var(--text-secondary);font-size:12px;">' + escapeHtml(r.commit_message) + '</span>' : (r.error ? '<span style="color:var(--text-muted);font-size:12px;">' + escapeHtml(r.error) + '</span>' : '');
    return (
      '<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--divider);font-size:13px;">' +
        '<span style="display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;background:' + c.bg + ';color:' + c.fg + ';border:1px solid ' + c.border + ';min-width:70px;text-align:center;">' + escapeHtml(s) + '</span>' +
        '<span style="font-weight:600;min-width:140px;">' + escapeHtml(r.name || '') + '</span>' +
        '<span style="color:var(--text-muted);min-width:200px;">' + host + '</span>' +
        commit + age + inspectorLink +
        '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + msg + '</span>' +
      '</div>'
    );
  }).join('');
  const footer = data && data.generated_at_ms
    ? '<div style="padding:6px 12px;font-size:11px;color:var(--text-muted);">updated ' + humanAge(Math.floor((Date.now() - data.generated_at_ms) / 1000)) + '</div>'
    : '';
  body.innerHTML = cells + footer;
}
async function loadDeployHealth() {
  if (_deployHealthLoading) return;
  _deployHealthLoading = true;
  try {
    const res = await fetch('/api/deploy/status');
    const data = await res.json();
    renderDeployHealth(data);
  } catch (e) {
    renderDeployHealth({ error: String(e && e.message || e) });
  } finally {
    _deployHealthLoading = false;
  }
}

// Project Status: per-weighted-project distribution of posts in the last N
// hours by platform against config.json weight targets. Each platform cell
// shows the count plus that project's share of the platform's posts in
// brackets, so operators can spot imbalance without a separate deficit field.
const PROJECT_STATUS_PLATFORMS = ['reddit', 'twitter', 'linkedin', 'moltbook', 'github'];
const PROJECT_STATUS_PLATFORM_LABELS = {
  reddit: 'Reddit', twitter: 'Twitter', linkedin: 'LinkedIn',
  moltbook: 'MoltBook', github: 'GitHub',
};
let _projectStatusLoading = false;
function formatPct(v) { return (Number(v || 0) * 100).toFixed(1) + '%'; }
function renderProjectStatus(data) {
  const body = document.getElementById('project-status-body');
  const totalEl = document.getElementById('project-status-total');
  const heading = document.getElementById('project-status-heading');
  if (!body) return;
  if (data && data.error) {
    if (totalEl) totalEl.textContent = 'error';
    body.innerHTML = '<div class="style-stats-empty">' + escapeHtml(data.error) + '</div>';
    return;
  }
  const hours = Number(data && data.hours) || 24;
  if (heading) heading.textContent = 'Project Status (last ' + hours + 'h)';
  const projects = (data && data.projects) || [];
  const unassigned = (data && data.unassigned) || [];
  const grandTotal = Number(data && data.grand_total) || 0;
  const totals = (data && data.platform_totals) || {};
  if (totalEl) {
    totalEl.textContent = grandTotal.toLocaleString() + ' post' + (grandTotal === 1 ? '' : 's') +
      ' · ' + projects.length + ' project' + (projects.length === 1 ? '' : 's');
  }
  if (!projects.length && !unassigned.length) {
    body.innerHTML = '<div class="style-stats-empty">No projects configured with weight &gt; 0.</div>';
    return;
  }
  const header =
    '<thead><tr>' +
      '<th style="text-align:left;">Project</th>' +
      '<th style="text-align:right;">Weight</th>' +
      '<th style="text-align:right;">Target&nbsp;%</th>' +
      PROJECT_STATUS_PLATFORMS.map(p =>
        '<th style="text-align:right;">' + PROJECT_STATUS_PLATFORM_LABELS[p] + '</th>'
      ).join('') +
      '<th style="text-align:right;">Total</th>' +
    '</tr></thead>';
  const cellWithShare = (n, platformTotal, targetShare, opts) => {
    const num = Number(n) || 0;
    const pt = Number(platformTotal) || 0;
    const style = 'text-align:right;font-variant-numeric:tabular-nums;' + (opts && opts.extra || '');
    if (num === 0 && !(opts && opts.showZeroShare)) {
      return '<td style="' + style + 'color:var(--text-very-faint);">0</td>';
    }
    const share = pt > 0 ? num / pt : 0;
    let shareColor = 'var(--text-muted)';
    if (targetShare != null && pt > 0) {
      const diff = share - targetShare;
      if (diff > 0.02) shareColor = '#15803d';
      else if (diff < -0.02) shareColor = '#b91c1c';
    }
    return '<td style="' + style + '">' +
      '<span style="font-weight:600;">' + num + '</span>' +
      ' <span style="color:' + shareColor + ';font-size:11px;">(' + formatPct(share) + ')</span>' +
    '</td>';
  };
  const rowHtml = (r) => {
    const targetShare = r.unassigned ? null : Number(r.target_share) || 0;
    const perPlatformTarget = (r && r.target_share_by_platform) || {};
    const platformCells = PROJECT_STATUS_PLATFORMS.map(p => {
      const n = (r.by_platform && r.by_platform[p]) || 0;
      // NA: project is weighted but ineligible for this platform (e.g. no
      // github_search_topics → not in the GitHub picker's pool).
      if (!r.unassigned && perPlatformTarget[p] === null) {
        return '<td style="text-align:right;font-variant-numeric:tabular-nums;color:var(--text-very-faint);">NA</td>';
      }
      const platTarget = r.unassigned
        ? null
        : (Object.prototype.hasOwnProperty.call(perPlatformTarget, p) ? perPlatformTarget[p] : targetShare);
      return cellWithShare(n, totals[p], platTarget);
    }).join('');
    const nameCell = r.website
      ? '<a href="' + escapeHtml(r.website) + '" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;border-bottom:1px dotted var(--text-very-faint);">' + escapeHtml(r.name) + '</a>'
      : escapeHtml(r.name);
    const nameLabel = r.unassigned
      ? nameCell + ' <span style="color:var(--text-muted);font-size:11px;font-weight:400;">(not weighted)</span>'
      : nameCell;
    const totalCell = cellWithShare(r.total, grandTotal, targetShare, { extra: 'font-weight:600;', showZeroShare: true });
    return '<tr>' +
      '<td style="text-align:left;font-weight:600;">' + nameLabel + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + (r.weight || 0) + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;color:var(--text-muted);">' + (r.unassigned ? '&mdash;' : formatPct(r.target_share)) + '</td>' +
      platformCells +
      totalCell +
    '</tr>';
  };
  const bodyRows = projects.map(rowHtml).join('') + unassigned.map(rowHtml).join('');
  const footerCells = PROJECT_STATUS_PLATFORMS.map(p =>
    '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + (Number(totals[p]) || 0) + '</td>'
  ).join('');
  const footerHtml =
    '<tr style="border-top:2px solid var(--border);font-weight:600;background:var(--bg-subtle);">' +
      '<td style="text-align:left;">Total</td>' +
      '<td></td><td></td>' + footerCells +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;">' + grandTotal + '</td>' +
    '</tr>';
  const legend =
    '<div style="font-size:11px;color:var(--text-muted);padding:8px 2px 2px;">' +
      'Bracketed % is this project’s share of that platform’s posts in the window. Green means above target share (overposting), red means below (eligible for the picker). NA means the project is ineligible for that platform (no topics configured or threads disabled) and is excluded from that platform’s target denominator.' +
    '</div>';
  body.innerHTML =
    '<div style="overflow-x:auto;">' +
      '<table class="style-stats-table">' + header +
        '<tbody>' + bodyRows + footerHtml + '</tbody>' +
      '</table>' +
    '</div>' + legend;
}
async function loadProjectStatus(force) {
  if (_projectStatusLoading) return;
  if (saAuthNotReady()) return;
  _projectStatusLoading = true;
  try {
    const hours = currentStatusWindow().hours;
    const res = await fetch('/api/project/status?hours=' + hours);
    const data = await res.json();
    renderProjectStatus(data);
  } catch (e) {
    renderProjectStatus({ error: String(e && e.message || e) });
  } finally {
    _projectStatusLoading = false;
  }
}

// In CLIENT_MODE, /api/* calls need the Firebase ID token on Authorization.
// Any fetch that fires before the fetch wrapper has a token returns
// {error:"missing_token"}, which the section renderers display as-is.
// Return true when we should skip the fetch outright (token missing in
// CLIENT_MODE); saStartApp() re-fires these after auth settles.
function saAuthNotReady() {
  var cfg = window.SA_CONFIG || {};
  return !!cfg.clientMode && !window.SA_ID_TOKEN;
}

let _funnelStatsLoadedFor = null;
let _funnelStatsLoading = false;
let _lastFunnelPayload = null;
async function loadFunnelStats(force) {
  if (_funnelStatsLoading) return;
  if (saAuthNotReady()) return;
  const days = currentStatsWindow().days;
  if (_funnelStatsLoadedFor === days && !force) return;
  _funnelStatsLoading = true;
  const totalEl = document.getElementById('funnel-stats-total');
  const body = document.getElementById('funnel-stats-body');
  if (totalEl) totalEl.textContent = 'loading\u2026';
  if (body) {
    body.innerHTML = '<div class="style-stats-empty">Loading\u2026 (first call can take 15\u201330s)</div>';
  }
  try {
    const res = await fetch('/api/funnel/stats?days=' + days);
    const data = await res.json();
    if (data && !data.error) _lastFunnelPayload = data;
    renderFunnelStats(data);
    _funnelStatsLoadedFor = days;
  } catch (e) {
    if (body) body.innerHTML = '<div class="style-stats-empty">Failed to load.</div>';
  } finally {
    _funnelStatsLoading = false;
  }
}

let _dmStatsLoadedFor = null;
let _dmStatsLoading = false;
async function loadDmStats(force) {
  if (_dmStatsLoading) return;
  if (saAuthNotReady()) return;
  const days = currentStatsWindow().days;
  const plat = currentStatsPlatform();
  const proj = currentStatsProject();
  const key  = days + '|' + plat + '|' + proj;
  if (_dmStatsLoadedFor === key && !force) return;
  _dmStatsLoading = true;
  const totalEl = document.getElementById('dm-stats-total');
  const body = document.getElementById('dm-stats-body');
  if (totalEl) totalEl.textContent = 'loading\u2026';
  if (body) body.innerHTML = '<div class="style-stats-empty">Loading\u2026</div>';
  try {
    const params = ['days=' + days];
    if (plat && plat !== 'all') params.push('platform=' + encodeURIComponent(plat));
    if (proj && proj !== 'all') params.push('project='  + encodeURIComponent(proj));
    const res = await fetch('/api/dm/stats?' + params.join('&'));
    const data = await res.json();
    renderDmStats(data);
    _dmStatsLoadedFor = key;
  } catch (e) {
    if (body) body.innerHTML = '<div class="style-stats-empty">Failed to load.</div>';
  } finally {
    _dmStatsLoading = false;
  }
}

let _lastActivityEvents = [];
function renderActivity(events) {
  _lastActivityEvents = events;
  refreshActivityProjectPills(events);
  const body = document.getElementById('activity-body');
  if (!body) return;
  renderSortArrows();
  const filtered = events.filter(e => {
    if (!_activityTypeFilter.has(e.type)) return false;
    if (!_activityPlatformFilter.has((e.platform || '').toLowerCase())) return false;
    if (!_activityProjectFilter.has(activityProjectKey(e))) return false;
    if (!activityMatchesSearch(e, _activitySearch)) return false;
    return true;
  });
  const sorted = sortActivity(filtered, _activitySortField, _activitySortDir);
  const start = _activityPage * _activityPageSize;
  const page = sorted.slice(start, start + _activityPageSize);
  document.getElementById('activity-count').textContent =
    sorted.length + ' of ' + events.length + ' events';
  renderPagination(sorted.length);
  if (!page.length) {
    body.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text);padding:40px;">No matching events</td></tr>';
    return;
  }
  const rows = page.map(e => {
    const isNew = !_activityFirstLoad && !_activitySeen.has(e.key);
    const timeAbs = e.occurred_at ? new Date(e.occurred_at).toLocaleString() : '';
    const detailHtml = e.detail
      ? '<span class="activity-detail">(' + escapeHtml(e.detail) + ')</span>'
      : '';
    const summaryText = escapeHtml(e.summary || '');
    const summaryLink = e.link
      ? ' <a class="activity-summary-url" href="' + escapeHtml(e.link) + '" target="_blank" rel="noopener">' + escapeHtml(e.link) + '</a>'
      : '';
    return '<tr' + (isNew ? ' class="activity-row-new"' : '') + ' data-key="' + escapeHtml(e.key) + '">' +
      '<td title="' + escapeHtml(timeAbs) + '">' +
        '<div class="activity-event-cell">' +
          '<span class="activity-time">' + escapeHtml(relTime(e.occurred_at)) + '</span>' +
          '<span class="ev-pill ev-' + escapeHtml(e.type) + '">' + escapeHtml(EVENT_LABELS[e.type] || e.type) + '</span>' +
        '</div>' +
      '</td>' +
      '<td class="activity-platform-cell">' + platformIconHtml(e.platform) + '</td>' +
      '<td>' +
        '<div class="activity-project-cell">' +
          '<span class="activity-project">' + escapeHtml(e.project || '') + '</span>' +
          detailHtml +
        '</div>' +
      '</td>' +
      '<td class="activity-summary">' + summaryText + summaryLink + '</td>' +
      '<td style="text-align:right;font-variant-numeric:tabular-nums;color:var(--text-secondary);">' + fmtCost(e.cost_usd) + '</td>' +
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
const _tabLoaded = { logs: false, activity: false, settings: false, top: false };
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
    if (name === 'stats') { loadActivityStats(); loadStyleStats(); loadDmStats(); loadAllPerDayCharts(); }
    if (name === 'top') {
      initTopFilters();
      if (_topSubtab === 'pages') {
        loadTopPages();
      } else if (_topSubtab === 'dms') {
        loadTopDms(true);
      } else if (!_tabLoaded.top) {
        loadTopPosts();
        _tabLoaded.top = true;
      } else {
        loadTopPosts(true);
      }
    }
    if (name === 'settings' && !_tabLoaded.settings) {
      loadSettings();
      _tabLoaded.settings = true;
    }
  });
});

// Top-of-tab window pills (24h / 7d / 14d / 30d). Selection drives all three
// Stats-tab sections so the user switches them in one click.
(function wireStatsWindowPills() {
  const row = document.getElementById('stats-window-pills');
  if (!row) return;
  // Sync the active pill from the saved/coerced _statsWindow, so the UI
  // reflects the user's persisted preference (default 7d) on first paint.
  row.dataset.selected = _statsWindow;
  row.querySelectorAll('.style-stats-pill').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-value') === _statsWindow);
  });
  row.addEventListener('click', ev => {
    const btn = ev.target.closest('.style-stats-pill');
    if (!btn) return;
    const v = btn.getAttribute('data-value') || '7d';
    if (!STATS_WINDOWS[v] || v === _statsWindow) return;
    _statsWindow = v;
    saveDashboardWindow(v);
    row.dataset.selected = v;
    row.querySelectorAll('.style-stats-pill').forEach(b => {
      b.classList.toggle('active', b === btn);
    });
    syncStatsHeadings();
    loadActivityStats();
    loadStyleStats();
    // daily-metrics is window-independent; don't refetch on pill change.
    const funnelEl = document.getElementById('funnel-stats');
    if (funnelEl && funnelEl.open) loadFunnelStats(true);
    const dmEl = document.getElementById('dm-stats');
    if (dmEl && dmEl.open) loadDmStats(true);
  });
  syncStatsHeadings();
})();

// Top-of-Status-tab window pills. Drives cost-stats, project-status,
// and the Job History table's time filter. Independent of the Stats-tab
// window so the operator can scope each page separately.
(function wireStatusWindowPills() {
  const row = document.getElementById('status-window-pills');
  if (!row) return;
  // Sync the active pill from the saved/coerced _statusWindow.
  row.dataset.selected = _statusWindow;
  row.querySelectorAll('.style-stats-pill').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-value') === _statusWindow);
  });
  row.addEventListener('click', ev => {
    const btn = ev.target.closest('.style-stats-pill');
    if (!btn) return;
    const v = btn.getAttribute('data-value') || '7d';
    if (!STATS_WINDOWS[v] || v === _statusWindow) return;
    _statusWindow = v;
    saveDashboardWindow(v);
    row.dataset.selected = v;
    row.querySelectorAll('.style-stats-pill').forEach(b => {
      b.classList.toggle('active', b === btn);
    });
    syncStatusHeadings();
    const costEl = document.getElementById('cost-stats');
    if (costEl && costEl.open) loadCostStats(true);
    loadProjectStatus(true);
    loadJobsHistory(true);
  });
  syncStatusHeadings();
})();

// Top-of-stats-tab platform/project pill click handling lives in
// renderStyleStatsPills (pills are rebuilt from the /api/style/stats payload),
// which invokes reloadStatsTabSections() to re-fetch every stats-tab section
// so platform+project scope flows through the whole page.

// Lazy-load funnel stats the first time the user opens the section. The fetch
// shells out to PostHog and two Postgres DBs, so we don't want to run it on
// every page load.
// Toggle listeners are wired immediately, but the initial "if open" load is
// deferred to saStartApp() so it runs after Firebase has attached an ID
// token. Otherwise, in CLIENT_MODE, these fire at script-parse time, hit
// /api/* without auth, get 401, and never retry.
(function wireFunnelStats() {
  const el = document.getElementById('funnel-stats');
  if (!el) return;
  el.addEventListener('toggle', () => {
    if (el.open) loadFunnelStats();
  });
})();

(function wireDmStats() {
  const el = document.getElementById('dm-stats');
  if (!el) return;
  el.addEventListener('toggle', () => {
    if (el.open) loadDmStats();
  });
})();

(function wireCostStats() {
  const el = document.getElementById('cost-stats');
  if (!el) return;
  el.addEventListener('toggle', () => {
    if (el.open) loadCostStats();
  });
  if (el.open) loadCostStats();
  const row = document.getElementById('cost-stats-platform-pills');
  if (row) {
    row.addEventListener('click', ev => {
      const btn = ev.target.closest('.style-stats-pill');
      if (!btn) return;
      const v = btn.getAttribute('data-value') || 'all';
      if (row.dataset.selected === v) return;
      row.dataset.selected = v;
      row.querySelectorAll('.style-stats-pill').forEach(b => {
        b.classList.toggle('active', b === btn);
      });
      if (el.open) loadCostStats(true);
    });
  }
})();

document.getElementById('log-job-filter').addEventListener('change', () => { loadLogFiles(); startLogAutoRefresh(); });
document.getElementById('log-file-select').addEventListener('change', e => loadLogContent(e.target.value));
document.getElementById('log-refresh-btn').addEventListener('click', loadLogFiles);
document.getElementById('save-settings').addEventListener('click', saveSettings);

// Init. In CLIENT_MODE the auth bootstrap below calls saStartApp() once
// Firebase hands us an ID token; in local mode it fires immediately.
function saStartApp() {
  document.body.classList.remove('sa-authed-pending');
  const isCloud = document.body.classList.contains('sa-cloud');
  const isAdmin = window.SA_IS_ADMIN !== false;
  // Status + pending are local-only (UI hidden by body.sa-cloud). Endpoints
  // are admin-only too, so skipping them on cloud also stops 403 spam for
  // scoped clients.
  if (!isCloud) {
    loadStatus();
    setInterval(loadStatus, 5000);
  }
  loadActivityStats();
  loadStyleStats();
  loadAllPerDayCharts();
  // Deploy Health is inside the Status tab, which is local-only. On the
  // hosted client dashboard we skip the fetch entirely; Cloud Run has no
  // mirror for project_deploy_status.py, so polling it just spams 503.
  if (!isCloud) {
    loadDeployHealth();
    setInterval(loadDeployHealth, 60000);
    loadProjectStatus();
    setInterval(loadProjectStatus, 60000);
  }
  // Funnel + DM stats sections are \`<details open>\` by default; load them
  // here (post-auth) rather than in their wire IIFEs, which fire before
  // the Firebase ID token is attached.
  const funnelEl = document.getElementById('funnel-stats');
  if (funnelEl && funnelEl.open) loadFunnelStats();
  const dmEl = document.getElementById('dm-stats');
  if (dmEl && dmEl.open) loadDmStats();
  setInterval(loadActivityStats, 300000);
  setInterval(loadStyleStats, 300000);
  setInterval(loadAllPerDayCharts, 300000);
  setTimeout(() => {
    // Logs + Settings tabs are admin-only (hidden via body.sa-non-admin);
    // their endpoints are admin-only too, so guard the preload for scoped users.
    if (isAdmin) {
      try { loadLogFiles(); _tabLoaded.logs = true; } catch {}
    }
    try { buildActivityFilters(); loadActivity(); _tabLoaded.activity = true; } catch {}
    try { initTopFilters(); loadTopPosts(); _tabLoaded.top = true; } catch {}
    if (isAdmin) {
      try { loadSettings(); _tabLoaded.settings = true; } catch {}
    }
  }, 100);
}
window.saStartApp = saStartApp;

// Auth bootstrap. CLIENT_MODE=0 (local operator): start immediately. Else
// init Firebase, gate the app on a valid ID token, and hide admin-only UI
// for project-scoped users based on /api/me claims.
(function saAuthBootstrap() {
  var cfg = window.SA_CONFIG || {};
  var host = (window.location && window.location.hostname) || '';
  var isLocalhost = host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '';
  if (!isLocalhost) document.body.classList.add('sa-cloud');
  if (!cfg.clientMode) { window.SA_IS_ADMIN = true; saStartApp(); return; }
  if (!cfg.firebase || !cfg.firebase.apiKey) {
    document.getElementById('sa-login-error').textContent = 'Auth not configured';
    document.getElementById('sa-login-overlay').style.display = 'flex';
    return;
  }
  firebase.initializeApp(cfg.firebase);
  var fbAuth = firebase.auth();
  var overlay = document.getElementById('sa-login-overlay');
  var errEl = document.getElementById('sa-login-error');
  var form = document.getElementById('sa-login-form');
  var descEl = document.getElementById('sa-login-desc');
  var submitBtn = document.getElementById('sa-login-submit');
  var emailInput = document.getElementById('sa-login-email');
  var descDefault = "Enter your email and we'll send you a sign-in link.";

  var actionCodeSettings = {
    url: window.location.origin + '/',
    handleCodeInApp: true
  };

  // If user landed here by clicking a magic link, finish the sign-in.
  if (fbAuth.isSignInWithEmailLink(window.location.href)) {
    overlay.style.display = 'flex';
    form.style.display = 'none';
    descEl.textContent = 'Completing sign-in...';
    var savedEmail = window.localStorage.getItem('saEmailForSignIn') || '';
    if (!savedEmail) {
      savedEmail = (window.prompt('Confirm your email for sign-in') || '').trim();
    }
    fbAuth.signInWithEmailLink(savedEmail, window.location.href).then(function() {
      window.localStorage.removeItem('saEmailForSignIn');
      if (window.history && window.history.replaceState) {
        window.history.replaceState({}, document.title, window.location.pathname);
      }
    }).catch(function(err) {
      form.style.display = '';
      descEl.textContent = descDefault;
      errEl.textContent = (err && err.message) || 'Sign-in link failed. Request a new one.';
    });
  }

  form.addEventListener('submit', function(e) {
    e.preventDefault();
    errEl.textContent = '';
    var email = emailInput.value.trim();
    if (!email) return;
    submitBtn.disabled = true;
    fbAuth.sendSignInLinkToEmail(email, actionCodeSettings).then(function() {
      window.localStorage.setItem('saEmailForSignIn', email);
      form.style.display = 'none';
      descEl.textContent = 'Check your email for a sign-in link. You can close this tab; the link will open it back up signed in.';
    }).catch(function(err) {
      errEl.textContent = err.message || 'Could not send link';
    }).finally(function() {
      submitBtn.disabled = false;
    });
  });
  window.saSignOut = function() {
    fbAuth.signOut().then(function() { location.reload(); });
  };
  fbAuth.onIdTokenChanged(function(user) {
    if (!user) {
      window.SA_ID_TOKEN = null;
      document.body.classList.add('sa-authed-pending');
      overlay.style.display = 'flex';
      return;
    }
    user.getIdToken().then(function(tok) {
      window.SA_ID_TOKEN = tok;
      return fetch('/api/me').then(function(r) { return r.json(); });
    }).then(function(me) {
      var u = me && me.user;
      if (!u) throw new Error('no user');
      window.SA_IS_ADMIN = !!u.admin;
      if (!u.admin) document.body.classList.add('sa-non-admin');
      var signoutBtn = document.getElementById('sa-signout-btn');
      if (signoutBtn) signoutBtn.style.display = '';
      var badge = document.getElementById('sa-user-badge');
      if (badge) {
        var who = u.email || u.uid || 'unknown';
        var projList = Array.isArray(u.projects) && u.projects.length ? u.projects.join(', ') : '';
        var tag = u.admin ? 'admin' : (projList || 'no projects');
        badge.textContent = who + ' · ' + tag;
        badge.title = 'uid: ' + (u.uid || '') + (projList ? ' | projects: ' + projList : '');
        badge.style.display = '';
      }
      overlay.style.display = 'none';
      saStartApp();
    }).catch(function(err) {
      errEl.textContent = (err && err.message) || 'Auth failed';
      overlay.style.display = 'flex';
    });
  });
  // Refresh token proactively so long sessions don't 401.
  setInterval(function() {
    var u = fbAuth.currentUser;
    if (u) u.getIdToken(true).then(function(t) { window.SA_ID_TOKEN = t; }).catch(function(){});
  }, 30 * 60 * 1000);
})();
</script>
</body>
</html>`;

// Firebase Web SDK config for the dashboard bootstrap. The apiKey is a
// client-side identifier (intended to be shipped in HTML), not a secret;
// access control is enforced by Firebase Security Rules and by HTTP
// referrer restrictions on the key itself. Values are injected via env
// so the image can be repointed at a different Firebase project, and so
// GitHub secret scanning does not flag literal AIza-prefixed strings in
// source. In CLIENT_MODE=1 these env vars are required; in local
// operator mode (CLIENT_MODE=0) the config is written into the HTML but
// Firebase is never initialized, so missing values are harmless.
function firebaseWebConfig() {
  return {
    apiKey: process.env.FIREBASE_WEB_API_KEY || '',
    authDomain: process.env.FIREBASE_AUTH_DOMAIN || '',
    projectId: process.env.FIREBASE_PROJECT_ID || '',
  };
}

function renderHtml() {
  return HTML
    .replace('__SA_CLIENT_MODE_PLACEHOLDER__', JSON.stringify(auth.CLIENT_MODE))
    .replace('__SA_FIREBASE_CONFIG_PLACEHOLDER__', JSON.stringify(firebaseWebConfig()));
}

// --- Server ---

const server = http.createServer((req, res) => {
  // CORS for local dev
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  const pathname = req.url.split('?')[0];
  if (pathname === '/' || pathname === '/index.html') {
    // Dashboard JS is inlined in the HTML, so any fix to client logic has to
    // reach users on their next navigation. Without cache-control, Chrome's
    // heuristic caching serves stale HTML for hours and clients end up running
    // JS that no longer matches the server's auth contract.
    res.writeHead(200, {
      'Content-Type': 'text/html',
      'Cache-Control': 'no-store, no-cache, must-revalidate',
      'Pragma': 'no-cache',
    });
    res.end(renderHtml());
  } else if (pathname.startsWith('/api/')) {
    Promise.resolve(handleApi(req, res)).catch(e => {
      try { json(res, { error: e.message || String(e) }, 500); } catch {}
    });
  } else {
    res.writeHead(404);
    res.end('Not found');
  }
});

function tryListen(port, maxAttempts = 10) {
  // In CLIENT_MODE the server runs on Cloud Run and must bind publicly;
  // otherwise keep localhost-only for the operator's own Mac.
  const host = auth.CLIENT_MODE ? '0.0.0.0' : '127.0.0.1';
  server.listen(port, host, () => {
    const actualPort = server.address().port;
    console.log(`Social Autoposter dashboard running at http://${host}:${actualPort}`);
    if (!auth.CLIENT_MODE) {
      const { platform } = os;
      const cmd = platform === 'darwin' ? 'open' : platform === 'win32' ? 'start' : 'xdg-open';
      try { execSync(`${cmd} http://localhost:${actualPort}`, { stdio: 'ignore' }); } catch {}
    }
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
