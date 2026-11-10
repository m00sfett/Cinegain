"""
"Programs & profiles" panel of the Cinegain window.

Shows every application Cinegain has ever heard, with a per-application profile
selector. Rows are updated in place on every heartbeat instead of being rebuilt,
so an open profile drop-down is never torn away under the mouse.
"""
import time

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cinegain_registry import PROFILE_OFF, PROFILE_STANDARD
from cinegain_profiles import STANDARD_SINK, USER_SLOTS
from i18n import t

PANEL_STYLE = """
QFrame#app_row {
    background-color: #14100D;
    border: 1px solid #2A1C16;
    border-radius: 6px;
}
QFrame#app_row[playing="true"] {
    border: 1px solid #6E1516;
    background-color: #1A130F;
}
QLabel#app_dot { font-size: 14px; }
QLabel#app_name { font-size: 13px; font-weight: 700; color: #EDE6DC; }
QLabel#app_name[idle="true"] { color: #A79B8C; font-weight: 600; }
QLabel#app_status { font-size: 11px; color: #A2968A; }
QPushButton#app_volume {
    background-color: #2A1C16; color: #E8C462; border: 1px solid #A86B4A;
    border-radius: 5px; padding: 2px 8px; font-size: 11px; font-weight: 700;
}
QPushButton#app_volume:hover { background-color: #8C1014; color: #FFD21F; border-color: #FFD21F; }
QComboBox#app_profile {
    background-color: #080604; color: #EDE6DC; border: 1px solid #6E1516;
    border-radius: 5px; padding: 3px 8px; min-width: 210px; font-size: 12px;
}
QComboBox#app_profile:hover, QComboBox#app_profile:focus { border-color: #FFD21F; }
QComboBox#app_profile[routed="true"] { color: #FFD21F; font-weight: 700; }
QComboBox#app_profile QAbstractItemView {
    background-color: #1A130F; color: #EDE6DC; border: 1px solid #FFD21F;
    selection-background-color: #8C1014; selection-color: #FFD21F;
}
"""


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


class AppRow(QFrame):
    profile_selected = pyqtSignal(str, str)  # key, profile
    menu_requested = pyqtSignal(str, object)  # key, global pos
    volume_clicked = pyqtSignal(str)

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("app_row")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.menu_requested.emit(self.key, self.mapToGlobal(pos))
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)

        self.dot = QLabel("○")
        self.dot.setObjectName("app_dot")
        self.dot.setFixedWidth(16)
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        self.name = QLabel()
        self.name.setObjectName("app_name")
        self.status = QLabel()
        self.status.setObjectName("app_status")
        text_box.addWidget(self.name)
        text_box.addWidget(self.status)

        self.volume = QPushButton()
        self.volume.setObjectName("app_volume")
        self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume.clicked.connect(lambda: self.volume_clicked.emit(self.key))
        self.volume.hide()

        self.combo = QComboBox()
        self.combo.setObjectName("app_profile")
        self.combo.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.combo.activated.connect(self._on_activated)
        self._choices_sig = None

        lay.addWidget(self.dot)
        lay.addLayout(text_box, 1)
        lay.addWidget(self.volume)
        lay.addWidget(self.combo)

    def _on_activated(self, index):
        profile = self.combo.itemData(index)
        if profile:
            self.profile_selected.emit(self.key, profile)

    def set_choices(self, choices, current):
        sig = (tuple(choices), current)
        if sig == self._choices_sig or self.combo.view().isVisible():
            return
        self._choices_sig = sig
        self.combo.blockSignals(True)
        self.combo.clear()
        for pid, label in choices:
            self.combo.addItem(label, pid)
        idx = self.combo.findData(current)
        if idx < 0:  # profile vanished; show what is effectively used
            idx = self.combo.findData(PROFILE_STANDARD)
        self.combo.setCurrentIndex(max(idx, 0))
        self.combo.blockSignals(False)

    def update_view(self, view, is_active, profile_label, now):
        self.name.setText(view.label)
        tip = view.name if view.binary.lower() == view.name.lower() else f"{view.name} · {view.binary}"
        self.name.setToolTip(tip)
        routed = bool(view.streams) and is_active and view.profile != PROFILE_OFF and \
            (view.sink or "").startswith("cinegain")
        if view.playing:
            self.dot.setText("●")
            self.dot.setStyleSheet(f"color: {'#6FBF8B' if routed else '#E2C89B'};")
        elif view.streams:
            self.dot.setText("⏸")
            self.dot.setStyleSheet("color: #E8C462;")
        else:
            self.dot.setText("○")
            self.dot.setStyleSheet("color: #5E5248;")

        if view.streams:
            if routed:
                label = profile_label if view.sink != STANDARD_SINK else t("profile_standard")
                status = t("routed_profile", profile=label)
            else:
                status = t("routed_direct")
            if view.paused:
                status = f"{t('paused')} · {status}"
        else:
            status = t("last_heard", time_str=format_ago(now - view.last_seen))
        self.status.setText(status)

        if view.streams and (view.muted or view.volume_pct < 100):
            self.volume.setText(t("muted") if view.muted else t("volume_low", pct=view.volume_pct))
            self.volume.setToolTip(t("volume_low_tip"))
            self.volume.show()
        else:
            self.volume.hide()

        for widget, prop, value in (
            (self, "playing", "true" if view.playing else "false"),
            (self.name, "idle", "false" if view.streams else "true"),
            (self.combo, "routed", "true" if routed else "false"),
        ):
            if widget.property(prop) != value:
                widget.setProperty(prop, value)
                _repolish(widget)


