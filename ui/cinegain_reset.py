"""
Factory reset: puts Cinegain back into the state of a fresh installation.

What it does, in order:
1. Copies the complete user state into a timestamped backup directory.
2. Moves every stream off Cinegain (or, when the caller keeps the standard
   sink running, from per-program sinks straight to it), restores 100 % volume
   and unmutes the streams of all applications Cinegain has ever seen.
3. Stops all profile sinks.
4. Deletes state.json, user_presets.json, config.json and apps.json.
5. Removes sticky Cinegain targets from WirePlumber's stream-properties and
   makes the running WirePlumber load the cleaned file (#26).

The standard sink itself is handled by the caller (CLI): a running one keeps
running and gets the default parameters pushed live, because restarting it
pauses browser players (#55).
"""
import os
import shutil
import time

import cinegain_paths as paths
from cinegain_pulse import PulseControl, clean_wireplumber_targets, is_cinegain_name
from cinegain_registry import AppRegistry, make_key


def backup_user_state(backup_root=None, clock=time.time):
    """Copies all existing user state files; returns the backup dir or None if nothing existed."""
    existing = [p for p in paths.USER_STATE_FILES if os.path.exists(p)]
    if os.path.exists(paths.WIREPLUMBER_STREAM_PROPS):
        existing.append(paths.WIREPLUMBER_STREAM_PROPS)
    if not existing:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    root = paths.private_dir(backup_root or paths.BACKUP_DIR)  # backups hold user state: 0700 (#42, #48)
    target = os.path.join(root, stamp)
    suffix = 1
    while os.path.exists(target):
        suffix += 1
        target = os.path.join(root, f"{stamp}-{suffix}")
    paths.private_dir(target)
    for src in existing:
        name = os.path.basename(src)
        if src == paths.WIREPLUMBER_STREAM_PROPS:
            name = "wireplumber-stream-properties"
        shutil.copy2(src, os.path.join(target, name))
    return target


MOVE_CONFIRM_TIMEOUT_S = 3.0
MOVE_SETTLE_S = 0.5


def wait_for_moves(pulse, moved, target, timeout=MOVE_CONFIRM_TIMEOUT_S, settle=MOVE_SETTLE_S,
                   clock=time.monotonic, sleep=time.sleep):
    """
    Waits until every moved stream reports the target sink (or is gone), then a short
    settle time, so a sink is never stopped while a stream still hangs on it (#55).
    Returns True when all moves were confirmed before the timeout.
    """
    pending = set(moved)
    deadline = clock() + timeout
    while pending:
        sink_ids = pulse.list_sinks()
        live = {st.index: sink_ids.get(st.sink, "") for st in pulse.list_streams()}
        pending = {i for i in pending if i in live and live[i] != target}
        if not pending:
            break
        if clock() >= deadline:
            return False
        sleep(0.1)
    if moved:
        sleep(settle)
    return True


def clean_wireplumber_live(keep=(), runner=None, sleep=None):
    """
    Removes sticky Cinegain targets from stream-properties. For the real file the
    running WirePlumber reloads the cleaned file; otherwise it writes its old copy
    back about a second later and the stale targets return (#26).
    Returns (cleaned, "reloaded" | "failed" | "skipped").
    """
    path = paths.WIREPLUMBER_STREAM_PROPS

    def clean():
        return clean_wireplumber_targets(path, keep=keep)

    if os.path.abspath(path) != os.path.abspath(paths.DEFAULT_WP_STREAM_PROPS):
        return clean(), "skipped"  # tests: never touch the live WirePlumber
    import cinegain_volume
    kw = {k: v for k, v in (("runner", runner), ("sleep", sleep)) if v is not None}
    cleaned, ok = cinegain_volume.reload_wireplumber_state(clean, **kw)
    return cleaned, "reloaded" if ok else "failed"


def reset_everything(pulse=None, sinks=None, backup=True, keep_sink=None):
    """
    Runs the full reset. Returns a dict describing what was done.

    keep_sink names a Cinegain sink that stays running (the standard sink): streams
    on it stay, streams on other Cinegain sinks move straight to it instead of
    bouncing through the hardware sink (#55).
    """
    pulse = pulse or PulseControl()
    report = {"backup": None, "streams_moved": 0, "volumes_restored": 0,
              "files_removed": [], "profiles_stopped": [], "wireplumber_cleaned": False}

    if backup:
        report["backup"] = backup_user_state()

    known = {k for k in AppRegistry().apps}
    sink_ids = pulse.list_sinks()
    keep_alive = keep_sink is not None and keep_sink in sink_ids.values()
    target = keep_sink if keep_alive else pulse.hardware_sink(sink_ids)
    moved = []
    for st in pulse.list_streams():
        if st.is_own:
            continue
        name = sink_ids.get(st.sink, "")
        if is_cinegain_name(name) and not (keep_alive and name == keep_sink):
            pulse.move(st.index, target)
            moved.append(st.index)
            report["streams_moved"] += 1
        if make_key(st.app_name or st.node_name, st.binary) in known and (
            st.volume_pct != 100 or st.muted
        ):
            pulse.set_volume_100(st.index)
            report["volumes_restored"] += 1

    # stop the old sinks only once the streams have left them (#55)
    report["moves_confirmed"] = wait_for_moves(pulse, moved, target)

    if sinks is not None:
        report["profiles_stopped"] = sinks.stop_all_profiles()

    for path in paths.USER_STATE_FILES:
        try:
            os.remove(path)
            report["files_removed"].append(path)
        except FileNotFoundError:
            pass

    keep = {keep_sink} if keep_alive else ()
    report["wireplumber_cleaned"], report["wireplumber"] = clean_wireplumber_live(keep)
    return report
