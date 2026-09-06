"""Общее оформление: палитра, рисование скруглений, кнопки.

Отдельный модуль, чтобы окно настроек и главная панель не дублировали
одно и то же и не тянули друг друга по кругу через импорты.
"""
from __future__ import annotations

import ctypes
import tkinter as tk
from tkinter import font as tkfont

import i18n


def _enable_dpi_awareness() -> float:
    """Говорим Windows, что рисуем сами, и узнаём настоящий масштаб экрана.

    Без этого система растягивает готовое окно постфактум, и всё выглядит
    мыльным. Вызывать нужно до создания первого окна Tk.
    Возвращает множитель: 1.0 при 100%, 1.25 при 125% и так далее.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return 1.0
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        return 1.0


SCALE = _enable_dpi_awareness()


def S(px: float) -> int:
    """Размер в точках с поправкой на масштаб экрана.

    Шрифты Tk задаются в пунктах и подстраиваются сами, а вот все наши
    прямоугольники и отступы — в точках, поэтому их множим вручную.
    """
    return int(round(px * SCALE))


class C:
    """Цвета. Тёмная схема с одним акцентом — синим."""
    bg = "#0d0f14"
    surface = "#151922"
    raised = "#1c2130"
    line = "#232838"

    text = "#eceff6"
    muted = "#8b93a8"
    faint = "#5a6178"

    accent = "#6c8cff"
    accent_dim = "#3d4d8f"
    ok = "#3ecf8e"
    warn = "#f5a524"
    bad = "#f45b5b"


RADIUS = S(14)


def round_rect(cv: tk.Canvas, x1, y1, x2, y2, r=RADIUS, **kw):
    """Скруглённый прямоугольник. Tk такого не умеет, рисуем полигоном."""
    r = min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def ellipsis(text: str, fnt: tkfont.Font, width: int) -> str:
    """Обрезает строку по ширине в пикселях, добавляя многоточие."""
    if fnt.measure(text) <= width:
        return text
    while text and fnt.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


def wrap(text, fnt: tkfont.Font, width) -> list[str]:
    """Переносит текст по словам под заданную ширину в пикселях."""
    lines = []
    for para in str(text).split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for word in para.split(" "):
            probe = (cur + " " + word).strip()
            if fnt.measure(probe) <= width:
                cur = probe
            else:
                if cur:
                    lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
    return lines


class Button(tk.Canvas):
    """Плоская кнопка со скруглением, наведением и нажатием."""

    def __init__(self, parent, text, command, *, kind="ghost", width=None,
                 height=None, bg=None):
        fnt = tkfont.Font(family="Segoe UI", size=10)
        height = height or S(38)
        w = width or (fnt.measure(text) + S(34))
        super().__init__(parent, width=w, height=height, bg=bg or C.bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.fnt = fnt
        self.kind = kind
        self.command = command
        self.text = text
        # _w и _h заняты самим tkinter под имя виджета — берём свои
        self._bw, self._bh = w, height
        self._state = "normal"
        self.enabled = True
        self._draw()
        self.bind("<Enter>", lambda e: self._set("hover") if self.enabled else None)
        self.bind("<Leave>", lambda e: self._set("normal"))
        self.bind("<ButtonPress-1>",
                  lambda e: self._set("press") if self.enabled else None)
        self.bind("<ButtonRelease-1>", self._release)

    def set_enabled(self, on: bool):
        """Выключенная кнопка должна и выглядеть выключенной, а не только молчать."""
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()

    def _palette(self):
        if not getattr(self, "enabled", True):
            return C.surface, C.faint, C.line
        if self.kind == "primary":
            fills = {"normal": C.accent, "hover": "#7d99ff", "press": C.accent_dim}
            return fills[self._state], "#0b0e16", None
        fills = {"normal": C.surface, "hover": C.raised, "press": C.surface}
        return fills[self._state], C.text, C.line

    def _draw(self):
        self.delete("all")
        fill, fg, outline = self._palette()
        round_rect(self, 1, 1, self._bw - 1, self._bh - 1, S(10),
                   fill=fill, outline=outline or fill, width=1)
        self.create_text(self._bw / 2, self._bh / 2 + 1, text=self.text,
                         fill=fg, font=self.fnt)

    def _set(self, state):
        self._state = state
        self._draw()

    def _release(self, _e):
        inside = self._state == "press"
        self._set("hover")
        if inside and self.command and getattr(self, "enabled", True):
            self.command()


class Segmented(tk.Canvas):
    """Переключатель из нескольких равных частей: активная подсвечена."""

    def __init__(self, parent, options, current, on_change, *, width=360, height=34,
                 bg=None):
        width, height = S(width), S(height)
        super().__init__(parent, width=width, height=height, bg=bg or C.bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.fnt = tkfont.Font(family="Segoe UI Semibold", size=9)
        self.options = options          # [(код, подпись), ...]
        self.current = current
        self.on_change = on_change
        self._sw, self._sh = width, height
        self._draw()
        self.bind("<Button-1>", self._click)

    def _draw(self):
        self.delete("all")
        round_rect(self, 1, 1, self._sw - 1, self._sh - 1, S(9),
                   fill=C.surface, outline=C.line)
        step = (self._sw - 4) / len(self.options)
        for idx, (code, label) in enumerate(self.options):
            x1 = 3 + idx * step
            active = code == self.current
            if active:
                round_rect(self, x1, 3, x1 + step - 2, self._sh - 3, S(7),
                           fill=C.accent, outline=C.accent)
            self.create_text(x1 + (step - 2) / 2, self._sh / 2 + 1, text=label,
                             font=self.fnt,
                             fill="#0b0e16" if active else C.muted)

    def set(self, code):
        self.current = code
        self._draw()

    def _click(self, e):
        step = (self._sw - 4) / len(self.options)
        idx = min(len(self.options) - 1, max(0, int((e.x - 3) // step)))
        code = self.options[idx][0]
        if code != self.current:
            self.current = code
            self._draw()
            self.on_change(code)


class LangSwitch(Segmented):
    """Переключатель RU / EN."""

    def __init__(self, parent, current, on_change):
        super().__init__(parent, [(c, c.upper()) for c in i18n.LANGS],
                         current, on_change, width=78, height=32)
