'use strict';

const { renderService, renderTimer, unitBase } = require('../bin/scheduler/systemd');

function test(name, fn) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    console.error(`FAIL ${name}: ${e.message}`);
    process.exitCode = 1;
  }
}

const STATS_JOB = {
  file: 'com.m13v.social-stats.plist',
  label: 'com.m13v.social-stats',
  script: '/Users/test/social-autoposter/skill/stats.sh',
  interval: 21600,
  runAtLoad: false,
  stdoutLog: '/Users/test/social-autoposter/skill/logs/launchd-stats-stdout.log',
  stderrLog: '/Users/test/social-autoposter/skill/logs/launchd-stats-stderr.log',
};

const STATS_ENV = {
  home: '/home/test',
  path: '/opt/node/bin:/usr/local/bin:/usr/bin:/bin',
};

const EXPECTED_SERVICE = `[Unit]
Description=com.m13v.social-stats

[Service]
Type=oneshot
ExecStart=/bin/bash /Users/test/social-autoposter/skill/stats.sh
Environment=PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=/home/test
StandardOutput=append:/Users/test/social-autoposter/skill/logs/launchd-stats-stdout.log
StandardError=append:/Users/test/social-autoposter/skill/logs/launchd-stats-stderr.log
`;

const EXPECTED_TIMER = `[Unit]
Description=Timer for com.m13v.social-stats

[Timer]
OnActiveSec=21600s
OnUnitActiveSec=21600s
Unit=com.m13v.social-stats.service

[Install]
WantedBy=timers.target
`;

function diffLines(got, expected, label) {
  const g = got.split('\n');
  const e = expected.split('\n');
  for (let i = 0; i < Math.max(g.length, e.length); i++) {
    if (g[i] !== e[i]) {
      throw new Error(
        `${label} line ${i + 1} differs\n  expected: ${JSON.stringify(e[i])}\n  got:      ${JSON.stringify(g[i])}`
      );
    }
  }
}

test('renderService emits expected .service unit', () => {
  const got = renderService(STATS_JOB, STATS_ENV);
  if (got !== EXPECTED_SERVICE) diffLines(got, EXPECTED_SERVICE, 'service');
});

test('renderTimer emits expected .timer unit (runAtLoad=false defers first fire)', () => {
  const got = renderTimer(STATS_JOB);
  if (got !== EXPECTED_TIMER) diffLines(got, EXPECTED_TIMER, 'timer');
});

test('renderTimer with runAtLoad=true fires immediately (OnActiveSec=0s)', () => {
  const job = Object.assign({}, STATS_JOB, { runAtLoad: true });
  const got = renderTimer(job);
  if (!got.includes('OnActiveSec=0s')) {
    throw new Error(`expected OnActiveSec=0s when runAtLoad=true, got:\n${got}`);
  }
});

test('unitBase strips .plist suffix from job.file', () => {
  const base = unitBase({ file: 'com.m13v.social-stats.plist' });
  if (base !== 'com.m13v.social-stats') throw new Error(`got: ${base}`);
});
