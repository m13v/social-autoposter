'use strict';

const platform = require('../platform');
const launchd = require('./launchd');
const systemd = require('./systemd');

function driverFor(name) {
  const key = name || platform.scheduler();
  if (key === 'launchd') return launchd;
  if (key === 'systemd') return systemd;
  throw new Error(`no scheduler driver for: ${key}`);
}

module.exports = { driverFor, launchd, systemd };
