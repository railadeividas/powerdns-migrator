#!/bin/bash

echo "--------------------------------"
echo "Running powerdns-migrator:      "
echo "--------------------------------"

python3 -m powerdns_migrator \
  --source-url http://localhost:8081 \
  --source-key pdns1key \
  --source-server-id localhost \
  --target-url http://localhost:8082 \
  --target-key pdns2key \
  --target-server-id localhost \
  --insecure-source \
  --insecure-target \
  --concurrency 10 \
  --log-level DEBUG \
  --timeout 10 \
  --on-error stop \
  --progress-interval 1 \
  --ignore-soa-serial \
  --zones-file examples/bash/zones.txt
  # --recreate \
  # --dry-run \

echo "--------------------------------"
echo "Migrated zones on target server:"
echo "--------------------------------"

curl --silent --request GET http://localhost:8082/api/v1/servers/localhost/zones \
  --header "X-API-Key: pdns2key" \
  --header "Content-Type: application/json" \
  | jq '.[] | .name'

# if arg --show-zones-details is provided, show details of zone1.test

if [[ "$1" != "--show-zones-details" ]]; then
  exit 0
fi

echo "--------------------------------"
echo "Zone details for zone1.test:    "
echo "--------------------------------"

curl --silent --request GET http://localhost:8082/api/v1/servers/localhost/zones/zone1.test \
  --header "X-API-Key: pdns2key" \
  --header "Content-Type: application/json" \
  | jq '.'
