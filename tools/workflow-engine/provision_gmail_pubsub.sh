#!/usr/bin/env bash
# Idempotently provision the Google Cloud side of authenticated Gmail -> Pub/Sub
# push delivery. This script never enables Workflow Engine watch mode and never
# creates service-account keys or touches Gmail OAuth token material.
set -Eeuo pipefail

command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud is required" >&2
  exit 1
}

project="${GCP_PROJECT_ID:?GCP_PROJECT_ID is required}"
endpoint="${GMAIL_PUBSUB_PUSH_ENDPOINT:?GMAIL_PUBSUB_PUSH_ENDPOINT is required}"
topic_id="${GMAIL_PUBSUB_TOPIC_ID:-workflow-engine-gmail}"
subscription_id="${GMAIL_PUBSUB_SUBSCRIPTION_ID:-workflow-engine-gmail-push}"
push_sa_id="${GMAIL_PUBSUB_PUSH_SERVICE_ACCOUNT_ID:-workflow-engine-pubsub-push}"
receiver_path="${GMAIL_PUBSUB_PATH:-/gmail/pubsub}"
audience="${GMAIL_PUBSUB_AUDIENCE:-$endpoint}"

[[ "$endpoint" == https://* ]] || {
  echo "GMAIL_PUBSUB_PUSH_ENDPOINT must use https://" >&2
  exit 1
}

[[ "$receiver_path" == /* && "$receiver_path" != *"?"* ]] || {
  echo "GMAIL_PUBSUB_PATH must be an absolute path without a query string" >&2
  exit 1
}

case "$endpoint" in
  *"$receiver_path") ;;
  *)
    echo "Push endpoint must end with GMAIL_PUBSUB_PATH ($receiver_path)" >&2
    exit 1
    ;;
esac

gcloud services enable \
  pubsub.googleapis.com \
  gmail.googleapis.com \
  iam.googleapis.com \
  --project "$project" \
  --quiet

project_number="$(
  gcloud projects describe "$project" \
    --format='value(projectNumber)'
)"
[[ "$project_number" =~ ^[0-9]+$ ]] || {
  echo "Could not resolve numeric Google Cloud project number" >&2
  exit 1
}

push_sa_email="${push_sa_id}@${project}.iam.gserviceaccount.com"
pubsub_service_agent="service-${project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
topic_resource="projects/${project}/topics/${topic_id}"

wait_for_service_account() {
  local email="$1"

  for _ in {1..30}; do
    if gcloud iam service-accounts describe "$email" \
      --project "$project" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Service account did not become visible: $email" >&2
  return 1
}

# Ensure the Pub/Sub service agent exists before granting it token-minting
# permission. API enablement can be eventually consistent.
gcloud beta services identity create \
  --service=pubsub.googleapis.com \
  --project "$project" \
  --quiet >/dev/null

wait_for_service_account "$pubsub_service_agent"

if ! gcloud iam service-accounts describe "$push_sa_email" \
  --project "$project" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$push_sa_id" \
    --project "$project" \
    --display-name="Workflow Engine Pub/Sub push" \
    --quiet
fi

wait_for_service_account "$push_sa_email"

# Least-privilege grant: Pub/Sub's service agent may mint OIDC tokens as only
# this user-managed push identity, rather than receiving project-wide access.
gcloud iam service-accounts add-iam-policy-binding "$push_sa_email" \
  --project "$project" \
  --member="serviceAccount:$pubsub_service_agent" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --quiet >/dev/null

if ! gcloud pubsub topics describe "$topic_id" \
  --project "$project" >/dev/null 2>&1; then
  gcloud pubsub topics create "$topic_id" \
    --project "$project" \
    --quiet
fi

# Gmail's fixed publisher identity must be able to publish mailbox-change
# notifications to this topic.
gcloud pubsub topics add-iam-policy-binding "$topic_id" \
  --project "$project" \
  --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --quiet >/dev/null

if gcloud pubsub subscriptions describe "$subscription_id" \
  --project "$project" >/dev/null 2>&1; then
  current_topic="$(
    gcloud pubsub subscriptions describe "$subscription_id" \
      --project "$project" \
      --format='value(topic)'
  )"

  [[ "$current_topic" == "$topic_resource" ]] || {
    echo "Existing subscription points to a different topic: $current_topic" >&2
    exit 1
  }

  gcloud pubsub subscriptions modify-push-config "$subscription_id" \
    --project "$project" \
    --push-endpoint="$endpoint" \
    --push-auth-service-account="$push_sa_email" \
    --push-auth-token-audience="$audience" \
    --quiet
else
  gcloud pubsub subscriptions create "$subscription_id" \
    --project "$project" \
    --topic="$topic_id" \
    --push-endpoint="$endpoint" \
    --push-auth-service-account="$push_sa_email" \
    --push-auth-token-audience="$audience" \
    --quiet
fi

echo
echo "Google Cloud Gmail Pub/Sub provisioning: ok"
echo "Install these non-secret Workflow Engine settings only after the HTTPS"
echo "reverse proxy reaches the local authenticated receiver:"
printf 'GMAIL_PUBSUB_TOPIC=%s\n' "$topic_resource"
printf 'GMAIL_PUBSUB_AUDIENCE=%s\n' "$audience"
printf 'GMAIL_PUBSUB_SERVICE_ACCOUNT=%s\n' "$push_sa_email"
printf 'GMAIL_PUBSUB_PATH=%s\n' "$receiver_path"
echo
echo "Keep GMAIL_INGEST_MODE=poll until endpoint reachability and auth are verified."
