#!/usr/bin/env python3
"""
Cinegain Desktop & System Tray Application.
Styled in Tobias's Red-Yellow-Black CI Design System (Red Star #1 / Night-Use Variant).
Features:
- Hero Preamp Volume Booster (-12 dB to +25 dB) + Film Voice DSP controls in 2 columns
- Factory presets (Pure Volume Boost +5..+25 dB, Film/Series, Night Mode, Max Cinema, Neutral 0 dB)
- 8 User Preset slots with live parameter-matching highlight
- Full keyboard navigation (Up/Down switches sliders across all 12 controls, Left/Right adjusts by 1.0 dB, Shift+Left/Right by 0.1 dB)
"""
import sys
import os
import json
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if os.path.exists(os.path.join(BASE_DIR, "bin", "cinegain")):
    BIN_PATH = os.path.join(BASE_DIR, "bin", "cinegain")
elif os.path.exists(os.path.expanduser("~/.local/bin/cinegain")):
    BIN_PATH = os.path.expanduser("~/.local/bin/cinegain")
else:
    BIN_PATH = "cinegain"

APP_VERSION = "0.3.4"

os.environ.setdefault("CINEGAIN_BASE_DIR", BASE_DIR)
import cinegain_paths as paths  # noqa: E402
from cinegain_profiles import DEFAULT_PARAMS, PROFILE_STANDARD, ProfileCatalog, SinkProcesses, full_params, read_meter  # noqa: E402
from cinegain_registry import profile_id_for  # noqa: E402
from cinegain_router import Router  # noqa: E402
from i18n import t, set_language, get_available_languages, get_current_language  # noqa: E402

PRESETS_PATH = os.path.join(BASE_DIR, "presets", "presets.json")
ICON_PATH = os.path.join(BASE_DIR, "assets", "icons", "cinegain.png")
LOGO_SVG_PATH = os.path.join(BASE_DIR, "assets", "icons", "cinegain.svg")
BRAND_ROTESON_PATH = os.path.join(BASE_DIR, "assets", "icons", "brand-roteson.png")
BRAND_R2W2_PATH = os.path.join(BASE_DIR, "assets", "icons", "brand-r2w2.png")
CONFIG_DIR = paths.CONFIG_DIR
USER_PRESETS_FILE = paths.USER_PRESETS_FILE
CONFIG_FILE = paths.CONFIG_FILE
LEVELER_DEFAULT_CUT_DB = 6.0  # cut range given to a leveler the user switches on via the boost slider

try:
    from PyQt6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QGridLayout,
        QLabel,
        QSlider,
        QPushButton,
        QGroupBox,
        QCheckBox,
        QSystemTrayIcon,
        QMenu,
        QScrollArea,
        QSizePolicy,
        QFrame,
        QInputDialog,
        QMessageBox,
    )
    from PyQt6.QtGui import QIcon, QAction, QKeyEvent, QPainter, QColor, QPixmap, QDesktopServices
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QByteArray, QPoint, QUrl
    from PyQt6.QtSvg import QSvgRenderer
except ImportError:
    print("Error: PyQt6 is required to run the Cinegain desktop interface.")
    sys.exit(1)

from program_sidebar import STANDARD, ProgramSidebar  # noqa: E402


def create_translate_icon(color="#FFD21F", size=18):
    """Renders the Material Translate (A / 文) vector icon from KeepaDB."""
    svg_data = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">
        <path fill="{color}" d="M12.87,15.07l-2.54,-2.51 0.03,-0.03c1.74,-1.94 2.98,-4.17 3.71,-6.53L17,6L17,4h-7L10,2L8,2v2L1,4v1.99h11.17C11.5,7.92 10.44,9.75 9,11.35 8.07,10.32 7.3,9.19 6.69,8h-2c0.73,1.63 1.73,3.17 2.98,4.56l-5.09,5.02L4,19l5,-5 3.11,3.11 0.76,-2.04zM18.5,10h-2L12,22h2l1.12,-3h4.75L21,22h2l-4.5,-12zM15.88,17l1.62,-4.33L19.12,17h-3.24z"/>
    </svg>"""
    renderer = QSvgRenderer(QByteArray(svg_data.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)



# All 12 UI-controlled DSP parameters used for preset equivalence comparison
UI_PARAM_KEYS = [
    "input_db",
    "lev_max_boost_db",
    "dialog_db",
    "mid_db",
    "side_db",
    "bass_db",
    "treble_db",
    "lev_target_db",
    "comp_thresh_db",
    "comp_ratio",
    "makeup_db",
    "lim_ceiling_db",
]


CI_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #080604;
    color: #EDE6DC;
    font-family: "Fira Sans", "Noto Sans", "Segoe UI", sans-serif;
    font-size: 13px;
}

/* Red Star #1 Banner Header */
QFrame#ci_banner {
    background-color: #B11218;
    border: 2px solid #FFD21F;
    border-radius: 8px;
}

QFrame#ci_banner QLabel {
    background-color: transparent;
}

/* Section Panels */
QGroupBox {
    background-color: #14100D;
    border: 1px solid #6E1516;
    border-radius: 8px;
    margin-top: 10px;
    padding: 14px 12px 10px 12px;
    font-family: "Fira Sans Condensed", "Fira Sans", sans-serif;
    font-size: 13px;
    font-weight: 700;
    color: #E0B83A;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    background-color: #080604;
    color: #FFD21F;
}

QScrollArea {
    background-color: transparent;
    border: none;
}

QWidget#apps_scroll_content, QLabel, QCheckBox, QSlider {
    background-color: transparent;
}

QScrollBar:vertical {
    background: #120C09;
    width: 8px;
    margin: 0px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #6E1516;
    min-height: 20px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #FFD21F;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

/* Slider Row Containers (Normal & Hero & Keyboard Focus) */
QFrame#slider_row {
    background-color: #1B1410;
    border: 1px solid #32241D;
    border-radius: 6px;
}

QFrame#slider_row[focusedRow="true"] {
    background-color: #2E2115;
    border: 2px solid #FFD21F;
}

QFrame#hero_slider_row {
    background-color: #1F1712;
    border: 2px solid #8F2026;
    border-radius: 8px;
}

QFrame#hero_slider_row[focusedRow="true"] {
    background-color: #2E2115;
    border: 2px solid #FFD21F;
}

/* Buttons & Presets (Yellow is Action) */
QPushButton {
    background-color: #1F1712;
    color: #EDE6DC;
    border: 1px solid #6E1516;
    border-radius: 6px;
    padding: 6px 10px;
    font-weight: 600;
    min-height: 18px;
}

QPushButton:hover {
    background-color: #32241D;
    border-color: #E0B83A;
    color: #FFD21F;
}

QPushButton:pressed {
    background-color: #FFD21F;
    color: #241900;
    border-color: #FFE15A;
}

/* Occupied User Preset Slot (when not currently active) */
QPushButton[userSaved="true"] {
    background-color: #261C10;
    color: #FFE69A;
    border: 1px solid #A86B4A;
}

/* Active Preset (Factory or User): Strong Signal Yellow */
QPushButton[presetActive="true"] {
    background-color: #FFD21F;
    color: #241900;
    border: 2px solid #FFE15A;
    font-weight: 800;
}

QPushButton[presetActive="true"]:hover {
    background-color: #FFE15A;
    color: #241900;
}

/* Master Power Button inside Red Banner */
QPushButton#btn_power_on {
    background-color: #FFD21F;
    border: 2px solid #241900;
    color: #241900;
    font-family: "Fira Sans Condensed", "Fira Sans", sans-serif;
    font-size: 14px;
    font-weight: 800;
}

QPushButton#btn_power_on:hover {
    background-color: #FFE15A;
}

QPushButton#btn_power_off {
    background-color: #240608;
    border: 2px solid #FFD21F;
    color: #FFD21F;
    font-family: "Fira Sans Condensed", "Fira Sans", sans-serif;
    font-size: 14px;
    font-weight: 800;
}

QPushButton#btn_power_off:hover {
    background-color: #3D0A0E;
    border-color: #FFE15A;
    color: #FFE15A;
}

QPushButton#btn_quit {
    background-color: #1F1712;
    border: 1px solid #3A2B24;
    color: #E2C89B;
    font-weight: 600;
}

QPushButton#btn_quit:hover {
    background-color: #5F1111;
    border-color: #FF6B5F;
    color: #FFE5E1;
}

QPushButton#btn_lang {
    background-color: #1F1712;
    border: 1px solid #FFD21F;
    color: #FFD21F;
    font-weight: 700;
    padding: 6px 8px;
    border-radius: 6px;
}

QPushButton#btn_lang:hover {
    background-color: #38241C;
    border-color: #FFE066;
    color: #FFE066;
}


/* Wide, High-Visibility Sliders */
QSlider {
    min-height: 26px;
}

QSlider::groove:horizontal {
    height: 10px;
    background: #0B0806;
    border: 1px solid #4A251D;
    border-radius: 5px;
}

QSlider::sub-page:horizontal {
    background: #E0B83A;
    border: 1px solid #FFD21F;
    border-radius: 5px;
}

QSlider::handle:horizontal {
    background: #FFD21F;
    border: 2px solid #241900;
    width: 18px;
    margin-top: -6px;
    margin-bottom: -6px;
    border-radius: 9px;
}

QPushButton:focus, QCheckBox:focus, QFrame#program_row:focus { border: 2px solid #FFF1D6; }
QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus {
    background: #FFE15A;
    border: 2px solid #B11218;
}

/* Hero Direct-Boost Slider (Extra Wide & Prominent) */
QSlider#hero_slider {
    min-height: 32px;
}

QSlider#hero_slider::groove:horizontal {
    height: 14px;
    background: #080604;
    border: 1px solid #6E1516;
    border-radius: 7px;
}

QSlider#hero_slider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #D01822, stop:0.35 #E0B83A, stop:1 #FFD21F);
    border: 1px solid #FFE15A;
    border-radius: 7px;
}

QSlider#hero_slider::handle:horizontal {
    background: #FFD21F;
    border: 3px solid #241900;
    width: 22px;
    margin-top: -6px;
    margin-bottom: -6px;
    border-radius: 11px;
}

QSlider#hero_slider::handle:horizontal:hover, QSlider#hero_slider::handle:horizontal:focus {
    background: #FFE15A;
    border: 3px solid #B11218;
}

/* Checkboxes for Audio Sources */
QCheckBox {
    spacing: 8px;
    color: #EDE6DC;
    font-weight: 600;
    min-height: 22px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #A86B4A;
    background: #1B1410;
}

QCheckBox::indicator:checked {
    background: #FFD21F;
    border: 2px solid #241900;
}

QMenu {
    background-color: #14100D;
    color: #EDE6DC;
    border: 1px solid #6E1516;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item {
    padding: 6px 20px 6px 12px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #FFD21F;
    color: #241900;
    font-weight: 700;
}

QMenu::separator {
    height: 1px;
    background: #6E1516;
    margin: 4px 0;
}
"""


