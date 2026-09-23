'use strict';

const ALLOWED_PHASES = new Set([
  'mock-fallback', 'awaiting-owner-session', 'manual-input-consumed',
  'manual-input-rejected', 'manual-input-timeout', 'verification-started',
  'verified-locally', 'probe-started', 'probe-completed', 'probe-failed',
]);

function createRuntimeTelemetry({write = (line) => process.stdout.write(line)} = {}) {
  return Object.freeze({
    emit(phase, details = {}) {
      if (!ALLOWED_PHASES.has(phase)) throw new Error('Unsupported telemetry phase.');
      const event = {
        component: 'live-validation', phase,
        check: typeof details.check === 'string' ? details.check : undefined,
        requestId: typeof details.requestId === 'string' ? details.requestId : undefined,
        mode: typeof details.mode === 'string' ? details.mode : undefined,
        ok: typeof details.ok === 'boolean' ? details.ok : undefined,
      };
      write(`${JSON.stringify(event)}\n`);
      return Object.freeze(event);
    },
  });
}

module.exports = {ALLOWED_PHASES, createRuntimeTelemetry};
