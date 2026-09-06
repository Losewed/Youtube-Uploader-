"""Окно настроек: формат публикации, модель и папка на Диске.

Списки моделей и папок тянутся из сети, поэтому окно открывается сразу,
а содержимое разделов подгружается следом — как и главная панель.
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont

from ui import C, RADIUS, S, Button, Segmented, ellipsis, round_rect, wrap

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"


def read_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def write_config(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- загрузка списков

def fetch_models(mcfg):
    """[(имя, умеет ли в картинки, контекст, загружена)] или строка с ошибкой."""
    try:
        import meta
        rows = meta.local_models_full(mcfg)
    except Exception as exc:
        return "%s: %s" % (type(exc).__name__, str(exc)[:70])

    out = []
    for row in rows:
        name = row.get("id") or row.get("name") or "?"
        out.append({
            "name": name,
            "vision": None,          # проверим лениво, только для выбранной
            "context": row.get("context_length"),
            "loaded": bool(row.get("loaded")),
        })
    return out


def fetch_folders():
    """[(id, имя)] или строка с ошибкой."""
    try:
        import gauth
        drive = gauth.drive_service(gauth.get_credentials())
        resp = drive.files().list(
            q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
            fields="files(id, name)", pageSize=200, orderBy="name",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute(num_retries=5)
        return [(f["id"], f["name"]) for f in resp.get("files", [])]
    except Exception as exc:
        return "%s: %s" % (type(exc).__name__, str(exc)[:70])


# --------------------------------------------------------------------------- окно

class Settings(tk.Toplevel):
    W, H = S(640), S(720)
    ROW_H = S(42)

    def __init__(self, parent, t, on_saved, on_wizard=None):
        super().__init__(parent)
        self.t = t
        self.on_saved = on_saved
        self.on_wizard = on_wizard
        self.cfg = read_config()

        self.title(t("set.title"))
        self.configure(bg=C.bg)
        h = min(self.H, parent.winfo_screenheight() - S(160))
        self.geometry("%dx%d+%d+%d" % (
            self.W, h,
            parent.winfo_rootx() + S(60), max(20, parent.winfo_rooty() + S(30))))
        self.minsize(S(560), S(520))
        self.transient(parent)

        self.f_h1 = tkfont.Font(family="Segoe UI Semibold", size=14)
        self.f_h2 = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.f_body = tkfont.Font(family="Segoe UI", size=10)
        self.f_small = tkfont.Font(family="Segoe UI", size=9)
        self.f_tiny = tkfont.Font(family="Segoe UI", size=8)

        self.models = None       # None — грузится, список или строка ошибки
        self.folders = None
        self._mail = queue.Queue()
        self._hover = (None, -1)
        self._checking = None

        self._build()
        self._load()
        self._pump()

    # ------------------------------------------------------------------ каркас
    def _build(self):
        t = self.t

        head = tk.Frame(self, bg=C.bg)
        head.pack(fill="x", padx=S(22), pady=(S(18), S(6)))
        tk.Label(head, text=t("set.title"), bg=C.bg, fg=C.text,
                 font=self.f_h1).pack(side="left")
        Button(head, t("set.close"), self.destroy).pack(side="right")

        # формат
        block = tk.Frame(self, bg=C.bg)
        block.pack(fill="x", padx=S(22), pady=(S(14), 0))
        tk.Label(block, text=t("set.format"), bg=C.bg, fg=C.faint,
                 font=self.f_tiny).pack(anchor="w", pady=(0, S(8)))
        self.seg = Segmented(
            block,
            [("shorts", t("mode.shorts")), ("video", t("mode.video")),
             ("auto", t("mode.auto"))],
            (self.cfg["youtube"].get("mode") or "auto"),
            # Segmented множит сам — передаём логические единицы
            self._set_mode, width=640 - 44, height=36,
        )
        self.seg.pack(anchor="w")
        self.mode_hint = tk.Label(block, text="", bg=C.bg, fg=C.muted,
                                  font=self.f_small, justify="left", anchor="w",
                                  wraplength=self.W - S(60))
        self.mode_hint.pack(anchor="w", pady=(S(8), 0))
        self._update_mode_hint()

        # модель
        tk.Label(self, text=t("set.model"), bg=C.bg, fg=C.faint,
                 font=self.f_tiny).pack(anchor="w", padx=S(22), pady=(S(18), S(6)))
        self.model_cv = tk.Canvas(self, bg=C.bg, highlightthickness=0, bd=0, height=S(190))
        self.model_cv.pack(fill="x", padx=S(22))
        self._wire(self.model_cv, "model")

        # папка
        tk.Label(self, text=t("set.folder"), bg=C.bg, fg=C.faint,
                 font=self.f_tiny).pack(anchor="w", padx=S(22), pady=(S(16), S(6)))
        self.folder_cv = tk.Canvas(self, bg=C.bg, highlightthickness=0, bd=0)
        self.folder_cv.pack(fill="both", expand=True, padx=S(22), pady=(0, S(4)))
        self._wire(self.folder_cv, "folder")

        foot = tk.Frame(self, bg=C.bg)
        foot.pack(side="bottom", fill="x", padx=S(22), pady=(S(6), S(14)))
        if self.on_wizard:
            Button(foot, t("set.wizard"), self._wizard).pack(side="left")
        self.status = tk.Label(foot, text="", bg=C.bg, fg=C.faint, font=self.f_small)
        self.status.pack(side="right")

    def _wizard(self):
        """Мастер поверх настроек: человек мог просто забыть, как всё настроить."""
        self.destroy()
        self.on_wizard()

    def _wire(self, cv, kind):
        cv.bind("<Button-1>", lambda e: self._click(kind, e))
        cv.bind("<Motion>", lambda e: self._motion(kind, e))
        cv.bind("<Leave>", lambda e: self._leave(kind))
        cv.bind("<Configure>", lambda e: self._render(kind))
        cv.bind("<MouseWheel>", lambda e: self._scroll(kind, e))
        setattr(self, "_scroll_" + kind, 0)

    # ------------------------------------------------------------------ данные
    def _load(self):
        self.status.configure(text=self.t("set.loading"))

        def work():
            self._mail.put({"models": fetch_models(self.cfg["metadata"])})
            self._mail.put({"folders": fetch_folders()})
            self._mail.put({"__done": True})

        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            while True:
                msg = self._mail.get_nowait()
                if msg.pop("__done", None):
                    self.status.configure(text="")
                vision = msg.pop("__vision", None)
                if vision:
                    name, ok = vision
                    self._checking = None
                    # Модель ставим в любом случае: проверка — это совет.
                    # Она опирается на необязательный эндпоинт и ошибается.
                    self._apply_model(name)
                    if ok is False:
                        self.status.configure(text=self.t("set.novision.warn"),
                                              fg=C.warn)

                if "models" in msg:
                    self.models = msg["models"]
                    self._render("model")
                if "folders" in msg:
                    self.folders = msg["folders"]
                    self._render("folder")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(120, self._pump)

    # ------------------------------------------------------------------ формат
    def _update_mode_hint(self):
        self.mode_hint.configure(
            text=self.t("mode.hint." + (self.cfg["youtube"].get("mode") or "auto")))

    def _set_mode(self, code):
        self.cfg["youtube"]["mode"] = code
        write_config(self.cfg)
        self._update_mode_hint()
        self.status.configure(text=self.t("set.saved"))
        self.on_saved()

    # ------------------------------------------------------------------ строки списков
    def _rows(self, kind):
        """Список строк для отрисовки: (заголовок, подпись, выбрана ли, данные)."""
        t = self.t
        if kind == "model":
            data, mcfg = self.models, self.cfg["metadata"]
            current = mcfg.get("vision_model") if mcfg.get("vision") else mcfg.get("local_model")
            if data is None:
                return None
            if isinstance(data, str):
                return data
            rows = []
            for row in data:
                bits = []
                if row["context"]:
                    bits.append(t("set.context", n=row["context"]))
                if row["loaded"]:
                    bits.append(t("set.inmemory"))
                if row["name"] == self._checking:
                    bits = [t("set.checking")]
                rows.append((row["name"].split("/")[-1], " · ".join(bits),
                             row["name"] in (current, self._checking), row["name"]))
            return rows

        data = self.folders
        current = self.cfg["drive"].get("folder_id")
        processed = self.cfg["drive"].get("processed_folder_id")
        if data is None:
            return None
        if isinstance(data, str):
            return data
        rows = []
        for fid, name in data:
            note = t("set.isprocessed") if fid == processed else ""
            rows.append((name, note, fid == current, fid))
        return rows

    def _fit_height(self, cv, rows):
        """Раздел моделей не тянется, поэтому подгоняем высоту под содержимое."""
        if rows is None:
            need = S(34)
        elif isinstance(rows, str):
            need = S(60)
        else:
            need = min(len(rows), 5) * (self.ROW_H + S(6)) + S(6)
        if cv.winfo_height() != need:
            cv.configure(height=need)

    def _render(self, kind):
        cv = self.model_cv if kind == "model" else self.folder_cv
        cv.delete("all")
        rows = self._rows(kind)
        if kind == "model":
            self._fit_height(cv, rows)
        width = cv.winfo_width() or self.W - S(44)

        if rows is None:
            cv.create_text(S(14), S(18), text=self.t("set.loading"), anchor="w",
                           fill=C.faint, font=self.f_small)
            return
        if isinstance(rows, str):
            round_rect(cv, 0, S(4), width - 2, S(52), S(10), fill=C.surface, outline=C.bad)
            cv.create_text(S(16), S(28), text=self.t("set.error") + ": " + rows, anchor="w",
                           fill=C.bad, font=self.f_small)
            return
        if not rows:
            cv.create_text(S(14), S(18), text=self.t("set.nothing"), anchor="w",
                           fill=C.faint, font=self.f_small)
            return

        hover_kind, hover_idx = self._hover
        top = S(2) - getattr(self, "_scroll_" + kind)
        for idx, (title, note, selected, _payload) in enumerate(rows):
            y = top + idx * (self.ROW_H + S(6))
            if y + self.ROW_H < 0 or y > cv.winfo_height():
                continue
            hot = hover_kind == kind and hover_idx == idx
            round_rect(cv, 0, y, width - 2, y + self.ROW_H, S(10),
                       fill=C.raised if (selected or hot) else C.surface,
                       outline=C.accent if selected else C.line,
                       width=2 if selected else 1)
            if selected:
                cv.create_oval(S(16), y + self.ROW_H / 2 - S(4), S(24), y + self.ROW_H / 2 + S(4),
                               fill=C.accent, outline="")
            cv.create_text(S(36), y + self.ROW_H / 2 - (S(7) if note else 0),
                           text=ellipsis(title, self.f_body, width - S(60)),
                           anchor="w", fill=C.text, font=self.f_body)
            if note:
                cv.create_text(S(36), y + self.ROW_H / 2 + S(9), text=note, anchor="w",
                               fill=C.faint, font=self.f_tiny)

    def _row_at(self, kind, y):
        rows = self._rows(kind)
        if not isinstance(rows, list):
            return -1
        rel = y - (S(2) - getattr(self, "_scroll_" + kind))
        idx = int(rel // (self.ROW_H + S(6)))
        if 0 <= idx < len(rows) and rel % (self.ROW_H + S(6)) <= self.ROW_H:
            return idx
        return -1

    def _motion(self, kind, e):
        idx = self._row_at(kind, e.y)
        if self._hover != (kind, idx):
            self._hover = (kind, idx)
            self._render(kind)

    def _leave(self, kind):
        self._hover = (None, -1)
        self._render(kind)

    def _scroll(self, kind, e):
        rows = self._rows(kind)
        if not isinstance(rows, list):
            return
        cv = self.model_cv if kind == "model" else self.folder_cv
        span = len(rows) * (self.ROW_H + S(6))
        limit = max(0, span - cv.winfo_height() + S(10))
        key = "_scroll_" + kind
        setattr(self, key, min(limit, max(0, getattr(self, key) - e.delta // 3)))
        self._render(kind)

    def _click(self, kind, e):
        rows = self._rows(kind)
        idx = self._row_at(kind, e.y)
        if not isinstance(rows, list) or idx < 0:
            return
        payload = rows[idx][3]
        if kind == "model":
            self._pick_model(payload)
        else:
            self._pick_folder(payload)

    # ------------------------------------------------------------------ выбор
    def _pick_model(self, name):
        """Проверку на поддержку картинок уводим в поток.

        Она ходит по сети и ждёт до 20 секунд — в главном потоке это выглядит
        как зависшее окно.
        """
        mcfg = self.cfg["metadata"]

        if not mcfg.get("vision"):
            self._apply_model(name)
            return

        if self._checking:
            return
        self._checking = name
        self.status.configure(text=self.t("set.checking"), fg=C.muted)
        self._render("model")

        def work():
            try:
                import meta
                vision = meta.check_vision(mcfg, name)
            except Exception:
                vision = None
            self._mail.put({"__vision": (name, vision)})

        threading.Thread(target=work, daemon=True).start()

    def _apply_model(self, name):
        mcfg = self.cfg["metadata"]
        mcfg["vision_model" if mcfg.get("vision") else "local_model"] = name
        write_config(self.cfg)
        self.status.configure(text=self.t("set.saved"), fg=C.faint)
        self._render("model")
        self.on_saved()

    def _pick_folder(self, folder_id):
        if folder_id == self.cfg["drive"].get("processed_folder_id"):
            self.status.configure(text=self.t("set.loopwarn"), fg=C.bad)
            return
        self.cfg["drive"]["folder_id"] = folder_id
        write_config(self.cfg)
        self.status.configure(text=self.t("set.saved"), fg=C.faint)
        self._render("folder")
        self.on_saved()
