'use strict';

// Isolated manual-session verification. Uses a synthetic value only.
const {createOwnerSession} = require('../manager-web/owner-session');
const {createValidationHooks} = require('../manager-web/live-validation');

async function main() {
  let consumedLength = 0;
  const hooks = createValidationHooks({
    ownerSession: createOwnerSession(),
    onManualInput: async ({inputBuffer}) => {
      consumedLength = inputBuffer.length;
    },
  });

  const event = hooks.requestManualCheck('private-key', 'synthetic verification only');
  const waiting = hooks.waitForManualInput(event.requestId, {timeoutMs: 1000});

  // Replace this with a human-controlled input adapter during field testing.
  await hooks.submitManualInput(event.requestId, 'synthetic-manual-input');
  const result = await waiting;

  process.stdout.write(`${JSON.stringify({
    requestId: result.requestId,
    phase: result.phase,
    check: result.check,
    consumedLength,
    secretReturned: Object.prototype.hasOwnProperty.call(result, 'input'),
  })}\n`);
}

main().catch((error) => {
  process.stderr.write(`manual-session-check: ${error.message}\n`);
  process.exitCode = 1;
});
