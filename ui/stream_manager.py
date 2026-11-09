"""
Cinegain Stream Manager.
Tracks active audio streams from PipeWire / PulseAudio, remembers applications
that played audio within the last 5 minutes (decay cache to prevent UI flickering on pause),
and handles selective rerouting to the Cinegain DSP sink and reliable restoration to the hardware sink.
"""
import os
import time
import subprocess
import re


class StreamManager:
    def __init__(self, history_seconds=300):
        self.history_seconds = history_seconds
        # Dict of app_name -> { 'last_seen': float, 'active': bool, 'binary': str, 'sink_inputs': set(str), 'on_cinegain': bool, 'volume_pct': int }
        self.apps = {}
        # Set of app names currently routed to Cinegain by user preference
        self.enabled_apps = set()
        # Default media apps to auto-enable on first discovery
        self.default_targets = {"Google Chrome", "Chromium", "Firefox", "Brave", "vlc", "mpv", "Spotify"}
        self.last_hw_sink = None

    def get_hardware_sink(self):
        """Finds the primary non-cinegain hardware sink name and numeric ID."""
        try:
            default_sink = subprocess.check_output(
                ["pactl", "get-default-sink"], stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            default_sink = ""

        try:
            out = subprocess.check_output(
                ["pactl", "list", "sinks", "short"], stderr=subprocess.DEVNULL, text=True
            )
            sinks = []
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    sid, sname = parts[0].strip(), parts[1].strip()
                    if "cinegain" not in sname.lower() and "kinoton" not in sname.lower():
                        sinks.append((sid, sname))
            if default_sink and "cinegain" not in default_sink.lower():
                for sid, sname in sinks:
                    if sname == default_sink:
                        self.last_hw_sink = sname
                        return sname
            if sinks:
                self.last_hw_sink = sinks[0][1]
                return sinks[0][1]
        except Exception:
            pass
        return self.last_hw_sink or "@DEFAULT_SINK@"

    def get_cinegain_sink_ids(self):
        """Returns set of numeric sink IDs and names belonging to cinegain_sink."""
        ids = {"cinegain_sink"}
        try:
            out = subprocess.check_output(
                ["pactl", "list", "sinks", "short"], stderr=subprocess.DEVNULL, text=True
            )
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and "cinegain" in parts[1].lower():
                    ids.add(parts[0].strip())
                    ids.add(parts[1].strip())
        except Exception:
            pass
        return ids

    def scan(self):
        """Scan current sink inputs from pactl and update the application registry."""
        now = time.time()
        cg_sinks = self.get_cinegain_sink_ids()
        try:
            out = subprocess.check_output(
                ["pactl", "list", "sink-inputs"], stderr=subprocess.DEVNULL, text=True
            )
        except Exception:
            out = ""

        for app_info in self.apps.values():
            app_info["active"] = False
            app_info["sink_inputs"].clear()
            app_info["on_cinegain"] = False

        blocks = out.split("Sink Input #")
        for b in blocks[1:]:
            lines = b.splitlines()
            if not lines:
                continue
            sid = lines[0].strip()

            sink_id = ""
            app_name = None
            binary = ""
            node_name = ""
            vol_pct = 100

            for l in lines:
                s = l.strip()
                if s.startswith("Sink:"):
                    sink_id = s.split(":", 1)[1].strip()
                elif s.startswith("Volume:") and "%" in s:
                    m = re.search(r"(\d+)%", s)
                    if m:
                        vol_pct = int(m.group(1))
                elif "application.name =" in s:
                    m = re.search(r'application\.name\s*=\s*"(.*?)"', s)
                    if m:
                        app_name = m.group(1)
                elif "application.process.binary =" in s:
                    m = re.search(r'application\.process\.binary\s*=\s*"(.*?)"', s)
                    if m:
                        binary = m.group(1)
                elif "node.name =" in s:
                    m = re.search(r'node\.name\s*=\s*"(.*?)"', s)
                    if m:
                        node_name = m.group(1)

            if not app_name:
                app_name = node_name or binary or f"Stream #{sid}"

            # Filter out Cinegain's own output stream
            if "cinegain" in app_name.lower() or "cinegain" in node_name.lower():
                continue

            on_cg = sink_id in cg_sinks

            if app_name not in self.apps:
                is_media = any(
                    t.lower() in app_name.lower() or t.lower() in binary.lower()
                    for t in self.default_targets
                )
                if is_media:
                    self.enabled_apps.add(app_name)

                self.apps[app_name] = {
                    "last_seen": now,
                    "active": True,
                    "binary": binary,
                    "sink_inputs": {sid},
                    "on_cinegain": on_cg,
                    "volume_pct": vol_pct,
                }
            else:
                self.apps[app_name]["last_seen"] = now
                self.apps[app_name]["active"] = True
                self.apps[app_name]["sink_inputs"].add(sid)
                self.apps[app_name]["on_cinegain"] = self.apps[app_name]["on_cinegain"] or on_cg
                self.apps[app_name]["volume_pct"] = vol_pct

        expired = [
            name
            for name, info in self.apps.items()
            if not info["active"] and (now - info["last_seen"] > self.history_seconds)
        ]
        for name in expired:
            del self.apps[name]

    def get_tracked_apps(self):
        """Returns list of dicts with app state for UI and Tray."""
        now = time.time()
        result = []
        for name, info in sorted(self.apps.items(), key=lambda x: (not x[1]["active"], x[0])):
            is_enabled = name in self.enabled_apps
            is_active = info["active"]
            idle_sec = 0 if is_active else int(now - info["last_seen"])
            result.append(
                {
                    "name": name,
                    "enabled": is_enabled,
                    "active": is_active,
                    "idle_seconds": idle_sec,
                    "on_cinegain": info.get("on_cinegain", False),
                    "volume_pct": info.get("volume_pct", 100),
                }
            )
        return result

    def set_app_enabled(self, app_name, enabled, service_active=True):
        if enabled:
            self.enabled_apps.add(app_name)
        else:
            self.enabled_apps.discard(app_name)
        if service_active:
            self.enforce_routing()
        else:
            self.release_all()

    def set_app_volume_100(self, app_name):
        """Restores an application's stream volume to 100% if it was accidentally lowered."""
        info = self.apps.get(app_name)
        if not info:
            return
        for sid in list(info["sink_inputs"]):
            subprocess.run(
                ["pactl", "set-sink-input-volume", str(sid), "100%"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["pactl", "set-sink-input-mute", str(sid), "0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    def enforce_routing(self):
        """Moves enabled apps to cinegain_sink and disabled apps back to the hardware sink."""
        hw_sink = self.get_hardware_sink()
        cg_sinks = self.get_cinegain_sink_ids()
        if len(cg_sinks) <= 1:
            # cinegain_sink is not actually present in pactl yet
            return

        for app_name, info in self.apps.items():
            if not info["active"]:
                continue
            should_route = app_name in self.enabled_apps
            target_sink = "cinegain_sink" if should_route else hw_sink
            for sid in list(info["sink_inputs"]):
                try:
                    subprocess.run(
                        ["pactl", "move-sink-input", str(sid), target_sink],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass

    def release_all(self):
        """Moves all streams (including any currently on cinegain_sink) back to the hardware sink."""
        hw_sink = self.get_hardware_sink()
        cg_sinks = self.get_cinegain_sink_ids()
        try:
            out = subprocess.check_output(
                ["pactl", "list", "sink-inputs", "short"], stderr=subprocess.DEVNULL, text=True
            )
            for line in out.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    sid, sink_id = parts[0].strip(), parts[1].strip()
                    if sink_id in cg_sinks:
                        subprocess.run(
                            ["pactl", "move-sink-input", sid, hw_sink],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
        except Exception:
            pass
        self._clean_wireplumber_target()

    def _clean_wireplumber_target(self):
        """Removes sticky target='cinegain_sink' from WirePlumber state file so streams don't get trapped."""
        wp_file = os.path.expanduser("~/.local/state/wireplumber/stream-properties")
        if not os.path.exists(wp_file):
            return
        try:
            with open(wp_file, "r", encoding="utf-8") as f:
                content = f.read()
            if "cinegain_sink" in content or "kinoton" in content:
                cleaned = re.sub(r',\s*"target"\s*:\s*"(?:cinegain|kinoton)[^"]*"', "", content)
                cleaned = re.sub(r'"target"\s*:\s*"(?:cinegain|kinoton)[^"]*"\s*,\s*', "", cleaned)
                if cleaned != content:
                    with open(wp_file, "w", encoding="utf-8") as f:
                        f.write(cleaned)
        except Exception:
            pass
