#!/usr/bin/env node
'use strict';

/**
 * Export and import browser cookies for social-autoposter profiles.
 *
 * Export: connects to running Playwright browser instances via CDP to extract
 * cookies. Falls back to launching headless Chrome if no running browser found.
 *
 * Import: launches headless Chromium per platform profile, injects cookies
 * via CDP (Network.setCookies) so they persist to the profile on disk.
 */

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');

const HOME = os.homedir();
const DEST = path.join(HOME, 'social-autoposter');
const PROFILES_DIR = path.join(HOME, '.claude', 'browser-profiles');
const PLATFORMS = ['reddit', 'twitter', 'linkedin'];

// ── WebSocket ──

let WS;
try {
  if (typeof globalThis.WebSocket !== 'undefined') {
    WS = globalThis.WebSocket;
  } else {
    try { WS = require('ws'); } catch {
      try { WS = require(path.join('/usr/lib/node_modules', 'ws')); } catch {}
    }
  }
} catch {}

// ── CDP helpers ──

function httpGet(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let d = '';
      res.on('data', c => { d += c; });
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch (e) { reject(e); } });
    });
    req.on('error', reject);
    req.setTimeout(3000, () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function cdpSend(wsUrl, method, params = {}) {
  if (!WS) throw new Error('No WebSocket. Install ws: npm install -g ws');
  return new Promise((resolve, reject) => {
    const ws = new WS(wsUrl);
    let done = false;
    const timer = setTimeout(() => { if (!done) { done = true; ws.close(); reject(new Error(`CDP ${method} timeout`)); } }, 15000);
    ws.on('open', () => ws.send(JSON.stringify({ id: 1, method, params })));
    ws.on('message', (raw) => {
      if (done) return;
      const msg = JSON.parse(typeof raw === 'string' ? raw : raw.toString());
      if (msg.id === 1) {
        done = true; clearTimeout(timer); ws.close();
        msg.error ? reject(new Error(`CDP: ${msg.error.message}`)) : resolve(msg.result);
      }
    });
    ws.on('error', (e) => { if (!done) { done = true; clearTimeout(timer); reject(e); } });
  });
}

// ── Find running browser CDP port ──

function findRunningBrowserPort(platform) {
  const profileDir = path.join(PROFILES_DIR, platform);
  // Parse ps output to find Chrome with this profile's user-data-dir
  const ps = spawnSync('ps', ['aux'], { encoding: 'utf8', maxBuffer: 10 * 1024 * 1024 });
  if (ps.status !== 0) return null;

  for (const line of ps.stdout.split('\n')) {
    if (!line.includes(`user-data-dir=${profileDir}`) || !line.includes('--remote-debugging-port=')) continue;
    // Only match the main Chrome process (not renderer helpers)
    if (line.includes('--type=')) continue;
    const match = line.match(/--remote-debugging-port=(\d+)/);
    if (match) return parseInt(match[1], 10);
  }
  return null;
}

// ── Cookie format conversion ──

function cdpToPlaywright(c) {
  const out = {
    name: c.name, value: c.value, domain: c.domain,
    path: c.path || '/', expires: c.expires || -1,
    httpOnly: Boolean(c.httpOnly), secure: Boolean(c.secure),
  };
  if (c.sameSite && ['Strict', 'Lax', 'None'].includes(c.sameSite)) out.sameSite = c.sameSite;
  return out;
}

function playwrightToCdp(c) {
  const out = {
    name: c.name, value: c.value, domain: c.domain,
    path: c.path || '/', httpOnly: Boolean(c.httpOnly), secure: Boolean(c.secure),
  };
  if (c.expires && c.expires > 0) out.expires = c.expires;
  if (c.sameSite) out.sameSite = c.sameSite;
  return out;
}

// ── Chrome launcher (for import or fallback export) ──

function findChromium() {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH;
  const candidates = [
    '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
  ];
  for (const c of candidates) { if (fs.existsSync(c)) return c; }
  return null;
}

async function withFreshChrome(profileDir, callback) {
  const chromium = findChromium();
  if (!chromium) throw new Error('No Chromium/Chrome binary found');

  const port = 19000 + Math.floor(Math.random() * 10000);
  const proc = spawn(chromium, [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-software-rasterizer',
    `--user-data-dir=${profileDir}`, `--remote-debugging-port=${port}`,
    '--window-size=800,600', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--password-store=basic', '--use-mock-keychain',
  ], { stdio: ['ignore', 'pipe', 'pipe'], detached: false });

  let wsUrl = null;
  for (let i = 0; i < 40; i++) {
    await new Promise(r => setTimeout(r, 250));
    try { wsUrl = (await httpGet(`http://127.0.0.1:${port}/json/version`)).webSocketDebuggerUrl; if (wsUrl) break; } catch {}
  }
  if (!wsUrl) { proc.kill('SIGKILL'); throw new Error(`Chrome CDP failed on port ${port}`); }

  try { return await callback(wsUrl, port); }
  finally {
    proc.kill('SIGTERM');
    await new Promise(r => setTimeout(r, 500));
    try { proc.kill('SIGKILL'); } catch {}
  }
}

// ── Export ──

async function exportCookiesFromCdp(port) {
  // Find a page target to use Network.getAllCookies
  const pages = await httpGet(`http://127.0.0.1:${port}/json/list`);
  const page = pages.find(p => p.type === 'page');
  if (!page) throw new Error('No page targets on CDP port ' + port);

  await cdpSend(page.webSocketDebuggerUrl, 'Network.enable');
  const { cookies } = await cdpSend(page.webSocketDebuggerUrl, 'Network.getAllCookies');
  return cookies.map(cdpToPlaywright);
}

async function exportCookies(platforms, outputDir) {
  if (!WS) {
    console.error('Error: WebSocket not available. Install ws: npm install -g ws');
    process.exit(1);
  }
  fs.mkdirSync(outputDir, { recursive: true });

  for (const platform of platforms) {
    const profileDir = path.join(PROFILES_DIR, platform);
    if (!fs.existsSync(profileDir)) {
      console.log(`  ${platform}: no profile at ${profileDir}, skipping`);
      continue;
    }

    // Try to find a running browser first (fast path)
    const runningPort = findRunningBrowserPort(platform);
    if (runningPort) {
      process.stdout.write(`  ${platform}: found running browser on port ${runningPort}...`);
      try {
        const cookies = await exportCookiesFromCdp(runningPort);
        const outFile = path.join(outputDir, `cookies-${platform}.json`);
        fs.writeFileSync(outFile, JSON.stringify({ cookies, origins: [] }, null, 2));
        console.log(` ${cookies.length} cookies -> ${outFile}`);
        continue;
      } catch (err) {
        console.log(` failed (${err.message}), trying headless...`);
      }
    }

    // Fallback: launch headless Chrome with the profile
    // This only works if the profile is not locked by another Chrome
    const lockFile = path.join(profileDir, 'SingletonLock');
    const hasLock = (() => { try { fs.lstatSync(lockFile); return true; } catch { return false; } })();
    if (hasLock) {
      console.log(`  ${platform}: profile is locked but no running browser found; cannot export`);
      console.log(`    (the browser may have crashed; remove ${lockFile} and retry)`);
      continue;
    }

    process.stdout.write(`  ${platform}: launching headless browser...`);
    try {
      const cookies = await withFreshChrome(profileDir, async (wsUrl, port) => {
        process.stdout.write(' extracting...');
        return await exportCookiesFromCdp(port);
      });
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
  if (!chromium) { console.error('Error: No Chromium/Chrome binary found.'); process.exit(1); }
  if (!WS) { console.error('Error: WebSocket not available. Install ws: npm install -g ws'); process.exit(1); }
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

    // Check if profile is locked (browser already running)
    const lockFile = path.join(profileDir, 'SingletonLock');
    const hasLock = (() => { try { fs.lstatSync(lockFile); return true; } catch { return false; } })();
    if (hasLock) {
      // Try to inject into the running browser
      const runningPort = findRunningBrowserPort(platform);
      if (runningPort) {
        process.stdout.write(`  ${platform}: injecting ${cookies.length} cookies into running browser...`);
        try {
          const pages = await httpGet(`http://127.0.0.1:${runningPort}/json/list`);
          const page = pages.find(p => p.type === 'page');
          if (!page) throw new Error('No page targets');
          await cdpSend(page.webSocketDebuggerUrl, 'Network.enable');
          await cdpSend(page.webSocketDebuggerUrl, 'Network.setCookies', { cookies });
          console.log(' done');
          continue;
        } catch (err) {
          console.log(` failed (${err.message})`);
          continue;
        }
      }
      console.log(`  ${platform}: profile is locked; cannot import`);
      continue;
    }

    process.stdout.write(`  ${platform}: launching browser...`);
    try {
      await withFreshChrome(profileDir, async (wsUrl, port) => {
        process.stdout.write(` injecting ${cookies.length} cookies...`);
        const pages = await httpGet(`http://127.0.0.1:${port}/json/list`);
        const page = pages.find(p => p.type === 'page');
        if (!page) throw new Error('No page targets');
        await cdpSend(page.webSocketDebuggerUrl, 'Network.enable');
        await cdpSend(page.webSocketDebuggerUrl, 'Network.setCookies', { cookies });
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
    console.log('Export extracts cookies from ~/.claude/browser-profiles/{reddit,twitter,linkedin}');
    console.log('Import injects cookies from cookies-{platform}.json into browser profiles');
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('Fatal:', err.message);
  process.exit(1);
});
