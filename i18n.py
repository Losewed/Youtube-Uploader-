"""Строки интерфейса на двух языках.

Выбор хранится в ui.json рядом со скриптом, чтобы не трогать config.json:
в нём лежат рабочие настройки, и ронять их из-за оформления не хочется.
"""
from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
PREFS = BASE / "ui.json"

LANGS = ("ru", "en")

STRINGS = {
    "ru": {
        "app.title": "Загрузка видео на YouTube",

        "btn.refresh": "Обновить",
        "btn.drive": "Сверить с Диском",
        "btn.run": "Проверить папку",
        "btn.dry": "Черновик",
        "btn.load": "Разбудить модель",
        "btn.unload": "Выгрузить модель",
        "btn.log": "Журнал",

        "tile.countdown": "до публикации",
        "tile.check": "проверка папки",
        "tile.model": "модель",
        "tile.queue": "в очереди",

        "tile.countdown.none": "нет",
        "tile.countdown.empty": "очередь публикаций пуста",
        "tile.check.sub": "каждые 30 мин, 10:00–00:00",
        "tile.model.sub": "поднимается только на время работы",
        "tile.queue.unknown": "?",
        "tile.queue.hint": "нажмите «Сверить с Диском»",
        "tile.queue.empty": "пусто",
        "tile.queue.none": "новых роликов нет",
        "tile.queue.error": "ошибка",

        "sec.videos": "РОЛИКИ",
        "sec.detail": "ПОДРОБНОСТИ",
        "sec.description": "ОПИСАНИЕ",
        "sec.tags": "ТЕГИ",

        "list.empty.title": "Пока ничего не загружено",
        "list.empty.sub": "Положите видео в папку «готовые видео»",
        "hint.title": "Как добавить ролик",
        "hint.line1": "Положите файл в папку «готовые видео»",
        "hint.line2": "на Google Диске — остальное произойдёт само",

        "row.noposter": "без обложки",
        "row.untitled": "без названия",
        "row.will": "выйдет {date}",
        "row.was": "вышел {date}",
        "row.nodate": "без даты публикации",
        "date.at": "{d} в {t}",

        "detail.in": "выйдет через {h} ч {m} мин",
        "detail.published": "опубликован {date}",
        "detail.unscheduled": "публикация не назначена",
        "detail.nodesc": "(в записи не сохранено — ролик залит раньше)",
        "detail.file": "Файл: ",
        "detail.author": "Текст написала: ",
        "author.local": "локальная модель",
        "author.claude": "Claude API",

        "status.probing": "Проверяю модель и расписание…",
        "status.drive": "Спрашиваю Google Диск…",
        "status.updated": "Обновлено в {time}",
        "status.launched": "Запущено в отдельном окне: {cmd}",
        "status.nolog": "Журнала пока нет",

        "model.checking": "проверяю…",
        "model.down": "сервер не отвечает",
        "model.unset": "модель не выбрана",
        "model.missing": "нет модели {name}",

        "task.none": "задача не создана",
        "task.unknown": "не определить",
        "btn.settings": "Настройки",

        "set.title": "Настройки",
        "set.wizard": "Пройти настройку заново",
        "set.close": "Закрыть",
        "set.format": "ФОРМАТ ПУБЛИКАЦИИ",
        "set.model": "МОДЕЛЬ ДЛЯ МЕТАДАННЫХ",
        "set.folder": "ПАПКА С ВИДЕО НА ДИСКЕ",
        "set.loading": "Загружаю…",
        "set.error": "Ошибка",
        "set.nothing": "ничего не нашлось",
        "set.saved": "Сохранено",
        "set.checking": "проверяю…",
        "set.context": "контекст {n}",
        "set.inmemory": "в памяти",
        "set.isprocessed": "сюда уезжают залитые",
        "set.novision": "Эта модель не умеет смотреть картинки — не подойдёт",
        "set.novision.warn": "Сохранено. Но сервер считает, что модель не смотрит картинки — проверьте черновиком",
        "set.loopwarn": "Это папка для залитых, брать из неё нельзя",

        "mode.shorts": "Shorts",
        "mode.video": "Обычное видео",
        "mode.auto": "Авто",
        "mode.hint.shorts": "Все ролики уходят как Shorts: горизонтальный кадр обрезается до 1080×1920, в описание добавляется #shorts.",
        "mode.hint.video": "Все ролики уходят обычным видео: кадр не трогается, описание чуть подробнее.",
        "mode.hint.auto": "Решает исходник: вертикальный ролик до 3 минут уйдёт в Shorts, всё остальное — обычным видео.",

        "wz.title": "Первая настройка",
        "wz.back": "Назад",
        "wz.next": "Дальше",
        "wz.finish": "Готово",
        "wz.skip": "Пропустить",
        "wz.step": "Шаг {n} из {total}",

        "wz.1.title": "Что понадобится",
        "wz.1.intro": "Программа берёт видео из папки на Google Диске, придумывает название и описание и заливает на ваш канал YouTube по расписанию.",
        "wz.1.need": "Для работы нужно три вещи:",
        "wz.1.google": "Доступ к Google — настроим на следующем шаге",
        "wz.1.ffmpeg": "ffmpeg — режет кадры и меняет формат видео",
        "wz.1.model": "Модель, которая пишет тексты — локальная или Claude",
        "wz.1.ffmpeg.ok": "ffmpeg найден",
        "wz.1.ffmpeg.no": "ffmpeg не найден. Поставьте: winget install Gyan.FFmpeg, потом перезапустите программу",

        "wz.2.title": "Доступ к Google",
        "wz.2.secret.no": "Нет файла client_secret.json",
        "wz.2.secret.how": "Его выдаёт Google Cloud Console: создайте проект, включите Google Drive API и YouTube Data API v3, выпустите OAuth-клиент типа «Desktop app» и скачайте JSON. Положите его в папку программы под именем client_secret.json.",
        "wz.2.secret.ok": "Файл client_secret.json на месте",
        "wz.2.open": "Открыть папку программы",
        "wz.2.console": "Открыть Google Cloud Console",
        "wz.2.login": "Войти в Google",
        "wz.2.logging": "Открывается браузер — выберите аккаунт и разрешите доступ…",
        "wz.2.done": "Вход выполнен. Канал: {channel}",
        "wz.2.fail": "Войти не вышло: {error}",
        "wz.2.already": "Вход уже выполнен. Канал: {channel}",

        "wz.3.title": "Папка с видео",
        "wz.3.intro": "Выберите папку на Диске, откуда брать ролики. Залитые файлы будут уезжать в корзину.",
        "wz.3.create": "Создать папки «видео / готовые видео»",
        "wz.3.created": "Папки созданы и выбраны",
        "wz.3.loading": "Смотрю, какие папки есть на Диске…",

        "wz.4.title": "Кто пишет тексты",
        "wz.4.local": "Локальная модель",
        "wz.4.claude": "Claude API",
        "wz.4.filename": "Пока никто",
        "wz.4.local.hint": "Сервер Unsloth, Ollama или LM Studio. Работает бесплатно, но должен быть запущен.",
        "wz.4.claude.hint": "Платно по факту использования, около 5 центов за ролик. Нужен ключ с console.anthropic.com.",
        "wz.4.filename.hint": "Название возьмётся из имени файла, описание и теги останутся пустыми. Настроить можно потом.",
        "wz.4.address": "Адрес сервера",
        "wz.4.key": "Ключ API",
        "wz.4.check": "Проверить",
        "wz.4.checking": "Проверяю…",
        "wz.4.found": "Сервер отвечает, моделей: {n}",
        "wz.4.nomodels": "Сервер отвечает, но моделей нет",
        "wz.4.down": "Сервер не отвечает",
        "wz.4.pick": "Выберите модель, которая умеет смотреть картинки",

        "wz.5.title": "Расписание",
        "wz.5.publish": "Во сколько публиковать",
        "wz.5.format": "Формат роликов",
        "wz.5.task": "Проверять папку автоматически",
        "wz.5.task.hint": "Каждые 30 минут с 10:00 до 00:00. Без этого придётся запускать вручную кнопкой.",
        "wz.5.task.ok": "Расписание создано",
        "wz.5.task.fail": "Не удалось создать задачу: {error}",
        "wz.5.ready": "Всё готово. Положите видео в выбранную папку — остальное произойдёт само.",
        "task.in": "через {m} мин",
    },
    "en": {
        "app.title": "YouTube upload panel",

        "btn.refresh": "Refresh",
        "btn.drive": "Check Drive",
        "btn.run": "Scan folder now",
        "btn.dry": "Dry run",
        "btn.load": "Load model",
        "btn.unload": "Unload model",
        "btn.log": "Log",

        "tile.countdown": "next publish in",
        "tile.check": "next folder scan",
        "tile.model": "model",
        "tile.queue": "in queue",

        "tile.countdown.none": "none",
        "tile.countdown.empty": "nothing scheduled",
        "tile.check.sub": "every 30 min, 10:00–00:00",
        "tile.model.sub": "loaded only while working",
        "tile.queue.unknown": "?",
        "tile.queue.hint": "press “Check Drive”",
        "tile.queue.empty": "empty",
        "tile.queue.none": "no new videos",
        "tile.queue.error": "error",

        "sec.videos": "VIDEOS",
        "sec.detail": "DETAILS",
        "sec.description": "DESCRIPTION",
        "sec.tags": "TAGS",

        "list.empty.title": "Nothing uploaded yet",
        "list.empty.sub": "Drop a video into the “готовые видео” folder",
        "hint.title": "How to add a video",
        "hint.line1": "Drop a file into the “готовые видео” folder",
        "hint.line2": "on Google Drive — the rest happens by itself",

        "row.noposter": "no poster",
        "row.untitled": "untitled",
        "row.will": "goes live {date}",
        "row.was": "published {date}",
        "row.nodate": "no publish date",
        "date.at": "{d} at {t}",

        "detail.in": "goes live in {h} h {m} min",
        "detail.published": "published {date}",
        "detail.unscheduled": "publishing not scheduled",
        "detail.nodesc": "(not stored — uploaded before this field existed)",
        "detail.file": "Source file: ",
        "detail.author": "Text written by: ",
        "author.local": "local model",
        "author.claude": "Claude API",

        "status.probing": "Checking model and schedule…",
        "status.drive": "Asking Google Drive…",
        "status.updated": "Updated at {time}",
        "status.launched": "Started in a separate window: {cmd}",
        "status.nolog": "No log yet",

        "model.checking": "checking…",
        "model.down": "server not responding",
        "model.unset": "no model selected",
        "model.missing": "model {name} is missing",

        "task.none": "task not created",
        "task.unknown": "cannot tell",
        "btn.settings": "Settings",

        "set.title": "Settings",
        "set.wizard": "Run setup again",
        "set.close": "Close",
        "set.format": "PUBLISHING FORMAT",
        "set.model": "MODEL FOR METADATA",
        "set.folder": "SOURCE FOLDER ON DRIVE",
        "set.loading": "Loading…",
        "set.error": "Error",
        "set.nothing": "nothing found",
        "set.saved": "Saved",
        "set.checking": "checking…",
        "set.context": "context {n}",
        "set.inmemory": "in memory",
        "set.isprocessed": "uploaded files go here",
        "set.novision": "This model cannot look at images — it will not work",
        "set.novision.warn": "Saved. But the server says this model cannot see images — check with a dry run",
        "set.loopwarn": "That is the folder for uploaded files",

        "mode.shorts": "Shorts",
        "mode.video": "Regular video",
        "mode.auto": "Auto",
        "mode.hint.shorts": "Everything goes out as Shorts: horizontal frames are cropped to 1080×1920 and #shorts is added to the description.",
        "mode.hint.video": "Everything goes out as a regular video: the frame is left alone, the description is a bit longer.",
        "mode.hint.auto": "The source decides: a vertical clip under 3 minutes becomes a Short, anything else a regular video.",

        "wz.title": "First-time setup",
        "wz.back": "Back",
        "wz.next": "Next",
        "wz.finish": "Done",
        "wz.skip": "Skip",
        "wz.step": "Step {n} of {total}",

        "wz.1.title": "What you will need",
        "wz.1.intro": "The program takes videos from a Google Drive folder, writes a title and description for each, and uploads them to your YouTube channel on a schedule.",
        "wz.1.need": "Three things are required:",
        "wz.1.google": "Google access — we will set it up on the next step",
        "wz.1.ffmpeg": "ffmpeg — grabs frames and changes the video format",
        "wz.1.model": "A model that writes the text — local or Claude",
        "wz.1.ffmpeg.ok": "ffmpeg found",
        "wz.1.ffmpeg.no": "ffmpeg not found. Install it: winget install Gyan.FFmpeg, then restart the program",

        "wz.2.title": "Google access",
        "wz.2.secret.no": "client_secret.json is missing",
        "wz.2.secret.how": "Google Cloud Console issues it: create a project, enable Google Drive API and YouTube Data API v3, create an OAuth client of type “Desktop app” and download the JSON. Put it into the program folder as client_secret.json.",
        "wz.2.secret.ok": "client_secret.json is in place",
        "wz.2.open": "Open program folder",
        "wz.2.console": "Open Google Cloud Console",
        "wz.2.login": "Sign in with Google",
        "wz.2.logging": "A browser is opening — pick the account and allow access…",
        "wz.2.done": "Signed in. Channel: {channel}",
        "wz.2.fail": "Sign-in failed: {error}",
        "wz.2.already": "Already signed in. Channel: {channel}",

        "wz.3.title": "Video folder",
        "wz.3.intro": "Pick the Drive folder to take videos from. Uploaded files go to the trash afterwards.",
        "wz.3.create": "Create “видео / готовые видео” folders",
        "wz.3.created": "Folders created and selected",
        "wz.3.loading": "Looking at the folders on your Drive…",

        "wz.4.title": "Who writes the text",
        "wz.4.local": "Local model",
        "wz.4.claude": "Claude API",
        "wz.4.filename": "Nobody for now",
        "wz.4.local.hint": "An Unsloth, Ollama or LM Studio server. Free, but it has to be running.",
        "wz.4.claude.hint": "Paid per use, roughly 5 cents per video. Needs a key from console.anthropic.com.",
        "wz.4.filename.hint": "The title comes from the file name, description and tags stay empty. You can set this up later.",
        "wz.4.address": "Server address",
        "wz.4.key": "API key",
        "wz.4.check": "Check",
        "wz.4.checking": "Checking…",
        "wz.4.found": "Server responds, models: {n}",
        "wz.4.nomodels": "Server responds but has no models",
        "wz.4.down": "Server not responding",
        "wz.4.pick": "Pick a model that can look at images",

        "wz.5.title": "Schedule",
        "wz.5.publish": "When to publish",
        "wz.5.format": "Video format",
        "wz.5.task": "Scan the folder automatically",
        "wz.5.task.hint": "Every 30 minutes from 10:00 to 00:00. Without it you would start each run by hand.",
        "wz.5.task.ok": "Schedule created",
        "wz.5.task.fail": "Could not create the task: {error}",
        "wz.5.ready": "All set. Drop a video into the chosen folder — the rest happens by itself.",
        "task.in": "in {m} min",
    },
}


def load_language() -> str:
    try:
        value = json.loads(PREFS.read_text(encoding="utf-8")).get("language")
        if value in LANGS:
            return value
    except Exception:
        pass
    return "ru"


def save_language(lang: str) -> None:
    try:
        PREFS.write_text(json.dumps({"language": lang}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    except Exception:
        pass


class T:
    """Переводчик. t('btn.refresh') или t('task.in', m=12)."""

    def __init__(self, lang: str = "ru"):
        self.lang = lang if lang in LANGS else "ru"

    def __call__(self, key: str, **fmt) -> str:
        text = STRINGS[self.lang].get(key) or STRINGS["ru"].get(key) or key
        return text.format(**fmt) if fmt else text

    def other(self) -> str:
        return "en" if self.lang == "ru" else "ru"

    def date(self, when) -> str:
        """05.09 в 03:30 / 05.09 at 03:30 — разделитель зависит от языка."""
        return self(
            "date.at",
            d=when.strftime("%d.%m"),
            t=when.strftime("%H:%M"),
        )
