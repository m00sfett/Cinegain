"""
One `pw-dump` per GUI tick (#38).

The heartbeat needs the same dump for the status (node id and parameters of the
standard filter), the hidden-volume guard (stream and sink volumes) and the default
sink. Each of them used to start its own `pw-dump` (or a whole `cinegain status`
process). Inside tick_scope() the first snapshot() fetches the dump, the following
ones in the same scope and thread reuse it; outside a scope every call fetches a
fresh one, so one-shot CLI commands and everything that waits for a node
(wait_for_node) never see stale data.
"""
import contextlib
import json
import subprocess
import threading

_scope = None   # [thread id, dump or None] while a tick scope is open


def fetch():
    """A fresh parsed dump. Raises CalledProcessError, ValueError or OSError."""
    data = json.loads(subprocess.check_output(["pw-dump"], stderr=subprocess.DEVNULL))
    if not isinstance(data, list) or not all(isinstance(obj, dict) for obj in data):
        raise ValueError("Invalid pw-dump object list")
    return data


def snapshot():
    """The dump of the open tick scope (fetched on first use), else a fresh one."""
    scope = _scope
    if scope is None or scope[0] != threading.get_ident():
        return fetch()
    if scope[1] is None:
        scope[1] = fetch()   # a failure is not remembered: the next caller tries again
    return scope[1]


def scoped_snapshot():
    """The shared dump inside a tick scope of this thread, else None (never starts a pw-dump on its own)."""
    scope = _scope
    if scope is None or scope[0] != threading.get_ident():
        return None
    try:
        return snapshot()
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


@contextlib.contextmanager
def tick_scope():
    """Everything inside shares one dump. Scopes do not nest: an inner one joins the outer."""
    global _scope
    if _scope is not None:
        yield
        return
    _scope = [threading.get_ident(), None]
    try:
        yield
    finally:
        _scope = None


def node_id_of(dump, node_name):
    """PipeWire object id of the first node with this node.name, or None."""
    for obj in dump:
        if obj.get("type") == "PipeWire:Interface:Node":
            if (obj.get("info") or {}).get("props", {}).get("node.name") == node_name:
                return obj.get("id")
    return None


def cinegain_params(dump, node_id):
    """The cinegain:* parameters of a filter node as {name: value}, or {} if it shows none."""
    for obj in dump:
        if obj.get("id") != node_id:
            continue
        for p in ((obj.get("info") or {}).get("params") or {}).get("Props") or []:
            if isinstance(p, dict) and isinstance(p.get("params"), list):
                it = iter(p["params"])
                found = {}
                for k, v in zip(it, it):
                    if isinstance(k, str) and k.startswith("cinegain:"):
                        if isinstance(v, bool):
                            v = 1.0 if v else 0.0
                        if isinstance(v, (int, float)):
                            found[k[len("cinegain:"):]] = round(float(v), 3)
                if found:
                    return found
    return {}


def default_sink(dump):
    """Name of the default audio sink from the "default" metadata ('' if it has none)."""
    for obj in dump:
        if obj.get("type") != "PipeWire:Interface:Metadata":
            continue
        if (obj.get("props") or {}).get("metadata.name") != "default":
            continue
        for m in obj.get("metadata") or []:
            if isinstance(m, dict) and m.get("key") == "default.audio.sink":
                value = m.get("value")
                if isinstance(value, dict) and isinstance(value.get("name"), str):
                    return value["name"]
    return ""
