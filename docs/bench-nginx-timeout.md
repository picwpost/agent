# Bench Nginx Timeout Configuration

## Problem

Frappe sites hosted behind this agent can return **504 Gateway Time-out** errors during slow operations — typically large document uploads, heavy report generation, or long-running API calls.

The timeout chain has two layers:

```
Browser
  │
  ▼
Proxy nginx  (agent/templates/proxy/nginx.conf.jinja2)
  │   proxy_read_timeout 600s   ← fine
  ▼
Bench nginx  (agent/templates/bench/nginx.conf.jinja2)
  │   proxy_read_timeout {{ http_timeout }}   ← was 120s — fires first
  ▼
gunicorn     (127.0.0.1:18xxx)
```

The bench-level nginx (which fronts gunicorn) had `http_timeout` set to **120 seconds** in every bench's `config.json`. Any request that took longer than 2 minutes hit this timeout before the proxy layer's 600s limit was ever reached.

## Solution

Two fixes were applied in `agent/bench.py` and `agent/cli.py`:

### 1. Minimum timeout enforcement (`agent/bench.py`)

`http_timeout` is read from each bench's `config.json`. The fix enforces a minimum of 600 seconds so that low values in existing configs are overridden when nginx is regenerated:

```python
# setup_nginx() and generate_supervisor_config() both use:
"http_timeout": max(self.bench_config.get("http_timeout", 600), 600),
```

This affects:
- `proxy_read_timeout` in the bench nginx config (`agent/templates/bench/nginx.conf.jinja2`)
- `--timeout` passed to gunicorn via supervisor (`agent/templates/bench/supervisor.conf`)

### 2. Bulk regenerate CLI command (`agent/cli.py`)

A new CLI command regenerates the nginx config for **all benches at once** and reloads nginx:

```bash
agent bench regenerate-nginx
```

This is equivalent to calling `setup_nginx()` on every bench sequentially and then doing a single `sudo systemctl reload nginx`.

---

## Applying the Fix on a Server

After pulling the updated agent and running `agent update`:

```bash
# 1. Pull and update the agent
cd ~/agent
git pull
agent update

# 2. Regenerate all bench nginx configs
agent bench regenerate-nginx

# 3. Verify the new timeout is applied
sudo nginx -T | grep proxy_read_timeout
# Expected: proxy_read_timeout 600; for every bench
```

### Manual verification per bench

```bash
grep proxy_read_timeout /home/frappe/benches/*/nginx.conf
```

All lines should read `proxy_read_timeout 600;`.

---

## Changing the Timeout Value

The minimum is enforced at **600 seconds** in the agent code. To set a higher value for a specific bench, update it via the Press controller by sending:

```json
{
  "bench_config": {
    "http_timeout": 1200
  }
}
```

This writes the new value to the bench's `config.json` and triggers nginx regeneration. Values below 600 will still be clamped to 600 by the `max()` enforcement in `bench.py`.

---

## Files Changed

| File | Change |
|------|--------|
| `agent/bench.py` | `max(self.bench_config.get("http_timeout", 600), 600)` at lines 513 and 670 |
| `agent/cli.py` | Added `agent bench regenerate-nginx` command |
| `agent/templates/bench/nginx.conf.jinja2` | Template unchanged — reads `{{ http_timeout }}` from context |
