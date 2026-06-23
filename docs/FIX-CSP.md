# Fix CSP

The current CSP in [PROXY_EXAMPLE.conf](/Users/ahmed/frappe_apps/agent/PROXY_EXAMPLE.conf#L1243) is a compatibility policy, not a best-practice production policy:

```nginx
more_set_headers "Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; style-src 'self' 'unsafe-inline' https:; img-src 'self' data: https:; font-src 'self' data: https:; connect-src 'self' https: wss:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'";
```

## Why this is not best practice

- `'unsafe-inline'` weakens CSP by allowing inline scripts.
- `'unsafe-eval'` weakens CSP further and should be avoided unless the app requires it.
- Broad `https:` allowlists permit any HTTPS origin, which is looser than necessary.

## Best-practice approach

1. Use explicit allowlists per directive.
2. Prefer nonces or hashes instead of `'unsafe-inline'`.
3. Remove `'unsafe-eval'` unless the frontend demonstrably needs it.
4. Keep strong baseline directives such as:
   - `object-src 'none'`
   - `base-uri 'self'`
   - `frame-ancestors 'self'`

## Better policy for this setup

For `fe-decoupling.mazeed.cloud` forwarding frontend traffic to `devmazeedfe.mazeed.cloud`, a tighter policy looks like:

```nginx
more_set_headers "Content-Security-Policy: default-src 'self'; script-src 'self' https://devmazeedfe.mazeed.cloud; style-src 'self' 'unsafe-inline' https://devmazeedfe.mazeed.cloud; img-src 'self' data: https://devmazeedfe.mazeed.cloud; font-src 'self' data: https://devmazeedfe.mazeed.cloud; connect-src 'self' https://fe-decoupling.mazeed.cloud https://devmazeedfe.mazeed.cloud wss://fe-decoupling.mazeed.cloud; object-src 'none'; base-uri 'self'; frame-ancestors 'self'";
```

## Production recommendation

- Replace `'unsafe-inline'` with nonces or hashes if the frontend can support it.
- Remove `'unsafe-eval'` if production testing shows it is not needed.
- Replace broad source expressions with exact domains only.

## Practical rule

- Current policy: acceptable for compatibility testing
- Tightened explicit-domain policy: better for production
- Nonce/hash-based policy: best practice
