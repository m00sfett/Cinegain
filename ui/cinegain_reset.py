"""
Factory reset: puts Cinegain back into the state of a fresh installation.

What it does, in order:
1. Copies the complete user state into a timestamped backup directory.
2. Moves every stream off Cinegain, restores 100 % volume and unmutes the
   streams of all applications Cinegain has ever seen.
3. Stops all profile sinks.
4. Deletes state.json, user_presets.json, config.json and apps.json.
5. Removes sticky Cinegain targets from WirePlumber's stream-properties.

The standard sink itself is handled by the caller (CLI), which knows whether
it was running and restarts it with the default parameters.
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


def reset_everything(pulse=None, sinks=None, backup=True):
    """Runs the full reset. Returns a dict describing what was done."""
    pulse = pulse or PulseControl()
    report = {"backup": None, "streams_moved": 0, "volumes_restored": 0,
              "files_removed": [], "profiles_stopped": [], "wireplumber_cleaned": False}

    if backup:
        report["backup"] = backup_user_state()

    known = {k for k in AppRegistry().apps}
    sink_ids = pulse.list_sinks()
    hw = pulse.hardware_sink(sink_ids)
    for st in pulse.list_streams():
        if st.is_own:
            continue
        if is_cinegain_name(sink_ids.get(st.sink, "")):
            pulse.move(st.index, hw)
            report["streams_moved"] += 1
        if make_key(st.app_name or st.node_name, st.binary) in known and (
            st.volume_pct != 100 or st.muted
        ):
            pulse.set_volume_100(st.index)
            report["volumes_restored"] += 1

    if sinks is not None:
        report["profiles_stopped"] = sinks.stop_all_profiles()

    for path in paths.USER_STATE_FILES:
        try:
            os.remove(path)
            report["files_removed"].append(path)
        except FileNotFoundError:
            pass

    report["wireplumber_cleaned"] = clean_wireplumber_targets(paths.WIREPLUMBER_STREAM_PROPS)
    return report
