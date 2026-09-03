import json
import os
import random
import asyncio
import time
from datetime import datetime, timedelta
import re
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import FloodWaitError

# ───── НАСТРОЙКИ ─────
# api_id/api_hash/owner_id — приватные данные, в репозиторий не попадают.
# Скопируйте secrets.example.json в secrets.json и впишите свои значения
# (api_id/api_hash берутся на https://my.telegram.org).
SECRETS_PATH = "secrets.json"


def load_secrets():
    if not os.path.exists(SECRETS_PATH):
        raise SystemExit(
            f"Не найден {SECRETS_PATH}. Скопируйте secrets.example.json в "
            f"{SECRETS_PATH} и укажите свои api_id/api_hash/owner_id."
        )
    with open(SECRETS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["api_id"], data["api_hash"], data["owner_id"]


api_id, api_hash, OWNER_ID = load_secrets()  # OWNER_ID: кому приходит дневной отчёт, единственный, кто может слать команды управления

CONFIG_PATH = "config.json"

# Значения по умолчанию — используются только пока не создан config.json
# (первый запуск). Дальше банворды и получателей уведомлений менять
# командами в ЛС боту (/help), а не правкой этих списков.
DEFAULT_NOTIFY_USERNAMES = [
    "@Severmng",
    "@Smoke_Shelter",
    "@BigPar_Smoke",
    "@Tyzemec1608",
    "@ApexVapeManager",
    OWNER_ID,
]

DEFAULT_BANNED_WORDS = [
    "голды", "голда", "голду", "пины", "usdt", "trc", "телефон", "электрошокер",
    "голдишку", "пуховик", "продам", "нфт", "водитель", "тезер", "юсдт", "акк",
    "ак", "пиво", "человека", "человек", "tether", "подработка", "подработку",
    "сиги", "сигареты", "помощник", "помошник", "звезды", "звезд", "u.s.d.t",
    "курс", "убрать", "секс", "пневмат", "работа", "юздт", "трс20", "юсдд",
    "расчет", "расчёт", "перец", "помочь", "сложного", "помощь",
    "дверь", "двери", "окна", "окно", "помыть", "поставить",
    "плотник", "строитель", "ремонт", "отделка", "сантехник",
    "электрик", "маляр", "штукатур", "плитка", "кладка",
    "уборка", "вывезти", "мусор", "строительный",
    "день", "рублей", "тысяч", "питание", "спроездом",
    "помогу", "работник", "рабочий", "бригада",
    "зарплата", "оплата", "договоримся", "звоните", "позвоните",
    "дизайнера", "люди", "людей", "сделаю", "работы", "шустрых",
    "разнорабочего", "водила", "машина", "водителя", "презерватив",
    "раба", "заработок", "энергетики", "вирт", "парня", "деле"
]


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (
                data.get("notify_usernames", DEFAULT_NOTIFY_USERNAMES),
                data.get("banned_words", DEFAULT_BANNED_WORDS),
            )
        except Exception as e:
            print(f"[!] Не удалось прочитать {CONFIG_PATH}: {e}")
    return list(DEFAULT_NOTIFY_USERNAMES), list(DEFAULT_BANNED_WORDS)


def save_config():
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"notify_usernames": NOTIFY_USERNAMES, "banned_words": BANNED_WORDS},
            f, ensure_ascii=False, indent=2,
        )


NOTIFY_USERNAMES, BANNED_WORDS = load_config()

KEYWORDS = [
    "куплю", "купить", "покупаю", "ищу", "срочно куплю",
    "ищю"
]


def _build_word_pattern(words):
    return re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b",
        re.IGNORECASE,
    )


KEYWORDS_PATTERN = _build_word_pattern(KEYWORDS)
BANNED_WORDS_PATTERN = _build_word_pattern(BANNED_WORDS)


def rebuild_banned_pattern():
    global BANNED_WORDS_PATTERN
    BANNED_WORDS_PATTERN = _build_word_pattern(BANNED_WORDS)


def normalize_username(username):
    username = username.strip()
    if not username.startswith("@"):
        username = "@" + username
    return username


