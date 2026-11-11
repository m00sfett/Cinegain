"""
Volume scale and overdrive (user decisions 2026-11-11, grill round 0.5.0).

The hero control (input_db) and the dynamic make-up gain normally reach +15 dB.
Overdrive widens that to +40 dB. It needs an explicit confirmation, lasts only
until the app (the process holding the router lock) restarts, and switching it
off drops every value above +15 dB at once (turning down is always instant).

The switch lives in a runtime file so the GUI, the tray and the CLI share it;
the router owner clears it on start.
"""
import os

import cinegain_log
import cinegain_paths as paths

log = cinegain_log.get("overdrive")

MIN_DB = -30.0
NORMAL_MAX_DB = 15.0
OVERDRIVE_MAX_DB = 40.0
CAPPED_KEYS = ("input_db", "makeup_db")
MAKEUP_MIN_DB = -24.0


def flag_path():
    return os.path.join(paths.RUNTIME_DIR, "overdrive")


def is_on():
    return os.path.exists(flag_path())


def set_on(on):
    """Switches overdrive; returns True when the state changed."""
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
    log.warning("overdrive %s", "ON (up to +40 dB)" if on else "off (capped at +15 dB)")
    return True


def max_db(on=None):
    return OVERDRIVE_MAX_DB if (is_on() if on is None else on) else NORMAL_MAX_DB


def clamp_value(key, value, on=None):
    if key not in CAPPED_KEYS:
        return value
    low = MAKEUP_MIN_DB if key == "makeup_db" else MIN_DB
    return max(low, min(max_db(on), float(value)))


def clamp_params(params, on=None):
    """Copy of a flat parameter set with the capped keys inside the current scale."""
    if not params:
        return params
    top = max_db(on)
    return {k: (clamp_value(k, v, top > NORMAL_MAX_DB) if k in CAPPED_KEYS else v)
            for k, v in params.items()}


def exceeds(params, on=False):
    """True when a parameter set holds a value the given scale would cut."""
    return any(float((params or {}).get(k, 0.0)) > max_db(on) for k in CAPPED_KEYS)
