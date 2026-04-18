'use strict';

const path = require('path');
const fs = require('fs');

function renderService(job, env) {
  return `[Unit]
Description=${job.label}

[Service]
Type=oneshot
ExecStart=/bin/bash ${job.script}
Environment=PATH=${env.path}
Environment=HOME=${env.home}
StandardOutput=append:${job.stdoutLog}
StandardError=append:${job.stderrLog}
`;
}

function renderTimer(job) {
  const onActive = job.runAtLoad ? 0 : job.interval;
  return `[Unit]
Description=Timer for ${job.label}

[Timer]
OnActiveSec=${onActive}s
OnUnitActiveSec=${job.interval}s
Unit=${job.label}.service

[Install]
WantedBy=timers.target
`;
}

function unitBase(job) {
  return job.file.replace(/\.plist$/, '').replace(/\.service$|\.timer$/, '');
}

function generate({ jobs, outDir, env }) {
  fs.mkdirSync(outDir, { recursive: true });
  const written = [];
  for (const job of jobs) {
    const base = unitBase(job);
    const serviceTarget = path.join(outDir, `${base}.service`);
    const timerTarget = path.join(outDir, `${base}.timer`);
    fs.writeFileSync(serviceTarget, renderService(job, env));
    fs.writeFileSync(timerTarget, renderTimer(job));
    written.push(serviceTarget, timerTarget);
  }
  return written;
}

function defaultEnv({ home, nodeBin }) {
  const dirs = new Set();
  if (nodeBin) dirs.add(nodeBin);
  dirs.add('/usr/local/bin');
  dirs.add('/usr/bin');
  dirs.add('/bin');
  return {
    home,
    path: [...dirs].join(':'),
  };
}

module.exports = { renderService, renderTimer, generate, defaultEnv, unitBase };
