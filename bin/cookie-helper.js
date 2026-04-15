#!/usr/bin/env node
'use strict';

/**
 * Export and import browser cookies for social-autoposter profiles.
 *
 * Export (macOS/Linux): reads the Cookies SQLite database directly, decrypts
 * cookie values using the OS keychain key, outputs Playwright storageState JSON.
 *
 * Import: launches headless Chromium per platform profile, injects cookies
 * via CDP (Network.setCookies) so they persist to the profile on disk.
 */

const { spawn, spawnSync, execSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');

const HOME = os.homedir();
const DEST = path.join(HOME, 'social-autoposter');
const PROFILES_DIR = path.join(HOME, '.claude', 'browser-profiles');
const PLATFORMS = ['reddit', 'twitter', 'linkedin'];

// ── SQLite-based cookie export (no Chrome launch needed) ──

// Get the Chromium Safe Storage encryption key from macOS keychain
function getEncryptionKey() {
  // Playwright's Chromium uses "Chromium Safe Storage"
  const candidates = [
    { service: 'Chromium Safe Storage', account: 'Chromium' },
    { service: 'Chrome Safe Storage', account: 'Chrome' },
  ];
  for (const { service, account } of candidates) {
    try {
      const pw = execSync(
        `security find-generic-password -w -s "${service}" -a "${account}"`,
        { encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }
      ).trim();
      if (pw) return { password: pw, browser: account };
    } catch {
      // not found, try next
    }
  }
  return null;
}

// Derive AES key from the keychain password (Chrome's PBKDF2 params)
function deriveKey(password) {
  return crypto.pbkdf2Sync(password, 'saltysalt', 1003, 16, 'sha1');
}

// Decrypt a Chrome encrypted_value blob
function decryptValue(encryptedBuf, key) {
  if (!encryptedBuf || encryptedBuf.length === 0) return '';

  // v10 prefix = AES-128-CBC with 16-byte zero IV (macOS)
  // v11 prefix = same but different on some versions
  const prefix = encryptedBuf.slice(0, 3).toString('ascii');
  if (prefix !== 'v10' && prefix !== 'v11') {
    // Not encrypted or unknown format; return as-is
    return encryptedBuf.toString('utf8');
  }

  const encrypted = encryptedBuf.slice(3);
  const iv = Buffer.alloc(16, ' '); // 16 space characters (0x20) per Chrome's macOS implementation
  try {
    const decipher = crypto.createDecipheriv('aes-128-cbc', key, iv);
    let decrypted = decipher.update(encrypted);
    decrypted = Buffer.concat([decrypted, decipher.final()]);
    return decrypted.toString('utf8');
  } catch {
    return ''; // decryption failed; skip this cookie
  }
}

// Chrome epoch: microseconds since 1601-01-01
// Unix epoch: seconds since 1970-01-01
// Difference: 11644473600 seconds
function chromeTimeToUnix(chromeTime) {
  if (!chromeTime || chromeTime === 0) return -1;
  return Math.floor(chromeTime / 1000000) - 11644473600;
}

// Map Chrome sameSite integer to string
function sameSiteToString(val) {
  switch (val) {
    case 0: return 'None';
    case 1: return 'Lax';
    case 2: return 'Strict';
    default: return 'Lax';
  }
}

// Read cookies from a profile's SQLite database
function readCookiesFromSqlite(profileDir, aesKey) {
  const cookieDb = path.join(profileDir, 'Default', 'Cookies');
  if (!fs.existsSync(cookieDb)) return [];

  // Copy the database to avoid SQLite lock issues
  const tmpDb = path.join(os.tmpdir(), `cookies-export-${Date.now()}.db`);
  fs.copyFileSync(cookieDb, tmpDb);

  // Also copy the WAL if it exists (needed for recent writes)
  const walFile = cookieDb + '-wal';
  if (fs.existsSync(walFile)) {
    try { fs.copyFileSync(walFile, tmpDb + '-wal'); } catch {}
  }
  const shmFile = cookieDb + '-shm';
  if (fs.existsSync(shmFile)) {
    try { fs.copyFileSync(shmFile, tmpDb + '-shm'); } catch {}
  }

  try {
    // Query all cookies as JSON using sqlite3
    const query = `SELECT host_key, name, value, encrypted_value, path, expires_utc, is_secure, is_httponly, samesite FROM cookies;`;
    const result = spawnSync('sqlite3', ['-separator', '|||', tmpDb, query], {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
      maxBuffer: 50 * 1024 * 1024,
    });

    if (result.status !== 0) {
      console.error(`    sqlite3 error: ${result.stderr}`);
      return [];
    }

    const cookies = [];
    for (const line of result.stdout.split('\n')) {
      if (!line.trim()) continue;
      const parts = line.split('|||');
      if (parts.length < 9) continue;

      const [hostKey, name, value, encryptedHex, cookiePath, expiresUtc, isSecure, isHttpOnly, sameSite] = parts;

      // Try plaintext value first; if empty, decrypt
      let cookieValue = value;
      if (!cookieValue && encryptedHex && aesKey) {
        // sqlite3 outputs BLOB as hex when using separator mode; we need binary
        // Re-query this specific cookie as hex blob
      }

      cookies.push({
        name,
        value: cookieValue,
        domain: hostKey,
        path: cookiePath || '/',
        expires: chromeTimeToUnix(parseInt(expiresUtc, 10)),
        httpOnly: isHttpOnly === '1',
        secure: isSecure === '1',
        sameSite: sameSiteToString(parseInt(sameSite, 10)),
      });
    }
    return cookies;
  } finally {
    try { fs.unlinkSync(tmpDb); } catch {}
    try { fs.unlinkSync(tmpDb + '-wal'); } catch {}
    try { fs.unlinkSync(tmpDb + '-shm'); } catch {}
  }
}

// Better approach: use sqlite3 with hex() for encrypted values
function readCookiesFromSqliteWithDecryption(profileDir, aesKey) {
  const cookieDb = path.join(profileDir, 'Default', 'Cookies');
  if (!fs.existsSync(cookieDb)) return [];

  const tmpDb = path.join(os.tmpdir(), `cookies-export-${Date.now()}.db`);
  fs.copyFileSync(cookieDb, tmpDb);
  const walFile = cookieDb + '-wal';
  if (fs.existsSync(walFile)) {
    try { fs.copyFileSync(walFile, tmpDb + '-wal'); } catch {}
  }
  const shmFile = cookieDb + '-shm';
  if (fs.existsSync(shmFile)) {
    try { fs.copyFileSync(shmFile, tmpDb + '-shm'); } catch {}
  }

  try {
    // Output as JSON using sqlite3's json mode
    const query = `SELECT host_key, name, value, hex(encrypted_value) as enc_hex, path, expires_utc, is_secure, is_httponly, samesite FROM cookies;`;
    const result = spawnSync('sqlite3', ['-json', tmpDb, query], {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'pipe'],
      maxBuffer: 50 * 1024 * 1024,
    });

    if (result.status !== 0) {
      console.error(`    sqlite3 error: ${result.stderr}`);
      return [];
    }

    let rows;
    try { rows = JSON.parse(result.stdout); } catch { return []; }

    const cookies = [];
    for (const row of rows) {
      let cookieValue = row.value || '';

      // If no plaintext value, try to decrypt
      if (!cookieValue && row.enc_hex && aesKey) {
        const buf = Buffer.from(row.enc_hex, 'hex');
        cookieValue = decryptValue(buf, aesKey);
      }

      // Skip cookies with no value at all
      if (!cookieValue) continue;

      cookies.push({
        name: row.name,
        value: cookieValue,
        domain: row.host_key,
        path: row.path || '/',
        expires: chromeTimeToUnix(row.expires_utc),
        httpOnly: row.is_httponly === 1,
        secure: row.is_secure === 1,
        sameSite: sameSiteToString(row.samesite),
      });
    }
    return cookies;
  } finally {
    try { fs.unlinkSync(tmpDb); } catch {}
    try { fs.unlinkSync(tmpDb + '-wal'); } catch {}
    try { fs.unlinkSync(tmpDb + '-shm'); } catch {}
  }
}

