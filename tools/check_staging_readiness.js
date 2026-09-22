'use strict';

/*
 * Non-destructive staging pre-flight. It never sends a bearer token, request
 * body, application, or private key. With the gate closed it performs zero
 * network calls and reports mock fallback.
 */

const {parseRuntimeConfig} = require('../manager-web/live-validation');

function endpointsFrom(env) {
  return String(env.STAGING_HEALTH_ENDPOINTS || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
}

function validateEndpoint(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error(`Unsupported protocol for ${value}`);
  }
  return url;
}

async function checkEndpoint(value, fetchImpl = globalThis.fetch) {
  const url = validateEndpoint(value);
  const response = await fetchImpl(url, {
    method: 'HEAD',
    headers: {Accept: 'application/health+json, application/json;q=0.9'},
    signal: AbortSignal.timeout(10000),
  });
  return {endpoint: url.origin + url.pathname, status: response.status, ok: response.ok};
}

async function main(env = process.env, fetchImpl = globalThis.fetch) {
  const config = parseRuntimeConfig(env);
  const endpoints = config.healthEndpoints;
  if (config.mode === 'mock') {
    return {mode: 'mock', networkAttempted: false, checks: [], configError: config.configError};
  }
  if (!endpoints.length) {
    return {mode: 'live-staging', networkAttempted: false, checks: [], error: 'STAGING_HEALTH_ENDPOINTS is empty'};
  }

  const checks = [];
  for (const endpoint of endpoints) {
    try {
      checks.push(await checkEndpoint(endpoint, fetchImpl));
    } catch (error) {
      checks.push({endpoint, ok: false, error: error instanceof Error ? error.message : 'probe failed'});
    }
  }
  return {mode: 'live-staging', networkAttempted: true, checks};
}

if (require.main === module) {
  main().then((result) => {
    process.stdout.write(`${JSON.stringify(result)}\n`);
    process.exitCode = result.error || result.checks.some((check) => !check.ok) ? 1 : 0;
  }).catch((error) => {
    process.stderr.write(`staging-readiness: ${error.message}\n`);
    process.exitCode = 1;
  });
}

module.exports = {checkEndpoint, endpointsFrom, main, validateEndpoint};
