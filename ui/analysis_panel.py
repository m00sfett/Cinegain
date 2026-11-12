"""
Analysis view (0.5.0, second main feature): every playback node with its hidden
PipeWire volume, the volume mixers show, mute and WirePlumber's saved value.
Streams and Cinegain nodes can be set (0-100 %) or reset; sound card outputs
are read-only. Below: the hidden-volume events of the last 7 days from the
debug log, so lowerings can finally be traced.
"""
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import cinegain_log
import cinegain_volume as vol
from i18n import t

log = cinegain_log.get("analysis")

YELLOW = "#FFD21F"
INK = "#EDE6DC"
INK_MUTED = "#E2C89B"
IDLE = "#A2968A"
WAITING = "#E8C462"
LOWERED_BG = "#2A0E0E"

PANEL_STYLE = f"""
QWidget#analysis_root {{ background: transparent; }}
QLabel {{ background: transparent; color: {INK}; }}
QLabel#analysis_title {{ font-family: "Fira Sans Condensed", sans-serif; font-size: 18px; font-weight: 800;
    color: {YELLOW}; letter-spacing: 0.5px; }}
QLabel#analysis_hint {{ font-size: 12px; color: {INK_MUTED}; }}
QLabel#analysis_section {{ font-family: "Fira Sans Condensed", sans-serif; font-size: 14px; font-weight: 800;
    color: #E0B83A; padding-top: 6px; }}
QTableWidget {{ background-color: #14100D; alternate-background-color: #181210; color: {INK};
    gridline-color: #2A1E18; border: 1px solid #6E1516; border-radius: 6px;
    selection-background-color: #3D2C07; selection-color: {YELLOW}; font-size: 12px; }}
QHeaderView::section {{ background-color: #1F1712; color: #E0B83A; border: none;
    border-bottom: 1px solid #6E1516; padding: 5px 8px; font-weight: 700; font-size: 11px; }}
QTableCornerButton::section {{ background-color: #1F1712; border: none; }}
QPushButton#row_reset {{ background-color: #2A0E0E; color: {WAITING}; border: 1px solid #A86B4A;
    border-radius: 5px; padding: 2px 10px; font-weight: 700; font-size: 11px; }}
QPushButton#row_reset:hover {{ background-color: #8C1014; color: {YELLOW}; border-color: {YELLOW}; }}
QPushButton#row_reset:disabled {{ color: #5E5248; border-color: #3A2A22; background: transparent; }}
QPushButton#analysis_full_reset {{ background-color: #3D2C07; color: {YELLOW}; border: 1px solid {YELLOW};
    border-radius: 6px; padding: 6px 14px; font-weight: 800; }}
QPushButton#analysis_full_reset:hover {{ background-color: #8C1014; }}
QCheckBox {{ color: {INK}; font-weight: 600; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #A86B4A; border-radius: 4px;
    background: #14100D; }}
QCheckBox::indicator:checked {{ background: {YELLOW}; border-color: {YELLOW}; }}
QComboBox {{ background-color: #14100D; color: {INK}; border: 1px solid #6E1516; border-radius: 5px;
    padding: 3px 8px; min-width: 160px; }}
QComboBox QAbstractItemView {{ background-color: #1A130F; color: {INK}; selection-background-color: #8C1014; }}
QSlider::groove:horizontal {{ height: 4px; background: #3A2A22; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: #E0B83A; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {YELLOW}; width: 12px; margin: -5px 0; border-radius: 6px; }}
QPlainTextEdit {{ background-color: #0E0B09; color: {INK_MUTED}; border: 1px solid #6E1516; border-radius: 6px;
    font-family: "Fira Mono", monospace; font-size: 11px; selection-background-color: #3D2C07; }}
"""

# column ids; the pro view adds node id and node name
COL_NAME, COL_KIND, COL_HIDDEN, COL_NORMAL, COL_SAVED, COL_SET, COL_RESET, COL_ID, COL_NODE = range(9)


def _db_text(v):
    if v is None:
        return "—"
    if v <= 0.0:
        return "−∞ dB"
    db = vol.to_db(v)
    return "0.0 dB" if abs(db) < 0.05 else f"{db:+.1f} dB".replace("-", "−")


