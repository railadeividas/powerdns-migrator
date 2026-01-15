# powerdns-migrator

Draft Python package and CLI to migrate zones between two PowerDNS servers using their HTTP API.

## Install (editable)

```bash
pip install -e .
```

## Usage

```bash
powerdns-migrate \
  --source-url https://pdns-source:8081 \
  --source-key "$PDNS_SOURCE_KEY" \
  --target-url https://pdns-target:8081 \
  --target-key "$PDNS_TARGET_KEY" \
  --zone example.com. \
  --recreate
```

Batch mode (async, parallel):

```bash
powerdns-migrate \
  --source-url https://pdns-source:8081 \
  --source-key "$PDNS_SOURCE_KEY" \
  --target-url https://pdns-target:8081 \
  --target-key "$PDNS_TARGET_KEY" \
  --zones-file /path/to/zones.txt \
  --concurrency 50
```

Key flags:
- `--server-id`: PowerDNS server id (default: `localhost`)
- `--recreate`: delete target zone if it already exists
- `--dry-run`: fetch and sanitize zone but skip target writes
- `--insecure-source` / `--insecure-target`: skip TLS verification for each side
- `--timeout`: HTTP timeout in seconds (default: 10)
- `--retries`: retry count for transient API errors
- `--retry-backoff`: base backoff seconds between retries
- `--retry-max-backoff`: maximum backoff seconds between retries
- `--retry-jitter`: max random jitter seconds added to backoff
- `--ignore-soa-serial`: ignore SOA serial changes and keep target serial
- `--on-error`: batch behavior on API error (continue or stop)
- `--zones-file`: migrate zones from a file (one per line)
- `--concurrency`: parallel migrations when using `--zones-file`
- `--graceful-timeout`: stop after N seconds on Ctrl+C (0 = wait indefinitely)
- `--progress-interval`: progress log interval in seconds (0 = disable)
- `--log-level`: set logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `--verbose`: enable debug logging (alias for `--log-level DEBUG`)

## Library use

```python
import asyncio

from powerdns_migrator.async_migrator import AsyncZoneMigrator
from powerdns_migrator.config import PowerDNSConnection

source = PowerDNSConnection(
    base_url="https://pdns-source:8081",
    api_key="SOURCE_KEY",
)
target = PowerDNSConnection(
    base_url="https://pdns-target:8081",
    api_key="TARGET_KEY",
)

async def run():
    migrator = AsyncZoneMigrator(source, target)
    try:
        await migrator.migrate("example.com.", recreate=True)
    finally:
        await migrator.close()

asyncio.run(run())
```

## Notes

- This is a draft. It intentionally keeps behavior simple: it fetches the entire zone (including rrsets) from the source and recreates it on the target.
- The migrator drops read-only fields returned by PowerDNS (`id`, `url`, `serial`, `notified_serial`, etc.).
- For existing zones on the target, use `--recreate` to delete before recreate.
- Tested with PowerDNS API v1. Additional adjustments may be needed for specific setups (DNSSEC, presigned zones, custom backends, etc.).
