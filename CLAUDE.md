# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is **Frappe Press Agent** — a Flask + RQ (Redis Queue) daemon that runs on Frappe Cloud server nodes. It exposes an HTTP API consumed by the Press controller and executes long-running server operations (bench management, site CRUD, database ops, proxy config) as background jobs via RQ workers.

## Setup & Running

```bash
# Install into a virtualenv
virtualenv env && source env/bin/activate
pip install -e .

# Start all processes (web server on :25052, redis on :25025, 2 RQ workers)
honcho start
```

The agent requires a `config.json` in the working directory. See `Server.set_config_attributes()` in `agent/server.py` for expected keys (`name`, `benches_directory`, `nginx_directory`, `redis_port`, etc.).

## Development Commands

```bash
# Lint / format
ruff check agent/
ruff format agent/

# Run tests (uses unittest, no pytest needed)
python -m unittest discover -s agent/tests

# Run a single test file
python -m unittest agent.tests.test_proxy

# Interactive console (requires config.json in cwd)
agent console

# CLI entry point
agent --help
```

## Architecture

### Entity Hierarchy

```
Server (base node)
  └── Bench (a frappe-bench on disk inside benches_directory/)
        └── Site (a Frappe site inside bench/sites/)

Proxy (nginx proxy node, extends Server)
DatabaseServer (MariaDB node, extends Server)
```

All entities inherit from `Base` (`agent/base.py`), which provides:
- `execute()` — runs shell commands, streams output line-by-line to Redis
- `get_config()` / `set_config()` — atomic JSON config file reads/writes with `filelock`
- `update_redis()` — live-pushes step output to `agent:job:<id>:step:<id>` Redis keys

### Job / Step Decorators (`agent/job.py`)

Operations are decorated with `@job(name)` and `@step(name)`. When an HTTP endpoint calls a `*_job` method:
1. `@job` enqueues the method into an RQ queue (high/default/low priority)
2. The RQ worker picks it up; each `@step`-decorated call inside creates a `StepModel` in `jobs.sqlite3`
3. Output is streamed to Redis and consumed by the Press controller

Job/step state is persisted in a local SQLite DB (`jobs.sqlite3`) via Peewee ORM.

### HTTP API (`agent/web.py`)

Flask app with Basic Auth (`validate_token` decorator). Routes follow the pattern:
- `POST /benches/<bench>/...` — bench operations
- `POST /benches/<bench>/sites/<site>/...` — site operations
- `POST /proxy/...` — proxy/nginx operations
- `POST /database/...` — database server operations
- `GET /jobs/<id>` — poll job status/output

### Nginx Config Generation

`Proxy` uses Jinja2 templates in `agent/templates/proxy/` (rendered via `PackageLoader`). The main template is `nginx.conf.jinja2`. Proxy config state is stored as JSON files under `nginx_directory/hosts/<domain>/` and `nginx_directory/upstreams/`.

### Key Supporting Modules

| Module | Purpose |
|---|---|
| `agent/bench.py` | Bench init, app installs, migrations, Docker image management |
| `agent/site.py` | Site restore, migrate, backup, config updates, domain management |
| `agent/proxy.py` | Nginx host/upstream CRUD, TLS cert management, config reload |
| `agent/database_server.py` | MariaDB user/database ops, binary log handling |
| `agent/builder.py` | Docker image build for benches |
| `agent/monitor.py` | Prometheus metrics exporter |
| `agent/security.py` | CSP and security header management |
| `agent/callbacks.py` | HTTP callbacks to the Press controller on job completion |

## Linting Rules

`ruff.toml` configures line length 110, Python 3.8 target. Enabled rule sets: `F, E, W, I, UP, B, RUF, FA, TCH, C90, RET, SIM`. Max cyclomatic complexity is 8 (`C901`).

## Testing Notes

Tests in `agent/tests/` use `unittest` with real filesystem fixtures (created in `setUp`, torn down in `tearDown`). `testcontainers[mysql]` is available for database tests. Tests that invoke shell commands use `unittest.mock.patch` to avoid actual subprocess calls.