class AnalysisPanel(QWidget):
    """Table of hidden volumes plus the event timeline."""

    full_reset_requested = pyqtSignal()
    auto_reset_changed = pyqtSignal(bool)

    def __init__(self, scanner=vol.scan_analysis, setter=vol.set_hidden, history=vol.history, parent=None):
        super().__init__(parent)
        self.setObjectName("analysis_root")
        self.setStyleSheet(PANEL_STYLE)
        self.scanner = scanner
        self.setter = setter
        self.history_source = history
        self.pro = False
        self.rows = []
        # Raises fade in (#27): on the router's guard while it runs, else on this one.
        self._own_guard = vol.VolumeGuard(scanner=lambda: [])
        self.fade_timer = QTimer(self)
        self.fade_timer.setInterval(50)
        self.fade_timer.timeout.connect(self._step_own_fades)
        self._ids = []
        self._history_at = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 4, 2, 2)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("analysis_title")
        self.lbl_hint = QLabel()
        self.lbl_hint.setObjectName("analysis_hint")
        self.lbl_hint.setWordWrap(True)
        heading.addWidget(self.lbl_title)
        heading.addWidget(self.lbl_hint)
        top.addLayout(heading, 1)
        self.chk_auto = QCheckBox()
        self.chk_auto.toggled.connect(self.auto_reset_changed.emit)
        self.btn_full_reset = QPushButton()
        self.btn_full_reset.setObjectName("analysis_full_reset")
        self.btn_full_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_full_reset.clicked.connect(self.full_reset_requested.emit)
        top.addWidget(self.chk_auto, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.btn_full_reset, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(top)

        self.table = QTableWidget(0, 9)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.table.setShowGrid(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for c in (COL_KIND, COL_HIDDEN, COL_NORMAL, COL_SAVED, COL_RESET, COL_ID, COL_NODE):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_SET, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_SET, 150)
        root.addWidget(self.table, 3)

        tl_head = QHBoxLayout()
        self.lbl_timeline = QLabel()
        self.lbl_timeline.setObjectName("analysis_section")
        self.cmb_filter = QComboBox()
        self.cmb_filter.currentIndexChanged.connect(lambda _i: self.refresh_history(force=True))
        tl_head.addWidget(self.lbl_timeline, 1)
        tl_head.addWidget(self.cmb_filter, 0)
        root.addLayout(tl_head)
        self.timeline = QTableWidget(0, 4)
        self.timeline.verticalHeader().hide()
        self.timeline.setAlternatingRowColors(True)
        self.timeline.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.timeline.setShowGrid(False)
        th = self.timeline.horizontalHeader()
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        th.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.timeline, 2)

        self.timer = QTimer(self)
        self.timer.setInterval(500)  # 0.5 s while the view is open (Q21), the router keeps 1.5 s
        self.timer.timeout.connect(self.refresh)
        self.retranslate()

    # -- lifecycle --------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.refresh_history(force=True)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def set_pro(self, pro):
        self.pro = bool(pro)
        self.table.setColumnHidden(COL_ID, not self.pro)
        self.table.setColumnHidden(COL_NODE, not self.pro)

    def set_auto_reset(self, on):
        self.chk_auto.blockSignals(True)
        self.chk_auto.setChecked(bool(on))
        self.chk_auto.blockSignals(False)

    def retranslate(self):
        self.lbl_title.setText(t("analysis_title"))
        self.lbl_hint.setText(t("analysis_hint"))
        self.chk_auto.setText(t("analysis_auto"))
        self.chk_auto.setToolTip(t("analysis_auto_tip"))
        self.btn_full_reset.setText(t("btn_full_reset"))
        self.btn_full_reset.setToolTip(t("btn_full_reset_tip"))
        self.table.setHorizontalHeaderLabels([
            t("analysis_col_name"), t("analysis_col_kind"), t("analysis_col_hidden"), t("analysis_col_normal"),
            t("analysis_col_saved"), t("analysis_col_set"), "", "ID", "node.name"])
        self.lbl_timeline.setText(t("analysis_timeline"))
        self.timeline.setHorizontalHeaderLabels([t("analysis_col_time"), t("analysis_col_name"),
                                                 t("analysis_col_event"), t("analysis_col_by")])
        self._ids = []  # rebuild rows (kind texts, buttons)
        self.set_pro(self.pro)
        if self.isVisible():
            self.refresh()
            self.refresh_history(force=True)

    # -- table ------------------------------------------------------------------
    def refresh(self):
        try:
            rows = self.scanner()
        except Exception:
            log.exception("analysis scan failed")
            return
        self.rows = rows
        ids = [(r["id"], r["kind"]) for r in rows]
        if ids != self._ids:
            self._rebuild(rows)
            self._ids = ids
            self._refill_filter()
        else:
            for i, r in enumerate(rows):
                self._fill_row(i, r)
        if time.time() - self._history_at > 5.0:
            self.refresh_history()

    def _rebuild(self, rows):
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for c in (COL_NAME, COL_KIND, COL_HIDDEN, COL_NORMAL, COL_SAVED, COL_ID, COL_NODE):
                item = QTableWidgetItem()
                if c in (COL_HIDDEN, COL_NORMAL, COL_SAVED, COL_ID):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, c, item)
            if r["editable"]:
                s = QSlider(Qt.Orientation.Horizontal)
                s.setRange(0, 100)
                s.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                s.setCursor(Qt.CursorShape.PointingHandCursor)
                s.setToolTip(t("analysis_set_tip"))
                s.sliderReleased.connect(lambda sl=s, nid=r["id"]: self._set_from_slider(nid, sl))
                wrap = QWidget()
                lay = QHBoxLayout(wrap)
                lay.setContentsMargins(8, 0, 8, 0)
                lay.addWidget(s)
                self.table.setCellWidget(i, COL_SET, wrap)
                b = QPushButton(t("btn_reset_hidden"))
                b.setObjectName("row_reset")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _c=False, nid=r["id"]: self._reset(nid))
                self.table.setCellWidget(i, COL_RESET, b)
            else:
                item = QTableWidgetItem(t("analysis_readonly"))
                item.setForeground(QColor(IDLE))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, COL_SET, item)
                self.table.removeCellWidget(i, COL_RESET)
            self._fill_row(i, r)

    def _fill_row(self, i, r):
        lowered = r["hidden"] < vol.HIDDEN_EPS
        fg = QColor(WAITING if lowered else (IDLE if not r["editable"] else INK))
        bg = QColor(LOWERED_BG) if lowered else None
        texts = {
            COL_NAME: r["label"] + (f"  ({t('analysis_muted')})" if r["mute"] else ""),
            COL_KIND: t(f"analysis_kind_{r['kind']}"),
            COL_HIDDEN: _db_text(r["hidden"]) + (f"  ·  {r['hidden']:.6f}" if self.pro else ""),
            COL_NORMAL: f"{r['normal'] * 100:.0f} %",
            COL_SAVED: _db_text(r["stored"]),
            COL_ID: str(r["id"]),
            COL_NODE: r["node_name"],
        }
        for c, text in texts.items():
            item = self.table.item(i, c)
            if item is None:
                continue
            item.setText(text)
            item.setForeground(fg)
            if bg is not None:
                item.setBackground(bg)
            else:
                item.setData(Qt.ItemDataRole.BackgroundRole, None)
        wrap = self.table.cellWidget(i, COL_SET)
        if wrap is not None:
            s = wrap.findChild(QSlider)
            if s is not None and not s.isSliderDown():
                s.blockSignals(True)
                s.setValue(int(round(r["hidden"] * 100)))
                s.blockSignals(False)
        btn = self.table.cellWidget(i, COL_RESET)
        if btn is not None:
            btn.setEnabled(lowered)

    def _row(self, node_id):
        return next((r for r in self.rows if r["id"] == node_id), None)

    def _apply(self, node_id, fraction):
        """Lowering is instant, raising fades in at <= 10 dB/s (#27)."""
        r = self._row(node_id)
        if r is not None:
            guard = vol.live_guard() or self._own_guard
            self.setter(node_id, fraction, r["label"], "user", r["hidden"], guard)
            if guard is self._own_guard and guard.fades:
                self.fade_timer.start()
        self.refresh()
        self.refresh_history(force=True)

    def _step_own_fades(self):
        try:
            self._own_guard.step()
        except Exception:
            log.exception("hidden volume fade step failed")
            self._own_guard.fades.clear()
        if not self._own_guard.fades:
            self.fade_timer.stop()

    def _set_from_slider(self, node_id, slider):
        self._apply(node_id, slider.value() / 100.0)

    def _reset(self, node_id):
        self._apply(node_id, 1.0)

    # -- timeline ---------------------------------------------------------------
    def _refill_filter(self):
        current = self.cmb_filter.currentData()
        self.cmb_filter.blockSignals(True)
        self.cmb_filter.clear()
        self.cmb_filter.addItem(t("analysis_all"), None)
        seen = set()
        for r in self.rows:
            if r["kind"] == vol.KIND_STREAM and r["key"] not in seen:
                seen.add(r["key"])
                self.cmb_filter.addItem(r["label"], r["key"])
        idx = self.cmb_filter.findData(current)
        self.cmb_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.cmb_filter.blockSignals(False)

    def refresh_history(self, force=False):
        if not force and time.time() - self._history_at < 5.0:
            return
        self._history_at = time.time()
        try:
            events = self.history_source(key=self.cmb_filter.currentData())
        except Exception:
            log.exception("reading the hidden-volume history failed")
            events = []
        self.timeline.setRowCount(max(1, len(events)))
        if not events:
            item = QTableWidgetItem(t("analysis_no_events"))
            item.setForeground(QColor(IDLE))
            self.timeline.setItem(0, 2, item)
            for c in (0, 1, 3):
                self.timeline.setItem(0, c, QTableWidgetItem(""))
            return
        labels = {r["key"]: r["label"] for r in self.rows}
        for i, ev in enumerate(events):
            when = time.strftime("%d.%m. %H:%M:%S", time.localtime(ev["time"]))
            name = labels.get(ev["key"], ev["key"]) or "—"
            was = _fmt_db(ev["was_db"]) if ev["was_db"] is not None else "?"
            if ev["kind"] == "lowered":
                text = t("analysis_ev_lowered", db=_fmt_db(ev["db"]), was=was)
            elif ev["kind"] == "raised":
                text = t("analysis_ev_raised", db=_fmt_db(ev["db"]), was=was)
            elif ev["kind"] == "mixer":
                text = t("analysis_ev_mixer", db=_fmt_db(ev["db"]), was=was)
            elif ev["kind"] in ("muted", "unmuted"):
                text = t(f"analysis_ev_{ev['kind']}")
            elif ev["kind"] == "link_failed":
                text = t("analysis_ev_link_failed")
            elif ev["kind"] == "auto":
                text = t("analysis_ev_auto", db=_fmt_db(ev["was_db"]))
            elif ev["kind"] == "set":
                text = t("analysis_ev_set", db=_fmt_db(ev["db"]))
            else:
                text = t("analysis_ev_full_reset")
            who = {"auto": t("analysis_by_auto"), "user": t("analysis_by_user"), "cli": "CLI",
                   "external": t("analysis_by_external"), "wireplumber": "WirePlumber",
                   "": t("analysis_by_unknown")}.get(ev["who"], ev["who"])
            for c, value in enumerate((when, name, text, who)):
                item = QTableWidgetItem(value)
                item.setForeground(QColor(WAITING if ev["kind"] == "lowered" else INK))
                self.timeline.setItem(i, c, item)


def _fmt_db(v):
    if v is None:
        return "?"
    return f"{v:+.1f} dB".replace("-", "−")


class LogPanel(QWidget):
    """Pro view: the tail of the debug log inside the app."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(PANEL_STYLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 4, 2, 2)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.lbl = QLabel()
        self.lbl.setObjectName("analysis_section")
        self.btn = QPushButton()
        self.btn.setObjectName("row_reset")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.refresh)
        head.addWidget(self.lbl, 1)
        head.addWidget(self.btn, 0)
        lay.addLayout(head)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(self.text, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.retranslate()

    def retranslate(self):
        self.lbl.setText(t("log_title", path=cinegain_log.paths.LOG_FILE))
        self.btn.setText(t("log_refresh"))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self):
        try:
            lines = cinegain_log.tail(lines=400)
        except Exception:
            lines = []
        text = "".join(lines) if lines and not isinstance(lines, str) else (lines or "")
        if text == getattr(self, "_shown", None):
            return  # nothing new: keep the scroll position and skip the re-layout (#39)
        self._shown = text
        bar = self.text.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        self.text.setPlainText(text)
        if at_end:
            bar.setValue(bar.maximum())
