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
  'schema.sql',
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

  // Create DB from schema if missing
  const dbPath = path.join(DEST, 'social_posts.db');
  if (!fs.existsSync(dbPath)) {
    const schemaPath = path.join(DEST, 'schema.sql');
    const result = spawnSync('sqlite3', [dbPath], {
      input: fs.readFileSync(schemaPath),
      stdio: ['pipe', 'inherit', 'inherit'],
    });
    if (result.status === 0) {
      console.log('  created social_posts.db');
    } else {
      console.warn('  WARNING: sqlite3 failed — create DB manually:');
      console.warn('    sqlite3 ~/social-autoposter/social_posts.db < ~/social-autoposter/schema.sql');
    }
  } else {
    console.log('  social_posts.db exists — skipping');
  }

  // Skill symlink: ~/.claude/skills/social-autoposter -> ~/social-autoposter/skill
  const skillsDir = path.join(os.homedir(), '.claude', 'skills');
  fs.mkdirSync(skillsDir, { recursive: true });
  linkOrRelink(path.join(DEST, 'skill'), path.join(skillsDir, 'social-autoposter'));
  console.log('  ~/.claude/skills/social-autoposter ->', path.join(DEST, 'skill'));

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

  // Re-symlink skill in case it broke
  const skillsDir = path.join(os.homedir(), '.claude', 'skills');
  try {
    linkOrRelink(path.join(DEST, 'skill'), path.join(skillsDir, 'social-autoposter'));
    console.log('  re-linked ~/.claude/skills/social-autoposter');
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
