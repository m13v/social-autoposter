'use strict';

const path = require('path');
const fs = require('fs');
const platform = require('../platform');

function renderPlist(job, env) {
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>${job.label}</string>
\t<key>ProgramArguments</key>
\t<array>
\t\t<string>/bin/bash</string>
\t\t<string>${job.script}</string>
\t</array>
\t<key>StartInterval</key>
\t<integer>${job.interval}</integer>
\t<key>StandardOutPath</key>
\t<string>${job.stdoutLog}</string>
\t<key>StandardErrorPath</key>
\t<string>${job.stderrLog}</string>
\t<key>EnvironmentVariables</key>
\t<dict>
\t\t<key>PATH</key>
\t\t<string>${env.path}</string>
\t\t<key>HOME</key>
\t\t<string>${env.home}</string>
\t</dict>
\t<key>RunAtLoad</key>
\t<${job.runAtLoad}/>
</dict>
</plist>
`;
}

function generate({ jobs, outDir, env }) {
  fs.mkdirSync(outDir, { recursive: true });
  const written = [];
  for (const job of jobs) {
    const xml = renderPlist(job, env);
    const target = path.join(outDir, job.file);
    fs.writeFileSync(target, xml);
    written.push(target);
  }
  return written;
}

function defaultEnv({ home, nodeBin }) {
  return {
    home,
    path: platform.launchdPath(nodeBin),
  };
}

module.exports = { renderPlist, generate, defaultEnv };
