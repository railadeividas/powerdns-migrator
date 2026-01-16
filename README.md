# powerdns-migrator

Python package and CLI to migrate zones between two PowerDNS servers using their HTTP API.

## Install

```bash
pip install powerdns-migrator
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
- `--dry-run`: fetch and validate zones without making changes to target server
- `--insecure-source` / `--insecure-target`: skip TLS verification for each side
- `--timeout`: HTTP timeout in seconds (default: 10)
- `--retries`: retry count for transient API errors
- `--retry-backoff`: base backoff seconds between retries
- `--retry-max-backoff`: maximum backoff seconds between retries
- `--retry-jitter`: max random jitter seconds added to backoff
- `--ignore-soa-serial`: ignore SOA serial changes and keep target serial
- `--auto-fix-cname-conflicts`: auto-fix CNAME conflicts (drop other types on same name, but drop CNAME at apex)
- `--auto-fix-double-cname-conflicts`: trim multi-record CNAME rrsets to a single record (first one wins)
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
        result = await migrator.migrate("example.com.", recreate=True, dry_run=False)
        print(f"Migration completed: {result['migrator_action']}")
        print(f"Changes applied: {len(result['changes'])}")
    finally:
        await migrator.close()

asyncio.run(run())
```

### Migration Result Structure

The `migrate()` method returns a dictionary with detailed information about the migration:

```python
{
    "source_zone": {...},        # Sanitized zone data from source
    "target_zone": {...},        # Zone data from target (empty in dry-run mode)
    "changes": {...},            # RRSet changes that were/would be applied
    "migrator_action": "..."     # Action taken: CREATE_ZONE, PATCH_ZONE, RECREATE_ZONE, or NOOP
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

### Example output:
```bash
# Test single zone migration
powerdns-migrate \
  --source-url https://pdns-source:8081 \
  --source-key "$PDNS_SOURCE_KEY" \
  --target-url https://pdns-target:8081 \
  --target-key "$PDNS_TARGET_KEY" \
  --zone example.com. \
  --dry-run

# Test batch migration
powerdns-migrate \
  --source-url https://pdns-source:8081 \
  --source-key "$PDNS_SOURCE_KEY" \
  --target-url https://pdns-target:8081 \
  --target-key "$PDNS_TARGET_KEY" \
  --zones-file /path/to/zones.txt \
  --concurrency 10 \
  --dry-run
```

The migrator will report what actions would be taken (`CREATE_ZONE`, `PATCH_ZONE`, `RECREATE_ZONE`, or `NOOP`) and provide detailed change information for each zone.

## Notes

- This packages is under active development. It intentionally keeps behavior simple: it fetches the entire zone (including rrsets) from the source and recreates it on the target.
- The migrator drops read-only fields returned by PowerDNS (`id`, `url`, `serial`, `notified_serial`, etc.).
- For existing zones on the target, use `--recreate` to delete before recreate.
- When `--auto-fix-cname-conflicts` is enabled, apex CNAMEs are removed and non-apex CNAMEs are kept while other rrsets with the same name are dropped.
- When `--auto-fix-double-cname-conflicts` is enabled, multi-record CNAME rrsets are trimmed to the first record.
- When `--normalize-txt-escapes` is enabled, TXT/SPF records with decimal escape sequences (e.g. `\239\191\189`) are normalized to raw bytes during comparison. This is useful when migrating between backends that represent non-ASCII content differently (e.g. MySQL vs LMDB).
- Tested with PowerDNS API v1. Additional adjustments may be needed for specific setups (DNSSEC, presigned zones, custom backends, etc.).

## Development

### Code Formatting with Ruff

This project uses [ruff](https://docs.astral.sh/ruff/) for code formatting and linting. Here are the most useful commands for contributors:

```bash
# Check formatting (without making changes)
ruff format --check .

# Show formatting differences (without making changes)
ruff format --diff .

# Apply formatting changes
ruff format .

# Run linting checks
ruff check .

# Auto-fix linting issues where possible
ruff check --fix .

# Run both linting and formatting checks
ruff check . && ruff format --check .
```

For more options and configuration, see `ruff --help` or the [ruff documentation](https://docs.astral.sh/ruff/).