def is_owner(sender):
    return bool(
        sender
        and sender.username
        and normalize_username(sender.username).lower() == OWNER_ID.lower()
    )

AUTO_MESSAGES = [
    "Напиши @C0nt1nious, у него должно быть",
    "Советую написать @C0nt1nious, он подскажет",
]

IGNORED_USERNAMES = {
    "nonameolega",
    "richardoon",
    "minskioleg",
    "ichnew",
    "nemoshig",
    "eaessi"
}

MAX_MESSAGE_LEN = 150
USER_COOLDOWN = 60 * 60     # 1 день
GLOBAL_COOLDOWN = 60 * 30           # 30 минут
DUPLICATE_USER_WINDOW = 60 * 60  # 1 час

# ───── СТАТИСТИКА ─────
stats = {
    "checked": 0,
    "found": 0,
    "sent": 0,
    "filtered_long": 0,
    "filtered_duplicate": 0,
}

found_clients = []
initial_setup_done = False  # вступление в группы + запуск шедулера — только один раз, не при каждом переподключении
last_report_date = None
ENABLE_AUTO_SUBSCRIBE = True
ENABLE_JOIN_AND_CACHE = True
# ───── ДАННЫЕ ─────
GROUPS_PATH = "groups.txt"
JOINED_GROUPS_PATH = "joined_groups.json"

with open(GROUPS_PATH, "r", encoding="utf-8") as f:
    GROUPS = [line.strip() for line in f if line.strip()]


def save_groups():
    with open(GROUPS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(GROUPS) + ("\n" if GROUPS else ""))


