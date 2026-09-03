from __future__ import annotations

import json
import os
import re
import unittest
from contextlib import contextmanager
from unittest.mock import PropertyMock, patch

from agent.job_purge import (
    PURGE_SCRIPT,
    RESULT_MARKER,
    SWEEP_BUDGET,
    build_purge_command,
    parse_purge_result,
)


def extract_lua_sweep() -> str:
    """The Lua script the container runs, pulled out of PURGE_SCRIPT.

    The sweep is the one piece that has to be exactly right -- it mutates a
    live queue shared with other sites -- so it is tested directly rather than
    through a container.
    """
    match = re.search(r'SWEEP = """(.*?)"""', PURGE_SCRIPT, re.S)
    assert match, "SWEEP script not found in PURGE_SCRIPT"
    return match.group(1)


REDIS_URL = os.environ.get("AGENT_TEST_REDIS_URL", "redis://127.0.0.1:11000")
TEST_QUEUE_KEY = "agent:test:job_purge:queue"


def run_sweep(queue: list[str], site_prefix: str, budget: int = SWEEP_BUDGET):
    """Execute the real Lua sweep against a real Redis, as production will.

    Testing the script through Redis rather than a Lua stub is the point: the
    sweep mutates a queue list shared with every other site on the bench, so
    what matters is how Redis itself evaluates it.
    """
    try:
        import redis as redis_module
    except ImportError:
        raise unittest.SkipTest("redis client not installed") from None

    connection = redis_module.from_url(REDIS_URL, decode_responses=True)
    try:
        connection.ping()
    except Exception as error:
        raise unittest.SkipTest(f"no redis at {REDIS_URL}: {error}") from None

    job_keys = [f"rq:job:{job_id}" for job_id in queue]
    job_keys += [f"rq:job:{job_id}:dependents" for job_id in queue]
    connection.delete(TEST_QUEUE_KEY, *job_keys) if job_keys else connection.delete(TEST_QUEUE_KEY)
    try:
        if queue:
            connection.rpush(TEST_QUEUE_KEY, *queue)
            for job_id in queue:
                connection.set(f"rq:job:{job_id}", "payload")
                connection.sadd(f"rq:job:{job_id}:dependents", "child")

        sweep = connection.register_script(extract_lua_sweep())
        total_purged = 0
        remaining = len(queue)
        while remaining > 0:
            purged, rotated = sweep(
                keys=[TEST_QUEUE_KEY],
                args=["rq:job:", site_prefix, min(budget, remaining)],
            )
            if not rotated:
                break
            total_purged += purged
            remaining -= rotated

        survivors = connection.lrange(TEST_QUEUE_KEY, 0, -1)
        deleted = [key for key in job_keys if not connection.exists(key)]
        return total_purged, survivors, deleted
    finally:
        connection.delete(TEST_QUEUE_KEY, *job_keys) if job_keys else connection.delete(TEST_QUEUE_KEY)


class TestPurgeCommand(unittest.TestCase):
    def test_site_name_and_options_are_shell_quoted_into_the_command(self):
        command = build_purge_command("site-1.example.com", 60, force_stop=False)
        self.assertIn("site-1.example.com", command)
        self.assertTrue(command.startswith("/home/frappe/frappe-bench/env/bin/python -"))

    def test_force_stop_is_passed_as_an_explicit_flag_and_defaults_off(self):
        self.assertIn(" 0 ", build_purge_command("site.example.com", 60, force_stop=False))
        self.assertIn(" 1 ", build_purge_command("site.example.com", 60, force_stop=True))

    def test_grace_seconds_is_coerced_to_an_integer_so_it_cannot_inject_arguments(self):
        command = build_purge_command("site.example.com", "60", force_stop=False)
        self.assertIn(" 60 ", command)

    def test_result_is_parsed_from_the_marker_line_ignoring_other_container_output(self):
        output = (
            "WARN: some deprecation notice\n"
            + RESULT_MARKER
            + json.dumps({"total_purged": 52000})
            + "\nbye\n"
        )
        self.assertEqual(parse_purge_result(output)["total_purged"], 52000)

    def test_missing_marker_raises_with_the_output_tail_for_debugging(self):
        with self.assertRaises(ValueError) as error:
            parse_purge_result("Traceback: redis connection refused")
        self.assertIn("redis connection refused", str(error.exception))


