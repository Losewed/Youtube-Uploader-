"""OAuth для Google Drive + YouTube. Один токен на оба сервиса."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE = Path(__file__).resolve().parent
CLIENT_SECRET = BASE / "client_secret.json"
TOKEN = BASE / "token.json"

log = logging.getLogger("gauth")

SCOPES = [
    "https://www.googleapis.com/auth/drive",           # читать папку, перемещать/удалять после загрузки
    "https://www.googleapis.com/auth/youtube.upload",  # заливать видео
    "https://www.googleapis.com/auth/youtube",         # плейлисты, публикация по расписанию
]


def get_credentials(interactive: bool = False) -> Credentials:
    creds: Credentials | None = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        # Сеть рвётся — это не повод считать токен мёртвым, поэтому повторяем.
        last_error = None
        for attempt in range(1, 4):
            try:
                creds.refresh(Request())
                TOKEN.write_text(creds.to_json(), encoding="utf-8")
                return creds
            except RefreshError as exc:
                # Google явно отказал: доступ отозван или срок refresh-токена вышел.
                log.warning("Google отклонил refresh-токен (%s) — нужен новый вход", exc)
                creds = None
                break
            except Exception as exc:
                last_error = exc
                log.warning("Не смог обновить токен (попытка %d/3): %s", attempt, exc)
                if attempt < 3:
                    time.sleep(2 * attempt)

        if creds is not None:
            raise RuntimeError(
                "Токен Google не обновился из-за сетевой ошибки: {}. "
                "Сам токен цел — просто повторите запуск.".format(last_error)
            )

    if not interactive:
        raise RuntimeError(
            "Нет действительного токена Google. Запустите один раз: python gauth.py"
        )

    if not CLIENT_SECRET.exists():
        raise RuntimeError(
            f"Нет файла {CLIENT_SECRET.name}. Скачайте OAuth-клиент типа "
            f"'Desktop app' из Google Cloud Console и положите его сюда."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(TOKEN, 0o600)
    return creds


def drive_service(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def youtube_service(creds: Credentials):
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


if __name__ == "__main__":
    c = get_credentials(interactive=True)
    yt = youtube_service(c)
    resp = yt.channels().list(part="snippet", mine=True).execute(num_retries=5)
    items = resp.get("items", [])
    if items:
        print(f"Авторизация успешна. Канал: {items[0]['snippet']['title']}")
    else:
        print("Авторизация успешна, но у аккаунта нет YouTube-канала.")
    print(f"Токен сохранён в {TOKEN}")
