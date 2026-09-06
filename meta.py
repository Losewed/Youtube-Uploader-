"""Транскрипция видео (ffmpeg + faster-whisper) и генерация метаданных.

Метаданные умеет писать либо Claude через Anthropic API, либо локальная модель
(Ollama или любой OpenAI-совместимый сервер) — в том числе на другом компьютере
в локальной сети. Порядок задаётся в config.json -> metadata.provider.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("meta")

# YouTube-лимиты
TITLE_LIMIT = 100
DESC_LIMIT = 5000
TAGS_TOTAL_LIMIT = 500

CATEGORIES = {
    "Люди и блоги": "22",
    "Развлечения": "24",
    "Наука и техника": "28",
    "Образование": "27",
    "Игры": "20",
    "Музыка": "10",
    "Спорт": "17",
    "Путешествия и события": "19",
    "Хобби и стиль": "26",
    "Авто и транспорт": "2",
    "Новости и политика": "25",
    "Комедии": "23",
    "Животные": "15",
    "Некоммерческие проекты": "29",
}

_model_cache: dict[str, object] = {}


# --------------------------------------------------------------------------- аудио

def extract_audio(video: Path, max_minutes: int) -> Path | None:
    """Достаёт из видео моно-WAV 16 кГц (то, что ест Whisper). None — если ffmpeg не смог."""
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg не найден в PATH — транскрипция пропущена")
        return None

    fd, name = tempfile.mkstemp(suffix=".wav", prefix="ytmeta_")
    import os as _os
    _os.close(fd)
    out = Path(name)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    if max_minutes and max_minutes > 0:
        cmd += ["-t", str(int(max_minutes * 60))]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]

    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 1024:
        log.warning("ffmpeg не извлёк аудио: %s", (proc.stderr or "").strip()[:400])
        out.unlink(missing_ok=True)
        return None
    return out


def video_duration(video: Path) -> float | None:
    """Длительность в секундах через ffprobe."""
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
        capture_output=True, text=True, errors="replace",
    )
    try:
        return float(proc.stdout.strip())
    except (TypeError, ValueError):
        return None


def frame_token_cost(video: Path, width: int) -> int:
    """Во сколько примерно токенов обойдётся один кадр.

    Модели семейства Qwen-VL режут картинку на участки 28x28 после
    внутреннего объединения патчей, поэтому цена кадра — это число таких
    участков. Оценка грубая, но её хватает, чтобы не переполнить контекст.
    """
    size = video_size(video)
    if size is None:
        return 400
    src_w, src_h = size
    height = max(1, round(width * src_h / src_w))
    return max(1, (width // 28) * (height // 28))


def extract_frames(video: Path, count: int, width: int = 768,
                   token_budget: int = 0) -> list[bytes]:
    """Равномерно берёт кадры по всему ролику. Возвращает JPEG-байты.

    token_budget — сколько токенов контекста можно потратить на картинки.
    Если запрошено больше кадров, чем влезает, число уменьшается.
    """
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg не найден — кадры не извлечь")
        return []

    duration = video_duration(video)
    if not duration or duration <= 0:
        log.warning("Не определил длительность — беру один кадр с начала")
        duration, count = 1.0, 1

    if token_budget > 0:
        per_frame = frame_token_cost(video, width)
        fits = max(1, token_budget // per_frame)
        if fits < count:
            log.warning(
                "Кадр стоит ~%d токенов, %d кадров не влезут в бюджет %d — беру %d",
                per_frame, count, token_budget, fits,
            )
            count = fits
        else:
            log.info("Кадров: %d, примерно %d токенов контекста",
                     count, count * per_frame)

    frames: list[bytes] = []
    tmpdir = Path(tempfile.mkdtemp(prefix="ytframes_"))
    try:
        for i in range(count):
            # середины равных отрезков: не первый чёрный кадр и не последний
            ts = duration * (i + 0.5) / count
            out = tmpdir / "frame{}.jpg".format(i)
            proc = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-ss", "{:.3f}".format(ts),
                 "-i", str(video), "-frames:v", "1",
                 "-vf", "scale={}:-2".format(width), "-q:v", "3", str(out)],
                capture_output=True, text=True, errors="replace",
            )
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                frames.append(out.read_bytes())
            else:
                log.warning("Кадр на %.1f с не извлёкся: %s", ts,
                            (proc.stderr or "").strip()[:200])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    log.info("Кадров из видео: %d", len(frames))
    return frames


def save_poster(video: Path, dest: Path, width: int = 320) -> bool:
    """Кладёт кадр из середины ролика в PNG — обложка для панели.

    PNG, а не JPEG, потому что Tk умеет показывать только его.
    """
    if not shutil.which("ffmpeg"):
        return False
    duration = video_duration(video) or 1.0
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", "{:.3f}".format(duration / 2),
         "-i", str(video), "-frames:v", "1",
         "-vf", "scale={}:-2:flags=lanczos".format(width), str(dest)],
        capture_output=True, text=True, errors="replace",
    )
    ok = proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0
    if not ok:
        log.info("Обложку сохранить не вышло: %s", (proc.stderr or "").strip()[:200])
    return ok


def video_size(video: Path) -> tuple[int, int] | None:
    """(ширина, высота) первого видеопотока."""
    if not shutil.which("ffprobe"):
        return None
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0:s=x", str(video)],
        capture_output=True, text=True, errors="replace",
    )
    try:
        w, h = proc.stdout.strip().split("\n")[0].split("x")
        return int(w), int(h)
    except (ValueError, IndexError):
        return None


def make_vertical(video: Path, mode: str = "blur") -> Path | None:
    """Делает 1080x1920 из горизонтального ролика.

    blur — вписать по ширине, поля сверху и снизу заполнить размытым кадром;
    crop — вырезать вертикальную полосу по центру.
    Возвращает путь к новому файлу или None, если ничего делать не нужно.
    """
    if not shutil.which("ffmpeg"):
        log.warning("ffmpeg не найден — вертикальную версию не собрать")
        return None

    size = video_size(video)
    if size is None:
        log.warning("Не определил размер кадра — оставляю файл как есть")
        return None

    width, height = size
    if height >= width:
        log.info("Кадр %dx%d уже вертикальный — переделывать нечего", width, height)
        return None

    if mode == "crop":
        # Увеличить до заполнения кадра, лишнее по бокам отрезать. Пропорции сохранены.
        vf = ("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
              "crop=1080:1920")
    elif mode == "stretch":
        # Растянуть по вертикали без сохранения пропорций.
        vf = "scale=1080:1920:flags=lanczos,setsar=1"
    else:
        vf = (
            "split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=24[bgb];"
            "[fg]scale=1080:-2:flags=lanczos[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )

    out = video.with_name(video.stem + "_vertical.mp4")
    log.info("Собираю вертикальную версию %dx%d -> 1080x1920 (режим %s)", width, height, mode)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vf" if mode in ("crop", "stretch") else "-filter_complex", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(out)],
        capture_output=True, text=True, errors="replace",
    )
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        log.warning("ffmpeg не собрал вертикальную версию: %s",
                    (proc.stderr or "").strip()[:400])
        out.unlink(missing_ok=True)
        return None

    log.info("Вертикальная версия готова: %.1f МБ", out.stat().st_size / 1024 / 1024)
    return out


def transcribe(video: Path, cfg: dict) -> str:
    """Возвращает расшифровку с таймкодами или пустую строку, если распознать не удалось."""
    if not cfg.get("transcribe", True):
        log.info("Распознавание речи выключено в настройках — пропускаю")
        return ""

    audio = extract_audio(video, cfg.get("transcribe_max_minutes", 20))
    if audio is None:
        return ""

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper не установлен — метаданные будут по имени файла")
        audio.unlink(missing_ok=True)
        return ""

    size = cfg.get("whisper_model", "small")
    try:
        model = _model_cache.get(size)
        if model is None:
            log.info("Загружаю модель Whisper %s (первый запуск скачивает её)", size)
            model = WhisperModel(size, device="cpu", compute_type="int8")
            _model_cache[size] = model

        segments, info = model.transcribe(
            str(audio),
            language=cfg.get("whisper_language") or None,
            vad_filter=True,
            beam_size=5,
        )
        log.info("Распознаю речь (язык: %s)", getattr(info, "language", "?"))

        lines = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                mm, ss = int(seg.start) // 60, int(seg.start) % 60
                lines.append("[{:02d}:{:02d}] {}".format(mm, ss, text))
        return "\n".join(lines)
    except Exception:
        log.exception("Ошибка транскрипции — продолжаю без неё")
        return ""
    finally:
        audio.unlink(missing_ok=True)


# --------------------------------------------------------------------------- общий промпт

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Заголовок ролика для YouTube"},
        "description": {"type": "string", "description": "Описание ролика"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Поисковые теги без решёток",
        },
        "category": {"type": "string", "enum": list(CATEGORIES.keys())},
        "language": {"type": "string", "description": "Код языка ролика, например ru или en"},
    },
    "required": ["title", "description", "tags", "category", "language"],
    "additionalProperties": False,
}

SYSTEM = """Ты — редактор YouTube-канала. По расшифровке ролика ты пишешь заголовок, описание и теги.

