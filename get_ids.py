import json
from telethon import TelegramClient

with open("secrets.json", "r", encoding="utf-8") as f:
    _secrets = json.load(f)

api_id = _secrets["api_id"]
api_hash = _secrets["api_hash"]
session = 'session'  # тот же, что в авторассылке

async def main():
    async with TelegramClient(session, api_id, api_hash) as client:
        messages = await client.get_messages("me", limit=20)
        for m in messages:
            print(m.id, "→", m.text[:50] if m.text else "<медиа>")

import asyncio
asyncio.run(main())
