# powerdns-migrator

Python package and CLI to migrate DNS zones between PowerDNS servers using their HTTP API.

## Features

- **CLI & Library** — Use as a command-line tool or import directly into your Python projects
- **Async & Parallel** — Batch migrate thousands of zones concurrently with configurable parallelism
- **Smart Sync** — Detects differences and merges only changed records, or fully recreates zones
- **Dry-Run Mode** — Preview changes before applying them to the target server
- **Retry with Backoff** — Configurable retries with exponential backoff and jitter for transient errors
- **TXT Escape Normalization** — Handle different TXT record encodings across backends (MySQL, etc.)
- **Error Handling** — Choose to stop or continue on errors during batch migrations
- **Progress Reporting** — Real-time progress logs for large batch migrations

## Install

```bash
pip install powerdns-migrator
```

## Usage

```bash
powerdns-migrator \
  --source-url https://pdns-source:8081 \
  --source-key "$PDNS_SOURCE_KEY" \
  --target-url https://pdns-target:8081 \
  --target-key "$PDNS_TARGET_KEY" \
  --zone example.com. \
  --recreate
```

Batch mode (async, parallel):

```bash
powerdns-migrator \
  --source-url https://pdns-source:8081 \
  --source-key "$PDNS_SOURCE_KEY" \
  --target-url https://pdns-target:8081 \
  --target-key "$PDNS_TARGET_KEY" \
  --zones-file /path/to/zones.txt \
  --concurrency 5
```

Key flags:
- `--server-id`: PowerDNS server id (default: `localhost`)
- `--recreate`: delete and recreate target zone if it already exists (without this flag, changes are merged)
- `--dry-run`: fetch and validate zones without making changes to target server
- `--insecure-source` / `--insecure-target`: skip TLS verification for each side
- `--timeout`: HTTP timeout in seconds (default: 10)
- `--retries`: retry count for transient API errors
- `--retry-backoff`: base backoff seconds between retries
- `--retry-max-backoff`: maximum backoff seconds between retries
- `--retry-jitter`: max random jitter seconds added to backoff
- `--retry-create-timeouts`: retry zone creation after timeouts (disabled by default)
- `--ignore-soa-serial`: ignore SOA serial changes and keep target serial
- `--normalize-txt-escapes`: normalize TXT/SPF decimal escape sequences (e.g. `\239`) to raw bytes for comparison
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

from powerdns_migrator import (
    AsyncZoneMigrator,
    PowerDNSConnection,
    PowerDNSAPIError,
    PowerDNSConnectionError,
    PowerDNSMigratorError,
    PowerDNSResponseError,
)

source = PowerDNSConnection(
    base_url="https://pdns-source:8081",
    api_key="SOURCE_KEY",
)
target = PowerDNSConnection(
    base_url="https://pdns-target:8081",
    api_key="TARGET_KEY",
)


async def run():
    try:
        async with AsyncZoneMigrator(source, target) as migrator:
            result = await migrator.migrate(
                "example.com.", recreate=True, dry_run=False
            )
            print(f"Migration completed: {result['migrator_action']}")
            print(f"Changes applied: {len(result['changes'])}")
    except PowerDNSAPIError as exc:
        print(
            f"API error: {exc.status} {exc.body} "
            f"(retries={exc.retries_attempted}, "
            f"timeout retries={exc.timeout_retries_attempted})"
        )
    except PowerDNSConnectionError as exc:
        print(f"Connection error: {exc.cause}")
    except PowerDNSResponseError as exc:
        print(f"Invalid API response: {exc.detail}")
    except PowerDNSMigratorError as exc:
        print(f"Migration error: {exc}")


