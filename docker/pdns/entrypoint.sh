#!/bin/sh
set -eu

cat > /etc/powerdns/pdns.conf <<EOF
launch=gmysql
gmysql-host=${PDNS_gmysql_host}
gmysql-user=${PDNS_gmysql_user}
gmysql-password=${PDNS_gmysql_password}
gmysql-dbname=${PDNS_gmysql_dbname}

api=yes
api-key=${PDNS_api_key}
webserver=yes
webserver-address=0.0.0.0
webserver-port=8081
webserver-allow-from=0.0.0.0/0

loglevel=${PDNS_loglevel:-4}
EOF

pdns_server --daemon=no --guardian=no --loglevel="${PDNS_loglevel:-4}" &
PDNS_PID=$!

if [ "${PDNS_seed_zones:-false}" = "true" ]; then
  for _ in $(seq 1 30); do
    if pdnsutil list-all-zones >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  # Skip import if zones already exist (check for known test zone)
  if pdnsutil list-zone aaaaecy.cloud >/dev/null 2>&1; then
    echo "[ENTRYPOINT] Zones already exist, skipping import"
  else

    ZONES_DIR="/zones/test-docker-zones-generated"

    # If no zone files exist, generate random ones to a tmp directory
    if ! ls "$ZONES_DIR"/*.zone >/dev/null 2>&1; then
      ZONE_COUNT="${PDNS_seed_zones_amount:-30}"
      echo "[ENTRYPOINT] No zone files found, generating $ZONE_COUNT random zones..."
      ZONES_DIR="/tmp/zones"
      mkdir -p "$ZONES_DIR"
      /zones/zone-generator.sh --random "$ZONE_COUNT" "$ZONES_DIR"
    else
      echo "[ENTRYPOINT] Using existing zone files from $ZONES_DIR"
    fi

    # Load all zone files
    for zonefile in "$ZONES_DIR"/*.zone; do
      [ -f "$zonefile" ] || continue
      zonename=$(basename "$zonefile" .zone)
      echo "[ENTRYPOINT] Loading zone: $zonename"
      if ! pdnsutil load-zone "$zonename" "$zonefile"; then
        echo "[ENTRYPOINT] WARNING: Failed to load zone $zonename, skipping..."
      fi
    done
    echo "[ENTRYPOINT] Done seeding zones"

  fi

else
  echo "[ENTRYPOINT] Not seeding zones"
fi

wait "$PDNS_PID"
