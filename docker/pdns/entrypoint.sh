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

if [ "${PDNS_seed_zones:-false}" = "true" ] && [ ! -f /data/seeded ]; then
  for _ in $(seq 1 30); do
    if pdnsutil list-all-zones >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  if [ -d /zones ]; then
    for zonefile in /zones/*.zone; do
      [ -f "$zonefile" ] || continue
      zonename=$(basename "$zonefile" .zone)
      pdnsutil load-zone "$zonename" "$zonefile"
    done
  fi

  # mkdir -p /data
  # touch /data/seeded
fi

wait "$PDNS_PID"
