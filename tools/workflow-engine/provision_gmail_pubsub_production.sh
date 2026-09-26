#!/usr/bin/env bash
# Non-secret production identity for the Gmail watch/Pub/Sub bridge.
# Authentication is intentionally left to gcloud/ADC; this file contains no key.
set -Eeuo pipefail

export GCP_PROJECT_ID="r0meo1-90ffe"
export GCP_EXPECTED_PROJECT_NUMBER="387497694802"
export GMAIL_PUBSUB_PUSH_ENDPOINT="https://bot.r0meo1.ru/gmail/pubsub"

exec "$(dirname "$0")/provision_gmail_pubsub.sh"
