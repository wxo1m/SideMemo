"""SideMemo: a Windows edge note utility inspired by the supplied reference UI."""
from __future__ import annotations
import ctypes
import ctypes.wintypes
from datetime import datetime, timezone
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

# In frozen PyInstaller builds, register _internal and Qt library directories
# before importing PySide6 to prevent procedure-not-found / DLL load failures.
if getattr(sys, "frozen", False):
    _app_root = Path(sys.executable).resolve().parent
    _internal_candidates = [
        _app_root / "_internal",
        _app_root,
    ]
    for _base in _internal_candidates:
        if _base.is_dir():
            for _sub in [_base, _base / "PySide6", _base / "shiboken6"]:
                if _sub.is_dir():
                    try:
                        os.add_dll_directory(str(_sub))
                    except Exception:
                        pass
            os.environ["PATH"] = f"{_base};{_base / 'PySide6'};{_base / 'shiboken6'};" + os.environ.get("PATH", "")

from PySide6.QtCore import QEvent, QPoint, QStandardPaths, Qt, QTimer, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QAction, QColor, QCursor, QFont, QIcon, QKeySequence, QPixmap, QTextCharFormat, QTextCursor, QTextListFormat
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFontComboBox,
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QStyle, QSystemTrayIcon, QTextEdit, QToolButton, QVBoxLayout, QWidget)

from sidememo_sync import SyncError, SupabaseClient

APP_NAME = "SideMemo"
APP_VERSION = "1.0.6"
GITHUB_REPO = "wxo1m/SideMemo"
BASE_DIR = Path(__file__).resolve().parent
ICON_PATH = BASE_DIR / "app.ico"
DATA_DIR = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
DATA_FILE = DATA_DIR / "sidememo.json"
ATTACH_DIR = DATA_DIR / "attachments"
THEMES = {
    "노랑": ("#fff2b6", "#d9bd45"), "분홍": ("#fad6e6", "#d989ad"),
    "파랑": ("#dce9fb", "#91afd8"), "초록": ("#dcf3df", "#94c99f"),
    "보라": ("#eadcf8", "#ae8ad4"),
}

def get_note_colors(note: dict) -> tuple[str, str]:
    if not isinstance(note, dict):
        return THEMES["노랑"]
    theme = note.get("theme", "노랑")
    if theme in THEMES:
        return THEMES[theme]
    custom = note.get("custom_color")
    if isinstance(custom, (list, tuple)) and len(custom) == 2:
        return (str(custom[0]), str(custom[1]))
    if "커스텀" in THEMES:
        return THEMES["커스텀"]
    return THEMES["노랑"]
DIMENSIONS = {
    "280×280 (작게)": (280, 280),
    "360×360 (기본)": (360, 360),
    "460×460 (크게)": (460, 460),
    "250×250 (작게)": (280, 280),
    "350×350 (기본)": (360, 360),
    "450×450 (크게)": (460, 460),
    "2×2 (작게)": (280, 280),
    "3×3 (기본)": (360, 360),
    "4×4 (크게)": (460, 460),
}
TAB_WIDTH = 46
DEFAULT = {"settings": {"position": "오른쪽", "size": "360×360 (기본)", "opacity": 100,
    "behavior": "마우스 조작", "delay": 0.3, "checklist": True, "tray": True, "autostart": False, "font": "맑은 고딕",
    "sync_enabled": False, "sync_url": "", "sync_key": "", "sync_interval": 60},
    "notes": [{"id": "note1", "title": "NOTE 1", "theme": "노랑", "font": "맑은 고딕", "font_size": 18, "tab_slot": "1번 칸", "html": "", "attachment": ""},
    {"id": "note2", "title": "NOTE 2", "theme": "초록", "font": "맑은 고딕", "font_size": 16, "tab_slot": "2번 칸", "html": "", "attachment": ""},
    {"id": "note3", "title": "NOTE 3", "theme": "파랑", "font": "맑은 고딕", "font_size": 16, "tab_slot": "3번 칸", "html": "", "attachment": ""}],
    "tombstones": {}}

SESSION_FILE = DATA_DIR / "sidememo_session.json"


def html_signature(html: str) -> str:
    """Coarse fingerprint of note HTML, ignoring whitespace-only differences.

    Qt re-serializes the document on every load, so comparing raw strings would
    report a change for notes that are actually identical and could bounce the
    same note between two devices forever.
    """
    compact = re.sub(r">\s+<", "><", str(html or ""))
    return re.sub(r"\s+", " ", compact).strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def load_session() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_session(session: dict) -> None:
    try:
        data_path()
        SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

STYLE = """
QWidget { color:#242a38; }
QDialog { background:#f3f3f4; font-family: 'Malgun Gothic', 'Segoe UI'; } QPushButton { background:#fff; border:1px solid #d8d8df; border-radius:7px; padding:7px 12px; color:#242a38; }
QPushButton:hover { background:#f2edfa; border-color:#9148be; }
QLineEdit, QSpinBox, QDoubleSpinBox { background:#fff; border:1px solid #dedee4; border-radius:7px; padding:7px; min-height:22px; color:#242a38; }
QComboBox { background:#fff; border:1px solid #dedee4; border-radius:7px; padding:6px 28px 6px 10px; min-height:22px; color:#242a38; }
QComboBox:hover { border-color:#9148be; }
QComboBox::drop-down { subcontrol-origin:padding; subcontrol-position:top right; width:26px; border:none; }
QComboBox::down-arrow { width:0; height:0; border-left:5px solid transparent; border-right:5px solid transparent; border-top:6px solid #66667a; margin-right:8px; }
QComboBox QAbstractItemView { background:#ffffff; color:#242a38; selection-background-color:#ede2f8; selection-color:#242a38; border:1px solid #dedee4; border-radius:6px; padding:4px; }
QScrollArea { border:1px solid #dbdbe0; border-radius:8px; background:#fff; } QLabel { color:#242a38; }
#sideToolbar QPushButton#peekBtn { background:#f8df7c; border:none; border-radius:11px; padding:2px 8px; font-weight:bold; font-size:11px; color:#2b3345; min-height:22px; max-height:22px; }
#sideToolbar QPushButton#peekBtn:hover { background:#ebd065; }
#sideToolbar QToolButton#sizeBtn { background:transparent; border:none; border-radius:4px; font-size:11px; font-weight:bold; color:#2b3345; min-width:24px; max-width:24px; min-height:22px; max-height:22px; }
#sideToolbar QToolButton#sizeBtn:hover { background:rgba(0,0,0,0.10); }
#sideToolbar QToolButton#sizeBtn::menu-indicator { image:none; width:0px; }
#sideToolbar QToolButton { background:transparent; border:none; border-radius:4px; font-size:11px; font-weight:bold; color:#2b3345; min-width:19px; max-width:19px; min-height:22px; max-height:22px; }
#sideToolbar QToolButton:hover { background:rgba(0,0,0,0.10); }
#sideToolbar QToolButton:pressed { background:rgba(0,0,0,0.20); }
#sideToolbar QLabel#fontSizeLabel { background:#fff; border:1px solid #c8c8ce; border-radius:4px; padding:1px 3px; font-size:11px; font-weight:bold; color:#2b3345; min-width:28px; max-width:28px; min-height:20px; max-height:20px; qproperty-alignment: AlignCenter; }
#sideToolbar QToolButton#decBtn, #sideToolbar QToolButton#incBtn { background:transparent; border:none; border-radius:4px; font-size:10px; font-weight:bold; color:#2b3345; min-width:16px; max-width:16px; min-height:22px; max-height:22px; }
#sideToolbar QToolButton#decBtn:hover, #sideToolbar QToolButton#incBtn:hover { background:rgba(0,0,0,0.10); }
"""

STYLE_DARK = """
QWidget { color:#e8e8ec; }
QDialog { background:#1e1e24; font-family: 'Malgun Gothic', 'Segoe UI'; } QPushButton { background:#2a2a35; border:1px solid #44444f; border-radius:7px; padding:7px 12px; color:#e8e8ec; }
QPushButton:hover { background:#332a42; border-color:#9b5fd4; }
QLineEdit, QSpinBox, QDoubleSpinBox { background:#2a2a35; border:1px solid #44444f; border-radius:7px; padding:7px; min-height:22px; color:#e8e8ec; }
QComboBox { background:#2a2a35; border:1px solid #44444f; border-radius:7px; padding:6px 28px 6px 10px; min-height:22px; color:#e8e8ec; }
QComboBox:hover { border-color:#9b5fd4; }
QComboBox::drop-down { subcontrol-origin:padding; subcontrol-position:top right; width:26px; border:none; }
QComboBox::down-arrow { width:0; height:0; border-left:5px solid transparent; border-right:5px solid transparent; border-top:6px solid #aaaab8; margin-right:8px; }
QComboBox QAbstractItemView { background:#2a2a35; color:#e8e8ec; selection-background-color:#3f3254; selection-color:#ffffff; border:1px solid #44444f; border-radius:6px; padding:4px; }
QScrollArea { border:1px solid #38383f; border-radius:8px; background:#232330; }
QScrollBar:vertical { background:#1e1e24; width:8px; border-radius:4px; } QScrollBar::handle:vertical { background:#44444f; border-radius:4px; }
QLabel { color:#e8e8ec; } QCheckBox { color:#e8e8ec; } QDoubleSpinBox { background:#2a2a35; border:1px solid #44444f; border-radius:7px; padding:7px; color:#e8e8ec; }
#sideToolbar QPushButton#peekBtn { background:#f8df7c; border:none; border-radius:11px; padding:2px 8px; font-weight:bold; font-size:11px; color:#2b3345; min-height:22px; max-height:22px; }
#sideToolbar QPushButton#peekBtn:hover { background:#ebd065; }
#sideToolbar QToolButton { background:transparent; border:none; border-radius:4px; font-size:11px; font-weight:bold; color:#2b3345; min-width:19px; max-width:19px; min-height:22px; max-height:22px; }
#sideToolbar QToolButton:hover { background:rgba(0,0,0,0.10); }
#sideToolbar QToolButton:pressed { background:rgba(0,0,0,0.20); }
#sideToolbar QLabel#fontSizeLabel { background:#2a2a35; border:1px solid #44444f; border-radius:4px; padding:1px 3px; font-size:11px; font-weight:bold; color:#e8e8ec; min-width:28px; max-width:28px; min-height:20px; max-height:20px; qproperty-alignment: AlignCenter; }
#sideToolbar QToolButton#decBtn, #sideToolbar QToolButton#incBtn { background:transparent; border:none; border-radius:4px; font-size:10px; font-weight:bold; color:#2b3345; min-width:16px; max-width:16px; min-height:22px; max-height:22px; }
#sideToolbar QToolButton#decBtn:hover, #sideToolbar QToolButton#incBtn:hover { background:rgba(0,0,0,0.10); }
"""


