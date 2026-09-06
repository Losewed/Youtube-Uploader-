"""Мастер первого запуска: проводит по настройке за пять шагов.

Открывается сам, когда настройки не заполнены — нет config.json, не выбрана
папка на Диске или нет входа в Google. Всё сетевое делается в потоках,
результаты приходят через очередь: окно не должно замирать.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont

from ui import C, S, Button, Segmented, ellipsis, round_rect, wrap

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

CONFIG = BASE / "config.json"
EXAMPLE = BASE / "config.example.json"
SECRET = BASE / "client_secret.json"
TOKEN = BASE / "token.json"

STEPS = 5


def needs_setup() -> bool:
    """Стоит ли показывать мастер."""
    if not CONFIG.exists() or not TOKEN.exists():
        return True
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return True
    folder = (cfg.get("drive") or {}).get("folder_id") or ""
    return not folder or folder.startswith("ВСТАВЬТЕ") or folder.startswith("IP_")


def base_config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- поле ввода

def entry(parent, width=44, show=None):
    """Поле ввода в общем оформлении: у Tk своего тёмного стиля нет."""
    e = tk.Entry(parent, width=width, bg=C.raised, fg=C.text, relief="flat",
                 insertbackground=C.text, highlightthickness=1,
                 highlightbackground=C.line, highlightcolor=C.accent,
                 font=("Segoe UI", 10), show=show)
    e.configure(insertwidth=1)
    return e


# --------------------------------------------------------------------------- окно

class Wizard(tk.Toplevel):
    W, H = S(700), S(640)

    def __init__(self, parent, t, on_done):
        super().__init__(parent)
        self.t = t
        self.on_done = on_done
        self.cfg = base_config()

        self.title(t("wz.title"))
        self.configure(bg=C.bg)
        h = min(self.H, parent.winfo_screenheight() - S(160))
        self.geometry("%dx%d+%d+%d" % (
            self.W, h,
            max(20, parent.winfo_rootx() + S(40)), max(20, parent.winfo_rooty() + S(20))))
        self.minsize(S(640), S(560))
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.f_h1 = tkfont.Font(family="Segoe UI Semibold", size=15)
        self.f_body = tkfont.Font(family="Segoe UI", size=10)
        self.f_small = tkfont.Font(family="Segoe UI", size=9)
        self.f_tiny = tkfont.Font(family="Segoe UI", size=8)

        self.step = 1
        self._mail = queue.Queue()
        self.channel = None          # имя канала после входа
        self.folders = None          # список папок с Диска
        self.folder_pick = None
        self.models = None
        self.provider = "local"
        self.model_pick = None
        self._hover = -1

        self._build()
        self._render()
        self._pump()

    # ------------------------------------------------------------------ каркас
    def _build(self):
        head = tk.Frame(self, bg=C.bg)
        head.pack(fill="x", padx=S(26), pady=(S(20), S(4)))
        self.lbl_step = tk.Label(head, text="", bg=C.bg, fg=C.faint, font=self.f_tiny)
        self.lbl_step.pack(anchor="w")
        self.lbl_title = tk.Label(head, text="", bg=C.bg, fg=C.text, font=self.f_h1)
        self.lbl_title.pack(anchor="w", pady=(S(4), 0))

        self.dots = tk.Canvas(self, height=S(10), bg=C.bg, highlightthickness=0, bd=0)
        self.dots.pack(fill="x", padx=S(26), pady=(S(12), 0))
        # Без этого полоска рисуется, пока холст ещё нулевой ширины.
        self.dots.bind("<Configure>", lambda e: self._dots())

        # Кнопки крепим снизу ДО тела: иначе растягивающееся тело выдавливает
        # их за нижний край окна.
        foot = tk.Frame(self, bg=C.bg)
        foot.pack(side="bottom", fill="x", padx=S(26), pady=S(18))

        self.body = tk.Frame(self, bg=C.bg)
        self.body.pack(fill="both", expand=True, padx=S(26), pady=(S(16), 0))
        self.btn_back = Button(foot, self.t("wz.back"), self._back)
        self.btn_back.pack(side="left")
        self.btn_next = Button(foot, self.t("wz.next"), self._next, kind="primary")
        self.btn_next.pack(side="right")
        self.status = tk.Label(foot, text="", bg=C.bg, fg=C.muted, font=self.f_small)
        self.status.pack(side="left", padx=(S(14), 0))

    def _dots(self):
        self.dots.delete("all")
        w = self.dots.winfo_width() or self.W - S(52)
        seg = w / STEPS
        for i in range(STEPS):
            done = i + 1 <= self.step
            self.dots.create_rectangle(i * seg + 1, S(3), (i + 1) * seg - S(3), S(7),
                                       fill=C.accent if done else C.line, outline="")

    def _clear(self):
        for child in self.body.winfo_children():
            child.destroy()

    def _text(self, text, *, fg=C.muted, font=None, pady=None):
        tk.Label(self.body, text=text, bg=C.bg, fg=fg, font=font or self.f_body,
                 justify="left", anchor="w", wraplength=self.W - S(70)).pack(
            anchor="w", pady=pady or (0, S(10)), fill="x")

    # ------------------------------------------------------------------ навигация
    def _render(self):
        self._clear()
        self.lbl_step.configure(text=self.t("wz.step", n=self.step, total=STEPS))
        self.lbl_title.configure(text=self.t("wz.%d.title" % self.step))
        self._dots()
        self.btn_back.set_enabled(self.step > 1)
        getattr(self, "_step%d" % self.step)()

    def _back(self):
        if self.step > 1:
            self.step -= 1
            self.status.configure(text="")
            self._render()

    def _next(self):
        if self.step >= STEPS:
            self._finish()
            return
        self.step += 1
        self.status.configure(text="")
        self._render()

    def _close(self):
        self.destroy()

    def _finish(self):
        CONFIG.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        self.on_done()
        self.destroy()

    # ------------------------------------------------------------------ шаг 1
    def _step1(self):
        t = self.t
        self._text(t("wz.1.intro"))
        self._text(t("wz.1.need"), fg=C.text, pady=(S(8), S(6)))
        for key in ("wz.1.google", "wz.1.ffmpeg", "wz.1.model"):
            self._text("•  " + t(key), pady=(0, S(4)))

        ok = bool(shutil.which("ffmpeg"))
        self._text(t("wz.1.ffmpeg.ok") if ok else t("wz.1.ffmpeg.no"),
                   fg=C.ok if ok else C.warn, pady=(S(16), 0))

    # ------------------------------------------------------------------ шаг 2
    def _step2(self):
        t = self.t
        have_secret = SECRET.exists()
        self._text(t("wz.2.secret.ok") if have_secret else t("wz.2.secret.no"),
                   fg=C.ok if have_secret else C.warn)

        if not have_secret:
            self._text(t("wz.2.secret.how"))
            row = tk.Frame(self.body, bg=C.bg)
            row.pack(anchor="w", pady=(S(6), 0))
            Button(row, t("wz.2.console"),
                   lambda: webbrowser.open("https://console.cloud.google.com/")
                   ).pack(side="left", padx=(0, S(10)))
            Button(row, t("wz.2.open"),
                   lambda: subprocess.Popen(["explorer", str(BASE)])).pack(side="left")
            return

        if TOKEN.exists() and self.channel is None:
            self._probe_channel()

        if self.channel:
            self._text(t("wz.2.done", channel=self.channel), fg=C.ok, pady=(S(14), 0))
        else:
            Button(self.body, t("wz.2.login"), self._login, kind="primary").pack(
                anchor="w", pady=(S(14), 0))

    def _login(self):
        self.status.configure(text=self.t("wz.2.logging"))

        def work():
            try:
                import gauth
                creds = gauth.get_credentials(interactive=True)
                yt = gauth.youtube_service(creds)
                items = yt.channels().list(part="snippet", mine=True).execute(
                    num_retries=5).get("items", [])
                name = items[0]["snippet"]["title"] if items else "—"
                self._mail.put({"channel": name})
            except Exception as exc:
                self._mail.put({"__error": self.t("wz.2.fail", error=str(exc)[:90])})

        threading.Thread(target=work, daemon=True).start()

    def _probe_channel(self):
        def work():
            try:
                import gauth
                yt = gauth.youtube_service(gauth.get_credentials())
                items = yt.channels().list(part="snippet", mine=True).execute(
                    num_retries=5).get("items", [])
                self._mail.put({"channel": items[0]["snippet"]["title"] if items else "—"})
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ шаг 3
    def _step3(self):
        t = self.t
        self._text(t("wz.3.intro"))

        Button(self.body, t("wz.3.create"), self._make_folders).pack(
            anchor="w", pady=(S(4), S(12)))

        self.folder_cv = tk.Canvas(self.body, bg=C.bg, highlightthickness=0, bd=0)
        self.folder_cv.pack(fill="both", expand=True)
        self.folder_cv.bind("<Button-1>", self._click_folder)
        self.folder_cv.bind("<Motion>", self._hover_folder)
        self.folder_cv.bind("<Configure>", lambda e: self._draw_folders())
        self.folder_cv.bind("<MouseWheel>", self._scroll_folders)
        self._fscroll = 0

        if self.folders is None:
            self.status.configure(text=t("wz.3.loading"))
            threading.Thread(target=self._load_folders, daemon=True).start()
        self._draw_folders()

    def _load_folders(self):
        try:
            import gauth
            drive = gauth.drive_service(gauth.get_credentials())
            resp = drive.files().list(
                q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields="files(id, name)", pageSize=200, orderBy="name",
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute(num_retries=5)
            self._mail.put({"folders": [(f["id"], f["name"]) for f in resp.get("files", [])]})
        except Exception as exc:
            self._mail.put({"folders": [], "__error": str(exc)[:90]})

    def _make_folders(self):
        self.status.configure(text=self.t("wz.3.loading"))

        def work():
            try:
                import gauth
                drive = gauth.drive_service(gauth.get_credentials())
                FOLDER = "application/vnd.google-apps.folder"

                def ensure(name, parent=None):
                    q = "mimeType='%s' and trashed=false and name='%s'" % (FOLDER, name)
                    q += " and '%s' in parents" % parent if parent else " and 'root' in parents"
                    found = drive.files().list(q=q, fields="files(id)").execute(
                        num_retries=5).get("files", [])
                    if found:
                        return found[0]["id"]
                    body = {"name": name, "mimeType": FOLDER}
                    if parent:
                        body["parents"] = [parent]
                    return drive.files().create(body=body, fields="id").execute(
                        num_retries=5)["id"]

                root = ensure("видео")
                src = ensure("готовые видео", root)
                self._mail.put({"__folders_made": src, "__status": self.t("wz.3.created")})
            except Exception as exc:
                self._mail.put({"__error": str(exc)[:90]})

        threading.Thread(target=work, daemon=True).start()

    def _draw_folders(self):
        cv = getattr(self, "folder_cv", None)
        if cv is None or not cv.winfo_exists():
            return
        cv.delete("all")
        if self.folders is None:
            cv.create_text(4, 14, text=self.t("wz.3.loading"), anchor="w",
                           fill=C.faint, font=self.f_small)
            return
        w = cv.winfo_width() or self.W - S(60)
        top = S(2) - self._fscroll
        for idx, (fid, name) in enumerate(self.folders):
            y = top + idx * S(44)
            if y + S(38) < 0 or y > cv.winfo_height():
                continue
            picked = fid == self.folder_pick
            round_rect(cv, 0, y, w - 2, y + S(38), S(10),
                       fill=C.raised if (picked or idx == self._hover) else C.surface,
                       outline=C.accent if picked else C.line,
                       width=2 if picked else 1)
            if picked:
                cv.create_oval(S(14), y + S(15), S(22), y + S(23), fill=C.accent, outline="")
            cv.create_text(S(34), y + S(19), text=ellipsis(name, self.f_body, w - S(60)),
                           anchor="w", fill=C.text, font=self.f_body)

    def _folder_at(self, y):
        if not self.folders:
            return -1
        rel = y - (S(2) - self._fscroll)
        idx = int(rel // S(44))
        return idx if 0 <= idx < len(self.folders) and rel % S(44) <= S(38) else -1

    def _hover_folder(self, e):
        idx = self._folder_at(e.y)
        if idx != self._hover:
            self._hover = idx
            self._draw_folders()

    def _click_folder(self, e):
        idx = self._folder_at(e.y)
        if idx >= 0:
            self.folder_pick = self.folders[idx][0]
            self.cfg["drive"]["folder_id"] = self.folder_pick
            self._draw_folders()

    def _scroll_folders(self, e):
        if not self.folders:
            return
        span = len(self.folders) * S(44)
        limit = max(0, span - self.folder_cv.winfo_height() + S(10))
        self._fscroll = min(limit, max(0, self._fscroll - e.delta // 3))
        self._draw_folders()

    # ------------------------------------------------------------------ шаг 4
    def _step4(self):
        t = self.t
        Segmented(self.body,
                  [("local", t("wz.4.local")), ("claude", t("wz.4.claude")),
                   ("none", t("wz.4.filename"))],
                  self.provider, self._set_provider,
                  width=700 - 60, height=36).pack(anchor="w")

        self.provider_hint = tk.Label(
            self.body, text=t("wz.4.%s.hint" % {"local": "local", "claude": "claude",
                                                "none": "filename"}[self.provider]),
            bg=C.bg, fg=C.muted, font=self.f_small, justify="left", anchor="w",
            wraplength=self.W - S(70))
        self.provider_hint.pack(anchor="w", pady=(S(10), S(14)), fill="x")

        if self.provider == "local":
            tk.Label(self.body, text=t("wz.4.address"), bg=C.bg, fg=C.faint,
                     font=self.f_tiny).pack(anchor="w")
            self.e_addr = entry(self.body)
            self.e_addr.insert(0, self.cfg["metadata"].get("local_url")
                               or "http://127.0.0.1:8888")
            self.e_addr.pack(anchor="w", pady=(S(4), S(10)), ipady=S(5))
            Button(self.body, t("wz.4.check"), self._check_local).pack(anchor="w")

            self.model_cv = tk.Canvas(self.body, bg=C.bg, highlightthickness=0, bd=0)
            self.model_cv.pack(fill="both", expand=True, pady=(S(12), 0))
            self.model_cv.bind("<Button-1>", self._click_model)
            self.model_cv.bind("<Configure>", lambda e: self._draw_models())
            self._draw_models()

        elif self.provider == "claude":
            tk.Label(self.body, text=t("wz.4.key"), bg=C.bg, fg=C.faint,
                     font=self.f_tiny).pack(anchor="w")
            self.e_key = entry(self.body, show="•")
            self.e_key.pack(anchor="w", pady=(S(4), 0), ipady=S(5))

    def _set_provider(self, code):
        self.provider = code
        self._render()

    def _check_local(self):
        url = self.e_addr.get().strip()
        self.cfg["metadata"]["local_url"] = url
        self.cfg["metadata"]["local_api"] = "openai"
        self.status.configure(text=self.t("wz.4.checking"))

        def work():
            try:
                import meta
                rows = meta.local_models_full(self.cfg["metadata"])
                names = [r.get("id") or r.get("name") for r in rows]
                self._mail.put({"models": names,
                                "__status": self.t("wz.4.found", n=len(names))
                                if names else self.t("wz.4.nomodels")})
            except Exception:
                self._mail.put({"models": [], "__status": self.t("wz.4.down")})

        threading.Thread(target=work, daemon=True).start()

    def _draw_models(self):
        cv = getattr(self, "model_cv", None)
        if cv is None or not cv.winfo_exists():
            return
        cv.delete("all")
        if not self.models:
            return
        w = cv.winfo_width() or self.W - 60
        cv.create_text(S(2), S(8), text=self.t("wz.4.pick"), anchor="nw", fill=C.faint,
                       font=self.f_tiny)
        for idx, name in enumerate(self.models):
            y = S(26) + idx * S(40)
            if y > cv.winfo_height():
                break
            picked = name == self.model_pick
            round_rect(cv, 0, y, w - 2, y + S(34), S(10),
                       fill=C.raised if picked else C.surface,
                       outline=C.accent if picked else C.line,
                       width=2 if picked else 1)
            cv.create_text(S(16), y + S(17), text=ellipsis(name.split("/")[-1], self.f_body, w - S(40)),
                           anchor="w", fill=C.text, font=self.f_body)

    def _click_model(self, e):
        if not self.models:
            return
        idx = int((e.y - S(26)) // S(40))
        if 0 <= idx < len(self.models) and (e.y - S(26)) % S(40) <= S(34):
            self.model_pick = self.models[idx]
            self.cfg["metadata"]["vision_model"] = self.model_pick
            self.cfg["metadata"]["vision"] = True
            self.cfg["metadata"]["provider"] = "local"
            self._draw_models()

    # ------------------------------------------------------------------ шаг 5
    def _step5(self):
        t = self.t
        tk.Label(self.body, text=t("wz.5.publish"), bg=C.bg, fg=C.faint,
                 font=self.f_tiny).pack(anchor="w")
        times = self.cfg["schedule"].get("publish_times") or ["03:30"]
        self.e_time = entry(self.body, width=18)
        self.e_time.insert(0, ", ".join(times))
        self.e_time.pack(anchor="w", pady=(S(4), S(16)), ipady=S(5))

        tk.Label(self.body, text=t("wz.5.format"), bg=C.bg, fg=C.faint,
                 font=self.f_tiny).pack(anchor="w")
        Segmented(self.body,
                  [("shorts", t("mode.shorts")), ("video", t("mode.video")),
                   ("auto", t("mode.auto"))],
                  self.cfg["youtube"].get("mode") or "auto", self._set_mode,
                  width=700 - 60, height=36).pack(anchor="w", pady=(S(4), S(16)))

        tk.Label(self.body, text=t("wz.5.task"), bg=C.bg, fg=C.faint,
                 font=self.f_tiny).pack(anchor="w")
        self._text(t("wz.5.task.hint"), pady=(S(4), S(6)))
        Button(self.body, t("wz.5.task"), self._make_task).pack(anchor="w")

        self._text(t("wz.5.ready"), fg=C.ok, pady=(S(18), 0))
        self.btn_next.text = t("wz.finish")
        self.btn_next._draw()

    def _set_mode(self, code):
        self.cfg["youtube"]["mode"] = code

    def _make_task(self):
        def work():
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", str(BASE / "setup_task.ps1")],
                    capture_output=True, text=True, errors="replace", timeout=90,
                    cwd=str(BASE),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if out.returncode == 0:
                    self._mail.put({"__status": self.t("wz.5.task.ok")})
                else:
                    self._mail.put({"__error": self.t(
                        "wz.5.task.fail", error=(out.stderr or "")[:80])})
            except Exception as exc:
                self._mail.put({"__error": self.t("wz.5.task.fail", error=str(exc)[:80])})

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ приём из потоков
    def _pump(self):
        try:
            while True:
                msg = self._mail.get_nowait()
                error = msg.pop("__error", None)
                if error:
                    self.status.configure(text=error, fg=C.bad)
                status = msg.pop("__status", None)
                if status:
                    self.status.configure(text=status, fg=C.muted)

                made = msg.pop("__folders_made", None)
                if made:
                    self.folder_pick = made
                    self.cfg["drive"]["folder_id"] = made
                    self.folders = None
                    threading.Thread(target=self._load_folders, daemon=True).start()

                if "channel" in msg:
                    self.channel = msg["channel"]
                    if self.step == 2:
                        self._render()
                if "folders" in msg:
                    self.folders = msg["folders"]
                    self._draw_folders()
                if "models" in msg:
                    self.models = msg["models"]
                    self._draw_models()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._pump)
