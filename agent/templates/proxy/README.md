# Proxy Template Notes

This directory contains the nginx proxy template used to render site-level proxy configuration.

## Content Security Policy Updates

`nginx.conf.jinja2` sets a `Content-Security-Policy` header with `more_set_headers` in the generated HTTPS server blocks.

The CSP was updated to support Microsoft Clarity and Flatpickr assets loaded from jsDelivr.

### Microsoft Clarity

Clarity was failing with this browser error:

```text
Refused to connect to 'https://l.clarity.ms/collect' because it violates the following Content Security Policy directive: "connect-src ..."
```

The proxy template now allows Clarity HTTPS subdomains in `connect-src`:

```nginx
connect-src 'self' ws: wss: https://*.clarity.ms;
```

This covers Clarity collection endpoints such as:

```text
https://l.clarity.ms/collect
```

The wildcard is scoped to `clarity.ms` subdomains instead of allowing all HTTPS connections.

### Flatpickr

Flatpickr was loaded from jsDelivr and failed under both script and stylesheet CSP checks:

```text
https://cdn.jsdelivr.net/npm/flatpickr
https://cdn.jsdelivr.net/npm/flatpickr/dist/flatpickr.min.css
```

The proxy template now allows jsDelivr for scripts and styles:

```nginx
script-src ... https://cdn.jsdelivr.net;
script-src-elem ... https://cdn.jsdelivr.net;
style-src ... https://cdn.jsdelivr.net;
style-src-elem ... https://cdn.jsdelivr.net;
```

`script-src-elem` and `style-src-elem` are included explicitly so browsers do not need to fall back to `script-src` and `style-src` when validating external `<script>` and `<link rel="stylesheet">` tags.

## Deployment Note

Changing this template does not update live response headers by itself. The nginx config must be regenerated from `nginx.conf.jinja2`, deployed to the proxy host, and nginx must be reloaded.

If the browser still reports a CSP like this after deployment:

```text
style-src 'self' 'unsafe-inline'
```

then the live response is not using the updated generated nginx config, or another upstream layer is also setting a `Content-Security-Policy` header.
