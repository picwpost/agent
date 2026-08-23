# Proxy Template Notes

This directory contains the Nginx proxy template used to render site-level proxy
configuration for the main proxy host.

The current source of truth is `nginx.conf.jinja2`.

## High-Level Structure

The template renders:

- upstream blocks for app servers from `upstreams`
- fixed local upstreams for `site_not_found`, `deactivated`, `suspended`, and
  `suspended_saas`
- frontend upstreams:
  - `amplify_host` -> `prod-mazeedfe.mazeed.cloud`
  - `amplify_dev_host` -> `devmazeedfe.mazeed.cloud`
  - `amplify_enivoing_host` -> `einvoice-kickoff.d3sv5nogsg24zt.amplifyapp.com`
- host/header maps used to split frontend traffic from backend traffic
- dedicated server blocks for:
  - `fe-decoupling-nextjs.mazeed.cloud`
  - `einvoice-kickoff-2.mazeed.cloud`
- generated server blocks for every host in `hosts`
- local error servers on `127.0.0.1:10090` to `127.0.0.1:10093`

## Backend Routing Maps

The backend routing is built from these maps:

- `map $actual_host $upstream_server_hash`
  Selects the backend HTTP upstream for normal app traffic.
- `map $actual_host $socket_upstream_hash`
  Selects the websocket upstream. Auto-scaled sites use the `_primary`
  upstream.
- `map $host $actual_host`
  Resolves incoming hostnames to the actual backend site name.
- `map $host $backend_host_header`
  Controls which `Host` header is sent to the backend app.

Current special cases in `map $host $actual_host`:

- backdoor hosts: `*-backdoor.<domain>` -> `<site>.<domain>`
- `fe-decoupling-frappe.mazeed.cloud` -> `fe-decoupling-nextjs.mazeed.cloud`

Current special cases in `map $host $backend_host_header`:

- backdoor hosts send `Host: $actual_host`
- `fe-decoupling-frappe.mazeed.cloud` sends `Host: $actual_host`

## Wildcard Frontend Split

Wildcard frontend behavior is handled only through these maps:

- `map $is_backdoor_host $wildcard_frontend_proxy_pass`
- `map $is_backdoor_host $wildcard_frontend_host_header`
- `map $is_backdoor_host $wildcard_frontend_x_proxy_upstream`

For the wildcard server (`*.{{ domain }}`):

- backend paths go to the app upstream:
  - `^/(frappe-api/.*|(api|assets|files|private/files)/)`
  - `/socket.io`
- frontend paths go to the frontend target:
  - `/fe_assets/`
  - `/`

Behavior:

- normal wildcard hosts use the production frontend target
  `https://prod-mazeedfe.mazeed.cloud`
- backdoor hosts bypass the frontend target and go directly to the app upstream

This wildcard split should be treated as sensitive behavior. Changes to it
affect every wildcard-routed host.

## Dedicated `fe-decoupling-nextjs` Host

`fe-decoupling-nextjs.mazeed.cloud` is intentionally defined as a dedicated
server block before the generated host loop.

Its current behavior matches the older direct-proxy pattern:

- backend paths go to the normal app upstream:
  - `^/(frappe-api/.*|(api|assets|files|private/files)/)`
  - `/socket.io`
- frontend paths go directly to the dev frontend host:
  - `/fe_assets/`
  - `/`

Frontend implementation details:

- sets `amplify_host=devmazeedfe.mazeed.cloud`
- sends `Host: $amplify_host`
- proxies with `proxy_pass https://amplify_dev_host`

This host does not use the wildcard frontend maps.

## Dedicated `einvoice-kickoff-2` Host

`einvoice-kickoff-2.mazeed.cloud` is intentionally defined as a dedicated
server block before the generated host loop.

Its behavior mirrors `fe-decoupling-nextjs.mazeed.cloud`:

- backend paths go to the normal app upstream:
  - `^/(frappe-api/.*|(api|assets|files|private/files)/)`
  - `/socket.io`
- frontend paths go directly to the configured Amplify host:
  - `/fe_assets/`
  - `/`

Frontend implementation details:

- sets `amplify_host=einvoice-kickoff.d3sv5nogsg24zt.amplifyapp.com`
- sends `Host: $amplify_host`
- proxies with `proxy_pass https://amplify_enivoing_host`

This host does not use the wildcard frontend maps.

## Generated Host Server Blocks

For hosts rendered from `hosts`:

- every HTTPS server block gets the shared security headers and CSP
- certificates are resolved from `nginx_directory/hosts/<cert_host>/`
- wildcard certificate reuse is handled through `wildcards`
- redirect-only hosts return `301`

For the wildcard generated host (`*.{{ domain }}`):

- backend and frontend traffic are split as described above

For non-wildcard generated hosts:

- `/assets/` is proxied to the backend upstream with cache enabled
- `/socket.io` is proxied to the socket upstream
- `/` is proxied to the backend upstream

If `host_options.codeserver` is enabled, the `/` location upgrades the
connection and keeps websocket-style headers.

## Shared Headers and CSP

All HTTPS server blocks in this template set:

- `X-Frame-Options: SAMEORIGIN`
- `X-XSS-Protection: 1; mode=block`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer-when-downgrade`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`

The current CSP allows:

- Microsoft Clarity:
  - `https://www.clarity.ms`
  - `https://scripts.clarity.ms`
  - `connect-src ... https://*.clarity.ms`
- jsDelivr for scripts and styles:
  - `https://cdn.jsdelivr.net`
- Mazeed framing rules:
  - `frame-src 'self' https://*.mazeed.cloud`
  - `frame-ancestors 'self' https://*.mazeed.cloud`

## Deployment Note

Updating `nginx.conf.jinja2` does not affect live traffic by itself.

The generated proxy config must be rebuilt and Nginx must be reloaded on the
proxy host. Typical validation flow:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

When debugging behavior, confirm both:

- the generated config matches the template changes
- the final response headers include the expected `X-Proxy-Upstream` value
