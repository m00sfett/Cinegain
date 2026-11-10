"""
Program sidebar of the Cinegain window.

Lists "Standard" (pinned) and every program Cinegain has ever heard. Clicking a
row selects what the presets and sliders on the right edit. Each program row has
its own "through Cinegain" switch. Programs silent for more than a week fold
away under a "silent for a while" divider. Rows are updated in place on every
heartbeat, never rebuilt, so hover, clicks and keyboard selection stay stable.
"""
import time

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from i18n import t

STANDARD = None  # selection value for the shared Standard settings
LONG_SILENT_S = 7 * 86400

YELLOW = "#FFD21F"
INK = "#EDE6DC"
INK_MUTED = "#E2C89B"
IDLE = "#A2968A"
RUNNING = "#6FBF8B"
WAITING = "#E8C462"
BORDER = "#6E1516"

# Material Symbols paths (Apache 2.0), drawn in the palette instead of emoji.
ICON_VOLUME_DOWN = "M18.5 12c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z"
ICON_VOLUME_OFF = (
    "M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 "
    "2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 "
    "3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 "
    "3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"
)
ICON_TUNE = (
    "M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 "
    "4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"
)
ICON_CHEVRON_RIGHT = "M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z"
ICON_EXPAND_MORE = "M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z"

SIDEBAR_STYLE = f"""
QFrame#program_row {{
    background-color: #14100D;
    border: 1px solid #2A1C16;
    border-radius: 7px;
}}
QFrame#program_row:hover {{ border-color: {BORDER}; background-color: #1A130F; }}
QFrame#program_row[selected="true"] {{
    border: 2px solid {YELLOW};
    background-color: #271C16;
}}
QLabel#program_name {{ font-size: 13px; font-weight: 700; color: {INK}; background: transparent; }}
QLabel#program_name[selected="true"] {{ color: {YELLOW}; }}
QLabel#program_name[idle="true"] {{ color: #B8AC9E; font-weight: 600; }}
QLabel#program_sub {{ font-size: 11px; color: {IDLE}; background: transparent; }}
QLabel#program_sub[own="true"] {{ color: {INK_MUTED}; }}
QPushButton#volume_badge {{
    background-color: #2A1C16; color: {WAITING}; border: 1px solid #A86B4A;
    border-radius: 5px; padding: 1px 6px 1px 3px; font-size: 11px; font-weight: 700;
}}
QPushButton#volume_badge:hover {{ background-color: #8C1014; color: {YELLOW}; border-color: {YELLOW}; }}
QPushButton#silent_toggle {{
    background: transparent; border: none; color: {IDLE}; font-size: 11px; font-weight: 700;
    text-align: left; padding: 6px 2px 2px 2px;
}}
QPushButton#silent_toggle:hover {{ color: {YELLOW}; }}
QWidget#program_list {{ background: transparent; }}
"""


_ICON_CACHE = {}


def svg_icon(path, color, size=16):
    cache_key = (path, color, size)
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]
    data = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">'
            f'<path fill="{color}" d="{path}"/></svg>')
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    _ICON_CACHE[cache_key] = QIcon(pix)
    return _ICON_CACHE[cache_key]


def format_ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def _repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _set_prop(widget, name, value):
    value = "true" if value else "false"
    if widget.property(name) != value:
        widget.setProperty(name, value)
        _repolish(widget)
        if isinstance(widget, ElidedLabel):
            widget._apply()  # the font may have changed with the new style


