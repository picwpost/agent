from __future__ import annotations

import contextlib
import traceback


def needs_update(config: dict) -> bool:
    return config.get("gunicorn_workers") == 2 or "burst_workers" not in config


def execute():
    """Raise gunicorn_workers off the old default of 2 and add burst_workers.

    `agent setup config` wrote gunicorn_workers=2 explicitly on every server
    provisioned before this, so the raised default in cli.py alone would never
    reach them -- the key is present, so the fallback never applies.

    Only rewrites a value still sitting at the old default, so a server an
    operator has deliberately tuned is left alone.

    Never raises. PatchHandler.execute() re-raises and run_patches() does not
    catch, so a throwing patch fails the whole agent update -- on every server
    it runs on. Worker counts are a performance tweak: not worth breaking agent
    updates fleet-wide over an unwritable config file or a held lock. A failure
    here just leaves the old worker counts in place, and the next agent update
    retries it because the patch is only logged as done on success.
    """
    try:
        _apply()
    except Exception:
        print("Could not tune agent worker counts; leaving them unchanged.")
        print(traceback.format_exc())


def _apply():
    from agent.server import Server

    server = Server()
    if not needs_update(server.config):
        # Decided without taking the config lock, so there is no lock to hand
        # back in the common case where this has already run.
        return

    config = server.get_config(for_update=True)
    try:
        if config.get("gunicorn_workers") == 2:
            config["gunicorn_workers"] = 4
        config.setdefault("burst_workers", 4)
        server.set_config(config, indent=4)
    except Exception:
        # set_config releases the lock on success. On failure it would be held
        # until this object is garbage collected, blocking every later config
        # write in the process, so hand it back before letting this fail.
        with contextlib.suppress(Exception):
            server._config_file_lock.release()
        raise

    print(f"Raised agent workers: {config['gunicorn_workers']} web, {config['burst_workers']} burst")
