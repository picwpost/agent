# Agent Repository Map

- Purpose: Python-based Agent that manages Frappe Cloud benches, sites, nginx proxy config, background workers, and exposes a Flask API/CLI for orchestration.
- Entrypoints: `agent.cli` (CLI commands and IPython console), `agent.web` (Flask app served by Gunicorn/Supervisor), RQ workers (jobs in `agent/job.py`), and supervisor/honcho via `Procfile`.
- Config: `config.json` in repo root (or provided via `--config-path`) defines server name, benches directory, nginx directory, redis port, press URL, etc. Many classes reload config from this file via `Base.get_config`.
- Processes/ports: Redis on `redis_port`, web API on `web_port` (default 25052), supervisor programs `agent:web`, `agent:worker-*`, `agent:redis`, optional `agent:nginx_reload_manager` for proxy servers.

## Core Modules
- `agent/server.py` — Central orchestrator. Knows server paths, renders supervisor/nginx configs, pulls code/images, manages benches/sites, Docker interactions, and runs update flows (`update_agent_cli`, `update_agent_web`). Uses Jinja templates under `agent/templates`.
- `agent/bench.py` — Bench abstraction (per tenant stack). Creates bench directories, manages config, deploys/upgrades containers, supervisord for single-container benches, nginx for sites, backups, app installs.
- `agent/site.py` — Site-level operations: create/migrate sites, run bench console commands, manage domains, backups/restore, enable apps, set admin passwords, import/export DB, and site analytics helpers.
- `agent/proxy.py` — Proxy server nginx config generator. Manages hosts and upstreams directories, adds/removes domains, handles autoscale secondaries, and triggers nginx reloads. Uses file locks to avoid races.
- `agent/nginx_reload_manager.py` — Background loop that batches nginx reload requests from Redis queues, runs `sudo nginx -s reload`, and auto-fixes duplicate upstream/domain files when possible.
- `agent/web.py` — Flask API surface; authenticates via bearer/basic token from `config.json`. Dispatches to Server/Bench/Site/Proxy/DB handlers, exposes job status, metrics, backup/restore, snapshot recovery, SSH proxy, ProxySQL, MinIO, etc.
- `agent/job.py` — Job/Step tracking with peewee + SQLite (`jobs.sqlite3`), RQ queue wrappers, decorators `@job`/`@step`, and callbacks for success/failure. Redis connection derived from config `redis_port`.
- `agent/base.py` — Shared execution wrapper; runs shell commands, streams output to Redis for job updates, handles return codes/errors (`AgentException`).
- `agent/utils.py` — Helpers: supervisor status parsing, registry health checks, system info, etc.
- Other helpers: `database.py` (SQL helpers), `database_server.py`, `database_physical_backup.py`/`_restore.py`, `snapshot_recovery.py`, `nfs_handler.py`, `proxysql.py`, `minio.py`, `monitor.py`, `usage.py` (cron utilities), `analytics.py` (site analytics cron), `patch_handler.py` (run patches listed in `patches.txt`), `security.py` (firewall rules), `ssh.py` (SSH proxy).

## Typical Flows
- Agent Update (CLI): `python -m agent.cli update` or `server.update_agent_cli()` from console. Pulls repo (`git fetch/merge upstream/master`), reinstalls editable package, regenerates supervisor configs, restarts redis/RQ/web workers, regenerates nginx, optionally restarts nginx reload manager on proxy servers, then runs patches.
- Agent Update (Press/API): `server.update_agent_web(url, branch)` resets repo, fetches upstream, checks out branch, reinstalls, restarts redis/workers/web, regenerates nginx and supervisor, runs patches. This is what `/api/method/run_doc_method` ends up calling.
- Bench lifecycle: `Server.new_bench` (job) logs into registry, sets up bench directory with docker-compose/config from image, deploys via `Bench.deploy`, generates nginx, and optionally supervisor config for single-container benches. Bench start/stop via CLI `agent bench start/stop`.
- Site lifecycle: `Site.new_site`/`restore`/`migrate`/`set_admin_password` etc. run bench commands through `bench_execute`, manage domains, TLS, backups, and DB import/export. Domain changes flow through Proxy/Server nginx generation.
- Proxy/nginx: Proxy manages `nginx/hosts` and `nginx/upstreams`; `Proxy.setup_proxy()` regenerates configs and reloads nginx. `NginxReloadManager` batches reloads from Redis queues to avoid frequent reloads and attempts auto-healing on conflicts.
- Jobs and Status: Functions decorated with `@job` enqueue RQ jobs (Redis queue name by priority), persisting status to SQLite models; `@step` tracks sub-steps. Output is streamed to Redis keys `agent:job:{id}` and `agent:job:{id}:step:{step_id}` for UI consumption.
- Web API: Served by Gunicorn (`Procfile`) through supervisor; protected by bearer/basic token. Exposes endpoints for server/bench/site actions, job status (`/jobs/<id>`), metrics, MinIO/ProxySQL operations, backups, snapshot recovery, SSH proxy, and nginx reload status (`/nginx_reload_status/<request_id>`).
- API Authentication: Incoming requests hit `agent.web`. `validate_access_token` checks the `Authorization` header. It accepts either `Bearer <token>` or `Basic` (base64 user:token). The token is hashed and stored as `access_token` in `config.json` (set via `agent setup authentication` or `/authentication`). In debug mode it skips the check; otherwise requests without a valid token get `401` and `WWW-Authenticate: Basic`.

## CLI Shortcuts
- `agent console` — IPython shell preloaded with `server = Server(...)`, plus `Proxy/Bench/Site` classes. Logs history to `logs/agent_console.log`.
- `agent update` — Calls `Server().update_agent_cli`.
- `agent bench start|stop` — Start/stop one or all benches.
- `agent run web|worker` — Dev server or RQ worker entrypoints (used by Procfile/honcho).
- `agent setup ...` — Helpers to write `config.json`, configure sudoers for py-spy, setup supervisor/nginx, initialize job DB, cron jobs for analytics/usage, etc.

## Layout Quick Reference
- Root scripts/configs: `Procfile`, `redis.conf`, `docker-compose.yml`, `wait-for-it.sh`, `monitor_idle.py`.
- Templates: `agent/templates/**` (supervisor.conf, nginx configs, bench docker-compose, etc).
- Pages/assets for nginx error pages: `agent/pages`.
- Tests: `agent/tests`.
- Logs: created under `logs/` relative to config dir (e.g., `logs/agent_console.log`).

## Notes for New Contributors
- Most actions shell out via `Base.execute`; be careful with command strings and failure propagation (`AgentException`).
- Jobs depend on Redis + RQ; ensure `config.json` has correct `redis_port` and that supervisor programs are running.
- Proxy servers run `nginx_reload_manager` via supervisor; reloads happen through Redis queues to avoid stampede reloads.
- Patches are sequenced in `patches.txt` and executed by `patch_handler.run_patches`; add new patch files under `agent/patches/`.
- When touching config-dependent code, prefer reading via `self.config`/`get_config` so live reloads pick up changes without restart.
