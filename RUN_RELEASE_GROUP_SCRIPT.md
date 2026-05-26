# Run Release Group Script

Runs a caller-supplied bash script against all active sites across a set of benches and returns the combined output as a single base64-encoded CSV.

## Endpoint

```
POST /server/run-release-group-script
```

### Request Body

| Field     | Type         | Required | Description |
|-----------|--------------|----------|-------------|
| `benches` | `list[str]`  | Yes      | Names of benches to target (as they appear on disk under `benches_directory`) |
| `script`  | `string`     | Yes      | Bash script content to execute |
| `timeout` | `integer`    | No       | Per-bench execution timeout in seconds. Clamped to 1–3600. Default: `300` |

### Response

```json
{ "job": 42 }
```

Poll `GET /jobs/<id>` until the job status is `Success`, then read `data.csv`.

### Job Result (`data` field)

```json
{
  "csv": "<base64-encoded string>",
  "row_count": 150,
  "error_count": 2
}
```

Decode `csv` with `base64.b64decode(data["csv"]).decode()` to get the raw CSV text.

---

## How the Script Is Invoked

The script runs **once per bench** from the bench's directory (`/home/frappe/benches/<bench-name>/`).

Active site names are passed as positional arguments:

```bash
bash <script> site1.example.com site2.example.com ...
```

Inside the script, use `$@` to iterate:

```bash
#!/bin/bash
for site in "$@"; do
    # run bench commands for $site
    echo "$site,some_value"
done
```

The script's **stdout** is captured verbatim as CSV rows. Each line of stdout becomes one row in the final CSV. The script is responsible for its own header row if one is needed.

---

## Active Site Definition

A site is included if:
- Its name does **not** start with `standby`
- Its `site_config.json` does not have `maintenance_mode = 1`
- Its name matches `^[a-zA-Z0-9._-]+$` (unsafe names are skipped and recorded as errors)

Benches that cannot be loaded from disk are skipped and reported in `error_count`.

---

## Job Steps

| Step | Description |
|------|-------------|
| Validate Bench List | Tries to load each bench. Raises if none are loadable. Returns `loadable` and `skipped` dicts. |
| Run Script on All Benches | Iterates loadable benches sequentially. Runs the script per bench. Collects rows and errors. |
| Aggregate CSV | Joins all rows, base64-encodes the result, returns final counts. |

---

## Example

### Request

```bash
curl -X POST https://<agent>/server/run-release-group-script \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "benches": ["bench-prod-001", "bench-prod-002"],
    "script": "#!/bin/bash\necho \"site,app_count\"\nfor site in \"$@\"; do\n  count=$(cd /home/frappe/frappe-bench && bench --site \"$site\" list-apps 2>/dev/null | wc -l)\n  echo \"$site,$count\"\ndone",
    "timeout": 120
  }'
```

### Poll for completion

```bash
curl https://<agent>/jobs/42
```

### Decode result

```python
import base64, json

job_data = json.loads(response.text)["data"]
csv_text = base64.b64decode(job_data["csv"]).decode()
print(csv_text)
```

---

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| Bench not loadable from disk | Recorded in `skipped` dict; job continues with remaining benches |
| All benches unloadable | Job fails immediately with `AgentException` |
| Site has malformed `site_config.json` | Site skipped; error recorded per-site |
| Script exits non-zero | Error recorded for that bench; other benches still run |
| Script exceeds `timeout` | Process killed; error recorded; other benches still run |
| All sites in a bench are filtered out | Bench skipped (no subprocess spawned); continues to next bench |

---

## Constraints

- Benches are processed **sequentially**, not in parallel.
- The script runs with the agent's OS user privileges — Press is responsible for validating script content before sending.
- `timeout` applies per bench, not per site. A bench with many sites shares the same timeout budget.
- Do not run multiple instances of this job simultaneously on the same server — concurrent `bench console` calls contend on gunicorn processes.
