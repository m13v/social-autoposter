#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawnSync } = require('child_process');

const platform = require('./platform');
const scheduler = require('./scheduler');

const DEST = path.join(os.homedir(), 'social-autoposter');
const PKG_ROOT = path.join(__dirname, '..');
const HOME = os.homedir();

// Files/dirs to copy from npm package to ~/social-autoposter
const COPY_TARGETS = [
  'scripts',
  'schema-postgres.sql',
  'config.example.json',
  'SKILL.md',
  'skill',
  'setup',
  'browser-agent-configs',
];

const ENV_TEMPLATE = `# social-autoposter environment variables
# Fill in your values below.

# Moltbook API key (required for Moltbook posting/scanning)
# Get it from: https://www.moltbook.com/settings/api
MOLTBOOK_API_KEY=

# Neon Postgres connection string. Bring your own Neon DB — apply schema with:
#   psql "$DATABASE_URL" -f schema-postgres.sql
# Format: postgresql://<user>:<password>@<host>/<db>?sslmode=require
DATABASE_URL=
`;

// Never overwrite these user files during update
const USER_FILES = new Set(['config.json', '.env', 'SKILL.md']);

// Browser agent config templates -> install path under ~/.claude/browser-agent-configs/
const BROWSER_AGENT_CONFIGS = [
  'twitter-agent-mcp.json',
  'twitter-agent.json',
  'reddit-agent-mcp.json',
  'reddit-agent.json',
  'linkedin-agent-mcp.json',
  'linkedin-agent.json',
];

const BROWSER_PROFILES = ['twitter', 'reddit', 'linkedin'];

