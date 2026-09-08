"""
Разовый скрипт: генерирует строку сессии Telethon для деплоя на хостинг
с эфемерным диском (Railway и т.п.), где обычный файл session.session
не переживает передеплой.

Если рядом уже лежит авторизованный session.session (обычный локальный
запуск veip.py/get_ids.py) — скрипт переиспользует его, повторный вход
не требуется. Если файла нет — запросит номер телефона и код из Telegram
(и пароль 2FA, если включён); в этом случае запускать нужно в обычном
интерактивном терминале.

    python generate_session_string.py

Полученную строку сохранить как переменную окружения SESSION_STRING
в настройках сервиса на Railway (Variables). После этого файл
session.session на хостинге не нужен — авторизация хранится в переменной.

Использует те же api_id/api_hash, что и veip.py, из secrets.json.
"""
import json
import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

with open("secrets.json", "r", encoding="utf-8") as f:
    secrets = json.load(f)

api_id = secrets["api_id"]
api_hash = secrets["api_hash"]

if os.path.exists("session.session"):
    with TelegramClient("session", api_id, api_hash) as client:
        if not client.is_user_authorized():
            raise SystemExit(
                "session.session есть, но не авторизован — удалите файл "
                "и запустите скрипт заново для входа с нуля."
            )
        session_string = StringSession.save(client.session)
else:
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()

print("\nГотово! Сохраните это значение как переменную окружения SESSION_STRING:\n")
print(session_string)
print()
