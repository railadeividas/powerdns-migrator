# Development

## Overview

The development environment uses Docker Compose to spin up a full test stack: two PowerDNS instances (source and target), a MySQL backend, and a FastAPI web UI that lets you interactively test migrations. Code changes are reflected immediately — no rebuilds needed.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with Compose plugin (`docker compose`)
- No Python installation needed locally — everything runs inside containers

---

## Starting the Environment

```bash
docker compose up -d --build
```

This builds and starts all services. On first run it takes a bit longer as Docker pulls base images. Subsequent starts are faster.

To stop everything:

```bash
docker compose down
```

> **Note:** MySQL uses `tmpfs` storage — all data is lost when containers stop. This is intentional so each run starts with a clean state.

---

## Services

| Service | Description | URL / Port |
|---|---|---|
| **webui** | FastAPI dev UI for testing migrations | http://localhost:8080 |
| **pdns-auth-1** | Source PowerDNS (pre-seeded with 50 zones) | API: http://localhost:8081, DNS: `:5001` |
| **pdns-auth-2** | Target PowerDNS (starts empty) | API: http://localhost:8082, DNS: `:5002` |
| **mysql** | Shared MySQL 8.0 backend | `localhost:3306` |
| **adminer** | Database web UI | http://localhost:8090 |
| **rabbitmq** | Message broker (for examples) | AMQP: `:5672`, UI: http://localhost:15672 |

### PowerDNS API credentials

| Instance | API Key | Server ID |
|---|---|---|
| pdns-auth-1 (source) | `pdns1key` | `localhost` |
| pdns-auth-2 (target) | `pdns2key` | `localhost` |

### Adminer credentials

- **Server:** `mysql`
- **Username:** `root`
- **Password:** `rootpass`

### RabbitMQ credentials

- **Username:** `admin`
- **Password:** `pass2login`

---

## Web UI

Open **http://localhost:8080** to access the migration UI.

The UI connects to both PowerDNS instances automatically (connection details are pre-filled from environment variables). From the UI you can:

- Browse zones on source and target
- Migrate a single zone or batch-migrate zones
- Test migration options (dry-run, recreate, CNAME conflict handling, etc.)
- View real-time streaming output from CLI runs

---

## Hot Reload

The `webui` container mounts two directories as volumes:

```
./docker/webui   → /app               (FastAPI app code)
./powerdns_migrator → /pkg/powerdns_migrator  (library source)
```

Uvicorn runs with `--reload`, so **any change to `powerdns_migrator/` or `docker/webui/app.py` is picked up instantly** — no restart or rebuild needed.

---

## Project Structure

```
powerdns-migrator/
├── powerdns_migrator/      # Main Python package (library + CLI)
│   ├── cli.py              # CLI entry point
│   ├── async_migrator.py   # Core migration logic
│   ├── async_client.py     # PowerDNS HTTP API client (aiohttp)
│   ├── config.py           # PowerDNSConnection dataclass
│   ├── errors.py           # Custom exception hierarchy
│   └── utils.py            # Helpers (zone name normalization, etc.)
│
├── docker/
│   ├── pdns/               # PowerDNS container (Dockerfile + entrypoint.sh)
│   ├── mysql/              # Database init SQL for both instances
│   ├── webui/              # FastAPI web UI (Dockerfile + app.py + index.html)
│   └── zones/              # Pre-generated test zone files + zone-generator.sh
│
├── examples/               # Integration examples (bash, MySQL, RabbitMQ)
├── docker-compose.yml
└── pyproject.toml
```

---

## Test Zones

The source PowerDNS instance (`pdns-auth-1`) is seeded with **50 randomly generated zones** on startup. This is controlled by:

```yaml
PDNS_seed_zones: "true"
PDNS_seed_zones_amount: "50"
```

Zone files are loaded from `docker/zones/test-docker-zones-generated/` if they exist, otherwise new zones are generated at startup using `docker/zones/zone-generator.sh`.

To generate a fresh set of zone files locally:

```bash
bash docker/zones/zone-generator.sh --random 50 docker/zones/test-docker-zones-generated
```

---

## Code Formatting and Linting

This project uses [ruff](https://docs.astral.sh/ruff/) for formatting and linting.

```bash
# Check formatting (no changes)
ruff format --check .

# Show formatting diff (no changes)
ruff format --diff .

# Apply formatting
ruff format .

# Run linting checks
ruff check .

# Auto-fix linting issues
ruff check --fix .

# Run both linting and formatting checks
ruff check . && ruff format --check .
```

---

## Typical Development Workflow

1. Start the stack: `docker compose up -d --build`
2. Open the Web UI at http://localhost:8080
3. Edit code in `powerdns_migrator/` — changes apply immediately via hot reload
4. Use the UI to trigger migrations and verify behaviour
5. To reset state (clear all zones): `docker compose restart` or `docker compose down && docker compose up -d`
6. Before committing, run `ruff check . && ruff format --check .`
