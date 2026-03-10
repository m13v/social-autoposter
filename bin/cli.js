#!/usr/bin/env node
'use strict';

const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawnSync } = require('child_process');

const DEST = path.join(os.homedir(), 'social-autoposter');
const PKG_ROOT = path.join(__dirname, '..');

// Files/dirs to copy from npm package to ~/social-autoposter
const COPY_TARGETS = [
  'scripts',
  'schema-postgres.sql',
  'config.example.json',
  '.env.example',
  'SKILL.md',
  'skill',
  'setup',
  'launchd',
  'syncfield.sh',
];

// Never overwrite these user files during update
const USER_FILES = new Set(['config.json', 'social_posts.db', '.env']);

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

  // Skill symlinks
  const skillsDir = path.join(os.homedir(), '.claude', 'skills');
  fs.mkdirSync(skillsDir, { recursive: true });
  linkOrRelink(path.join(DEST, 'skill'), path.join(skillsDir, 'social-autoposter'));
  console.log('  ~/.claude/skills/social-autoposter ->', path.join(DEST, 'skill'));
  linkOrRelink(path.join(DEST, 'setup'), path.join(skillsDir, 'social-autoposter-setup'));
  console.log('  ~/.claude/skills/social-autoposter-setup ->', path.join(DEST, 'setup'));

  // DB symlink: ~/.claude/social_posts.db -> ~/social-autoposter/social_posts.db
  const claudeDir = path.join(os.homedir(), '.claude');
  try {
    linkOrRelink(dbPath, path.join(claudeDir, 'social_posts.db'));
    console.log('  ~/.claude/social_posts.db ->', dbPath);
  } catch {}

  console.log('');
  console.log('Done! Next steps:');
  console.log('  1. Edit ~/social-autoposter/config.json with your accounts');
  console.log('  2. Tell your Claude agent: "set up social autoposter"');
  console.log('     (uses the setup/SKILL.md wizard for browser login verification)');
  console.log('  3. Or configure manually and run: bash ~/social-autoposter/setup.sh');
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

  // Re-symlink skill, setup skill, and DB in case they broke
  const skillsDir = path.join(os.homedir(), '.claude', 'skills');
  const claudeDir = path.join(os.homedir(), '.claude');
  try {
    linkOrRelink(path.join(DEST, 'skill'), path.join(skillsDir, 'social-autoposter'));
    console.log('  re-linked ~/.claude/skills/social-autoposter');
  } catch {}
  try {
    linkOrRelink(path.join(DEST, 'setup'), path.join(skillsDir, 'social-autoposter-setup'));
    console.log('  re-linked ~/.claude/skills/social-autoposter-setup');
  } catch {}
  try {
    linkOrRelink(path.join(DEST, 'social_posts.db'), path.join(claudeDir, 'social_posts.db'));
    console.log('  re-linked ~/.claude/social_posts.db');
  } catch {}

  console.log('');
  console.log('Update complete. config.json and social_posts.db were preserved.');
}

const cmd = process.argv[2];
if (cmd === 'init') {
  init();
} else if (cmd === 'update') {
  update();
} else {
  console.log('social-autoposter — automated social posting for Claude agents');
  console.log('');
  console.log('Usage:');
  console.log('  npx social-autoposter init    first-time setup');
  console.log('  npx social-autoposter update  update scripts, preserve config');
}
