"""
Event source for the GUI heartbeat (#38).

Instead of polling PipeWire every 1.5-3 s, the GUI follows `pactl subscribe`
(one long-lived, mostly sleeping process) and file changes of the state Cinegain
shares with its CLI. A relevant event triggers a heartbeat soon; a slow safety
tick still runs for anything no event covers. If `pactl subscribe` is missing or
dies, the GUI falls back to the polling intervals until it is back.

The parsing and the throttle are plain Python so they can be tested without Qt.
"""
import re

# `pactl subscribe` prints e.g. "Event 'change' on sink-input #123"
_EVENT_RE = re.compile(r"^Event '(new|change|remove)' on ([a-z-]+)(?: #(\d+))?")
# Facilities that can change what the heartbeat shows or routes. Clients, modules,
# sources and source-outputs (microphones) are noise for Cinegain.
RELEVANT_FACILITIES = frozenset({"sink-input", "sink", "server", "card"})

DELAY_MS = 150          # let a burst of events (a move = several lines) settle
MIN_SPACING_MS = 500    # at most two event-driven ticks per second (fades step at 10 Hz)
SAFETY_TICK_MS = 15000  # with events: slow tick for what no event reports
RESTART_BACKOFF_MS = 5000


def parse_event(line):
    """(kind, facility, index) of one `pactl subscribe` line, or None."""
    m = _EVENT_RE.match(line.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def is_relevant(line):
    ev = parse_event(line)
    return ev is not None and ev[1] in RELEVANT_FACILITIES


class Throttle:
    """
    Decides when an event-driven tick may run: DELAY_MS after the first event of a
    burst, but never closer than MIN_SPACING_MS to the previous tick. Times in ms.
    """

    def __init__(self, delay=DELAY_MS, spacing=MIN_SPACING_MS):
        self.delay = delay
        self.spacing = spacing
        self.last_tick = None
        self.due = None

    def event(self, now):
        """Registers an event; returns the time the tick is due (unchanged if already pending)."""
        if self.due is None:
            due = now + self.delay
            if self.last_tick is not None:
                due = max(due, self.last_tick + self.spacing)
            self.due = due
        return self.due

    def ticked(self, now):
        """Any heartbeat (event-driven or not) ran at `now`."""
        self.last_tick = now
        self.due = None


def make_qt_source(parent, on_change, on_state=None):
    """
    Builds the Qt side: a QProcess running `pactl subscribe`, a single-shot timer for
    the throttle, and a QFileSystemWatcher for the given paths. Imported lazily so
    the module stays importable without PyQt6 (tests).
    """
    return _QtEventSource(parent, on_change, on_state)


class _QtEventSource:
    def __init__(self, parent, on_change, on_state):
        from PyQt6.QtCore import QElapsedTimer, QFileSystemWatcher, QProcess, QTimer

        self._QProcess = QProcess
        self._on_change = on_change
        self._on_state = on_state or (lambda running: None)
        self._clock = QElapsedTimer()
        self._clock.start()
        self.throttle = Throttle()
        self.running = False
        self._stopping = False
        self._buf = b""

        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)

        self._restart = QTimer(parent)
        self._restart.setSingleShot(True)
        self._restart.setInterval(RESTART_BACKOFF_MS)
        self._restart.timeout.connect(self.start)

        self._proc = QProcess(parent)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._proc.readyReadStandardOutput.connect(self._read)
        self._proc.started.connect(lambda: self._set_running(True))
        self._proc.finished.connect(self._finished)
        self._proc.errorOccurred.connect(self._error)

        self._watcher = QFileSystemWatcher(parent)
        self._watcher.fileChanged.connect(self._file_changed)
        self._watcher.directoryChanged.connect(lambda _p: self.notify())
        self._files = set()

    # -- public ---------------------------------------------------------------
    def start(self):
        if self._proc.state() != self._QProcess.ProcessState.NotRunning:
            return
        self._stopping = False
        self._proc.start("pactl", ["subscribe"])

    def stop(self):
        self._stopping = True
        self._restart.stop()
        self._timer.stop()
        if self._proc.state() != self._QProcess.ProcessState.NotRunning:
            self._proc.kill()
            self._proc.waitForFinished(1000)

    def watch_file(self, path):
        """Watches a file that is replaced atomically (re-armed after every change)."""
        self._files.add(path)
        self._rearm(path)

    def watch_dir(self, path):
        import os
        if os.path.isdir(path) and path not in self._watcher.directories():
            self._watcher.addPath(path)

    def ticked(self):
        """Tell the throttle that a heartbeat just ran (any source)."""
        self.throttle.ticked(self._clock.elapsed())
        self._timer.stop()

    def notify(self):
        """Something changed: schedule a throttled tick."""
        now = self._clock.elapsed()
        due = self.throttle.event(now)
        if not self._timer.isActive():
            self._timer.start(max(0, int(due - now)))

    # -- internals ------------------------------------------------------------
    def _fire(self):
        self._on_change()

    def _read(self):
        self._buf += bytes(self._proc.readAllStandardOutput())
        *lines, self._buf = self._buf.split(b"\n")
        if any(is_relevant(raw.decode("utf-8", "replace")) for raw in lines):
            self.notify()

    def _rearm(self, path):
        import os
        if path not in self._watcher.files() and os.path.exists(path):
            self._watcher.addPath(path)

    def _file_changed(self, path):
        self._rearm(path)  # an atomic replace drops the watch
        self.notify()

    def _set_running(self, running):
        if self.running != running:
            self.running = running
            self._on_state(running)

    def _finished(self, *_args):
        self._set_running(False)
        if not self._stopping:
            self._restart.start()

    def _error(self, _err):
        if self._proc.state() == self._QProcess.ProcessState.NotRunning:
            self._finished()
