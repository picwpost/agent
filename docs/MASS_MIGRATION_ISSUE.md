# Mass Site Migration Failure — Root Cause Analysis

## Problem Statement

When Press sends a large number of concurrent `POST /benches/{bench}/sites/{site}/update/migrate`
requests (e.g. 500–30,000 sites), **all migrations fail** and every site is left stuck in
maintenance mode (returning 503 to users). The agent's bench queue grows to tens of thousands of
jobs. The failure is systematic, not random — it will reproduce every time at this scale.

---

## System Components

```
Press (controller)
  │
  │  HTTP POST x N
  ▼
Agent (Flask + RQ)
  ├── Agent Redis (port 25025)   ← agent's own job queue
  │     [RUNNING] Job A
  │     [RUNNING] Job B
  │     [PENDING] Jobs C … N
  │
  └── Docker exec into bench container
        │
        ├── bench Redis (frappe-worker queue)
        │     queues: home-frappe-frappe-bench:default/short/long
        │     consumers: frappe-worker processes
        │
        └── Frappe Scheduler process
              fires every ~4 min, enqueues jobs for ALL sites
```

The agent's Redis and the bench's Redis are **separate instances**. The bench's Redis holds
Frappe background jobs (email flush, monitor flush, Google Calendar sync, etc.) for every site.

---

## The Scheduler Burst

The Frappe scheduler fires in bursts. Every ~4 minutes it enqueues scheduled tasks for **every
site** on the bench simultaneously. For a bench with N sites and ~4 tasks/site/tick:

| Sites | Jobs per burst | Drain time at 14 jobs/sec |
|-------|---------------|--------------------------|
| 500   | ~2,000        | ~143 seconds (~2.4 min)  |
| 5,000 | ~20,000       | ~1,429 seconds (~24 min) |
| 30,000| ~120,000      | ~8,571 seconds (~143 min)|

From the actual worker logs, the burst pattern is visible:

```
03:10:37  [last job of previous burst completes]
          ← 2-minute silence (queue empty) →
03:12:44  [burst fires: all 500 sites enqueued simultaneously]
03:12:44 → 03:13:03+  [workers drain: ~14 jobs/sec, many sites in parallel]
```

**Key observation**: For a 500-site bench the queue empties between bursts (2-min gap).
For a 30,000-site bench the burst takes 143 minutes to drain — **the queue is never empty**.

---

## Root Cause 1 — `bench ready-for-migration` always finds pending jobs

### What it does (`frappe/commands/scheduler.py:231`)

```python
def ready_for_migration(context, site=None):
    frappe.init(site)
    pending_jobs = False

    # Check 3 times, 1 second apart
    for _ in range(3):
        pending_jobs |= any_job_pending(site=site)
        time.sleep(1)

    if pending_jobs:
        sys.exit(1)   # failure — site has pending jobs
    else:
        return 0      # success — safe to migrate
```

### What `any_job_pending` checks (`frappe/utils/doctor.py:81`)

```python
def any_job_pending(site: str) -> bool:
    for queue in get_queue_list():
        q = get_queue(queue)
        for job_id in q.get_job_ids():            # Redis LRANGE 0 -1
            if job_id.startswith(site):
                return True
        for job_id in q.started_job_registry.get_job_ids():
            if job_id.startswith(site):
                return True
    return False
```

`q.get_job_ids()` fetches **every job ID in the entire bench queue** (not filtered by site up
front). For a 30,000-site bench with 120,000 queued jobs, this is 120,000 job IDs returned in
a single LRANGE response. Python then iterates all of them checking `startswith(site)`.

**The condition `ready-for-migration` requires** (queue empty for this site) **is never
satisfiable** on a 30,000-site bench because the scheduler refills the queue faster than workers
drain it. The command will exit with failure code 1 every single time.

---

## Root Cause 2 — `wait_till_ready` timeout is bypassed by a blocking subprocess

### The agent loop (`agent/site.py:522`)

```python
WAIT_TIMEOUT = 60  # 60 seconds

while (time.time() - start) < WAIT_TIMEOUT:
    try:
        output = self.bench_execute("ready-for-migration")
        return data          # ← never reached when queue has 120,000 jobs
    except Exception as e:
        time.sleep(1)
        # retry only if subprocess EXITED with failure
        # if subprocess HANGS, time.time() never advances in this loop
```

`bench_execute` → `docker_execute` → `self.execute(command)` → `run_subprocess` →
`parse_output(process)` in `agent/base.py`:

```python
# agent/base.py:114
def parse_output(self, process) -> str:
    # reads stdout byte-by-byte until process exits
    for char in iter(partial(process.stdout.read, 1), b""):
        ...
    # NO TIMEOUT. Thread blocks here for however long the process runs.
```

If the subprocess hangs, the Python thread is stuck inside `parse_output()`. `time.time()`
is never checked again. `WAIT_TIMEOUT = 60` is never enforced.

**Why the subprocess hangs for exactly 5 minutes**: see Root Cause 3 below.

---

## Root Cause 3 — `bench purge-jobs` does O(N) Redis HGETALL, hangs for 5 minutes

After `WAIT_TIMEOUT` (60s) expires, the agent calls `bench purge-jobs` if
`clear_jobs_on_timeout=True` (the default from `web.py`):

```python
# agent/site.py:539
if clear_jobs_on_timeout:
    data["cleared_job_queues"] = self.clear_job_queues()  # bench purge-jobs
```

### What `bench purge-jobs` does (`frappe/utils/doctor.py:15`)

