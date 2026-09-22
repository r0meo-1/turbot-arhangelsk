'use strict';

const {randomUUID} = require('node:crypto');
const readline = require('node:readline');

/*
 * Validation orchestration scaffold. It deliberately does not own a key and
 * is not served by Flask. A UI or CLI can use this hook to show that a real
 * check is waiting for a human owner session without turning that state into
 * a fake employer response.
 */

const LIVE_CHECKS = Object.freeze({
  IPHONE: 'iphone',
  PRIVATE_KEY: 'private-key',
});

function isLiveStagingAllowed(env = process.env) {
  return env.LUNA_ALLOW_LIVE_STAGING === 'true';
}

function parseRuntimeConfig(env = process.env) {
  if (!isLiveStagingAllowed(env)) {
    return Object.freeze({mode: 'mock', liveStagingAllowed: false, healthEndpoints: [], ownerSessionConfigured: false, configError: null});
  }
  const healthEndpoints = String(env.STAGING_HEALTH_ENDPOINTS || '').split(',').map((value) => value.trim()).filter(Boolean);
  if (healthEndpoints.some((value) => !/^https?:\/\//i.test(value))) {
    return Object.freeze({mode: 'mock', liveStagingAllowed: false, healthEndpoints: [], ownerSessionConfigured: false, configError: 'invalid staging endpoint'});
  }
  return Object.freeze({mode: 'live-staging', liveStagingAllowed: true, healthEndpoints, ownerSessionConfigured: Boolean(String(env.OWNER_SESSION_ENDPOINT || '').trim()), configError: null});
}

function listenForManualInput({requestId, submit, input = process.stdin, output = process.stdout} = {}) {
  if (typeof submit !== 'function') throw new Error('submit callback is required.');
  output.write(`Owner input for ${requestId} (hidden, memory only): `);
  const silentOutput = {write() {}};
  const rl = readline.createInterface({input, output: silentOutput, terminal: false});
  return new Promise((resolve, reject) => {
    rl.question('', async (value) => {
      rl.close();
      const buffer = Buffer.from(value, 'utf8');
      try {
        resolve(await submit(buffer));
      } catch (error) {
        reject(error);
      } finally {
        buffer.fill(0);
      }
    });
  });
}

function createValidationHooks({
  ownerSession,
  onManualIntervention = () => {},
  onManualInput = async () => {},
  env = process.env,
  telemetry = null,
} = {}) {
  if (!ownerSession || typeof ownerSession.getState !== 'function') {
    throw new Error('ownerSession is required.');
  }

  const pending = new Map();
  const runtimeConfig = parseRuntimeConfig(env);
  const liveStagingAllowed = runtimeConfig.mode === 'live-staging';
  const emit = (phase, details) => {
    if (telemetry && typeof telemetry.emit === 'function') telemetry.emit(phase, details);
  };

  return Object.freeze({
    requestManualCheck(check, details = '') {
      if (!Object.values(LIVE_CHECKS).includes(check)) {
        throw new Error('Unsupported live check.');
      }

      const requestId = randomUUID();
      if (!liveStagingAllowed) {
        const fallback = Object.freeze({
          requestId,
          check,
          phase: 'mock-fallback',
          requiresManualInput: false,
          fallback: 'synthetic-fixture',
          details: String(details),
          session: ownerSession.getState(),
          runtime: runtimeConfig,
        });
        onManualIntervention(fallback);
        emit(fallback.phase, fallback);
        return fallback;
      }

      const event = Object.freeze({
        requestId,
        check,
        phase: 'awaiting-owner-session',
        requiresManualInput: true,
        details: String(details),
        session: ownerSession.getState(),
      });

      // The callback receives status only; it never receives a private key.
      onManualIntervention(event);
      emit(event.phase, event);
      pending.set(requestId, {check, event, consumer: null, timer: null});
      return event;
    },

    waitForManualInput(requestId, {timeoutMs = 30000} = {}) {
      const request = pending.get(requestId);
      if (!request) throw new Error('Unknown or completed manual request.');
      if (request.consumer) throw new Error('Manual request is already being awaited.');

      return new Promise((resolve) => {
        request.consumer = async (inputBuffer) => {
          try {
            await onManualInput({
              requestId,
              check: request.check,
              inputBuffer,
            });
            emit('manual-input-consumed', {requestId, check: request.check});
            resolve({requestId, check: request.check, phase: 'manual-input-consumed'});
          } catch (error) {
            emit('manual-input-rejected', {requestId, check: request.check});
            resolve({
              requestId,
              check: request.check,
              phase: 'manual-input-rejected',
              reason: error instanceof Error ? error.message : 'manual input rejected',
            });
          }
        };
        request.timer = setTimeout(() => {
          pending.delete(requestId);
          emit('manual-input-timeout', {requestId, check: request.check});
          resolve({requestId, check: request.check, phase: 'manual-input-timeout'});
        }, timeoutMs);
      });
    },

    async submitManualInput(requestId, value) {
      const request = pending.get(requestId);
      if (!request || !request.consumer) {
        throw new Error('Manual request is not awaiting input.');
      }
      if (!(typeof value === 'string' || Buffer.isBuffer(value)) || value.length === 0) {
        throw new Error('Manual input must be a non-empty string.');
      }

      const inputBuffer = Buffer.isBuffer(value) ? Buffer.from(value) : Buffer.from(value, 'utf8');
      clearTimeout(request.timer);
      pending.delete(requestId);
      try {
        await request.consumer(inputBuffer);
      } finally {
        // The consumer is the only callback that sees the input buffer.
        inputBuffer.fill(0);
      }
    },

    getStatus() {
      return Object.freeze({
        liveStagingAllowed,
        runtime: runtimeConfig,
        iphone: 'not-verified',
        privateKey: 'not-provided',
        ownerSession: ownerSession.getState(),
      });
    },
  });
}

module.exports = {LIVE_CHECKS, createValidationHooks, isLiveStagingAllowed, listenForManualInput, parseRuntimeConfig};
