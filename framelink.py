"""FrameLink for Windows: launch an Android phone camera with scrcpy for OBS."""
from __future__ import annotations
import os
import shutil
import re
import json
import datetime as dt
import subprocess
import ctypes
import platform
import webbrowser
import hashlib
import threading
import urllib.error
import urllib.request
import socket
import sys
import uuid
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from license_crypto import decode_license

BG = "#0c0e12"
PANEL = "#14171d"
TEXT = "#f3f5f8"
MUTED = "#9aa3af"
LIME = "#c8fa63"
GREEN = "#8aa8ff"
LICENSE_PUBLIC_KEY = bytes.fromhex("ea187c6b65e9bff5a5ea285f47fa0aceb4dd3fbd71d678378582a70248afd524178960b4290dbf82d9fc78518d8944787981ad26379a4c3c7986a101fff2556c")
BUY_LICENSE_URL = ""
SUPPORT_EMAIL = ""

def _load_license_client_config():
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "license-client.json")
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "license-client.json")
    else:
        candidates.append(Path(__file__).resolve().with_name("license-client.json"))
    for path in candidates:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
    return {}

class LicenseServiceError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

class FrameLink(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FrameLink · Android camera for OBS")
        self.geometry("1000x900")
        self.minsize(850, 760)
        self.configure(bg=BG)
        self.license_state_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FrameLink" / "license.json"
        self.trial_state_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FrameLink" / "trial.json"
        self.remote_settings_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FrameLink" / "settings.json"
        self.license_config = _load_license_client_config()
        self.online_check_in_progress = False
        self.remote_settings = {}
        self._device_hash = None
        try: self.remote_settings = json.loads(self.remote_settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError): pass
        self.license_label = tk.StringVar(value="Free trial · 15 days")
        self._start_trial_if_needed()
        self.scrcpy_path = tk.StringVar(value=self.find_scrcpy() or "")
        self.device = tk.StringVar()
        self.facing = tk.StringVar(value="Back camera")
        self.mode = tk.StringVar(value="camera")
        self.status = tk.StringVar(value="Searching for an Android phone automatically…")
        self.device_detail = tk.StringVar(value="Searching for Android phones automatically…")
        self.camera_resolution = tk.StringVar(value="")
        self.screen_resolution = tk.StringVar(value="")
        self.record_screen = tk.BooleanVar(value=False)
        self.record_path = tk.StringVar(value=str(Path.home() / "Videos" / "FrameLink" / "FrameLink-Screen.mp4"))
        self.is_recording = False
        self.connection_text = tk.StringVar(value="NO PHONE")
        self.proc: subprocess.Popen | None = None
        self.session_mode: str | None = None
        self.devices: list[tuple[str, str, str]] = []
        self.device_preference_path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FrameLink" / "last-device.txt"
        try:
            self.preferred_device_serial = self.device_preference_path.read_text(encoding="utf-8").strip()
        except OSError:
            self.preferred_device_serial = ""
        self.camera_size_cache: dict[tuple[str, str], list[str]] = {}
        self.camera_fps_cache: dict[tuple[str, str], dict[str, set[int]]] = {}
        self.camera_fps_by_resolution: dict[str, set[int]] = {}
        self.screen_resolution_cache: dict[str, str] = {}
        self.camera_resolution_map: dict[str, str | None] = {}
        self.screen_resolution_map: dict[str, str | None] = {}
        self.flow_window = None
        self.flow_mode = None
        self._style()
        self._build()
        # Preserve the 15-day trial; require activation only after access ends.
        if not self.has_access():
            self.withdraw()
            self.after(100, self.open_license_page)
        self.after(250, self.start_adb_monitor)
        self.after(2500, self.poll_devices)
        self.after(700, self.poll_process)
        self.after(60000, self.poll_entitlement)
        self.after(1200, self.fetch_public_config)
        self.after(1800, self.online_license_check)

    @staticmethod
    def find_scrcpy():
        candidate = shutil.which("scrcpy")
        if candidate: return candidate
        roots = [
            Path(__file__).resolve().parent / "scrcpy" / "scrcpy-win64-v5.0.1",
            Path.cwd(),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "scrcpy",
        ]
        for root in roots:
            for name in ("scrcpy.exe", "scrcpy-noconsole.exe"):
                p = root / name
                if p.exists(): return str(p)
        return ""

    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=TEXT, font=("Arial", 12, "bold"))
        s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Arial", 11, "bold"))
        s.configure("Card.TLabel", background=PANEL, foreground=TEXT, font=("Arial", 12, "bold"))
        s.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED, font=("Arial", 11, "bold"))
        s.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Arial", 28, "bold"))
        s.configure("Heading.TLabel", background=BG, foreground=TEXT, font=("Arial", 18, "bold"))
        s.configure("TButton", background="#252a32", foreground=TEXT, borderwidth=0, padding=(13, 11), font=("Arial", 11, "bold"))
        s.map("TButton", background=[("active", "#343a44")])
        s.configure("Accent.TButton", background=LIME, foreground="#11150a")
        s.map("Accent.TButton", background=[("active", "#d8ff91")], foreground=[("active", "#11150a")])
        s.configure("HomeObs.TButton", background="#2563eb", foreground="#ffffff", borderwidth=0, padding=(15, 13), font=("Arial", 14, "bold"))
        s.map("HomeObs.TButton", background=[("active", "#1d4ed8")], foreground=[("active", "#ffffff")])
        s.configure("HomeScreen.TButton", background="#f97316", foreground="#ffffff", borderwidth=0, padding=(15, 13), font=("Arial", 14, "bold"))
        s.map("HomeScreen.TButton", background=[("active", "#ea580c")], foreground=[("active", "#ffffff")])
        s.configure("FlowObs.TButton", background="#2563eb", foreground="#ffffff", borderwidth=0, padding=(15, 13), font=("Arial", 14, "bold"))
        s.map("FlowObs.TButton", background=[("active", "#1d4ed8")], foreground=[("active", "#ffffff")])
        s.configure("FlowScreen.TButton", background="#f97316", foreground="#ffffff", borderwidth=0, padding=(15, 13), font=("Arial", 14, "bold"))
        s.map("FlowScreen.TButton", background=[("active", "#ea580c")], foreground=[("active", "#ffffff")])
        s.configure("TCombobox", fieldbackground="#101318", background="#101318", foreground=TEXT, arrowcolor=LIME, arrowsize=18, padding=(10, 8), font=("Arial", 11, "bold"))
        s.map("TCombobox", fieldbackground=[("readonly", "#101318"), ("focus", "#171b22")], foreground=[("readonly", TEXT)], arrowcolor=[("active", "#eaffb8"), ("focus", LIME)])
        s.configure("Refresh.TButton", background="#1c211a", foreground=LIME, borderwidth=0, padding=(7, 5), font=("Segoe UI Symbol", 15))
        s.map("Refresh.TButton", background=[("active", "#293129")], foreground=[("active", "#eaffb8")])

    def card(self, parent, title, subtitle=None):
        box = tk.Frame(parent, bg=PANEL, highlightbackground="#252c3a", highlightthickness=1, padx=20, pady=18)
        ttk.Label(box, text=title, style="Card.TLabel", font=("Arial", 13, "bold")).pack(anchor="w")
        if subtitle: ttk.Label(box, text=subtitle, style="CardMuted.TLabel", wraplength=700).pack(anchor="w", pady=(5, 14))
        return box

    def _build(self):
        self.app_root = tk.Frame(self, bg=BG)
        self.app_root.pack(fill="both", expand=True)
        root = tk.Frame(self.app_root, bg=BG, padx=28, pady=24)
        root.pack(fill="both", expand=True)
        self.home_view = root
        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", pady=(0, 28))
        tk.Label(header, text="F", bg=LIME, fg="#11150a", font=("Arial", 15, "bold"), width=3, height=1).pack(side="left", padx=(0, 12))
        brand = tk.Frame(header, bg=BG); brand.pack(side="left")
        tk.Label(brand, text="framelink", bg=BG, fg=TEXT, font=("Arial", 14, "bold")).pack(anchor="w")
        tk.Label(brand, text="ANDROID CAMERA · OBS", bg=BG, fg=MUTED, font=("Arial", 10, "bold")).pack(anchor="w")
        self.connection_badge = tk.Label(header, textvariable=self.connection_text, bg="#321f22", fg="#ff8585", font=("Arial", 10, "bold"), padx=13, pady=8)
        self.connection_badge.pack(side="right")

        tk.Label(root, text="Phone camera, ready for OBS.", bg=BG, fg=TEXT, font=("Arial", 26, "bold")).pack(anchor="w")
        tk.Label(root, text="Connect over USB and open a clean camera feed with scrcpy.", bg=BG, fg=MUTED, font=("Arial", 11, "bold")).pack(anchor="w", pady=(5, 18))

        grid = tk.Frame(root, bg=BG); grid.pack(fill="both", expand=True)
        grid.columnconfigure(0, weight=1)
        left = tk.Frame(grid, bg=BG); left.grid(row=0, column=0, sticky="nsew")

        connect = self.card(left, "Connect phone", "Enable USB debugging and approve the prompt on your Android phone.")
        connect.pack(fill="x", pady=(0, 12))
        tk.Label(connect, text="SCRCPY EXECUTABLE", bg=PANEL, fg=MUTED, font=("Arial", 9, "bold")).pack(anchor="w")
        pathrow = tk.Frame(connect, bg=PANEL); pathrow.pack(fill="x", pady=(5, 11))
        ttk.Entry(pathrow, textvariable=self.scrcpy_path).pack(side="left", fill="x", expand=True, ipady=5)
        ttk.Button(pathrow, text="Browse…", command=self.browse).pack(side="left", padx=(8, 0))
        tk.Label(connect, text="ANDROID DEVICE", bg=PANEL, fg=MUTED, font=("Arial", 9, "bold")).pack(anchor="w")
        devrow = tk.Frame(connect, bg=PANEL); devrow.pack(fill="x", pady=(5, 0))
        self.device_box = ttk.Combobox(devrow, textvariable=self.device, state="readonly")
        self.device_box.pack(side="left", fill="x", expand=True, ipady=4)
        self.device_box.bind("<<ComboboxSelected>>", self.update_selected_device_status)
        ttk.Button(devrow, text="⟳", style="Refresh.TButton", width=3, command=self.refresh_devices).pack(side="left", padx=(6, 0))

        actions = tk.Frame(left, bg=BG); actions.pack(fill="x", pady=(7, 15))
        ttk.Button(actions, text="Connect to OBS", style="HomeObs.TButton", command=self.open_camera_page).pack(side="left", fill="x", expand=True, padx=(0, 5), ipady=7)
        ttk.Button(actions, text="Mobile screen preview", style="HomeScreen.TButton", command=self.open_screen_page).pack(side="left", fill="x", expand=True, padx=(5, 0), ipady=7)

        footer = tk.Frame(root, bg=BG); footer.pack(fill="x", pady=(14, 0))
        ttk.Button(footer, textvariable=self.license_label, command=self.open_license_page).pack(side="left")
        tk.Label(footer, text="Open source · runs locally · no account", bg=BG, fg="#66707d", font=("Arial", 10, "bold")).pack(side="left")
        tk.Label(footer, text="scrcpy by Genymobile", bg=BG, fg="#66707d", font=("Arial", 10, "bold")).pack(side="right")
        self.refresh_license_label()
        self.set_connection("NO PHONE")

    def _read_license_state(self):
        try:
            return json.loads(self.license_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    def device_fingerprint(self):
        if self._device_hash: return self._device_hash
        seed = ""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            seed = str(winreg.QueryValueEx(key, "MachineGuid")[0]).strip()
            winreg.CloseKey(key)
        except (ImportError, OSError):
            pass
        if not seed:
            identity_file = self.license_state_path.parent / "device-id.txt"
            try:
                seed = identity_file.read_text(encoding="ascii").strip()
            except OSError:
                seed = uuid.uuid4().hex
                try:
                    identity_file.parent.mkdir(parents=True, exist_ok=True)
                    identity_file.write_text(seed, encoding="ascii")
                except OSError:
                    seed = platform.node() or seed
        self._device_hash = hashlib.sha256(("FrameLink-device-v1:" + seed).encode("utf-8")).hexdigest()
        return self._device_hash

    def _edge_request(self, function_name, payload=None, method="POST"):
        base = str(self.license_config.get("supabase_url", "")).rstrip("/")
        anon = str(self.license_config.get("supabase_anon_key", "")).strip()
        if not base.startswith("https://") or "YOUR_PROJECT" in base or not anon or "YOUR_SUPABASE" in anon:
            raise LicenseServiceError("Online licensing is not configured. Add the Supabase URL and public anon key to license-client.json.")
        url = f"{base}/functions/v1/{function_name}"
        data = None if method == "GET" else json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method, headers={
            "apikey": anon, "Authorization": f"Bearer {anon}", "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=18) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try: detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except (ValueError, OSError): detail = ""
            raise LicenseServiceError(detail or f"License service returned HTTP {exc.code}.", exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LicenseServiceError(f"Could not reach the license service: {exc}") from exc
        if result.get("error"):
            raise LicenseServiceError(str(result["error"]))
        return result

    def fetch_public_config(self):
        if not str(self.license_config.get("supabase_url", "")).startswith("https://") or "YOUR_PROJECT" in str(self.license_config.get("supabase_url", "")):
            return
        def worker():
            try: result = self._edge_request("public-config", method="GET"); error = None
            except LicenseServiceError as exc: result, error = None, exc
            self.after(0, lambda: self._apply_public_config(result, error))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_public_config(self, result, error=None):
        if result and isinstance(result.get("settings"), dict):
            self.remote_settings.update(result["settings"])
            try:
                self.remote_settings_path.parent.mkdir(parents=True, exist_ok=True)
                self.remote_settings_path.write_text(json.dumps(self.remote_settings), encoding="utf-8")
            except OSError: pass
            try:
                trial_state = json.loads(self.trial_state_path.read_text(encoding="utf-8"))
                trial_state["duration_days"] = max(0, int(self.remote_settings.get("trial_duration", 15)))
                self.trial_state_path.write_text(json.dumps(trial_state), encoding="utf-8")
            except (OSError, ValueError, TypeError): pass
            self.refresh_license_label()

    def online_license_check(self):
        if self.online_check_in_progress:
            self.after(300000, self.online_license_check)
            return
        state = self._read_license_state() or {}
        if not state.get("activation_token"):
            self.after(300000, self.online_license_check)
            return
        self.online_check_in_progress = True
        fingerprint = self.device_fingerprint()
        def worker():
            try: result = self._edge_request("verify", {"license_id": state.get("license_id"), "device_hash": fingerprint}); error = None
            except LicenseServiceError as exc: result, error = None, exc
            self.after(0, lambda: self._finish_online_check(result, error, state, fingerprint))
        threading.Thread(target=worker, daemon=True).start()
        self.after(300000, self.online_license_check)

    def _finish_online_check(self, result, error, old_state, fingerprint):
        self.online_check_in_progress = False
        if result and result.get("activation_token"):
            now = dt.datetime.now(dt.timezone.utc).isoformat()
            old_state.update({"activation_token": result["activation_token"], "license_id": decode_license(result["activation_token"], LICENSE_PUBLIC_KEY).get("id"),
                              "device_hash": fingerprint, "license_type": result.get("license_type"), "expires_at": result.get("expires_at"),
                              "last_verified": result.get("last_verification", now), "last_seen": now, "server_status": "active"})
            try:
                self.license_state_path.parent.mkdir(parents=True, exist_ok=True)
                self.license_state_path.write_text(json.dumps(old_state), encoding="utf-8")
            except OSError: pass
            self._apply_public_config({"settings": result.get("settings", {})})
            self.refresh_license_label()
            popup = getattr(self, "activation_window", None)
            if popup and popup.winfo_exists() and self.has_access(): self._finish_activation(popup)
        elif error and error.status in (400, 401, 403, 404, 409):
            old_state["server_status"] = "invalid"
            try: self.license_state_path.write_text(json.dumps(old_state), encoding="utf-8")
            except OSError: pass
            self.refresh_license_label()
            if not self.trial_info()["active"]:
                if self.proc and self.proc.poll() is None: self.stop_camera()
                self.status.set(str(error))
                self.open_license_page()

    def license_info(self, update_last_seen=False):
        state = self._read_license_state()
        if not state:
            return None
        try:
            now = dt.datetime.now(dt.timezone.utc)
            if state.get("activation_token"):
                if state.get("server_status") == "invalid": return None
                payload = decode_license(state["activation_token"], LICENSE_PUBLIC_KEY)
                if payload.get("device_hash") != self.device_fingerprint(): return None
                activated = dt.datetime.fromisoformat(payload["activated_at"])
                if activated.tzinfo is None: activated = activated.replace(tzinfo=dt.timezone.utc)
                lease_end = dt.datetime.fromisoformat(payload["lease_expires_at"])
                if lease_end.tzinfo is None: lease_end = lease_end.replace(tzinfo=dt.timezone.utc)
                last_seen = dt.datetime.fromisoformat(state.get("last_seen", payload.get("issued", payload["activated_at"])))
                if last_seen.tzinfo is None: last_seen = last_seen.replace(tzinfo=dt.timezone.utc)
                if now + dt.timedelta(minutes=5) < last_seen or now >= lease_end: return None
                expiry = dt.datetime.fromisoformat(payload["expires_at"]) if payload.get("expires_at") else None
                if expiry and expiry.tzinfo is None: expiry = expiry.replace(tzinfo=dt.timezone.utc)
                if update_last_seen:
                    state["last_seen"] = now.isoformat()
                    self.license_state_path.parent.mkdir(parents=True, exist_ok=True)
                    self.license_state_path.write_text(json.dumps(state), encoding="utf-8")
                return {"payload": payload, "activated": activated, "expiry": expiry}
            payload = decode_license(state["code"], LICENSE_PUBLIC_KEY)
            activated = dt.datetime.fromisoformat(state["activated_at"])
            if activated.tzinfo is None: activated = activated.replace(tzinfo=dt.timezone.utc)
            expiry = activated + dt.timedelta(days=int(payload["days"]))
            last_seen = dt.datetime.fromisoformat(state.get("last_seen", state["activated_at"]))
            if last_seen.tzinfo is None: last_seen = last_seen.replace(tzinfo=dt.timezone.utc)
            if now + dt.timedelta(minutes=5) < last_seen or now >= expiry: return None
            if update_last_seen:
                state["last_seen"] = now.isoformat()
                self.license_state_path.parent.mkdir(parents=True, exist_ok=True)
                self.license_state_path.write_text(json.dumps(state), encoding="utf-8")
            return {"payload": payload, "activated": activated, "expiry": expiry}
        except (KeyError, ValueError, TypeError, OSError):
            return None

    def _start_trial_if_needed(self):
        if self.license_info() or self.trial_state_path.exists(): return
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            self.trial_state_path.parent.mkdir(parents=True, exist_ok=True)
            self.trial_state_path.write_text(json.dumps({"started_at": now, "last_seen": now}), encoding="utf-8")
        except OSError:
            pass

    def trial_info(self, update_last_seen=False):
        try:
            state = json.loads(self.trial_state_path.read_text(encoding="utf-8"))
            started = dt.datetime.fromisoformat(state["started_at"])
            last_seen = dt.datetime.fromisoformat(state.get("last_seen", state["started_at"]))
            if started.tzinfo is None: started = started.replace(tzinfo=dt.timezone.utc)
            if last_seen.tzinfo is None: last_seen = last_seen.replace(tzinfo=dt.timezone.utc)
            now = dt.datetime.now(dt.timezone.utc)
            expiry = started + dt.timedelta(days=int(state.get("duration_days", 15)))
            if now + dt.timedelta(minutes=5) < last_seen: return {"active": False, "expiry": expiry, "days_left": 0}
            if update_last_seen and now < expiry:
                state["last_seen"] = now.isoformat()
                self.trial_state_path.write_text(json.dumps(state), encoding="utf-8")
            seconds_left = max(0, (expiry - now).total_seconds())
            return {"active": now < expiry, "expiry": expiry, "days_left": int(seconds_left + 86399) // 86400}
        except (OSError, ValueError, KeyError, TypeError):
            return {"active": False, "expiry": None, "days_left": 0}

    def has_access(self, update_last_seen=False):
        return bool(self.license_info(update_last_seen=update_last_seen) or self.trial_info(update_last_seen=update_last_seen)["active"])

    def poll_entitlement(self):
        if not self.has_access(update_last_seen=True):
            if self.proc and self.proc.poll() is None: self.stop_camera()
            if not self.trial_info()["active"]:
                self.status.set("The trial or license has expired. Activate a license to continue.")
                self.open_license_page()
        self.after(60000, self.poll_entitlement)

    def refresh_license_label(self):
        info = self.license_info(update_last_seen=True)
        if info:
            kind = info["payload"].get("license_type", "")
            label = {"monthly": "Monthly", "yearly": "Yearly", "lifetime": "Lifetime"}.get(kind, "Licensed")
            self.license_label.set(f"{label} · lifetime" if kind == "lifetime" else f"{label} · until {info['expiry'].astimezone().strftime('%Y-%m-%d')}" if info["expiry"] else "Licensed")
            return
        trial = self.trial_info(update_last_seen=True)
        if trial["active"]: self.license_label.set(f"Free trial · {trial['days_left']} days left")
        elif trial["expiry"]: self.license_label.set("Trial expired · Activate")
        else: self.license_label.set("License · Activate")

    def open_license_page(self):
        if getattr(self, "activation_window", None) and self.activation_window.winfo_exists():
            self.activation_window.lift()
            return
        window = tk.Toplevel(self)
        self.activation_window = window
        window.title("FrameLink · Activation")
        window.geometry("650x610")
        window.minsize(610, 580)
        window.resizable(False, False)
        window.configure(bg="#0b1020")
        window.transient(self)
        if not self.has_access():
            window.protocol("WM_DELETE_WINDOW", self._deny_activation_close)

        shell = tk.Frame(window, bg="#0b1020", padx=38, pady=30)
        shell.pack(fill="both", expand=True)
        head = tk.Frame(shell, bg="#0b1020")
        head.pack(fill="x", pady=(0, 25))
        tk.Label(head, text="F", bg="#2563eb", fg="white", font=("Segoe UI", 16, "bold"), width=3).pack(side="left", padx=(0, 12))
        brand = tk.Frame(head, bg="#0b1020")
        brand.pack(side="left", anchor="w")
        tk.Label(brand, text="FrameLink", bg="#0b1020", fg="#f8fafc", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(brand, text="ANDROID CAMERA · OBS", bg="#0b1020", fg="#8793aa", font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(3, 0))
        tk.Label(shell, text="Activate Your Software", bg="#0b1020", fg="#f8fafc", font=("Segoe UI", 25, "bold")).pack(anchor="w")
        tk.Label(shell, text="Enter your license key to unlock the full application.", bg="#0b1020", fg="#aab4c5", font=("Segoe UI", 11)).pack(anchor="w", pady=(8, 22))
        card = tk.Frame(shell, bg="#121a2b", highlightbackground="#26334a", highlightthickness=1, padx=22, pady=20)
        card.pack(fill="x")
        tk.Label(card, text="LICENSE KEY", bg="#121a2b", fg="#cbd5e1", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 8))
        entry = tk.Entry(card, bg="#0b1020", fg="#66758e", insertbackground="white", relief="flat", font=("Consolas", 13, "bold"), highlightthickness=1, highlightbackground="#34425a", highlightcolor="#3b82f6")
        entry.pack(fill="x", ipady=12, padx=1)
        entry.insert(0, "XXXX-XXXX-XXXX-XXXX")
        entry.bind("<FocusIn>", lambda event: self._clear_license_placeholder(entry))
        entry.bind("<FocusOut>", lambda event: self._restore_license_placeholder(entry))
        entry.bind("<Control-v>", lambda event: self.paste_license(entry))
        entry.bind("<Control-V>", lambda event: self.paste_license(entry))
        context = tk.Menu(window, tearoff=False)
        context.add_command(label="Paste", command=lambda: self.paste_license(entry))
        context.add_command(label="Copy", command=lambda: self.copy_license_text(entry))
        context.add_command(label="Select all", command=lambda: (entry.select_range(0, tk.END), entry.icursor(tk.END)))
        entry.bind("<Button-3>", lambda event: context.tk_popup(event.x_root, event.y_root))
        status = tk.Label(card, text="", bg="#121a2b", fg="#aab4c5", font=("Segoe UI", 10), anchor="w", justify="left", wraplength=500)
        status.pack(fill="x", pady=(12, 0))
        spinner = tk.Label(card, text="", bg="#121a2b", fg="#60a5fa", font=("Segoe UI", 12, "bold"))
        spinner.pack(anchor="w", pady=(3, 0))
        actions = tk.Frame(card, bg="#121a2b")
        actions.pack(fill="x", pady=(15, 0))
        tk.Button(actions, text="Paste", command=lambda: self.paste_license(entry), bg="#202c42", fg="#e2e8f0", activebackground="#2d3d58", activeforeground="white", relief="flat", bd=0, font=("Segoe UI", 10, "bold"), padx=18, pady=12, cursor="hand2").pack(side="left", padx=(0, 10))
        activate = tk.Button(actions, text="Activate", bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white", relief="flat", bd=0, font=("Segoe UI", 11, "bold"), padx=28, pady=12, cursor="hand2")
        activate.config(command=lambda: self._begin_activation(entry, window, status, spinner, activate))
        activate.pack(side="right", fill="x", expand=True)
        links = tk.Frame(shell, bg="#0b1020")
        links.pack(fill="x", pady=(19, 0))
        self._activation_link_label(links, "Buy License", "buy").pack(side="left")
        tk.Label(links, text="   ·   ", bg="#0b1020", fg="#596579").pack(side="left")
        self._activation_link_label(links, "Contact Support", "support").pack(side="left")
        tk.Label(shell, text="Your license key is verified locally on this PC.", bg="#0b1020", fg="#718096", font=("Segoe UI", 9)).pack(anchor="w", pady=(17, 0))
        window.bind("<Return>", lambda event: activate.invoke())
        entry.focus_set()

    def _deny_activation_close(self):
        messagebox.showinfo("Activation required", "Activate FrameLink to continue.", parent=self.activation_window)

    @staticmethod
    def _clear_license_placeholder(entry):
        if entry.get() == "XXXX-XXXX-XXXX-XXXX":
            entry.delete(0, tk.END)
            entry.config(fg="#f8fafc")

    @staticmethod
    def _restore_license_placeholder(entry):
        if not entry.get().strip():
            entry.insert(0, "XXXX-XXXX-XXXX-XXXX")
            entry.config(fg="#66758e")

    def _activation_link_label(self, parent, text, kind):
        label = tk.Label(parent, text=text, bg="#0b1020", fg="#60a5fa", cursor="hand2", font=("Segoe UI", 10, "underline"))
        label.bind("<Button-1>", lambda event: self._open_activation_link(kind))
        return label

    def _open_activation_link(self, kind):
        destination = (self.remote_settings.get("buy_license_url") or BUY_LICENSE_URL) if kind == "buy" else (f"mailto:{self.remote_settings.get('support_email') or SUPPORT_EMAIL}" if self.remote_settings.get("support_email") or SUPPORT_EMAIL else "")
        if destination:
            webbrowser.open(destination)
        else:
            label = "Buy License URL" if kind == "buy" else "Support email"
            messagebox.showinfo("Link not configured", f"Add the {label} in FrameLink License Console → Settings.")

    def _begin_activation(self, entry, window, status, spinner, button):
        code = entry.get().strip()
        if not code or code == "XXXX-XXXX-XXXX-XXXX":
            status.config(text="Enter or paste a license key to continue.", fg="#fca5a5")
            entry.focus_set()
            return
        button.config(state="disabled", text="Checking…")
        status.config(text="Checking your license key…", fg="#aab4c5")
        frames = ("◌", "◔", "◑", "◕")
        def animate(index=0):
            if not window.winfo_exists() or not spinner.winfo_exists(): return
            spinner.config(text=frames[index % len(frames)])
            window.after(110, animate, index + 1)
        animate()
        window.after(650, lambda: self._activate_license(code, window, status, button, spinner))

    def paste_license(self, entry):
        try:
            value = self.clipboard_get().strip()
            if not value: raise tk.TclError("Clipboard is empty")
            entry.delete(0, tk.END)
            entry.insert(0, value)
            entry.icursor(tk.END)
            entry.focus_set()
        except tk.TclError:
            messagebox.showinfo("Clipboard empty", "Copy the license code first, then press Paste.")
        return "break"

    @staticmethod
    def copy_license_text(entry):
        try:
            value = entry.selection_get()
        except tk.TclError:
            value = entry.get()
        if value:
            entry.clipboard_clear()
            entry.clipboard_append(value)

    def _activate_license(self, code, window, status=None, button=None, spinner=None):
        def worker():
            try:
                result = self._edge_request("activate", {"code": code.strip(), "device_hash": self.device_fingerprint(), "computer_name": platform.node() or "Windows PC"})
                payload = decode_license(result["activation_token"], LICENSE_PUBLIC_KEY)
                now = dt.datetime.now(dt.timezone.utc)
                state = {"activation_token": result["activation_token"], "license_id": payload["id"], "device_hash": payload["device_hash"],
                         "activated_at": payload["activated_at"], "last_verified": now.isoformat(), "last_seen": now.isoformat(),
                         "expires_at": result.get("expires_at"), "license_type": result.get("license_type"), "server_status": "active"}
                self.license_state_path.parent.mkdir(parents=True, exist_ok=True)
                self.license_state_path.write_text(json.dumps(state), encoding="utf-8")
                self.after(0, lambda: self._activation_succeeded(window, payload, result, state))
            except (LicenseServiceError, ValueError, KeyError, OSError) as exc:
                error_text = str(exc)
                self.after(0, lambda error_text=error_text: self._activation_failed(window, error_text, status, button, spinner))
        threading.Thread(target=worker, daemon=True).start()

    def _activation_failed(self, window, error, status, button, spinner):
        if not window.winfo_exists(): return
        if status: status.config(text=f"Activation failed: {error}", fg="#fca5a5")
        if spinner and spinner.winfo_exists(): spinner.config(text="")
        if button: button.config(state="normal", text="Activate")

    def _activation_succeeded(self, window, payload, result, state):
        if not window.winfo_exists(): return
        self.remote_settings.update(result.get("settings") or {})
        try:
            self.remote_settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.remote_settings_path.write_text(json.dumps(self.remote_settings), encoding="utf-8")
        except OSError: pass
        self.refresh_license_label()
        expiry = dt.datetime.fromisoformat(result["expires_at"]) if result.get("expires_at") else None
        if expiry and expiry.tzinfo is None: expiry = expiry.replace(tzinfo=dt.timezone.utc)
        self._show_activation_success(window, payload, expiry)

    def _show_activation_success(self, window, payload, expiry):
        for child in window.winfo_children(): child.destroy()
        window.geometry("650x610")
        panel = tk.Frame(window, bg="#0b1020", padx=42, pady=36)
        panel.pack(fill="both", expand=True)
        tk.Label(panel, text="✓", bg="#073b32", fg="#6ee7b7", font=("Segoe UI", 30, "bold"), width=3, height=1).pack(anchor="w", pady=(8, 20))
        tk.Label(panel, text="Activation successful", bg="#0b1020", fg="#f8fafc", font=("Segoe UI", 25, "bold")).pack(anchor="w")
        tk.Label(panel, text="FrameLink is ready. Your license is active on this device.", bg="#0b1020", fg="#aab4c5", font=("Segoe UI", 11), wraplength=530).pack(anchor="w", pady=(8, 24))
        days = int(payload.get("days", 0))
        license_type = {"monthly": "Monthly", "yearly": "Yearly", "lifetime": "Lifetime"}.get(payload.get("license_type"), f"{days}-day license")
        details = tk.Frame(panel, bg="#121a2b", highlightbackground="#26334a", highlightthickness=1, padx=20, pady=14)
        details.pack(fill="x")
        expiry_text = "Never" if payload.get("license_type") == "lifetime" or expiry is None else expiry.astimezone().strftime("%B %d, %Y")
        for title, value in (("LICENSE TYPE", license_type), ("EXPIRATION DATE", expiry_text), ("ACTIVATED DEVICE", platform.node() or "This PC")):
            row = tk.Frame(details, bg="#121a2b")
            row.pack(fill="x", pady=7)
            tk.Label(row, text=title, bg="#121a2b", fg="#8290a7", font=("Segoe UI", 8, "bold")).pack(anchor="w")
            tk.Label(row, text=value, bg="#121a2b", fg="#f1f5f9", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(3, 0))
        tk.Button(panel, text="Continue to FrameLink", command=lambda: self._finish_activation(window), bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white", relief="flat", bd=0, font=("Segoe UI", 11, "bold"), padx=24, pady=13, cursor="hand2").pack(fill="x", pady=(22, 0))
        window.after(2400, lambda: self._finish_activation(window) if window.winfo_exists() else None)

    def _finish_activation(self, window):
        window.destroy()
        self.activation_window = None
        self.deiconify()
        self.lift()

    def _build_disconnected_view(self):
        center = ttk.Frame(self.disconnected_view)
        center.pack(fill="both", expand=True, pady=(30, 45))
        icon = tk.Label(center, text="▯", bg="#111827", fg="#94a3b8", font=("Segoe UI Light", 38), width=3, height=2)
        icon.pack(pady=(0, 12))
        self.disconnected_title = ttk.Label(center, text="No device connected", style="Title.TLabel", font=("Arial", 22, "bold"))
        self.disconnected_title.pack()
        self.disconnected_copy = ttk.Label(center, text="Connect your phone by USB. FrameLink will detect it automatically when USB debugging is enabled.", style="Muted.TLabel", wraplength=440, justify="center")
        self.disconnected_copy.pack(pady=(8, 16))
        ttk.Button(center, text="Check connection now", style="Accent.TButton", command=self.connect_phone).pack(ipadx=14, ipady=2)
        ttk.Button(center, text="USB debugging help", command=self.show_debug_guide).pack(pady=(9, 0))

    def _build_connected_view(self):
        summary = self.card(self.connected_view, "", None)
        summary.pack(fill="x", pady=(4, 18))
        row = tk.Frame(summary, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="▯", bg="#102535", fg="#7dd3fc", font=("Arial", 23, "bold"), width=2, height=1).pack(side="left", padx=(0, 14))
        device_info = tk.Frame(row, bg=PANEL)
        device_info.pack(side="left", fill="x", expand=True)
        self.home_device_name = ttk.Label(device_info, text="Android phone", style="Card.TLabel", font=("Arial", 14, "bold"))
        self.home_device_name.pack(anchor="w")
        ttk.Label(device_info, text="USB  ·  scrcpy ready", style="CardMuted.TLabel").pack(anchor="w", pady=(3, 0))
        tk.Label(row, text="✓  Ready to stream", bg="#063b30", fg="#6ee7b7", font=("Arial", 10, "bold"), padx=12, pady=7).pack(side="right")

        actions = ttk.Frame(self.connected_view)
        actions.pack(fill="x")
        actions.columnconfigure(0, weight=1, uniform="action")
        actions.columnconfigure(1, weight=1, uniform="action")
        obs = self._action_card(actions, 0, "CAMERA", "Connect to OBS", "Use your phone camera as a video source. Pick a resolution and start streaming.", "Camera", "sky", self.open_camera_page)
        screen = self._action_card(actions, 1, "SCREEN", "Mobile screen preview", "Mirror your phone screen with scrcpy and preview it live.", "Screen", "emerald", self.open_screen_page)
        return obs, screen

    def _action_card(self, parent, column, eyebrow, title, description, badge, accent, command):
        card = tk.Frame(parent, bg=PANEL, highlightbackground="#252c3a", highlightthickness=1, padx=20, pady=18)
        card.grid(row=0, column=column, sticky="nsew", padx=(0, 9) if column == 0 else (9, 0))
        color = "#7dd3fc" if accent == "sky" else "#6ee7b7"
        tint = "#102535" if accent == "sky" else "#102b28"
        tk.Label(card, text="◉" if accent == "sky" else "▣", bg=tint, fg=color, font=("Arial", 21, "bold"), width=2, height=1).pack(anchor="w", pady=(0, 12))
        tk.Label(card, text=eyebrow, bg=PANEL, fg=color, font=("Arial", 9, "bold")).pack(anchor="w")
        tk.Label(card, text=title, bg=PANEL, fg="#f8fafc", font=("Arial", 17, "bold")).pack(anchor="w", pady=(4, 0))
        tk.Label(card, text=description, bg=PANEL, fg="#94a3b8", font=("Arial", 11, "bold"), wraplength=350, justify="left").pack(anchor="w", pady=(7, 14))
        ttk.Button(card, text="Configure  →", style="Accent.TButton" if accent == "sky" else "TButton", command=command).pack(fill="x", ipady=4)
        return card

    def show_debug_guide(self):
        guide = tk.Toplevel(self)
        guide.title("Connect Android phone")
        guide.configure(bg=BG)
        guide.transient(self)
        guide.grab_set()
        guide.geometry("500x475")
        guide.resizable(False, False)
        body = tk.Frame(guide, bg=PANEL, highlightbackground="#252c3a", highlightthickness=1, padx=24, pady=22)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        tk.Label(body, text="Enable USB debugging", bg=PANEL, fg="#f8fafc", font=("Arial", 18, "bold")).pack(anchor="w")
        tk.Label(body, text="Follow these steps on your Android phone, then refresh the device list.", bg=PANEL, fg="#94a3b8", font=("Arial", 11, "bold"), wraplength=410, justify="left").pack(anchor="w", pady=(5, 14))
        steps = [
            ("1", "Open Settings", "Go to About phone and tap Build number seven times to enable Developer options."),
            ("2", "Enable debugging", "Open Developer options and turn on USB debugging."),
            ("3", "Approve the computer", "Connect by USB, unlock the phone, and tap Allow on the debugging prompt."),
        ]
        for n, title, description in steps:
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", pady=7)
            tk.Label(row, text=n, bg="#202938", fg="#cbd5e1", font=("Arial", 11, "bold"), width=3, height=2).pack(side="left", padx=(0, 11))
            words = tk.Frame(row, bg=PANEL); words.pack(side="left", fill="x", expand=True)
            tk.Label(words, text=title, bg=PANEL, fg="#f8fafc", font=("Arial", 11, "bold")).pack(anchor="w")
            tk.Label(words, text=description, bg=PANEL, fg="#94a3b8", font=("Arial", 10, "bold"), wraplength=350, justify="left").pack(anchor="w", pady=(2, 0))
        ttk.Button(body, text="Refresh devices", style="Accent.TButton", command=lambda: (guide.destroy(), self.refresh_devices())).pack(fill="x", pady=(18, 0), ipady=3)

    def connect_phone(self):
        """Try the real ADB connection immediately; show guidance only if needed."""
        self.status.set("Checking for Android phones…")
        self.update_idletasks()
        self.refresh_devices()
        index = self.device_box.current()
        if 0 <= index < len(self.devices):
            serial, state, model = self.devices[index]
            if state == "device":
                self.set_connection("CONNECTED", model)
                self.status.set(f"{model} connected. Choose Camera to OBS or Mobile screen preview.")
            elif state == "unauthorized":
                self.set_connection("AUTHORIZE PHONE", model)
                self.status.set(f"{model} is detected. Unlock the phone and approve the USB debugging prompt; FrameLink will connect automatically.")
            else:
                self.set_connection("PHONE OFFLINE", model)
                self.status.set(f"{model} is detected but offline. Reconnect USB; FrameLink will retry automatically.")
        else:
            self.status.set("No authorized phone detected yet. Connect by USB and approve Android's USB debugging prompt; FrameLink keeps checking automatically.")

    def start_adb_monitor(self):
        """Start the bundled ADB server once, then begin automatic USB polling."""
        adb = self.adb_path()
        if adb:
            try:
                subprocess.run([adb, "start-server"], capture_output=True, text=True,
                               timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               env=self.android_env())
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.refresh_devices()

    def open_flow_window(self, mode):
        if self.flow_window and self.flow_window.winfo_exists():
            if self.proc and self.proc.poll() is None:
                self.flow_mode = self.session_mode
                self._render_flow_running()
            else:
                self.flow_mode = mode
                self._render_flow_configuration()
            self.home_view.pack_forget()
            self.flow_window.pack(fill="both", expand=True)
            return
        self.flow_mode = mode
        self.flow_fps = tk.StringVar(value="30")
        self.flow_window = tk.Frame(self.app_root, bg=BG, padx=28, pady=24)
        self.home_view.pack_forget()
        self.flow_window.pack(fill="both", expand=True)
        head = tk.Frame(self.flow_window, bg=BG); head.pack(fill="x")
        ttk.Button(head, text="← Back", command=self.close_flow_window).pack(side="left", padx=(0, 14))
        tk.Label(head, text="F", bg=LIME, fg="#11150a", font=("Arial", 14, "bold"), width=2, height=1).pack(side="left", padx=(0, 11))
        titles = tk.Frame(head, bg=BG); titles.pack(side="left", fill="x", expand=True)
        ttk.Label(titles, text="Connect phone", font=("Arial", 16, "bold")).pack(anchor="w")
        self.flow_subtitle = ttk.Label(titles, text="", style="Muted.TLabel")
        self.flow_subtitle.pack(anchor="w", pady=(2, 0))

        self.flow_body = tk.Frame(self.flow_window, bg=BG)
        self.flow_body.pack(fill="both", expand=True, pady=(24, 0))
        self._render_flow_configuration()

    def _render_flow_configuration(self):
        for child in self.flow_body.winfo_children(): child.destroy()
        is_camera = self.flow_mode == "camera"
        accent = "sky" if is_camera else "emerald"
        self.flow_subtitle.configure(text="Choose camera resolution and frame rate, then connect." if is_camera else "Use native phone display resolution and choose frame rate.")
        panel = self.card(self.flow_body, "RESOLUTION", "Choose a mode supported by the selected phone.")
        panel.pack(fill="x")
        if is_camera:
            lens_row = tk.Frame(panel, bg=PANEL); lens_row.pack(fill="x", pady=(0, 12))
            ttk.Label(lens_row, text="CAMERA LENS", style="CardMuted.TLabel").pack(side="left", padx=(0, 12))
            self.camera_box = ttk.Combobox(lens_row, textvariable=self.facing, values=("Back camera", "Front camera"), state="readonly", width=18)
            self.camera_box.pack(side="left", fill="x", expand=True)
            self.camera_box.bind("<<ComboboxSelected>>", self.camera_facing_changed)
            ttk.Label(panel, text="CAMERA RESOLUTION", style="CardMuted.TLabel").pack(anchor="w", pady=(0, 6))
            self.camera_resolution_panel = tk.Frame(panel, bg=PANEL)
            self.camera_resolution_panel.pack(fill="x")
            self.camera_size_hint = ttk.Label(panel, text="Reading supported camera modes…", style="CardMuted.TLabel", wraplength=480)
            self.camera_size_hint.pack(anchor="w", pady=(7, 0))
            self.load_camera_resolutions()
        else:
            tk.Label(panel, text="NATIVE PHONE DISPLAY", bg=PANEL, fg=LIME, font=("Arial", 12, "bold")).pack(anchor="w", pady=(2, 5))
            ttk.Label(panel, text="Uses the phone’s own screen resolution automatically. No upscaling or resolution choice.", style="CardMuted.TLabel", wraplength=480).pack(anchor="w")
            self.load_screen_resolutions()
            record_card = self.card(self.flow_body, "SCREEN RECORDING", "Save a high-quality MP4 recording while previewing the phone.")
            record_card.pack(fill="x", pady=(12, 0))
            ttk.Checkbutton(record_card, text="Record when I connect", variable=self.record_screen).pack(anchor="w", pady=(4, 10))
            path_row = tk.Frame(record_card, bg=PANEL)
            path_row.pack(fill="x")
            ttk.Entry(path_row, textvariable=self.record_path).pack(side="left", fill="x", expand=True, ipady=5)
            ttk.Button(path_row, text="Choose file…", command=self.choose_record_path).pack(side="left", padx=(8, 0))
            ttk.Label(record_card, text="High-quality capture · 20 Mbps · MP4", style="CardMuted.TLabel").pack(anchor="w", pady=(8, 0))

        fps_card = self.card(self.flow_body, "FRAME RATE")
        fps_card.pack(fill="x", pady=(12, 0))
        fps_row = tk.Frame(fps_card, bg=PANEL); fps_row.pack(fill="x", pady=(8, 0))
        self.fps_buttons = []
        if is_camera:
            fps_values = sorted(self.camera_fps_by_resolution.get(self.camera_resolution.get(), {30}) & {30, 60})
            if not fps_values: fps_values = [30]
        else:
            fps_values = [30, 60]
        if self.flow_fps.get() not in {str(fps) for fps in fps_values}:
            self.flow_fps.set("30" if 30 in fps_values else str(fps_values[0]))
        for fps in fps_values:
            btn = tk.Button(fps_row, text=f"{fps} fps", command=lambda value=fps: self.select_flow_fps(value), relief="flat", bd=0, padx=16, pady=9, font=("Arial", 11, "bold"), cursor="hand2")
            btn.pack(side="left", fill="x", expand=True, padx=(0, 7))
            self.fps_buttons.append((fps, btn))
        self.select_flow_fps(int(self.flow_fps.get()))
        self.flow_start_btn = ttk.Button(self.flow_body, text="Connect", style="FlowObs.TButton" if is_camera else "FlowScreen.TButton", command=self.start_selected_flow)
        self.flow_start_btn.pack(fill="x", pady=(14, 0), ipady=5)
        if is_camera and not self.camera_resolution_map:
            self.flow_start_btn.configure(state="disabled")

    def select_flow_mode(self, mode):
        if self.proc and self.proc.poll() is None:
            return
        self.flow_mode = mode
        self.mode.set(mode)
        self._render_flow_configuration()

    def camera_facing_changed(self, _event=None):
        self.load_camera_resolutions()
        if self.flow_window and self.flow_window.winfo_exists():
            self._render_flow_configuration()

    def select_flow_fps(self, fps):
        self.flow_fps.set(str(fps))
        for value, btn in getattr(self, "fps_buttons", []):
            selected = value == fps
            color = "#0c2a3d" if selected and self.flow_mode == "camera" else "#0b3027" if selected else "#0d1119"
            fg = "#7dd3fc" if selected and self.flow_mode == "camera" else "#6ee7b7" if selected else "#cbd5e1"
            btn.configure(bg=color, fg=fg, activebackground=color, activeforeground=fg, highlightthickness=1, highlightbackground="#0ea5e9" if selected and self.flow_mode == "camera" else "#10b981" if selected else "#252c3a")

    def start_selected_flow(self):
        if self.session_mode == self.flow_mode and self.proc and self.proc.poll() is None:
            self.stop_camera()
            return
        if self.flow_mode == "camera": self.start_camera()
        else: self.start_screen_preview()

    def _render_flow_running(self):
        if not self.flow_window or not self.flow_window.winfo_exists(): return
        for child in self.flow_body.winfo_children(): child.destroy()
        is_camera = self.session_mode == "camera"
        color, tint = ("#7dd3fc", "#0c2a3d") if is_camera else ("#6ee7b7", "#0b3027")
        preview = tk.Frame(self.flow_body, bg="#05070c", highlightbackground="#252c3a", highlightthickness=1, height=230)
        preview.pack(fill="x")
        preview.pack_propagate(False)
        tk.Label(preview, text="✓", bg="#05070c", fg=color, font=("Arial", 31, "bold")).place(relx=0.5, rely=0.38, anchor="center")
        tk.Label(preview, text="scrcpy is running in its own window", bg="#05070c", fg="#cbd5e1", font=("Arial", 13, "bold")).place(relx=0.5, rely=0.58, anchor="center")
        index = self.device_box.current()
        model = self.devices[index][2] if 0 <= index < len(self.devices) else "Android phone"
        stats = tk.Frame(self.flow_body, bg=BG); stats.pack(fill="x", pady=(12, 0))
        summary = [("DEVICE", model)]
        if is_camera:
            summary.append(("CAMERA MODE", self.camera_resolution.get() or "Automatic"))
        summary.append(("FRAME RATE", f"{self.flow_fps.get()} fps"))
        for label, value in summary:
            cell = tk.Frame(stats, bg="#0d1119", highlightbackground="#252c3a", highlightthickness=1, padx=11, pady=9)
            cell.pack(side="left", fill="x", expand=True, padx=(0, 7))
            tk.Label(cell, text=label, bg="#0d1119", fg="#64748b", font=("Arial", 9, "bold")).pack(anchor="w")
            tk.Label(cell, text=value, bg="#0d1119", fg="#f8fafc", font=("Arial", 10, "bold"), wraplength=135).pack(anchor="w", pady=(4, 0))
        tk.Label(self.flow_body, text="✓  Stream is running. Add the scrcpy window as a Window Capture source in OBS.", bg=tint, fg=color, font=("Arial", 11, "bold"), anchor="w", padx=13, pady=11, wraplength=500, justify="left").pack(fill="x", pady=(12, 0))
        ttk.Button(self.flow_body, text="Disconnect", style="Accent.TButton", command=self.stop_camera).pack(fill="x", pady=(10, 0), ipady=5)

    def close_flow_window(self):
        if self.flow_window and self.flow_window.winfo_exists(): self.flow_window.pack_forget()
        if self.home_view and self.home_view.winfo_exists(): self.home_view.pack(fill="both", expand=True)

    def open_camera_page(self):
        if not self.has_access(update_last_seen=True):
            self.open_license_page()
            return
        if not self._get_connected_serial(): return
        self.mode.set("camera")
        self.open_flow_window("camera")

    def open_screen_page(self):
        if not self.has_access(update_last_seen=True):
            self.open_license_page()
            return
        if not self._get_connected_serial(): return
        self.mode.set("display")
        self.open_flow_window("display")

    def browse(self):
        path = filedialog.askopenfilename(title="Select scrcpy.exe", filetypes=[("scrcpy", "scrcpy.exe"), ("Programs", "*.exe")])
        if path: self.scrcpy_path.set(path); self.refresh_devices()

    def adb_path(self):
        exe = Path(self.scrcpy_path.get()).resolve()
        local = exe.parent / "adb.exe"
        return str(local) if local.exists() else shutil.which("adb")

    @staticmethod
    def android_env():
        env = os.environ.copy()
        # Keep ADB's keys/config in a writable, app-specific Windows location.
        data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FrameLink" / "android"
        data.mkdir(parents=True, exist_ok=True)
        env["ANDROID_USER_HOME"] = str(data)
        # Older Android tools use ANDROID_SDK_HOME as the parent of .android.
        env["ANDROID_SDK_HOME"] = str(data.parent)
        # Keep HOME compatible with tools that use it, without changing USERPROFILE.
        env["HOME"] = str(data.parent)
        return env

    def refresh_devices(self):
        adb = self.adb_path()
        if not adb:
            self.device_box["values"] = ("scrcpy not found",)
            self.device.set("scrcpy not found")
            self.device_detail.set("The bundled scrcpy could not be found. Choose its location in the source folder.")
            self.set_connection("NO PHONE")
            self.status.set("scrcpy is missing. Use Browse to select scrcpy.exe.")
            return
        try:
            result = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=self.android_env())
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip().splitlines()
                reason = detail[-1] if detail else f"ADB exited with code {result.returncode}"
                self.device_box["values"] = ("ADB could not start",)
                self.device.set("ADB could not start")
                self.set_connection("ADB ERROR")
                self.device_detail.set(reason)
                self.status.set(f"ADB error: {reason}. Close other ADB tools, then restart FrameLink.")
                return
            self.devices = []
            for line in result.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] in ("device", "unauthorized", "offline"):
                    serial, state = parts[0], parts[1]
                    model = next((p.split(":", 1)[1].replace("_", " ") for p in parts[2:] if p.startswith("model:")), serial)
                    self.devices.append((serial, state, model))
            choices = [f"{model} · {serial}" + (" · allow USB debugging" if state == "unauthorized" else " · reconnect USB" if state == "offline" else "") for serial, state, model in self.devices]
            self.device_box["values"] = choices or ("No phone detected",)
            if choices:
                # Re-select the last used authorized phone when available;
                # otherwise choose the first ready USB phone automatically.
                selected = next((i for i, item in enumerate(self.devices)
                                 if item[0] == self.preferred_device_serial and item[1] == "device"), None)
                if selected is None:
                    selected = next((i for i, item in enumerate(self.devices) if item[1] == "device"), 0)
                self.device_box.current(selected)
                self.update_selected_device_status()
            else:
                self.device.set("No phone detected")
                self.set_connection("NO PHONE")
                self.device_detail.set("Connect your Android phone with a USB data cable, unlock it, then refresh.")
                if not self.proc: self.status.set("Waiting for a phone… Connect it by USB; FrameLink checks automatically every few seconds.")
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.set_connection("ADB ERROR")
            self.status.set(f"Could not run ADB: {exc}")

    def update_selected_device_status(self, _event=None):
        index = self.device_box.current()
        if index < 0 or index >= len(self.devices):
            return
        serial, state, model = self.devices[index]
        if state == "device":
            self.preferred_device_serial = serial
            try:
                self.device_preference_path.parent.mkdir(parents=True, exist_ok=True)
                self.device_preference_path.write_text(serial, encoding="utf-8")
            except OSError:
                pass
            self.set_connection("CONNECTED", model)
            self.device_detail.set(f"{model} is connected and ready.")
            if not self.proc:
                self.status.set(f"{model} is connected. Choose an action to continue.")
            if self.flow_window and self.flow_window.winfo_exists():
                if self.flow_mode == "camera": self.load_camera_resolutions()
                elif self.flow_mode == "display": self.load_screen_resolutions()
        elif state == "unauthorized":
            self.set_connection("AUTHORIZE PHONE", model)
            self.device_detail.set(f"{model} found. Approve the USB debugging prompt on the phone.")
            self.status.set(f"PHONE DETECTED: {model}. Unlock it and tap Allow on the USB debugging prompt.")
        else:
            self.set_connection("PHONE OFFLINE", model)
            self.device_detail.set(f"{model} is detected but offline. Reconnect the USB cable.")
            self.status.set(f"PHONE DETECTED: {model}, but it is offline. Reconnect USB and refresh.")

    def load_camera_resolutions(self, _event=None):
        index = self.device_box.current()
        if index < 0 or index >= len(self.devices) or self.devices[index][1] != "device":
            self.camera_resolution_map = {}
            self.camera_fps_by_resolution = {}
            if hasattr(self, "home_quality_box"):
                self.home_quality_box.configure(values=("Connect phone to read camera modes",))
                self.home_quality.set("Connect phone to read camera modes")
            if self.flow_window and self.flow_window.winfo_exists():
                self.camera_size_hint.configure(text="Connect and authorize a phone to read its camera modes.")
                self.render_resolution_cards(self.camera_resolution_panel, self.camera_resolution, {}, "sky")
            return
        serial = self.devices[index][0]
        facing = "back" if self.facing.get().startswith("Back") else "front"
        key = (serial, facing)
        if key not in self.camera_size_cache:
            executable = Path(self.scrcpy_path.get())
            args = [str(executable), f"--serial={serial}", "--video-source=camera", f"--camera-facing={facing}", "--list-camera-sizes"]
            try:
                result = subprocess.run(args, cwd=str(executable.parent), capture_output=True, text=True, timeout=12, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=self.android_env())
                reported = result.stdout + "\n" + result.stderr
                sizes, fps_by_size = [], {}
                target_camera_found = False
                in_target_camera = False
                supported_fps = {30}
                if result.returncode == 0:
                    for line in reported.splitlines():
                        header = re.search(r"--camera-id=\d+\s+\((back|front),\s*(\d+)x(\d+),\s*fps=\[([^\]]+)\]", line, re.IGNORECASE)
                        if header:
                            if not target_camera_found and header.group(1).lower() == facing:
                                target_camera_found = True
                                in_target_camera = True
                                supported_fps = {int(value) for value in re.findall(r"\d+", header.group(4))}
                                top_size = f"{header.group(2)}x{header.group(3)}"
                                sizes.append(top_size)
                                fps_by_size[top_size] = set(supported_fps)
                            else:
                                in_target_camera = False
                            continue
                        if in_target_camera:
                            for width, height in re.findall(r"(?<!\d)(\d{3,5})\s*x\s*(\d{3,5})(?!\d)", line):
                                w, h = int(width), int(height)
                                if w >= 640 and h >= 480:
                                    size = f"{w}x{h}"
                                    if size not in sizes: sizes.append(size)
                                    fps_by_size[size] = set(supported_fps)
                    # Older scrcpy builds can list sizes without camera headers.
                    if not sizes:
                        for width, height in re.findall(r"(?<!\d)(\d{3,5})\s*x\s*(\d{3,5})(?!\d)", reported):
                            w, h = int(width), int(height)
                            if w >= 640 and h >= 480:
                                size = f"{w}x{h}"
                                if size not in sizes: sizes.append(size)
                                fps_by_size[size] = {30}
                sizes.sort(key=lambda item: int(item.split("x")[0]) * int(item.split("x")[1]), reverse=True)
                self.camera_size_cache[key] = sizes
                self.camera_fps_cache[key] = fps_by_size
            except (OSError, subprocess.TimeoutExpired):
                self.camera_size_cache[key] = []
                self.camera_fps_cache[key] = {}
        sizes = self.camera_size_cache[key]
        self.camera_resolution_map = {}
        self.camera_fps_by_resolution = {}
        qualified = {"1080p Full HD": [], "4K Ultra HD": []}
        for size in sizes:
            width, height = (int(value) for value in size.split("x"))
            long_side, short_side = max(width, height), min(width, height)
            if long_side >= 3840 and short_side >= 2160:
                qualified["4K Ultra HD"].append(size)
            elif 1920 <= long_side <= 2560 and 1080 <= short_side <= 1200:
                qualified["1080p Full HD"].append(size)
        for label, modes in qualified.items():
            if modes:
                actual = max(modes, key=lambda item: int(item.split("x")[0]) * int(item.split("x")[1]))
                self.camera_resolution_map[label] = actual
                self.camera_fps_by_resolution[label] = set(self.camera_fps_cache.get(key, {}).get(actual, {30}))
        if self.camera_resolution_map:
            options = list(self.camera_resolution_map)
            if self.flow_window and self.flow_window.winfo_exists(): self.camera_size_hint.configure(text="Only supported Full HD and 4K modes are shown. 60 fps appears only when reported by the phone.")
        else:
            options = []
            if self.flow_window and self.flow_window.winfo_exists(): self.camera_size_hint.configure(text="This camera did not report a supported 1080p or 4K mode.")
        if self.camera_resolution.get() not in options: self.camera_resolution.set(options[0])
        if hasattr(self, "home_quality_box"):
            labels = list(self.camera_resolution_map)
            self.home_quality_box.configure(values=labels or ("No supported 1080p/4K mode",))
            if labels and self.home_quality.get() not in labels: self.home_quality.set(labels[0])
            elif not labels: self.home_quality.set("No supported 1080p/4K mode")
        if self.flow_window and self.flow_window.winfo_exists():
            self.render_resolution_cards(self.camera_resolution_panel, self.camera_resolution, self.camera_resolution_map, "sky")

    def load_screen_resolutions(self):
        index = self.device_box.current()
        if index < 0 or index >= len(self.devices) or self.devices[index][1] != "device":
            self.screen_resolution_map = {}
            return
        serial = self.devices[index][0]
        self.screen_resolution_map = {"Native · phone resolution": None}
        self.screen_resolution.set("Native · phone resolution")

    def render_resolution_cards(self, parent, variable, resolution_map, accent):
        for child in parent.winfo_children(): child.destroy()
        selected_bg, selected_border, selected_text = (
            ("#102232", "#0ea5e9", "#7dd3fc") if accent == "sky"
            else ("#102621", "#10b981", "#6ee7b7")
        )
        parent.columnconfigure(0, weight=1)
        for row, label in enumerate(resolution_map):
            actual = resolution_map[label]
            selected = variable.get() == label
            bg = selected_bg if selected else "#0d1119"
            border = selected_border if selected else "#252c3a"
            tile = tk.Frame(parent, bg=bg, highlightbackground=border, highlightthickness=1, padx=14, pady=10, cursor="hand2")
            tile.grid(row=row, column=0, sticky="ew", pady=4)
            left = tk.Frame(tile, bg=bg); left.pack(side="left", fill="x", expand=True)
            radio = tk.Label(left, text="●" if selected else "○", bg=bg, fg=selected_text if selected else "#64748b", font=("Arial", 14, "bold"))
            radio.pack(side="left", padx=(0, 11))
            words = tk.Frame(left, bg=bg); words.pack(side="left", fill="x", expand=True)
            if actual is None and label.startswith("Best available"):
                title, note, dimensions = "Best available", "Highest mode from the phone camera", "AUTO"
            elif label.startswith("Native"):
                title, note, dimensions = "Native", "Phone display default", label.partition("·")[2].strip()
            elif "maximum" in label:
                title = label.replace(" maximum", "")
                note = "Limit output size"
                dimensions = label.split()[0] + " px max"
            elif actual and "x" in actual:
                w, h = (int(v) for v in actual.split("x"))
                tier = "4K UHD" if label.startswith("4K") else "1080p FHD"
                title, dimensions = tier, f"{w} × {h}"
                note = "Recommended" if row == 1 else ("Highest quality" if row == 0 else "")
            else:
                title, note, dimensions = label, "", label
            tk.Label(words, text=title, bg=bg, fg="#f8fafc", font=("Arial", 12, "bold"), anchor="w").pack(anchor="w")
            if note: tk.Label(words, text=note, bg=bg, fg="#94a3b8", font=("Arial", 10, "bold"), anchor="w").pack(anchor="w", pady=(2, 0))
            tk.Label(tile, text=dimensions, bg=bg, fg=selected_text if selected else "#64748b", font=("Arial", 10, "bold"), anchor="e").pack(side="right", padx=(10, 0))
            def choose(_event=None, choice=label):
                variable.set(choice)
                if variable is self.camera_resolution:
                    self.after_idle(self._render_flow_configuration)
                else:
                    self.render_resolution_cards(parent, variable, resolution_map, accent)
            tile.bind("<Button-1>", choose)
            left.bind("<Button-1>", choose)
            radio.bind("<Button-1>", choose)
            words.bind("<Button-1>", choose)
            for child in words.winfo_children(): child.bind("<Button-1>", choose)

    def start_camera(self):
        if self.session_mode == "camera" and self.proc and self.proc.poll() is None:
            self.stop_camera()
            return
        if self.camera_resolution.get() not in self.camera_resolution_map:
            messagebox.showwarning("Camera mode unavailable", "This phone did not report a supported Full HD or 4K mode for the selected camera.")
            return
        size = self.camera_resolution_map[self.camera_resolution.get()]
        self.launch_session("camera", resolution=size, session_kind="camera")

    def start_screen_preview(self):
        if self.session_mode == "display" and self.proc and self.proc.poll() is None:
            self.stop_camera()
            return
        size = self.screen_resolution_map.get(self.screen_resolution.get())
        record_file = None
        if self.record_screen.get():
            raw_path = self.record_path.get().strip()
            if not raw_path:
                messagebox.showwarning("Choose a save location", "Choose where to save the screen recording.")
                return
            record_file = Path(raw_path).expanduser()
            if record_file.suffix.lower() != ".mp4":
                record_file = record_file.with_suffix(".mp4")
            try:
                record_file.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("Cannot use save location", str(exc))
                return
            self.record_path.set(str(record_file))
        self.is_recording = bool(record_file)
        self.launch_session("display", resolution=size, session_kind="display", record_file=record_file)

    def choose_record_path(self):
        folder = Path(self.record_path.get()).expanduser().parent
        if not folder.is_dir():
            folder = Path.home() / "Videos"
        if not folder.is_dir():
            folder = Path.home()
        path = filedialog.asksaveasfilename(
            title="Save phone screen recording",
            initialdir=str(folder),
            initialfile=Path(self.record_path.get()).name,
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4")],
        )
        if path:
            self.record_path.set(path)

    def update_action_buttons(self):
        if self.proc and self.proc.poll() is None:
            self._render_flow_running()
        elif self.flow_window and self.flow_window.winfo_exists():
            self._render_flow_configuration()

    def set_connection(self, label, model=None):
        colors = {
            "CONNECTED": ("#1e3020", LIME),
            "NO PHONE": ("#321f22", "#ff8585"),
            "AUTHORIZE PHONE": ("#3b321b", "#ffd166"),
            "PHONE OFFLINE": ("#3b321b", "#ffd166"),
            "ADB ERROR": ("#321f22", "#ff8585"),
        }
        display = {
            "CONNECTED": f"{model} · CONNECTED" if model else "CONNECTED",
            "AUTHORIZE PHONE": f"PHONE FOUND · ALLOW USB DEBUGGING{f' · {model}' if model else ''}",
            "PHONE OFFLINE": f"PHONE FOUND · OFFLINE{f' · {model}' if model else ''}",
        }.get(label, label)
        self.connection_text.set(display)
        bg, fg = colors.get(label, ("#252a32", MUTED))
        self.connection_badge.configure(bg=bg, fg=fg)
        if hasattr(self, "status"):
            if label == "CONNECTED":
                self.status.set(f"PHONE DETECTED: {model or 'Android phone'} · connected and ready.")
                if hasattr(self, "home_quality_box"): self.load_camera_resolutions()
            elif label == "AUTHORIZE PHONE":
                self.status.set(f"PHONE DETECTED: {model or 'Android phone'} · unlock it and approve USB debugging.")
            elif label == "PHONE OFFLINE":
                self.status.set(f"PHONE DETECTED: {model or 'Android phone'} · offline. Reconnect USB.")
            elif label == "ADB ERROR":
                self.status.set("ADB could not check the phone. Check the USB connection and Android debugging service.")
            elif label == "NO PHONE":
                self.status.set("Waiting for phone… Enable USB debugging, use a data cable, and allow the prompt on the phone.")

    def _get_connected_serial(self):
        index = self.device_box.current()
        if index < 0 or index >= len(self.devices):
            self.refresh_devices()
            index = self.device_box.current()
        if index < 0 or index >= len(self.devices):
            return None
        serial, state, _ = self.devices[index]
        if state != "device":
            self.status.set("Unlock the phone and approve USB debugging, then refresh the device list.")
            return None
        return serial

    def launch_session(self, mode, resolution=None, session_kind=None, record_file=None):
        executable = Path(self.scrcpy_path.get())
        if not executable.is_file():
            messagebox.showerror("scrcpy not found", "The bundled scrcpy files could not be found. Reinstall FrameLink or select the official scrcpy executable.")
            return
        serial = self._get_connected_serial()
        if not serial: return
        args = [str(executable), f"--serial={serial}"]
        fps = self.flow_fps.get() if getattr(self, "flow_fps", None) else "30"
        if mode == "camera":
            camera = "back" if self.facing.get().startswith("Back") else "front"
            args += ["--video-source=camera", f"--camera-facing={camera}", f"--camera-fps={fps}"]
            if resolution:
                if "x" in resolution: args.append(f"--camera-size={resolution}")
                else: args.append(f"--max-size={resolution}")
        else:
            args += ["--video-bit-rate=20M", f"--max-fps={fps}"]
            if resolution: args.append(f"--max-size={resolution}")
            if record_file:
                args += [f"--record={record_file}", "--record-format=mp4"]
        window_title = "FrameLink Camera" if mode == "camera" else "FrameLink Mobile Screen"
        args += ["--no-audio", f"--window-title={window_title}"]
        self._close_session()
        try:
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(args, cwd=str(executable.parent), creationflags=flags, env=self.android_env())
            self.session_mode = session_kind or mode
            if mode == "camera":
                self.status.set("Camera preview started. In OBS, add a Window Capture source and choose FrameLink Camera.")
            else:
                self.status.set(f"Phone screen preview started{f' · recording to {record_file}' if record_file else ''}.")
            self.update_action_buttons()
            if self.flow_window and self.flow_window.winfo_exists(): self._render_flow_running()
        except OSError as exc:
            if record_file:
                self.is_recording = False
            messagebox.showerror("Could not start scrcpy", str(exc))

    def stop_camera(self):
        if self.proc and self.proc.poll() is None:
            self._close_session()
            self.status.set("Camera session stopped." if self.session_mode == "camera" else "Screen preview stopped.")
        self.proc = None
        self.session_mode = None
        self.is_recording = False
        self.update_action_buttons()
        if self.flow_window and self.flow_window.winfo_exists(): self._render_flow_configuration()

    def _close_session(self):
        proc = self.proc
        if not proc or proc.poll() is not None: return
        try:
            user32 = ctypes.windll.user32
            enum_windows = user32.EnumWindows
            get_pid = user32.GetWindowThreadProcessId
            post_message = user32.PostMessageW
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def close_owned_window(hwnd, _):
                pid = wintypes.DWORD()
                get_pid(hwnd, ctypes.byref(pid))
                if pid.value == proc.pid:
                    post_message(hwnd, 0x0010, 0, 0)  # WM_CLOSE: let scrcpy finalize recordings.
                    return False
                return True
            enum_windows(callback_type(close_owned_window), 0)
            proc.wait(timeout=4)
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired: proc.kill()

    def poll_devices(self):
        self.refresh_devices()
        self.after(2500, self.poll_devices)

    def poll_process(self):
        if self.proc and self.proc.poll() is not None:
            self.proc = None
            self.session_mode = None
            self.update_action_buttons()
            if self.flow_window and self.flow_window.winfo_exists(): self._render_flow_configuration()
            self.status.set("scrcpy session closed. If it closed unexpectedly, check USB authorization and Android 12+ for camera mode.")
        self.after(700, self.poll_process)

if __name__ == "__main__":
    FrameLink().mainloop()
