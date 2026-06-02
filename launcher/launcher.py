"""
MIDI Gen Launcher
-----------------
Downloads the latest version from a private GitHub repository
and launches the Electron app. Works on Windows and macOS.

Build to exe/app with PyInstaller:
  Windows: pyinstaller --onefile --windowed --name "MIDI Gen" launcher.py
  macOS:   pyinstaller --onefile --windowed --name "MIDI Gen" launcher.py
"""

import os
import sys
import json
import shutil
import platform
import subprocess
import threading
import zipfile
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# ─── CONFIG — замени эти значения ─────────────────────────────────────────────
GITHUB_TOKEN  = "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"      # ← вставь сюда свой PAT токен
GITHUB_OWNER  = "dmitrijnovoslavskij"             # ← твой GitHub username
GITHUB_REPO   = "AI-MIDI-Generator"            # ← название репозитория
GITHUB_BRANCH = "main"                      # ← ветка (main или master)
APP_VERSION_FILE = "version.txt"            # ← файл с версией в репо (опционально)
# ──────────────────────────────────────────────────────────────────────────────

IS_WINDOWS = platform.system() == "Windows"
IS_MAC     = platform.system() == "Darwin"
ARCH       = platform.machine().lower()  # "arm64" или "x86_64"

ELECTRON_VERSION = "v28.2.0"

# Папка куда всё устанавливается — рядом с launcher'ом
if IS_WINDOWS:
    INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "MIDIGen"
else:
    INSTALL_DIR = Path.home() / "Applications" / "MIDIGen"

RUNTIME_DIR  = INSTALL_DIR / "runtime"
VENV_DIR     = RUNTIME_DIR / "venv"
ELECTRON_DIR = INSTALL_DIR / "electron"
APP_DIR      = INSTALL_DIR / "app"

PY_WIN = VENV_DIR / "Scripts" / "python.exe"
PY_MAC = VENV_DIR / "bin" / "python3"
PY_BIN = PY_WIN if IS_WINDOWS else PY_MAC

ELECTRON_WIN = ELECTRON_DIR / "electron.exe"
ELECTRON_MAC = ELECTRON_DIR / "Electron.app" / "Contents" / "MacOS" / "Electron"
ELECTRON_BIN = ELECTRON_WIN if IS_WINDOWS else ELECTRON_MAC

# ─── GitHub API helpers ───────────────────────────────────────────────────────

def github_request(url: str) -> dict:
    """Makes authenticated request to GitHub API."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {GITHUB_TOKEN}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("User-Agent", "MIDIGen-Launcher/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def download_file(url: str, dest: Path, progress_cb=None):
    """Downloads a file with progress callback(downloaded_bytes, total_bytes)."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"token {GITHUB_TOKEN}")
    req.add_header("Accept", "application/octet-stream")
    req.add_header("User-Agent", "MIDIGen-Launcher/1.0")

    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 65536  # 64KB chunks

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)


def download_repo_zip(progress_cb=None) -> Path:
    """Downloads the entire repo as a ZIP archive."""
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/zipball/{GITHUB_BRANCH}"
    dest = INSTALL_DIR / "repo.zip"
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    download_file(url, dest, progress_cb)
    return dest


