# Frontend Tenant Routing Setup

This setup makes users see only your frontend app on tenant domains like `site1.mazeed.cloud`, while API traffic is forwarded to your server-agent.

## 1. DNS

- Create a wildcard DNS record: `*.mazeed.cloud` -> your proxy server public IP.

## 2. Enable frontend tenant routing on proxy

Call Agent API:

```bash
curl -X POST http://<agent-host>:25052/proxy/frontend-tenant-routing \
  -H "Authorization: Bearer <agent-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "frontend_upstream": "127.0.0.1:3000",
    "server_agent_upstream": "127.0.0.1:26000"
  }'
```

Config keys used:

- `frontend_tenant_mode` (bool)
- `frontend_upstream` (host:port)
- `server_agent_upstream` (host:port)

When enabled:

- `https://site1.mazeed.cloud/` -> frontend upstream
- `https://site1.mazeed.cloud/api/*` -> server-agent upstream
- Proxy injects tenant headers:
  - `X-Forwarded-Host`: original host (for example `site1.mazeed.cloud`)
  - `X-Tenant-Site`: mapped tenant/site value from proxy host mapping

## 3. Keep tenant host mapping

Make sure wildcard host mapping exists in proxy (already created by default setup for `*.domain`), and add specific host mappings if needed using existing `/proxy/hosts` APIs.

## 4. Server-agent requirements

Your server-agent should:

- Read tenant from `X-Tenant-Site` (preferred) or `X-Forwarded-Host`
- Enforce tenant auth/authorization
- Call the correct Frappe site APIs internally
- Never expose Frappe site URLs directly to browser clients

## 5. Frontend requirements

- Frontend must call relative APIs (`/api/...`) only.
- Do not call Frappe domains directly from browser.
