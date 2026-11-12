"""
The GUI's hot CLI calls without a new process (#38).

`cinegain status --json` (heartbeat) and `cinegain batch` (every Standard slider
change) used to start a whole Python process each, 100 to 150 ms apiece. Here the
CLI script is loaded once as a module and its own cmd_status / cmd_batch run inside
the GUI process, so the behaviour and the output stay exactly the CLI's. Anything
else, or any failure to load, returns None and the caller starts the CLI as before.

The node id of the Standard filter is taken from the last status and handed to the
batch command, so a slider change needs no pw-dump. A stale id (filter restarted
since) makes the push fail; the command is then repeated once with a fresh lookup.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import threading
from importlib.machinery import SourceFileLoader

import cinegain_log
import cinegain_modes as modes

log = cinegain_log.get("inproc")

_lock = threading.Lock()
_cli = {}            # script path -> loaded module, or None if it could not be loaded
node_hint = None     # Standard's node id from the last status


def load(bin_path):
    """The CLI script as a module (cached), or None."""
    if bin_path in _cli:
        return _cli[bin_path]
    cli = None
    try:
        if bin_path and bin_path.startswith("/"):
            loader = SourceFileLoader("cinegain_cli_inproc", bin_path)
            spec = importlib.util.spec_from_loader(loader.name, loader)
            cli = importlib.util.module_from_spec(spec)
            loader.exec_module(cli)
    except Exception:
        log.exception("could not load the CLI %s: commands run as processes", bin_path)
        cli = None
    _cli[bin_path] = cli
    return cli


def _call(func, namespace):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = func(namespace)
        except SystemExit as e:  # a CLI helper that exits must not end the GUI
            code = e.code if isinstance(e.code, int) else 1
    return out.getvalue().strip(), err.getvalue().strip(), int(code or 0)


def _parse_batch(args):
    """('batch', json, [--preset-id X] [--mode M]) -> Namespace, or None if it is anything else."""
    if len(args) < 2 or not isinstance(args[1], str):
        return None
    ns = argparse.Namespace(json_params=args[1], preset_id="custom", mode=None)
    rest = list(args[2:])
    while rest:
        flag = rest.pop(0)
        if flag not in ("--preset-id", "--mode") or not rest:
            return None
        value = rest.pop(0)
        if flag == "--mode":
            if value not in modes.MODES:
                return None
            ns.mode = value
        else:
            ns.preset_id = value
    return ns


def run(bin_path, args):
    """
    (stdout, stderr, returncode) of `cinegain <args>` run in this process, or None when the
    caller has to start the CLI itself (other command, other thread, CLI not loadable).
    """
    global node_hint
    if args[:1] not in (("status",), ("batch",)) or threading.current_thread() is not threading.main_thread():
        return None
    if args[:1] == ("status",) and tuple(args) != ("status", "--json"):
        return None
    namespace = _parse_batch(args) if args[0] == "batch" else argparse.Namespace(json=True)
    if namespace is None:
        return None
    cli = load(bin_path)
    if cli is None:
        return None
    with _lock:
        try:
            if args[0] == "status":
                out, err, code = _call(cli.cmd_status, namespace)
                try:
                    st = json.loads(out)
                    node_hint = st.get("node_id") if isinstance(st, dict) and st.get("active") else None
                except ValueError:
                    node_hint = None
                return out, err, code
            real = cli.get_cinegain_node_id
            if node_hint:
                hint = node_hint
                cli.get_cinegain_node_id = lambda: hint
                try:
                    out, err, code = _call(cli.cmd_batch, namespace)
                finally:
                    cli.get_cinegain_node_id = real
                if code == 0:
                    return out, err, code
                node_hint = None   # stale or failed: ask PipeWire once and look again
            return _call(cli.cmd_batch, namespace)
        except Exception:
            log.exception("cinegain %s failed in process: falling back to the CLI", " ".join(args[:1]))
            return None
