import json
import os
import shutil
import random
import asyncio
import time
from datetime import datetime, timedelta
import re
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.errors import FloodWaitError

# ───── НАСТРОЙКИ ─────
# DATA_DIR — где лежат/создаются все изменяемые файлы (сессия, конфиг,
# группы, кеш, фидбек). По умолчанию — папка проекта (локальный запуск,
# ничего не меняется). На Railway/другом хостинге с эфемерным диском можно
# подключить постоянный Volume и указать DATA_DIR=/путь/к/volume — тогда
# то, что бот меняет командами в ЛС (группы/банворды/админы/фидбек),
# переживёт передеплой. Без volume эти файлы каждый передеплой откатятся
# к тому, что закоммичено в git.
DATA_DIR = os.environ.get("DATA_DIR", ".")


def _data_path(name):
    return os.path.join(DATA_DIR, name)


# api_id/api_hash/owner_id — приватные данные, в репозиторий не попадают.
# Локально: скопируйте secrets.example.json в secrets.json и впишите свои
# значения (api_id/api_hash берутся на https://my.telegram.org).
# На Railway/другом хостинге — задайте переменные окружения API_ID,
# API_HASH, OWNER_ID вместо файла (см. README, раздел про Railway).
SECRETS_PATH = _data_path("secrets.json")


def load_secrets():
    env_api_id = os.environ.get("API_ID")
    env_api_hash = os.environ.get("API_HASH")
    env_owner_id = os.environ.get("OWNER_ID")
    if env_api_id and env_api_hash and env_owner_id:
        return int(env_api_id), env_api_hash, env_owner_id

    if not os.path.exists(SECRETS_PATH):
        raise SystemExit(
            f"Не найден {SECRETS_PATH} и не заданы переменные окружения "
            "API_ID/API_HASH/OWNER_ID. Локально — скопируйте secrets.example.json "
            f"в {SECRETS_PATH}; на хостинге — задайте переменные окружения."
        )
    with open(SECRETS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["api_id"], data["api_hash"], data["owner_id"]


api_id, api_hash, OWNER_ID = load_secrets()  # OWNER_ID: кому приходит дневной отчёт, единственный, кто может слать команды управления

CONFIG_PATH = _data_path("config.json")

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


# ───── ОБУЧЕНИЕ С ПОДТВЕРЖДЕНИЕМ (/spam, /notspam, /suggestions) ─────
FEEDBACK_STOPWORDS = {
    "в", "на", "и", "с", "по", "за", "для", "от", "до", "из", "у", "о", "а", "но",
    "же", "ли", "бы", "не", "что", "это", "как", "мне", "вам", "есть", "или",
    "то", "так", "при", "под", "над",
} | set(KEYWORDS)


def suggest_banned_words(top_n=10, min_count=2):
    # слово попадает в подсказку, только если встречалось в подтверждённом
    # спаме минимум min_count раз и ни разу — в подтверждённых лидах
    spam_counts = {}
    lead_words = set()
    for t in FEEDBACK["lead_texts"]:
        lead_words.update(re.findall(r"[а-яёa-z0-9]+", t))
    for t in FEEDBACK["spam_texts"]:
        for w in set(re.findall(r"[а-яёa-z0-9]+", t)):
            if w in FEEDBACK_STOPWORDS or w in BANNED_WORDS or w in lead_words:
                continue
            spam_counts[w] = spam_counts.get(w, 0) + 1
    ranked = sorted(spam_counts.items(), key=lambda kv: -kv[1])
    return [(w, c) for w, c in ranked if c >= min_count][:top_n]


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
GROUPS_PATH = _data_path("groups.txt")
JOINED_GROUPS_PATH = _data_path("joined_groups.json")

if not os.path.exists(GROUPS_PATH):
    # DATA_DIR ещё пустой (например, свежий volume на хостинге) — затравка
    # из groups.txt в корне проекта, который лежит в репозитории
    bundled_groups = "groups.txt"
    if DATA_DIR != "." and os.path.exists(bundled_groups):
        shutil.copy(bundled_groups, GROUPS_PATH)
    else:
        open(GROUPS_PATH, "w", encoding="utf-8").close()

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


FEEDBACK_PATH = _data_path("feedback.json")
FEEDBACK_CAP = 300  # сколько последних примеров каждого типа хранить


def load_feedback():
    if os.path.exists(FEEDBACK_PATH):
        try:
            with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "spam_texts": data.get("spam_texts", []),
                "lead_texts": data.get("lead_texts", []),
            }
        except Exception as e:
            print(f"[!] Не удалось прочитать {FEEDBACK_PATH}: {e}")
    return {"spam_texts": [], "lead_texts": []}


