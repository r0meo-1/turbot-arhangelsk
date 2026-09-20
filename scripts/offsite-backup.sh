#!/usr/bin/env bash
# Upload the newest verified SQLite backup copies to an approved S3-compatible
# off-host store. Plaintext database copies never leave BACKUP_DIR.
#
# The production cron may call this script unconditionally. It exits 0 while
# disabled or unconfigured, and fails closed once explicitly enabled.
set -Eeuo pipefail
umask 077

APP_DIR="${APP_DIR:-/opt/turbot}"
BACKUP_DIR="${BACKUP_DIR:-/opt/turbot/backups}"
CONFIG_FILE="${OFFSITE_BACKUP_CONFIG_FILE:-/etc/turbot/offsite-backup.env}"
MAX_BACKUP_AGE_SECONDS="${MAX_BACKUP_AGE_SECONDS:-93600}"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Off-site backup: state=disabled reason=config_missing"
    exit 0
fi

owner_uid="$(stat -c %u "$CONFIG_FILE")"
mode="$(stat -c %a "$CONFIG_FILE")"
if [[ "$owner_uid" != "0" ]]; then
    echo "ERROR: off-site backup config must be owned by root." >&2
    exit 1
fi
if (( (8#$mode & 077) != 0 )); then
    echo "ERROR: off-site backup config must not be group/world accessible." >&2
    exit 1
fi

# This file is root-owned and mode 0600/0400. It is intentionally separate from
# /opt/turbot/.env, which is writable by the application account and must never
# be sourced by a root cron job.
# shellcheck disable=SC1090
source "$CONFIG_FILE"

OFFSITE_BACKUP_ENABLED="${OFFSITE_BACKUP_ENABLED:-false}"
if [[ "$OFFSITE_BACKUP_ENABLED" != "true" ]]; then
    echo "Off-site backup: state=disabled"
    exit 0
fi

for command in age aws tar sha256sum; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "ERROR: $command is required for enabled off-site backup." >&2
        exit 1
    fi
done

: "${OFFSITE_S3_ENDPOINT_URL:?OFFSITE_S3_ENDPOINT_URL is required}"
: "${OFFSITE_S3_BUCKET:?OFFSITE_S3_BUCKET is required}"
: "${OFFSITE_S3_REGION:?OFFSITE_S3_REGION is required}"
: "${OFFSITE_AGE_RECIPIENT:?OFFSITE_AGE_RECIPIENT is required}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID is required}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY is required}"

OFFSITE_S3_PREFIX="${OFFSITE_S3_PREFIX:-turbot}"
if [[ "$OFFSITE_S3_ENDPOINT_URL" != https://* ]]; then
    echo "ERROR: off-site S3 endpoint must use HTTPS." >&2
    exit 1
fi
if [[ ! "$OFFSITE_S3_BUCKET" =~ ^[A-Za-z0-9._-]{3,63}$ ]]; then
    echo "ERROR: invalid off-site S3 bucket name." >&2
    exit 1
fi
if [[ ! "$OFFSITE_S3_PREFIX" =~ ^[A-Za-z0-9._/-]{1,120}$ || "$OFFSITE_S3_PREFIX" == /* || "$OFFSITE_S3_PREFIX" == *..* ]]; then
    echo "ERROR: invalid off-site S3 prefix." >&2
    exit 1
fi
if [[ ! "$OFFSITE_AGE_RECIPIENT" =~ ^age1[0-9a-z]+$ ]]; then
    echo "ERROR: OFFSITE_AGE_RECIPIENT must be an age X25519 public recipient." >&2
    exit 1
fi

latest_backup() {
    local stem="$1"
    find "$BACKUP_DIR" -maxdepth 1 -type f -name "${stem}_*.sqlite" -printf '%T@ %p\n' \
        | sort -nr | head -n 1 | cut -d' ' -f2-
}

check_fresh() {
    local path="$1"
    local now mtime age
    now="$(date +%s)"
    mtime="$(stat -c %Y "$path")"
    age=$((now - mtime))
    if (( age < 0 || age > MAX_BACKUP_AGE_SECONDS )); then
        echo "ERROR: backup copy is outside the off-site RPO window: age_seconds=$age." >&2
        return 1
    fi
}

main_backup="$(latest_backup bot_state)"
if [[ -z "$main_backup" || ! -f "$main_backup" ]]; then
    echo "ERROR: no verified main SQLite backup is available for off-site upload." >&2
    exit 1
fi
check_fresh "$main_backup"

files=("$(basename "$main_backup")")
vk_backup="$(latest_backup vk_bot_state || true)"
if [[ -n "$vk_backup" && -f "$vk_backup" ]]; then
    check_fresh "$vk_backup"
    files+=("$(basename "$vk_backup")")
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$tmp_dir/turbot-backup-$timestamp.tar.gz"
cipher="$archive.age"

tar -C "$BACKUP_DIR" -czf "$archive" -- "${files[@]}"
age -r "$OFFSITE_AGE_RECIPIENT" -o "$cipher" "$archive"
rm -f "$archive"

cipher_sha256="$(sha256sum "$cipher" | awk '{print $1}')"
key="$OFFSITE_S3_PREFIX/$(date -u +%Y/%m/%d)/$(basename "$cipher")"

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export AWS_DEFAULT_REGION="$OFFSITE_S3_REGION"
if [[ -n "${AWS_SESSION_TOKEN:-}" ]]; then
    export AWS_SESSION_TOKEN
fi

aws --endpoint-url "$OFFSITE_S3_ENDPOINT_URL" --region "$OFFSITE_S3_REGION" \
    s3 cp "$cipher" "s3://$OFFSITE_S3_BUCKET/$key" \
    --only-show-errors

listed="$(
    aws --endpoint-url "$OFFSITE_S3_ENDPOINT_URL" --region "$OFFSITE_S3_REGION" \
        s3api list-objects-v2 \
        --bucket "$OFFSITE_S3_BUCKET" \
        --prefix "$key" \
        --max-items 1 \
        --query "Contents[?Key=='$key'].Key | [0]" \
        --output text
)"
if [[ "$listed" != "$key" ]]; then
    echo "ERROR: uploaded ciphertext was not confirmed by remote listing." >&2
    exit 1
fi

echo "Off-site backup complete: encrypted=true files=${#files[@]} sha256=$cipher_sha256 key=$key"
