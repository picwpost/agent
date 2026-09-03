"""Purge a single site's pending background jobs before a migration.

`Wait for Enqueued Jobs` blocks a site update until `bench ready-for-migration`
reports a clear queue, and that command fails while any RQ job id namespaced to
the site sits in a queue list or in the started registry. A site carrying tens
of thousands of queued jobs can never clear that check inside its window, so
the update dies after burning the whole timeout.

This module runs a purge inside the bench container immediately after
maintenance mode goes on -- which is what stops the scheduler from refilling
the queue -- so that `Wait for Enqueued Jobs` becomes a fast confirmation
rather than an open-ended wait.
"""

from __future__ import annotations

import json
from shlex import quote

# Marks the payload line so it can be picked out of whatever else the container
# writes to stdout (deprecation warnings, redis notices, and the like).
RESULT_MARKER = "__AGENT_JOB_PURGE_RESULT__"

# How long to let jobs that are genuinely mid-execution finish on their own.
# Anything still running past this is reported, and only force-stopped when the
# caller explicitly asks for it.
DEFAULT_GRACE_SECONDS = 60

# Queue entries rotated per Lua call. Each call blocks Redis for its duration,
# so this bounds that to a few tens of milliseconds while still clearing a
# 52k-job queue in a handful of round trips.
SWEEP_BUDGET = 10000

BENCH_PYTHON = "/home/frappe/frappe-bench/env/bin/python"

# After a successful purge the readiness gate passes on the first try, so a long
# window buys nothing. Without one, fall back to the original 300s -- a failed
# purge must never leave the update with less time than it had before.
WAIT_TIMEOUT_AFTER_PURGE = 60
WAIT_TIMEOUT_WITHOUT_PURGE = 300