Правила:
- Заголовок: конкретный, по сути ролика, без кликбейта и без CAPS. Не выдумывай фактов, которых нет в расшифровке.
- Описание: 2-4 абзаца. Первые 150 символов - самое важное, их видно в поиске. Если в расшифровке есть чёткие смысловые блоки и ролик длиннее 3 минут, добавь тайм-коды в конце отдельными строками формата "0:00 Название блока" (первый обязательно 0:00). Тайм-коды бери только из реальных меток расшифровки.
- Теги: конкретные поисковые запросы по теме ролика, в нижнем регистре, без решёток. Общие слова вроде "видео" не нужны.
- Пиши на языке, указанном в задании. Никаких плейсхолдеров вроде [ссылка] и никаких эмодзи в заголовке."""

# Локальные модели слабее и без явного напоминания про формат уезжают в свободный текст.
SYSTEM_LOCAL_SUFFIX = """

Формат ответа: один JSON-объект и ничего кроме него.
Поля: title (строка), description (строка), tags (массив строк), category (строка), language (строка).
Поле category выбери ровно одно из списка: {categories}"""


def _build_prompt(video_name: str, transcript: str, cfg: dict,
                  duration_sec: float | None, n_frames: int = 0) -> str:
    out_lang = cfg.get("output_language", "ru")
    n_tags = int(cfg.get("tags_count", 15))
    t_max = int(cfg.get("title_max_chars", 90))

    parts = ["Имя файла: " + video_name]
    if duration_sec:
        parts.append("Длительность: {} мин {} с".format(
            int(duration_sec // 60), int(duration_sec % 60)))
        if cfg.get("mode") == "shorts":
            parts.append(
                "Это короткий вертикальный ролик (Shorts). Описание — 1-2 коротких "
                "предложения. Тайм-коды не добавляй."
            )
        else:
            parts.append(
                "Это обычное видео для YouTube, не Shorts. Описание — 2-4 "
                "предложения, можно чуть подробнее. Тайм-коды не добавляй: "
                "у нас нет расшифровки, чтобы их брать."
            )
    if (cfg.get("channel_context") or "").strip():
        parts.append("О канале: " + cfg["channel_context"].strip())
    parts.append("")
    parts.append(
        "Язык вывода: {}. Заголовок не длиннее {} символов. Тегов примерно {}.".format(
            out_lang, t_max, n_tags)
    )
    parts.append("")
    if transcript:
        parts.append("Расшифровка ролика:")
        parts.append(transcript)
    elif n_frames:
        parts.append(
            "Речи в ролике нет. Ниже {} кадров из него, снятых равномерно по всей "
            "длительности. Опиши то, что реально видно на кадрах: что за постройка, "
            "какого примерно размера, что внутри и что вокруг. "
            "Про материал пиши обобщённо — кирпич, камень, дерево, стекло, шерсть. "
            "Не называй конкретную разновидность блока, если не уверен на все сто: "
            "перепутанный вид блока в заголовке аудитория замечает сразу, а "
            "обобщённое слово выглядит нормально. "
            "Не выдумывай деталей, которых на кадрах не видно. "
            "Тайм-кодов в описании быть не должно — взять их неоткуда.".format(n_frames)
        )
    else:
        parts.append(
            "Речи в ролике нет и кадры недоступны — опирайся на имя файла и описание канала."
        )
    return "\n".join(parts)


def _parse_json(text: str) -> dict:
    """Достаёт JSON из ответа модели, даже если она обернула его в болтовню или ```json."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("в ответе модели нет JSON")
    return json.loads(match.group(0))


# --------------------------------------------------------------------------- Claude

def _is_param_problem(exc, names) -> bool:
    """400 бывает и про параметры, и про пустой баланс — деградировать надо только в первом случае."""
    msg = str(exc).lower()
    if any(word in msg for word in ("credit balance", "billing", "quota", "rate limit")):
        return False
    return any(name in msg for name in names)


def _claude_create(client, **kwargs):
    """Запрос к Claude с постепенной деградацией, если SDK или модель не знают новых
    параметров: beta-путь с серверным фолбэком -> обычный вызов -> вызов без
    output_config/thinking."""
    try:
        return client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **kwargs,
        )
    except (TypeError, AttributeError) as exc:
        log.info("SDK не знает серверных фолбэков (%s) — обычный вызов", exc)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 400 or not _is_param_problem(
            exc, ("betas", "fallback", "beta")
        ):
            raise
        log.info("API отклонил beta-параметры (%s) — обычный вызов", exc)

    try:
        return client.messages.create(**kwargs)
    except (TypeError, AttributeError) as exc:
        log.warning("SDK не принял output_config/thinking (%s) — упрощаю запрос", exc)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 400 or not _is_param_problem(
            exc, ("output_config", "thinking", "schema", "json_schema")
        ):
            raise
        log.warning("API отклонил output_config/thinking (%s) — упрощаю запрос", exc)

    plain = {k: v for k, v in kwargs.items() if k not in ("output_config", "thinking")}
    plain["system"] = plain.get("system", "") + (
        "\n\nОтветь одним JSON-объектом с полями title, description, tags, category, "
        "language. Никакого текста вокруг JSON."
    )
    return client.messages.create(**plain)


def _ask_claude(prompt: str, cfg: dict) -> dict:
    import anthropic  # ImportError ловится вызывающей стороной

    client = anthropic.Anthropic()
    resp = _claude_create(
        client,
        model=cfg.get("model", "claude-opus-5"),
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )

    if getattr(resp, "stop_reason", None) == "refusal":
        category = getattr(getattr(resp, "stop_details", None), "category", "?")
        raise RuntimeError("Claude отклонил запрос ({})".format(category))

    text = next((b.text for b in resp.content if b.type == "text"), "")
    return _parse_json(text)


# --------------------------------------------------------------------------- локальная модель

# Системный прокси (VPN) перехватывает даже запросы к 127.0.0.1 и они виснут
# до таймаута. Для своего сервера моделей ходим напрямую, мимо прокси.
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _auth_headers(cfg: dict) -> dict:
    """Токен нужен, только если в Unsloth не включён keyless-доступ."""
    key = (cfg.get("local_api_key") or "").strip()
    return {"Authorization": "Bearer " + key} if key else {}


def _http_json(url: str, payload: dict, timeout: int, headers: dict | None = None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    head = {"Content-Type": "application/json"}
    head.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=head, method="POST")
    try:
        with _direct.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Сервер обычно объясняет причину в теле ответа — вытащим её в лог.
        try:
            detail = exc.read().decode("utf-8", "replace")
            message = json.loads(detail).get("error", {}).get("message") or detail
        except Exception:
            message = ""
        if message:
            log.warning("Сервер модели ответил %s: %s", exc.code, message[:400])
        exc.detail_message = message or ""
        raise


def unload_model(cfg: dict, model: str | None = None) -> bool:
    """Освобождает видеопамять после работы. True, если сервер согласился."""
    if model is None:
        # Выгружаем ту модель, которой реально пользовались.
        if cfg.get("vision") and cfg.get("vision_model"):
            model = cfg["vision_model"]
        else:
            model = cfg.get("local_model") or ""
    url = local_url(cfg) + "/v1/unload"
    try:
        _http_json(url, {"model_path": model}, 120, _auth_headers(cfg))
        log.info("Модель выгружена, видеопамять свободна")
        return True
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            log.info("Нет прав на выгрузку модели — оставляю как есть")
        else:
            log.info("Выгрузить модель не вышло: %s", exc)
        return False
    except Exception as exc:
        log.info("Выгрузить модель не вышло: %s", exc)
        return False


def _model_unloaded(exc) -> bool:
    """Сервер жалуется именно на то, что модель не в памяти?"""
    detail = getattr(exc, "detail_message", "").lower()
    return "no model loaded" in detail or "not loaded" in detail


def load_model(cfg: dict, model: str) -> bool:
    """Поднимает модель в память. True, если получилось.

    По умолчанию — крошечным запросом на генерацию: сервер сам подтягивает
    нужную модель из кэша (нужен включённый «Переключать модель по запросу»).
    Через /v1/load ходим, только если это явно разрешили в настройках: там имя
    резолвится как репозиторий HuggingFace и модель может скачаться заново.
    """
    if not cfg.get("local_auto_load", True):
        return False

    timeout = int(cfg.get("local_load_timeout", 900))

    if cfg.get("local_load_via_api"):
        url = local_url(cfg) + "/v1/load"
        log.info("Прошу сервер загрузить %s через /v1/load", model)
        try:
            _http_json(url, {"model_path": model}, timeout, _auth_headers(cfg))
            log.info("Модель загружена")
            return True
        except Exception as exc:
            log.warning("Не удалось загрузить модель через API: %s", exc)
            return False

    # Обычный путь: пустяковый запрос, который заставляет сервер поднять модель.
    log.info("Бужу модель %s пробным запросом", model)
    url = local_url(cfg) + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    try:
        _http_json(url, payload, timeout, _auth_headers(cfg))
        log.info("Модель в памяти")
        return True
    except urllib.error.HTTPError as exc:
        detail = getattr(exc, "detail_message", "")
        if _model_unloaded(exc):
            log.warning(
                "Сервер не поднимает модель сам. Включите в Unsloth "
                "Настройки -> API -> «Переключать модель по запросу», "
                "либо загрузите модель вручную в интерфейсе."
            )
        else:
            log.warning("Разбудить модель не вышло: %s", detail or exc)
        return False
    except Exception as exc:
        log.warning("Разбудить модель не вышло: %s", exc)
        return False


def _ask_local(prompt: str, cfg: dict, images: list[bytes] | None = None) -> dict:
    """Ollama (/api/chat) или OpenAI-совместимый сервер (/v1/chat/completions)."""
    base = local_url(cfg)
    api = (cfg.get("local_api") or "ollama").lower()
    model = cfg.get("local_model") or "qwen2.5:7b"
    timeout = int(cfg.get("local_timeout", 600))

    system = SYSTEM + SYSTEM_LOCAL_SUFFIX.format(categories=", ".join(CATEGORIES))

    if images:
        if api != "openai":
            raise RuntimeError("картинки поддерживаются только через OpenAI-совместимый API")
        model = cfg.get("vision_model") or model
        content: list[dict] = [{"type": "text", "text": prompt}]
        for raw in images:
            b64 = base64.b64encode(raw).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64," + b64},
            })
        user_content: object = content
    else:
        user_content = prompt

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    def _run() -> dict:
        return _ask_local_once(base, api, model, messages, cfg)

    try:
        return _run()
    except urllib.error.HTTPError as exc:
        # Модель выгрузилась из памяти — поднимаем и пробуем ещё раз.
        if exc.code != 400 or not _model_unloaded(exc):
            raise
        if not load_model(cfg, model):
            raise
        return _run()


def _ask_local_once(base: str, api: str, model: str, messages: list, cfg: dict) -> dict:
    timeout = int(cfg.get("local_timeout", 600))

    if api == "ollama":
        url = base + "/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "format": SCHEMA,
            "options": {"temperature": 0.4, "num_ctx": 8192},
        }
        try:
            data = _http_json(url, payload, timeout, _auth_headers(cfg))
        except urllib.error.HTTPError as exc:
            if exc.code != 400:
                raise
            # Старые версии Ollama не понимают схему — просят просто JSON.
            log.info("Сервер не принял JSON-схему — прошу обычный JSON")
            payload["format"] = "json"
            data = _http_json(url, payload, timeout, _auth_headers(cfg))
        content = (data.get("message") or {}).get("content", "")
    else:
        url = base + "/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "youtube_meta", "schema": SCHEMA, "strict": True},
            },
        }
        # Не все серверы умеют строгую схему, поэтому спускаемся по ступеням.
        variants = [
            payload["response_format"],
            {"type": "json_object"},
            None,  # только инструкция в системном промпте
        ]
        data = None
        for i, fmt in enumerate(variants):
            if fmt is None:
                payload.pop("response_format", None)
            else:
                payload["response_format"] = fmt
            try:
                data = _http_json(url, payload, timeout, _auth_headers(cfg))
                break
            except urllib.error.HTTPError as exc:
                # 404 — нет такой модели; 400 бывает и про формат, и про то, что
                # модель не загружена. Понижать формат стоит только в первом случае.
                detail = getattr(exc, "detail_message", "").lower()
                about_format = any(
                    word in detail
                    for word in ("response_format", "json_schema", "schema",
                                 "format", "unsupported", "invalid_request")
                )
                if (exc.code not in (400, 415, 422) or not about_format
                        or i == len(variants) - 1):
                    raise
                log.info("Сервер не принял формат ответа (%s) — пробую проще", exc.code)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("сервер вернул пустой ответ")
        content = (choices[0].get("message") or {}).get("content", "")

    if not content.strip():
        raise RuntimeError("модель вернула пустой текст")
    return _parse_json(content)


def local_url(cfg: dict) -> str:
    """Базовый адрес без хвоста. Пользователь мог скопировать его вместе с /v1."""
    url = (cfg.get("local_url") or "http://localhost:11434").strip().rstrip("/")
    for tail in ("/v1", "/api"):
        if url.endswith(tail):
            url = url[: -len(tail)]
    return url


def local_models(cfg: dict) -> list[str]:
    """Список моделей на сервере. Бросает исключение, если сервер недоступен."""
    api = (cfg.get("local_api") or "ollama").lower()
    path = "/api/tags" if api == "ollama" else "/v1/models"
    req = urllib.request.Request(
        local_url(cfg) + path, headers=_auth_headers(cfg), method="GET"
    )
    with _direct.open(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if api == "ollama":
        return [item.get("name", "?") for item in data.get("models", [])]
    return [item.get("id", "?") for item in data.get("data", [])]


def _model_key(name: str) -> str:
    """unsloth/Qwen3-27B-GGUF, Qwen3-27B-GGUF и qwen3-27b-gguf:q4 — одно и то же."""
    return name.strip().split("/")[-1].split(":")[0].lower()


def local_models_full(cfg: dict) -> list[dict]:
    """Сырые записи о моделях: имя, квантизация, контекст, загружена ли."""
    api = (cfg.get("local_api") or "ollama").lower()
    path = "/api/tags" if api == "ollama" else "/v1/models"
    req = urllib.request.Request(
        local_url(cfg) + path, headers=_auth_headers(cfg), method="GET"
    )
    with _direct.open(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("models" if api == "ollama" else "data", [])


def check_vision(cfg: dict, model: str):
    """Умеет ли модель смотреть картинки. None — сервер не ответил внятно.

    Пробуем два написания имени: со слешем как есть и закодированное. Сервер
    может понимать только одно из них, а на незнакомое имя честно отвечать
    «нет» — и мы приняли бы это за отказ.
    """
    base = local_url(cfg) + "/api/models/check-vision/"
    data = None
    for candidate in (model, urllib.parse.quote(model, safe="")):
        req = urllib.request.Request(base + candidate, headers=_auth_headers(cfg),
                                     method="GET")
        try:
            with _direct.open(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            log.info("Проверка картинок для %s: %s", model, data)
            break
        except Exception as exc:
            log.info("Проверка картинок (%s) не удалась: %s", candidate[:40], exc)
    if data is None:
        return None
    if isinstance(data, bool):
        return data
    for key in ("vision", "supports_vision", "is_vision", "has_vision", "supported"):
        if key in data:
            return bool(data[key])
    return None


def has_model(models: list[str], wanted: str) -> bool:
    key = _model_key(wanted)
    return any(_model_key(m) == key for m in models)


# --------------------------------------------------------------------------- диспетчер

def _fallback(video_name: str, cfg: dict) -> dict:
    stem = re.sub(r"[_\-]+", " ", Path(video_name).stem).strip()
    title = stem[:TITLE_LIMIT] or "Без названия"
    return {
        "title": title,
        "description": title,
        "tags": [],
        "category_id": cfg.get("fallback_category_id", "22"),
        "language": cfg.get("output_language", "ru"),
        "generated": False,
        "provider": "имя файла",
    }


def _provider_order(cfg: dict) -> list[str]:
    provider = (cfg.get("provider") or "auto").lower()
    if provider == "claude":
        return ["claude"]
    if provider == "local":
        return ["local"]
    if provider == "local_first":
        return ["local", "claude"]
    return ["claude", "local"]  # auto


def generate(video_name: str, transcript: str, cfg: dict, duration_sec: float | None = None,
             video_path: Path | None = None) -> dict:
    """Возвращает dict: title, description, tags, category_id, language, generated, provider."""
    images: list[bytes] = []
    if cfg.get("vision") and video_path is not None and not transcript:
        images = extract_frames(
            video_path,
            int(cfg.get("vision_frames", 12)),
            int(cfg.get("vision_width", 640)),
            int(cfg.get("vision_token_budget", 16000)),
        )
        if not images:
            # Без кадров текстовой модели опираться не на что: она знает только
            # имя файла. Поднимать ради этого тяжёлую модель незачем — пусть
            # ролик подождёт следующего запуска.
            log.warning("Кадры не извлеклись, а без них описывать нечего — "
                        "оставляю ролик на следующий раз")
            return _fallback(video_name, cfg)

    prompt = _build_prompt(video_name, transcript, cfg, duration_sec, len(images))

    for provider in _provider_order(cfg):
        try:
            if provider == "claude":
                data = _ask_claude(prompt, cfg)
            else:
                log.info("Спрашиваю %s на %s",
                         cfg.get("vision_model") if images else cfg.get("local_model"),
                         cfg.get("local_url"))
                data = _ask_local(prompt, cfg, images or None)
        except ImportError:
            log.warning("Пакет anthropic не установлен — пропускаю Claude")
            continue
        except Exception as exc:
            log.warning("Генератор %s не справился: %s", provider, exc)
            continue

        clean = _sanitize(data, cfg)
        clean["provider"] = provider
        log.info("Метаданные написал: %s", provider)
        return clean

    log.warning("Ни один генератор не ответил — беру название из имени файла")
    return _fallback(video_name, cfg)


def _sanitize(data: dict, cfg: dict) -> dict:
    title = " ".join(str(data.get("title", "")).split())[:TITLE_LIMIT] or "Без названия"

    description = str(data.get("description", "")).strip()
    footer = (cfg.get("description_footer") or "").strip()
    if footer:
        description = description + "\n\n" + footer
    # YouTube отклоняет угловые скобки в заголовке и описании
    description = description.replace("<", "‹").replace(">", "›")[:DESC_LIMIT]
    title = title.replace("<", "‹").replace(">", "›")

    tags: list[str] = []
    used = 0
    for raw in data.get("tags") or []:
        tag = " ".join(str(raw).split()).lstrip("#").strip()
        if not tag or any(tag.lower() == t.lower() for t in tags):
            continue
        cost = len(tag) + (1 if tags else 0)
        if used + cost > TAGS_TOTAL_LIMIT:
            break
        tags.append(tag)
        used += cost

    category_id = CATEGORIES.get(
        str(data.get("category", "")).strip(), cfg.get("fallback_category_id", "22")
    )

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "category_id": category_id,
        "language": str(data.get("language") or cfg.get("output_language", "ru"))[:5],
        "generated": True,
    }