// ── CDP-based cookie import ──

function findChromium() {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
    return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  }
  const candidates = [
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/usr/bin/google-chrome',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

function getCdpWsUrl(port) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}/json/version`, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try { resolve(JSON.parse(data).webSocketDebuggerUrl); }
        catch (e) { reject(new Error(`CDP parse error: ${e.message}`)); }
      });
    });
    req.on('error', reject);
    req.setTimeout(3000, () => { req.destroy(); reject(new Error('CDP timeout')); });
  });
}

function getPageWsUrl(port) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}/json/list`, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const pages = JSON.parse(data);
          const page = pages.find(p => p.type === 'page');
          if (page) resolve(page.webSocketDebuggerUrl);
          else reject(new Error('No page targets'));
        } catch (e) { reject(new Error(`CDP parse error: ${e.message}`)); }
      });
    });
    req.on('error', reject);
    req.setTimeout(3000, () => { req.destroy(); reject(new Error('CDP timeout')); });
  });
}

// Resolve WebSocket class
let WebSocketClass;
try {
  if (typeof globalThis.WebSocket !== 'undefined') {
    WebSocketClass = globalThis.WebSocket;
  } else {
    try { WebSocketClass = require('ws'); } catch {
      try { WebSocketClass = require(path.join('/usr/lib/node_modules', 'ws')); } catch {
        // Will fail later if import-cookies is used without ws
      }
    }
  }
} catch {}

