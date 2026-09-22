'use strict';

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

function createValidationHooks({ownerSession, onManualIntervention = () => {}} = {}) {
  if (!ownerSession || typeof ownerSession.getState !== 'function') {
    throw new Error('ownerSession is required.');
  }

  return Object.freeze({
    requestManualCheck(check, details = '') {
      if (!Object.values(LIVE_CHECKS).includes(check)) {
        throw new Error('Unsupported live check.');
      }

      const event = Object.freeze({
        check,
        phase: 'awaiting-owner-session',
        requiresManualInput: true,
        details: String(details),
        session: ownerSession.getState(),
      });

      // The callback receives status only; it never receives a private key.
      onManualIntervention(event);
      return event;
    },

    getStatus() {
      return Object.freeze({
        iphone: 'not-verified',
        privateKey: 'not-provided',
        ownerSession: ownerSession.getState(),
      });
    },
  });
}

module.exports = {LIVE_CHECKS, createValidationHooks};
