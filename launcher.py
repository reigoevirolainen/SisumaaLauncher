# Sisumaa Client ver 1.0 (PRO v8) — single-file launcher.py
#
# macOS .app:
#   You MUST build on macOS (you cannot convert a Windows .exe into a real macOS .app).
#   On macOS:
#     python3 -m pip install -U pyinstaller PySide6 PySide6-WebEngine msal
#     pyinstaller --windowed --onefile --name "SisumaaClient" launcher.py

import sys, os, json, subprocess, base64, shutil, struct, socket, threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PySide6.QtCore import Qt, QUrl, QSize, QTimer, QPropertyAnimation, QEasingCurve, QObject, Signal
from PySide6.QtGui import QPixmap, QFontDatabase, QFont, QDesktopServices, QColor, QMovie
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFileDialog, QMessageBox, QFrame, QComboBox, QCheckBox, QSlider, QLineEdit
)

# Optional WebEngine
WEBENGINE_OK = True
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:
    WEBENGINE_OK = False
    QWebEngineView = None

# Optional MSAL (for Prism-style Device Login UI + token)
MSAL_OK = True
try:
    import msal
except Exception:
    MSAL_OK = False
    msal = None

APP_NAME = "Sisumaa Client ver 1.0"

# Azure app (your client id)
CLIENT_ID = "5379cfa8-fdb1-41bb-9a46-bdeff6ba1f4f"

# Assets (relative, next to launcher)
ASSET_BG = "background.bmp"
ASSET_LOGO = "Selgrootu_must_mehike_istub_1.bmp"
ASSET_FONT = "KOMIKAX_.ttf"  # user font
ASSET_HOME_GIF = "Download_1.gif"

# Installers (relative, next to launcher)
FABRIC_JAR = "fabric-installer-1.1.1.jar"
TLAUNCHER_EXE = "TLauncher-Installer-1.9.5.5.exe"
STARTERCORE_JAR = "starter-core-1.266-v2.jar"
SISUMAA_INSTALLER = "Sisumaa2SMP-Installer.exe"

DISCORD_INVITE = "https://discord.gg/sWDjrm6y5h"
MODRINTH_DISCOVER = "https://modrinth.com/discover/mods"

SERVER_NAME = "Sisumaa 2 SMP"
SERVER_HOST = "82.141.114.148"
SERVER_PORT = 28585
SERVER_ADDR = f"{SERVER_HOST}:{SERVER_PORT}"
BUILD_INFO = "Sisumaa build - ver 1.0"
SERVER_MAP = "http://82.141.114.148:28586/"

CONFIG_FILE = "client_config.json"
TOKEN_CACHE_FILE = "msal_token_cache.json"


# ------------------------- Paths / config -------------------------

def exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def rel_path(name: str) -> Path:
    # Prefer next to exe, fallback to script dir
    p = exe_dir() / name
    if p.exists():
        return p
    return Path(__file__).resolve().parent / name

def load_config() -> dict:
    p = exe_dir() / CONFIG_FILE
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception:
            return {}
    return {}

def save_config(cfg: dict) -> None:
    p = exe_dir() / CONFIG_FILE
    try:
        p.write_text(json.dumps(cfg, indent=2), "utf-8")
    except Exception:
        pass

def try_open_url(url: str):
    QDesktopServices.openUrl(QUrl(url))

def minecraft_dir() -> Path:
    # Windows default
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / ".minecraft"
    # fallback
    return Path.home() / ".minecraft"

def mods_dir() -> Path:
    d = minecraft_dir() / "mods"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _http_json(url: str, timeout=8) -> dict:
    req = Request(url, headers={"User-Agent": "SisumaaClient/1.0"})
    with urlopen(req, timeout=timeout) as r:
        data = r.read()
    return json.loads(data.decode("utf-8"))

