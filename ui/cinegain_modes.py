"""
Two ways a target (Standard or a program with its own setting) can sound:

- "fixed" (Fest, the main feature): a fixed volume boost plus a fixed tone
  (voices, bass, treble, stereo width). Nothing analyses the signal; leveler and
  compressor stay neutral. The look-ahead limiter always guards against clipping.
- "dynamic" (Dynamisch · Beta): the full film-sound chain (leveler, compressor,
  speech focus) as before.

Each mode keeps its own complete set of values (user decision 2026-11-11, Q17b),
so switching back and forth never loses anything. The filter always receives the
"effective" parameters of the active mode; that is what `params` in apps.json
and state.json hold, so routing and older readers keep working.

Both modes run in the same LADSPA plugin: switching only pushes parameters, no
sink restart (see notes/spaeter-analysieren.md for the deferred analysis).
"""
import cinegain_overdrive as overdrive
import cinegain_paths as paths
from cinegain_profiles import DEFAULT_PARAMS, full_params, normalize_params

MODE_FIXED = "fixed"
MODE_DYNAMIC = "dynamic"
MODES = (MODE_FIXED, MODE_DYNAMIC)

# Sliders both modes share; they sit in the same place in both layouts.
FIXED_KEYS = ("input_db", "dialog_db", "bass_db", "treble_db", "side_db")
# Sliders only the dynamic mode shows (below the shared ones).
DYNAMIC_ONLY_KEYS = ("lev_max_boost_db", "mid_db", "lev_target_db", "comp_thresh_db",
                     "comp_ratio", "makeup_db", "lim_ceiling_db")
DYNAMIC_KEYS = FIXED_KEYS + DYNAMIC_ONLY_KEYS

# Limiter ceiling in fixed mode: a little headroom for inter-sample peaks at high boosts.
FIXED_CEILING_DB = -1.0
LEVELER_DEFAULT_CUT_DB = 6.0

# Factory presets that are plain volume steps belong to the fixed mode.
FIXED_PRESETS = ("clean_linear", "boost_3db", "boost_6db", "boost_9db", "boost_12db", "boost_15db")


def neutral_fixed():
    return {k: 0.0 for k in FIXED_KEYS}


def neutral_dynamic():
    return {k: float(DEFAULT_PARAMS[k]) for k in DYNAMIC_KEYS}


def fixed_effective(fixed_set):
    params = dict(DEFAULT_PARAMS)
    for k in FIXED_KEYS:
        params[k] = float((fixed_set or {}).get(k, 0.0))
    params["lim_ceiling_db"] = FIXED_CEILING_DB
    params["bypass"] = 0.0
    return params


def dynamic_effective(dynamic_set):
    params = full_params(dynamic_set)
    # Same rule as since 0.3.3: boost > 0 means leveler on, and a leveler that can
    # only boost never pulls loud scenes back.
    params["leveler"] = 1.0 if params["lev_max_boost_db"] > 0.05 else 0.0
    if params["leveler"] and params["lev_max_cut_db"] <= 0.05:
        params["lev_max_cut_db"] = LEVELER_DEFAULT_CUT_DB
    params["bypass"] = 0.0
    return params


def effective(mode, sets, overdrive_on=None):
    """Parameters the filter gets; volume and make-up stay inside the current scale."""
    sets = sets or {}
    if mode == MODE_DYNAMIC:
        params = dynamic_effective(sets.get(MODE_DYNAMIC))
    else:
        params = fixed_effective(sets.get(MODE_FIXED))
    return overdrive.clamp_params(params, overdrive_on)


def clamp_sets(sets, overdrive_on=None):
    """Copy of both value sets with volume and make-up inside the current scale."""
    sets = clean_sets(sets)
    return {m: overdrive.clamp_params(v, overdrive_on) for m, v in sets.items()}