def save_feedback():
    with open(FEEDBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(FEEDBACK, f, ensure_ascii=False, indent=2)


# Размеченные примеры сообщений — каждый найденный лид сразу попадает сюда
# как "notspam" (по умолчанию считаем находку реальным лидом); командой
# /spam владелец переносит конкретное сообщение в спам. На основе этого
# /suggestions предлагает новые бан-слова.
FEEDBACK = load_feedback()


def mark_feedback(text, is_spam):
    target = FEEDBACK["spam_texts"] if is_spam else FEEDBACK["lead_texts"]
    other = FEEDBACK["lead_texts"] if is_spam else FEEDBACK["spam_texts"]
    if text in other:
        other.remove(text)
    if text not in target:
        target.append(text)
    del target[:-FEEDBACK_CAP]  # оставляем только последние FEEDBACK_CAP
    save_feedback()


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


# SESSION_STRING (переменная окружения) — сессия Telethon как строка, без
# файла на диске. Нужна для хостинга с эфемерной файловой системой (Railway
# и т.п.): без неё контейнер после каждого передеплоя терял бы авторизацию
# и требовал заново вводить код из Telegram. Строку получить один раз
# локально скриптом generate_session_string.py и положить в переменные
# окружения хостинга. Если переменной нет — как раньше, файл session.session
# в DATA_DIR (локальный запуск).
SESSION_STRING = os.environ.get("SESSION_STRING")
if SESSION_STRING:
    session = StringSession(SESSION_STRING)
else:
    session = _data_path("session")

client = TelegramClient(session, api_id, api_hash)

ALLOWED_CHAT_IDS = set()
# восстанавливаем из кеша без единого запроса к Telegram —
# резолвиться заново будут только новые группы
ALLOWED_CHAT_IDS.update(cid for cid in JOINED_GROUPS.values() if cid is not None)
handled_messages = set()
user_last_reply = {}
last_global_send = 0
user_last_seen = {}

# message_id (в чате с владельцем) → исходный текст лида — чтобы /spam и
# /notspam, отправленные ответом на пересланное сообщение, знали, какой
# текст размечать. Переживать перезапуск бота фидбеку не нужно.
lead_msg_ids = {}
LEAD_MSG_CAP = 2000

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

    # по умолчанию считаем находку реальным лидом — /spam переносит
    # конкретное сообщение в спам, если это ошибка
    mark_feedback(text, is_spam=False)

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
            sent = await client.send_message(admin, admin_text)
            if admin.lower() == OWNER_ID.lower():
                # запоминаем, какому сообщению у владельца соответствует
                # какой текст — для /spam и /notspam
                lead_msg_ids[sent.id] = event.raw_text
                if len(lead_msg_ids) > LEAD_MSG_CAP:
                    lead_msg_ids.clear()
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
    "/spam — ответом на пересланный лид: это спам, а не реальный лид (для /suggestions)\n"
    "/notspam — ответом на пересланный лид: отменить пометку /spam, если ошиблись\n"
    "/suggestions — показать слова, частые в спаме и не встречавшиеся в лидах\n"
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
    print(
        f"[cmd-debug] ЛС от username={getattr(sender, 'username', None)!r} "
        f"id={getattr(sender, 'id', None)!r} is_owner={is_owner(sender)} "
        f"OWNER_ID={OWNER_ID!r}"
    )
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

    elif cmd in ("/spam", "/notspam"):
        if not event.is_reply:
            await event.reply("Ответьте этой командой на пересланное сообщение с лидом")
            return
        raw = lead_msg_ids.get(event.reply_to_msg_id)
        if raw is None:
            await event.reply("Не нашёл исходный текст для этого сообщения (слишком старое или не от бота)")
            return
        mark_feedback(raw.lower(), is_spam=(cmd == "/spam"))
        await event.reply("✅ Записано как " + ("спам" if cmd == "/spam" else "реальный лид"))

    elif cmd == "/suggestions":
        candidates = suggest_banned_words()
        if not candidates:
            await event.reply("Пока нет кандидатов — нужно больше размеченного спама через /spam")
            return
        lines = [f"{w} (в спаме {c} раз)" for w, c in candidates]
        await event.reply(
            "Возможные новые бан-слова (встречались только в спаме, ни разу в лидах):\n"
            + "\n".join(lines)
            + "\n\nДобавить: /addword <слово>"
        )

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
