# Site Update Flow: `POST /benches/{bench}/sites/{site}/update/migrate`

## 1. HTTP Route — `web.py:878`

`update_site_migrate()` extracts the payload fields and calls `Server().update_site_migrate_job(...)`, returning `{"job": <id>}` to Press immediately. The actual work runs asynchronously.

### Request Payload

| Field | Type | Default | Description |
|---|---|---|---|
| `target` | string | required | Destination bench name |
| `activate` | bool | `true` | Disable maintenance mode after migration |
| `skip_failing_patches` | bool | `false` | Add `--skip-failing` to migrate command |
| `skip_backups` | bool | `false` | Skip table backup steps entirely |
| `before_migrate_scripts` | dict | `{}` | App-specific scripts to run before migrate |
| `skip_search_index` | bool | `true` | Add `--skip-search-index` to migrate command |
| `clear_jobs_on_timeout` | bool | `true` | Purge jobs if ready-for-migration times out |

---

## 2. Job Enqueue — `server.py:461`

`@job("Update Site Migrate", priority="low")` — enqueued on the RQ **low-priority** queue.

| Property | Value |
|---|---|
| Job name | Update Site Migrate |
| Queue priority | low |
| Timeout | 4 hours |
| Result TTL | 24 hours |
| Persistence | `jobs.sqlite3` via `JobModel` |

---

## 3. Execution Steps

| # | Step Name | File | Condition | Shell Command / Action |
|---|---|---|---|---|
| 1 | Enable Maintenance Mode | `site.py:512` | always | `bench --site {site} set-maintenance-mode on` |
| 2 | Wait for Enqueued Jobs | `site.py:522` | always | Polls `bench ready-for-migration` every 1 s (max 60 s). On timeout + `clear_jobs_on_timeout=true` → runs `bench purge-jobs`, retries once |
| 3 | Clear Backup Directory | `site.py:557` | `skip_backups=false` | Removes and recreates `{site}/.migrate/` |
| 4 | Backup Site Tables | `site.py:563` | `skip_backups=false` | `mysqldump --single-transaction` each table → `{site}/.migrate/{table}.sql.gz` |
| 5 | Move Site Directory | `server.py:616` | always | `shutil.move({source}/sites/{site}/ → {target}/sites/)`. Archives destination if it exists without `site_config.json` |
| 6 | Setup NGINX (source bench) | `bench.py:477` | always | Regenerates nginx config for source bench (site removed), reloads nginx |
| 7 | Bench Setup NGINX Target | `bench.py:484` | always | Regenerates nginx config for target bench (site added), reloads nginx |
| 8 | Reload NGINX | `server.py:654` | always | `sudo systemctl reload nginx`. On failure runs `sudo nginx -t` |
| 9 | Run App Specific Scripts | `site.py:583` | `before_migrate_scripts` not empty | For each app: `bench --site {site} console` with script on stdin |
| 10 | Migrate Site | `site.py:589` | always | `bench --site {site} migrate [--skip-search-index] [--skip-failing]` — runs all patches and schema changes |
| 11 | Log Touched Tables | `site.py:608` | always | Reads `touched_tables.json` written by migrate (fallback to `previous_tables.json`) |
| 12 | Generate Theme Files | `server.py:507` | always (suppressed) | `bench --site {site} execute frappe.website...generate_theme_files_if_not_exist` — failure ignored |
| 13 | Disable Maintenance Mode | `site.py:643` | `activate=true` | `bench --site {site} set-maintenance-mode off` |
| 14 | Build Search Index | `site.py:619` | always (suppressed) | `bench --site {site} build-search-index` — failure ignored |

---

## 4. Payload Field → Step Usage

| Field | Controls Step(s) | Effect |
|---|---|---|
| `target` | 5, 7 | Move destination + target nginx config |
| `activate` | 13 | Whether to bring site back online after migrate |
| `skip_backups` | 3, 4 | Both backup steps are entirely skipped |
| `skip_failing_patches` | 10 | Adds `--skip-failing` flag to migrate |
| `skip_search_index` | 10 | Adds `--skip-search-index` flag to migrate |
| `before_migrate_scripts` | 9 | Scripts run in Frappe console per app, before migrate |
| `clear_jobs_on_timeout` | 2 | Purges Redis job queues if wait step times out |

---

## 5. Files Modified During Execution

| File / Path | Step | Change |
|---|---|---|
| `{source}/sites/{site}/` | 5 | Entire directory moved to target bench |
| `{target}/sites/{site}/` | 5 | Site directory arrives here |
| `{site}/.migrate/previous_tables.json` | 4 | List of all tables before migration |
| `{site}/.migrate/{table}.sql.gz` | 4 | Per-table mysqldump backup |
| `{site}/touched_tables.json` | 10→11 | Written by migrate, read in step 11 |
| nginx config (source bench) | 6 | Regenerated without the site |
| nginx config (target bench) | 7 | Regenerated with the site |

---

## 6. Key Source Files

| File | Role |
|---|---|
| `agent/web.py:878` | Route handler — extracts payload, calls job |
| `agent/server.py:461` | `update_site_migrate_job` — orchestrates all steps |
| `agent/server.py:616` | `move_site` — filesystem directory move |
| `agent/site.py:512–648` | Individual site steps (maintenance, backup, migrate) |
| `agent/bench.py:477–490` | Nginx config regeneration per bench |
| `agent/job.py:156–211` | `@job` / `@step` decorators (SQLite + Redis persistence) |
