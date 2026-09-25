# Workflow Engine Ops

Operational helpers for the standalone workflow engine running under `/opt/workflow-engine`.

These files intentionally contain **no secrets**.

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
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/workflow-engine-linear-live/tools/workflow-engine/probe_linear.sh \
  -o /tmp/probe_linear.sh

chmod 700 /tmp/probe_linear.sh
/tmp/probe_linear.sh
rm -f /tmp/probe_linear.sh
```

Expected result:

```text
LINEAR PROBE PASS
team=R0meo1
```

## Deploy the version-aware delivery worker

The deployment helper backs up the current worker, validates Python syntax, replaces the worker, restarts the service, and leaves `LINEAR_MODE` unchanged.

```bash
curl -fsSL \
  https://raw.githubusercontent.com/r0meo-1/turbot-arhangelsk/workflow-engine-linear-live/tools/workflow-engine/deploy_delivery_worker.sh \
  -o /tmp/deploy_delivery_worker.sh

chmod 700 /tmp/deploy_delivery_worker.sh
/tmp/deploy_delivery_worker.sh
rm -f /tmp/deploy_delivery_worker.sh
```

Keep `LINEAR_MODE=dry_run` for the first deployment.

The worker treats an old `dryrun-linear:<task_id>` mapping as non-live state. On the first live cycle it creates one real Linear issue, replaces the mapping with the real Linear issue UUID, and later task versions update that same issue instead of creating duplicates.

## Switch to live without nano

After the credential probe, dry-run verification, and CI are green:

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
