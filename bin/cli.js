#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawnSync } = require('child_process');

const DEST = path.join(os.homedir(), 'social-autoposter');
const PKG_ROOT = path.join(__dirname, '..');
const HOME = os.homedir();

// Files/dirs to copy from npm package to ~/social-autoposter
const COPY_TARGETS = [
  'scripts',
  'schema-postgres.sql',
  'config.example.json',
  '.env.example',
  'SKILL.md',
  'skill',
  'setup',
  'browser-agent-configs',
];

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
  // Detect PATH for launchd (include node, homebrew, system)
  const nodeBin = path.dirname(process.execPath);
  const pathDirs = new Set([nodeBin, '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin']);
  const launchdPath = [...pathDirs].join(':');

  const plists = [
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

  const launchdDir = path.join(DEST, 'launchd');
  fs.mkdirSync(launchdDir, { recursive: true });

  for (const p of plists) {
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>${p.label}</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>/bin/bash</string>
\t\t<string>${p.script}</string>
\t</array>
\t<key>StartInterval</key>
\t<integer>${p.interval}</integer>
\t<key>StandardOutPath</key>
\t<string>${p.stdoutLog}</string>
\t<key>StandardErrorPath</key>
\t<string>${p.stderrLog}</string>
\t<key>EnvironmentVariables</key>
\t<dict>
\t\t<key>PATH</key>
\t\t<string>${launchdPath}</string>
\t\t<key>HOME</key>
\t\t<string>${HOME}</string>
\t</dict>
\t<key>RunAtLoad</key>
\t<${p.runAtLoad}/>
</dict>
</plist>
`;
    fs.writeFileSync(path.join(launchdDir, p.file), xml);
  }
  console.log('  generated launchd plists with correct paths');
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

  // .env — only if it doesn't exist
  const envDest = path.join(DEST, '.env');
  if (!fs.existsSync(envDest)) {
    fs.copyFileSync(path.join(PKG_ROOT, '.env.example'), envDest);
    console.log('  created .env from template');
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
