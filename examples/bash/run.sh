#!/bin/bash

powerdns_migrator \
  --source-url http://localhost:8081 \
  --source-key change_me \
  --source-server-id localhost \
  --target-url http://localhost:8082 \
  --target-key change_me \
  --target-server-id localhost \
  --insecure-source \
  --insecure-target \
  --concurrency 10 \
  --log-level DEBUG \
  --timeout 10 \
  --on-error stop \
  --progress-interval 1 \
  --zones-file zones.txt
  # --recreate \
  # --dry-run \
