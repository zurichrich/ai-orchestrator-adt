#!/usr/bin/env bash
# Copyright 2026 zurichrich
# SPDX-License-Identifier: Apache-2.0
# Send a notification (email via Resend, Slack later).
# Usage: notify.sh <event_type> <project> <slug> <subject> <body>
# Reads project YAML for recipients. Uses RESEND_API_KEY from env.

set -euo pipefail

# ADT_DIR defaults to this script's repo root (lib/..); override with the env var.
ADT_DIR="${ADT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck disable=SC1091
source "$ADT_DIR/lib/load-project.sh"

event_type="${1:?event_type required}"
project="${2:?project required}"
slug="${3:?slug required}"
subject="${4:?subject required}"
body="${5:?body required}"

load_project "$project"

# Safety: refuse to send to any address on the project's optional blocklist
# (notifications.email.blocklist in projects/<name>.yaml). No address is baked
# into this shared script — each project declares its own.
to="$PROJECT_NOTIFY_EMAIL_TO"
if [[ -n "${PROJECT_NOTIFY_EMAIL_BLOCKLIST:-}" ]]; then
  while IFS= read -r blocked; do
    [[ -z "$blocked" ]] && continue
    if [[ "$to" == "$blocked" ]]; then
      echo "notify: '$to' is on this project's email blocklist — refusing to send" >&2
      exit 1
    fi
  done <<< "$PROJECT_NOTIFY_EMAIL_BLOCKLIST"
fi

from="$PROJECT_NOTIFY_EMAIL_FROM"
key_env="$PROJECT_NOTIFY_EMAIL_KEY_ENV"
key="${!key_env:-}"

if [[ -z "$key" ]]; then
  # Try to read it from the project's .env as a fallback.
  if [[ -f "$PROJECT_PATH/.env" ]]; then
    key=$(grep -E "^${key_env}=" "$PROJECT_PATH/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)
  fi
fi

if [[ -z "$key" ]]; then
  echo "notify: $key_env not set in env or $PROJECT_PATH/.env — falling back to local log only" >&2
  log_file="$ADT_DIR/logs/notify-$(date +%Y%m).log"
  mkdir -p "$(dirname "$log_file")"
  printf '[%s] event=%s project=%s slug=%s\nSubject: %s\n%s\n---\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$event_type" "$project" "$slug" "$subject" "$body" \
    >> "$log_file"
  exit 0
fi

# JSON-escape the body using jq if available; otherwise basic escape.
if command -v jq >/dev/null 2>&1; then
  payload=$(jq -nc --arg from "$from" --arg to "$to" --arg subject "$subject" --arg text "$body" \
    '{from: $from, to: $to, subject: $subject, text: $text}')
else
  esc_body=$(printf '%s' "$body" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | awk '{printf "%s\\n", $0}')
  payload=$(printf '{"from":"%s","to":"%s","subject":"%s","text":"%s"}' "$from" "$to" "$subject" "$esc_body")
fi

response=$(curl -sS -o /tmp/notify-resp.json -w "%{http_code}" \
  -X POST https://api.resend.com/emails \
  -H "Authorization: Bearer $key" \
  -H "Content-Type: application/json" \
  -d "$payload" || echo "000")

if [[ "$response" == "200" || "$response" == "202" ]]; then
  echo "notify: sent to $to (HTTP $response)"
else
  echo "notify: failed (HTTP $response). Response:" >&2
  cat /tmp/notify-resp.json >&2 || true
  # Fall back to local log
  log_file="$ADT_DIR/logs/notify-$(date +%Y%m).log"
  mkdir -p "$(dirname "$log_file")"
  printf '[%s] event=%s project=%s slug=%s HTTP=%s\nSubject: %s\n%s\n---\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$event_type" "$project" "$slug" "$response" "$subject" "$body" \
    >> "$log_file"
  exit 1
fi