class AppsPanel(QWidget):
    """Scrollable list of AppRow widgets bound to a Router."""

    changed = pyqtSignal()

    def __init__(self, router, parent=None):
        super().__init__(parent)
        self.router = router
        self.rows = {}
        self.order = []
        self.setStyleSheet(PANEL_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.content = QWidget()
        self.content.setObjectName("apps_scroll_content")
        self.list_layout = QVBoxLayout(self.content)
        self.list_layout.setContentsMargins(4, 2, 4, 2)
        self.list_layout.setSpacing(4)
        self.empty = QLabel(t("apps_empty"))
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: #A79B8C; font-style: italic; padding: 6px 2px;")
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch()
        self.scroll.setWidget(self.content)
        outer.addWidget(self.scroll)

    # -- labels -------------------------------------------------------------
    def profile_label(self, pid, user_slots=None):
        if pid == PROFILE_OFF:
            return t("profile_off")
        if pid == PROFILE_STANDARD:
            return t("profile_standard")
        if pid in USER_SLOTS:
            slots = user_slots if user_slots is not None else self.router.catalog.user_slots()
            return f"★ {slots[pid]['name']}"
        return t(f"preset_{pid}")

    def choices(self):
        slots = self.router.catalog.user_slots()
        return [(pid, self.profile_label(pid, slots)) for pid, _ in self.router.catalog.choices()]

    # -- updates ----------------------------------------------------------
    def update_views(self, views, is_active):
        now = time.time()
        choices = self.choices()
        labels = dict(choices)
        keys = [v.key for v in views]
        for key in list(self.rows):
            if key not in keys:
                row = self.rows.pop(key)
                self.list_layout.removeWidget(row)
                row.deleteLater()
        for v in views:
            row = self.rows.get(v.key)
            if row is None:
                row = AppRow(v.key, self.content)
                row.profile_selected.connect(self._on_profile_selected)
                row.menu_requested.connect(self._on_menu)
                row.volume_clicked.connect(self._on_volume)
                self.rows[v.key] = row
            row.set_choices(choices, v.profile)
            row.update_view(v, is_active, labels.get(self.router.effective_profile(v.profile), v.profile), now)
        if keys != self.order:
            # Re-insert rows in the new order (above the trailing stretch).
            for key in keys:
                self.list_layout.removeWidget(self.rows[key])
            for i, key in enumerate(keys):
                self.list_layout.insertWidget(1 + i, self.rows[key])
            self.order = keys
        self.empty.setVisible(not views)

    def retranslate(self):
        self.empty.setText(t("apps_empty"))
        for row in self.rows.values():
            row._choices_sig = None

    # -- actions --------------------------------------------------------------
    def _on_profile_selected(self, key, profile):
        self.router.registry.set_profile(key, profile)
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
            text, ok = QInputDialog.getText(
                self, t("rename_app_title"), t("rename_app_prompt"), text=entry["label"]
            )
            if ok:
                self.router.registry.rename(key, text)
        elif chosen == act_volume:
            self.router.restore_volume(key)
        elif chosen == act_forget:
            self.router.registry.forget(key)
        else:
            return
        self.changed.emit()