asyncio.run(run())
```

The client and migrator support `async with` to close the sessions they create.
When you pass existing `AsyncPowerDNSClient` instances to a migrator, you retain
ownership and must close those clients yourself. Transient HTTP responses,
including `500`, use the configured retries. A `POST /zones` transport failure
is not retried automatically. Set `retry_create_timeouts=True` on the client or
migrator (or use `--retry-create-timeouts` in the CLI) to retry creation after
timeouts. A timed-out first attempt may have created the zone, so a retry can
return `409`. Other POST transport failures are not retried.

HTTP failures expose `retries_attempted` and `timeout_retries_attempted` on
`PowerDNSAPIError`. For example, a `409` with `timeout_retries_attempted=1`
followed one timeout retry. `PowerDNSConnectionError` exposes the same counts.
Both counts are zero when the first attempt fails without a retry; a retry
after a `500` increases only `retries_attempted`.

### PowerDNSConnection Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `base_url` | `str` | *required* | PowerDNS API base URL (e.g. `https://pdns:8081`) |
| `api_key` | `str` | *required* | PowerDNS API key |
| `server_id` | `str` | `"localhost"` | PowerDNS server id |
| `verify_ssl` | `bool` | `True` | Verify TLS certificates |

### AsyncZoneMigrator Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `source` | `PowerDNSConnection` | *required* | Source PowerDNS connection config |
| `target` | `PowerDNSConnection` | *required* | Target PowerDNS connection config |
| `timeout` | `float` | `10.0` | HTTP timeout in seconds |
| `retries` | `int` | `3` | Retry count for transient HTTP responses and non-POST transport failures |
| `retry_backoff` | `float` | `0.5` | Base backoff seconds between retries |
| `retry_max_backoff` | `float` | `5.0` | Maximum backoff seconds between retries |
| `retry_jitter` | `float` | `0.1` | Max random jitter seconds added to backoff |
| `retry_create_timeouts` | `bool` | `False` | Retry zone creation after timeouts |
| `ignore_soa_serial` | `bool` | `False` | Ignore SOA serial changes and keep target serial |
| `normalize_txt_escapes` | `bool` | `False` | Normalize TXT/SPF decimal escape sequences to raw bytes for comparison |

### migrate() Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `zone_name` | `str` | *required* | Zone name to migrate (e.g. `"example.com."`) |
| `recreate` | `bool` | `False` | Delete and recreate target zone if it exists (otherwise changes are merged) |
| `dry_run` | `bool` | `False` | Validate and compute changes without modifying target |

### Migration Result Structure

The `migrate()` method returns a dictionary with detailed information about the migration:

```python
{
    "source_zone": {...},  # Sanitized zone data from source
    "target_zone": {...},  # Zone data from target (empty in dry-run mode)
    "changes": {...},  # RRSet changes that were/would be applied
    "migrator_action": "...",  # Action taken: CREATE_ZONE, PATCH_ZONE, RECREATE_ZONE, or NOOP
}
```

**Action Types:**
- `CREATE_ZONE`: Zone created on target (didn't exist before)
- `PATCH_ZONE`: Zone updated with specific RRSet changes
- `RECREATE_ZONE`: Zone deleted and recreated (when using `--recreate`)
- `NOOP`: No changes needed (zone already in sync)

## Dry Run Mode

The `--dry-run` flag allows you to test migrations safely without making any changes to the target PowerDNS server. This is useful for:

- **Validation**: Verify zones can be fetched and parsed from source
- **Change Analysis**: See exactly what would be migrated or modified
- **Safety**: Test configurations and permissions before real migrations

### What dry-run does:
- ✅ Fetches zone data from source PowerDNS server
- ✅ Sanitizes and validates zone structure
- ✅ Computes required changes on target server
- ✅ Returns detailed migration plan and statistics
- ❌ **Does NOT** create, delete, or modify zones on target server
- ❌ **Does NOT** make any API calls to target server (except zone existence checks)

## Examples

The repository includes ready-to-use examples for common integration patterns in the [`examples/`](examples/) directory.

## Notes

- This packages is under active development. It intentionally keeps behavior simple: it fetches the entire zone (including rrsets) from the source and recreates it on the target.
- The migrator drops read-only fields returned by PowerDNS (`id`, `url`, `serial`, `notified_serial`, etc.).
- For existing zones on the target, use `--recreate` to delete before recreate.
- When `--normalize-txt-escapes` is enabled, TXT/SPF records with decimal escape sequences (e.g. `\239\191\189`) are normalized to raw bytes during comparison. This is useful when migrating between backends that represent non-ASCII content differently (e.g. MySQL vs LMDB).
- Tested with PowerDNS API v1. Additional adjustments may be needed for specific setups (DNSSEC, presigned zones, custom backends, etc.).