def clamp_stored(registry, state_path=None, overdrive_on=False):
    """
    Caps every saved program setting and Standard to the current scale (after a
    restart or when overdrive goes off). Returns the keys that changed
    ("standard" for Standard).
    """
    changed = []
    for entry in registry.entries():
        key = entry["key"]
        if entry.get("mode") in MODES and entry.get("sets"):
            capped = clamp_sets(entry["sets"], overdrive_on)
            if capped != clean_sets(entry["sets"]):
                registry.set_setting(key, entry["mode"], capped, save=False)
                changed.append(key)
    if changed:
        registry.save()
    std = load_standard(state_path)
    capped = clamp_sets(std["sets"], overdrive_on)
    if capped != std["sets"]:
        save_standard(std["mode"], capped, state_path=state_path)
        changed.append("standard")
    return changed


def is_dynamic_params(flat):
    """True for a flat parameter set that uses the leveler or the compressor."""
    p = full_params(flat)
    return p["leveler"] > 0.5 or p["lev_max_boost_db"] > 0.05 or p["comp_ratio"] > 1.01


def clean_sets(raw):
    """Validated {"fixed": {...}, "dynamic": {...}} with every key present."""
    raw = raw if isinstance(raw, dict) else {}
    fixed = neutral_fixed()
    fixed.update({k: v for k, v in normalize_params(raw.get(MODE_FIXED)).items() if k in FIXED_KEYS})
    dynamic = full_params(raw.get(MODE_DYNAMIC))
    return {MODE_FIXED: fixed, MODE_DYNAMIC: dynamic}


def migrate(flat, mode=None, sets=None, default_mode=None):
    """
    Mode and value sets for an entry saved by 0.3.x (only a flat parameter set).
    Existing dynamic values stay as the dynamic set; the fixed set starts neutral
    (user decision Q20b). `default_mode` forces the mode (Standard starts fixed, Q5);
    otherwise programs using leveler or compressor stay dynamic.
    """
    if mode in MODES and isinstance(sets, dict):
        return mode, clean_sets(sets)
    new_sets = {MODE_FIXED: neutral_fixed(), MODE_DYNAMIC: full_params(flat)}
    if default_mode in MODES:
        new_mode = default_mode
    else:
        new_mode = MODE_DYNAMIC if flat and is_dynamic_params(flat) else MODE_FIXED
    return new_mode, new_sets


def preset_mode(preset_id):
    return MODE_FIXED if preset_id in FIXED_PRESETS else MODE_DYNAMIC


def mode_values(mode, flat):
    """The part of a flat parameter set that belongs to a mode's value set."""
    keys = FIXED_KEYS if mode == MODE_FIXED else None
    p = full_params(flat) if mode == MODE_DYNAMIC else normalize_params(flat)
    if keys:
        return {k: float(p.get(k, 0.0)) for k in keys}
    return p


def apply_values(mode, sets, values):
    """Copy of sets with values merged into the given mode's set (fixed keeps only its keys)."""
    sets = clean_sets(sets)
    vals = normalize_params(values)
    if mode == MODE_FIXED:
        vals = {k: v for k, v in vals.items() if k in FIXED_KEYS}
    sets[mode].update(vals)
    return sets


# -- Standard (state.json) -------------------------------------------------------
def load_standard(state_path=None):
    """
    Standard's setting: {"mode", "sets", "params" (effective), "preset", "migrated"}.
    A 0.3.x state.json has no mode: Standard then starts fixed and neutral (Q5, Q20b),
    its old values are kept as the dynamic set.
    """
    data = paths.read_json(state_path or paths.STATE_FILE, default={}) or {}
    data = data if isinstance(data, dict) else {}
    flat = normalize_params(data.get("params")) if isinstance(data.get("params"), dict) else {}
    migrated = data.get("mode") not in MODES
    mode, sets = migrate(flat or None, data.get("mode"), data.get("sets"), default_mode=MODE_FIXED)
    return {"mode": mode, "sets": sets, "params": effective(mode, sets),
            "preset": data.get("preset", "clean_linear"), "migrated": migrated}


def save_standard(mode, sets, preset=None, state_path=None):
    current = load_standard(state_path)
    sets = clamp_sets(sets)
    state = {"preset": current["preset"] if preset is None else preset, "mode": mode,
             "sets": sets, "params": effective(mode, sets)}
    paths.write_json_atomic(state_path or paths.STATE_FILE, state)
    return dict(state, migrated=False)
