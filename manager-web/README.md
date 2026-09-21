# Mobile manager journal (work in progress)

The website Flask app serves `/manager/`. This interface uses the existing
Agent Desk bearer-protected CRM endpoints and database. It does not create a
second customer store. It currently supports the today/overdue queue, request
history, adding a note, and scheduling a next task in the device's timezone.

The public HTML contains no CRM data. The user supplies an existing Agent Desk
pairing key; it is held in memory and cleared on logout/pagehide. No persistent
browser token storage, external scripts, or customer-message sends are used.
This key has the existing shared Agent Desk privileges, not per-manager roles.

Writes are never automatically retried: a network error can occur after a
successful server commit. The interface asks the manager to inspect history
before retrying. Buttons disable while their operation is pending.

Browser smoke on localhost with an isolated in-memory synthetic API passed: valid/invalid key, queue, request history, note persistence across reconnect, task creation with local time, logout clearing the visible data. At 375px viewport width, no horizontal overflow. This is Chromium evidence, not Safari or production database validation.

Remaining before release: actual iPhone
verification, task completion, Telegram admin identity
verification, deployment packaging review, CI and production revision checks.
Do not claim issue #209 is complete or that this version has been deployed.

Validation so far: `python -m pytest tests/test_manager_web.py
tests/test_website_app.py -q --basetemp=<fresh-local-directory>`: 38 passed.
This covers server routes and existing CRM operations, not browser interaction.

Synthetic browser check also passed creating a quote (150000 RUB) and recording a reaction that remained selected after reopening the card. No real offers or customer data were used.
