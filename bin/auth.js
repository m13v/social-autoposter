'use strict';

// Firebase auth for the dashboard when CLIENT_MODE=1.
// When CLIENT_MODE is unset (local operator use), authorize() is a no-op
// and returns a synthetic admin principal so every existing route keeps
// working unchanged.

let _admin = null;

function getAdmin() {
  if (_admin) return _admin;
  const admin = require('firebase-admin');
  if (!admin.apps.length) {
    const projectId = process.env.FIREBASE_PROJECT_ID || 's4l-app-prod';
    admin.initializeApp({ projectId });
  }
  _admin = admin;
  return _admin;
}

const CLIENT_MODE = process.env.CLIENT_MODE === '1';

// Routes that require an admin claim even when authenticated.
// Clients authenticated with only a project scope cannot hit these.
const ADMIN_ONLY_PATTERNS = [
  /^\/api\/pause$/,
  /^\/api\/resume$/,
  /^\/api\/jobs\/[^/]+\/(toggle|run|stop|interval)$/,
  /^\/api\/phase\/[^/]+\/interval$/,
  /^\/api\/config$/,
  /^\/api\/env$/,
  /^\/api\/logs(\/.*)?$/,
  /^\/api\/webhooks(\/.*)?$/,
  /^\/api\/status$/,
  /^\/api\/pending$/,
];

function isAdminOnly(pathname) {
  return ADMIN_ONLY_PATTERNS.some(re => re.test(pathname));
}

function extractToken(req) {
  const h = req.headers['authorization'] || req.headers['Authorization'];
  if (!h) return null;
  const m = String(h).match(/^Bearer\s+(.+)$/);
  return m ? m[1].trim() : null;
}

async function verifyAuth(req, pathname) {
  if (!CLIENT_MODE) {
    return { ok: true, user: { uid: 'local', email: 'local', admin: true, projects: [] } };
  }
  const token = extractToken(req);
  if (!token) {
    console.warn(JSON.stringify({ event: 'auth_reject', reason: 'missing_token', path: pathname, authHeaderPresent: !!(req.headers['authorization'] || req.headers['Authorization']) }));
    return { ok: false, status: 401, error: 'missing_token' };
  }
  try {
    const decoded = await getAdmin().auth().verifyIdToken(token);
    const user = {
      uid: decoded.uid,
      email: decoded.email || null,
      admin: decoded.admin === true,
      projects: Array.isArray(decoded.projects) ? decoded.projects : [],
    };
    if (isAdminOnly(pathname) && !user.admin) {
      console.warn(JSON.stringify({ event: 'auth_reject', reason: 'admin_required', path: pathname, uid: user.uid, email: user.email }));
      return { ok: false, status: 403, error: 'admin_required' };
    }
    return { ok: true, user };
  } catch (e) {
    console.warn(JSON.stringify({ event: 'auth_reject', reason: 'invalid_token', path: pathname, errCode: e.code || null, errMessage: e.message, tokenHead: token.slice(0, 20), tokenLen: token.length }));
    return { ok: false, status: 401, error: 'invalid_token', detail: e.message };
  }
}

function scopedProjects(user, requested) {
  if (user.admin) {
    return requested && requested !== 'all' ? [requested] : null;
  }
  if (!user.projects.length) return [];
  if (!requested || requested === 'all') return user.projects.slice();
  return user.projects.includes(requested) ? [requested] : [];
}

// Returns { clause, ok } where clause is a " AND <column> IN ('a','b')" fragment
// (possibly empty when admin + no filter) and ok=false means the user has no
// access (non-admin with empty projects claim) so the handler should 403.
// Project names are validated against a conservative charset to prevent injection.
const PROJECT_NAME_RE = /^[A-Za-z0-9_\-]{1,64}$/;

function projectClause(user, column, requested) {
  const list = scopedProjects(user, requested);
  if (list === null) return { clause: '', ok: true }; // admin, unfiltered
  const safe = list.filter(p => PROJECT_NAME_RE.test(p));
  if (!safe.length) return { clause: '', ok: false };
  const quoted = safe.map(p => `'${p.replace(/'/g, "''")}'`).join(',');
  return { clause: ` AND ${column} IN (${quoted})`, ok: true, list: safe };
}

module.exports = {
  CLIENT_MODE,
  verifyAuth,
  isAdminOnly,
  scopedProjects,
  projectClause,
};
