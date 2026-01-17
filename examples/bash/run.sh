#!/bin/bash

# Script to run the migrator with a single zone from already defined source and target servers

# Usage:
# ./run.sh zone1.test.

# Load source and target configuration from environment variables; Otherwise use defaults
SOURCE_URL="${MIGRATOR_SOURCE_URL:-http://localhost:8081}"
SOURCE_KEY="${MIGRATOR_SOURCE_KEY:-pdns1key}"
SOURCE_SERVER_ID="${MIGRATOR_SOURCE_SERVER_ID:-localhost}"

TARGET_URL="${MIGRATOR_TARGET_URL:-http://localhost:8082}"
TARGET_KEY="${MIGRATOR_TARGET_KEY:-pdns2key}"
TARGET_SERVER_ID="${MIGRATOR_TARGET_SERVER_ID:-localhost}"

# If zone is in args, use it
if [ -n "$1" ]; then
  ZONE="$1"
else
  echo "No zone provided"
  exit 1
fi

# Run migrator
powerdns_migrator \
  --source-url "$SOURCE_URL" \
  --source-key "$SOURCE_KEY" \
  --source-server-id "$SOURCE_SERVER_ID" \
  --target-url "$TARGET_URL" \
  --target-key "$TARGET_KEY" \
  --target-server-id "$TARGET_SERVER_ID" \
  --insecure-source \
  --insecure-target \
  --concurrency 10 \
  --log-level DEBUG \
  --timeout 10 \
  --on-error stop \
  --progress-interval 1 \
  --ignore-soa-serial \
  --normalize-txt-escapes \
  --auto-fix-double-cname-conflicts \
  --auto-fix-cname-conflicts \
  --recreate \
  --dry-run \
  --zone "$ZONE"

# It's possible to migrate multiple zones from a file using argument --zones-file zones.txt
# If needed, just modify the script to pass the file as an argument.
