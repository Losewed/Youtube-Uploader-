r"""Панель управления загрузкой видео на YouTube.

Запуск: Панель.bat  или  .\.venv\Scripts\pythonw.exe dashboard.py

Всё рисуется на Canvas вручную: скруглённые карточки, кнопки с наведением,
живой обратный отсчёт. Никаких зависимостей сверх стандартной библиотеки.
Строки интерфейса лежат в i18n.py, язык переключается кнопкой в шапке.
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import font as tkfont
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import i18n
from ui import C, RADIUS, S, Button, LangSwitch, ellipsis, round_rect, wrap

TASK_NAME = "ClaudeYouTubeUploader"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------- сбор данных

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def next_run_time(t):
    """(строка для показа, datetime или None)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-ScheduledTaskInfo -TaskName '%s').NextRunTime.ToString('o')" % TASK_NAME],
            capture_output=True, text=True, errors="replace", timeout=25,
            creationflags=NO_WINDOW,
        )
        raw = (out.stdout or "").strip()
        if not raw:
            return t("task.none"), None
        when = datetime.fromisoformat(raw)
        return t.date(when), when
    except Exception:
        return t("task.unknown"), None


def port_open(url: str, timeout=1.5) -> bool:
    """Быстрый стук в порт: мёртвый сервер не должен держать панель 15 секунд."""
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(url if "//" in url else "//" + url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
    sock = socket.socket()
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        sock.close()


def model_state(mcfg, t):
    try:
        import meta
        if not port_open(meta.local_url(mcfg)):
            return t("model.down"), C.bad
        models = meta.local_models(mcfg)
    except Exception:
        return t("model.down"), C.bad
    wanted = mcfg.get("vision_model") if mcfg.get("vision") else mcfg.get("local_model")
    if not wanted:
        return t("model.unset"), C.warn
    short = wanted.split("/")[-1]
    key = short.split(":")[0].lower()
    if any(m.split("/")[-1].split(":")[0].lower() == key for m in models):
        return short, C.ok
    return t("model.missing", name=short), C.bad


def gather_local(t):
    cfg = load_json(BASE / "config.json", {})
    state = load_json(BASE / "state.json", {"uploaded": {}})
    known = set((state.get("uploaded") or {}).keys())
    tz = ZoneInfo((cfg.get("schedule") or {}).get("timezone", "UTC"))
    now = datetime.now(tz)

    items = []
    for rec in (state.get("uploaded") or {}).values():
        when = None
        if rec.get("publish_at"):
            try:
                when = datetime.fromisoformat(rec["publish_at"]).astimezone(tz)
            except ValueError:
                pass
        items.append({"rec": rec, "when": when, "waiting": bool(when and when > now)})

    waiting = sorted((i for i in items if i["waiting"]), key=lambda i: i["when"])
    done = sorted((i for i in items if not i["waiting"]),
                  key=lambda i: i["when"] or now, reverse=True)

    return {
        "cfg": cfg, "tz": tz,
        "items": waiting + done,
        "waiting": len(waiting),
        "total": len(items),
        "next_run_label": "…",
        "next_run": None,
        "model": (t("model.checking"), C.muted),
        "queue": None,
        "known": known,
    }


def probe_queue(cfg, known, t):
    """Что лежит на Диске и ещё не залито."""
    try:
        import gauth
        import uploader
        drive = gauth.drive_service(gauth.get_credentials())
        files = uploader.list_videos(drive, cfg)
        return [f["name"] for f in files if f["id"] not in known]
    except Exception as exc:
        return t("tile.queue.error") + ": " + str(exc)[:70]


# --------------------------------------------------------------------------- приложение

class Dashboard(tk.Tk):
    PAD = S(22)
    LIST_W = S(392)
    ROW_H = S(96)

    def __init__(self):
        super().__init__()
        self.t = i18n.T(i18n.load_language())

        # Размер считаем от экрана: при масштабе 125% жёсткие 760 точек
        # превращаются в 950 и нижние кнопки уезжают под панель задач.
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(S(1180), sw - S(140))
        h = min(S(780), sh - S(150))
        self.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, max(0, (sh - h) // 2 - 20)))
        self.minsize(S(940), S(600))
        self.configure(bg=C.bg)
        self.title(self.t("app.title"))

        self.f_title = tkfont.Font(family="Segoe UI Semibold", size=17)
        self.f_h2 = tkfont.Font(family="Segoe UI Semibold", size=12)
        self.f_body = tkfont.Font(family="Segoe UI", size=10)
        self.f_small = tkfont.Font(family="Segoe UI", size=9)
        self.f_tiny = tkfont.Font(family="Segoe UI", size=8)
        self.f_num = tkfont.Font(family="Segoe UI Light", size=26)
        self.f_value = tkfont.Font(family="Segoe UI", size=13)

        self.data = None
        self.selected = 0
        self._busy = False
        self._images = {}
        self._mail = queue.Queue()
        self._scroll = 0
        self._hover_row = -1

        self._build()
        # Пустые настройки — значит первый запуск: ведём по шагам, а не бросаем
        # человека наедине с config.json.
        import setup_wizard
        if setup_wizard.needs_setup():
            self.after(300, self.open_wizard)
        self.after(80, lambda: self.refresh(False))
        self._tick()
        self._pump()

    # ------------------------------------------------------------------ язык
    def switch_language(self, code):
        i18n.save_language(code)
        self.t = i18n.T(code)
        self.title(self.t("app.title"))
        for child in self.winfo_children():
            child.destroy()
        self._build()
        self.refresh(False)

    # ------------------------------------------------------------------ каркас
    def _build(self):
        t = self.t

        head = tk.Frame(self, bg=C.bg)
        head.pack(fill="x", padx=self.PAD, pady=(S(18), S(14)))

        tk.Label(head, text=t("app.title"), bg=C.bg, fg=C.text,
                 font=self.f_title).pack(side="left")

        self.btn_deep = Button(head, t("btn.drive"), lambda: self.refresh(True))
        self.btn_deep.pack(side="right")
        self.btn_quick = Button(head, t("btn.refresh"), lambda: self.refresh(False))
        self.btn_quick.pack(side="right", padx=(0, S(10)))
        LangSwitch(head, self.t.lang, self.switch_language).pack(side="right", padx=(0, S(14)))
        Button(head, t("btn.settings"), self.open_settings).pack(side="right", padx=(0, S(10)))

        self.stats = tk.Canvas(self, height=S(124), bg=C.bg, highlightthickness=0, bd=0)
        self.stats.pack(fill="x", padx=self.PAD)
        self.stats.bind("<Configure>", lambda e: self._render_stats())

        # Нижнюю панель кладём раньше списка и прижимаем к низу, иначе
        # растягивающийся список выдавливает кнопки за границу окна.
        foot = tk.Frame(self, bg=C.bg)
        foot.pack(side="bottom", fill="x", padx=self.PAD, pady=S(16))

        body = tk.Frame(self, bg=C.bg)
        body.pack(fill="both", expand=True, padx=self.PAD, pady=(S(16), 0))

        left = tk.Frame(body, bg=C.bg, width=self.LIST_W)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        self.list_cv = tk.Canvas(left, bg=C.bg, highlightthickness=0, bd=0)
        self.list_cv.pack(fill="both", expand=True)
        self.list_cv.bind("<Button-1>", self._click_list)
        self.list_cv.bind("<Motion>", self._hover_list)
        self.list_cv.bind("<Leave>", lambda e: self._render_list(-1))
        self.list_cv.bind("<MouseWheel>", self._scroll_list)
        # Без этого список не перерисуется ни при смене размера окна, ни сразу
        # после пересборки: на момент первой отрисовки холст ещё нулевой высоты.
        self.list_cv.bind("<Configure>", lambda e: self._render_list())

        right = tk.Frame(body, bg=C.bg)
        right.pack(side="left", fill="both", expand=True, padx=(S(18), 0))
        self.detail = tk.Canvas(right, bg=C.bg, highlightthickness=0, bd=0)
        self.detail.pack(fill="both", expand=True)
        self.detail.bind("<Configure>", lambda e: self._render_detail())
        self.detail.bind("<Button-1>", self._click_detail)
        self.detail.bind("<Motion>", self._hover_detail)

        for text, cmd, kind in (
            (t("btn.run"), lambda: self.console("run"), "primary"),
            (t("btn.dry"), lambda: self.console("run", "--dry-run"), "ghost"),
            (t("btn.load"), lambda: self.console("loadmodel"), "ghost"),
            (t("btn.unload"), lambda: self.console("unloadmodel"), "ghost"),
            (t("btn.log"), self.open_log, "ghost"),
        ):
            Button(foot, text, cmd, kind=kind).pack(side="left", padx=(0, S(10)))

        self.status = tk.Label(foot, text="", bg=C.bg, fg=C.faint, font=self.f_small)
        self.status.pack(side="right")

    # ------------------------------------------------------------------ данные
    def refresh(self, deep: bool):
        """Местное — сразу, сетевое — следом, чтобы окно не подвисало."""
        if self._busy:
            return
        self._busy = True

        data = gather_local(self.t)
        self.data = data
        self.selected = min(self.selected, max(len(data["items"]) - 1, 0))
        self._render_stats()
        self._render_list()
        self._render_detail()
        self.status.configure(text=self.t("status.probing"))

        t = self.t

        def probes():
            # Тревожить окно из чужого потока нельзя — складываем в очередь,
            # главный поток разбирает её в _pump.
            label, when = next_run_time(t)
            self._mail.put({"next_run_label": label, "next_run": when})
            self._mail.put({"model": model_state(data["cfg"].get("metadata") or {}, t)})
            if deep:
                self._mail.put({"__status": t("status.drive")})
                self._mail.put({"queue": probe_queue(data["cfg"], data["known"], t)})
            self._mail.put({"__done": True})

        threading.Thread(target=probes, daemon=True).start()

    def _pump(self):
        """Раз в 120 мс забирает готовые результаты из фоновых потоков."""
        try:
            while True:
                msg = self._mail.get_nowait()
                if msg.pop("__done", None):
                    self._busy = False
                    self.status.configure(text=self.t(
                        "status.updated", time=datetime.now().strftime("%H:%M:%S")))
                status = msg.pop("__status", None)
                if status:
                    self.status.configure(text=status)
                if msg and self.data:
                    self.data.update(msg)
                    self._render_stats()
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _tick(self):
        """Раз в секунду перерисовывает обратный отсчёт."""
        if not self.winfo_exists():
            return          # окно закрыли — таймер должен замолчать
        if self.data:
            self._render_stats()
        self.after(1000, self._tick)

    # ------------------------------------------------------------------ плитки
    def _tile(self, cv, x, w, title, value, colour, sub=""):
        h = S(112)
        round_rect(cv, x, S(4), x + w, S(4) + h, RADIUS, fill=C.surface, outline=C.line)
        cv.create_text(x + S(20), S(26), text=title.upper(), anchor="w",
                       fill=C.faint, font=self.f_tiny)
        fnt = self.f_num if 1 < len(value) <= 9 else self.f_value
        cv.create_text(x + S(20), S(60), text=value, anchor="w", fill=colour, font=fnt)
        if sub:
            cv.create_text(x + S(20), S(84), text=sub, anchor="w", fill=C.muted,
                           font=self.f_small)

    def _render_stats(self):
        cv, t = self.stats, self.t
        cv.delete("all")
        if not self.data:
            return
        width = cv.winfo_width() or 1100
        gap = S(14)
        w = (width - gap * 3) / 4
        d = self.data

        nearest = next((i for i in d["items"] if i["waiting"]), None)
        if nearest:
            left = nearest["when"] - datetime.now(d["tz"])
            total = max(0, int(left.total_seconds()))
            value = "%d:%02d:%02d" % (total // 3600, total % 3600 // 60, total % 60)
            sub, colour = t.date(nearest["when"]), C.accent
        else:
            value, sub, colour = t("tile.countdown.none"), t("tile.countdown.empty"), C.faint
        self._tile(cv, 0, w, t("tile.countdown"), value, colour, sub)

        if d["next_run"]:
            left = d["next_run"] - datetime.now(d["next_run"].tzinfo)
            mins = max(0, int(left.total_seconds() // 60))
            value = t("task.in", m=mins) if mins < 90 else d["next_run_label"]
        else:
            value = d["next_run_label"]
        self._tile(cv, w + gap, w, t("tile.check"), value, C.text, t("tile.check.sub"))

        text, colour = d["model"]
        self._tile(cv, (w + gap) * 2, w, t("tile.model"),
                   ellipsis(text, self.f_value, int(w) - S(40)), colour, t("tile.model.sub"))

        q = d["queue"]
        if q is None:
            value, sub, colour = t("tile.queue.unknown"), t("tile.queue.hint"), C.faint
        elif isinstance(q, str):
            value, sub, colour = t("tile.queue.error"), q, C.bad
        elif not q:
            value, sub, colour = t("tile.queue.empty"), t("tile.queue.none"), C.muted
        else:
            value = str(len(q))
            sub, colour = ellipsis(", ".join(q), self.f_small, int(w) - S(40)), C.warn
        self._tile(cv, (w + gap) * 3, w, t("tile.queue"), value, colour, sub)

    # ------------------------------------------------------------------ список
    def _render_list(self, hover=None):
        if hover is None:
            hover = self._hover_row
        cv, t = self.list_cv, self.t
        cv.delete("all")
        if not self.data:
            return

        items = self.data["items"]
        cv.create_text(S(4), S(8), text=t("sec.videos"), anchor="nw", fill=C.faint,
                       font=self.f_tiny)

        if not items:
            round_rect(cv, 0, S(30), self.LIST_W - S(8), S(130), RADIUS,
                       fill=C.surface, outline=C.line)
            cv.create_text(self.LIST_W / 2 - S(4), S(70), text=t("list.empty.title"),
                           fill=C.muted, font=self.f_body)
            cv.create_text(self.LIST_W / 2 - S(4), S(94), text=t("list.empty.sub"),
                           fill=C.faint, font=self.f_small)
            return

        top = S(30) - self._scroll
        bottom = top + len(items) * (self.ROW_H + S(10))
        if cv.winfo_height() - bottom > S(120):
            round_rect(cv, 0, bottom + S(8), self.LIST_W - S(8), bottom + S(104), RADIUS,
                       fill=C.surface, outline=C.line)
            cv.create_text(S(20), bottom + S(32), anchor="nw", fill=C.muted,
                           font=self.f_body, text=t("hint.title"))
            cv.create_text(S(20), bottom + S(56), anchor="nw", fill=C.faint,
                           font=self.f_small, text=t("hint.line1"))
            cv.create_text(S(20), bottom + S(74), anchor="nw", fill=C.faint,
                           font=self.f_small, text=t("hint.line2"))

        for idx, item in enumerate(items):
            y = top + idx * (self.ROW_H + S(10))
            if y > cv.winfo_height() or y + self.ROW_H < S(20):
                continue
            self._row(cv, idx, item, y, selected=idx == self.selected, hover=idx == hover)

    def _row(self, cv, idx, item, y, *, selected, hover):
        t = self.t
        w, h = self.LIST_W - S(8), self.ROW_H
        fill = C.raised if (selected or hover) else C.surface
        outline = C.accent if selected else C.line
        round_rect(cv, 0, y, w, y + h, RADIUS, fill=fill, outline=outline,
                   width=2 if selected else 1)

        rec = item["rec"]
        img = self._poster(rec.get("video_id"), small=True)
        tx = S(14)
        if img:
            cv.create_image(tx, y + h / 2, image=img, anchor="w")
            tx += img.width() + S(14)
        else:
            round_rect(cv, tx, y + S(16), tx + S(120), y + h - S(16), S(8),
                       fill=C.bg, outline=C.line)
            cv.create_text(tx + S(60), y + h / 2, text=t("row.noposter"),
                           fill=C.faint, font=self.f_tiny)
            tx += S(134)

        avail = w - tx - S(16)
        lines = wrap(rec.get("title") or t("row.untitled"), self.f_body, avail)[:2]
        ty = y + S(22)
        for i, line in enumerate(lines):
            if i == 1 and len(lines) == 2:
                line = ellipsis(line, self.f_body, avail)
            cv.create_text(tx, ty, text=line, anchor="w", fill=C.text, font=self.f_body)
            ty += S(18)

        if item["waiting"]:
            dot, label = C.accent, t("row.will", date=t.date(item["when"]))
        elif item["when"]:
            dot, label = C.ok, t("row.was", date=t.date(item["when"]))
        else:
            dot, label = C.faint, t("row.nodate")
        cv.create_oval(tx, y + S(58), tx + S(7), y + S(65), fill=dot, outline="")
        cv.create_text(tx + S(14), y + S(62), text=label, anchor="w",
                       fill=C.muted, font=self.f_small)

        tags = rec.get("tags") or []
        if tags:
            cv.create_text(tx, y + S(80), anchor="w", fill=C.faint, font=self.f_tiny,
                           text=ellipsis(" · ".join(tags[:3]), self.f_tiny, avail))

    def _hover_list(self, e):
        idx = self._row_at(e.y)
        if idx != self._hover_row:
            self._hover_row = idx
            self._render_list(idx)

    def _click_list(self, e):
        idx = self._row_at(e.y)
        if self.data and 0 <= idx < len(self.data["items"]):
            self.selected = idx
            self._render_list()
            self._render_detail()

    def _row_at(self, y):
        if not self.data or not self.data["items"]:
            return -1
        rel = y - (S(30) - self._scroll)
        idx = int(rel // (self.ROW_H + S(10)))
        if 0 <= idx < len(self.data["items"]) and rel % (self.ROW_H + S(10)) <= self.ROW_H:
            return idx
        return -1

    def _scroll_list(self, e):
        if not self.data:
            return
        span = len(self.data["items"]) * (self.ROW_H + S(10))
        limit = max(0, span - self.list_cv.winfo_height() + S(40))
        self._scroll = min(limit, max(0, self._scroll - e.delta // 3))
        self._render_list()

    # ------------------------------------------------------------------ обложки
    def _poster(self, video_id, *, small):
        if not video_id:
            return None
        key = (video_id, small)
        if key in self._images:
            return self._images[key]
        path = BASE / "thumbs" / (video_id + ".png")
        if not path.exists():
            self._images[key] = None
            return None
        try:
            img = tk.PhotoImage(file=str(path))
            # PhotoImage умеет только целочисленное увеличение и уменьшение,
            # поэтому дробный масштаб набираем дробью: 1.25 — это 5/4.
            from fractions import Fraction
            from ui import SCALE
            frac = Fraction(SCALE).limit_denominator(8)
            num, den = frac.numerator, frac.denominator * (2 if small else 1)
            if num != 1:
                img = img.zoom(num, num)
            if den != 1:
                img = img.subsample(den, den)
        except Exception:
            img = None
        self._images[key] = img
        return img

    # ------------------------------------------------------------------ подробности
    def _render_detail(self):
        cv, t = self.detail, self.t
        cv.delete("all")
        self._detail_link = None
        if not self.data or not self.data["items"] or self.selected >= len(self.data["items"]):
            return

        item = self.data["items"][self.selected]
        rec = item["rec"]
        w, pad = cv.winfo_width() or S(640), S(24)

        cv.create_text(S(4), S(8), text=t("sec.detail"), anchor="nw", fill=C.faint,
                       font=self.f_tiny)
        round_rect(cv, 0, S(30), w - 2, cv.winfo_height() - S(4), RADIUS,
                   fill=C.surface, outline=C.line)

        y = S(30) + pad
        img = self._poster(rec.get("video_id"), small=False)
        if img:
            cv.create_image(pad, y, image=img, anchor="nw")
            text_x = pad + img.width() + S(20)
        else:
            text_x = pad
        head_w = w - text_x - pad

        ty = y + S(4)
        for line in wrap(rec.get("title") or t("row.untitled"), self.f_h2, head_w)[:3]:
            cv.create_text(text_x, ty, text=line, anchor="nw", fill=C.text, font=self.f_h2)
            ty += S(22)

        if item["waiting"]:
            left = item["when"] - datetime.now(self.data["tz"])
            chip = t("detail.in", h=int(left.total_seconds() // 3600),
                     m=int(left.total_seconds() % 3600 // 60))
            colour = C.accent
        elif item["when"]:
            chip, colour = t("detail.published", date=t.date(item["when"])), C.ok
        else:
            chip, colour = t("detail.unscheduled"), C.faint
        self._chip(cv, text_x, ty + S(8), chip, colour)

        y = max(ty + S(46), y + (img.height() if img else 0) + S(18)) + S(10)

        cv.create_text(pad, y, text=t("sec.description"), anchor="nw", fill=C.faint,
                       font=self.f_tiny)
        y += S(20)
        desc = rec.get("description") or t("detail.nodesc")
        for line in wrap(desc, self.f_body, w - pad * 2)[:7]:
            cv.create_text(pad, y, text=line, anchor="nw", fill=C.muted, font=self.f_body)
            y += S(19)

        tags = rec.get("tags") or []
        if tags:
            y += S(14)
            cv.create_text(pad, y, text=t("sec.tags"), anchor="nw", fill=C.faint,
                           font=self.f_tiny)
            y += S(22)
            x = pad
            for tag in tags:
                tw = self.f_small.measure(tag) + S(20)
                if x + tw > w - pad:
                    x, y = pad, y + S(30)
                round_rect(cv, x, y, x + tw, y + S(24), S(12), fill=C.raised, outline=C.line)
                cv.create_text(x + tw / 2, y + S(12), text=tag, fill=C.muted, font=self.f_small)
                x += tw + S(8)
            y += S(34)

        y += S(12)
        cv.create_text(pad, y, anchor="nw", fill=C.faint, font=self.f_small,
                       text=t("detail.file") + ellipsis(rec.get("name", "—"),
                                                        self.f_small, w - pad * 2 - S(120)))
        y += S(20)
        author = {"local": t("author.local"), "claude": t("author.claude")}.get(
            rec.get("metadata_provider"), rec.get("metadata_provider") or "—")
        cv.create_text(pad, y, anchor="nw", fill=C.faint, font=self.f_small,
                       text=t("detail.author") + author)

        y += S(20)
        vid = rec.get("video_id")
        if vid:
            url = "https://youtu.be/" + vid
            node = cv.create_text(pad, y, text=url, anchor="nw", fill=C.accent,
                                  font=self.f_small)
            box = cv.bbox(node)
            cv.create_line(box[0], box[3] - 1, box[2], box[3] - 1, fill=C.accent_dim)
            self._detail_link = (box, url)

    def _chip(self, cv, x, y, text, colour):
        w = self.f_small.measure(text) + S(30)
        round_rect(cv, x, y, x + w, y + S(26), S(13), fill=C.bg, outline=colour)
        cv.create_oval(x + S(11), y + S(10), x + S(17), y + S(16), fill=colour, outline="")
        cv.create_text(x + S(24), y + S(13), text=text, anchor="w", fill=colour,
                       font=self.f_small)


    def _hover_detail(self, e):
        link = getattr(self, "_detail_link", None)
        inside = link and link[0][0] <= e.x <= link[0][2] and link[0][1] <= e.y <= link[0][3]
        self.detail.configure(cursor="hand2" if inside else "")

    def _click_detail(self, e):
        link = getattr(self, "_detail_link", None)
        if link and link[0][0] <= e.x <= link[0][2] and link[0][1] <= e.y <= link[0][3]:
            import webbrowser
            webbrowser.open(link[1])

    # ------------------------------------------------------------------ действия
    def _python(self):
        if (BASE / ".venv" / "Scripts" / "python.exe").exists():
            return r".venv\Scripts\python.exe"
        return "python"

    def console(self, *args):
        subprocess.Popen(
            "cmd /k {} uploader.py {}".format(self._python(), " ".join(args)),
            cwd=str(BASE),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        self.status.configure(text=self.t("status.launched", cmd=" ".join(args)))

    def open_wizard(self):
        existing = getattr(self, "_wizard", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        import setup_wizard
        self._wizard = setup_wizard.Wizard(
            self, self.t, on_done=lambda: self.refresh(False))

    def open_settings(self):
        """Окно настроек. Уже открытое просто поднимаем, а не плодим второе."""
        existing = getattr(self, "_settings", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        import settings_window
        self._settings = settings_window.Settings(
            self, self.t, on_saved=lambda: self.refresh(False),
            on_wizard=self.open_wizard)

    def open_log(self):
        log = BASE / "logs" / "uploader.log"
        if log.exists():
            subprocess.Popen(["notepad.exe", str(log)])
        else:
            self.status.configure(text=self.t("status.nolog"))


if __name__ == "__main__":
    Dashboard().mainloop()
