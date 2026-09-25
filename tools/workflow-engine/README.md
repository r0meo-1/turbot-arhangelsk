# Workflow Engine Ops

Small operational helpers for the standalone workflow engine running under `/opt/workflow-engine`.

These files intentionally contain **no secrets**.

## Configure Linear without an editor

Run the helper as root. It securely prompts for the API key, rewrites only the `LINEAR_*` entries in `/etc/workflow-engine/env`, preserves the rest of the environment file, fixes permissions, and restarts the delivery worker.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/workflow-engine-ops/tools/workflow-engine/configure_linear.sh \
  -o /tmp/configure_linear.sh

chmod 700 /tmp/configure_linear.sh
LINEAR_MODE=dry_run /tmp/configure_linear.sh
rm -f /tmp/configure_linear.sh
```

The API key is read with hidden terminal input and is not printed.

Use `LINEAR_MODE=dry_run` until the live Linear adapter has passed its create/update tests.

## Verify

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/workflow-engine-ops/tools/workflow-engine/verify.sh \
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