class ElidedLabel(QLabel):
    """Single-line label that shortens itself with "…" instead of widening its row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(10)

    def setText(self, text):
        self._full = text
        self.setToolTip(text if self.fontMetrics().horizontalAdvance(text) > self.width() else "")
        self._apply()

    def text(self):
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply()

    def _apply(self):
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width()))
        super().setText(elided)
        self.setToolTip(self._full if elided != self._full else "")


class StatusDot(QWidget):
    """Playing (filled), paused (two bars), silent (ring) or the Standard tune icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.state = "silent"
        self.color = IDLE
        self._tune = None

    def set_state(self, state, color):
        if (state, color) != (self.state, self.color):
            self.state, self.color = state, color
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        if self.state == "standard":
            if self._tune is None:
                self._tune = svg_icon(ICON_TUNE, YELLOW, 18).pixmap(18, 18)
            p.drawPixmap(0, 0, self._tune)
        elif self.state == "playing":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QRectF(4, 4, 10, 10))
        elif self.state == "paused":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(4.5, 4, 3.2, 10), 1, 1)
            p.drawRoundedRect(QRectF(10.3, 4, 3.2, 10), 1, 1)
        else:
            p.setPen(QPen(c, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(5, 5, 8, 8))
        p.end()


class ToggleSwitch(QAbstractButton):
    """Compact on/off switch in the CI palette (yellow = through Cinegain)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(QSize(38, 20))

    def sizeHint(self):
        return QSize(38, 20)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        hover = self.underMouse()
        track = QRectF(1, 2, 36, 16)
        p.setPen(QPen(QColor(YELLOW if on else ("#A86B4A" if hover else BORDER)), 1.2))
        p.setBrush(QColor("#3D2C07" if on else "#080604"))
        p.drawRoundedRect(track, 8, 8)
        knob_x = 20 if on else 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(YELLOW if on else "#7A6A5C"))
        p.drawEllipse(QRectF(knob_x, 4, 12, 12))
        p.end()


class ProgramRow(QFrame):
    clicked = pyqtSignal(object)  # key (None = Standard)
    switched = pyqtSignal(str, bool)
    menu_requested = pyqtSignal(str, object)
    volume_clicked = pyqtSignal(str)

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("program_row")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        if key is not STANDARD:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(
                lambda pos: self.menu_requested.emit(self.key, self.mapToGlobal(pos))
            )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 6, 9, 6)
        lay.setSpacing(8)
        self.dot = StatusDot()
        text = QVBoxLayout()
        text.setSpacing(1)
        self.name = ElidedLabel()
        self.name.setObjectName("program_name")
        self.sub = ElidedLabel()
        self.sub.setObjectName("program_sub")
        text.addWidget(self.name)
        text.addWidget(self.sub)
        lay.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(text, 1)

        self.badge = None
        self.switch = None
        if key is not STANDARD:
            self.badge = QPushButton()
            self.badge.setObjectName("volume_badge")
            self.badge.setCursor(Qt.CursorShape.PointingHandCursor)
            self.badge.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.badge.setIconSize(QSize(14, 14))
            self.badge.clicked.connect(lambda: self.volume_clicked.emit(self.key))
            self.badge.hide()
            self.switch = ToggleSwitch()
            self.switch.setToolTip(t("prog_switch_tip"))
            self.switch.toggled.connect(lambda on: self.switched.emit(self.key, on))
            lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)

    def set_selected(self, selected, label):
        _set_prop(self, "selected", selected)
        _set_prop(self.name, "selected", selected)
        self.name.setText(f"▶ {label}" if selected else label)

    def update_standard(self, selected):
        self.dot.set_state("standard", YELLOW)
        self.set_selected(selected, t("prog_standard"))
        self.sub.setText(t("prog_standard_sub"))

    def update_program(self, view, selected, is_active, now):
        if view.playing:
            self.dot.set_state("playing", RUNNING if (view.routed and is_active) else INK_MUTED)
        elif view.streams:
            self.dot.set_state("paused", WAITING)
        else:
            self.dot.set_state("silent", "#5E5248")
        self.set_selected(selected, view.label)
        tip = view.name if view.binary.lower() == view.name.lower() else f"{view.name} · {view.binary}"
        self.setToolTip(tip)

        # The sub-line always names the setting; the switch shows the routing. Only a
        # program without own values that is switched off reads "direct".
        if view.own:
            setting = t("prog_own_db", db=f"{view.params.get('input_db', 0.0):+.1f} dB")
        elif not view.enabled:
            setting = t("prog_off")
        else:
            setting = t("prog_follows")
        if not view.streams:
            setting = f"{setting} · {t('prog_ago', time_str=format_ago(now - view.last_seen))}"
        self.sub.setText(setting)
        _set_prop(self.sub, "own", view.own and view.enabled)
        _set_prop(self.name, "idle", not view.streams and not selected)

        if self.switch.isChecked() != view.enabled:
            self.switch.blockSignals(True)
            self.switch.setChecked(view.enabled)
            self.switch.blockSignals(False)
            self.switch.update()

        if view.streams and (view.muted or view.volume_pct < 100):
            if view.muted:
                self.badge.setIcon(svg_icon(ICON_VOLUME_OFF, WAITING, 14))
                self.badge.setText("")
            else:
                self.badge.setIcon(svg_icon(ICON_VOLUME_DOWN, WAITING, 14))
                self.badge.setText(f"{view.volume_pct} %")
            self.badge.setToolTip(t("volume_low_tip"))
            self.badge.show()
        else:
            self.badge.hide()


class ProgramSidebar(QGroupBox):
    """Standard + all programs; emits the selection and switch changes."""

    selection_changed = pyqtSignal(object)  # key or None
    changed = pyqtSignal()

    def __init__(self, router, parent=None):
        super().__init__(t("prog_group"), parent)
        self.router = router
        self.selected = STANDARD
        self.rows = {}
        self.order = []  # visible keys in navigation order (excluding Standard)
        self.views = {}
        self.show_silent = False
        self.is_active = False
        self.setStyleSheet(SIDEBAR_STYLE)
        self.setFixedWidth(300)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("program_list")
        self.list = QVBoxLayout(content)
        self.list.setContentsMargins(0, 0, 4, 0)
        self.list.setSpacing(5)

        self.standard_row = ProgramRow(STANDARD, content)
        self.standard_row.clicked.connect(self.select)
        self.list.addWidget(self.standard_row)

        self.empty = QLabel(t("apps_empty"))
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {IDLE}; font-style: italic; padding: 8px 2px; background: transparent;")
        self.list.addWidget(self.empty)

        self.silent_toggle = QPushButton()
        self.silent_toggle.setObjectName("silent_toggle")
        self.silent_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.silent_toggle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.silent_toggle.clicked.connect(self._toggle_silent)
        self.list.addWidget(self.silent_toggle)
        self.list.addStretch()
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll)
        self.standard_row.update_standard(True)

    # -- updates ------------------------------------------------------------
    def update_views(self, views, is_active):
        now = time.time()
        self.is_active = is_active
        self.views = {v.key: v for v in views}
        recent = [v for v in views if v.streams or now - v.last_seen <= LONG_SILENT_S]
        silent = [v for v in views if v not in recent]
        # A selected program never hides under the fold.
        if self.selected in {v.key for v in silent} and not self.show_silent:
            self.show_silent = True

        for key in list(self.rows):
            if key not in self.views:
                row = self.rows.pop(key)
                self.list.removeWidget(row)
                row.deleteLater()
        for v in views:
            if v.key not in self.rows:
                row = ProgramRow(v.key, self.scroll.widget())
                row.clicked.connect(self.select)
                row.switched.connect(self._on_switch)
                row.menu_requested.connect(self._on_menu)
                row.volume_clicked.connect(self._on_volume)
                self.rows[v.key] = row
            self.rows[v.key].update_program(v, v.key == self.selected, is_active, now)

        visible = [v.key for v in recent] + ([v.key for v in silent] if self.show_silent else [])
        layout_order = [v.key for v in recent] + ["__silent__"] + [v.key for v in silent]
        if layout_order != getattr(self, "_layout_order", None):
            for key in self.rows:
                self.list.removeWidget(self.rows[key])
            self.list.removeWidget(self.silent_toggle)
            pos = 2  # after Standard and the empty hint
            for key in layout_order:
                widget = self.silent_toggle if key == "__silent__" else self.rows[key]
                self.list.insertWidget(pos, widget)
                pos += 1
            self._layout_order = layout_order
        for v in silent:
            self.rows[v.key].setVisible(self.show_silent)
        for v in recent:
            self.rows[v.key].setVisible(True)
        self.silent_toggle.setVisible(bool(silent))
        icon = ICON_EXPAND_MORE if self.show_silent else ICON_CHEVRON_RIGHT
        self.silent_toggle.setIcon(svg_icon(icon, IDLE, 14))
        self.silent_toggle.setText(t("prog_long_silent", n=len(silent)))
        self.empty.setVisible(not views)
        self.order = visible
        self.standard_row.update_standard(self.selected is STANDARD)

        if self.selected is not STANDARD and self.selected not in self.views:
            self.select(STANDARD)

    def retranslate(self):
        self.setTitle(t("prog_group"))
        self.empty.setText(t("apps_empty"))
        for row in self.rows.values():
            row.switch.setToolTip(t("prog_switch_tip"))
        self.standard_row.update_standard(self.selected is STANDARD)

    # -- selection ------------------------------------------------------------
    def select(self, key):
        if key == self.selected:
            return
        old = self.selected
        self.selected = key
        if old is not STANDARD and old in self.rows and old in self.views:
            self.rows[old].set_selected(False, self.views[old].label)
            _set_prop(self.rows[old].name, "idle", not self.views[old].streams)
        if key is STANDARD:
            self.standard_row.update_standard(True)
        else:
            self.standard_row.update_standard(False)
            if key in self.rows:
                self.rows[key].set_selected(True, self.views[key].label)
                _set_prop(self.rows[key].name, "idle", False)
                self.rows[key].setVisible(True)
                self.scroll.ensureWidgetVisible(self.rows[key])
        self.selection_changed.emit(key)

    def select_step(self, delta):
        """PageUp/PageDown: moves the selection through Standard and the visible programs."""
        chain = [STANDARD] + self.order
        idx = chain.index(self.selected) if self.selected in chain else 0
        self.select(chain[max(0, min(len(chain) - 1, idx + delta))])

    def _toggle_silent(self):
        self.show_silent = not self.show_silent
        self.changed.emit()

    # -- actions --------------------------------------------------------------
    def _on_switch(self, key, on):
        self.router.registry.set_enabled(key, on)
        self.changed.emit()

    def _on_volume(self, key):
        self.router.restore_volume(key)
        self.changed.emit()

    def _on_menu(self, key, pos):
        entry = self.router.registry.get(key)
        if entry is None:
            return
        menu = QMenu(self)
        act_rename = menu.addAction(t("menu_rename"))
        act_volume = menu.addAction(t("app_menu_volume"))
        menu.addSeparator()
        act_forget = menu.addAction(t("app_menu_forget"))
        chosen = menu.exec(pos)
        if chosen == act_rename:
            text, ok = QInputDialog.getText(self, t("rename_app_title"), t("rename_app_prompt"),
                                            text=entry["label"])
            if ok:
                self.router.registry.rename(key, text)
        elif chosen == act_volume:
            self.router.restore_volume(key)
        elif chosen == act_forget:
            if self.selected == key:
                self.select(STANDARD)
            self.router.registry.forget(key)
        else:
            return
        self.changed.emit()
