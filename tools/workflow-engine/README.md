# Workflow Engine Ops

Operational helpers for the standalone workflow engine running under `/opt/workflow-engine`.

These files intentionally contain **no secrets**.

## Canonical runtime source

The versioned runtime package now lives under [`runtime/`](runtime/). It was imported from the sanitized production source snapshot captured on 2026-09-26. Operational helpers in this directory must reference that runtime tree rather than maintaining a second Python copy.

The source import does not deploy or restart the running VPS service by itself.

## Configure Linear without an editor

Run the helper as root. It securely prompts for the API key, rewrites only the `LINEAR_*` entries in `/etc/workflow-engine/env`, preserves the rest of the environment file, fixes permissions, and restarts the delivery worker.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/main/tools/workflow-engine/configure_linear.sh \
  -o /tmp/configure_linear.sh

chmod 700 /tmp/configure_linear.sh
LINEAR_MODE=dry_run /tmp/configure_linear.sh
rm -f /tmp/configure_linear.sh
```

The API key is read with hidden terminal input and is not printed.

## Probe Linear credentials

This is non-destructive. It verifies the API key and expected team without creating an issue.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/main/tools/workflow-engine/probe_linear.sh \
  -o /tmp/probe_linear.sh

chmod 700 /tmp/probe_linear.sh
/tmp/probe_linear.sh
rm -f /tmp/probe_linear.sh
```

Expected success:

```text
LINEAR PROBE PASS
team=R0meo1
```

If Linear returns `RESTRICTED_COUNTRY_BLOCKED`, that is a permanent region restriction for the host, not an API-key permission failure. Keep `LINEAR_MODE=dry_run` on that host and only run live delivery from a region where Linear permits API access.

## ChatGPT Linear connector fallback

When the VPS is region-blocked but the ChatGPT Linear connector can create the issue, record that external issue back into canonical delivery state.

The helper does not call Linear. It only records the already-created Linear issue UUID into the workflow database and marks matching delivery events as `delivered_connector`.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/main/tools/workflow-engine/record_linear_mapping.sh \
  -o /tmp/record_linear_mapping.sh

chmod 700 /tmp/record_linear_mapping.sh

/tmp/record_linear_mapping.sh \
  <canonical-task-id> \
  <linear-issue-uuid>

rm -f /tmp/record_linear_mapping.sh
```

This preserves the same dedupe invariant as live API delivery: one canonical task maps to one Linear issue.

## Deploy the complete canonical runtime

Production runtime deployment is isolated from TurBot even though it reuses the
same restricted SSH entrypoint.

The GitHub workflow `.github/workflows/workflow-engine-deploy.yml` runs only
after a successful **Deploy TurBot** workflow (or by manual dispatch). For an
automatic run it first checks whether the triggering commit changed the
Workflow Engine runtime or its deployment protocol.

The sender refuses a dirty checkout, a moving branch and any SHA that is no
longer the current `main` revision. The server accepts only the dedicated
`WORKFLOW_ENGINE_DEPLOY_BUNDLE_V1` protocol.

Server-side deployment:

1. accepts only files under `tools/workflow-engine/runtime/`;
2. rejects symlinks, device files, path traversal, SQLite/runtime state,
   `.env`, OAuth token files and backups;
3. installs runtime dependencies;
4. compiles the staged package and runs the built-in selftest + runtime tests
   before either service is stopped;
5. creates an online SQLite backup and verifies `PRAGMA integrity_check`;
6. swaps only `/opt/workflow-engine/workflow_engine`,
   `requirements.txt`, `README.md` and the non-secret deployed revision;
7. never edits `/etc/workflow-engine/env`, OAuth token JSON or
   `/var/lib/workflow-engine/workflow.db`;
8. restarts only `workflow-engine` and `workflow-delivery`;
9. verifies both services and `python -m workflow_engine.main status`;
10. restores the previous runtime package on failure. The SQLite database is
    deliberately not rolled back because schema changes are additive and a
    database rollback could discard messages accepted during recovery.

`GMAIL_INGEST_MODE=poll` remains the runtime default. Deploying code that
contains the Pub/Sub receiver therefore does **not** enable watch mode until
the protected server configuration and external Google Cloud resources are
explicitly provisioned.

Manual exact-SHA sender usage from a clean tested checkout:

```bash
export DEPLOY_HOST=<production-host>
bash deploy/send-workflow-engine-bundle.sh "$(git rev-parse HEAD)"
```

The restricted deploy key remains server-controlled; no Workflow Engine secret
is transmitted in the bundle.

## Deploy the version-aware delivery worker

The deployment helper backs up the current worker, validates Python syntax, replaces the worker from `main`, restarts the service, and leaves `LINEAR_MODE` unchanged.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/main/tools/workflow-engine/deploy_delivery_worker.sh \
  -o /tmp/deploy_delivery_worker.sh

chmod 700 /tmp/deploy_delivery_worker.sh
/tmp/deploy_delivery_worker.sh
rm -f /tmp/deploy_delivery_worker.sh
```

Keep `LINEAR_MODE=dry_run` on the current VPS because Linear reports `RESTRICTED_COUNTRY_BLOCKED` for that execution region.

The worker treats an old `dryrun-linear:<task_id>` mapping as non-live state. On a permitted live host, the first live cycle creates one real Linear issue, replaces the mapping with the real Linear issue UUID, and later task versions update that same issue instead of creating duplicates.

## Switch to live without nano

Only on a Linear-supported execution region, after the credential probe, dry-run verification, and CI are green:

```bash
sed -i 's/^LINEAR_MODE=.*/LINEAR_MODE=live/' /etc/workflow-engine/env
systemctl restart workflow-delivery
```

Then inspect:

```bash
journalctl -u workflow-delivery --since "-2 min" --no-pager
```

## Verify

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/main/tools/workflow-engine/verify.sh \
  -o /tmp/workflow-verify.sh

chmod 700 /tmp/workflow-verify.sh
/tmp/workflow-verify.sh
rm -f /tmp/workflow-verify.sh
```

The verifier checks both systemd services, engine counters, delivery state, duplicate protection, destination mappings, and only reports whether the Linear key is present.

## Security

Never commit:

- `token.json`
- Gmail OAuth client secrets
- `LINEAR_API_KEY`
- `/etc/workflow-engine/env`
- the SQLite runtime database
