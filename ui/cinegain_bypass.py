"""
"Filter off" (bypass) shared by the GUI, the tray and the CLI (#33).

Every Cinegain filter passes audio through untouched while bypass is on, and
streams stay where they are. The state lives in a runtime file, like overdrive,
so the CLI knows it: `set`, `batch`, `preset` and `start` keep the filter in
the state the user chose instead of silently switching it on. The file lives in
the runtime directory (gone after a reboot) and is cleared on `cinegain stop`.
"""
import os

import cinegain_log
import cinegain_paths as paths

log = cinegain_log.get("bypass")


def flag_path():
    return os.path.join(paths.RUNTIME_DIR, "bypass")


def is_on():
    """True while the filter is switched off (pass-through)."""
    return os.path.exists(flag_path())


def set_on(on):
    """Switches the pass-through state; returns True when it changed."""
    path = flag_path()
    if bool(on) == is_on():
        return False
    if on:
        os.makedirs(paths.RUNTIME_DIR, mode=0o700, exist_ok=True)
        with open(path, "w") as f:
            f.write("1\n")
    else:
        try:
            os.remove(path)
        except OSError:
            pass
    log.info("bypass flag %s", "set (filter off)" if on else "cleared (filter on)")
    return True
