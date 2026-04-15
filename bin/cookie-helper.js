#!/usr/bin/env node
'use strict';

/**
 * Export and import browser cookies for social-autoposter profiles.
 *
 * Export: launches headless Chromium per platform profile, extracts cookies
 * via CDP (Storage.getCookies), saves as Playwright-compatible JSON.
 *
 * Import: reads cookie JSON files, launches headless Chromium per platform
 * profile, injects cookies via CDP (Storage.setCookies).
 */

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');

const HOME = os.homedir();
const DEST = path.join(HOME, 'social-autoposter');
const PROFILES_DIR = path.join(HOME, '.claude', 'browser-profiles');
const PLATFORMS = ['reddit', 'twitter', 'linkedin'];

// Find a working Chromium/Chrome binary
function findChromium() {
  // Environment override (used in Freestyle VMs)
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
    return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  }
  const candidates = [
    '/usr/bin/chromium',                                          // Debian/VM
    '/usr/bin/chromium-browser',                                  // Ubuntu
    '/usr/bin/google-chrome',                                     // Linux Chrome
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', // macOS
    '/Applications/Chromium.app/Contents/MacOS/Chromium',         // macOS Chromium
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

// Get the CDP WebSocket URL from a running Chrome instance
function getCdpWsUrl(port) {
  return new Promise((resolve, reject) => {
    const req = http.get(`http://127.0.0.1:${port}/json/version`, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        try {
          const info = JSON.parse(data);
          resolve(info.webSocketDebuggerUrl);
        } catch (e) {
          reject(new Error(`Failed to parse CDP response: ${e.message}`));
        }
      });
    });
    req.on('error', reject);
    req.setTimeout(3000, () => { req.destroy(); reject(new Error('CDP timeout')); });
  });
}

// Minimal CDP client over WebSocket (no dependencies beyond Node stdlib)
function cdpSend(wsUrl, method, params = {}) {
  // Use dynamic import for the built-in WebSocket (Node 22+) or ws package
  return new Promise(async (resolve, reject) => {
    let WebSocketClass;
    try {
      // Node 22+ has global WebSocket
      if (typeof globalThis.WebSocket !== 'undefined') {
        WebSocketClass = globalThis.WebSocket;
      } else {
        // Fallback to ws package (installed globally in VM)
        WebSocketClass = require('ws');
      }
    } catch {
      reject(new Error('No WebSocket implementation found. Install ws: npm install -g ws'));
      return;
    }

    const ws = new WebSocketClass(wsUrl);
    const id = 1;
    let resolved = false;

    const timer = setTimeout(() => {
      if (!resolved) { resolved = true; ws.close(); reject(new Error(`CDP ${method} timeout`)); }
    }, 15000);

    ws.on('open', () => {
      ws.send(JSON.stringify({ id, method, params }));
    });

    ws.on('message', (raw) => {
      if (resolved) return;
      const msg = JSON.parse(typeof raw === 'string' ? raw : raw.toString());
      if (msg.id === id) {
        resolved = true;
        clearTimeout(timer);
        ws.close();
        if (msg.error) {
          reject(new Error(`CDP error: ${msg.error.message}`));
        } else {
          resolve(msg.result);
        }
      }
    });

    ws.on('error', (err) => {
      if (!resolved) { resolved = true; clearTimeout(timer); reject(err); }
    });
  });
}

// Convert CDP cookie format to Playwright storageState format
function cdpToPlaywright(cookie) {
  const out = {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path || '/',
    expires: cookie.expires || -1,
    httpOnly: Boolean(cookie.httpOnly),
    secure: Boolean(cookie.secure),
  };
  if (cookie.sameSite && ['Strict', 'Lax', 'None'].includes(cookie.sameSite)) {
    out.sameSite = cookie.sameSite;
  }
  return out;
}

// Convert Playwright cookie format back to CDP format for setCookies
function playwrightToCdp(cookie) {
  const out = {
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path || '/',
    httpOnly: Boolean(cookie.httpOnly),
    secure: Boolean(cookie.secure),
  };
  if (cookie.expires && cookie.expires > 0) {
    out.expires = cookie.expires;
  }
  if (cookie.sameSite) {
    out.sameSite = cookie.sameSite;
  }
  return out;
}

// Launch Chrome with a profile, run a callback with the CDP WebSocket URL, then kill it
async function withChrome(profileDir, callback) {
  const chromium = findChromium();
  if (!chromium) {
    throw new Error('No Chromium or Chrome binary found');
  }

  // Pick a random port to avoid collisions
  const port = 19000 + Math.floor(Math.random() * 10000);

  const args = [
    '--headless=new',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-software-rasterizer',
    `--user-data-dir=${profileDir}`,
    `--remote-debugging-port=${port}`,
    '--window-size=800,600',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
  ];

  const proc = spawn(chromium, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    detached: false,
  });

  // Wait for CDP to be ready (up to 10s)
  let wsUrl = null;
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 250));
    try {
      wsUrl = await getCdpWsUrl(port);
      if (wsUrl) break;
    } catch {
      // not ready yet
    }
  }

  if (!wsUrl) {
    proc.kill('SIGKILL');
    throw new Error(`Chrome failed to start CDP on port ${port}`);
  }

  try {
    return await callback(wsUrl);
  } finally {
    proc.kill('SIGTERM');
    // Give it a moment to flush profile data to disk
    await new Promise(r => setTimeout(r, 500));
    try { proc.kill('SIGKILL'); } catch {}
  }
}

// ── Export ──

async function exportCookies(platforms, outputDir) {
  const chromium = findChromium();
  if (!chromium) {
    console.error('Error: No Chromium or Chrome binary found.');
    console.error('On macOS, install Google Chrome. On Linux, install chromium.');
    process.exit(1);
  }
  console.log(`Using browser: ${chromium}`);

  for (const platform of platforms) {
    const profileDir = path.join(PROFILES_DIR, platform);
    if (!fs.existsSync(profileDir)) {
      console.log(`  ${platform}: no profile at ${profileDir}, skipping`);
      continue;
    }

    // Check if profile has any real data (not just an empty dir)
    const contents = fs.readdirSync(profileDir);
    if (contents.length === 0) {
      console.log(`  ${platform}: empty profile, skipping`);
      continue;
    }

    process.stdout.write(`  ${platform}: launching browser...`);

    try {
      const result = await withChrome(profileDir, async (wsUrl) => {
        process.stdout.write(' extracting cookies...');
        const { cookies } = await cdpSend(wsUrl, 'Storage.getCookies');
        return cookies;
      });

      const exported = result.map(cdpToPlaywright);
      const outFile = path.join(outputDir, `cookies-${platform}.json`);
      fs.writeFileSync(outFile, JSON.stringify({ cookies: exported, origins: [] }, null, 2));
      console.log(` ${exported.length} cookies -> ${outFile}`);
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
    console.error('On macOS, install Google Chrome. On Linux, install chromium.');
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
      await withChrome(profileDir, async (wsUrl) => {
        process.stdout.write(` injecting ${cookies.length} cookies...`);
        await cdpSend(wsUrl, 'Storage.setCookies', { cookies });
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
  const mode = args[0]; // 'export' or 'import'

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
    console.log('Export extracts cookies from ~/.claude/browser-profiles/{reddit,twitter,linkedin}');
    console.log('Import injects cookies from cookies-{platform}.json files into browser profiles');
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('Fatal:', err.message);
  process.exit(1);
});
