'use strict';

const assert = require('assert');
const { renderPlist } = require('../bin/scheduler/launchd');

function test(name, fn) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    console.error(`FAIL ${name}: ${e.message}`);
    process.exitCode = 1;
  }
}

// Byte-for-byte expected output. This must match the XML that
// bin/cli.js generatePlists() currently emits so Phase B can
// swap in the scheduler driver with zero behavioral change.
const EXPECTED_STATS_PLIST = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>com.m13v.social-stats</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>/bin/bash</string>
\t\t<string>/Users/test/social-autoposter/skill/stats.sh</string>
\t</array>
\t<key>StartInterval</key>
\t<integer>21600</integer>
\t<key>StandardOutPath</key>
\t<string>/Users/test/social-autoposter/skill/logs/launchd-stats-stdout.log</string>
\t<key>StandardErrorPath</key>
\t<string>/Users/test/social-autoposter/skill/logs/launchd-stats-stderr.log</string>
\t<key>EnvironmentVariables</key>
\t<dict>
\t\t<key>PATH</key>
\t\t<string>/usr/test/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
\t\t<key>HOME</key>
\t\t<string>/Users/test</string>
\t</dict>
\t<key>RunAtLoad</key>
\t<false/>
</dict>
</plist>
`;

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
  home: '/Users/test',
  path: '/usr/test/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin',
};

test('renderPlist matches cli.js generatePlists() output byte-for-byte', () => {
  const got = renderPlist(STATS_JOB, STATS_ENV);
  if (got !== EXPECTED_STATS_PLIST) {
    const gotLines = got.split('\n');
    const expLines = EXPECTED_STATS_PLIST.split('\n');
    for (let i = 0; i < Math.max(gotLines.length, expLines.length); i++) {
      if (gotLines[i] !== expLines[i]) {
        throw new Error(
          `line ${i + 1} differs\n  expected: ${JSON.stringify(expLines[i])}\n  got:      ${JSON.stringify(gotLines[i])}`
        );
      }
    }
    throw new Error('unknown diff');
  }
});
