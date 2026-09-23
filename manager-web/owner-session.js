'use strict';

/*
 * Local-owner bridge. This is intentionally a Node CLI/controller, not a
 * browser bundle: browser JavaScript must never receive a private key.
 *
 * Safe defaults:
 *   node manager-web/owner-session.js                 # synthetic status
 *   node manager-web/owner-session.js --live --confirm-live
 *
 * Live mode requires OWNER_SESSION_ENDPOINT. The key is read with echo
 * disabled, kept in a Buffer for the shortest possible scope, and cleared in
 * finally. No key or response body is written to disk or JSON fixtures.
 */

const readline = require('node:readline');

const LIVE_CONFIRMATION = 'LIVE';
const VALID_MODES = new Set(['mock', 'live']);

function parseArgs(argv) {
  const args = new Set(argv.slice(2));
  const mode = args.has('--live') ? 'live' : 'mock';
  if (args.has('--mock')) return {mode: 'mock', confirmed: false};
  return {mode, confirmed: args.has('--confirm-live')};
}

function scrub(buffer) {
  if (Buffer.isBuffer(buffer)) buffer.fill(0);
}

function createOwnerSession({mode = 'mock', liveConnector = null} = {}) {
  if (!VALID_MODES.has(mode)) throw new Error('Unsupported owner-session mode.');
  let currentMode = mode;
  let status = mode === 'mock' ? 'draft' : 'pending-local-verification';
  let employerResponse = null;

  return Object.freeze({
    getState() {
      return Object.freeze({
        mode: currentMode,
        status,
        submitted: false,
        employerResponse,
        keyPresent: false,
      });
    },
    async verifyLive(privateKey) {
      if (currentMode !== 'live') throw new Error('Live mode is not enabled.');
      if (typeof privateKey !== 'string' || privateKey.length === 0) {
        throw new Error('A private key is required for local verification.');
      }
      if (typeof liveConnector !== 'function') {
        throw new Error('No live connector configured; no external request was made.');
      }

      const keyBuffer = Buffer.from(privateKey, 'utf8');
      status = 'verification-started';
      try {
        // The connector is the only function allowed to receive the key.
        const result = await liveConnector(keyBuffer);
        status = 'verified-locally';
        employerResponse = result && typeof result.status === 'string'
          ? result.status
          : null;
        return this.getState();
      } finally {
        scrub(keyBuffer);
      }
    },
  });
}

function parseAllowedHosts(value) {
  const hosts = Array.isArray(value) ? value : String(value || '').split(',');
  return new Set(hosts.map((host) => host.trim().toLowerCase()).filter(Boolean));
}

function validateOwnerEndpoint(endpoint, allowedHosts) {
  if (!endpoint) throw new Error('OWNER_SESSION_ENDPOINT is required for live mode.');
  let url;
  try {
    url = new URL(endpoint);
  } catch {
    throw new Error('OWNER_SESSION_ENDPOINT must be a valid HTTPS URL.');
  }
  if (url.protocol !== 'https:' || url.username || url.password) {
    throw new Error('OWNER_SESSION_ENDPOINT must use HTTPS without embedded credentials.');
  }
  const allowlist = parseAllowedHosts(allowedHosts);
  if (allowlist.size === 0) {
    throw new Error('OWNER_SESSION_ALLOWED_HOSTS is required for live mode.');
  }
  if (!allowlist.has(url.host.toLowerCase())) {
    throw new Error('OWNER_SESSION_ENDPOINT host is not allowlisted.');
  }
  return url.href;
}

function createLiveConnector({endpoint, allowedHosts, fetchImpl = globalThis.fetch} = {}) {
  const verifiedEndpoint = validateOwnerEndpoint(endpoint, allowedHosts);
  if (typeof fetchImpl !== 'function') throw new Error('Fetch is unavailable.');

  return async function liveConnector(privateKeyBuffer) {
    const response = await fetchImpl(verifiedEndpoint, {
      method: 'POST',
      headers: {Authorization: `Bearer ${privateKeyBuffer.toString('utf8')}`},
      body: JSON.stringify({check: 'owner-session'}),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) throw new Error(`Live verification failed with HTTP ${response.status}.`);
    return {status: `http-${response.status}`};
  };
}

function askHidden(question, input = process.stdin) {
  const silentOutput = {write() {}};
  const rl = readline.createInterface({input, output: silentOutput, terminal: false});
  return new Promise((resolve) => rl.question(question, (answer) => {
    rl.close();
    resolve(answer);
  }));
}

async function runCli(argv = process.argv, env = process.env) {
  const {mode, confirmed} = parseArgs(argv);
  if (mode === 'mock') {
    process.stdout.write(`${JSON.stringify(createOwnerSession().getState())}\n`);
    return;
  }
  if (!confirmed) throw new Error('Live mode requires --confirm-live.');

  const session = createOwnerSession({
    mode: 'live',
    liveConnector: createLiveConnector({
      endpoint: env.OWNER_SESSION_ENDPOINT,
      allowedHosts: env.OWNER_SESSION_ALLOWED_HOSTS,
    }),
  });
  process.stdout.write('Private key (input hidden, memory only): ');
  let privateKey = await askHidden('');
  try {
    const state = await session.verifyLive(privateKey);
    process.stdout.write(`${JSON.stringify(state)}\n`);
  } finally {
    // Drop the local binding as soon as the verification attempt ends.
    privateKey = '';
  }
}

module.exports = {
  createLiveConnector,
  createOwnerSession,
  parseAllowedHosts,
  parseArgs,
  runCli,
  validateOwnerEndpoint,
};

if (require.main === module) {
  runCli().catch((error) => {
    process.stderr.write(`owner-session: ${error.message}\n`);
    process.exitCode = 1;
  });
}
