"""
Разовый скрипт: генерирует строку сессии Telethon для деплоя на хостинг
с эфемерным диском (Railway и т.п.), где обычный файл session.session
не переживает передеплой.

Запускать ЛОКАЛЬНО, в обычном терминале (не через автоматизацию) — потребуется
ввести номер телефона и код из Telegram (и пароль 2FA, если он включён).

    python generate_session_string.py

Полученную строку сохранить как переменную окружения SESSION_STRING
в настройках сервиса на Railway (Variables). После этого файл
session.session на хостинге не нужен — авторизация хранится в переменной.

Использует те же api_id/api_hash, что и veip.py, из secrets.json.
"""
import json

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

with open("secrets.json", "r", encoding="utf-8") as f:
    secrets = json.load(f)

with TelegramClient(StringSession(), secrets["api_id"], secrets["api_hash"]) as client:
    session_string = client.session.save()
    print("\nГотово! Сохраните это значение как переменную окружения SESSION_STRING:\n")
    print(session_string)
    print()
