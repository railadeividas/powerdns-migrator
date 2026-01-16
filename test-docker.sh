#!/bin/bash

# Usage examples:
# -----------------------
# # single zone (same as before)
# ./test-docker.sh --migrate --zone zone1.test --debug

# # file with multiple zones
# ./test-docker.sh --migrate --zones-file zones.txt --debug

# # with toggles
# ./test-docker.sh --migrate --zones-file zones.txt --dry-run --recreate --debug

set -euo pipefail

print_header() {
  local text="$1"
  local line
  line=$(printf '%*s' "${#text}" '' | tr ' ' '=')

  echo
  echo "$line"
  echo "$text"
  echo "$line"
  echo
}

# ---- PowerDNS migrator runner ----
run_pdns_migrator() {
  local zone="$1"        # may be empty
  local zones_file="$2"  # may be empty
  local dry_run="$3"     # "true" / "false"
  local recreate="$4"    # "true" / "false"
  local auto_fix="$5"    # "true" / "false"

  # Must provide exactly one: zone OR zones_file
  if [[ -n "$zone" && -n "$zones_file" ]]; then
    echo "Error: use only one of --zone or --zones-file" >&2
    return 1
  fi
  if [[ -z "$zone" && -z "$zones_file" ]]; then
    echo "Error: either --zone or --zones-file is required" >&2
    return 1
  fi

  if [[ -n "$zones_file" && ! -f "$zones_file" ]]; then
    echo "Error: zones file not found: $zones_file" >&2
    return 1
  fi

  local cmd=(
    python3 -m powerdns_migrator
    --source-url http://localhost:8081
    --source-key pdns1key
    --source-server-id localhost
    --target-url http://localhost:8082
    --target-key pdns2key
    --target-server-id localhost
    --insecure-source
    --insecure-target
    --concurrency 10
    --log-level "$LOG_LEVEL"
    --timeout 10
    --on-error stop
    --progress-interval 1
    --ignore-soa-serial
  )

  # Choose zone input arg
  if [[ -n "$zone" ]]; then
    cmd+=(--zone "$zone")
    print_header "Migrating zone: $zone"
  else
    cmd+=(--zones-file "$zones_file")
    print_header "Migrating zones from file: $zones_file"
  fi

  [[ "$dry_run" == true ]] && cmd+=(--dry-run)
  [[ "$recreate" == true ]] && cmd+=(--recreate)
  [[ "$auto_fix" == true ]] && cmd+=(--auto-fix-cname-conflicts)
  [[ "$auto_fix" == true ]] && cmd+=(--auto-fix-double-cname-conflicts)
  [[ "$auto_fix" == true ]] && cmd+=(--normalize-txt-escapes)

  "${cmd[@]}"
}

# ---- flags / defaults ----
DELETE_TARGET_ZONES=false
SHOW_ZONES_DETAILS=false
SHOW_TARGET_ZONES=false

MIGRATE=false
MIGRATE_DRY_RUN=false
MIGRATE_AUTOFIX=false
MIGRATE_RECREATE=false
ZONE=""   # required only when --migrate is used
ZONES_FILE=""  # required only when --migrate is used

LOG_LEVEL="INFO"
DEBUG=false

# ---- argument parsing (switch to while+shift so --zone works) ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --delete-target-zones)
      DELETE_TARGET_ZONES=true
      shift
      ;;
    --show-zones-details)
      SHOW_ZONES_DETAILS=true
      shift
      ;;
    --show-target-zones)
      SHOW_TARGET_ZONES=true
      shift
      ;;
    --debug)
      LOG_LEVEL="DEBUG"
      DEBUG=true
      shift
      ;;
    --migrate)
      MIGRATE=true
      shift
      ;;
    --zone)
      ZONE="${2:-}"
      shift 2
      ;;
    --zones-file)
      ZONES_FILE="${2:-}"
      shift 2
      ;;
    --dry-run)
      MIGRATE_DRY_RUN=true
      shift
      ;;
    --auto-fix)
      MIGRATE_AUTOFIX=true
      shift
      ;;
    --recreate)
      MIGRATE_RECREATE=true
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# ---- existing actions ----
if [[ "$DELETE_TARGET_ZONES" == true ]]; then
  print_header "Removing existing zones on target"

  echo -n "zone1.test status code: "
  curl -s -o /dev/null -w "%{http_code}\n" \
    --request DELETE "http://localhost:8082/api/v1/servers/localhost/zones/zone1.test" \
    --header "X-API-Key: pdns2key" \
    --header "Content-Type: application/json"

  echo -n "zone2.test status code: "
  curl -s -o /dev/null -w "%{http_code}\n" \
    --request DELETE "http://localhost:8082/api/v1/servers/localhost/zones/zone2.test" \
    --header "X-API-Key: pdns2key" \
    --header "Content-Type: application/json"

  echo -n "zone3.test status code: "
  curl -s -o /dev/null -w "%{http_code}\n" \
    --request DELETE "http://localhost:8082/api/v1/servers/localhost/zones/zone3.test" \
    --header "X-API-Key: pdns2key" \
    --header "Content-Type: application/json"
fi

if [[ "$SHOW_TARGET_ZONES" == true ]]; then
  print_header "Zones on target server"

  curl --silent --request GET "http://localhost:8082/api/v1/servers/localhost/zones" \
    --header "X-API-Key: pdns2key" \
    --header "Content-Type: application/json" \
    | jq '.[] | .name'
fi

# ---- new action: migrate ----
if [[ "$MIGRATE" == true ]]; then
  # Require exactly one input
  if [[ -n "$ZONE" && -n "$ZONES_FILE" ]]; then
    echo "Error: use only one of --zone or --zones-file" >&2
    exit 1
  fi
  if [[ -z "$ZONE" && -z "$ZONES_FILE" ]]; then
    echo "Error: --zone <name> or --zones-file <path> is required when using --migrate" >&2
    exit 1
  fi

  run_pdns_migrator "$ZONE" "$ZONES_FILE" "$MIGRATE_DRY_RUN" "$MIGRATE_RECREATE" "$MIGRATE_AUTOFIX"
fi


if [[ "$SHOW_TARGET_ZONES" == true ]]; then
  print_header "Zones on target server"

  curl --silent --request GET "http://localhost:8082/api/v1/servers/localhost/zones" \
    --header "X-API-Key: pdns2key" \
    --header "Content-Type: application/json" \
    | jq '.[] | .name'
fi