function copyDir(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDir(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function linkOrRelink(target, linkPath) {
  try { fs.rmSync(linkPath, { recursive: true, force: true }); } catch {}
  fs.symlinkSync(target, linkPath);
}

function installBrowserAgentConfigs() {
  const nodeBin = path.dirname(process.execPath);
  const srcDir = path.join(PKG_ROOT, 'browser-agent-configs');
  const destDir = path.join(HOME, '.claude', 'browser-agent-configs');
  fs.mkdirSync(destDir, { recursive: true });

  let installed = 0;
  let skipped = 0;
  for (const name of BROWSER_AGENT_CONFIGS) {
    const src = path.join(srcDir, name);
    const dest = path.join(destDir, name);
    if (!fs.existsSync(src)) continue;
    if (fs.existsSync(dest)) {
      skipped++;
      continue;
    }
    const tpl = fs.readFileSync(src, 'utf8');
    const out = tpl
      .replace(/__HOME__/g, HOME)
      .replace(/__NODE_BIN__/g, nodeBin);
    fs.writeFileSync(dest, out);
    installed++;
  }
  console.log(`  browser agent configs -> ${destDir} (installed ${installed}, skipped ${skipped} existing)`);

  // Create empty persistent profile dirs so Playwright has somewhere to land cookies
  const profilesDir = path.join(HOME, '.claude', 'browser-profiles');
  fs.mkdirSync(profilesDir, { recursive: true });
  for (const p of BROWSER_PROFILES) {
    fs.mkdirSync(path.join(profilesDir, p), { recursive: true });
  }
  console.log(`  browser profile dirs ready -> ${profilesDir}/{${BROWSER_PROFILES.join(',')}}`);
}

function generatePlists() {
  const nodeBin = path.dirname(process.execPath);
  const jobs = [
    {
      file: 'com.m13v.social-stats.plist',
      label: 'com.m13v.social-stats',
      script: `${DEST}/skill/stats.sh`,
      interval: 21600,
      runAtLoad: false,
      stdoutLog: `${DEST}/skill/logs/launchd-stats-stdout.log`,
      stderrLog: `${DEST}/skill/logs/launchd-stats-stderr.log`,
    },
    {
      file: 'com.m13v.social-engage.plist',
      label: 'com.m13v.social-engage',
      script: `${DEST}/skill/engage.sh`,
      interval: 21600,
      runAtLoad: false,
      stdoutLog: `${DEST}/skill/logs/launchd-engage-stdout.log`,
      stderrLog: `${DEST}/skill/logs/launchd-engage-stderr.log`,
    },
  ];

  const driver = scheduler.driverFor();
  const env = driver.defaultEnv({ home: HOME, nodeBin });
  const kind = platform.scheduler();
  const outDir = path.join(DEST, kind === 'systemd' ? 'systemd' : 'launchd');
  driver.generate({ jobs, outDir, env });
  console.log(`  generated ${kind} units at ${outDir}`);
}

// On Linux we translate every shipped launchd plist into a systemd
// .service + .timer pair at install time. Plists remain the source of truth
// so the macOS pipeline is untouched; the systemd/ dir is derived.
function generateSystemdFromPlists() {
  const launchdDriver = scheduler.driverFor('launchd');
  const systemdDriver = scheduler.driverFor('systemd');
  const srcDir = path.join(DEST, 'launchd');
  const outDir = path.join(DEST, 'systemd');
  if (!fs.existsSync(srcDir)) return 0;
  const plists = fs.readdirSync(srcDir).filter(f => f.endsWith('.plist'));
  const nodeBin = path.dirname(process.execPath);
  const env = systemdDriver.defaultEnv({ home: HOME, nodeBin });

  const jobs = [];
  let skipped = 0;
  for (const f of plists) {
    const xml = fs.readFileSync(path.join(srcDir, f), 'utf8');
    const { label, scriptPath } = launchdDriver.parseUnit(xml);
    if (!label || !scriptPath) { skipped++; continue; }
    const sched = launchdDriver.scheduleFromUnit(xml);
    if (!sched.intervalSecs) {
      console.log(`  skip ${f}: calendar schedule not yet translated to OnCalendar`);
      skipped++;
      continue;
    }
    // Plists ship with the publisher's absolute paths baked in. Rebuild
    // paths against the current user's DEST so any user on any host gets
    // correct units without us having to re-ship plists per install target.
    const scriptBase = path.basename(scriptPath);
    const stdoutMatch = (xml.match(/<key>StandardOutPath<\/key>\s*<string>([^<]+)<\/string>/) || [])[1];
    const stderrMatch = (xml.match(/<key>StandardErrorPath<\/key>\s*<string>([^<]+)<\/string>/) || [])[1];
    const shortLabel = label.replace(/^com\.m13v\.social-/, '');
    const stdout = `${DEST}/skill/logs/${stdoutMatch ? path.basename(stdoutMatch) : `launchd-${shortLabel}-stdout.log`}`;
    const stderr = `${DEST}/skill/logs/${stderrMatch ? path.basename(stderrMatch) : `launchd-${shortLabel}-stderr.log`}`;
    const runAtLoad = /<key>RunAtLoad<\/key>\s*<true\s*\/>/.test(xml);
    jobs.push({
      file: f,
      label,
      script: `${DEST}/skill/${scriptBase}`,
      interval: sched.intervalSecs,
      runAtLoad,
      stdoutLog: stdout,
      stderrLog: stderr,
    });
  }
  systemdDriver.generate({ jobs, outDir, env });
  console.log(`  translated ${jobs.length} launchd plists -> systemd units (skipped ${skipped})`);
  return jobs.length;
}

// Link every DEST/systemd/*.{service,timer} into ~/.config/systemd/user/ and
// reload the user daemon. Caller is expected to `systemctl --user enable --now
// <timer>` for each timer they actually want running; this mirrors how macOS
// setup leaves loading to the user via the SKILL.md wizard.
function installSystemdUnits() {
  const driver = scheduler.driverFor('systemd');
  const unitDir = path.join(DEST, 'systemd');
  const agentsDir = platform.agentsDir();
  if (!fs.existsSync(unitDir)) return;
  fs.mkdirSync(agentsDir, { recursive: true });
  const services = fs.readdirSync(unitDir).filter(f => f.endsWith('.service'));
  let linked = 0;
  for (const f of services) {
    if (driver.install(path.join(unitDir, f), agentsDir)) linked++;
  }
  const r = spawnSync('systemctl', ['--user', 'daemon-reload'], { encoding: 'utf8' });
  if (r.status === 0) {
    console.log(`  linked ${linked} unit pair(s) into ${agentsDir}; systemctl --user daemon-reload OK`);
  } else {
    console.warn(`  linked ${linked} unit pair(s); daemon-reload failed: ${(r.stderr || '').trim()}`);
  }
  const linger = spawnSync('loginctl', ['show-user', os.userInfo().username, '--property=Linger'], { encoding: 'utf8' });
  if (!/Linger=yes/.test(linger.stdout || '')) {
    console.log('  note: run `sudo loginctl enable-linger $USER` so timers fire when nobody is logged in');
  }
  console.log('  next: systemctl --user enable --now <timer> for each job you want scheduled');
}

function init() {
  console.log('Setting up social-autoposter in', DEST);
  fs.mkdirSync(DEST, { recursive: true });

  // Copy all package files
  for (const f of COPY_TARGETS) {
    const src = path.join(PKG_ROOT, f);
    const dest = path.join(DEST, f);
    if (!fs.existsSync(src)) continue;
    const stat = fs.statSync(src);
    if (stat.isDirectory()) {
      copyDir(src, dest);
    } else {
      fs.copyFileSync(src, dest);
    }
    console.log('  copied', f);
  }

  // Generate launchd plists with user's actual HOME
  generatePlists();

  // On Linux, derive systemd units from every plist and link them into
  // ~/.config/systemd/user/. macOS install is unchanged.
  if (platform.scheduler() === 'systemd') {
    generateSystemdFromPlists();
    installSystemdUnits();
  }

  // Install browser agent MCP configs + profile dirs (skips existing files)
  installBrowserAgentConfigs();

  // config.json — only if it doesn't exist
  const configDest = path.join(DEST, 'config.json');
  if (!fs.existsSync(configDest)) {
    fs.copyFileSync(path.join(PKG_ROOT, 'config.example.json'), configDest);
    console.log('  created config.json from template');
  } else {
    console.log('  config.json exists — skipping');
  }

  // .env — only if it doesn't exist. Written from an in-package template so
  // the NPM tarball no longer ships a credential-bearing .env.example file.
  const envDest = path.join(DEST, '.env');
  if (!fs.existsSync(envDest)) {
    fs.writeFileSync(envDest, ENV_TEMPLATE);
    console.log('  created .env from template (fill in DATABASE_URL and MOLTBOOK_API_KEY)');
  } else {
    console.log('  .env exists — skipping');
  }

  // Check psycopg2-binary (required to connect to Neon DB)
  const pip3Check = spawnSync('pip3', ['show', 'psycopg2-binary'], { stdio: 'pipe' });
  if (pip3Check.status !== 0) {
    console.log('  installing psycopg2-binary (required for Neon DB)...');
    const pipInstall = spawnSync('pip3', ['install', 'psycopg2-binary', '-q'], { stdio: 'inherit' });
    if (pipInstall.status !== 0) {
      console.warn('  WARNING: psycopg2-binary install failed — run manually:');
      console.warn('    pip3 install psycopg2-binary');
    } else {
      console.log('  psycopg2-binary installed');
    }
  } else {
    console.log('  psycopg2-binary already installed');
  }

  // Remove stale skill/SKILL.md if it exists (SKILL.md lives at repo root only)
  const skillMd = path.join(DEST, 'skill', 'SKILL.md');
  try { fs.rmSync(skillMd, { force: true }); } catch {}

  // Skill symlinks — point to repo root so Claude loads SKILL.md directly
  const skillsDir = path.join(os.homedir(), '.claude', 'skills');
  fs.mkdirSync(skillsDir, { recursive: true });
  linkOrRelink(DEST, path.join(skillsDir, 'social-autoposter'));
  console.log('  ~/.claude/skills/social-autoposter ->', DEST);
  linkOrRelink(path.join(DEST, 'setup'), path.join(skillsDir, 'social-autoposter-setup'));
  console.log('  ~/.claude/skills/social-autoposter-setup ->', path.join(DEST, 'setup'));

  console.log('');
  console.log('Done! Next steps:');
  console.log('  1. Edit ~/social-autoposter/config.json with your accounts');
  console.log('  2. Tell your Claude agent: "set up social autoposter"');
  console.log('     (uses the setup/SKILL.md wizard for browser login verification)');
  console.log('  3. Posts are logged to the shared Neon DB (DATABASE_URL in .env)');
}

function update() {
  if (!fs.existsSync(DEST)) {
    console.error('Not installed. Run: npx social-autoposter init');
    process.exit(1);
  }

  console.log('Updating social-autoposter...');

  for (const f of COPY_TARGETS) {
    if (USER_FILES.has(f)) {
      console.log('  skipping', f, '(user file)');
      continue;
    }
    const src = path.join(PKG_ROOT, f);
    const dest = path.join(DEST, f);
    if (!fs.existsSync(src)) continue;
    const stat = fs.statSync(src);
    if (stat.isDirectory()) {
      copyDir(src, dest);
    } else {
      fs.copyFileSync(src, dest);
    }
    console.log('  updated', f);
  }

  // Regenerate launchd plists with correct paths
  generatePlists();

  // Refresh systemd units on Linux so plist changes propagate.
  if (platform.scheduler() === 'systemd') {
    generateSystemdFromPlists();
    installSystemdUnits();
  }

  // Top up browser agent configs (won't overwrite user customizations)
  installBrowserAgentConfigs();

  // Remove stale skill/SKILL.md if it exists (SKILL.md lives at repo root only)
  const skillMd = path.join(DEST, 'skill', 'SKILL.md');
  try { fs.rmSync(skillMd, { force: true }); } catch {}

  // Re-symlink skills — point to repo root so Claude loads SKILL.md directly
  const skillsDir = path.join(os.homedir(), '.claude', 'skills');
  try {
    linkOrRelink(DEST, path.join(skillsDir, 'social-autoposter'));
    console.log('  re-linked ~/.claude/skills/social-autoposter');
  } catch {}
  try {
    linkOrRelink(path.join(DEST, 'setup'), path.join(skillsDir, 'social-autoposter-setup'));
    console.log('  re-linked ~/.claude/skills/social-autoposter-setup');
  } catch {}

  console.log('');
  console.log('Update complete. config.json was preserved.');
}

const cmd = process.argv[2];
if (cmd === 'init') {
  init();
} else if (cmd === 'update') {
  update();
} else if (cmd === 'export-cookies') {
  // Forward to cookie-helper with 'export' + remaining args
  process.argv = [process.argv[0], process.argv[1], 'export', ...process.argv.slice(3)];
  require('./cookie-helper.js');
} else if (cmd === 'import-cookies') {
  // Forward to cookie-helper with 'import' + remaining args
  process.argv = [process.argv[0], process.argv[1], 'import', ...process.argv.slice(3)];
  require('./cookie-helper.js');
} else if (!cmd) {
  require('./server.js');
} else {
  console.log('social-autoposter — automated social posting for Claude agents');
  console.log('');
  console.log('Usage:');
  console.log('  npx social-autoposter              open the dashboard');
  console.log('  npx social-autoposter init          first-time setup');
  console.log('  npx social-autoposter update        update scripts, preserve config');
  console.log('  npx social-autoposter export-cookies [dir]  export browser cookies');
  console.log('  npx social-autoposter import-cookies [dir]  import browser cookies');
}