def get_remote_version() -> str:
    """Gets version string from repo (reads version.txt if exists)."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{APP_VERSION_FILE}?ref={GITHUB_BRANCH}"
        data = github_request(url)
        import base64
        return base64.b64decode(data["content"]).decode().strip()
    except Exception:
        # Если version.txt нет — используем последний коммит SHA
        try:
            url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/commits/{GITHUB_BRANCH}"
            data = github_request(url)
            return data["sha"][:7]
        except Exception:
            return "unknown"


def get_local_version() -> str:
    version_file = INSTALL_DIR / "installed_version.txt"
    if version_file.exists():
        return version_file.read_text().strip()
    return ""


def save_local_version(version: str):
    (INSTALL_DIR / "installed_version.txt").write_text(version)


# ─── Setup helpers ────────────────────────────────────────────────────────────

def find_system_python() -> str | None:
    candidates = ["python3", "python"] if IS_MAC else ["python", "python3"]
    for name in candidates:
        try:
            result = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                ver = result.stdout or result.stderr
                import re
                m = re.search(r"Python (\d+)\.(\d+)", ver)
                if m and int(m.group(1)) == 3 and int(m.group(2)) >= 8:
                    return name
        except Exception:
            pass

    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        for v in ["313", "312", "311", "310", "39", "38"]:
            p = Path(local) / "Programs" / "Python" / f"Python{v}" / "python.exe"
            if p.exists():
                return str(p)
    return None


def run_silent(cmd: list, cwd=None):
    """Runs a command silently (no window on Windows)."""
    kwargs = dict(
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, **kwargs)


def kill_port_8000():
    try:
        if IS_WINDOWS:
            result = run_silent(["netstat", "-aon"])
            for line in result.stdout.splitlines():
                if ":8000" in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    if pid.isdigit():
                        run_silent(["taskkill", "/PID", pid, "/F"])
        else:
            result = run_silent(["lsof", "-ti", ":8000"])
            pid = result.stdout.strip()
            if pid:
                run_silent(["kill", "-9", pid])
    except Exception:
        pass


# ─── GUI ──────────────────────────────────────────────────────────────────────

class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("MIDI Gen")
        self.resizable(False, False)
        self.configure(bg="#0f0f0f")

        # Center window
        w, h = 480, 300
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._build_ui()
        self._thread = None

        # Start after UI is drawn
        self.after(200, self._start)

    def _build_ui(self):
        # Dark minimal design with accent color
        BG    = "#0f0f0f"
        FG    = "#e8e8e8"
        DIM   = "#555555"
        ACCENT = "#00d4ff"
        FONT_TITLE = ("Courier New", 20, "bold") if IS_WINDOWS else ("Menlo", 20, "bold")
        FONT_SUB   = ("Courier New", 9)          if IS_WINDOWS else ("Menlo", 9)
        FONT_LOG   = ("Courier New", 8)          if IS_WINDOWS else ("Menlo", 8)

        self.configure(bg=BG)

        # Top bar — title
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=32, pady=(28, 0))

        tk.Label(
            header, text="MIDI GEN", font=FONT_TITLE,
            bg=BG, fg=FG, anchor="w"
        ).pack(side="left")

        self._version_label = tk.Label(
            header, text="", font=FONT_SUB,
            bg=BG, fg=DIM, anchor="e"
        )
        self._version_label.pack(side="right", pady=(8, 0))

        # Status label
        self._status = tk.Label(
            self, text="Initializing...", font=FONT_SUB,
            bg=BG, fg=DIM, anchor="w"
        )
        self._status.pack(fill="x", padx=32, pady=(20, 6))

        # Progress bar
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor="#1e1e1e",
            background=ACCENT,
            borderwidth=0,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )

        self._progress_var = tk.DoubleVar(value=0)
        self._progress = ttk.Progressbar(
            self,
            variable=self._progress_var,
            style="Accent.Horizontal.TProgressbar",
            maximum=100,
            length=416,
            mode="determinate",
        )
        self._progress.pack(padx=32, pady=(0, 6))

        # Sub-progress label (bytes downloaded)
        self._sub_status = tk.Label(
            self, text="", font=FONT_LOG,
            bg=BG, fg=DIM, anchor="w"
        )
        self._sub_status.pack(fill="x", padx=32)

        # Log area
        log_frame = tk.Frame(self, bg="#141414", bd=0)
        log_frame.pack(fill="both", expand=True, padx=32, pady=(16, 28))

        self._log = tk.Text(
            log_frame,
            bg="#141414", fg="#444444",
            font=FONT_LOG,
            bd=0, relief="flat",
            state="disabled",
            wrap="word",
            height=5,
            cursor="arrow",
            selectbackground="#141414",
        )
        self._log.pack(fill="both", expand=True, padx=8, pady=6)
        self._log.tag_config("ok",  foreground="#00d4ff")
        self._log.tag_config("err", foreground="#ff4444")

    # ── UI helpers ────────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status.config(text=text)
        self.update_idletasks()

    def _set_progress(self, value: float, sub: str = ""):
        self._progress_var.set(value)
        self._sub_status.config(text=sub)
        self.update_idletasks()

    def _log_line(self, text: str, tag: str = ""):
        self._log.config(state="normal")
        self._log.insert("end", text + "\n", tag or ())
        self._log.see("end")
        self._log.config(state="disabled")
        self.update_idletasks()

    def _set_version(self, text: str):
        self._version_label.config(text=text)

    # ── Main flow ─────────────────────────────────────────────────────────────

    def _start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self._do_setup()
        except Exception as e:
            import traceback
            self._set_status(f"Error: {e}")
            self._log_line(traceback.format_exc(), "err")
            messagebox.showerror("MIDI Gen — Error", traceback.format_exc())

    def _do_setup(self):
        # ── 1. Check remote version ───────────────────────────────────────────
        self._set_status("Checking for updates...")
        self._set_progress(5)

        try:
            remote_ver = get_remote_version()
            local_ver  = get_local_version()
            self._set_version(f"v{remote_ver[:7]}")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise RuntimeError("GitHub token invalid or expired (401)")
            elif e.code == 404:
                raise RuntimeError(f"Repository not found: {GITHUB_OWNER}/{GITHUB_REPO}")
            raise RuntimeError(f"GitHub API error: {e.code}")
        except Exception as e:
            raise RuntimeError(f"Cannot connect to GitHub: {e}")

        needs_download = (remote_ver != local_ver) or not APP_DIR.exists()

        # ── 2. Download repo if needed ────────────────────────────────────────
        if needs_download:
            self._set_status(f"Downloading app ({remote_ver[:7]})...")
            self._log_line(f"→ Downloading {GITHUB_OWNER}/{GITHUB_REPO}@{GITHUB_BRANCH}")
            self._set_progress(10)

            def on_progress(downloaded, total):
                if total > 0:
                    pct = 10 + int((downloaded / total) * 30)
                    mb_down = downloaded / 1_048_576
                    mb_total = total / 1_048_576
                    self._set_progress(pct, f"{mb_down:.1f} MB / {mb_total:.1f} MB")
                else:
                    mb = downloaded / 1_048_576
                    self._set_progress(25, f"{mb:.1f} MB downloaded")

            zip_path = download_repo_zip(on_progress)
            self._log_line("✓ Downloaded", "ok")

            # ── 3. Extract ────────────────────────────────────────────────────
            self._set_status("Extracting...")
            self._set_progress(42)

            # Remove old app dir
            if APP_DIR.exists():
                shutil.rmtree(APP_DIR)

            # Extract ZIP — GitHub zips have a top-level folder like "owner-repo-abc1234/"
            with zipfile.ZipFile(zip_path, "r") as zf:
                all_names = zf.namelist()
                top_folder = all_names[0].split("/")[0] if all_names else ""
                zf.extractall(INSTALL_DIR / "_extract_tmp")

            # Move extracted folder to APP_DIR
            extracted = INSTALL_DIR / "_extract_tmp" / top_folder
            shutil.move(str(extracted), str(APP_DIR))
            shutil.rmtree(INSTALL_DIR / "_extract_tmp", ignore_errors=True)
            zip_path.unlink(missing_ok=True)

            save_local_version(remote_ver)
            self._log_line("✓ Extracted", "ok")
        else:
            self._log_line(f"✓ App up to date ({remote_ver[:7]})", "ok")
            self._set_progress(42)

        # ── 4. Python venv ────────────────────────────────────────────────────
        self._set_status("Checking Python environment...")
        self._set_progress(45)

        if not PY_BIN.exists():
            system_py = find_system_python()
            if not system_py:
                raise RuntimeError(
                    "Python 3.8+ not found.\n"
                    + ("Download from https://python.org (check 'Add to PATH')" if IS_WINDOWS
                       else "Install with: brew install python3")
                )
            self._log_line(f"→ Creating venv with {system_py}")
            VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
            result = run_silent([system_py, "-m", "venv", str(VENV_DIR)])
            if result.returncode != 0:
                raise RuntimeError(f"Failed to create venv:\n{result.stderr}")
            self._log_line("✓ Venv created", "ok")
        else:
            self._log_line("✓ Venv exists", "ok")

        self._set_progress(50)

        # ── 5. Python dependencies ────────────────────────────────────────────
        deps = ["fastapi", "uvicorn", "mido", "requests", "numpy", "transformers", "torch"]
        total_deps = len(deps)

        run_silent([str(PY_BIN), "-m", "pip", "install", "--upgrade", "pip", "-q"])

        for i, dep in enumerate(deps):
            check = run_silent([str(PY_BIN), "-c", f"import {dep.replace('-','_')}"])
            if check.returncode == 0:
                self._log_line(f"✓ {dep}", "ok")
            else:
                self._set_status(f"Installing {dep}...")
                self._log_line(f"→ Installing {dep}...")
                result = run_silent([str(PY_BIN), "-m", "pip", "install", "--no-cache-dir", dep, "-q"])
                if result.returncode != 0:
                    self._log_line(f"✗ {dep} failed", "err")
                else:
                    self._log_line(f"✓ {dep}", "ok")

            pct = 50 + int(((i + 1) / total_deps) * 20)
            self._set_progress(pct)

        # ── 6. Electron ───────────────────────────────────────────────────────
        self._set_status("Checking Electron...")
        self._set_progress(72)

        if not ELECTRON_BIN.exists():
            self._set_status(f"Downloading Electron {ELECTRON_VERSION}...")
            self._log_line(f"→ Downloading Electron {ELECTRON_VERSION}...")

            if IS_WINDOWS:
                zip_name = f"electron-{ELECTRON_VERSION}-win32-x64.zip"
            elif ARCH in ("arm64", "aarch64"):
                zip_name = f"electron-{ELECTRON_VERSION}-darwin-arm64.zip"
            else:
                zip_name = f"electron-{ELECTRON_VERSION}-darwin-x64.zip"

            electron_url  = f"https://github.com/electron/electron/releases/download/{ELECTRON_VERSION}/{zip_name}"
            electron_zip  = INSTALL_DIR / "electron.zip"
            ELECTRON_DIR.mkdir(parents=True, exist_ok=True)

            # Electron не требует токен — публичный репо
            def electron_progress(downloaded, total):
                if total > 0:
                    pct = 72 + int((downloaded / total) * 18)
                    mb_d = downloaded / 1_048_576
                    mb_t = total / 1_048_576
                    self._set_progress(pct, f"Electron: {mb_d:.0f} / {mb_t:.0f} MB")

            # Качаем без токена
            req = urllib.request.Request(electron_url)
            req.add_header("User-Agent", "MIDIGen-Launcher/1.0")
            downloaded_bytes = 0
            with urllib.request.urlopen(req, timeout=120) as resp:
                # Follow redirects handled automatically by urllib
                total = int(resp.headers.get("Content-Length", 0))
                with open(electron_zip, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded_bytes += len(chunk)
                        electron_progress(downloaded_bytes, total)

            with zipfile.ZipFile(electron_zip, "r") as zf:
                zf.extractall(ELECTRON_DIR)
            electron_zip.unlink(missing_ok=True)

            if not IS_WINDOWS and ELECTRON_BIN.exists():
                os.chmod(ELECTRON_BIN, 0o755)

            self._log_line("✓ Electron installed", "ok")
        else:
            self._log_line("✓ Electron exists", "ok")

        # ── 7. Init files ─────────────────────────────────────────────────────
        init_py = APP_DIR / "app" / "__init__.py"
        if not init_py.exists():
            init_py.parent.mkdir(parents=True, exist_ok=True)
            init_py.write_text("")

        (INSTALL_DIR / "midi_output").mkdir(exist_ok=True)
        (INSTALL_DIR / "models").mkdir(exist_ok=True)

        self._set_progress(95)

        # ── 8. Launch ─────────────────────────────────────────────────────────
        self._set_status("Launching...")
        self._log_line("→ Starting backend + Electron...")

        kill_port_8000()

        # Start Python backend
        backend_opts = dict(
            args=[
                str(PY_BIN), "-m", "uvicorn",
                "app.main:app",
                "--host", "127.0.0.1",
                "--port", "8000",
            ],
            cwd=str(APP_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if IS_WINDOWS:
            backend_opts["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            )
        subprocess.Popen(**backend_opts)

        # Wait for backend
        import time, socket
        for _ in range(15):
            try:
                with socket.create_connection(("127.0.0.1", 8000), timeout=1):
                    break
            except OSError:
                time.sleep(1)

        # Start Electron
        electron_main = APP_DIR / "app" / "electron_main.js"
        e_opts = dict(
            args=[str(ELECTRON_BIN), str(electron_main)],
            cwd=str(APP_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if IS_WINDOWS:
            e_opts["creationflags"] = subprocess.DETACHED_PROCESS
        subprocess.Popen(**e_opts)

        self._set_progress(100)
        self._set_status("Launched! ✓")
        self._log_line("✓ App running", "ok")

        # Закрываем launcher через 1.5 секунды
        self.after(1500, self.destroy)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()