def is_dark_mode() -> bool:
    """Check Windows dark mode registry setting."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return val == 0
    except Exception:
        return False


def get_style() -> str:
    return STYLE_DARK if is_dark_mode() else STYLE


def get_app_icon() -> QIcon:
    if ICON_PATH.is_file():
        return QIcon(str(ICON_PATH))
    cand = Path(sys.executable).parent / "app.ico"
    if cand.is_file():
        return QIcon(str(cand))
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)


def data_path():
    DATA_DIR.mkdir(parents=True, exist_ok=True); ATTACH_DIR.mkdir(parents=True, exist_ok=True)


def load_data():
    data_path()
    try:
        with DATA_FILE.open(encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = json.loads(json.dumps(DEFAULT))
    for note in data.get("notes", []):
        if note.get("theme") == "커스텀" and "custom_color" in note:
            cc = note["custom_color"]
            if isinstance(cc, (list, tuple)) and len(cc) == 2:
                THEMES["커스텀"] = (str(cc[0]), str(cc[1]))
    return data


def save_data(data):
    data_path(); DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r'\d+', str(v))
    return tuple(int(x) for x in nums) if nums else (0, 0, 0)


class UpdateCheckWorker(QThread):
    update_available = Signal(str, str, str, int)
    already_latest = Signal(str)
    check_failed = Signal(str)

    def run(self):
        latest_tag = ""
        release_notes = ""
        download_url = ""
        asset_size = 0
        found_update = False
        v_ok = False
        r_ok = False
        last_err = ""

        # 1. Check version.json from GitHub raw URL (always accessible, carries custom release_notes)
        try:
            v_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/version.json"
            v_req = urllib.request.Request(v_url, headers={
                "User-Agent": f"SideMemo-App/{APP_VERSION}",
                "Cache-Control": "no-cache"
            })
            with urllib.request.urlopen(v_req, timeout=5) as resp:
                if resp.status == 200:
                    v_data = json.loads(resp.read().decode("utf-8"))
                    v_ver = v_data.get("version", "").strip()
                    v_notes = v_data.get("release_notes", "").strip()
                    v_dl = v_data.get("download_url", "").strip()
                    v_ok = True
                    if parse_version(v_ver) > parse_version(APP_VERSION):
                        latest_tag = f"v{v_ver}" if not v_ver.startswith("v") else v_ver
                        release_notes = v_notes
                        download_url = v_dl or f"https://github.com/{GITHUB_REPO}/releases/download/{latest_tag}/SideMemo-Setup.exe"
                        found_update = True
        except Exception as e:
            last_err = str(e)

        # 2. Check GitHub Releases API
        try:
            r_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            r_req = urllib.request.Request(r_url, headers={
                "User-Agent": f"SideMemo-App/{APP_VERSION}",
                "Accept": "application/vnd.github.v3+json"
            })
            with urllib.request.urlopen(r_req, timeout=5) as resp:
                if resp.status == 200:
                    r_data = json.loads(resp.read().decode("utf-8"))
                    r_tag = r_data.get("tag_name", "").strip()
                    r_body = r_data.get("body", "").strip()
                    r_ok = True
                    if parse_version(r_tag) > parse_version(APP_VERSION):
                        latest_tag = r_tag
                        if r_body and not release_notes:
                            release_notes = r_body
                        for asset in r_data.get("assets", []):
                            if asset.get("name", "").lower().endswith(".exe"):
                                download_url = asset.get("browser_download_url", download_url)
                                asset_size = asset.get("size", 0)
                                break
                        found_update = True
        except Exception as e:
            if not last_err:
                last_err = str(e)

        if found_update:
            if not release_notes:
                release_notes = "새로운 기능 및 안정성 개선이 포함되어 있습니다."
            if not download_url:
                download_url = f"https://github.com/{GITHUB_REPO}/releases/download/{latest_tag}/SideMemo-Setup.exe"
            self.update_available.emit(latest_tag, release_notes, download_url, asset_size)
        elif v_ok or r_ok:
            self.already_latest.emit(APP_VERSION)
        else:
            self.check_failed.emit(last_err or "네트워크 연결 또는 배포 릴리스 확인")


class DownloadWorker(QThread):
    progress = Signal(int)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, download_url: str, tag_name: str, parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self.tag_name = tag_name

    def run(self):
        try:
            temp_dir = tempfile.gettempdir()
            clean_tag = self.tag_name.lstrip("vV")
            target_file = os.path.join(temp_dir, f"SideMemo-Setup-{clean_tag}.exe")
            urls = [self.download_url]
            fallback_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/installer/SideMemo-Setup.exe"
            if fallback_url not in urls:
                urls.append(fallback_url)

            success = False
            last_err = None
            for u in urls:
                if not u:
                    continue
                try:
                    req = urllib.request.Request(u, headers={
                        "User-Agent": f"SideMemo-App/{APP_VERSION}"
                    })
                    with urllib.request.urlopen(req, timeout=45) as resp, open(target_file, "wb") as f:
                        total_size = int(resp.headers.get("content-length", 0))
                        downloaded = 0
                        block_size = 65536
                        while True:
                            chunk = resp.read(block_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                self.progress.emit(int((downloaded / total_size) * 100))
                    if os.path.exists(target_file) and os.path.getsize(target_file) > 500000:
                        success = True
                        break
                except Exception as e:
                    last_err = e

            if success:
                self.finished.emit(target_file)
            else:
                self.error.emit(str(last_err or "다운로드에 실패했습니다."))
        except Exception as e:
            self.error.emit(str(e))


class UpdateDialog(QDialog):
    def __init__(self, tag_name: str, notes: str, download_url: str, asset_size: int, parent=None):
        super().__init__(parent)
        self.download_url = download_url
        self.tag_name = tag_name
        self.setWindowTitle("SideMemo 업데이트")
        self.setFixedSize(500, 370)
        self.setStyleSheet(get_style())
        self.setWindowIcon(get_app_icon())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(10)

        header = QLabel(f"<b style='font-size:18px;'>새로운 업데이트가 있습니다!</b><br>"
                        f"<span style='color:#9b5fd4; font-weight:bold;'>현재: v{APP_VERSION} → 최신: {tag_name}</span>")
        layout.addWidget(header)

        notes_label = QLabel("<b>변경 사항:</b>")
        layout.addWidget(notes_label)

        self.notes_box = QTextEdit()
        self.notes_box.setReadOnly(True)
        self.notes_box.setPlainText(notes.strip() or "새로운 기능 및 안정성 개선이 포함되어 있습니다.")
        layout.addWidget(self.notes_box, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("지금 업데이트를 다운로드하고 설치하시겠습니까?")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.later_btn = QPushButton("나중에")
        self.later_btn.clicked.connect(self.reject)
        self.update_btn = QPushButton("지금 업데이트")
        self.update_btn.setStyleSheet("background:#9250bc; color:white; font-weight:bold; padding:7px 16px;")
        self.update_btn.clicked.connect(self.start_download)

        btn_row.addWidget(self.later_btn)
        btn_row.addWidget(self.update_btn)
        layout.addLayout(btn_row)

    def start_download(self):
        self.update_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.progress_bar.show()
        self.status_label.setText("업데이트 파일을 다운로드하는 중입니다...")

        self.worker = DownloadWorker(self.download_url, self.tag_name, self)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished.connect(self.on_download_finished)
        self.worker.error.connect(self.on_download_error)
        self.worker.start()

    def on_download_finished(self, target_file: str):
        self.status_label.setText("다운로드 완료! 설치 프로그램을 실행합니다...")
        try:
            subprocess.Popen([target_file])
            QApplication.instance().quit()
        except Exception as e:
            QMessageBox.critical(self, "실행 실패", f"설치 프로그램을 실행하지 못했습니다: {e}")
            self.update_btn.setEnabled(True)
            self.later_btn.setEnabled(True)

    def on_download_error(self, err_msg: str):
        self.status_label.setText("다운로드에 실패했습니다.")
        QMessageBox.warning(self, "다운로드 실패", f"업데이트 다운로드 중 오류가 발생했습니다:\n{err_msg}")
        self.update_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        self.progress_bar.hide()


def check_for_updates(parent=None, silent_if_latest=True):
    worker = UpdateCheckWorker(parent)
    def on_update(tag, notes, url, size):
        dlg = UpdateDialog(tag, notes, url, size, parent=parent)
        dlg.exec()

    def on_latest(curr):
        if not silent_if_latest:
            QMessageBox.information(parent, "업데이트 확인", f"현재 최신 버전(v{curr})을 사용하고 있습니다.")

    def on_fail(err):
        if not silent_if_latest:
            QMessageBox.information(parent, "업데이트 확인", f"최신 버전을 확인하지 못했습니다.\n(네트워크 연결 또는 배포 릴리스 확인)")

    worker.update_available.connect(on_update)
    worker.already_latest.connect(on_latest)
    worker.check_failed.connect(on_fail)
    worker.start()
    if parent:
        parent._update_worker = worker
    return worker


ATTACH_SYNC_LIMIT = 1_500_000  # 1.5MB - keep the synced JSON payload reasonable


def attachment_to_data_uri(path: str) -> str:
    """Encode a local attachment so the web app can render it without file access."""
    try:
        candidate = Path(path)
        if not candidate.is_file() or candidate.stat().st_size > ATTACH_SYNC_LIMIT:
            return ""
        import base64
        suffix = candidate.suffix.lower().lstrip(".") or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "bmp": "bmp"}.get(suffix, "png")
        encoded = base64.b64encode(candidate.read_bytes()).decode("ascii")
        return f"data:image/{mime};base64,{encoded}"
    except (OSError, ValueError):
        return ""


class SyncCycle(QThread):
    """One push-then-pull round trip against Supabase, off the UI thread."""

    completed = Signal(list, object, list, object)
    failed = Signal(str)
    auth_lost = Signal()

    def __init__(self, client: SupabaseClient, push_rows: list[dict], push_settings: dict | None,
                 since: str | None, parent=None, pull_only: bool = False):
        super().__init__(parent)
        self.client = client
        self.push_rows = [] if pull_only else push_rows
        self.push_settings = None if pull_only else push_settings
        self.since = since

    def run(self):
        try:
            pushed_rows = self.client.push_notes(self.push_rows) if self.push_rows else []
            pushed_settings = self.client.push_settings(self.push_settings) if self.push_settings is not None else None
            rows = self.client.pull_notes(self.since)
            settings_row = self.client.pull_settings()
            self.completed.emit(rows, settings_row, pushed_rows or [], pushed_settings)
        except SyncError as exc:
            message = str(exc)
            if "다시 로그인" in message or "만료" in message or "로그인 정보" in message:
                self.auth_lost.emit()
            self.failed.emit(message)
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(str(exc))


class LoginDialog(QDialog):
    """Sign in / sign up against the configured Supabase project."""

    def __init__(self, app: "SideMemo", parent=None):
        super().__init__(parent or app)
        self.app = app
        self.dark = is_dark_mode()
        self.setWindowTitle("SideMemo 로그인")
        self.setMinimumWidth(420)
        self.setStyleSheet(get_style())
        self.setWindowIcon(get_app_icon())
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(10)
        sub = "#aaaaaa" if self.dark else "#666666"
        root.addWidget(QLabel(f"<b style='font-size:19px'>SideMemo 계정</b><br>"
                              f"<span style='color:{sub}'>같은 계정으로 PC와 웹에서 메모를 함께 사용합니다.</span>"))
        self.email = QLineEdit(self.app.data["settings"].get("sync_email", ""))
        self.email.setPlaceholderText("이메일 주소")
        self.password = QLineEdit()
        self.password.setPlaceholderText("비밀번호 (6자 이상)")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        root.addWidget(QLabel("이메일")); root.addWidget(self.email)
        root.addWidget(QLabel("비밀번호")); root.addWidget(self.password)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.sign_in_btn = QPushButton("로그인")
        self.sign_in_btn.setStyleSheet("background:#9250bc;color:white;font-weight:bold;")
        self.sign_up_btn = QPushButton("회원가입")
        cancel = QPushButton("닫기")
        buttons.addStretch(); buttons.addWidget(self.sign_up_btn); buttons.addWidget(self.sign_in_btn); buttons.addWidget(cancel)
        root.addLayout(buttons)
        self.sign_in_btn.clicked.connect(lambda: self.submit(sign_up=False))
        self.sign_up_btn.clicked.connect(lambda: self.submit(sign_up=True))
        cancel.clicked.connect(self.reject)

    def submit(self, sign_up: bool):
        email = self.email.text().strip()
        password = self.password.text()
        if not email or not password:
            self.status.setText("이메일과 비밀번호를 모두 입력해 주세요.")
            return
        if sign_up and len(password) < 6:
            self.status.setText("비밀번호는 6자 이상이어야 합니다.")
            return
        self.sign_in_btn.setEnabled(False); self.sign_up_btn.setEnabled(False)
        self.status.setText("처리 중...")
        QApplication.processEvents()
        try:
            session = self.app.sync_sign_up(email, password) if sign_up else self.app.sync_sign_in(email, password)
        except SyncError as exc:
            self.status.setText(str(exc))
            self.sign_in_btn.setEnabled(True); self.sign_up_btn.setEnabled(True)
            return
        self.sign_in_btn.setEnabled(True); self.sign_up_btn.setEnabled(True)
        if not session:
            self.status.setText("가입 확인 메일을 보냈습니다. 메일 인증 후 로그인해 주세요.")
            return
        self.accept()

class SettingsDialog(QDialog):
    def __init__(self, app: "SideMemo", note_index: int | None = None):
        super().__init__(app); self.app, self.note_index = app, note_index; self.dark = is_dark_mode()
        self.setWindowTitle("SideMemo 설정"); self.setMinimumSize(760, 730); self.setStyleSheet(get_style()); self.setWindowIcon(get_app_icon())
        root = QVBoxLayout(self); root.setContentsMargins(12, 10, 12, 10); root.setSpacing(8)
        root.addLayout(self.header())
        self.tabbar_layout = QHBoxLayout(); root.addLayout(self.tabbar_layout); self.refresh_tabbar()
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); root.addWidget(self.scroll, 1)
        footer = QHBoxLayout(); self.tray_box = QCheckBox("트레이 아이콘 표시"); self.start_box = QCheckBox("Windows 시작 시 SideMemo 자동 실행")
        footer.addWidget(self.tray_box); footer.addWidget(self.start_box); footer.addStretch(); self.apply_button = QPushButton("적용"); self.apply_button.setMinimumWidth(102); self.apply_button.setStyleSheet("background:#9250bc;color:white;font-weight:bold;")
        close = QPushButton("닫기"); footer.addWidget(self.apply_button); footer.addWidget(close); root.addLayout(footer)
        close.clicked.connect(self.close); self.apply_button.clicked.connect(self.apply)
        self.show_page(note_index)

    def header(self):
        h = QHBoxLayout()
        c1 = "#aaaaaa" if self.dark else "#666666"
        c2 = "#6ab0ff" if self.dark else "#006bc5"
        label = QLabel(f"<b style='font-size:21px'>SideMemo 설정</b> <span style='font-size:12px;color:#9b5fd4;font-weight:bold;'>v{APP_VERSION}</span><br><span style='color:{c1}'>© 2026. SideMemo All rights reserved.</span><br><span style='color:{c2}'>빠르게 열고 닫는 화면 가장자리 메모</span>")
        h.addWidget(label); h.addStretch()
        check_update_btn = QPushButton("업데이트 확인")
        check_update_btn.setStyleSheet("font-size:11px; padding:5px 10px;")
        check_update_btn.clicked.connect(lambda: check_for_updates(parent=self, silent_if_latest=False))
        h.addWidget(check_update_btn)
        h.addWidget(QLabel("◎"))
        lang = QComboBox(); lang.addItem("한국어"); lang.setFixedWidth(120); h.addWidget(lang)
        return h

    def refresh_tabbar(self):
        while self.tabbar_layout.count():
            item = self.tabbar_layout.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
        gear = QPushButton("⚙"); gear.setFixedWidth(42); gear.clicked.connect(lambda: self.show_page(None)); self.tabbar_layout.addWidget(gear)
        for i, note in enumerate(self.app.data["notes"]):
            txt_col = "#d0d0d8" if self.dark else "#26314b"
            _, edge_col = get_note_colors(note)
            b = QPushButton(f"● {note['title']}"); b.setStyleSheet(f"color:{txt_col}; border-color:{edge_col};")
            b.clicked.connect(lambda checked=False, x=i: self.show_page(x)); self.tabbar_layout.addWidget(b)
        plus = QPushButton("+"); plus.setFixedWidth(42); plus.clicked.connect(self.add_note_from_settings); self.tabbar_layout.addWidget(plus); self.tabbar_layout.addStretch()

    def add_note_from_settings(self):
        self.app.create_new_note()
        self.refresh_tabbar()
        self.show_page(len(self.app.data["notes"]) - 1)

    def show_page(self, note_index):
        self.note_index = note_index; w = QWidget(); self.form = QVBoxLayout(w); self.form.setContentsMargins(16, 15, 16, 15); self.form.setSpacing(12)
        if note_index is None: self.global_page()
        else: self.note_page(note_index)
        self.form.addStretch(); self.scroll.setWidget(w)

    def row(self, title, control, hint=""):
        line = QHBoxLayout(); label = QLabel(f"<b>{title}</b>"); label.setFixedWidth(175); line.addWidget(label); line.addWidget(control, 1); self.form.addLayout(line)
        if hint:
            hint_col = "#888898" if self.dark else "#74747a"
            self.form.addWidget(QLabel(hint), 0, Qt.AlignmentFlag.AlignLeft)
            self.form.itemAt(self.form.count()-1).widget().setStyleSheet(f"color:{hint_col}; margin-left:175px;")

    def global_page(self):
        bc = "#38383f" if self.dark else "#ddd"
        tc = "#e8e8ec" if self.dark else "#242a38"
        bg = "#28282f" if self.dark else "#fff"
        self.form.addWidget(QLabel(f"<div style='border:1px solid {bc};border-radius:8px;padding:12px;background:{bg};color:{tc}'><b style='font-size:18px'>▣ SideMemo</b><br>화면 좌·우 테두리에서 빠르게 열고 닫을 수 있는 메모 도구입니다.</div>"))
        s = self.app.data["settings"]
        self.memo_size = QComboBox(); self.memo_size.addItems(["280×280 (작게)", "360×360 (기본)", "460×460 (크게)"]); self.memo_size.setCurrentText(s["size"]); self.row("메모 크기", self.memo_size)
        op_widget = QWidget(); op_layout = QVBoxLayout(op_widget); op_layout.setContentsMargins(0,0,0,0); op_layout.setSpacing(2)
        self.opacity = QSlider(Qt.Orientation.Horizontal); self.opacity.setRange(40, 100); self.opacity.setValue(s["opacity"])
        op_lbl_row = QHBoxLayout(); op_lbl_row.setContentsMargins(2,0,2,0)
        lbl_col = "#888898" if self.dark else "#74747a"
        lbl_40 = QLabel("40%"); lbl_40.setStyleSheet(f"color:{lbl_col}; font-size:12px;")
        lbl_100 = QLabel("100%"); lbl_100.setStyleSheet(f"color:{lbl_col}; font-size:12px;")
        op_lbl_row.addWidget(lbl_40); op_lbl_row.addStretch(); op_lbl_row.addWidget(lbl_100)
        op_layout.addWidget(self.opacity); op_layout.addLayout(op_lbl_row)
        self.row("메모 불투명도", op_widget)
        self.behavior = QComboBox(); self.behavior.addItems(["마우스 조작", "항상 펼침"]); self.behavior.setCurrentText(s["behavior"]); self.row("메모 열기 방법", self.behavior)
        self.delay = QDoubleSpinBox(); self.delay.setRange(0.1, 5.0); self.delay.setSingleStep(0.1); self.delay.setDecimals(1); self.delay.setSuffix("초"); self.delay.setValue(float(s.get("delay", 0.3))); self.row("접힘 대기 시간", self.delay)
        self.global_font = QFontComboBox()
        self.global_font.setEditable(False)
        cur_global_font = s.get("font", "맑은 고딕")
        self.global_font.setCurrentFont(QFont(cur_global_font))
        self.row("전체 글꼴", self.global_font)
        self.sample = QLabel("가나다 ABC 123")
        self.update_sample_font(cur_global_font)
        self.global_font.currentFontChanged.connect(lambda f: self.update_sample_font(f.family()))
        self.form.addWidget(self.sample)
        self.sync_enabled = QCheckBox("이 PC에서 자동 동기화 사용")
        self.sync_enabled.setChecked(bool(s.get("sync_enabled", False)))
        self.row("자동 동기화", self.sync_enabled)
        self.sync_url = QLineEdit(s.get("sync_url", "")); self.sync_url.setPlaceholderText("https://<project>.supabase.co")
        self.row("Supabase 주소", self.sync_url)
        self.sync_key = QLineEdit(s.get("sync_key", "")); self.sync_key.setPlaceholderText("anon public key")
        self.row("Supabase anon key", self.sync_key)
        self.sync_interval = QSpinBox(); self.sync_interval.setRange(15, 3600); self.sync_interval.setSuffix("초")
        self.sync_interval.setValue(int(s.get("sync_interval", 60))); self.row("동기화 주기", self.sync_interval)
        account = QWidget(); al2 = QHBoxLayout(account); al2.setContentsMargins(0,0,0,0)
        login_btn = QPushButton("로그인 / 계정"); login_btn.clicked.connect(self.app.open_login)
        sync_btn = QPushButton("지금 동기화"); sync_btn.clicked.connect(lambda: self.app.sync_now(manual=True))
        al2.addWidget(login_btn); al2.addWidget(sync_btn); al2.addStretch()
        self.row("계정", account)
        winget_widget = QWidget(); wl = QHBoxLayout(winget_widget); wl.setContentsMargins(0,0,0,0)
        self.winget_edit = QLineEdit("winget install wxo1m.SideMemo")
        self.winget_edit.setReadOnly(True)
        copy_btn = QPushButton("복사")
        def copy_cmd():
            QApplication.clipboard().setText("winget install wxo1m.SideMemo")
            copy_btn.setText("복사됨!")
            QTimer.singleShot(1500, lambda: copy_btn.setText("복사"))
        copy_btn.clicked.connect(copy_cmd)
        wl.addWidget(self.winget_edit, 1); wl.addWidget(copy_btn)
        self.row("winget 명령어", winget_widget)
        self.checklist = QCheckBox("체크리스트 도구 모음 사용"); self.checklist.setChecked(s["checklist"]); self.row("체크리스트", self.checklist)
        backup = QWidget(); bl = QHBoxLayout(backup); bl.setContentsMargins(0,0,0,0); export = QPushButton("백업 내보내기"); restore = QPushButton("백업 가져오기"); export.clicked.connect(self.app.export_backup); restore.clicked.connect(self.app.import_backup); bl.addWidget(export); bl.addWidget(restore); self.row("백업 관리", backup)
        self.tray_box.setChecked(s["tray"]); self.start_box.setChecked(s["autostart"])

    def update_sample_font(self, font_name: str):
        sbg = "#2e2e3a" if self.dark else "#ececef"
        stc = "#e8e8ec" if self.dark else "#242a38"
        self.sample.setStyleSheet(f"font-family: '{font_name}'; font-size:24px; background:{sbg}; color:{stc}; padding:18px; border-radius:6px;")
        self.sample.setFont(QFont(font_name, 24))

    def note_page(self, index):
        n = self.app.data["notes"][index]
        self.current_theme = n.get("theme", "노랑")
        self.current_custom_color = n.get("custom_color")
        self.title_edit = QLineEdit(n["title"]); self.title_edit.setMaxLength(6); self.row("인덱스 제목", self.title_edit)
        total_notes = len(self.app.data["notes"])
        slot_items = [f"{i+1}번 칸" for i in range(total_notes)]
        self.slot = QComboBox(); self.slot.addItems(slot_items)
        current_slot = f"{index+1}번 칸"
        self.slot.setCurrentText(current_slot); self.row("인덱스 위치", self.slot)
        self.font_size = QSpinBox(); self.font_size.setRange(8, 48); self.font_size.setSuffix("px"); self.font_size.setValue(n["font_size"]); self.row("기본 글자 크기", self.font_size)
        self.font = QFontComboBox()
        self.font.setEditable(False)
        cur_font_name = n.get("font", "맑은 고딕")
        self.font.setCurrentFont(QFont(cur_font_name))
        self.row("글꼴", self.font)
        self.sample = QLabel("가나다 ABC 123")
        self.update_sample_font(cur_font_name)
        self.font.currentFontChanged.connect(lambda f: self.update_sample_font(f.family()))
        self.form.addWidget(self.sample)
        colors = QWidget(); cl = QHBoxLayout(colors); cl.setContentsMargins(0,0,0,0); self.theme_buttons = []
        for name in ("분홍", "노랑", "초록", "파랑"):
            bg, edge = THEMES[name]
            b = QPushButton(name); b.setStyleSheet(f"background:{bg};border:2px solid {edge if name == self.current_theme else '#d9d9dd'};"); b.clicked.connect(lambda checked=False,x=name: self.choose_theme(x)); cl.addWidget(b); self.theme_buttons.append((name,b))
        custom_bg, custom_edge = get_note_colors(n) if self.current_theme == "커스텀" else ("#ffffff", "#d9d9dd")
        self.custom_btn = QPushButton("커스텀")
        self.custom_btn.setStyleSheet(f"background:{custom_bg};border:2px solid {custom_edge if self.current_theme == '커스텀' else '#d9d9dd'};")
        self.custom_btn.clicked.connect(self.custom_color)
        cl.addWidget(self.custom_btn)
        self.row("색 테마", colors)
        attach = QWidget(); al = QHBoxLayout(attach); al.setContentsMargins(0,0,0,0); self.attach_label = QLabel(Path(n.get("attachment", "")).name or "첨부 이미지 없음"); choose = QPushButton("이미지 선택"); choose.clicked.connect(self.choose_attachment); al.addWidget(self.attach_label,1); al.addWidget(choose); self.row("첨부 이미지", attach)

    def choose_theme(self, name):
        self.current_theme = name
        for key, button in self.theme_buttons:
            button.setStyleSheet(f"background:{THEMES[key][0]};border:2px solid {THEMES[key][1] if key == name else '#d9d9dd'};")
        if hasattr(self, "custom_btn"):
            self.custom_btn.setStyleSheet("background:#ffffff;border:2px solid #d9d9dd;")

    def custom_color(self):
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            bg = color.lighter(175).name()
            edge = color.darker(115).name()
            self.current_theme = "커스텀"
            self.current_custom_color = [bg, edge]
            THEMES["커스텀"] = (bg, edge)
            if hasattr(self, "custom_btn"):
                self.custom_btn.setStyleSheet(f"background:{bg};border:2px solid {edge};")
            for key, button in self.theme_buttons:
                button.setStyleSheet(f"background:{THEMES[key][0]};border:2px solid #d9d9dd;")

    def choose_attachment(self):
        src, _ = QFileDialog.getOpenFileName(self, "첨부 이미지", "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if src:
            data_path(); target = ATTACH_DIR / f"{uuid.uuid4().hex}{Path(src).suffix.lower()}"; shutil.copy2(src, target); self.attachment = str(target); self.attach_label.setText(Path(src).name)

    def apply(self):
        if self.note_index is None:
            chosen_global_font = self.global_font.currentFont().family()
            s = self.app.data["settings"]; s.update({"size":self.memo_size.currentText(), "opacity":self.opacity.value(), "behavior":self.behavior.currentText(), "delay":round(self.delay.value(), 1), "checklist":self.checklist.isChecked(), "tray":self.tray_box.isChecked(), "autostart":self.start_box.isChecked(), "font":chosen_global_font, "sync_enabled":self.sync_enabled.isChecked(), "sync_url":self.sync_url.text().strip(), "sync_key":self.sync_key.text().strip(), "sync_interval":self.sync_interval.value()})
            self.app.save_editor()
            s.pop("settings_synced_at", None)
            for note in self.app.data["notes"]:
                note["font"] = chosen_global_font
                if note.get("html"):
                    note["html"] = re.sub(r"font-family:[^;\"'\s]+", f"font-family:'{chosen_global_font}'", note["html"])
                note.pop("synced_at", None)
            self.app.set_note(self.app.active)
            self.app.update_autostart(); self.app.reposition(); self.app.update_opacity(); self.app.setup_tray()
            self.app.start_sync_timer()
        else:
            self.app.save_editor(); notes = self.app.data["notes"]
            n = notes[self.note_index]
            chosen_font = self.font.currentFont().family()
            n.update({"title":self.title_edit.text() or "새 메모", "font_size":self.font_size.value(), "font":chosen_font, "theme":self.current_theme})
            if self.current_theme == "커스텀" and hasattr(self, "current_custom_color") and self.current_custom_color:
                n["custom_color"] = list(self.current_custom_color)
            if hasattr(self, "attachment"): n["attachment"] = self.attachment
            n.pop("synced_at", None)
            try:
                target_pos = int(self.slot.currentText().replace("번 칸", "").strip()) - 1
            except Exception:
                target_pos = self.note_index
            if 0 <= target_pos < len(notes) and target_pos != self.note_index:
                moved_note = notes.pop(self.note_index)
                notes.insert(target_pos, moved_note)
                self.note_index = target_pos
                self.app.active = target_pos
                for item in notes:
                    item.pop("synced_at", None)
            for i, note in enumerate(notes):
                note["tab_slot"] = f"{i+1}번 칸"
            self.app.set_note(self.note_index)
            self.refresh_tabbar()
        save_data(self.app.data); self.app.refresh_tabs()


class SideMemo(QMainWindow):
    def __init__(self):
        super().__init__(); self.data = load_data(); self.migrate_settings(); self.active = 0; self.toolbar_locked = False; self.collapsed = True; self.setWindowIcon(get_app_icon())
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.collapse)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(STYLE); self.build(); self.setup_tray(); self.setup_single_instance(); self.register_hotkey(); self.set_note(0); self.reposition(); self.page.hide(); self.show(); self.raise_()
        self.save_timer = QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.timeout.connect(self.save_editor)
        QTimer.singleShot(2500, lambda: check_for_updates(parent=self, silent_if_latest=True))
        self._sync_client = None; self._syncing = False; self._sync_since = None; self._sync_status = ""
        QTimer.singleShot(1500, self.start_sync_timer)

    def build(self):
        central = QWidget(); central.setStyleSheet("background:transparent;"); self.setCentralWidget(central); self.root_layout = QHBoxLayout(central); self.root_layout.setContentsMargins(0,0,0,0); self.root_layout.setSpacing(0)
        self.page = QWidget(); self.page_layout = QVBoxLayout(self.page); self.page_layout.setContentsMargins(0,0,0,0); self.page_layout.setSpacing(0)
        header = QHBoxLayout(); header.setContentsMargins(10,6,8,2); self.note_title = QLabel(); self.note_title.setStyleSheet("font-size:16px;font-weight:bold;color:#26314b;"); header.addWidget(self.note_title); header.addStretch()
        settings = QToolButton(); settings.setText("⚙"); settings.clicked.connect(self.open_settings)
        delete = QToolButton(); delete.setText("🗑"); delete.setToolTip("현재 메모 삭제"); delete.clicked.connect(self.delete_active_note)
        close = QToolButton(); close.setText("×"); close.clicked.connect(self.hide); header.addWidget(settings); header.addWidget(delete); header.addWidget(close); self.page_layout.addLayout(header)
        self.editor = QTextEdit(); self.editor.setAcceptRichText(True); self.editor.setFrameShape(QFrame.Shape.NoFrame); self.editor.setMinimumHeight(40); self.editor.setStyleSheet("padding:4px 14px;background:transparent;color:#26314b;"); self.editor.textChanged.connect(self.queue_save); self.editor.installEventFilter(self); self.editor.viewport().installEventFilter(self); self.page_layout.addWidget(self.editor,1)
        self.toolbar = QWidget(); self.toolbar.setObjectName("sideToolbar"); self.toolbar.setFixedHeight(34); tl = QHBoxLayout(self.toolbar); tl.setContentsMargins(4,2,4,4); tl.setSpacing(2)
        self.ice = QPushButton("❄"); self.ice.setObjectName("peekBtn"); self.ice.setToolTip("고정"); self.ice.setAccessibleName("고정"); self.ice.setFixedSize(28,22); self.ice.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed); self.ice.clicked.connect(self.toggle_toolbar_lock); tl.addWidget(self.ice)
        # Font size controls: A- [size] A+
        self.dec_btn = QToolButton(); self.dec_btn.setObjectName("decBtn"); self.dec_btn.setText("A-"); self.dec_btn.setFixedSize(16,22); self.dec_btn.clicked.connect(self.dec_font_size); tl.addWidget(self.dec_btn)
        self.font_size_label = QLabel("16"); self.font_size_label.setObjectName("fontSizeLabel"); self.font_size_label.setFixedSize(28,20); self.font_size_label.setAlignment(Qt.AlignmentFlag.AlignCenter); tl.addWidget(self.font_size_label)
        self.inc_btn = QToolButton(); self.inc_btn.setObjectName("incBtn"); self.inc_btn.setText("A+"); self.inc_btn.setFixedSize(16,22); self.inc_btn.clicked.connect(self.inc_font_size); tl.addWidget(self.inc_btn)
        self.tool_buttons = {}
        for label, action in [("B",self.bold),("I",self.italic),("U",self.underline),("S",self.strike),("≡",self.align_center),("•",self.bullet),("1.",self.numbered),("☑",self.check_item)]:
            b=QToolButton(); b.setText(label); b.setFixedSize(19,22); b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed); b.clicked.connect(action); tl.addWidget(b); self.tool_buttons[label] = b
        tl.addStretch(1); self.page_layout.addWidget(self.toolbar)
        self.tab_view = QScrollArea(); self.tab_view.setFixedWidth(TAB_WIDTH); self.tab_view.setWidgetResizable(False); self.tab_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.tab_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.tab_view.setStyleSheet("QScrollArea { border:0; background:transparent; } QScrollBar { width:0; height:0; }")
        self.tabs = QWidget(); self.tabs.setFixedWidth(TAB_WIDTH); self.tabs.setStyleSheet("background:transparent;"); self.tabs_layout = QVBoxLayout(self.tabs); self.tabs_layout.setContentsMargins(0,6,0,6); self.tabs_layout.setSpacing(4); self.tab_view.setWidget(self.tabs)
        self.tab_view.installEventFilter(self); self.tab_view.viewport().installEventFilter(self); self.tabs.installEventFilter(self)
        self.root_layout.addWidget(self.page,1); self.root_layout.addWidget(self.tab_view); self.refresh_tabs()

    def refresh_tabs(self):
        while self.tabs_layout.count():
            item=self.tabs_layout.takeAt(0); w=item.widget(); w.deleteLater() if w else None
        for i,n in enumerate(self.data["notes"]):
            bg,edge=get_note_colors(n); b=QPushButton("\n".join(n["title"])); b.setFixedHeight(108); b.setStyleSheet(f"background:{bg};border:2px solid {edge};border-radius:14px;color:#26314b;font-weight:bold;font-size:12px;"); b.setProperty("tab_index", i); b.installEventFilter(self); self.tabs_layout.addWidget(b)
        add=QPushButton("+"); add.setFixedSize(28,28); add.setStyleSheet("background:#ffffff;border:1px solid #d7d7df;border-radius:14px;font-size:17px;"); add.setProperty("is_add", True); add.installEventFilter(self); self.tabs_layout.addWidget(add, 0, Qt.AlignmentFlag.AlignHCenter); self.tabs_layout.addStretch()
        self.tabs.setFixedHeight(max(1, len(self.data["notes"]) * 112 + 48))

    def select_tab(self,index):
        if self.collapsed: self.expand()
        self.save_editor(); self.set_note(index)

    def set_note(self,index):
        self.active = index; n = self.data["notes"][index]; bg, _ = get_note_colors(n)
        self.page.setStyleSheet(f"background:{bg};")
        self.toolbar.setStyleSheet(f"background:{bg};")
        self.note_title.setText(n["title"])
        font_family = n.get("font", "맑은 고딕")
        font_size = n.get("font_size", 16)
        target_font = QFont(font_family, font_size)
        self.editor.blockSignals(True)
        self.editor.setStyleSheet(f"font-family: '{font_family}'; font-size: {font_size}pt; padding:4px 14px; background:transparent; color:#26314b;")
        self.editor.setFont(target_font)
        self.editor.document().setDefaultFont(target_font)
        html_content = n.get("html", "")
        if html_content:
            updated_html = re.sub(r"font-family:[^;\"'\s]+", f"font-family:'{font_family}'", html_content)
            self.editor.setHtml(updated_html)
            n["html"] = updated_html
        else:
            self.editor.setPlainText("")
        # Apply font family across the whole text so saved inline HTML styles don't lock the old font
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextCharFormat()
        fmt.setFontFamilies([font_family])
        fmt.setFontPointSize(font_size)
        cursor.mergeCharFormat(fmt)
        self.editor.setFontFamily(font_family)
        self.editor.setFontPointSize(font_size)
        self.editor.blockSignals(False)
        self.refresh_tabs()
        if hasattr(self, "font_size_label"): self.font_size_label.setText(str(font_size))
        self.apply_current_note_font()

    def save_editor(self):
        if self.data["notes"]:
            note = self.data["notes"][self.active]
            html = self.editor.toHtml()
            if html_signature(note.get("html")) != html_signature(html):
                note["html"] = html
                note.pop("synced_at", None)
            else:
                note["html"] = html
            save_data(self.data)

    def queue_save(self): self.save_timer.start(650) if hasattr(self,"save_timer") else None
    def format(self, fn): fn(); self.editor.setFocus(); self.queue_save()
    def bold(self): self.format(lambda:self.editor.setFontWeight(QFont.Weight.Normal if self.editor.fontWeight()>QFont.Weight.Normal else QFont.Weight.Bold))
    def italic(self): self.format(lambda:self.editor.setFontItalic(not self.editor.fontItalic()))
    def underline(self): self.format(lambda:self.editor.setFontUnderline(not self.editor.fontUnderline()))
    def strike(self): self.format(lambda:self.editor.setFontStrikeOut(not self.editor.fontStrikeOut()))

    def apply_current_note_font(self):
        """Ensure current font size and family on cursor / new line."""
        if not self.data["notes"] or self.active >= len(self.data["notes"]):
            return
        n = self.data["notes"][self.active]
        size = n.get("font_size", 16)
        family = n.get("font", "맑은 고딕")
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        fmt.setFontFamilies([family])
        self.editor.mergeCurrentCharFormat(fmt)
        self.editor.setFontPointSize(size)
        self.editor.setFontFamily(family)

    def set_font_size(self, size):
        self.data["notes"][self.active]["font_size"] = size
        font_family = self.data["notes"][self.active].get("font", "맑은 고딕")
        target_font = QFont(font_family, size)
        self.editor.setFont(target_font)
        self.editor.document().setDefaultFont(target_font)
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            fmt = QTextCharFormat()
            fmt.setFontPointSize(size)
            cursor.mergeCharFormat(fmt)
            self.editor.setTextCursor(cursor)
        else:
            cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document)
            fmt = QTextCharFormat()
            fmt.setFontPointSize(size)
            cursor.mergeCharFormat(fmt)
            cursor.clearSelection()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.editor.setTextCursor(cursor)
            cursor.endEditBlock()
        self.apply_current_note_font()
        if hasattr(self, "font_size_label"): self.font_size_label.setText(str(size))
        self.queue_save()

    def dec_font_size(self):
        cur = int(self.data["notes"][self.active].get("font_size", 16))
        SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48]
        idx = next((i for i, s in enumerate(SIZES) if s >= cur), len(SIZES)-1)
        new = SIZES[max(0, idx-1)]
        self.set_font_size(new)
        if hasattr(self, "font_size_label"): self.font_size_label.setText(str(new))
    def inc_font_size(self):
        cur = int(self.data["notes"][self.active].get("font_size", 16))
        SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48]
        idx = next((i for i, s in enumerate(SIZES) if s > cur), len(SIZES)-1)
        new = SIZES[min(len(SIZES)-1, idx)]
        self.set_font_size(new)
        if hasattr(self, "font_size_label"): self.font_size_label.setText(str(new))
    def align_center(self): self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter if self.editor.alignment()!=Qt.AlignmentFlag.AlignCenter else Qt.AlignmentFlag.AlignLeft)
    def bullet(self): self.editor.textCursor().insertList(QTextListFormat.Style.ListDisc)
    def numbered(self): self.editor.textCursor().insertList(QTextListFormat.Style.ListDecimal)
    def check_item(self): self.editor.textCursor().insertHtml("☐ ")

    def continue_check_item(self):
        """Add an unchecked checkbox after Enter from a checkbox line."""
        cursor = self.editor.textCursor()
        if not cursor.block().text().lstrip().startswith(("☐", "☑")):
            cursor.insertText("☐ ")
            self.editor.setTextCursor(cursor)
            self.apply_current_note_font()
            self.queue_save()

    def open_settings(self): SettingsDialog(self).exec()
    def create_new_note(self):
        self.save_editor(); next_idx = len(self.data["notes"]) + 1
        global_font = self.data["settings"].get("font", "맑은 고딕")
        for item in self.data["notes"]:
            item.pop("synced_at", None)
        self.data["notes"].append({"id":uuid.uuid4().hex,"title":f"NOTE {next_idx}","theme":"보라","font":global_font,"font_size":16,"tab_slot":f"{next_idx}번 칸","html":"","attachment":""})
        self.set_note(len(self.data["notes"]) - 1); save_data(self.data)
    def new_note(self):
        self.create_new_note(); SettingsDialog(self, self.active).exec()
    def delete_active_note(self):
        if len(self.data["notes"]) == 1:
            QMessageBox.information(self, "메모 삭제", "마지막 메모는 삭제할 수 없습니다. 새 메모를 만든 뒤 삭제해 주세요."); return
        note = self.data["notes"][self.active]
        answer = QMessageBox.question(self, "메모 삭제", f"‘{note['title']}’ 메모를 삭제할까요?\n이 작업은 되돌릴 수 없습니다.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        attachment = Path(note.get("attachment", ""))
        self.data["notes"].pop(self.active)
        self.data.setdefault("tombstones", {})[str(note.get("id", ""))] = True
        if attachment.is_file() and attachment.parent == ATTACH_DIR:
            try: attachment.unlink()
            except OSError: pass
        for index, item in enumerate(self.data["notes"]):
            item["tab_slot"] = f"{index+1}번 칸"
            item.pop("synced_at", None)
        self.active = max(0, min(self.active, len(self.data["notes"]) - 1)); self.set_note(self.active); save_data(self.data)
    def toggle_toolbar_lock(self): self.toolbar_locked=not self.toolbar_locked; self.ice.setStyleSheet("background:#b8e6f7;" if self.toolbar_locked else ""); self.toolbar.show()
    def enterEvent(self,event): self.timer.stop(); self.toolbar.show(); super().enterEvent(event)
    def leaveEvent(self,event):
        if self.data["settings"]["behavior"] == "마우스 조작": self.timer.start(int(self.data["settings"]["delay"]*1000))
        super().leaveEvent(event)
    def collapse(self):
        if self.toolbar_locked: return
        self.collapsed=True; self.page.hide(); self.setFixedWidth(TAB_WIDTH); self.reposition()
    def expand(self): self.collapsed=False; self.page.show(); self.setMinimumWidth(0); self.setMaximumWidth(16777215); self.reposition()
    def mouseDoubleClickEvent(self,event):
        self.expand() if self.collapsed else self.collapse()
    def update_opacity(self): self.setWindowOpacity(self.data["settings"]["opacity"]/100)
    def migrate_settings(self):
        sizes={"2×2 (작게)":"280×280 (작게)","3×3 (기본)":"360×360 (기본)","4×4 (크게)":"460×460 (크게)",
               "250×250 (작게)":"280×280 (작게)","350×350 (기본)":"360×360 (기본)","450×450 (크게)":"460×460 (크게)"}
        self.data["settings"]["size"] = sizes.get(self.data["settings"].get("size"), self.data["settings"].get("size", "360×360 (기본)"))
        self.data["settings"].pop("monitor", None)
        self.data["settings"].pop("toggle", None)
        self.data["settings"].pop("spell", None)

    def arrange_side(self):
        self.root_layout.removeWidget(self.page); self.root_layout.removeWidget(self.tab_view)
        if self.data["settings"]["position"] == "왼쪽":
            self.root_layout.addWidget(self.tab_view); self.root_layout.addWidget(self.page, 1)
        else:
            self.root_layout.addWidget(self.page, 1); self.root_layout.addWidget(self.tab_view)

    def reposition(self):
        s=self.data["settings"]; self.arrange_side(); screen=QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen(); r=screen.availableGeometry(); page_w, page_h = DIMENSIONS.get(s.get("size"), (360, 360)); w = TAB_WIDTH if self.collapsed else (page_w + TAB_WIDTH); h = page_h; x = r.left() if s["position"]=="왼쪽" else r.right()-w+1; target_y = r.top() + s["y"] if "y" in s and s["y"] is not None else r.top() + (r.height()-h)//2; min_y = r.top(); max_y = max(r.top(), r.bottom()-h+1); y = max(min_y, min(max_y, target_y)); self.setGeometry(x, y, w, h); self.update_opacity()

    def finish_drag(self, end_global: QPoint):
        screen = QApplication.screenAt(end_global) or QApplication.primaryScreen()
        r = screen.availableGeometry()
        new_pos = "왼쪽" if end_global.x() < r.center().x() else "오른쪽"
        self.data["settings"]["position"] = new_pos
        h = self.height()
        cur_y = self.drag_start_window_pos.y() + (end_global.y() - self.drag_press_global.y()) if hasattr(self, "drag_start_window_pos") and hasattr(self, "drag_press_global") else self.y()
        min_y = r.top()
        max_y = max(r.top(), r.bottom() - h + 1)
        clamped_y = max(min_y, min(max_y, cur_y))
        self.data["settings"]["y"] = clamped_y - r.top()
        save_data(self.data)
        self.reposition()

    def eventFilter(self, watched, event):
        if hasattr(self, "editor") and (watched is self.editor or watched is self.editor.viewport()):
            if (watched is self.editor and event.type() == QEvent.Type.KeyPress
                    and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))):
                if self.editor.textCursor().block().text().lstrip().startswith(("☐", "☑")):
                    QTimer.singleShot(0, self.continue_check_item)
                else:
                    QTimer.singleShot(0, self.apply_current_note_font)
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                point = event.position().toPoint()
                cursor = self.editor.cursorForPosition(point)
                target_cursor = None
                is_to_checked = False
                cr = QTextCursor(cursor)
                cr.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor, 1)
                if cr.selectedText() == "☐":
                    target_cursor = cr; is_to_checked = True
                elif cr.selectedText() == "☑":
                    target_cursor = cr; is_to_checked = False
                else:
                    cl = QTextCursor(cursor)
                    cl.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor, 1)
                    if cl.selectedText() == "☐":
                        target_cursor = cl; is_to_checked = True
                    elif cl.selectedText() == "☑":
                        target_cursor = cl; is_to_checked = False
                if target_cursor:
                    target_cursor.insertText("☑" if is_to_checked else "☐")
                    target_cursor.select(QTextCursor.SelectionType.LineUnderCursor)
                    fmt = QTextCharFormat()
                    fmt.setFontStrikeOut(is_to_checked)
                    target_cursor.mergeCharFormat(fmt)
                    self.queue_save()
                    return True
        if not hasattr(self, "tab_view") or not hasattr(self, "tabs"):
            return super().eventFilter(watched, event)
        is_tab_target = (watched in (self.tab_view, self.tab_view.viewport(), self.tabs) or 
                         (isinstance(watched, QPushButton) and (watched.property("tab_index") is not None or watched.property("is_add"))))
        if is_tab_target:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self.drag_press_global = event.globalPosition().toPoint()
                    self.drag_start_window_pos = self.pos()
                    self.drag_started = False
                    self.drag_target = watched
                    if isinstance(watched, QPushButton):
                        watched.setDown(True)
                    return True
            elif etype == QEvent.Type.MouseMove:
                if getattr(self, "drag_target", None) and (event.buttons() & Qt.MouseButton.LeftButton):
                    cur_pos = event.globalPosition().toPoint()
                    delta = cur_pos - self.drag_press_global
                    if not self.drag_started and delta.manhattanLength() >= QApplication.startDragDistance():
                        self.drag_started = True
                        if isinstance(self.drag_target, QPushButton):
                            self.drag_target.setDown(False)
                    if self.drag_started:
                        self.move(self.drag_start_window_pos + delta)
                        screen = QApplication.screenAt(cur_pos) or QApplication.primaryScreen()
                        r = screen.availableGeometry()
                        new_pos = "왼쪽" if cur_pos.x() < r.center().x() else "오른쪽"
                        if new_pos != self.data["settings"]["position"]:
                            self.data["settings"]["position"] = new_pos
                            self.arrange_side()
                        return True
            elif etype == QEvent.Type.MouseButtonRelease:
                if getattr(self, "drag_target", None) and event.button() == Qt.MouseButton.LeftButton:
                    target = self.drag_target
                    self.drag_target = None
                    if isinstance(target, QPushButton):
                        target.setDown(False)
                    if getattr(self, "drag_started", False):
                        self.drag_started = False
                        self.finish_drag(event.globalPosition().toPoint())
                        return True
                    else:
                        if isinstance(target, QPushButton):
                            idx = target.property("tab_index")
                            if idx is not None:
                                self.select_tab(idx)
                            elif target.property("is_add"):
                                self.new_note()
                        return True
        return super().eventFilter(watched, event)
    def setup_single_instance(self):
        self.server = QLocalServer(self)
        self.server.removeServer("SideMemoSingleInstance")
        self.server.listen("SideMemoSingleInstance")
        self.server.newConnection.connect(self.on_new_connection)

    def on_new_connection(self):
        sock = self.server.nextPendingConnection()
        if sock:
            sock.readyRead.connect(lambda: None)
            sock.disconnectFromServer()
        self.show(); self.raise_(); self.activateWindow()
        if self.collapsed:
            self.expand()

    def setup_tray(self):
        if not hasattr(self,"tray"): self.tray=QSystemTrayIcon(get_app_icon(),self); self.tray.activated.connect(lambda reason:self.show_and_raise() if reason==QSystemTrayIcon.ActivationReason.Trigger else None)
        else: self.tray.setIcon(get_app_icon())
        menu=QMenu(); menu.addAction("열기",self.show_and_raise); menu.addAction("새 메모",self.new_note); menu.addAction("설정",self.open_settings); menu.addSeparator(); menu.addAction("백업 내보내기",self.export_backup); menu.addAction("지금 동기화",lambda: self.sync_now(manual=True)); menu.addAction("로그인 / 계정",self.open_login); menu.addSeparator(); menu.addAction("종료",self.quit); self.tray.setContextMenu(menu); self.tray.setVisible(self.data["settings"].get("tray",True))
    def show_and_raise(self): self.show(); self.raise_(); self.activateWindow(); self.expand()
    def closeEvent(self,event): event.ignore(); self.hide(); self.tray.showMessage("SideMemo", "트레이에서 다시 열 수 있습니다.")
    def quit(self): self.save_editor(); self.unregister_hotkey(); QApplication.quit()

    # ------------------------------------------------------------- sync/cloud
    def sync_client(self) -> SupabaseClient:
        s = self.data["settings"]
        if getattr(self, "_sync_client", None) is None:
            self._sync_client = SupabaseClient(s.get("sync_url", ""), s.get("sync_key", ""), load_session())
        else:
            self._sync_client.url = (s.get("sync_url", "") or "").strip().rstrip("/")
            self._sync_client.anon_key = (s.get("sync_key", "") or "").strip()
        return self._sync_client

    def sync_sign_in(self, email: str, password: str):
        client = self.sync_client()
        session = client.sign_in(email, password)
        save_session(session)
        self.data["settings"]["sync_enabled"] = True
        self.data["settings"]["sync_email"] = email
        save_data(self.data)
        self.sync_now(manual=True, initial=True)
        return session

    def sync_sign_up(self, email: str, password: str):
        client = self.sync_client()
        try:
            session = client.sign_up(email, password)
        except SyncError as exc:
            if "가입 확인 메일" in str(exc):
                return None
            raise
        save_session(session)
        self.data["settings"]["sync_enabled"] = True
        self.data["settings"]["sync_email"] = email
        save_data(self.data)
        self.sync_now(manual=True, initial=True)
        return session

    def sync_sign_out(self):
        try:
            self.sync_client().sign_out()
        except SyncError:
            pass
        save_session({})
        self.data["settings"]["sync_enabled"] = False
        save_data(self.data)

    def open_login(self):
        if not self.data["settings"].get("sync_url") or not self.data["settings"].get("sync_key"):
            QMessageBox.information(self, "로그인",
                "먼저 설정 > 동기화에서 Supabase 주소와 anon key를 입력해 주세요.")
            return
        client = self.sync_client()
        if client.signed_in:
            answer = QMessageBox.question(self, "계정",
                f"{client.email} (으)로 로그인되어 있습니다.\n로그아웃할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.sync_sign_out()
                QMessageBox.information(self, "계정", "로그아웃했습니다.")
            return
        dlg = LoginDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(self, "계정", f"{client.email} (으)로 로그인했습니다.")

    def note_payload(self, note: dict) -> dict:
        """Serialize one note for the cloud, dropping local-only bookkeeping."""
        payload = {k: v for k, v in note.items()
                   if k not in ("synced_at", "attachment", "attachment_data")}
        if payload.get("attachment"):
            encoded = attachment_to_data_uri(str(payload["attachment"]))
            if encoded:
                payload["attachment_data"] = encoded
        return payload

    def build_push_rows(self) -> list[dict]:
        """Rows that changed locally since the last successful sync.

        A note is considered dirty when it has no ``synced_at`` stamp, which
        every local edit clears. Tombstones are pushed until the server echoes
        back a timestamp for them.
        """
        rows: list[dict] = []
        user_id = self.sync_client().user_id
        for position, note in enumerate(self.data.get("notes", [])):
            note_id = str(note.get("id") or uuid.uuid4().hex)
            note["id"] = note_id
            if note.get("synced_at"):
                continue
            if str(note_id) in self.data.get("tombstones", {}):
                continue
            rows.append({"user_id": user_id, "note_id": note_id, "position": position,
                         "payload": self.note_payload(note), "deleted": False})
        for note_id, stamp in list(self.data.get("tombstones", {}).items()):
            # True = 아직 서버가 확인하지 않은 삭제, 문자열 = 서버가 확인한 시각
            if stamp is not True:
                continue
            rows.append({"user_id": user_id, "note_id": str(note_id), "position": -1,
                         "payload": {"id": str(note_id)}, "deleted": True})
        return rows

    def confirm_pushed(self, pushed_rows: list[dict]) -> None:
        """Adopt the server timestamp for rows we just wrote."""
        by_id = {str(n.get("id")): n for n in self.data.get("notes", [])}
        for row in pushed_rows or []:
            note_id = str(row.get("note_id") or "")
            stamp = row.get("updated_at") or ""
            if not note_id or not stamp:
                continue
            if row.get("deleted"):
                self.data.setdefault("tombstones", {})[note_id] = stamp
                continue
            note = by_id.get(note_id)
            if note is not None:
                note["synced_at"] = stamp

    def merge_remote_notes(self, rows: list[dict]) -> bool:
        """Fold server rows into local state. Returns True when data changed."""
        changed = False
        by_id = {str(n.get("id")): n for n in self.data.get("notes", [])}
        fresh: list[tuple[int, str]] = []
        for row in rows or []:
            note_id = str(row.get("note_id") or "")
            if not note_id:
                continue
            remote_stamp = row.get("updated_at") or ""
            if row.get("deleted"):
                if note_id in by_id:
                    by_id.pop(note_id)
                    changed = True
                self.data.setdefault("tombstones", {})[note_id] = remote_stamp or True
                continue
            local = by_id.get(note_id)
            payload = row.get("payload") or {}
            if local is None:
                payload["id"] = note_id
                payload["synced_at"] = remote_stamp
                by_id[note_id] = payload
                fresh.append((int(row.get("position") or 0), note_id))
                changed = True
                continue
            # Local edits not yet pushed stay authoritative for this cycle.
            if not local.get("synced_at"):
                continue
            if local.get("synced_at") == remote_stamp:
                continue
            payload["id"] = note_id
            payload["synced_at"] = remote_stamp
            local_attach = str(local.get("attachment") or "")
            if local_attach and not payload.get("attachment"):
                payload["attachment"] = local_attach
            by_id[note_id] = payload
            changed = True
        if changed:
            order = [str(n.get("id")) for n in self.data.get("notes", []) if str(n.get("id")) in by_id]
            if fresh:
                distant = max(0, len(order) - len(fresh))
                order += [note_id for _, note_id in sorted(fresh, key=lambda item: (item[0] if item[0] < distant else distant, item[1]))]
            seen: set[str] = set()
            ordered: list[dict] = []
            for note_id in order:
                if note_id in seen or note_id not in by_id:
                    continue
                seen.add(note_id)
                ordered.append(by_id[note_id])
            if not ordered:
                ordered = [{"id": uuid.uuid4().hex, "title": "NOTE 1", "theme": "노랑",
                            "font": self.data["settings"].get("font", "맑은 고딕"),
                            "font_size": 16, "tab_slot": "1번 칸", "html": "", "attachment": ""}]
            for index, note in enumerate(ordered):
                note["tab_slot"] = f"{index+1}번 칸"
            self.data["notes"] = ordered
        return changed

    def merge_remote_settings(self, settings_row: dict | None) -> bool:
        """Adopt cloud settings when this PC has no newer local change."""
        if not settings_row or not isinstance(settings_row.get("payload"), dict):
            return False
        s = self.data["settings"]
        remote_stamp = settings_row.get("updated_at") or ""
        if remote_stamp and s.get("settings_synced_at") == remote_stamp:
            return False
        if not s.get("settings_synced_at") and not self.settings_are_pristine():
            return False
        changed = False
        for key in ("size", "opacity", "behavior", "delay", "checklist", "tray", "font"):
            if key in settings_row["payload"] and s.get(key) != settings_row["payload"][key]:
                s[key] = settings_row["payload"][key]
                changed = True
        s["settings_synced_at"] = remote_stamp
        return changed

    def settings_payload(self) -> dict:
        s = self.data["settings"]
        return {k: s.get(k) for k in ("size", "opacity", "behavior", "delay", "checklist", "tray", "font")}

    def sync_now(self, manual: bool = False, initial: bool = False):
        if getattr(self, "_syncing", False):
            return
        client = self.sync_client()
        if not client.configured:
            if manual:
                QMessageBox.information(self, "동기화", "설정에서 Supabase 주소와 anon key를 먼저 입력해 주세요.")
            return
        if not client.signed_in:
            if manual:
                self.open_login()
            return
        self.save_editor()
        self._syncing = True
        s = self.data["settings"]
        # The first sync on a PC adopts what is already in the cloud instead of
        # overwriting it with this machine's defaults.
        pull_only = not s.get("settings_initialized")
        push_settings = None if (pull_only or s.get("settings_synced_at")) else self.settings_payload()
        worker = SyncCycle(client, self.build_push_rows(), push_settings, None, self,
                           pull_only=pull_only)
        worker.completed.connect(lambda rows, srow, pushed, spushed: self.on_sync_done(rows, srow, pushed, spushed, initial))
        worker.failed.connect(lambda msg: self.on_sync_failed(msg, manual))
        worker.auth_lost.connect(lambda: save_session({}))
        worker.finished.connect(lambda: setattr(self, "_syncing", False))
        self._sync_worker = worker
        worker.start()

    def settings_are_pristine(self) -> bool:
        """True when the shared settings are still the stock values."""
        s = self.data["settings"]
        for key, value in DEFAULT["settings"].items():
            if key in ("sync_enabled", "sync_url", "sync_key", "sync_interval", "sync_email"):
                continue
            if s.get(key) != value:
                return False
        return True

    def notes_are_pristine(self) -> bool:
        """True when this PC still only holds the untouched starter notes."""
        notes = self.data.get("notes", [])
        if not notes or len(notes) > 4:
            return False
        for note in notes:
            text = re.sub(r"<[^>]+>", "", str(note.get("html") or "")).replace("&nbsp;", " ").strip()
            if text or note.get("attachment"):
                return False
        return True

    def on_sync_done(self, rows, settings_row, pushed_rows, pushed_settings, initial: bool):
        first_pull = not self.data["settings"].get("settings_initialized")
        self.data["settings"]["settings_initialized"] = True
        remote_notes = [r for r in (rows or []) if r.get("note_id") and not r.get("deleted")]
        if first_pull and remote_notes and self.notes_are_pristine():
            # 아직 아무것도 쓰지 않은 새 설치라면 클라우드 메모로 대체합니다.
            self.data["notes"] = []
        if first_pull and not remote_notes:
            # 클라우드가 비어 있으면 이 PC의 메모를 바로 올립니다.
            QTimer.singleShot(120, lambda: self.sync_now())
        self.confirm_pushed(pushed_rows)
        if pushed_settings and pushed_settings.get("updated_at"):
            self.data["settings"]["settings_synced_at"] = pushed_settings["updated_at"]
        changed = self.merge_remote_notes(rows)
        if not pushed_settings:
            # 방금 올린 설정을 서버의 이전 값으로 되돌리지 않습니다.
            changed = self.merge_remote_settings(settings_row) or changed
        if changed:
            if self.data["notes"]:
                self.active = max(0, min(self.active, len(self.data["notes"]) - 1))
                self.set_note(self.active)
            self.reposition(); self.update_opacity(); self.refresh_tabs()
        save_data(self.data)
        self._sync_status = f"마지막 동기화 {datetime.now().strftime('%H:%M:%S')}"
        if initial:
            QMessageBox.information(self, "동기화", "로그인했고 동기화를 시작했습니다.")

    def on_sync_failed(self, message: str, manual: bool):
        self._sync_status = f"동기화 실패: {message}"
        if manual:
            QMessageBox.warning(self, "동기화 실패", message)

    def start_sync_timer(self):
        if not hasattr(self, "sync_timer"):
            self.sync_timer = QTimer(self)
            self.sync_timer.timeout.connect(lambda: self.sync_now())
        seconds = max(15, int(self.data["settings"].get("sync_interval", 60)))
        self.sync_timer.stop()
        if self.data["settings"].get("sync_enabled") and self.sync_client().configured:
            self.sync_timer.start(seconds * 1000)
            QTimer.singleShot(1200, lambda: self.sync_now())
    def export_backup(self):
        path,_=QFileDialog.getSaveFileName(self,"백업 내보내기",str(Path.home()/"SideMemo-backup.zip"),"SideMemo 백업 (*.zip)")
        if path:
            if not path.lower().endswith(".zip"): path += ".zip"
            self.save_editor()
            with zipfile.ZipFile(path,"w",zipfile.ZIP_DEFLATED) as archive:
                archive.write(DATA_FILE,"sidememo.json")
                for note in self.data["notes"]:
                    attachment=Path(note.get("attachment", ""))
                    if attachment.is_file(): archive.write(attachment,f"attachments/{attachment.name}")
            QMessageBox.information(self,"백업 완료","메모와 첨부 이미지를 하나의 백업 파일로 저장했습니다.")
    def import_backup(self):
        path,_=QFileDialog.getOpenFileName(self,"백업 가져오기","","SideMemo 백업 (*.zip);;이전 JSON 백업 (*.json)")
        if path:
            try:
                if path.lower().endswith(".zip"):
                    with zipfile.ZipFile(path) as archive:
                        candidate=json.loads(archive.read("sidememo.json").decode("utf-8"))
                        for name in archive.namelist():
                            if name.startswith("attachments/") and not name.endswith("/"):
                                target=ATTACH_DIR / Path(name).name
                                target.write_bytes(archive.read(name))
                        for note in candidate["notes"]:
                            if note.get("attachment"): note["attachment"]=str(ATTACH_DIR / Path(note["attachment"]).name)
                else: candidate=json.loads(Path(path).read_text(encoding="utf-8"))
                assert "notes" in candidate; self.data=candidate; save_data(self.data); self.set_note(0); QMessageBox.information(self,"가져오기 완료","백업을 불러왔습니다.")
            except Exception: QMessageBox.warning(self,"가져오기 실패","올바른 SideMemo 백업 파일이 아닙니다.")
    def update_autostart(self):
        import winreg
        key=winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\Microsoft\Windows\CurrentVersion\Run",0,winreg.KEY_SET_VALUE)
        try:
            if self.data["settings"]["autostart"]: winreg.SetValueEx(key,APP_NAME,0,winreg.REG_SZ,f'"{sys.executable}"')
            else:
                try: winreg.DeleteValue(key,APP_NAME)
                except FileNotFoundError: pass
        finally: winreg.CloseKey(key)
    def register_hotkey(self):
        try: ctypes.windll.user32.RegisterHotKey(int(self.winId()),1,0x0002|0x0001,ord('M'))
        except Exception: pass
    def unregister_hotkey(self):
        try: ctypes.windll.user32.UnregisterHotKey(int(self.winId()),1)
        except Exception: pass
    def nativeEvent(self,event_type,message):
        if event_type==b"windows_generic_MSG":
            msg=ctypes.wintypes.MSG.from_address(int(message));
            if msg.message==0x0312 and msg.wParam==1: self.show_and_raise(); return True,0
        return super().nativeEvent(event_type,message)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Malgun Gothic", 10))
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(get_app_icon())

    # Check if another instance is already running
    socket = QLocalSocket()
    socket.connectToServer("SideMemoSingleInstance")
    if socket.waitForConnected(500):
        # Already running: notify existing instance to show and raise, then exit
        socket.write(b"SHOW")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        sys.exit(0)

    # First instance: launch and display sidebar immediately
    window = SideMemo()
    sys.exit(app.exec())
