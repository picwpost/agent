# Re-add Migration and Download Safeguards

## Summary
Restore the current changes in `agent/site.py`, `agent/server.py`, `agent/utils.py`, and `agent/web.py` after the temporary removal, so site migration can recover from long job queues and backup downloads can retry regional S3 endpoints when the global host fails.

## Key Changes
- In `agent/site.py`, keep `wait_till_ready()` bounded, return the retry trace, and add a timeout fallback that can purge job queues before retrying readiness.
- In `agent/server.py`, thread a `clear_jobs_on_timeout` option through the migrate flow and pass it into `site.wait_till_ready(...)` so the behavior is controllable from the job entrypoint.
- In `agent/web.py`, expose the new migrate option through `/benches/<bench>/sites/<site>/update/migrate`, defaulting it on for API callers unless explicitly disabled.
- In `agent/utils.py`, make `download_file()` try a regional S3 URL variant when the original signed URL uses the global `s3.amazonaws.com` endpoint, while preserving the original URL as the first attempt.

## Test Plan
- Add or update unit tests for `Site.wait_till_ready()` covering:
  - success before timeout
  - timeout without queue clearing
  - timeout with queue clearing and a second readiness attempt
- Add tests for `download_file()` covering:
  - normal download path
  - fallback to the regional S3 host when the original request fails
  - no fallback for non-S3 URLs
- Add a route-level test for `update_site_migrate` to verify `clear_jobs_on_timeout` is forwarded correctly.

## Assumptions
- The intended end state is to reintroduce the exact behavior currently present in the working tree, not redesign it.
- `clear_jobs_on_timeout` stays `False` by default at the server layer and `True` for the HTTP API entrypoint.
- The S3 fallback should remain best-effort and should not alter the signed query string, only the host.