```python
def purge_pending_jobs(event=None, site=None, queue=None):
    for _queue in get_queue_list(queue):
        q = get_queue(_queue)
        for job in q.jobs:             # ← NOT q.get_job_ids() — fetches FULL job data
            if job.kwargs["site"] == site:
                job.delete()
```

`q.jobs` in RQ fetches the **complete payload** of each job via individual `HGETALL` commands —
one Redis round-trip per job. With 120,000 jobs in the queue:

```
120,000 × HGETALL
```

### Why HGETALL is slow during an active burst

Redis is single-threaded. During the burst, frappe-workers are issuing continuous `BLPOP`
commands (blocking pop — waiting for the next job). All commands queue up behind each other:

```
Redis command queue during burst:
  BLPOP (worker 1, blocking)
  BLPOP (worker 2, blocking)
  BLPOP (worker 3, blocking)
  ...
  HGETALL job_1  ← purge-jobs waiting here
  HGETALL job_2
  HGETALL job_3
  ... 120,000 more
```

At ~150ms per HGETALL under this contention: **120,000 × 150ms = 18,000 seconds**. The
Redis client socket read timeout fires first at **~300 seconds (5 minutes)**, killing the
subprocess.

This explains the screenshot observation: "Wait for Enqueued Jobs" always shows exactly **5m 0s**
and a red failure status. It is not waiting for the queue to drain — it is stuck inside
`bench purge-jobs`'s Redis HGETALL loop until the socket times out.

---

## The Failure Cascade

```
Press sends 500 migration requests
        │
        ▼
Agent enqueues 500 RQ jobs (low-priority queue)
  [RUNNING] Job A  [RUNNING] Job B  [PENDING] Jobs C–500
        │
        ▼ (both workers, simultaneously)
Step 1: enable_maintenance_mode  ← fast, ~1s each
        Site A: maintenance ON
        Site B: maintenance ON
        │
        ▼
Step 2: wait_till_ready
        bench ready-for-migration → subprocess starts
        Scheduler burst is active (120,000 queued jobs)
        any_job_pending finds jobs → exits failure
        ↓ (loop retries every 4-5 seconds)
        After 60s: WAIT_TIMEOUT exceeded
        bench purge-jobs called → 120,000 × HGETALL
        ↓ (stuck for 5 minutes in parse_output)
        Redis socket timeout fires at 300s
        subprocess exits non-zero → AgentException
        Job A fails
        │
        ▼
Step 13 (disable_maintenance_mode) ← NEVER REACHED
        Site A: maintenance STAYS ON forever
        Site B: same
        │
        ▼
Next 2 jobs picked up (Sites C and D)
        Sites C, D → maintenance ON → same 5-min failure
        │
        ▼
Repeat: 2 more sites per 5 minutes enter maintenance
        500 sites total enter maintenance over ~20 hours
        Each failed purge-jobs is partial (timed out mid-scan)
        Each scheduler burst adds 120,000 more jobs before purge completes
        Queue grows: 120,000 → 240,000 → ... → millions
```

**Net result**: Every site returns 503. Queue is unbounded. System is in a degraded state
that self-reinforces.

---

## Evidence

| Observation | Explanation |
|---|---|
| "Wait for Enqueued Jobs" fails at exactly **5m 0s** | Redis socket read timeout (300s) inside `bench purge-jobs` HGETALL loop |
| All subsequent steps are **grayed out** | Job failed at step 2; steps 3–14 never execute |
| **Maintenance mode never turns off** | Step 13 (`disable_maintenance_mode`) only runs after step 2 succeeds |
| Bench queue grows to **30,000+ jobs** | Partial purge + scheduler bursts accumulating; each burst adds 120,000 jobs while purge times out |
| **Works fine for 1–2 sites** | Small bench → burst drains in seconds → queue empty → `ready-for-migration` succeeds |
| **2 agent workers blocked** | `parse_output()` holds each worker thread for 5 minutes with no escape |
| **498 jobs just sitting idle** | They are queued in agent Redis but no worker is free to pick them up |

---

## Why Normal Migrations Are Not Affected

For a bench with 50–100 sites, the scheduler burst is ~300–500 jobs. At 14 jobs/sec it drains
in 20–35 seconds. `bench ready-for-migration` retries every ~4 seconds; within 60 seconds it
catches the empty window and succeeds. `bench purge-jobs` is never called. Everything works.

The failure mode is **scale-triggered**: it only manifests when burst drain time > WAIT_TIMEOUT.

**Threshold**: burst drain time > 60 seconds → failure guaranteed
→ sites × 4 jobs / 14 jobs/sec > 60s
→ sites > **210 sites on the bench**

Any bench with more than ~210 sites is at risk when doing a mass deployment.

---

## Files Involved

| File | Relevant Code | Issue |
|---|---|---|
| `agent/base.py:92` | `run_subprocess` / `parse_output` | No subprocess timeout; blocks indefinitely |
| `agent/site.py:522` | `wait_till_ready` | `WAIT_TIMEOUT=60` bypassed when subprocess hangs |
| `agent/site.py:539` | `clear_job_queues` → `bench purge-jobs` | O(N) HGETALL; hangs 5 min under load |
| `agent/server.py:472` | `clear_jobs_on_timeout=False` default | Web.py overrides this to `True`, enabling the slow purge path |
| `agent/web.py:891` | `data.get("clear_jobs_on_timeout", True)` | Default enables `bench purge-jobs` on every timeout |
| `frappe/utils/doctor.py:15` | `purge_pending_jobs` → `q.jobs` loop | Per-job HGETALL instead of bulk operation |
| `frappe/commands/scheduler.py:231` | `ready_for_migration` | Correct logic, but condition unsatisfiable at 30,000 sites |
