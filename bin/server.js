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
  { label: 'com.m13v.social-reddit', name: 'Reddit', type: 'Post', platform: 'Reddit', script: 'run-reddit.sh', logPrefix: 'run-reddit-', plist: 'com.m13v.social-reddit.plist' },
  { label: 'com.m13v.social-twitter', name: 'Twitter', type: 'Post', platform: 'Twitter', script: 'run-twitter.sh', logPrefix: 'run-twitter-', plist: 'com.m13v.social-twitter.plist' },
  { label: 'com.m13v.social-linkedin', name: 'LinkedIn', type: 'Post', platform: 'LinkedIn', script: 'run-linkedin.sh', logPrefix: 'run-linkedin-', plist: 'com.m13v.social-linkedin.plist' },
  { label: 'com.m13v.social-moltbook', name: 'MoltBook', type: 'Post', platform: 'MoltBook', script: 'run-moltbook.sh', logPrefix: 'run-moltbook-', plist: 'com.m13v.social-moltbook.plist' },
  { label: 'com.m13v.social-github', name: 'GitHub', type: 'Post', platform: 'GitHub', script: 'github.sh', logPrefix: 'github-', plist: 'com.m13v.social-github.plist' },
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
    const pending = psql("SELECT COUNT(*) FROM replies WHERE status='pending'");
    return json(res, { jobs, pendingReplies: pending ? parseInt(pending, 10) : null });
  }

  // POST /api/jobs/:label/toggle
  const toggleMatch = p.match(/^\/api\/jobs\/([^/]+)\/toggle$/);
  if (toggleMatch && req.method === 'POST') {
    const label = decodeURIComponent(toggleMatch[1]);
    const job = JOBS.find(j => j.label === label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    const plistSrc = path.join(LAUNCHD_DIR, job.plist);
    const agentLink = getLaunchAgentPath(job.plist);
    const loaded = isJobLoaded(label);
    try {
      if (loaded) {
        execSync(`launchctl unload "${agentLink}"`, { stdio: 'pipe' });
        try { fs.unlinkSync(agentLink); } catch {}
      } else {
        // Symlink plist to ~/Library/LaunchAgents/ and load
        const agentDir = path.dirname(agentLink);
        fs.mkdirSync(agentDir, { recursive: true });
        try { fs.unlinkSync(agentLink); } catch {}
        fs.symlinkSync(plistSrc, agentLink);
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
    const job = JOBS.find(j => j.label === label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    const scriptPath = path.join(DEST, 'skill', job.script);
    const jobEnv = { ...process.env, ...loadEnv(), HOME: os.homedir() };
    delete jobEnv.CLAUDECODE;
    const child = spawn('bash', [scriptPath], {
      detached: true,
      stdio: 'ignore',
      env: jobEnv,
    });
    child.unref();
    return json(res, { started: true, pid: child.pid });
  }

  // POST /api/jobs/:label/stop
  const stopMatch = p.match(/^\/api\/jobs\/([^/]+)\/stop$/);
  if (stopMatch && req.method === 'POST') {
    const label = decodeURIComponent(stopMatch[1]);
    const job = JOBS.find(j => j.label === label);
    if (!job) return json(res, { error: 'Unknown job' }, 404);
    const pids = getJobPids(job.script);
    for (const pid of pids) {
      try { process.kill(pid, 'SIGTERM'); } catch {}
    }
    // Also kill any child claude processes spawned by the script
    try { execSync(`pkill -f "claude.*${job.script.replace('.sh', '')}" 2>/dev/null`, { stdio: 'pipe' }); } catch {}
    return json(res, { stopped: true, killedPids: pids });
  }

  // POST /api/jobs/:label/interval
  const intervalMatch = p.match(/^\/api\/jobs\/([^/]+)\/interval$/);
  if (intervalMatch && req.method === 'POST') {
    return readBody(req).then(body => {
      const { interval } = JSON.parse(body);
      const label = decodeURIComponent(intervalMatch[1]);
      const job = JOBS.find(j => j.label === label);
      if (!job) return json(res, { error: 'Unknown job' }, 404);
      const plistPath = path.join(LAUNCHD_DIR, job.plist);
      let xml = fs.readFileSync(plistPath, 'utf8');
      xml = xml.replace(
        /(<key>StartInterval<\/key>\s*<integer>)\d+(<\/integer>)/,
        `$1${interval}$2`
      );
      fs.writeFileSync(plistPath, xml);
      // Reload if currently loaded
      const agentLink = getLaunchAgentPath(job.plist);
      if (isJobLoaded(label)) {
        try {
          execSync(`launchctl unload "${agentLink}"`, { stdio: 'pipe' });
          // Re-symlink with updated plist
          try { fs.unlinkSync(agentLink); } catch {}
          fs.symlinkSync(plistPath, agentLink);
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
        const job = JOBS.find(j => j.name.toLowerCase() === jobFilter.toLowerCase());
        if (job) {
          files = files.filter(f => {
            if (job.logPrefix) return f.startsWith(job.logPrefix);
            return /^\d{4}-\d{2}-\d{2}_/.test(f);
          });
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
  .badge.running { background: #1e3a5f; color: #60a5fa; animation: pulse 2s infinite; }
  .badge.scheduled { background: #064e3b; color: #6ee7b7; }
  .badge.stopped { background: #292524; color: #a3a3a3; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
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
  <div>
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
  <div id="pending-section" style="margin-top: 16px;"></div>
</div>

<div class="content hidden" id="tab-logs">
  <div class="log-controls">
    <select id="log-job-filter">
      <option value="">All jobs</option>
      <option value="reddit">Reddit</option>
      <option value="twitter">Twitter</option>
      <option value="linkedin">LinkedIn</option>
      <option value="moltbook">MoltBook</option>
      <option value="github">GitHub</option>
      <option value="engage reddit+mb">Engage</option>
      <option value="engage twitter">Engage Twitter</option>
      <option value="engage linkedin">Engage LinkedIn</option>
      <option value="github engage">GitHub Engage</option>
      <option value="stats">Stats</option>
      <option value="audit">Audit</option>
      <option value="octolens">Octolens</option>
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
    '<div class="cell-info" data-field="lastrun">' + relTime(job.lastRun) + '</div>' +
    '<div class="cell-actions">' + runStopBtn + toggleBtn + '</div>' +
  '</div></td>';
}

function renderSpanCell(job, colspan) {
  if (!job) return '<td colspan="' + colspan + '"><span class="matrix-cell-empty">-</span></td>';
  const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
  const runStopBtn = job.running
    ? '<button class="btn danger" onclick="stopJob(\\'' + job.label + '\\')">Stop</button>'
    : '<button class="btn" onclick="runJob(\\'' + job.label + '\\')">Run</button>';
  const toggleBtn = job.loaded
    ? '<button class="btn danger" onclick="toggleJob(\\'' + job.label + '\\')">Off</button>'
    : '<button class="btn primary" onclick="toggleJob(\\'' + job.label + '\\')">On</button>';

  return '<td colspan="' + colspan + '" data-job="' + job.label + '"><div class="matrix-cell">' +
    '<span class="badge ' + job.status + '" data-field="status">' + statusLabel + '</span>' +
    '<div class="cell-info" data-field="lastrun">' + relTime(job.lastRun) + '</div>' +
    '<div class="cell-actions">' + runStopBtn + toggleBtn + '</div>' +
  '</div></td>';
}

function renderFreqCell(jobType, interval) {
  const intervalOptions = INTERVALS.map(i =>
    '<option value="' + i.value + '"' + (i.value === interval ? ' selected' : '') + '>' + i.label + '</option>'
  ).join('');
  return '<td class="freq-cell" data-freq="' + jobType + '">' +
    '<select onchange="setPhaseInterval(\\'' + jobType + '\\', this.value)">' + intervalOptions + '</select>' +
  '</td>';
}

function buildMatrix(jobs) {
  const map = {};
  jobs.forEach(j => { map[j.type + ':' + j.platform] = j; });

  let html = '';
  for (const jobType of JOB_TYPES) {
    // Get interval from the first job in this row
    const rowJobs = jobs.filter(j => j.type === jobType);
    const interval = rowJobs.length ? rowJobs[0].interval : null;

    html += '<tr><td class="row-label">' + jobType + '</td>';
    html += renderFreqCell(jobType, interval);

    const allJob = map[jobType + ':all'];
    if (allJob) {
      html += renderSpanCell(allJob, PLATFORMS.length);
    } else {
      for (const plat of PLATFORMS) {
        html += renderCell(map[jobType + ':' + plat] || null);
      }
    }
    html += '</tr>';
  }
  return html;
}

function updateCell(td, job) {
  const statusLabel = job.status === 'running' ? 'Running' : job.status === 'scheduled' ? 'Scheduled' : 'Stopped';
  const badge = td.querySelector('[data-field="status"]');
  if (badge) { badge.textContent = statusLabel; badge.className = 'badge ' + job.status; }
  const lastrun = td.querySelector('[data-field="lastrun"]');
  if (lastrun) { lastrun.textContent = relTime(job.lastRun); }
}

async function loadStatus() {
  try {
    const [statusRes, pendingRes] = await Promise.all([
      fetch('/api/status'),
      fetch('/api/pending'),
    ]);
    const data = await statusRes.json();
    const pending = await pendingRes.json();

    document.getElementById('pending-badge').textContent =
      (data.pendingReplies != null ? data.pendingReplies : '--') + ' pending';

    const container = document.getElementById('matrix-body');

    if (!_initialized) {
      container.innerHTML = buildMatrix(data.jobs);
      _initialized = true;
    } else {
      data.jobs.forEach(job => {
        const td = container.querySelector('[data-job="' + job.label + '"]');
        if (td) updateCell(td, job);
      });
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
async function loadLogFiles() {
  const filter = document.getElementById('log-job-filter').value;
  const res = await fetch('/api/logs?job=' + filter);
  const data = await res.json();
  const sel = document.getElementById('log-file-select');
  sel.innerHTML = data.files.map(f => '<option value="' + f + '">' + f + '</option>').join('');
  if (data.files.length) loadLogContent(data.files[0]);
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
setInterval(loadStatus, 30000);
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
