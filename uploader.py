"""Берёт новые видео из папки Google Drive, придумывает метаданные через Claude
и заливает их на YouTube с отложенной публикацией.

Команды:
    python uploader.py run [--limit N] [--dry-run]
    python uploader.py folders [подстрока]   — найти ID папки на Диске
    python uploader.py list                  — что лежит в папке и что уже загружено
    python uploader.py plan                  — на какое время встанут ближайшие ролики
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

import gauth
import meta

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
STATE = BASE / "state.json"
LOCK = BASE / ".lock"
LOG_FILE = BASE / "logs" / "uploader.log"

RETRIABLE_STATUS = {500, 502, 503, 504}
MAX_UPLOAD_RETRIES = 6

log = logging.getLogger("uploader")


# --------------------------------------------------------------------------- инфраструктура

def setup_logging(verbose: bool = False) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Под планировщиком консоль может быть в cp866, где нет тире и кавычек-ёлочек
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit(
            "Нет config.json. Скопируйте config.example.json в config.json и заполните folder_id."
        )
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    folder = (cfg.get("drive") or {}).get("folder_id") or ""
    if not folder or folder.startswith("ВСТАВЬТЕ"):
        raise RuntimeError(
            "В config.json не задан drive.folder_id. Найдите ID папки командой:\n"
            "  python uploader.py folders <часть имени папки>"
        )
    return cfg


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("state.json повреждён — начинаю с чистого состояния")
    return {"uploaded": {}, "last_publish_at": None}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


class SingleRun:
    """Не даёт двум копиям скрипта работать одновременно."""

    @staticmethod
    def _alive(pid: int) -> bool:
        """Жив ли процесс с таким номером. На Windows спрашиваем у tasklist."""
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "PID eq {}".format(pid), "/NH"],
                capture_output=True, text=True, errors="replace", timeout=15,
            ).stdout
        except Exception:
            return True  # не смогли проверить — считаем живым, так безопаснее
        return str(pid) in out

    def __enter__(self):
        if LOCK.exists():
            age = time.time() - LOCK.stat().st_mtime
            try:
                pid = int(LOCK.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pid = None

            if pid is not None and self._alive(pid):
                raise SystemExit(
                    "Другой запуск ещё идёт (процесс {}). Выхожу.".format(pid)
                )
            log.warning("Нашёл брошенный .lock от процесса %s (%.0f мин) — забираю",
                        pid, age / 60)
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return self

    def __exit__(self, *exc):
        LOCK.unlink(missing_ok=True)
        return False


# --------------------------------------------------------------------------- Google Drive

VIDEO_FIELDS = (
    "nextPageToken, files(id, name, size, mimeType, createdTime, modifiedTime, "
    "parents, videoMediaMetadata(durationMillis))"
)


def _subfolder_ids(drive, folder_id: str) -> list[str]:
    found, queue = [], [folder_id]
    while queue:
        current = queue.pop()
        page = None
        while True:
            resp = drive.files().list(
                q=f"'{current}' in parents and trashed = false "
                f"and mimeType = 'application/vnd.google-apps.folder'",
                fields="nextPageToken, files(id, name)",
                pageToken=page,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute(num_retries=5)
            for item in resp.get("files", []):
                found.append(item["id"])
                queue.append(item["id"])
            page = resp.get("nextPageToken")
            if not page:
                break
    return found


def list_videos(drive, cfg: dict) -> list[dict]:
    folder_id = cfg["drive"]["folder_id"]
    folders = [folder_id]
    if cfg["drive"].get("recurse"):
        folders += _subfolder_ids(drive, folder_id)

    files: list[dict] = []
    for fid in folders:
        page = None
        while True:
            resp = drive.files().list(
                q=f"'{fid}' in parents and trashed = false and mimeType contains 'video/'",
                fields=VIDEO_FIELDS,
                orderBy="createdTime",
                pageSize=200,
                pageToken=page,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute(num_retries=5)
            files.extend(resp.get("files", []))
            page = resp.get("nextPageToken")
            if not page:
                break

    min_age = int(cfg["drive"].get("min_age_minutes", 2))
    if min_age > 0:
        cutoff = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=min_age)
        settled = []
        for f in files:
            if _parse_rfc3339(f["modifiedTime"]) > cutoff:
                log.info("Пропускаю %s — файл изменён меньше %d мин назад", f["name"], min_age)
            else:
                settled.append(f)
        files = settled

    files.sort(key=lambda f: f.get("createdTime", ""))
    return files


def _parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def download(drive, file: dict, dest_dir: Path) -> Path:
    dest = dest_dir / file["name"]
    request = drive.files().get_media(fileId=file["id"], supportsAllDrives=True)
    total = int(file.get("size") or 0)
    log.info("Скачиваю %s (%s)", file["name"], _human(total))

    with io.FileIO(dest, "wb") as handle:
        downloader = MediaIoBaseDownload(handle, request, chunksize=16 * 1024 * 1024)
        done = False
        last_pct = -10
        while not done:
            status, done = downloader.next_chunk(num_retries=5)
            if status:
                pct = int(status.progress() * 100)
                if pct - last_pct >= 10:
                    log.info("  скачано %d%%", pct)
                    last_pct = pct
    return dest


def resolve_mode(cfg: dict, video: Path) -> str:
    """'shorts' или 'video' для этого ролика.

    В режиме auto решает сам исходник: YouTube считает роликом Shorts только
    вертикальный кадр длительностью до трёх минут. Значит вертикальный экспорт
    уходит в Shorts, горизонтальный — обычным видео, и ничего указывать не надо.
    """
    mode = (cfg["youtube"].get("mode") or "auto").lower()
    if mode in ("shorts", "video"):
        return mode

    size = meta.video_size(video)
    duration = meta.video_duration(video) or 0
    if size and size[1] >= size[0] and 0 < duration <= 180:
        return "shorts"
    return "video"


def after_upload(drive, file: dict, cfg: dict) -> None:
    action = cfg["drive"].get("after_upload", "none")
    if action == "trash":
        # В корзину: из папки пропадает, но 30 дней можно восстановить.
        drive.files().update(
            fileId=file["id"], body={"trashed": True},
            fields="id", supportsAllDrives=True,
        ).execute(num_retries=5)
        log.info("Отправил в корзину Диска: %s", file["name"])
    elif action == "delete":
        # Безвозвратно, мимо корзины.
        drive.files().delete(fileId=file["id"], supportsAllDrives=True).execute(num_retries=5)
        log.info("Удалил с Диска навсегда: %s", file["name"])
    elif action == "move":
        target = cfg["drive"].get("processed_folder_id") or ""
        if not target:
            log.info("processed_folder_id не задан — файл оставлен на месте")
            return
        parents = ",".join(file.get("parents", []))
        drive.files().update(
            fileId=file["id"],
            addParents=target,
            removeParents=parents,
            fields="id, parents",
            supportsAllDrives=True,
        ).execute(num_retries=5)
        log.info("Перенёс на Диске в папку обработанных: %s", file["name"])


# --------------------------------------------------------------------------- расписание

def next_slot(state: dict, sched: dict, taken: list[datetime]) -> datetime:
    tz = ZoneInfo(sched.get("timezone", "UTC"))
    times = sorted(sched.get("publish_times") or ["12:00"])

    now = datetime.now(tz)
    cursor = now + timedelta(hours=float(sched.get("min_lead_hours", 3)))

    last_raw = state.get("last_publish_at")
    if last_raw:
        last = _parse_rfc3339(last_raw).astimezone(tz)
        if last + timedelta(minutes=1) > cursor:
            cursor = last + timedelta(minutes=1)
    for busy in taken:
        busy_tz = busy.astimezone(tz)
        if busy_tz + timedelta(minutes=1) > cursor:
            cursor = busy_tz + timedelta(minutes=1)

    day = cursor.date()
    for _ in range(400):
        for stamp in times:
            hour, minute = (int(x) for x in stamp.split(":"))
            candidate = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(
                hour=hour, minute=minute
            )
            if candidate >= cursor:
                return candidate
        day = day + timedelta(days=1)

    raise RuntimeError("Не нашёл свободный слот публикации за 400 дней вперёд")


# --------------------------------------------------------------------------- YouTube

def upload_video(youtube, path: Path, info: dict, publish_at: datetime | None, ytcfg: dict) -> str:
    snippet = {
        "title": info["title"],
        "description": info["description"],
        "tags": info["tags"],
        "categoryId": info["category_id"] if ytcfg.get("auto_category", True)
        else str(ytcfg.get("category_id", "22")),
        "defaultLanguage": info["language"],
        "defaultAudioLanguage": info["language"],
    }
    status = {
        "privacyStatus": ytcfg.get("privacy_status", "private"),
        "selfDeclaredMadeForKids": bool(ytcfg.get("made_for_kids", False)),
    }
    if publish_at is not None:
        status["publishAt"] = publish_at.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    media = MediaFileUpload(str(path), chunksize=16 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(
        part="snippet,status",
        body={"snippet": snippet, "status": status},
        media_body=media,
    )

    response = None
    attempt = 0
    last_pct = -10
    log.info("Заливаю на YouTube: %s", info["title"])
    while response is None:
        try:
            progress, response = request.next_chunk()
            if progress:
                pct = int(progress.progress() * 100)
                if pct - last_pct >= 10:
                    log.info("  залито %d%%", pct)
                    last_pct = pct
        except HttpError as exc:
            if exc.resp.status in RETRIABLE_STATUS and attempt < MAX_UPLOAD_RETRIES:
                attempt += 1
                delay = min(2 ** attempt, 64) + random.random()
                log.warning("YouTube вернул %s — повтор через %.0f с (попытка %d)",
                            exc.resp.status, delay, attempt)
                time.sleep(delay)
                continue
            raise
        except (ConnectionError, TimeoutError, OSError) as exc:
            if attempt < MAX_UPLOAD_RETRIES:
                attempt += 1
                delay = min(2 ** attempt, 64) + random.random()
                log.warning("Сеть отвалилась (%s) — повтор через %.0f с", exc, delay)
                time.sleep(delay)
                continue
            raise

    return response["id"]


def add_to_playlist(youtube, video_id: str, playlist_id: str) -> None:
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }
        },
    ).execute(num_retries=5)
    log.info("Добавил в плейлист %s", playlist_id)


# --------------------------------------------------------------------------- команды

def _human(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def cmd_folders(args) -> int:
    creds = gauth.get_credentials()
    drive = gauth.drive_service(creds)
    query = "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if args.query:
        query += f" and name contains '{args.query}'"
    resp = drive.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=100,
        orderBy="name",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute(num_retries=5)
    items = resp.get("files", [])
    if not items:
        print("Папки не найдены.")
        return 0
    for item in items:
        print(f"{item['id']}  {item['name']}")
    return 0


def cmd_list(args) -> int:
    cfg = load_config()
    state = load_state()
    creds = gauth.get_credentials()
    drive = gauth.drive_service(creds)

    files = list_videos(drive, cfg)
    if not files:
        print("В папке нет видеофайлов.")
        return 0

    for file in files:
        done = state["uploaded"].get(file["id"])
        mark = "загружено" if done else "новое"
        extra = f" -> youtu.be/{done['video_id']}" if done else ""
        print(f"[{mark:>9}] {file['name']}  ({_human(int(file.get('size') or 0))}){extra}")
    return 0


def cmd_plan(args) -> int:
    cfg = load_config()
    state = load_state()
    tz = ZoneInfo(cfg["schedule"].get("timezone", "UTC"))
    now = datetime.now(tz)

    # Уже на YouTube, ждут своего часа.
    waiting = []
    for rec in state["uploaded"].values():
        stamp = rec.get("publish_at")
        if not stamp:
            continue
        try:
            when = _parse_rfc3339(stamp).astimezone(tz)
        except ValueError:
            continue
        if when > now:
            waiting.append((when, rec))
    waiting.sort(key=lambda x: x[0])

    if waiting:
        print("Загружено, ждёт публикации:")
        for when, rec in waiting:
            left = when - now
            hours = int(left.total_seconds() // 3600)
            mins = int(left.total_seconds() % 3600 // 60)
            print(f"  {when:%d.%m %H:%M} ({hours} ч {mins} мин)  {rec.get('title', '?')}")
            print(f"      https://youtu.be/{rec.get('video_id', '?')}")
    else:
        print("Ничего не ждёт публикации.")

    # Что лежит в папке и ещё не залито.
    print()
    creds = gauth.get_credentials()
    drive = gauth.drive_service(creds)
    pending = [f for f in list_videos(drive, cfg) if f["id"] not in state["uploaded"]]
    if not pending:
        print("В папке новых видео нет.")
        return 0

    print("В папке, ещё не загружено:")
    taken = [w for w, _ in waiting]
    for file in pending[: int(cfg["schedule"].get("max_per_run", 3))]:
        slot = next_slot(state, cfg["schedule"], taken)
        taken.append(slot)
        print(f"  {slot:%d.%m %H:%M}  {file['name']}")
    if len(pending) > int(cfg["schedule"].get("max_per_run", 3)):
        print(f"  ...и ещё {len(pending) - int(cfg['schedule'].get('max_per_run', 3))} "
              f"в очереди на следующие запуски")
    return 0


def cmd_models(args) -> int:
    """Что за модели лежат на сервере и какие из них годятся для кадров."""
    cfg = load_config()
    mcfg = cfg["metadata"]
    print("сервер:", meta.local_url(mcfg))
    print()
    try:
        rows = meta.local_models_full(mcfg)
    except Exception as exc:
        log.error("Сервер моделей не ответил: %s", exc)
        return 2

    used = {
        mcfg.get("vision_model"): "работает сейчас (по кадрам)",
        mcfg.get("local_model"): "запасная (по расшифровке)",
    }
    print("%-46s %-9s %-9s %s" % ("МОДЕЛЬ", "КВАНТ", "КОНТЕКСТ", "СОСТОЯНИЕ"))
    print("-" * 92)
    for row in rows:
        name = row.get("id") or row.get("name") or "?"
        note = used.get(name, "")
        if row.get("loaded"):
            note = (note + ", в памяти").lstrip(", ")
        print("%-46s %-9s %-9s %s" % (
            name[:46],
            row.get("quant") or "-",
            row.get("context_length") or "-",
            note,
        ))
    print()
    print("Сменить модель для кадров:")
    print("  uploader.py setmodel ИМЯ_МОДЕЛИ")
    print()
    print("Модель должна уметь смотреть картинки. Текстовая примет запрос,")
    print("но кадры проигнорирует и напишет заголовок наугад.")
    return 0


def _find_folder(drive, query: str):
    """По ID или по части имени. Возвращает (id, name) или список кандидатов."""
    # Похоже на идентификатор: длинная строка без пробелов.
    if len(query) > 20 and " " not in query:
        try:
            f = drive.files().get(fileId=query, fields="id,name,mimeType",
                                  supportsAllDrives=True).execute(num_retries=5)
            if f.get("mimeType") == "application/vnd.google-apps.folder":
                return (f["id"], f["name"]), []
        except HttpError:
            pass

    safe = query.replace("'", "\'")
    resp = drive.files().list(
        q="mimeType = 'application/vnd.google-apps.folder' and trashed = false "
          "and name contains '%s'" % safe,
        fields="files(id, name)", pageSize=50, orderBy="name",
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute(num_retries=5)
    found = resp.get("files", [])
    if len(found) == 1:
        return (found[0]["id"], found[0]["name"]), []
    return None, found


def cmd_setmode(args) -> int:
    """Переключает режим публикации."""
    cfg = load_config()
    cfg["youtube"]["mode"] = args.mode
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    what = {
        "shorts": "все ролики уходят как Shorts — горизонтальные кадрируются "
                  "в вертикаль",
        "video": "все ролики уходят обычным видео — кадр не трогается",
        "auto": "решает исходник: вертикальный до 3 минут уйдёт в Shorts, "
                "остальное обычным видео",
    }[args.mode]
    log.info("Режим: %s", args.mode)
    print(what)
    print()
    print("Подпись в описании: %s" % (
        cfg["youtube"].get("footer_shorts") if args.mode == "shorts"
        else cfg["youtube"].get("footer_video") if args.mode == "video"
        else "своя для каждого режима"))
    return 0


def cmd_setfolder(args) -> int:
    """Меняет папку, из которой берутся видео (или куда уезжают залитые)."""
    cfg = load_config()
    field = "processed_folder_id" if args.processed else "folder_id"
    role = "куда уезжают залитые" if args.processed else "откуда берутся видео"

    drive = gauth.drive_service(gauth.get_credentials())
    hit, others = _find_folder(drive, args.folder)

    if hit is None:
        if not others:
            log.error("Папка не найдена: %s", args.folder)
        else:
            log.error("Под описание подходит несколько папок — уточните или "
                      "укажите ID:")
            for f in others:
                print("   %s  %s" % (f["id"], f["name"]))
        return 1

    folder_id, name = hit
    if not args.processed and cfg["drive"].get("processed_folder_id") == folder_id:
        log.error("Это папка для залитых роликов — брать из неё нельзя, "
                  "получится круг")
        return 1

    cfg["drive"][field] = folder_id
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Папка «%s» теперь %s", name, role)

    if not args.processed:
        files = list_videos(drive, cfg)
        state = load_state()
        fresh = [f for f in files if f["id"] not in state["uploaded"]]
        print()
        print("В папке видео: %d, из них новых: %d" % (len(files), len(fresh)))
        for f in fresh[:5]:
            print("   " + f["name"])
        if len(fresh) > 5:
            print("   ...и ещё %d" % (len(fresh) - 5))
        if fresh:
            print()
            print("Посмотреть, что получится:  uploader.py run --dry-run")
    return 0


def cmd_setmodel(args) -> int:
    """Меняет модель в config.json, предварительно проверив её на сервере."""
    cfg = load_config()
    mcfg = cfg["metadata"]
    field = "local_model" if args.text else "vision_model"
    role = "текстовую (по расшифровке)" if args.text else "для кадров"

    try:
        names = meta.local_models(mcfg)
    except Exception as exc:
        log.error("Сервер моделей не ответил: %s", exc)
        return 2

    if not meta.has_model(names, args.model):
        log.error("Такой модели на сервере нет: %s", args.model)
        print()
        print("Что есть:")
        for name in names:
            print("   " + name)
        return 1

    exact = next((n for n in names if n == args.model), None) or next(
        (n for n in names
         if n.split("/")[-1].split(":")[0].lower()
         == args.model.split("/")[-1].split(":")[0].lower()), args.model)

    if not args.text:
        vision = meta.check_vision(mcfg, exact)
        if vision is False:
            log.error("Модель %s не умеет смотреть картинки — она не подойдёт", exact)
            print("Если всё-таки хотите её поставить, добавьте --force.")
            if not args.force:
                return 1
        elif vision is None:
            log.warning("Не удалось проверить, умеет ли %s смотреть картинки", exact)

    mcfg[field] = exact
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Модель %s теперь %s", exact, role)
    print()
    print("Проверьте на реальном ролике:")
    print("   uploader.py run --dry-run")
    return 0


def cmd_unloadmodel(args) -> int:
    cfg = load_config()
    mcfg = cfg["metadata"]
    print("сервер:", meta.local_url(mcfg))
    if meta.unload_model(mcfg):
        return 0
    print()
    print("Не вышло. Скорее всего нужен токен доступа: создайте его в Unsloth")
    print("(Настройки -> API -> Токены доступа) и впишите в config.json")
    print("в поле metadata.local_api_key.")
    return 1


def cmd_loadmodel(args) -> int:
    """Просит сервер поднять модель в память — проверка, что скрипт это умеет."""
    cfg = load_config()
    mcfg = cfg["metadata"]
    model = args.model or (
        mcfg.get("vision_model") if mcfg.get("vision") else mcfg.get("local_model")
    )
    print(f"сервер: {meta.local_url(mcfg)}")
    print(f"модель: {model}")

    if meta.load_model(mcfg, model):
        print()
        print("Получилось. Скрипт может поднимать модель сам — автовыгрузка в Unsloth")
        print("больше не помешает ночной работе.")
        return 0

    print()
    print("Не вышло. Скрипт зависит от того, что модель уже в памяти.")
    print("Поставьте в Unsloth автовыгрузку при простое в 0, либо создайте токен")
    print("и впишите его в config.json -> metadata.local_api_key.")
    return 1


def cmd_check(args) -> int:
    """Проверяет всё, от чего зависит ночной запуск."""
    cfg = load_config()
    mcfg = cfg["metadata"]
    ok = True

    provider = (mcfg.get("provider") or "auto").lower()
    print(f"движок метаданных: {provider}")

    if provider in ("local", "local_first", "auto"):
        url = meta.local_url(mcfg)
        want = mcfg.get("local_model", "qwen2.5:7b")
        if "IP_" in url:
            ok = False
            print(f"локальная модель: адрес не задан ({url})")
            print("   впишите в config.json -> metadata.local_url адрес компьютера с моделью,")
            print("   например http://192.168.1.50:11434")
        else:
            wanted = [(want, "текстовая")]
            if mcfg.get("vision"):
                wanted.append((mcfg.get("vision_model", ""), "vision"))

            try:
                models = meta.local_models(mcfg)
                print(f"локальная модель на {url}: сервер отвечает")
                for name, kind in wanted:
                    if not name or "УКАЖИТЕ" in name:
                        ok = False
                        print(f"   {kind}: имя модели не задано в config.json")
                    elif meta.has_model(models, name):
                        print(f"   {kind}: {name} — на месте")
                    else:
                        ok = False
                        print(f"   {kind}: {name} — НЕ НАЙДЕНА")
                        print(f"      есть: {', '.join(models) or '(пусто)'}")
            except Exception as exc:
                ok = False
                print(f"локальная модель на {url}: НЕДОСТУПНА ({exc})")
                if (mcfg.get("local_api") or "ollama").lower() == "openai":
                    print("   Unsloth: включите API settings -> Keyless API access -> Chat and inference")
                    print("   (или создайте токен и впишите его в metadata.local_api_key)")
                else:
                    print("   Ollama: задайте на том компьютере переменную OLLAMA_HOST=0.0.0.0")
                print("   плюс: компьютер включён, модель загружена, брандмауэр пропускает порт")

    if provider in ("claude", "auto", "local_first"):
        import os
        print("ключ ANTHROPIC_API_KEY:", "задан" if os.environ.get("ANTHROPIC_API_KEY") else "нет")

    print("ffmpeg:", "найден" if shutil.which("ffmpeg") else "НЕ НАЙДЕН")

    try:
        creds = gauth.get_credentials()
        drive = gauth.drive_service(creds)
        folder = drive.files().get(
            fileId=cfg["drive"]["folder_id"], fields="name", supportsAllDrives=True
        ).execute(num_retries=5)
        print(f"папка на Диске: {folder['name']}")
        yt = gauth.youtube_service(creds).channels().list(
            part="snippet", mine=True
        ).execute(num_retries=5)
        items = yt.get("items", [])
        print("канал YouTube:", items[0]["snippet"]["title"] if items else "НЕ НАЙДЕН")
    except Exception as exc:
        ok = False
        print(f"Google: ОШИБКА ({str(exc)[:200]})")

    print()
    print("итог:", "всё готово к работе" if ok else "есть проблемы, смотрите выше")
    return 0 if ok else 1


def cmd_run(args) -> int:
    cfg = load_config()
    state = load_state()
    ytcfg = cfg["youtube"]
    sched = cfg["schedule"]

    if ytcfg.get("privacy_status") != "private" and not args.no_schedule:
        log.warning(
            "privacy_status = %s: YouTube игнорирует отложенную публикацию для непубличных "
            "статусов кроме private. Видео выйдет сразу.", ytcfg.get("privacy_status")
        )

    creds = gauth.get_credentials()
    drive = gauth.drive_service(creds)
    youtube = gauth.youtube_service(creds)

    files = list_videos(drive, cfg)
    pending = [f for f in files if f["id"] not in state["uploaded"]]
    if not pending:
        log.info("Новых видео нет.")
        return 0

    limit = args.limit if args.limit is not None else int(sched.get("max_per_run", 3))

    # Квота YouTube — 10 000 единиц в сутки, одна загрузка стоит 1600.
    # При частых запусках без этого предела можно исчерпать её за пару проходов.
    max_day = int(sched.get("max_per_day", 5))
    if max_day > 0 and not args.dry_run:
        today = datetime.now(ZoneInfo("UTC")).date()
        done_today = 0
        for rec in state["uploaded"].values():
            stamp = rec.get("uploaded_at")
            if not stamp:
                continue
            try:
                if _parse_rfc3339(stamp).date() == today:
                    done_today += 1
            except ValueError:
                continue
        left = max_day - done_today
        if left <= 0:
            log.info("Сегодня уже загружено %d из %d — жду завтрашних суток",
                     done_today, max_day)
            return 0
        if left < limit:
            log.info("Сегодня осталось %d загрузок из дневных %d", left, max_day)
            limit = left

    pending = pending[:limit]
    log.info("К загрузке: %d видео", len(pending))

    work_dir = Path(tempfile.mkdtemp(prefix="ytupload_"))
    taken: list[datetime] = []
    failures = 0
    deferred = 0

    try:
        for file in pending:
            local: Path | None = None
            try:
                local = download(drive, file, work_dir)

                duration_ms = (file.get("videoMediaMetadata") or {}).get("durationMillis")
                duration = int(duration_ms) / 1000 if duration_ms else None

                transcript = meta.transcribe(local, cfg["metadata"])
                if transcript:
                    log.info("Расшифровка: %d символов", len(transcript))

                mode = resolve_mode(cfg, local)
                log.info("Режим публикации: %s",
                         "Shorts" if mode == "shorts" else "обычное видео")

                mcfg = dict(cfg["metadata"])
                mcfg["mode"] = mode
                mcfg["description_footer"] = (
                    ytcfg.get("footer_shorts") if mode == "shorts"
                    else ytcfg.get("footer_video")
                ) or ytcfg.get("description_footer", "")
                mcfg["fallback_category_id"] = str(ytcfg.get("category_id", "22"))
                info = meta.generate(file["name"], transcript, mcfg, duration, local)

                if not info["generated"] and mcfg.get("require_generated", True):
                    log.warning(
                        "Метаданные не сгенерировались (модель недоступна) — "
                        "откладываю %s до следующего запуска", file["name"]
                    )
                    continue

                slot = None if args.no_schedule else next_slot(state, sched, taken)

                log.info("Метаданные от: %s", info.get("provider", "?"))
                log.info("Заголовок: %s", info["title"])
                log.info("Теги: %s", ", ".join(info["tags"]) or "(нет)")
                if slot:
                    log.info("Публикация: %s", slot.strftime("%Y-%m-%d %H:%M %Z"))

                if args.dry_run:
                    print("\n--- ЧЕРНОВИК (ничего не загружено) ---")
                    print(f"Файл:      {file['name']}")
                    print(f"Режим:     {'Shorts' if mode == 'shorts' else 'обычное видео'}")
                    print(f"Текст от:  {info.get('provider', '?')}")
                    print(f"Заголовок: {info['title']}")
                    print(f"Категория: {info['category_id']}")
                    print(f"Публикация:{slot.strftime(' %Y-%m-%d %H:%M %Z') if slot else ' сразу'}")
                    print(f"Теги:      {', '.join(info['tags'])}")
                    print("Описание:")
                    print(info["description"])
                    print("--- конец черновика ---\n")
                    if slot:
                        taken.append(slot)
                    continue

                # Shorts определяются по вертикальному кадру, а не по хештегу.
                upload_path = local
                vertical: Path | None = None
                # Кадрируем только под Shorts: обычному видео вертикаль не нужна.
                if mode == "shorts" and ytcfg.get("force_vertical", True):
                    vertical = meta.make_vertical(
                        local, ytcfg.get("vertical_mode", "blur")
                    )
                    if vertical is not None:
                        upload_path = vertical

                video_id = upload_video(youtube, upload_path, info, slot, ytcfg)

                # Обложка для панели: у приватных роликов публичных превью нет,
                # поэтому кадр сохраняем сами, пока файл ещё под рукой.
                meta.save_poster(local, BASE / "thumbs" / (video_id + ".png"))
                if vertical is not None:
                    vertical.unlink(missing_ok=True)
                log.info("Готово: https://youtu.be/%s", video_id)

                if ytcfg.get("playlist_id"):
                    try:
                        add_to_playlist(youtube, video_id, ytcfg["playlist_id"])
                    except HttpError:
                        log.exception("Не смог добавить в плейлист — видео всё равно залито")

                state["uploaded"][file["id"]] = {
                    "video_id": video_id,
                    "name": file["name"],
                    "title": info["title"],
                    "publish_at": slot.isoformat() if slot else None,
                    "uploaded_at": datetime.now(ZoneInfo("UTC")).isoformat(),
                    "metadata_generated": info["generated"],
                    "metadata_provider": info.get("provider"),
                    # Чтобы панель могла показать тексты, не дёргая YouTube.
                    "description": info["description"],
                    "tags": info["tags"],
                }
                if slot:
                    state["last_publish_at"] = slot.isoformat()
                    taken.append(slot)
                save_state(state)

                after_upload(drive, file, cfg)

            except meta.MetadataUnavailable as exc:
                deferred += 1
                log.warning("Откладываю %s до следующего запуска: %s", file["name"], exc)
            except Exception:
                failures += 1
                log.exception("Не смог обработать %s — иду к следующему", file["name"])
            finally:
                if local is not None:
                    local.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        # Видеопамять нужна не только нам — освобождаем, если просили.
        if cfg["metadata"].get("local_unload_after") and not args.dry_run:
            meta.unload_model(cfg["metadata"])

    if deferred:
        log.warning("Отложено видео: %d — движок метаданных был недоступен", deferred)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Drive -> Claude -> YouTube")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="загрузить новые видео")
    run.add_argument("--limit", type=int, default=None, help="сколько видео за запуск")
    run.add_argument("--dry-run", action="store_true",
                     help="показать метаданные, ничего не загружать")
    run.add_argument("--no-schedule", action="store_true",
                     help="без отложенной публикации")
    run.set_defaults(func=cmd_run)

    folders = sub.add_parser("folders", help="найти ID папки на Диске")
    folders.add_argument("query", nargs="?", default=None, help="часть имени папки")
    folders.set_defaults(func=cmd_folders)

    lst = sub.add_parser("list", help="показать содержимое папки")
    lst.set_defaults(func=cmd_list)

    plan = sub.add_parser("plan", help="показать расписание ближайших публикаций")
    plan.set_defaults(func=cmd_plan)


    check = sub.add_parser("check", help="проверить всё, от чего зависит ночной запуск")
    check.set_defaults(func=cmd_check)

    mdl = sub.add_parser("models", help="какие модели есть на сервере")
    mdl.set_defaults(func=cmd_models)

    smode = sub.add_parser("setmode", help="Shorts или обычное видео")
    smode.add_argument("mode", choices=["shorts", "video", "auto"])
    smode.set_defaults(func=cmd_setmode)

    sf = sub.add_parser("setfolder", help="сменить папку на Диске")
    sf.add_argument("folder", help="ID папки или часть её имени")
    sf.add_argument("--processed", action="store_true",
                    help="менять папку для залитых, а не для новых")
    sf.set_defaults(func=cmd_setfolder)

    sm = sub.add_parser("setmodel", help="сменить модель, которая пишет метаданные")
    sm.add_argument("model", help="имя модели, как в списке models")
    sm.add_argument("--text", action="store_true",
                    help="менять текстовую модель, а не ту, что смотрит кадры")
    sm.add_argument("--force", action="store_true",
                    help="поставить, даже если модель не умеет в картинки")
    sm.set_defaults(func=cmd_setmodel)

    um = sub.add_parser("unloadmodel", help="выгрузить модель, освободить видеопамять")
    um.set_defaults(func=cmd_unloadmodel)

    lm = sub.add_parser("loadmodel", help="поднять модель в память через сервер")
    lm.add_argument("model", nargs="?", default=None, help="имя модели, по умолчанию из конфига")
    lm.set_defaults(func=cmd_loadmodel)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 0

    setup_logging(args.verbose)

    try:
        if args.func is cmd_run:
            with SingleRun():
                return args.func(args)
        return args.func(args)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        log.warning("Прервано пользователем")
        return 130


if __name__ == "__main__":
    sys.exit(main())