PURGE_SCRIPT = r'''
import json
import sys
import time

import frappe
from frappe.utils.background_jobs import get_queue, get_queue_list, get_redis_conn
from rq.command import send_stop_job_command
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job, JobStatus
from rq.worker import Worker

SITE = sys.argv[1]
GRACE_SECONDS = int(sys.argv[2])
FORCE_STOP = sys.argv[3] == "1"
SWEEP_BUDGET = int(sys.argv[4])
RESULT_MARKER = sys.argv[5]

# Exactly the namespace frappe.utils.background_jobs.create_job_id() builds.
# The trailing separator is load-bearing: frappe's own filter_current_site_jobs
# uses a bare startswith(site), which would also match "workspace-1380..."
# while purging "workspace-138...". Sequential site names make that a real
# collision, so match the separator too and the prefix becomes exact.
PREFIX = SITE + "::"

# Rotates the head of the queue list through itself, dropping this site's jobs
# and re-pushing everyone else's in their original order. Atomic, and O(N) for
# the whole queue -- Job.delete() does an LREM per job, which is O(N) each and
# so O(N^2) overall, about 2.7e9 list comparisons at 52k jobs.
SWEEP = """
local queue_key   = KEYS[1]
local job_prefix  = ARGV[1]
local site_prefix = ARGV[2]
local budget      = tonumber(ARGV[3])
local length      = redis.call("llen", queue_key)
if budget > length then budget = length end
local purged = 0
for _ = 1, budget do
    local job_id = redis.call("lpop", queue_key)
    if job_id == false then break end
    if string.sub(job_id, 1, string.len(site_prefix)) == site_prefix then
        redis.call("del", job_prefix .. job_id, job_prefix .. job_id .. ":dependents")
        purged = purged + 1
    else
        redis.call("rpush", queue_key, job_id)
    end
end
return {purged, budget}
"""

JOB_PREFIX = Job.redis_job_namespace_prefix


def site_job_ids(registry):
    return [job_id for job_id in registry.get_job_ids() if job_id and job_id.startswith(PREFIX)]


def delete_job_keys(connection, job_id):
    connection.delete(JOB_PREFIX + job_id, JOB_PREFIX + job_id + ":dependents")


def sweep_queue(sweep, queue):
    """Examine every entry the queue holds right now, exactly once.

    Survivors are re-pushed to the tail, so a chunked sweep has to rotate
    precisely as many entries as the queue held when it started: rotate more
    and it re-examines survivors, rotate fewer and it leaves this site's jobs
    stranded behind the ones it just moved.
    """
    remaining = queue.count
    purged = 0
    while remaining > 0:
        chunk_purged, rotated = sweep(
            keys=[queue.key],
            args=[JOB_PREFIX, PREFIX, min(SWEEP_BUDGET, remaining)],
        )
        if not rotated:
            break
        purged += chunk_purged
        remaining -= rotated
    return purged


def purge_registry(connection, registry):
    """Drop this site's entries from a ZSET-backed registry.

    Covers deferred jobs (waiting on a dependency) and scheduled jobs (waiting
    on a clock). Neither sits in the queue list, so the Lua sweep never sees
    them, but both become queued jobs later and would land mid-migration.
    """
    job_ids = site_job_ids(registry)
    for job_id in job_ids:
        registry.remove(job_id)
        delete_job_keys(connection, job_id)
    return len(job_ids)


def reap_stale_started(connection, registry, live_workers):
    """Remove this site's dead entries from the started registry.

    registry.cleanup() is RQ's own handling for abandoned jobs, but it only
    fires once a job is past timeout + 60s -- 26 minutes on the `long` queue.
    An entry whose job hash is already gone, or whose worker is no longer
    heartbeating, is dead right now, and is exactly what leaves
    `ready-for-migration` blocked with no actual work in flight.
    """
    registry.cleanup()
    reaped = []
    for job_id in site_job_ids(registry):
        try:
            job = Job.fetch(job_id, connection=connection)
            status = job.get_status()
        except (NoSuchJobError, AttributeError):
            registry.remove(job_id)
            delete_job_keys(connection, job_id)
            reaped.append(job_id)
            continue
        worker_gone = job.worker_name and job.worker_name not in live_workers
        if status != JobStatus.STARTED or worker_gone:
            registry.remove(job_id)
            delete_job_keys(connection, job_id)
            reaped.append(job_id)
    return reaped


def running_job_ids(queues):
    running = []
    for queue in queues:
        running.extend(site_job_ids(queue.started_job_registry))
    return sorted(set(running))


def main():
    frappe.init(SITE)
    connection = get_redis_conn()
    sweep = connection.register_script(SWEEP)
    queues = [get_queue(qtype) for qtype in get_queue_list()]
    live_workers = {worker.name for worker in Worker.all(connection=connection)}

    result = {
        "site": SITE,
        "grace_seconds": GRACE_SECONDS,
        "force_stop_requested": FORCE_STOP,
        "purged": {},
        "deferred_purged": {},
        "scheduled_purged": {},
        "stale_reaped": [],
        "force_stopped": [],
    }

    for queue in queues:
        result["purged"][queue.name] = sweep_queue(sweep, queue)
        result["deferred_purged"][queue.name] = purge_registry(connection, queue.deferred_job_registry)
        result["scheduled_purged"][queue.name] = purge_registry(connection, queue.scheduled_job_registry)
        result["stale_reaped"].extend(
            reap_stale_started(connection, queue.started_job_registry, live_workers)
        )

    result["running_at_start"] = running_job_ids(queues)

    # A job that was already executing when the sweep ran can enqueue children
    # behind it, so let the few genuinely running jobs finish before calling the
    # queue clear -- then sweep once more for whatever they left.
    deadline = time.time() + GRACE_SECONDS
    while time.time() < deadline and running_job_ids(queues):
        time.sleep(2)

    still_running = running_job_ids(queues)
    if still_running and FORCE_STOP:
        for job_id in still_running:
            try:
                send_stop_job_command(connection=connection, job_id=job_id)
                result["force_stopped"].append(job_id)
            except (InvalidJobOperation, NoSuchJobError):
                pass
        time.sleep(2)

    for queue in queues:
        result["purged"][queue.name] += sweep_queue(sweep, queue)

    result["running_at_end"] = running_job_ids(queues)
    result["total_purged"] = sum(result["purged"].values())
    print(RESULT_MARKER + json.dumps(result, sort_keys=True))


try:
    main()
finally:
    frappe.destroy()
'''


def build_purge_command(site: str, grace_seconds: int, force_stop: bool) -> str:
    """Read the script from stdin rather than -c, so nothing has to be escaped."""
    return " ".join(
        [
            BENCH_PYTHON,
            "-",
            quote(site),
            quote(str(int(grace_seconds))),
            "1" if force_stop else "0",
            quote(str(SWEEP_BUDGET)),
            quote(RESULT_MARKER),
        ]
    )


def parse_purge_result(output: str) -> dict:
    for line in reversed(output.splitlines()):
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER) :])
    raise ValueError(f"Job purge did not report a result. Output tail:\n{output[-2000:]}")