def open_link(url):
    """Opens a URL in the default browser; falls back to xdg-open. Never raises."""
    try:
        if QDesktopServices.openUrl(QUrl(url)):
            return
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        pass


class LinkLabel(QLabel):
    """Label that opens a URL on left click."""

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            open_link(self._url)
        super().mousePressEvent(event)


def brand_label(path, size, tooltip, link=None):
    """Crisp logo label for the banner (HiDPI-aware); optional link opens in the browser."""
    lbl = LinkLabel(link) if link else QLabel()
    lbl.setFixedSize(size, size)
    lbl.setStyleSheet("background: transparent; border: none;")
    lbl.setToolTip(tooltip)
    if path.endswith(".svg"):
        renderer = QSvgRenderer(path)
        dpr = 2.0
        pix = QPixmap(int(size * dpr), int(size * dpr))
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        renderer.render(painter)
        painter.end()
    else:
        pix = QPixmap(path)
        dpr = max(1.0, pix.width() / size)
        pix = pix.scaled(int(size * dpr), int(size * dpr), Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    pix.setDevicePixelRatio(dpr)
    lbl.setPixmap(pix)
    if link:
        lbl.setCursor(Qt.CursorShape.PointingHandCursor)
    return lbl


def amp(text):
    """Escapes '&' so Qt shows it instead of turning it into a keyboard mnemonic."""
    return text.replace("&", "&&")


def run_cinegain(*args):
    cmd = [BIN_PATH] + list(args)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
    except OSError:
        return "", 1
    return res.stdout.strip(), res.returncode


class MeterStrip(QWidget):
    """Four small live bars: input peak, leveler gain, compressor and limiter reduction."""

    # label, low, high (dB); bars with a negative range grow from the right (reduction)
    BARS = (("IN", -60.0, 0.0), ("LEVELER", -12.0, 24.0), ("COMP", -24.0, 0.0), ("LIMIT", -12.0, 0.0))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.values = None  # (level, leveler, gr, limit) or None when idle

    def set_values(self, values):
        if values != self.values:
            self.values = values
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        gap = 10
        bw = (w - gap * (len(self.BARS) - 1)) / len(self.BARS)
        for i, (name, lo, hi) in enumerate(self.BARS):
            x = i * (bw + gap)
            p.fillRect(int(x), 14, int(bw), 8, QColor("#14100D"))
            p.setPen(QColor("#A79B8C"))
            p.drawText(int(x), 0, int(bw), 12, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            if self.values is None:
                continue
            v = self.values[i]
            frac = max(0.0, min(1.0, (v - lo) / (hi - lo)))
            if name in ("COMP", "LIMIT"):
                frac = 1.0 - frac  # reduction grows with magnitude
                color = QColor("#FFD21F") if v > -6 else QColor("#B11218")
            elif name == "IN":
                color = QColor("#6FBF8B") if v < -3 else QColor("#B11218")
            else:
                color = QColor("#FFD21F")
            p.fillRect(int(x), 14, int(bw * frac), 8, color)
            p.setPen(QColor("#EDE6DC"))
            p.drawText(int(x), 0, int(bw), 12, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{v:+.0f} dB" if name == "LEVELER" else f"{max(v, -99):.0f} dB")
        p.end()


class KeyNavSlider(QSlider):
    """
    Custom QSlider that delegates Up/Down/F6 to the window's 12-slider focus ring
    and uses Left/Right for crisp 1.0 dB steps (Shift+Left/Right for 0.1 dB fine steps).
    """

    navRequested = pyqtSignal(int)  # -1 = prev, +1 = next, 100 = switch column
    programRequested = pyqtSignal(int)  # PageUp = -1, PageDown = +1 (program sidebar)
    focusedIn = pyqtSignal()

    def __init__(self, orientation, param_name, parent=None):
        super().__init__(orientation, parent)
        self.param_name = param_name
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focusedIn.emit()

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key.Key_Up:
            self.navRequested.emit(-1)
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            self.navRequested.emit(1)
            event.accept()
            return
        if key == Qt.Key.Key_F6:
            self.navRequested.emit(100)
            event.accept()
            return
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.programRequested.emit(-1 if key == Qt.Key.Key_PageUp else 1)
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            direction = 1 if key == Qt.Key.Key_Right else -1
            if mods & Qt.KeyboardModifier.ShiftModifier:
                step = 1  # 0.1 dB fine step
            else:
                # 1.0 dB standard step (0.5 dB for limiter ceiling near 0 dB)
                step = 5 if self.param_name == "lim_ceiling_db" else 10
            new_val = max(self.minimum(), min(self.maximum(), self.value() + direction * step))
            self.setValue(new_val)
            event.accept()
            return
        super().keyPressEvent(event)


class SliderRowWidget(QFrame):
    """Clickable row frame that highlights when its slider is active in keyboard navigation."""

    clicked = pyqtSignal()

    def __init__(self, is_hero=False, parent=None):
        super().__init__(parent)
        self.setObjectName("hero_slider_row" if is_hero else "slider_row")
        self.setProperty("focusedRow", "false")

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class CinegainMainWindow(QMainWindow):
    def __init__(self, router):
        super().__init__()
        self.router = router
        self.is_active = False
        self.catalog = ProfileCatalog()
        self.selected_key = STANDARD  # None = Standard, otherwise a program key
        self._program_dirty = None  # program key whose own values wait for the debounced flush
        self._initial_selection_done = False
        self.sliders = {}  # param_name -> dict(slider, val_lbl, title_lbl, row_frame, base_title, unit, is_hero)
        self.slider_order = []  # ordered list of param_names for Up/Down keyboard navigation
        self.slider_ui_elements = {}  # param_name -> dict(label, frame, label_key, tip_key)
        self.focused_slider_idx = 0

        self._load_config()
        self.factory_presets = self._load_factory_presets()
        self.user_presets = self._load_user_presets()
        self.preset_buttons = {}  # preset_id -> QPushButton
        self.user_preset_buttons = {}  # slot_id -> QPushButton

        self.setWindowTitle(t("app_title"))
        self.setStyleSheet(CI_STYLESHEET)
        self.setMinimumSize(480, 360)
        available = self.screen().availableGeometry()
        self.resize(min(1400, available.width()), min(880, available.height()))

        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))

        self.init_ui()
        self.init_tray()

        # Debounce timer for slider changes (smooth 60fps dragging & keyboard repeating)
        self.pending_params = {}
        self.param_timer = QTimer(self)
        self.param_timer.setSingleShot(True)
        self.param_timer.setInterval(35)
        self.param_timer.timeout.connect(self.flush_pending_params)

        # Heartbeat timer for PipeWire status & stream tracking
        self._meter_seq = {}
        self._meter_idle = 0
        self.meter_timer = QTimer(self)
        self.meter_timer.setInterval(120)
        self.meter_timer.timeout.connect(self.update_meters)
        self.meter_timer.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_heartbeat)
        self.timer.start(1500)

        # Auto-start Cinegain engine on initial GUI launch if not running
        out, code = run_cinegain("status", "--json")
        try:
            st = json.loads(out)
            if code in (0, 1) and isinstance(st, dict) and st.get("active") is False:
                run_cinegain("start", "--route")
        except ValueError:
            pass

        self.on_heartbeat(sync_sliders=True)
        # Focus the primary Direct-Boost slider immediately for instant keyboard control
        self.set_focused_slider(0)

    def _load_factory_presets(self):
        try:
            with open(PRESETS_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            cleaned = {}
            for pid, pdata in raw.items():
                pdict = {}
                for k, v in pdata.get("params", {}).items():
                    pdict[k.replace("cinegain:", "")] = float(v)
                cleaned[pid] = {
                    "name": pdata.get("name", pid),
                    "description": pdata.get("description", ""),
                    "params": pdict,
                }
            return cleaned
        except Exception:
            return {}

    def _load_user_presets(self):
        slots = {f"user_{i}": {"name": f"Eigenes {i}", "params": None} for i in range(1, 9)}
        if os.path.exists(USER_PRESETS_FILE):
            try:
                with open(USER_PRESETS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for sid, sdata in data.items():
                    if sid in slots and isinstance(sdata, dict):
                        slots[sid]["name"] = sdata.get("name", slots[sid]["name"])
                        if isinstance(sdata.get("params"), dict) and sdata["params"]:
                            slots[sid]["params"] = {
                                k.replace("cinegain:", ""): float(v)
                                for k, v in sdata["params"].items()
                            }
            except Exception:
                pass
        return slots

    def _save_user_presets(self):
        try:
            paths.write_json_atomic(USER_PRESETS_FILE, self.user_presets)
        except OSError:
            pass

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict) and "lang" in cfg:
                    set_language(cfg["lang"])
            except Exception:
                pass

    def _save_config(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"lang": get_current_language()}, f, indent=2)
        except Exception:
            pass

    def show_language_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1A130F;
                color: #EDE6DC;
                border: 1px solid #FFD21F;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px 6px 20px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #8C1014;
                color: #FFD21F;
            }
        """)
        current = get_current_language()
        for code, name in get_available_languages().items():
            prefix = "✓ " if code == current else "   "
            act = QAction(f"{prefix}{name} ({code})", self)
            act.triggered.connect(lambda checked=False, c=code: self.change_language(c))
            menu.addAction(act)
        menu.exec(self.btn_lang.mapToGlobal(QPoint(0, self.btn_lang.height() + 4)))

    def change_language(self, lang_code):
        if set_language(lang_code):
            self._save_config()
            self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(t("app_title"))
        self.lbl_subtitle.setText(self._subtitle())
        self.btn_lang.setText(f" {get_current_language().upper()}")
        self.btn_quit.setText(t("btn_quit"))
        self.btn_quit.setToolTip(t("btn_quit_tip"))
        self.btn_power.setText(t("btn_power_off") if self.is_active else t("btn_power_on"))
        self.lbl_status.setText(t("status_active") if self.is_active else t("status_inactive"))

        self.details.setText(t("details"))
        if hasattr(self, "grp_presets"):
            self.grp_presets.setTitle(amp(t("presets_group").partition("(")[0].partition("（")[0].strip()))
        if hasattr(self, "grp_main"):
            self.grp_main.setTitle(amp(t("main_group")))
        if hasattr(self, "grp_adv"):
            self.grp_adv.setTitle(amp(t("adv_group")))
        if hasattr(self, "sidebar"):
            self.sidebar.retranslate()
            self.update_edit_header()
        if hasattr(self, "btn_reset"):
            self.btn_reset.setText(t("btn_reset"))
            self.btn_reset.setToolTip(t("btn_reset_tip"))

        for pid, b in self.preset_buttons.items():
            b.setText(amp(t(f"preset_{pid}")))

        for param_name, meta in self.slider_ui_elements.items():
            lbl = meta["label"]
            frame = meta["frame"]
            lbl_key = meta["label_key"]
            tip_key = meta["tip_key"]
            lbl.setText(t(lbl_key) if lbl_key.startswith("lbl_") else lbl_key)
            if tip_key:
                tooltip = t(tip_key) if tip_key.startswith("tip_") else tip_key
                lbl.setToolTip(tooltip)
                frame.setToolTip(tooltip)

        self._refresh_user_preset_buttons_text()

        if hasattr(self, "tray_open_action"):
            self.tray_open_action.setText(t("tray_open"))
            self.tray_toggle_action.setText(t("tray_toggle"))
            self.tray_quit_action.setText(t("tray_quit"))

        self.on_heartbeat(sync_sliders=False)

    def init_ui(self):
        main_widget = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(main_widget)
        self.setCentralWidget(scroll)
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # 1. CI Red Banner Header (#B11218 background, #FFD21F typography)
        banner = QFrame()
        banner.setObjectName("ci_banner")
        hdr = QHBoxLayout(banner)
        hdr.setContentsMargins(16, 10, 14, 10)
        hdr.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.lbl_title = QLabel("CINEGAIN")
        self.lbl_title.setStyleSheet(
            'font-family: "Fira Sans Condensed", sans-serif; font-size: 22px; '
            "font-weight: 800; letter-spacing: 1.5px; color: #FFD21F;"
        )
        self.lbl_subtitle = QLabel(self._subtitle())
        self.lbl_subtitle.setWordWrap(True)
        self.lbl_subtitle.setStyleSheet("font-size: 12px; font-weight: 600; color: #FFF1D6;")
        title_box.addWidget(self.lbl_title)
        title_box.addWidget(self.lbl_subtitle)

        self.lbl_status = QLabel(t("status_checking"))
        self.lbl_status.setStyleSheet(
            'font-family: "Fira Sans Condensed", sans-serif; font-size: 13px; '
            "font-weight: 800; color: #FFD21F; background-color: #1F1712; "
            "border: 1px solid #FFD21F; border-radius: 6px; padding: 6px 12px;"
        )

        self.btn_power = QPushButton(t("btn_power_on"))
        self.btn_power.setObjectName("btn_power_on")
        self.btn_power.setFixedWidth(165)
        self.btn_power.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_power.clicked.connect(self.toggle_power)

        self.btn_lang = QPushButton()
        self.btn_lang.setObjectName("btn_lang")
        self.btn_lang.setIcon(create_translate_icon("#FFD21F", 18))
        self.btn_lang.setText(f" {get_current_language().upper()}")
        self.btn_lang.setFixedWidth(84)
        self.btn_lang.setToolTip("Language / Sprache (19 languages)")
        self.btn_lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lang.clicked.connect(self.show_language_menu)

        self.btn_quit = QPushButton(t("btn_quit"))
        self.btn_quit.setObjectName("btn_quit")
        self.btn_quit.setFixedWidth(92)
        self.btn_quit.setToolTip(t("btn_quit_tip"))
        self.btn_quit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_quit.clicked.connect(self.quit_app)

        if os.path.exists(LOGO_SVG_PATH):
            hdr.addWidget(brand_label(LOGO_SVG_PATH, 46, "Cinegain"), 0, Qt.AlignmentFlag.AlignVCenter)
        hdr.addLayout(title_box)
        hdr.addStretch()
        hdr.addWidget(self.lbl_status)
        hdr.addWidget(self.btn_power)
        hdr.addWidget(self.btn_lang)
        hdr.addWidget(self.btn_quit)
        brands = [(BRAND_ROTESON_PATH, "roteson.de", "https://roteson.de"),
                  (BRAND_R2W2_PATH, "R²W² Development", None)]
        brands = [b for b in brands if os.path.exists(b[0])]
        if brands:
            hdr.addSpacing(6)
            for path, tip, link in brands:
                hdr.addWidget(brand_label(path, 50, tip, link), 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(banner)

        # Body: program sidebar (left) selects what the editor (right) changes.
        body = QHBoxLayout()
        body.setSpacing(14)
        self.sidebar = ProgramSidebar(self.router)
        self.sidebar.selection_changed.connect(self.on_selection_changed)
        self.sidebar.changed.connect(self.refresh_apps)
        body.addWidget(self.sidebar)
        editor = QVBoxLayout()
        editor.setSpacing(10)
        editor.addWidget(self._build_edit_header())
        body.addLayout(editor, 1)
        layout.addLayout(body, 1)

        # 2. Presets Section (Row 1: Pure Boost + Cinema Profiles | Row 2: 8 User Presets)
        self.grp_presets = QGroupBox(amp(t("presets_group").partition("(")[0].partition("（")[0].strip()))
        presets_vbox = QVBoxLayout(self.grp_presets)
        presets_vbox.setSpacing(8)
        presets_vbox.setContentsMargins(10, 12, 10, 8)

        # Row 1: Factory Presets
        row1 = QGridLayout()
        row1.setSpacing(6)

        factory_row_def = [
            ("clean_linear", t("preset_clean_linear"), "0 dB"),
            ("boost_5db", t("preset_boost_5db"), "+5 dB"),
            ("boost_10db", t("preset_boost_10db"), "+10 dB"),
            ("boost_15db", t("preset_boost_15db"), "+15 dB"),
            ("boost_20db", t("preset_boost_20db"), "+20 dB"),
            ("boost_25db", t("preset_boost_25db"), "+25 dB"),
            ("|", "", ""),
            ("dialog_boost", t("preset_dialog_boost"), ""),
            ("night_mode", t("preset_night_mode"), ""),
            ("max_loudness", t("preset_max_loudness"), ""),
        ]

        self.details = QCheckBox(t("details"))
        self.details.toggled.connect(self.set_details_visible)
        for index, (pid, text, tip) in enumerate(factory_row_def):
            if pid == "|":
                continue
            b = QPushButton(amp(text))
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda checked=False, p=pid: self.apply_factory_preset(p))
            row1.addWidget(b, 1 if index < 6 else 0, index if index < 6 else index - 7)
            self.preset_buttons[pid] = b

        row1.addWidget(self.details, 0, 3, 1, 3)
        presets_vbox.addLayout(row1)

        # Row 2: 8 User Preset Slots
        row2 = QGridLayout()
        row2.setSpacing(6)
        for i in range(1, 9):
            sid = f"user_{i}"
            b = QPushButton()
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.clicked.connect(lambda checked=False, s=sid: self.on_user_preset_clicked(s))
            b.customContextMenuRequested.connect(
                lambda pos, s=sid, btn=b: self.show_user_preset_menu(s, btn, pos)
            )
            row2.addWidget(b, (i-1) // 4, (i-1) % 4)
            self.user_preset_buttons[sid] = b

        presets_vbox.addLayout(row2)
        editor.addWidget(self.grp_presets)

        self.meter_strip = MeterStrip()
        self.meter_strip.setToolTip(t('meter_tip'))
        editor.addWidget(self.meter_strip)

        # 3. Side-by-Side Two-Column Controls (Left: Hero Direct Boost + Main Focus, Right: Fine-Tuning)
        cols_layout = QHBoxLayout()
        cols_layout.setSpacing(14)

        # Left Column: Main Volume & Speech Focus (No numbering!)
        self.grp_main = QGroupBox(amp(t("main_group")))
        gm_layout = QVBoxLayout(self.grp_main)
        gm_layout.setSpacing(8)
        gm_layout.setContentsMargins(10, 14, 10, 10)

        # HERO CONTROL: Direct Volume Boost (-12 dB to +25 dB)
        self.add_control_slider(
            gm_layout,
            "lbl_input_db",
            "input_db",
            -12.0,
            25.0,
            8.0,
            " dB",
            "tip_input_db",
            is_hero=True,
        )

        self.add_control_slider(
            gm_layout,
            "lbl_lev_max_boost_db",
            "lev_max_boost_db",
            0.0,
            24.0,
            15.0,
            " dB",
            "tip_lev_max_boost_db",
        )

        self.add_control_slider(
            gm_layout,
            "lbl_dialog_db",
            "dialog_db",
            -6.0,
            15.0,
            6.5,
            " dB",
            "tip_dialog_db",
        )

        self.add_control_slider(
            gm_layout,
            "lbl_mid_db",
            "mid_db",
            -6.0,
            12.0,
            2.0,
            " dB",
            "tip_mid_db",
        )

        self.add_control_slider(
            gm_layout,
            "lbl_side_db",
            "side_db",
            -15.0,
            6.0,
            -3.0,
            " dB",
            "tip_side_db",
        )
        gm_layout.addStretch()

        # Right Column: Fine-Tuning (EQ, Compressor & Limiter)
        self.grp_adv = QGroupBox(amp(t("adv_group")))
        adv_layout = QVBoxLayout(self.grp_adv)
        adv_layout.setSpacing(6)
        adv_layout.setContentsMargins(10, 14, 10, 10)

        self.add_control_slider(
            adv_layout,
            "lbl_bass_db",
            "bass_db",
            -15.0,
            12.0,
            -3.0,
            " dB",
            "tip_bass_db",
        )
        self.add_control_slider(
            adv_layout,
            "lbl_treble_db",
            "treble_db",
            -12.0,
            12.0,
            1.5,
            " dB",
            "tip_treble_db",
        )
        self.add_control_slider(
            adv_layout,
            "lbl_lev_target_db",
            "lev_target_db",
            -30.0,
            -6.0,
            -16.0,
            " dB",
            "tip_lev_target_db",
        )
        self.add_control_slider(
            adv_layout,
            "lbl_comp_thresh_db",
            "comp_thresh_db",
            -40.0,
            0.0,
            -12.0,
            " dB",
            "tip_comp_thresh_db",
        )
        self.add_control_slider(
            adv_layout,
            "lbl_comp_ratio",
            "comp_ratio",
            1.0,
            12.0,
            3.5,
            ":1",
            "tip_comp_ratio",
        )
        self.add_control_slider(
            adv_layout,
            "lbl_makeup_db",
            "makeup_db",
            -6.0,
            18.0,
            2.0,
            " dB",
            "tip_makeup_db",
        )
        self.add_control_slider(
            adv_layout,
            "lbl_lim_ceiling_db",
            "lim_ceiling_db",
            -12.0,
            0.0,
            -0.5,
            " dB",
            "tip_lim_ceiling_db",
        )
        adv_layout.addStretch()

        cols_layout.addWidget(self.grp_main, 1)
        cols_layout.addWidget(self.grp_adv, 1)
        editor.addLayout(cols_layout, 1)
        self.set_details_visible(False)

        # 5. Footer with Version, AGPLv3, and roteson.de link
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(4, 2, 4, 0)
        self.btn_reset = QPushButton(t("btn_reset"))
        self.btn_reset.setObjectName("btn_reset")
        self.btn_reset.setToolTip(t("btn_reset_tip"))
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_reset.setStyleSheet(
            "QPushButton#btn_reset { background: transparent; color: #A79B8C; border: 1px solid #3A2A22; "
            "border-radius: 5px; padding: 3px 10px; font-size: 11px; font-weight: 700; }"
            "QPushButton#btn_reset:hover { color: #FFD21F; border-color: #B11218; background: #2A0E0E; }"
        )
        self.btn_reset.clicked.connect(self.reset_everything)
        footer_layout.addWidget(self.btn_reset)
        self.lbl_footer = QLabel(
            f'<span style="color:#A79B8C;">Cinegain v{APP_VERSION} · AGPLv3 · </span>'
            f'<a href="https://roteson.de" style="color:#FFD21F; text-decoration:none; font-weight:700;">roteson.de</a>'
        )
        self.lbl_footer.setOpenExternalLinks(True)
        self.lbl_footer.setStyleSheet("font-size: 11px;")
        footer_layout.addStretch()
        footer_layout.addWidget(self.lbl_footer)
        layout.addLayout(footer_layout)

        self._refresh_user_preset_buttons_text()

    def add_control_slider(
        self,
        parent_layout,
        label_key,
        param_name,
        min_val,
        max_val,
        def_val,
        unit,
        tip_key=None,
        is_hero=False,
    ):
        # Start from the neutral factory default; real values arrive with the first status.
        def_val = DEFAULT_PARAMS.get(param_name, def_val)
        idx = len(self.slider_order)
        self.slider_order.append(param_name)

        row_frame = SliderRowWidget(is_hero=is_hero)
        box = QVBoxLayout(row_frame)
        if is_hero:
            box.setContentsMargins(12, 10, 12, 10)
            box.setSpacing(6)
        else:
            box.setContentsMargins(10, 6, 10, 6)
            box.setSpacing(3)

        hdr = QHBoxLayout()
        label_text = t(label_key) if label_key.startswith("lbl_") else label_key
        lbl = QLabel(label_text)
        lbl.setMinimumHeight(20 if is_hero else 18)
        if tip_key:
            tooltip = t(tip_key) if tip_key.startswith("tip_") else tip_key
            lbl.setToolTip(tooltip)
            row_frame.setToolTip(tooltip)

        self.slider_ui_elements[param_name] = {
            "label": lbl,
            "frame": row_frame,
            "label_key": label_key,
            "tip_key": tip_key,
        }


        val_lbl = QLabel(self._format_val(param_name, def_val, unit))
        val_lbl.setFixedWidth(84 if is_hero else 74)
        val_lbl.setMinimumHeight(20 if is_hero else 18)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val_font_size = "16px" if is_hero else "13px"
        val_lbl.setStyleSheet(
            f'color: #FFD21F; font-family: "Fira Mono", monospace; font-size: {val_font_size}; font-weight: 800;'
        )

        hdr.addWidget(lbl)
        hdr.addStretch()
        hdr.addWidget(val_lbl)

        slider = KeyNavSlider(Qt.Orientation.Horizontal, param_name)
        if is_hero:
            slider.setObjectName("hero_slider")
        slider.setMinimum(int(round(min_val * 10)))
        slider.setMaximum(int(round(max_val * 10)))
        slider.setValue(int(round(def_val * 10)))
        slider.setCursor(Qt.CursorShape.PointingHandCursor)

        slider.valueChanged.connect(
            lambda v, p=param_name, vl=val_lbl, u=unit: self.on_slider_changed(p, v / 10.0, vl, u)
        )
        slider.focusedIn.connect(lambda i=idx: self.set_focused_slider(i, set_qt_focus=False))
        slider.programRequested.connect(self.sidebar.select_step)
        slider.navRequested.connect(lambda delta, i=idx: self.navigate_slider(i, delta))
        row_frame.clicked.connect(lambda i=idx: self.set_focused_slider(i, set_qt_focus=True))

        box.addLayout(hdr)
        box.addWidget(slider)
        parent_layout.addWidget(row_frame)

        self.sliders[param_name] = {
            "slider": slider,
            "val_lbl": val_lbl,
            "title_lbl": lbl,
            "row_frame": row_frame,
            "base_title": label_text,
            "unit": unit,
            "is_hero": is_hero,
        }
        self._apply_row_focus_style(param_name, False)

    def _apply_row_focus_style(self, param_name, is_focused):
        info = self.sliders[param_name]
        row_frame = info["row_frame"]
        lbl = info["title_lbl"]
        base_title = info["base_title"]
        is_hero = info["is_hero"]

        row_frame.setProperty("focusedRow", "true" if is_focused else "false")
        row_frame.style().unpolish(row_frame)
        row_frame.style().polish(row_frame)

        if is_focused:
            lbl.setText(f"▶ {base_title}")
            font_sz = "14px" if is_hero else "13px"
            lbl.setStyleSheet(
                f"font-size: {font_sz}; font-weight: 800; color: #FFD21F;"
            )
        else:
            lbl.setText(base_title)
            if is_hero:
                lbl.setStyleSheet(
                    'font-family: "Fira Sans Condensed", sans-serif; font-size: 14px; font-weight: 800; color: #FFE15A;'
                )
            else:
                lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #EDE6DC;")

    def set_focused_slider(self, idx, set_qt_focus=True):
        if not self.slider_order:
            return
        idx = idx % len(self.slider_order)
        self.focused_slider_idx = idx
        for i, pname in enumerate(self.slider_order):
            self._apply_row_focus_style(pname, i == idx)
        if set_qt_focus:
            target_slider = self.sliders[self.slider_order[idx]]["slider"]
            if not target_slider.hasFocus():
                target_slider.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_details_visible(self, visible):
        self.grp_adv.setVisible(visible)
        for pid, button in self.preset_buttons.items():
            if pid == "clean_linear" or pid.startswith("boost_"):
                button.setVisible(visible)
        for button in self.user_preset_buttons.values():
            button.setVisible(visible)

    def navigate_slider(self, current_idx, delta):
        total = len(self.slider_order) if self.details.isChecked() else 5
        if total == 0:
            return
        if delta == 100 and total == 5:
            self.details.setChecked(True)
            total = len(self.slider_order)
        if delta == 100:
            # Switch between Left column (0..4) and Right column (5..11)
            if current_idx < 5:
                next_idx = min(current_idx + 5, total - 1)
            else:
                next_idx = min(current_idx - 5, 4)
        else:
            next_idx = (current_idx + delta) % total
        self.set_focused_slider(next_idx, set_qt_focus=True)

    def keyPressEvent(self, event: QKeyEvent):
        """Fallback window-level arrow handling if no slider currently holds focus."""
        key = event.key()
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.sidebar.select_step(-1 if key == Qt.Key.Key_PageUp else 1)
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            active_pname = self.slider_order[self.focused_slider_idx]
            active_slider = self.sliders[active_pname]["slider"]
            active_slider.setFocus(Qt.FocusReason.ShortcutFocusReason)
            active_slider.keyPressEvent(event)
            return
        super().keyPressEvent(event)

    def _format_val(self, param_name, val, unit):
        if param_name == "comp_ratio":
            return f"{val:.1f}{unit}"
        return f"{val:+.1f}{unit}" if val > 0 else f"{val:.1f}{unit}"

    def get_current_slider_params(self):
        return {
            pname: round(info["slider"].value() / 10.0, 2)
            for pname, info in self.sliders.items()
        }

    def _params_match(self, p_a, p_b, tol=0.08):
        if not p_a or not p_b:
            return False
        for k in UI_PARAM_KEYS:
            if k not in p_a or k not in p_b:
                return False
            if abs(float(p_a[k]) - float(p_b[k])) > tol:
                return False
        return True

    def _refresh_user_preset_buttons_text(self):
        for sid, btn in self.user_preset_buttons.items():
            slot = self.user_presets.get(sid, {})
            name = slot.get("name", sid)
            params = slot.get("params")
            if params:
                in_db = params.get("input_db", 0.0)
                sign = "+" if in_db > 0 else ""
                btn.setText(amp(t("user_slot_saved", name=name, sign=sign, db=in_db)))
                btn.setToolTip(t("user_slot_saved_tip", name=name, sign=sign, db=in_db))
                btn.setProperty("userSaved", "true")
            else:
                btn.setText(amp(t("user_slot_free", name=name)))
                btn.setToolTip(t("user_slot_free_tip", name=name))
                btn.setProperty("userSaved", "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def update_preset_highlights(self):
        """
        Highlights ANY factory or user preset whose 12 parameters match the current sliders.
        Also highlights the active user slot if it matches.
        """
        current = self.get_current_slider_params()

        # Check all factory presets
        for pid, btn in self.preset_buttons.items():
            f_params = self.factory_presets.get(pid, {}).get("params")
            matched = self._params_match(current, f_params)
            btn.setProperty("presetActive", "true" if matched else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # Check all 8 user presets
        for sid, btn in self.user_preset_buttons.items():
            u_params = self.user_presets.get(sid, {}).get("params")
            matched = self._params_match(current, u_params)
            btn.setProperty("presetActive", "true" if matched else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def on_slider_changed(self, param_name, val, val_lbl, unit):
        val_lbl.setText(self._format_val(param_name, val, unit))
        if self.selected_key is STANDARD:
            self.pending_params[param_name] = val
            if param_name == "lev_max_boost_db" and val > 0.05 and self.catalog.standard_params()["lev_max_cut_db"] <= 0.05:
                # a leveler that can only boost never pulls loud scenes back
                self.pending_params["lev_max_cut_db"] = LEVELER_DEFAULT_CUT_DB
        else:
            self._store_program_values(self.selected_key, self.get_current_slider_params())
        self.update_preset_highlights()
        self.param_timer.start()

    def meter_pid(self):
        """PID of the filter process that currently shapes the selected target."""
        profile = PROFILE_STANDARD
        if self.selected_key is not STANDARD:
            entry = self.router.registry.get(self.selected_key)
            if entry and entry["params"] and entry["enabled"]:
                profile = profile_id_for(self.selected_key)
        return self.router.sinks.pid(profile)

    def update_meters(self):
        """Shows the plugin's live levels; bars go empty when no audio flows through the filter."""
        values = None
        try:
            pid = self.meter_pid() if self.is_active else None
            m = read_meter(pid) if pid else None
            if m:
                seq = m[0]
                if seq != self._meter_seq.get(pid):
                    self._meter_seq[pid] = seq
                    self._meter_idle = 0
                else:
                    self._meter_idle += 1
                if self._meter_idle < 4:
                    values = m[1:]
        except Exception:
            values = None  # a timer slot must never raise (PyQt aborts the app)
        self.meter_strip.set_values(values)

    def _store_program_values(self, key, values):
        """Gives the program its own setting (copied from Standard on first change)."""
        entry = self.router.registry.get(key)
        if entry is None:
            return
        first_time = entry["params"] is None
        base = entry["params"] or self.catalog.standard_params()
        params = full_params(base)
        params.update(values)
        params["bypass"] = 0.0
        if "lev_max_boost_db" in values and "leveler" not in values:
            # Same rule as `cinegain batch` for Standard: boost > 0 means leveler on.
            params["leveler"] = 1.0 if float(values["lev_max_boost_db"]) > 0.05 else 0.0
            if params["leveler"] and params["lev_max_cut_db"] <= 0.05:
                params["lev_max_cut_db"] = LEVELER_DEFAULT_CUT_DB
        self.router.registry.set_params(key, params, save=False)
        self._program_dirty = key
        if first_time:
            self.update_edit_header()

    def flush_pending_params(self):
        if self._program_dirty:
            key, self._program_dirty = self._program_dirty, None
            self.router.registry.save()
            if not self.router.push_program_params(key):
                # No own sink yet (first change): route now instead of on the next heartbeat.
                self.refresh_apps()
        if not self.pending_params:
            return
        to_send = dict(self.pending_params)
        self.pending_params.clear()
        run_cinegain("batch", json.dumps(to_send), "--preset-id", "custom")

    def _set_sliders_silently(self, params_dict):
        for k, v in params_dict.items():
            short_k = k.replace("cinegain:", "")
            if short_k in self.sliders:
                info = self.sliders[short_k]
                s = info["slider"]
                vl = info["val_lbl"]
                u = info["unit"]
                s.blockSignals(True)
                s.setValue(int(round(float(v) * 10)))
                vl.setText(self._format_val(short_k, float(v), u))
                s.blockSignals(False)

    def apply_factory_preset(self, preset_name):
        """Loads a factory preset into whatever is selected in the sidebar."""
        if self.selected_key is STANDARD:
            self.apply_standard_preset(preset_name)
            return
        params = self.catalog.params(preset_name)
        if params:
            self._load_into_program(self.selected_key, params)

    def apply_standard_preset(self, preset_name):
        """Loads a factory preset into Standard (used by the tray, which has no selection)."""
        self.pending_params.clear()
        run_cinegain("preset", preset_name)
        if self.selected_key is STANDARD:
            f_params = self.factory_presets.get(preset_name, {}).get("params")
            if f_params:
                self._set_sliders_silently(f_params)
            self.on_heartbeat(sync_sliders=True)
        self.update_preset_highlights()

    def _load_into_program(self, key, params):
        self._set_sliders_silently(params)
        self._store_program_values(key, params)
        self.flush_pending_params()
        self.update_preset_highlights()

    def on_user_preset_clicked(self, slot_id):
        """
        User slots are templates. An empty slot stores the current values; a filled
        slot loads its values into whatever is selected in the sidebar.
        """
        slot = self.user_presets.get(slot_id)
        if not slot:
            return
        if not slot.get("params"):
            self._overwrite_user_slot(slot_id)
            return
        saved_p = dict(slot["params"])
        if self.selected_key is STANDARD:
            self.pending_params.clear()
            self._set_sliders_silently(saved_p)
            run_cinegain("batch", json.dumps(saved_p), "--preset-id", slot_id)
        else:
            self._load_into_program(self.selected_key, full_params(saved_p))
        self.update_preset_highlights()

    def show_user_preset_menu(self, slot_id, button, pos):
        slot = self.user_presets.get(slot_id)
        if not slot:
            return
        menu = QMenu(self)
        act_save = QAction(t("menu_save_here"), self)
        act_rename = QAction(t("menu_rename"), self)
        act_clear = QAction(t("menu_clear"), self)

        act_save.triggered.connect(lambda: self._overwrite_user_slot(slot_id))
        act_rename.triggered.connect(lambda: self._rename_user_slot(slot_id))
        act_clear.triggered.connect(lambda: self._clear_user_slot(slot_id))

        menu.addAction(act_save)
        menu.addAction(act_rename)
        if slot.get("params"):
            menu.addSeparator()
            menu.addAction(act_clear)

        menu.exec(button.mapToGlobal(pos))

    def _overwrite_user_slot(self, slot_id):
        self.user_presets[slot_id]["params"] = self.get_current_slider_params()
        self._save_user_presets()
        self._refresh_user_preset_buttons_text()
        self.update_preset_highlights()

    def _rename_user_slot(self, slot_id):
        old_name = self.user_presets[slot_id].get("name", slot_id)
        new_name, ok = QInputDialog.getText(
            self,
            t("rename_title"),
            t("rename_prompt"),
            text=old_name,
        )
        if ok and new_name.strip():
            self.user_presets[slot_id]["name"] = new_name.strip()[:22]
            self._save_user_presets()
            self._refresh_user_preset_buttons_text()
            self.update_preset_highlights()

    def _clear_user_slot(self, slot_id):
        idx_num = slot_id.split("_")[-1]
        self.user_presets[slot_id]["name"] = f"User {idx_num}"
        self.user_presets[slot_id]["params"] = None
        self._save_user_presets()
        self._refresh_user_preset_buttons_text()
        self.update_preset_highlights()

    def toggle_power(self):
        if self.is_active:
            self.pending_params.clear()
            self.router.release_all()
            run_cinegain("stop")
        else:
            run_cinegain("start")
        self.on_heartbeat(sync_sliders=True)

    def on_heartbeat(self, sync_sliders=False):
        out, code = run_cinegain("status", "--json")
        params = {}
        preset_id = None
        try:
            st = json.loads(out)
            if not isinstance(st, dict) or not isinstance(st.get("active"), bool):
                raise ValueError("Invalid status")
            if code != 0 and (code != 1 or st["active"]):
                raise ValueError("Status command failed")
            self.is_active = st["active"]
            params = st.get("params", {})
            preset_id = st.get("preset")
        except (ValueError, TypeError):
            self.lbl_status.setText(t("unknown_status"))
            self.lbl_status.setStyleSheet("color: #FFD21F;")
            self.btn_power.setEnabled(False)
            self.tray_toggle_action.setEnabled(False)
            return

        self.btn_power.setEnabled(True)
        self.tray_toggle_action.setEnabled(True)

        if self.is_active:
            self.lbl_status.setText(t("status_active"))
            self.lbl_status.setStyleSheet(
                'font-family: "Fira Sans Condensed", sans-serif; font-size: 13px; '
                "font-weight: 800; color: #6FBF8B; background-color: #1F1712; "
                "border: 1px solid #6FBF8B; border-radius: 6px; padding: 6px 12px;"
            )
            self.btn_power.setText(t("btn_power_off"))
            self.btn_power.setObjectName("btn_power_off")
            if hasattr(self, "tray_toggle_action"):
                self.tray_toggle_action.setText(t("btn_power_off"))
        else:
            self.lbl_status.setText(t("status_inactive"))
            self.lbl_status.setStyleSheet(
                'font-family: "Fira Sans Condensed", sans-serif; font-size: 13px; '
                "font-weight: 800; color: #E2C89B; background-color: #1F1712; "
                "border: 1px solid #A86B4A; border-radius: 6px; padding: 6px 12px;"
            )
            self.btn_power.setText(t("btn_power_on"))
            self.btn_power.setObjectName("btn_power_on")
            if hasattr(self, "tray_toggle_action"):
                self.tray_toggle_action.setText(t("btn_power_on"))

        self.btn_power.style().unpolish(self.btn_power)
        self.btn_power.style().polish(self.btn_power)

        if sync_sliders and params and self.selected_key is STANDARD:
            for pname, info in self.sliders.items():
                if pname in params and not info["slider"].isSliderDown():
                    val = float(params[pname])
                    s = info["slider"]
                    vl = info["val_lbl"]
                    u = info["unit"]
                    s.blockSignals(True)
                    s.setValue(int(round(val * 10)))
                    vl.setText(self._format_val(pname, val, u))
                    s.blockSignals(False)

        self.update_preset_highlights()

        self.refresh_apps()

    def refresh_apps(self):
        """Scans streams, enforces per-program routing and updates the sidebar."""
        views = self.router.tick(active=self.is_active)
        self.sidebar.update_views(views, self.is_active)
        if not self._initial_selection_done:
            # Open on the program that is playing right now, if it is the only one.
            self._initial_selection_done = True
            playing = [v for v in views if v.playing]
            if len(playing) == 1:
                self.sidebar.select(playing[0].key)
        self.update_edit_header()

    # -- editing target ---------------------------------------------------------
    def _subtitle(self):
        return f"{t('app_subtitle')} · {t('kbd_program')}"

    def _build_edit_header(self):
        frame = QFrame()
        frame.setObjectName("edit_header")
        frame.setStyleSheet(
            "QFrame#edit_header { background-color: #1F1712; border: 1px solid #6E1516; border-radius: 8px; }"
            "QLabel { background: transparent; }"
            "QPushButton#btn_back_standard { background-color: #14100D; color: #FFD21F; "
            "border: 1px solid #A86B4A; border-radius: 6px; padding: 6px 14px; font-weight: 700; }"
            "QPushButton#btn_back_standard:hover { background-color: #8C1014; border-color: #FFD21F; }"
        )
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(16, 10, 12, 10)
        lay.setSpacing(12)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.lbl_edit_target = QLabel()
        self.lbl_edit_target.setStyleSheet(
            'font-family: "Fira Sans Condensed", sans-serif; font-size: 21px; font-weight: 800; '
            "letter-spacing: 0.6px; color: #FFD21F;"
        )
        self.lbl_edit_state = QLabel()
        self.lbl_edit_state.setStyleSheet("font-size: 12px; color: #E2C89B;")
        self.lbl_edit_state.setWordWrap(True)
        text.addWidget(self.lbl_edit_target)
        text.addWidget(self.lbl_edit_state)
        self.btn_back_standard = QPushButton()
        self.btn_back_standard.setObjectName("btn_back_standard")
        self.btn_back_standard.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back_standard.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_back_standard.clicked.connect(self.back_to_standard)
        lay.addLayout(text, 1)
        lay.addWidget(self.btn_back_standard, 0, Qt.AlignmentFlag.AlignVCenter)
        return frame

    def update_edit_header(self):
        if not hasattr(self, "lbl_edit_target"):
            return
        key = self.selected_key
        entry = self.router.registry.get(key) if key is not STANDARD else None
        if entry is None:
            self.lbl_edit_target.setText(amp(t("prog_standard")).upper())
            self.lbl_edit_state.setText(t("edit_state_standard"))
            self.btn_back_standard.hide()
            return
        self.lbl_edit_target.setText(entry["label"].upper())
        if not entry["enabled"]:
            state = t("edit_state_off")
        elif entry["params"]:
            state = t("edit_state_own")
        else:
            state = t("edit_state_follows")
        self.lbl_edit_state.setText(state)
        self.btn_back_standard.setText(t("btn_back_standard"))
        self.btn_back_standard.setToolTip(t("btn_back_standard_tip"))
        self.btn_back_standard.setVisible(entry["params"] is not None)

    def target_values(self):
        """Parameter values of the current editing target."""
        if self.selected_key is not STANDARD:
            entry = self.router.registry.get(self.selected_key)
            if entry and entry["params"]:
                return full_params(entry["params"])
        return self.catalog.standard_params()

    def on_selection_changed(self, key):
        # Commit pending edits to the previous target before switching.
        self.param_timer.stop()
        self.flush_pending_params()
        self.selected_key = key
        self._set_sliders_silently(self.target_values())
        self.update_edit_header()
        self.update_preset_highlights()

    def back_to_standard(self):
        if self.selected_key is STANDARD:
            return
        self._program_dirty = None
        self.router.registry.set_params(self.selected_key, None)
        self._set_sliders_silently(self.catalog.standard_params())
        self.refresh_apps()
        self.update_preset_highlights()

    def reset_everything(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(t("reset_confirm_title"))
        box.setText(t("reset_confirm_title"))
        box.setInformativeText(t("reset_confirm_text"))
        box.setStandardButtons(QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Reset:
            return
        self.timer.stop()
        self.param_timer.stop()
        self.pending_params.clear()
        out, code = run_cinegain("reset", "--yes", "--json")
        try:
            report = json.loads(out.splitlines()[-1]) if out else {}
        except (ValueError, IndexError):
            report = {}
        if code != 0:
            QMessageBox.critical(self, t("reset_confirm_title"), t("reset_failed"))
            self.timer.start(1500)
            return
        QMessageBox.information(
            self, t("reset_done_title"), t("reset_done_text", backup=report.get("backup") or "—")
        )
        # Start over with a fresh process so every widget, language and preset is pristine.
        if hasattr(self, "tray"):
            self.tray.hide()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def init_tray(self):
        self.tray = QSystemTrayIcon(self)
        if os.path.exists(ICON_PATH):
            self.tray.setIcon(QIcon(ICON_PATH))
        else:
            self.tray.setIcon(
                self.style().standardIcon(self.style().StandardPixmap.SP_MediaVolume)
            )

        self.tray.setToolTip(t("app_title"))

        menu = QMenu()
        self.tray_open_action = QAction(t("tray_open"), self)
        self.tray_open_action.triggered.connect(self.show_and_raise)
        menu.addAction(self.tray_open_action)

        self.tray_toggle_action = QAction(t("tray_toggle"), self)
        self.tray_toggle_action.triggered.connect(self.toggle_power)
        menu.addAction(self.tray_toggle_action)

        menu.addSeparator()

        for pid, label in [
            ("boost_10db", "+10 dB Boost"),
            ("boost_20db", "+20 dB Boost"),
            ("dialog_boost", t("preset_dialog_boost")),
            ("night_mode", t("preset_night_mode")),
            ("max_loudness", t("preset_max_loudness")),
            ("clean_linear", t("preset_clean_linear")),
        ]:
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, p=pid: self.apply_standard_preset(p))
            menu.addAction(act)

        menu.addSeparator()
        self.tray_quit_action = QAction(t("tray_quit"), self)
        self.tray_quit_action.triggered.connect(self.quit_app)
        menu.addAction(self.tray_quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

    def on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_and_raise()

    def show_and_raise(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.set_focused_slider(self.focused_slider_idx, set_qt_focus=True)

    def closeEvent(self, event):
        if hasattr(self, "tray") and self.tray.isVisible():
            self.hide()
            event.ignore()
        else:
            event.accept()

    def quit_app(self):
        self.pending_params.clear()
        self.router.release_all()
        run_cinegain("stop")
        if hasattr(self, "tray"):
            self.tray.hide()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Cinegain")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(CI_STYLESHEET)

    win = CinegainMainWindow(Router())
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