class TestLuaSweep(unittest.TestCase):
    """The sweep must purge only the target site and preserve everyone else."""

    def test_only_the_target_sites_jobs_are_purged(self):
        queue = ["a.example.com::1", "b.example.com::1", "a.example.com::2"]
        purged, survivors, _ = run_sweep(queue, "a.example.com::")
        self.assertEqual(purged, 2)
        self.assertEqual(survivors, ["b.example.com::1"])

    def test_a_site_whose_name_prefixes_another_is_not_purged_with_it(self):
        # The bug this guards: frappe's own filter_current_site_jobs uses a bare
        # startswith(site), so purging workspace-138 would take workspace-1380
        # with it. Sequential site names make that collision real.
        queue = ["workspace-138.example.com::1", "workspace-1380.example.com::1"]
        purged, survivors, _ = run_sweep(queue, "workspace-138.example.com::")
        self.assertEqual(purged, 1)
        self.assertEqual(survivors, ["workspace-1380.example.com::1"])

    def test_surviving_jobs_keep_their_original_order(self):
        queue = [f"other.example.com::{i}" for i in range(10)]
        queue.insert(5, "mine.example.com::x")
        purged, survivors, _ = run_sweep(queue, "mine.example.com::")
        self.assertEqual(purged, 1)
        self.assertEqual(survivors, [f"other.example.com::{i}" for i in range(10)])

    def test_order_is_preserved_across_a_chunked_sweep(self):
        # A small budget forces several passes; each one moves survivors to the
        # tail, so a wrong rotation count would either re-examine them or
        # strand purgeable jobs behind them.
        queue = []
        for i in range(50):
            queue.append(f"other.example.com::{i}")
            queue.append(f"mine.example.com::{i}")
        purged, survivors, _ = run_sweep(queue, "mine.example.com::", budget=7)
        self.assertEqual(purged, 50)
        self.assertEqual(survivors, [f"other.example.com::{i}" for i in range(50)])

    def test_purged_jobs_have_their_hash_and_dependents_keys_deleted(self):
        _, _, deleted = run_sweep(["mine.example.com::x"], "mine.example.com::")
        self.assertIn("rq:job:mine.example.com::x", deleted)
        self.assertIn("rq:job:mine.example.com::x:dependents", deleted)

    def test_an_empty_queue_is_a_no_op(self):
        purged, survivors, deleted = run_sweep([], "mine.example.com::")
        self.assertEqual((purged, survivors, deleted), (0, [], []))

    def test_a_queue_with_no_jobs_for_this_site_is_left_untouched(self):
        queue = [f"other.example.com::{i}" for i in range(20)]
        purged, survivors, deleted = run_sweep(queue, "mine.example.com::", budget=3)
        self.assertEqual(purged, 0)
        self.assertEqual(survivors, queue)
        self.assertEqual(deleted, [])


if __name__ == "__main__":
    unittest.main()


class TestBurstWorkers(unittest.TestCase):
    """Burst workers must be addressable without touching the main workers."""

    @contextmanager
    def _server(self, config):
        """A Server with a stubbed config -- `config` is a read-only property
        that reads the real config.json off disk."""
        from agent.server import Server

        with patch.object(Server, "config", new_callable=PropertyMock, return_value=config):
            yield Server.__new__(Server)

    def test_worker_count_defaults_for_servers_provisioned_before_burst_workers(self):
        with self._server({"workers": 2}) as server:
            self.assertEqual(server.burst_worker_count, 4)

    def test_configured_worker_count_is_honoured(self):
        with self._server({"workers": 2, "burst_workers": 8}) as server:
            self.assertEqual(server.burst_worker_count, 8)

    def test_process_names_target_only_burst_workers_never_the_main_worker_program(self):
        with self._server({"workers": 2, "burst_workers": 3}) as server:
            names = server._burst_worker_names()
        self.assertEqual(names, "agent:burst_worker-0 agent:burst_worker-1 agent:burst_worker-2")
        # The whole point of a separate program: nothing here can bounce
        # agent:worker-N and take an in-flight site update with it.
        self.assertNotIn("agent:worker", names.replace("agent:burst_worker", ""))


class TestPurgeIsNonFatal(unittest.TestCase):
    """A failed purge must degrade to the old behaviour, never abort an update."""

    def _site(self):
        from agent.site import Site

        site = Site.__new__(Site)
        site.name = "site.example.com"
        return site

    def test_a_successful_purge_reports_true(self):
        from agent.site import Site

        site = self._site()
        with patch.object(Site, "purge_pending_jobs", return_value={"total_purged": 52000}):
            self.assertTrue(site.purge_pending_jobs_if_possible())

    def test_a_purge_that_raises_is_swallowed_and_reports_false(self):
        # This runs on every site update. A bug in the purge must not abort a
        # migration that would otherwise have succeeded.
        from agent.site import Site

        site = self._site()
        with patch.object(Site, "purge_pending_jobs", side_effect=RuntimeError("container gone")):
            self.assertFalse(site.purge_pending_jobs_if_possible())

    def test_the_fallback_window_is_never_shorter_than_the_original(self):
        from agent.job_purge import WAIT_TIMEOUT_AFTER_PURGE, WAIT_TIMEOUT_WITHOUT_PURGE

        self.assertEqual(WAIT_TIMEOUT_WITHOUT_PURGE, 300, "must match the pre-existing timeout")
        self.assertLess(WAIT_TIMEOUT_AFTER_PURGE, WAIT_TIMEOUT_WITHOUT_PURGE)

    def test_callers_that_do_not_purge_keep_the_original_window(self):
        import inspect

        from agent.job_purge import WAIT_TIMEOUT_WITHOUT_PURGE
        from agent.site import Site

        default = inspect.signature(Site.wait_till_ready).parameters["timeout"].default
        self.assertEqual(default, WAIT_TIMEOUT_WITHOUT_PURGE)


class TestWorkerConfigPatchNeverBreaksAgentUpdate(unittest.TestCase):
    def test_a_failure_is_swallowed_because_a_throwing_patch_fails_the_agent_update(self):
        # PatchHandler.execute() re-raises and run_patches() does not catch, so
        # a throwing patch fails the whole agent update on every server it runs
        # on. Worker counts are not worth that.
        from agent.patches import raise_gunicorn_and_burst_workers as patch_module

        with patch.object(patch_module, "_apply", side_effect=OSError("read-only file system")):
            patch_module.execute()  # must not raise