function cdpSend(wsUrl, method, params = {}) {
  if (!WebSocketClass) throw new Error('No WebSocket. Install ws: npm install -g ws');
  return new Promise((resolve, reject) => {
    const ws = new WebSocketClass(wsUrl);
    const id = 1;
    let resolved = false;
    const timer = setTimeout(() => {
      if (!resolved) { resolved = true; ws.close(); reject(new Error(`CDP ${method} timeout`)); }
    }, 15000);
    ws.on('open', () => { ws.send(JSON.stringify({ id, method, params })); });
    ws.on('message', (raw) => {
      if (resolved) return;
      const msg = JSON.parse(typeof raw === 'string' ? raw : raw.toString());
      if (msg.id === id) {
        resolved = true; clearTimeout(timer); ws.close();
        if (msg.error) reject(new Error(`CDP error: ${msg.error.message}`));
        else resolve(msg.result);
      }
    });
    ws.on('error', (err) => {
      if (!resolved) { resolved = true; clearTimeout(timer); reject(err); }
    });
  });
}

function playwrightToCdp(cookie) {
  const out = {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path || '/',
    httpOnly: Boolean(cookie.httpOnly),
    secure: Boolean(cookie.secure),
  };
  if (cookie.expires && cookie.expires > 0) out.expires = cookie.expires;
  if (cookie.sameSite) out.sameSite = cookie.sameSite;
  return out;
}

async function withChrome(profileDir, callback) {
  const chromium = findChromium();
  if (!chromium) throw new Error('No Chromium or Chrome binary found');

  const port = 19000 + Math.floor(Math.random() * 10000);
  const args = [
    '--headless=new', '--no-sandbox', '--disable-gpu',
    '--disable-software-rasterizer', `--user-data-dir=${profileDir}`,
    `--remote-debugging-port=${port}`, '--window-size=800,600',
    '--no-first-run', '--no-default-browser-check', '--disable-extensions',
  ];
  const proc = spawn(chromium, args, { stdio: ['ignore', 'pipe', 'pipe'], detached: false });

  let wsUrl = null;
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 250));
    try { wsUrl = await getCdpWsUrl(port); if (wsUrl) break; } catch {}
  }
  if (!wsUrl) { proc.kill('SIGKILL'); throw new Error(`Chrome failed to start CDP on port ${port}`); }

  try {
    return await callback(wsUrl, port);
  } finally {
    proc.kill('SIGTERM');
    await new Promise(r => setTimeout(r, 500));
    try { proc.kill('SIGKILL'); } catch {}
  }
}

// ── Export ──