def _http_bytes(url: str, timeout=12) -> bytes:
    req = Request(url, headers={"User-Agent": "SisumaaClient/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


# ------------------------- Running installers -------------------------

def run_file(path: Path) -> bool:
    try:
        if path.suffix.lower() == ".jar":
            java = shutil.which("javaw") or shutil.which("java")
            if not java:
                QMessageBox.critical(None, "Java missing",
                                     "Java was not found.\nInstall Java (JRE/JDK) to run .jar installers.")
                return False
            subprocess.Popen([java, "-jar", str(path)], cwd=str(path.parent))
            return True
        else:
            subprocess.Popen([str(path)], cwd=str(path.parent))
            return True
    except Exception as e:
        QMessageBox.critical(None, "Error", f"Could not run:\n{path}\n\n{e}")
        return False


# ------------------------- Minecraft status ping (1.7+) -------------------------
# Minimal implementation (no external libs). Shows online/max + sample list (if server provides it).

def _pack_varint(value: int) -> bytes:
    out = b""
    v = value & 0xFFFFFFFF
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out += bytes([b | 0x80])
        else:
            out += bytes([b])
            break
    return out

def _read_varint(sock: socket.socket) -> int:
    num_read = 0
    result = 0
    while True:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("EOF")
        value = byte[0] & 0x7F
        result |= value << (7 * num_read)
        num_read += 1
        if num_read > 5:
            raise ValueError("VarInt too big")
        if not (byte[0] & 0x80):
            break
    return result

def mc_status(host: str, port: int, timeout=2.5) -> dict:
    """
    Returns dict:
      {"ok": True, "online": int, "max": int, "motd": str, "sample": [names]}
    or {"ok": False, "error": "..."}
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.settimeout(timeout)

        # Handshake packet
        protocol_version = 762  # 1.20.4-ish; server will still respond for status generally
        host_bytes = host.encode("utf-8")
        data = b"".join([
            _pack_varint(0x00),
            _pack_varint(protocol_version),
            _pack_varint(len(host_bytes)),
            host_bytes,
            struct.pack(">H", port),
            _pack_varint(1)  # next state: status
        ])
        packet = _pack_varint(len(data)) + data
        s.sendall(packet)

        # Status request packet
        req = _pack_varint(1) + _pack_varint(0x00)
        s.sendall(req)

        # Read response
        _ = _read_varint(s)              # length
        pid = _read_varint(s)            # packet id
        if pid != 0x00:
            raise ValueError(f"Unexpected packet id {pid}")
        str_len = _read_varint(s)
        raw = b""
        while len(raw) < str_len:
            chunk = s.recv(str_len - len(raw))
            if not chunk:
                break
            raw += chunk
        s.close()

        j = json.loads(raw.decode("utf-8", "replace"))
        players = j.get("players", {}) or {}
        online = int(players.get("online", 0))
        maxp = int(players.get("max", 0))
        sample = []
        for item in (players.get("sample") or []):
            name = item.get("name")
            if name:
                sample.append(name)

        # MOTD can be component / string
        desc = j.get("description", "")
        if isinstance(desc, dict):
            motd = desc.get("text", "")
        else:
            motd = str(desc)

        return {"ok": True, "online": online, "max": maxp, "motd": motd, "sample": sample}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------- Minecraft launcher detection -------------------------

def detect_minecraft_launchers() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ
    programfiles = Path(env.get("ProgramFiles", r"C:\Program Files"))
    programfilesx86 = Path(env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))

    # Legacy launcher locations
    candidates += [
        programfilesx86 / "Minecraft Launcher" / "MinecraftLauncher.exe",
        programfiles / "Minecraft Launcher" / "MinecraftLauncher.exe",
        programfilesx86 / "Minecraft Launcher" / "Minecraft.exe",
        programfiles / "Minecraft Launcher" / "Minecraft.exe",
    ]

    # XboxGames common roots
    for root in [Path("C:/XboxGames"), Path("D:/XboxGames"), Path("E:/XboxGames")]:
        candidates += [
            root / "Minecraft Launcher_1" / "Content" / "Minecraft.exe",
            root / "Minecraft Launcher" / "Content" / "Minecraft.exe",
        ]

    out = [p for p in candidates if p.exists()]
    seen = set()
    uniq = []
    for p in out:
        k = str(p).lower()
        if k not in seen:
            uniq.append(p)
            seen.add(k)
    return uniq


# ------------------------- UI components -------------------------

class NavButton(QPushButton):
    def __init__(self, label: str, badge_color: str):
        super().__init__(label)
        self.badge_color = badge_color
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(46)
        self.setObjectName("navBtn")
        self._active = False

        self.anim = QPropertyAnimation(self, b"minimumWidth")
        self.anim.setDuration(140)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

    def set_active(self, active: bool):
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, e):
        self.anim.stop()
        self.anim.setStartValue(self.minimumWidth())
        self.anim.setEndValue(260)
        self.anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.anim.stop()
        self.anim.setStartValue(self.minimumWidth())
        self.anim.setEndValue(240)
        self.anim.start()
        super().leaveEvent(e)

class ColorButton(QPushButton):
    def __init__(self, text: str, obj: str):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(44)
        self.setObjectName(obj)


class Card(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        lay.addWidget(t)
        self.body = QVBoxLayout()
        self.body.setSpacing(12)
        lay.addLayout(self.body)


class _AuthWorker(QObject):
    finished = Signal(dict)
    progress = Signal(str)

    def __init__(self, client_id: str):
        super().__init__()
        self.client_id = client_id

    def run_device_flow(self):
        if not MSAL_OK:
            self.finished.emit({"ok": False, "error": "msal_missing"})
            return
        try:
            cache = msal.SerializableTokenCache()
            cache_path = exe_dir() / TOKEN_CACHE_FILE
            if cache_path.exists():
                cache.deserialize(cache_path.read_text("utf-8"))

            app = msal.PublicClientApplication(
                self.client_id,
                authority="https://login.microsoftonline.com/consumers",
                token_cache=cache
            )

            flow = app.initiate_device_flow(
                scopes=["XboxLive.signin"]
            )

            if "user_code" not in flow:
                self.finished.emit({"ok": False, "error": str(flow)})
                return

            self.progress.emit(flow["user_code"])
            try_open_url("https://www.microsoft.com/link")

            result = app.acquire_token_by_device_flow(flow)

            if cache.has_state_changed:
                cache_path.write_text(cache.serialize(), "utf-8")

            if "access_token" in result:
                self.finished.emit({"ok": True, "result": result})
            else:
                self.finished.emit({"ok": False, "error": result})

        except Exception as e:
            self.finished.emit({"ok": False, "error": str(e)})



# ------------------------- Main window -------------------------

class Launcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1200, 760)
        self.cfg = load_config()

        self._load_font()
        self._build_ui()
        self._apply_styles()

        self.switch_tab("Home")
        self._start_status_updates()

        # if we have previously applied skin locally, show it immediately in viewer
        self._try_load_applied_skin_on_start()

    # ----- font/background -----

    def _load_font(self):
        f = rel_path(ASSET_FONT)
        if f.exists():
            fid = QFontDatabase.addApplicationFont(str(f))
            fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
            family = fams[0] if fams else "Segoe UI"
            self.setFont(QFont(family, 11))
        else:
            self.setFont(QFont("Segoe UI", 10))

    def _refresh_background(self):
        bg_path = rel_path(ASSET_BG)
        if bg_path.exists():
            pix = QPixmap(str(bg_path))
            self.bg.setPixmap(pix.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            self.bg.setPixmap(QPixmap())
        self.bg.setGeometry(0, 0, self.width(), self.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._refresh_background()

    # ----- UI -----

    def _build_ui(self):
        self.root = QWidget()
        self.setCentralWidget(self.root)

        self.bg = QLabel(self.root)
        self.bg.lower()

        root_lay = QHBoxLayout(self.root)
        root_lay.setContentsMargins(18, 18, 18, 18)
        root_lay.setSpacing(14)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 14, 14, 14)
        side.setSpacing(10)

        # Logo (no extra box, part of client)
        self.logo = QLabel()
        self.logo.setObjectName("logo")
        self.logo.setAlignment(Qt.AlignCenter)
        lp = rel_path(ASSET_LOGO)
        if lp.exists():
            pix = QPixmap(str(lp))
            self.logo.setPixmap(pix.scaled(140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.logo.setText("Logo missing")
        side.addWidget(self.logo)

        self.btns: dict[str, NavButton] = {}
        def add_nav(key: str, title: str, color: str):
            b = NavButton(title, color)
            b.setMinimumWidth(240)
            b.clicked.connect(lambda _, k=key: self.switch_tab(k))
            self.btns[key] = b
            side.addWidget(b)

        add_nav("Home", "Home", "#55ffa4")
        add_nav("Play", "Play", "#55ffa4")
        add_nav("Skin", "Skin", "#ffbd40")
        add_nav("Settings", "Settings", "#8fb3ff")
        add_nav("Install", "Install", "#b8b8b8")
        add_nav("Discord", "Discord", "#8b5cff")
        add_nav("Mods", "Mods", "#ff6bd6")

        # keep your nav buttons, add stretch, then put Login at the bottom-left as requested
        side.addStretch(1)
        add_nav("Login", "Login", "#55ffa4")

        root_lay.addWidget(self.sidebar, 0)

        # Content stack
        self.stack = QStackedWidget()
        self.stack.setObjectName("stack")
        root_lay.addWidget(self.stack, 1)

        self.pages = {
            "Home": self._page_home(),
            "Play": self._page_play(),
            "Skin": self._page_skin(),
            "Settings": self._page_settings(),
            "Install": self._page_install(),
            "Discord": self._page_discord(),
            "Mods": self._page_mods(),
            "Login": self._page_login(),
        }
        for k in ["Home","Play","Skin","Settings","Install","Discord","Mods","Login"]:
            self.stack.addWidget(self.pages[k])

        self._refresh_background()

    def _page_shell(self, title: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        header = QLabel(title)
        header.setObjectName("pageTitle")
        lay.addWidget(header)
        return w

    # ----- HOME -----

    def _page_home(self) -> QWidget:
        w = self._page_shell("Home")
        lay: QVBoxLayout = w.layout()

        card = Card("Welcome")
        # media
        media_row = QHBoxLayout()
        media_row.setSpacing(14)

        self.home_media = QLabel()
        self.home_media.setObjectName("homeMedia")
        self.home_media.setAlignment(Qt.AlignCenter)

        gif = rel_path(ASSET_HOME_GIF)
        if gif.exists():
            self.movie = QMovie(str(gif))
            self.movie.setCacheMode(QMovie.CacheAll)
            self.home_media.setMovie(self.movie)
            self.movie.start()
        else:
            self.home_media.setText("Download_1.gif not found")

        media_row.addStretch(1)
        media_row.addWidget(self.home_media, 0)
        media_row.addStretch(1)
        card.body.addLayout(media_row)

        # Status text
        self.status_title = QLabel("Server status: loading…")
        self.status_title.setObjectName("statusTitle")
        card.body.addWidget(self.status_title)

        self.status_players = QLabel("")
        self.status_players.setObjectName("muted")
        card.body.addWidget(self.status_players)

        # Buttons row (separate buttons as requested)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_server = ColorButton(f"Server: {SERVER_NAME}", "btnServer")
        self.btn_server.clicked.connect(lambda: self.copy_ip(show_msg=True))
        btn_row.addWidget(self.btn_server)

        self.btn_map = ColorButton("Open BlueMap", "btnMap")
        self.btn_map.clicked.connect(lambda: try_open_url(SERVER_MAP))
        btn_row.addWidget(self.btn_map)

        self.btn_join = ColorButton("Join Discord", "btnDiscord")
        self.btn_join.clicked.connect(lambda: try_open_url(DISCORD_INVITE))
        btn_row.addWidget(self.btn_join)

        card.body.addLayout(btn_row)

        hint = QLabel("Tip: Click the Server button to copy IP. Then open Minecraft and add/join the server.")
        hint.setObjectName("muted")
        card.body.addWidget(hint)

        lay.addWidget(card)
        lay.addStretch(1)
        return w

    # ----- PLAY -----

    def _page_play(self) -> QWidget:
        w = self._page_shell("Play")
        lay: QVBoxLayout = w.layout()

        card = Card("Play options")

        top = QLabel(f"{SERVER_NAME} — <b>{SERVER_ADDR}</b>   •   {BUILD_INFO}")
        top.setObjectName("bigText")
        card.body.addWidget(top)

        self.play_status = QLabel("Server status: loading…")
        self.play_status.setObjectName("muted")
        card.body.addWidget(self.play_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        b1 = ColorButton("Copy IP", "btnGrey")
        b1.clicked.connect(lambda: self.copy_ip(show_msg=True))
        btn_row.addWidget(b1)

        b2 = ColorButton("Play Minecraft", "btnGreen")  # green as requested
        b2.clicked.connect(self.play_minecraft)
        btn_row.addWidget(b2)

        b3 = ColorButton("Open BlueMap", "btnMap")
        b3.clicked.connect(lambda: try_open_url(SERVER_MAP))
        btn_row.addWidget(b3)

        card.body.addLayout(btn_row)

        auto = QLabel("Auto-join: official launcher does not support guaranteed auto-join without managing the Java runtime.\n"
                      "This client focuses on safe actions: open launcher + copy IP.")
        auto.setObjectName("muted")
        card.body.addWidget(auto)

        lay.addWidget(card)
        lay.addStretch(1)
        return w

    # ----- SKIN -----

    def _page_skin(self) -> QWidget:
        w = self._page_shell("Skin")
        lay: QVBoxLayout = w.layout()

        card = Card("3D Skin Viewer (offline + online)")
        hint = QLabel("Offline: choose a real .png skin file • Online: enter username and load from Mojang • Drag to rotate • Scroll to zoom")
        hint.setObjectName("muted")
        card.body.addWidget(hint)

        # Online skin controls
        online_row = QHBoxLayout()
        online_row.setSpacing(10)
        self.online_name = QLineEdit()
        self.online_name.setObjectName("line")
        self.online_name.setPlaceholderText("Online username (e.g. Notch)")
        online_row.addWidget(self.online_name, 1)

        b_load = ColorButton("Load Online Skin", "btnGreen")
        b_load.clicked.connect(self.load_online_skin)
        online_row.addWidget(b_load, 0)

        b_apply = ColorButton("Apply (save locally)", "btnGrey")
        b_apply.clicked.connect(self.apply_online_skin_locally)
        online_row.addWidget(b_apply, 0)

        card.body.addLayout(online_row)

        # Centered path + button (offline choose)
        row = QHBoxLayout()
        row.setSpacing(10)

        self.skin_path = QLineEdit()
        self.skin_path.setReadOnly(True)
        self.skin_path.setPlaceholderText("No skin selected")
        self.skin_path.setObjectName("line")
        row.addWidget(self.skin_path, 1)

        choose = ColorButton("Choose Skin PNG", "btnYellow")
        choose.clicked.connect(self.choose_skin)
        row.addWidget(choose, 0)
        card.body.addLayout(row)

        # Viewer wrap (no white background)
        wrap = QFrame()
        wrap.setObjectName("viewerWrap")
        wrap_lay = QVBoxLayout(wrap)
        wrap_lay.setContentsMargins(0, 0, 0, 0)

        if WEBENGINE_OK:
            self.web = QWebEngineView()
            self.web.setObjectName("webview")
            self.web.setAttribute(Qt.WA_TranslucentBackground, True)
            self.web.page().setBackgroundColor(QColor(0, 0, 0, 0))
            self.web.setHtml(self._skin_html(None))
            wrap_lay.addWidget(self.web, 1)
        else:
            self.web = None
            msg = QLabel("3D viewer needs PySide6-WebEngine.\nInstall: python -m pip install PySide6-WebEngine")
            msg.setObjectName("muted")
            msg.setAlignment(Qt.AlignCenter)
            wrap_lay.addWidget(msg, 1)

        card.body.addWidget(wrap, 1)

        lay.addWidget(card, 1)
        return w

    # ----- SETTINGS -----

    def _page_settings(self) -> QWidget:
        w = self._page_shell("Settings")
        lay: QVBoxLayout = w.layout()

        card = Card("Client settings (saved locally)")

        # Username (offline)
        urow = QHBoxLayout()
        urow.setSpacing(10)
        ul = QLabel("Offline username")
        ul.setObjectName("muted")
        self.username = QLineEdit()
        self.username.setObjectName("line")
        self.username.setText(self.cfg.get("username", "Selgrootu"))
        urow.addWidget(ul, 0)
        urow.addWidget(self.username, 1)
        card.body.addLayout(urow)

        # Resolution
        rrow = QHBoxLayout()
        rrow.setSpacing(10)
        rl = QLabel("Resolution")
        rl.setObjectName("muted")
        self.res = QComboBox()
        self.res.setObjectName("combo")
        res_list = ["1920x1080","1600x900","1366x768","1280x720","2560x1440"]
        self.res.addItems(res_list)
        self.res.setCurrentText(self.cfg.get("resolution", "1920x1080"))
        rrow.addWidget(rl, 0)
        rrow.addWidget(self.res, 1)
        card.body.addLayout(rrow)

        # Fullscreen + close on launch
        self.fullscreen = QCheckBox("Fullscreen (for future game launch integration)")
        self.fullscreen.setChecked(bool(self.cfg.get("fullscreen", False)))
        self.fullscreen.setObjectName("check")
        card.body.addWidget(self.fullscreen)

        self.close_on_launch = QCheckBox("Close client when launching Minecraft")
        self.close_on_launch.setChecked(bool(self.cfg.get("close_on_launch", False)))
        self.close_on_launch.setObjectName("check")
        card.body.addWidget(self.close_on_launch)

        # RAM
        raml = QLabel("Allocated RAM (GB) (saved for future)")
        raml.setObjectName("muted")
        card.body.addWidget(raml)
        self.ram = QSlider(Qt.Horizontal)
        self.ram.setObjectName("slider")
        self.ram.setRange(2, 16)
        self.ram.setValue(int(self.cfg.get("ram_gb", 8)))
        self.ram_value = QLabel(f"{self.ram.value()} GB")
        self.ram_value.setObjectName("muted")
        self.ram.valueChanged.connect(lambda v: self.ram_value.setText(f"{v} GB"))
        card.body.addWidget(self.ram)
        card.body.addWidget(self.ram_value)

        # Apply
        apply_row = QHBoxLayout()
        apply_row.addStretch(1)
        apply_btn = ColorButton("Apply", "btnGreen")
        apply_btn.clicked.connect(self.apply_settings)
        apply_row.addWidget(apply_btn)
        card.body.addLayout(apply_row)

        # Auth info (honest)
        info = QLabel("Microsoft login (device flow): optional. If you want it to actually fetch tokens, install 'msal'.")
        info.setObjectName("muted")
        card.body.addWidget(info)

        lay.addWidget(card)
        lay.addStretch(1)
        return w

    # ----- INSTALL -----

    def _page_install(self) -> QWidget:
        w = self._page_shell("Install")
        lay: QVBoxLayout = w.layout()

        card = Card("Installers (place next to launcher)")
        btns = QVBoxLayout()
        btns.setSpacing(10)

        def mk(text, fname, obj="btnGrey"):
            b = ColorButton(text, obj)
            b.clicked.connect(lambda: self.install_file(fname))
            return b

        btns.addWidget(mk("Install Fabric Loader", FABRIC_JAR, "btnGrey"))
        btns.addWidget(mk("Install TLauncher (Windows)", TLAUNCHER_EXE, "btnGrey"))
        btns.addWidget(mk("Install TLauncher (Mac)", STARTERCORE_JAR, "btnGrey"))
        btns.addWidget(mk("Install Sisumaa Modpack", SISUMAA_INSTALLER, "btnGrey"))

        card.body.addLayout(btns)

        note = QLabel("If Fabric installer doesn't open: make sure Java is installed and 'java -version' works.")
        note.setObjectName("muted")
        card.body.addWidget(note)

        lay.addWidget(card)
        lay.addStretch(1)
        return w

    # ----- DISCORD -----

    def _page_discord(self) -> QWidget:
        w = self._page_shell("Discord")
        lay: QVBoxLayout = w.layout()

        card = Card("Sisumaa 2 SMP Discord")
        txt = QLabel("Updates • Events • Support • Community")
        txt.setObjectName("muted")
        card.body.addWidget(txt)

        btn = ColorButton("Open Discord Invite", "btnPurple")
        btn.clicked.connect(lambda: try_open_url(DISCORD_INVITE))
        card.body.addWidget(btn)

        lay.addWidget(card)
        lay.addStretch(1)
        return w

    # ----- MODS -----

    def _page_mods(self) -> QWidget:
        w = self._page_shell("Mods")
        lay: QVBoxLayout = w.layout()

        card = Card("Mod Finder (Modrinth) + Install local mods")

        # Install mods tab section (inside Mods page)
        row = QHBoxLayout()
        row.setSpacing(10)

        b_install = ColorButton("Install Mod (.jar) from PC", "btnGreen")
        b_install.clicked.connect(self.install_local_mod)
        row.addWidget(b_install, 0)

        b_open = ColorButton("Open Mods Folder", "btnGrey")
        b_open.clicked.connect(self.open_mods_folder)
        row.addWidget(b_open, 0)

        row.addStretch(1)
        card.body.addLayout(row)

        if WEBENGINE_OK:
            self.mods_web = QWebEngineView()
            self.mods_web.setObjectName("webview")
            self.mods_web.setUrl(QUrl(MODRINTH_DISCOVER))
            card.body.addWidget(self.mods_web, 1)
        else:
            msg = QLabel("Embedded Modrinth needs PySide6-WebEngine.\nOpen in browser instead.")
            msg.setObjectName("muted")
            card.body.addWidget(msg)
            btn = ColorButton("Open Modrinth in Browser", "btnPink")
            btn.clicked.connect(lambda: try_open_url(MODRINTH_DISCOVER))
            card.body.addWidget(btn)

        lay.addWidget(card, 1)
        return w

    # ----- LOGIN -----

    def _page_login(self) -> QWidget:
        w = self._page_shell("Login")
        lay: QVBoxLayout = w.layout()

        card = Card("Microsoft Device Login")
        info = QLabel(""
                      "")
        info.setObjectName("muted")
        card.body.addWidget(info)

        self.login_status = QLabel("Status: Not logged in")
        self.login_status.setObjectName("statusTitle")
        card.body.addWidget(self.login_status)

        # message box
        self.device_message = QLabel("")
        self.device_message.setWordWrap(True)
        self.device_message.setObjectName("muted")
        card.body.addWidget(self.device_message)

        self.code_label = QLabel("CODE: -")
        self.code_label.setStyleSheet("QLabel { color: #00ff55; font-family: Arial; font-size: 26px; font-weight: bold; }")
        card.body.addWidget(self.code_label)

        row = QHBoxLayout()
        row.setSpacing(10)

        b_start = ColorButton("Start Device Login", "btnGreen")
        b_start.clicked.connect(self.start_device_login)
        row.addWidget(b_start, 0)

        b_open = ColorButton("Open device login page", "btnGrey")
        b_open.clicked.connect(lambda: try_open_url("https://www.microsoft.com/link"))
        row.addWidget(b_open, 0)

        row.addStretch(1)
        card.body.addLayout(row)

        lay.addWidget(card)
        lay.addStretch(1)
        return w

    # ----- actions -----

    def switch_tab(self, key: str):
        for k, b in self.btns.items():
            b.set_active(k == key)
        self.stack.setCurrentWidget(self.pages[key])

    def copy_ip(self, show_msg=False):
        QApplication.clipboard().setText(SERVER_ADDR)
        if show_msg:
            QMessageBox.information(self, "Copied", f"Copied:\n{SERVER_ADDR}")

    def install_file(self, filename: str):
        p = rel_path(filename)
        if not p.exists():
            QMessageBox.critical(self, "Missing file",
                                 f"Not found:\n{p}\n\nPlace it next to launcher.py / launcher.exe")
            return
        run_file(p)

    def play_minecraft(self):
        # Try direct exe paths
        found = detect_minecraft_launchers()
        if found:
            try:
                subprocess.Popen([str(found[0])], cwd=str(found[0].parent))
                if self.cfg.get("close_on_launch"):
                    self.close()
                return
            except Exception:
                pass

        # Try protocol
        try:
            os.startfile("minecraft-launcher:")
            if self.cfg.get("close_on_launch"):
                self.close()
            return
        except Exception:
            pass

        QMessageBox.critical(self, "Minecraft not found",
                             "Minecraft Launcher was not found.\n\nOpening Microsoft Store search…")
        try_open_url("https://www.microsoft.com/store/search?query=Minecraft%20Launcher")

    def apply_settings(self):
        self.cfg["username"] = self.username.text().strip() or "Player"
        self.cfg["resolution"] = self.res.currentText()
        self.cfg["fullscreen"] = bool(self.fullscreen.isChecked())
        self.cfg["close_on_launch"] = bool(self.close_on_launch.isChecked())
        self.cfg["ram_gb"] = int(self.ram.value())
        save_config(self.cfg)
        QMessageBox.information(self, "Saved", "Settings saved.")

    # ----- mods install -----

    def open_mods_folder(self):
        d = mods_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(d))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def install_local_mod(self):
        file, _ = QFileDialog.getOpenFileName(self, "Choose mod file", "", "Mod files (*.jar)")
        if not file:
            return
        try:
            target = mods_dir() / Path(file).name
            shutil.copy(file, target)
            QMessageBox.information(self, "Mod installed", f"Installed:\n{target}")
        except Exception as e:
            QMessageBox.critical(self, "Install failed", str(e))

    # ----- server status updates -----

    def _start_status_updates(self):
        self._update_status()
        self.timer = QTimer(self)
        self.timer.setInterval(10_000)  # 10s
        self.timer.timeout.connect(self._update_status)
        self.timer.start()

    def _update_status(self):
        st = mc_status(SERVER_HOST, SERVER_PORT)
        if st.get("ok"):
            online = st["online"]
            maxp = st["max"]
            sample = st.get("sample") or []
            self.status_title.setText(f"Server status: ONLINE  •  {online}/{maxp} players")
            self.play_status.setText(f"ONLINE  •  {online}/{maxp} players")
            if sample:
                self.status_players.setText("Online now: " + ", ".join(sample[:12]))
            else:
                self.status_players.setText("Online list: (server didn't provide names)")
        else:
            err = st.get("error", "unknown")
            self.status_title.setText("Server status: OFFLINE")
            self.play_status.setText("OFFLINE  •  " + err)
            self.status_players.setText("")

    # ----- skin viewer -----

    def _skin_html(self, skin_b64: str | None) -> str:
        skin = "null" if not skin_b64 else f'"data:image/png;base64,{skin_b64}"'
        return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <script src="https://unpkg.com/skinview3d/bundles/skinview3d.bundle.js"></script>
  <style>
    html, body {{
      margin:0; padding:0; width:100%; height:100%;
      background: transparent !important;
      overflow:hidden;
    }}
    #wrap {{
      width:100%; height:100%;
      display:flex; align-items:center; justify-content:center;
      background: transparent !important;
    }}
    canvas {{ background: transparent !important; }}
  </style>
</head>
<body>
  <div id="wrap"><canvas id="skin"></canvas></div>
  <script>
    const canvas = document.getElementById("skin");
    function size() {{
      const w = Math.max(640, Math.floor(window.innerWidth * 0.90));
      const h = Math.max(560, Math.floor(window.innerHeight * 0.86));
      canvas.width = w; canvas.height = h;
      return {{w, h}};
    }}
    const s = size();
    const viewer = new skinview3d.SkinViewer({{
      canvas,
      width: s.w,
      height: s.h,
      skin: {skin}
    }});
    viewer.controls.enableRotate = true;
    viewer.controls.enableZoom = true;
    viewer.controls.rotateSpeed = 1.0;
    viewer.camera.position.set(20, 12, 35);
    viewer.fov = 45;

    window.addEventListener("resize", () => {{
      const ns = size();
      viewer.setSize(ns.w, ns.h);
    }});
  </script>
</body>
</html>
"""

    def _try_load_applied_skin_on_start(self):
        try:
            p = self.cfg.get("applied_skin_path")
            if not p:
                return
            fp = Path(p)
            if not fp.exists():
                return
            if not (WEBENGINE_OK and getattr(self, "web", None)):
                return
            with open(fp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.web.setHtml(self._skin_html(b64))
            self.skin_path.setText(str(fp))
        except Exception:
            pass

    def choose_skin(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose Skin PNG", "", "PNG Files (*.png)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            QMessageBox.critical(self, "Not a PNG", "Please select a real .png skin file.")
            return
        self.skin_path.setText(path)

        if not (WEBENGINE_OK and self.web):
            return

        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.web.setHtml(self._skin_html(b64))
        except Exception as e:
            QMessageBox.critical(self, "Skin error", str(e))

    def load_online_skin(self):
        name = (self.online_name.text() or "").strip()
        if not name:
            QMessageBox.information(self, "Missing", "Enter a username first.")
            return
        try:
            prof = _http_json(f"https://api.mojang.com/users/profiles/minecraft/{name}")
            uuid = prof.get("id")
            if not uuid:
                QMessageBox.critical(self, "Not found", "Username not found on Mojang.")
                return
            # sessionserver profile with textures
            sprof = _http_json(f"https://sessionserver.mojang.com/session/minecraft/profile/{uuid}")
            props = sprof.get("properties") or []
            tex = None
            for p in props:
                if p.get("name") == "textures":
                    tex = p.get("value")
                    break
            if not tex:
                QMessageBox.critical(self, "No textures", "No textures found for this user.")
                return

            data = json.loads(base64.b64decode(tex + "==").decode("utf-8", "replace"))
            skin_url = (((data.get("textures") or {}).get("SKIN") or {}).get("url"))
            if not skin_url:
                QMessageBox.critical(self, "No skin", "No skin URL found.")
                return

            png = _http_bytes(skin_url)
            b64 = base64.b64encode(png).decode("ascii")
            self.cfg["last_online_skin_username"] = name
            self.cfg["last_online_skin_png_b64"] = b64  # stored for Apply
            save_config(self.cfg)

            self.skin_path.setText(f"Online: {name}")
            if WEBENGINE_OK and self.web:
                self.web.setHtml(self._skin_html(b64))
        except HTTPError as e:
            QMessageBox.critical(self, "HTTP error", str(e))
        except URLError as e:
            QMessageBox.critical(self, "Network error", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def apply_online_skin_locally(self):
        # Save last loaded online skin to launcher folder and remember it in config
        b64 = self.cfg.get("last_online_skin_png_b64")
        name = self.cfg.get("last_online_skin_username") or "skin"
        if not b64:
            QMessageBox.information(self, "Missing", "Load an online skin first.")
            return
        try:
            skins_folder = exe_dir() / "skins"
            skins_folder.mkdir(parents=True, exist_ok=True)
            out = skins_folder / f"{name}.png"
            out.write_bytes(base64.b64decode(b64))
            self.cfg["applied_skin_path"] = str(out)
            save_config(self.cfg)
            QMessageBox.information(self, "Saved", f"Saved locally:\n{out}")
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    # ----- login -----

    def start_device_login(self):
        if not MSAL_OK:
            QMessageBox.information(self, "msal missing",
                                    "MSAL is not installed.\n\nInstall:\npython -m pip install msal")
            return

        self.login_status.setText("Status: Logging in…")
        self.device_message.setText("Starting device login…")

        self.auth_worker = _AuthWorker(CLIENT_ID)
        self.auth_worker.progress.connect(self._show_code)
        self.auth_worker.finished.connect(self._on_auth_finished)

        t = threading.Thread(target=self.auth_worker.run_device_flow, daemon=True)
        t.start()

    def _show_code(self, code: str):
        self.code_label.setText(f"CODE: {code}")
        self.device_message.setText("Go to https://www.microsoft.com/link and enter this code.")


    def _on_auth_finished(self, payload: dict):
        if payload.get("ok"):
            res = payload.get("result") or {}
            self.cfg["msal_logged_in"] = True
            self.cfg["msal_scopes"] = res.get("scope", "")
            # don't store access_token in config; cache file already persists it
            save_config(self.cfg)
            self.login_status.setText("Status: Logged in (token cached)")
            QMessageBox.information(self, "Login", "Logged in. Token cache saved next to launcher.")
        else:
            err = payload.get("error", "unknown")
            self.cfg["msal_logged_in"] = False
            save_config(self.cfg)
            self.login_status.setText("Status: Not logged in")
            if err == "msal_missing":
                QMessageBox.information(self, "msal missing", "Install:\npython -m pip install msal")
            else:
                QMessageBox.critical(self, "Login failed", str(err))

    # ----- styles -----

    def _apply_styles(self):
        # Not transparent buttons, more “client-like” look
        self.setStyleSheet("""
        QWidget { color: #EAF3F0; }

        #sidebar {
            background: rgba(12, 14, 16, 0.88);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 18px;
        }
        #logo { margin: 4px 0 10px 0; }

        QPushButton#navBtn {
            background: rgba(26, 30, 34, 1.0);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 14px;
            padding: 0 14px;
            font-weight: 700;
            text-align: left;
        }
        QPushButton#navBtn:hover {
            border: 1px solid rgba(85,255,164,0.55);
        }
        QPushButton#navBtn[active="true"] {
            background: rgba(18, 60, 42, 1.0);
            border: 1px solid rgba(85,255,164,0.85);
        }

        #stack {
            background: rgba(10, 12, 14, 0.76);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 18px;
            padding: 16px;
        }

        #pageTitle {
            font-size: 28px;
            font-weight: 900;
            padding: 4px 6px 10px 6px;
        }

        #card {
            background: rgba(0,0,0,0.62);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 16px;
        }
        #cardTitle { font-size: 16px; font-weight: 900; }
        #bigText { font-size: 16px; font-weight: 800; }
        #muted { color: rgba(234,243,240,0.80); }
        #statusTitle { font-size: 15px; font-weight: 800; }

        #homeMedia {
            min-width: 360px;
            max-width: 520px;
            min-height: 220px;
            max-height: 420px;
        }

        QLineEdit#line {
            background: rgba(26, 30, 34, 1.0);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 12px;
            padding: 10px 12px;
        }

        #viewerWrap {
            background: rgba(0,0,0,0.25);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 16px;
            min-height: 560px;
        }
        #webview { background: transparent; }

        QComboBox#combo {
            background: rgba(26, 30, 34, 1.0);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 12px;
            padding: 8px 10px;
        }
        QCheckBox#check { font-weight: 700; }

        QSlider#slider::groove:horizontal {
            height: 8px;
            background: rgba(255,255,255,0.12);
            border-radius: 4px;
        }
        QSlider#slider::handle:horizontal {
            width: 18px;
            margin: -6px 0;
            border-radius: 9px;
            background: rgba(85,255,164,1.0);
        }

        QPushButton#btnGreen {
            background: rgba(85,255,164,1.0);
            color: #08110D;
            border: 1px solid rgba(85,255,164,1.0);
            border-radius: 14px;
            padding: 12px 16px;
            font-weight: 900;
        }
        QPushButton#btnGreen:hover { background: rgba(85,255,164,0.92); }

        QPushButton#btnServer {
            background: rgba(85,255,164,1.0);
            color: #08110D;
            border-radius: 14px;
            padding: 12px 16px;
            font-weight: 900;
        }

        QPushButton#btnMap {
            background: rgba(55, 170, 255, 1.0);
            color: #051019;
            border-radius: 14px;
            padding: 12px 16px;
            font-weight: 900;
        }

        QPushButton#btnDiscord, QPushButton#btnPurple {
            background: rgba(139, 92, 255, 1.0);
            color: #0E0619;
            border-radius: 14px;
            padding: 12px 16px;
            font-weight: 900;
        }

        QPushButton#btnYellow {
            background: rgba(255, 189, 64, 1.0);
            color: #1B1304;
            border-radius: 14px;
            padding: 12px 16px;
            font-weight: 900;
        }

        QPushButton#btnGrey {
            background: rgba(55, 60, 66, 1.0);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 14px;
            padding: 12px 16px;
            font-weight: 800;
        }
        QPushButton#btnGrey:hover {
            border: 1px solid rgba(255,255,255,0.22);
        }

        QPushButton#btnPink {
            background: rgba(255, 107, 214, 1.0);
            color: #200010;
            border-radius: 14px;
            padding: 12px 16px;
            font-weight: 900;
        }
        """)


def main():
    app = QApplication(sys.argv)
    win = Launcher()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