def load_joined_groups():
    if os.path.exists(JOINED_GROUPS_PATH):
        try:
            with open(JOINED_GROUPS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                # старый формат файла (просто список ссылок, без id) —
                # id ещё не закеширован, для этих групп он будет
                # получен один раз при ближайшем запуске
                return {identifier: None for identifier in data}
            return data
        except Exception as e:
            print(f"[!] Не удалось прочитать {JOINED_GROUPS_PATH}: {e}")
    return {}


def save_joined_groups():
    with open(JOINED_GROUPS_PATH, "w", encoding="utf-8") as f:
        json.dump(JOINED_GROUPS, f, ensure_ascii=False, indent=2)


# Группы, в которые уже успешно вступили — chat_id каждой закеширован тут,
# так что при обычном рестарте бота ничего не резолвится и не запрашивается
# через API. Вступление (и резолв через get_entity) повторяется только для
# групп, которых ещё нет в этом словаре, т.е. которые реально добавили в
# groups.txt. Если вступление не удалось (флудвейт, ошибка сети и т.п.),
# группа сюда не попадает, и попытка будет повторена при следующем запуске.
JOINED_GROUPS = load_joined_groups()

# groups.txt меняется руками/командами — если группу оттуда убрали,
# чистим и её отметку о подписке, чтобы файл не тянул мёртвые записи.
_stale_joined = [g for g in JOINED_GROUPS if g not in GROUPS]
if _stale_joined:
    for _g in _stale_joined:
        del JOINED_GROUPS[_g]
    save_joined_groups()


client = TelegramClient("session", api_id, api_hash)

ALLOWED_CHAT_IDS = set()
# восстанавливаем из кеша без единого запроса к Telegram —
# резолвиться заново будут только новые группы
ALLOWED_CHAT_IDS.update(cid for cid in JOINED_GROUPS.values() if cid is not None)
handled_messages = set()
user_last_reply = {}
last_global_send = 0
user_last_seen = {}

# ───── ВСТУПАЕМ В ГРУППЫ ─────
async def join_single_group(identifier, skip_if_joined=False):
    entity = await client.get_entity(identifier)
    ALLOWED_CHAT_IDS.add(entity.id)

    if ENABLE_AUTO_SUBSCRIBE:
        if skip_if_joined and identifier in JOINED_GROUPS:
            pass  # уже подписаны в прошлый раз — не дёргаем API повторно
        else:
            try:
                await client(JoinChannelRequest(entity))
            except FloodWaitError as fe:
                print(f"⚠ FloodWait {fe.seconds} сек – ждём...")
                await asyncio.sleep(fe.seconds)

    JOINED_GROUPS[identifier] = entity.id
    save_joined_groups()

    return entity


async def join_groups_and_cache_ids():
    # обрабатываем только группы, которых ещё нет в кеше (id не известен) —
    # т.е. реально новые строки в groups.txt. Первый запуск = кеш пуст,
    # поэтому обрабатываются все группы; дальше — только изменения файла.
    pending = [g for g in GROUPS if JOINED_GROUPS.get(g) is None]
    if not pending:
        print("ℹ️ Список групп не менялся — пропускаем подписку")
        return

    for group in pending:
        try:
            entity = await join_single_group(group, skip_if_joined=True)
            print(f"[+] Группа: {group} ({entity.id})")

            await asyncio.sleep(random.randint(1, 2))

        except FloodWaitError as fe:
            print(f"⚠ FloodWait {fe.seconds} сек – ждём...")
            #await asyncio.sleep(fe.seconds)

        except Exception as e:
            print(f"[!] Ошибка группы {group}: {e}")

            # 🔥 ВЫТАСКИВАЕМ секунды из текста
            match = re.search(r'wait of (\d+) seconds', str(e))
            if match:
                wait_time = int(match.group(1))
                print(f"⏳ Ждём {wait_time} сек (из Exception)...")
                await asyncio.sleep(wait_time)

# ───── ОБРАБОТКА СООБЩЕНИЙ ─────
@client.on(events.NewMessage)
async def handler(event):
    global last_global_send

    if not event.is_group:
        return

    chat = event.chat
    if not chat or chat.id not in ALLOWED_CHAT_IDS:
        return

    stats["checked"] += 1
    sender = await event.get_sender()
    if not sender:
        return

    # ───── ИГНОР ПО USERNAME (если есть) ─────
    if sender.username and sender.username.lower() in IGNORED_USERNAMES:
        print("🚫 Игнор по username:", sender.username)
        return

    # антидубль сообщений
    if event.id in handled_messages:
        stats["filtered_duplicate"] += 1
        return

    handled_messages.add(event.id)
    if len(handled_messages) > 50_000:
        handled_messages.clear()

    text = (event.raw_text or "").lower()

    if len(text) > MAX_MESSAGE_LEN:
        stats["filtered_long"] += 1
        return

    if not KEYWORDS_PATTERN.search(text):
        return

    if BANNED_WORDS_PATTERN.search(text):
        print("🚫 Сообщение содержит запрещённые слова, пропускаем")
        return

    now = time.time()

    # антидубль: один пользователь в разных группах (по ID)
    if sender.id in user_last_seen:
        if now - user_last_seen[sender.id] < DUPLICATE_USER_WINDOW:
            print("⏭ Повтор от пользователя, пропускаем уведомление")
            return

    user_last_seen[sender.id] = now

    # антидубль по пользователю (ЛС) – используем ID
    if sender.id in user_last_reply:
        if now - user_last_reply[sender.id] < USER_COOLDOWN:
            return

    # ───── ФОРМИРУЕМ ССЫЛКУ НА СООБЩЕНИЕ ─────
    if chat.username:
        message_link = f"https://t.me/{chat.username}/{event.id}"
    else:
        chat_id_positive = abs(chat.id) if chat.id < 0 else chat.id
        message_link = f"https://t.me/c/{chat_id_positive}/{event.id}"

    # ───── ОПРЕДЕЛЯЕМ, КАК ОТОБРАЗИТЬ ПОЛЬЗОВАТЕЛЯ ─────
    if sender.username:
        user_display = f"@{sender.username}"
        user_short = sender.username
    else:
        user_display = f"Пользователь {sender.id} (без username)"
        user_short = f"user_{sender.id}"

    stats["found"] += 1

    print("\n🔥 НАЙДЕН КЛИЕНТ")
    print("Группа:", chat.title)
    print("Пользователь:", user_display)
    print("Сообщение:", event.raw_text)
    print("Ссылка:", message_link)

    found_clients.append(
        f"{user_display} | {chat.title}\n{text}\n{message_link}"
    )

    admin_text = (
        f"{user_display}\n"
        f"{event.raw_text}\n"
        f"{message_link}"
    )

    for admin in NOTIFY_USERNAMES:
        try:
            await client.send_message(admin, admin_text)
            print(f"✅ Отправлено: {admin}")
        except FloodWaitError as e:
            print(f"⏳ FloodWait для {admin}: {e.seconds} сек")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            print(f"❌ Не удалось отправить {admin}: {e}")     


    # try:
    #     await client.send_message(
    #         sender.id,
    #         random.choice(AUTO_MESSAGES)
    #     )
    #     user_last_reply[sender.id] = now
    #     last_global_send = now
    #     stats["sent"] += 1
    #     print("✅ ЛС отправлено")

    # except FloodWaitError as e:
    #     print(f"⏳ FLOODWAIT {e.seconds} сек")
    #     await asyncio.sleep(e.seconds)

    # except Exception as e:
    #     print("⛔ ЛС запрещено:", e)

# ───── УПРАВЛЕНИЕ КОМАНДАМИ (только владелец, в ЛС боту) ─────
HELP_TEXT = (
    "Доступные команды:\n"
    "/addword <слово> — добавить бан-слово\n"
    "/delword <слово> — убрать бан-слово\n"
    "/listwords — показать бан-слова\n"
    "/addgroup <@группа или ссылка> — добавить и вступить в группу\n"
    "/delgroup <@группа> — убрать группу из мониторинга\n"
    "/listgroups — показать список групп\n"
    "/addadmin <@username> — добавить получателя уведомлений\n"
    "/deladmin <@username> — убрать получателя уведомлений\n"
    "/listadmins — показать получателей уведомлений\n"
    "/help — это сообщение"
)


@client.on(events.NewMessage(outgoing=False))
async def command_handler(event):
    if not event.is_private:
        return

    sender = await event.get_sender()
    if not is_owner(sender):
        return

    text = (event.raw_text or "").strip()
    if not text.startswith("/"):
        return

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/help":
        await event.reply(HELP_TEXT)

    elif cmd == "/addword":
        if not arg:
            await event.reply("Использование: /addword <слово>")
            return
        word = arg.lower()
        if word in BANNED_WORDS:
            await event.reply(f"«{word}» уже в бан-списке")
            return
        BANNED_WORDS.append(word)
        rebuild_banned_pattern()
        save_config()
        await event.reply(f"✅ Добавлено бан-слово: {word}")

    elif cmd == "/delword":
        word = arg.lower()
        if word not in BANNED_WORDS:
            await event.reply(f"«{word}» не найдено в бан-списке")
            return
        BANNED_WORDS.remove(word)
        rebuild_banned_pattern()
        save_config()
        await event.reply(f"✅ Убрано бан-слово: {word}")

    elif cmd == "/listwords":
        await event.reply("Бан-слова:\n" + ", ".join(BANNED_WORDS))

    elif cmd == "/addgroup":
        if not arg:
            await event.reply("Использование: /addgroup <@группа или ссылка>")
            return
        if arg in GROUPS:
            await event.reply("Эта группа уже в списке")
            return
        try:
            entity = await join_single_group(arg)
        except Exception as e:
            await event.reply(f"❌ Не удалось добавить группу: {e}")
            return
        GROUPS.append(arg)
        save_groups()
        await event.reply(f"✅ Группа добавлена: {getattr(entity, 'title', arg)} ({entity.id})")

    elif cmd == "/delgroup":
        if not arg or arg not in GROUPS:
            await event.reply("Группа не найдена в списке")
            return
        try:
            entity = await client.get_entity(arg)
            ALLOWED_CHAT_IDS.discard(entity.id)
        except Exception as e:
            print(f"[!] Не удалось получить entity при удалении группы {arg}: {e}")
        GROUPS.remove(arg)
        save_groups()
        JOINED_GROUPS.pop(arg, None)
        save_joined_groups()
        await event.reply(f"✅ Группа убрана из мониторинга: {arg}")

    elif cmd == "/listgroups":
        await event.reply("Группы:\n" + "\n".join(GROUPS) if GROUPS else "Список групп пуст")

    elif cmd == "/addadmin":
        if not arg:
            await event.reply("Использование: /addadmin <@username>")
            return
        username = normalize_username(arg)
        if username.lower() in [u.lower() for u in NOTIFY_USERNAMES]:
            await event.reply(f"{username} уже получает уведомления")
            return
        NOTIFY_USERNAMES.append(username)
        save_config()
        await event.reply(f"✅ Добавлен получатель: {username}")

    elif cmd == "/deladmin":
        if not arg:
            await event.reply("Использование: /deladmin <@username>")
            return
        username = normalize_username(arg)
        if username.lower() == OWNER_ID.lower():
            await event.reply("Нельзя убрать владельца из списка получателей")
            return
        match = next((u for u in NOTIFY_USERNAMES if u.lower() == username.lower()), None)
        if not match:
            await event.reply(f"{username} не найден в списке получателей")
            return
        NOTIFY_USERNAMES.remove(match)
        save_config()
        await event.reply(f"✅ Убран получатель: {match}")

    elif cmd == "/listadmins":
        await event.reply("Получатели уведомлений:\n" + "\n".join(NOTIFY_USERNAMES))

# ───── ДНЕВНОЙ ОТЧЁТ ─────
async def send_daily_report():

    global last_report_date

    today = datetime.now().date()
    if last_report_date == today:
        print("⏭ Отчёт уже отправлялся сегодня, пропуск")
        return

    last_report_date = today

    text = f"""
📊 ДНЕВНОЙ ОТЧЁТ ({datetime.now().strftime('%d.%m.%Y')})

🔍 Проверено: {stats['checked']}
🔥 Найдено: {stats['found']}
✉️ ЛС отправлено: {stats['sent']}

🚫 Длинные: {stats['filtered_long']}
🚫 Дубликаты: {stats['filtered_duplicate']}
"""

    if found_clients:
        text += "\n👤 Клиенты:\n" + "\n\n".join(found_clients[:20])

    await client.send_message(OWNER_ID, text)

    for k in stats:
        stats[k] = 0
    found_clients.clear()

async def daily_report_scheduler():
    while True:
        now = datetime.now()
        target = now.replace(hour=23, minute=59, second=0)
        if now > target:
            target += timedelta(days=1)

        await asyncio.sleep((target - now).total_seconds())
        await send_daily_report()

# ───── MAIN ─────
RECONNECT_DELAY = 5          # стартовая задержка перед повторным подключением, сек
RECONNECT_DELAY_MAX = 300    # верхняя граница задержки (5 минут)
STABLE_UPTIME = 60           # если продержались дольше этого — задержку сбрасываем к стартовой


async def main():
    global initial_setup_done

    delay = RECONNECT_DELAY
    while True:
        connected_at = time.time()
        try:
            await client.start()
            await asyncio.sleep(5)

            if not initial_setup_done:
                if ENABLE_JOIN_AND_CACHE:
                    await join_groups_and_cache_ids()
                client.loop.create_task(daily_report_scheduler())
                initial_setup_done = True

            print("🚀 Юзербот запущен (ФИНАЛЬНАЯ ВЕРСИЯ)")
            await client.run_until_disconnected()
            print("ℹ️ Соединение закрыто, переподключаемся...")

        except FloodWaitError as fe:
            print(f"⚠ FloodWait при подключении: {fe.seconds} сек")
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(fe.seconds)
            continue

        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            print(f"⚠ Обрыв соединения: {e}")

        except Exception as e:
            print(f"❌ Ошибка в главном цикле: {e}")

        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass

        if time.time() - connected_at > STABLE_UPTIME:
            delay = RECONNECT_DELAY
        else:
            delay = min(delay * 2, RECONNECT_DELAY_MAX)

        print(f"🔄 Переподключение через {delay} сек...")
        await asyncio.sleep(delay)

asyncio.run(main())