async function exportCookies(platforms, outputDir) {
  // On macOS, get the encryption key from keychain
  const keyInfo = (os.platform() === 'darwin') ? getEncryptionKey() : null;
  let aesKey = null;
  if (keyInfo) {
    aesKey = deriveKey(keyInfo.password);
    console.log(`Using ${keyInfo.browser} keychain for cookie decryption`);
  } else if (os.platform() === 'darwin') {
    console.warn('Warning: could not find Chromium/Chrome keychain entry. Encrypted cookies will be skipped.');
  }

  // Verify sqlite3 is available
  const sqlite3Check = spawnSync('sqlite3', ['--version'], { encoding: 'utf8', stdio: 'pipe' });
  if (sqlite3Check.status !== 0) {
    console.error('Error: sqlite3 not found. Install it to export cookies.');
    process.exit(1);
  }

  fs.mkdirSync(outputDir, { recursive: true });

  for (const platform of platforms) {
    const profileDir = path.join(PROFILES_DIR, platform);
    if (!fs.existsSync(profileDir)) {
      console.log(`  ${platform}: no profile at ${profileDir}, skipping`);
      continue;
    }

    const cookieDb = path.join(profileDir, 'Default', 'Cookies');
    if (!fs.existsSync(cookieDb)) {
      console.log(`  ${platform}: no Cookies database, skipping`);
      continue;
    }

    process.stdout.write(`  ${platform}: reading cookies...`);

    try {
      const cookies = readCookiesFromSqliteWithDecryption(profileDir, aesKey);
      const outFile = path.join(outputDir, `cookies-${platform}.json`);
      fs.writeFileSync(outFile, JSON.stringify({ cookies, origins: [] }, null, 2));
      console.log(` ${cookies.length} cookies -> ${outFile}`);
    } catch (err) {
      console.log(` FAILED: ${err.message}`);
    }
  }
}

// ── Import ──

async function importCookies(platforms, inputDir) {
  const chromium = findChromium();
  if (!chromium) {
    console.error('Error: No Chromium or Chrome binary found.');
    process.exit(1);
  }
  if (!WebSocketClass) {
    console.error('Error: WebSocket not available. Install ws: npm install -g ws');
    process.exit(1);
  }
  console.log(`Using browser: ${chromium}`);

  for (const platform of platforms) {
    const cookieFile = path.join(inputDir, `cookies-${platform}.json`);
    if (!fs.existsSync(cookieFile)) {
      console.log(`  ${platform}: no ${cookieFile} found, skipping`);
      continue;
    }

    const data = JSON.parse(fs.readFileSync(cookieFile, 'utf8'));
    const cookies = (data.cookies || []).map(playwrightToCdp);
    if (cookies.length === 0) {
      console.log(`  ${platform}: cookie file is empty, skipping`);
      continue;
    }

    const profileDir = path.join(PROFILES_DIR, platform);
    fs.mkdirSync(profileDir, { recursive: true });

    process.stdout.write(`  ${platform}: launching browser...`);

    try {
      await withChrome(profileDir, async (wsUrl, port) => {
        process.stdout.write(` injecting ${cookies.length} cookies...`);
        const pageWsUrl = await getPageWsUrl(port);
        await cdpSend(pageWsUrl, 'Network.enable');
        await cdpSend(pageWsUrl, 'Network.setCookies', { cookies });
      });
      console.log(' done');
    } catch (err) {
      console.log(` FAILED: ${err.message}`);
    }
  }
}

// ── Main ──

async function main() {
  const args = process.argv.slice(2);
  const mode = args[0];

  if (mode === 'export') {
    const outputDir = args[1] || DEST;
    console.log(`Exporting browser cookies to ${outputDir}/`);
    await exportCookies(PLATFORMS, outputDir);
    console.log('\nExport complete. Files ready for upload to VM.');
  } else if (mode === 'import') {
    const inputDir = args[1] || DEST;
    console.log(`Importing browser cookies from ${inputDir}/`);
    await importCookies(PLATFORMS, inputDir);
    console.log('\nImport complete. Browser profiles updated.');
  } else {
    console.log('Usage:');
    console.log('  npx social-autoposter export-cookies [output-dir]');
    console.log('  npx social-autoposter import-cookies [input-dir]');
    console.log('');
    console.log('Export reads cookies from ~/.claude/browser-profiles/{reddit,twitter,linkedin}');
    console.log('Import injects cookies from cookies-{platform}.json into browser profiles');
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('Fatal:', err.message);
  process.exit(1);
});
