import os
import re
import time
import sqlite3
import asyncio
import hashlib
import logging

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError


# ============================================================
# НАСТРОЙКИ
# ============================================================

load_dotenv()

# Bothost: используем стандартные имена переменных.
# Для совместимости оставлены и старые TG_* имена.
API_ID = int(
    os.getenv("TELEGRAM_API_ID")
    or os.getenv("TG_API_ID")
    or "0"
)

API_HASH = (
    os.getenv("TELEGRAM_API_HASH")
    or os.getenv("TG_API_HASH")
    or ""
).strip()

# На сервере нужен именно String Session.
SESSION_STRING = (
    os.getenv("SESSION_STRING")
    or os.getenv("TG_SESSION")
    or ""
).strip()

DATABASE_FILE = os.getenv(
    "TG_DATABASE",
    "telegram_monitor.db"
).strip()

# Если в базе еще не задан чат, будет использовано это значение.
# Можно оставить "me", тогда уведомления временно идут в Избранное.
ENV_NOTIFICATION_TARGET = os.getenv(
    "TG_NOTIFICATION_TARGET",
    "me"
).strip()

# Мониторить только группы/супергруппы
ONLY_GROUPS = True

# Не анализировать собственные сообщения
IGNORE_OUTGOING = True

# Минимальный балл для заявки
MIN_SCORE = 5

# Балл, после которого заявка считается горячей
HOT_SCORE = 9

# Защита от повторов
DEDUP_SECONDS = 30 * 60

# Максимальная длина текста в уведомлении
MAX_MESSAGE_LENGTH = 3000

# Ежедневная сводка
DAILY_SUMMARY_HOUR = int(
    os.getenv("DAILY_SUMMARY_HOUR", "21")
)

DAILY_SUMMARY_MINUTE = int(
    os.getenv("DAILY_SUMMARY_MINUTE", "0")
)


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("telegram_monitor")


# ============================================================
# ПРОВЕРКА НАСТРОЕК
# ============================================================

if not API_ID:
    raise RuntimeError(
        "Не указан TELEGRAM_API_ID (или TG_API_ID) в переменных окружения"
    )

if not API_HASH:
    raise RuntimeError(
        "Не указан TELEGRAM_API_HASH (или TG_API_HASH) в переменных окружения"
    )

if not SESSION_STRING:
    raise RuntimeError(
        "Не указан SESSION_STRING (или TG_SESSION). "
        "Для Bothost нужен String Session, а не файл .session."
    )


# ============================================================
# TELEGRAM CLIENT
# ============================================================

# StringSession позволяет серверу запускать userbot без
# интерактивного ввода номера, кода и пароля 2FA.
client = TelegramClient(
    StringSession(SESSION_STRING),
    API_ID,
    API_HASH,
    auto_reconnect=True,
    connection_retries=None,
    retry_delay=5,
)


# ============================================================
# КАТЕГОРИИ УСЛУГ
# ============================================================

CATEGORIES = {
    "🪑 Мебель": [
        "мебель",
        "шкаф",
        "шкафа",
        "шкафы",
        "кухня",
        "кухню",
        "кухонный гарнитур",
        "диван",
        "кровать",
        "матрас",
        "комод",
        "стол",
        "стул",
        "кресло",
        "тумба",
        "полка",
        "полки",
        "гардероб",
        "гардеробная",
        "мебельщик",
        "сборка мебели",
    ],

    "🏠 Ремонт": [
        "ремонт",
        "ремонт квартиры",
        "ремонт дома",
        "ремонт комнаты",
        "отделка",
        "отделочник",
        "мастер по ремонту",
        "ремонтник",
        "строитель",
        "строительство",
        "штукатур",
        "штукатурка",
        "шпаклевка",
        "шпаклевщик",
        "плиточник",
        "кафель",
        "укладка плитки",
        "поклейка обоев",
        "обои",
        "маляр",
        "покраска стен",
        "покраска",
    ],

    "🚿 Сантехника": [
        "сантехник",
        "сантехника",
        "сантехнические",
        "труба",
        "трубы",
        "протечка",
        "течет",
        "течет кран",
        "кран",
        "смеситель",
        "унитаз",
        "унитаза",
        "инсталляция",
        "раковина",
        "мойка",
        "ванна",
        "душ",
        "душевая",
        "канализация",
        "засор",
        "прочистить трубу",
        "водопровод",
        "батарея",
        "радиатор",
        "отопление",
    ],

    "⚡ Электрика": [
        "электрик",
        "электрика",
        "электричество",
        "розетка",
        "розетки",
        "выключатель",
        "выключатели",
        "проводка",
        "провода",
        "кабель",
        "свет",
        "светильник",
        "люстра",
        "лампа",
        "щиток",
        "электрощит",
        "автомат",
        "автоматы",
        "узо",
        "электромонтаж",
    ],

    "❄️ Кондиционеры / климат": [
        "кондиционер",
        "кондиционера",
        "кондиционеры",
        "сплит",
        "сплит система",
        "сплит-система",
        "климат",
        "вентиляция",
        "вентилятор",
        "вытяжка",
        "установка кондиционера",
        "чистка кондиционера",
        "заправка кондиционера",
    ],

    "🪟 Окна / двери": [
        "окно",
        "окна",
        "окон",
        "оконщик",
        "пластиковые окна",
        "пвх окна",
        "стеклопакет",
        "стекло",
        "разбитое окно",
        "дверь",
        "двери",
        "дверная",
        "замена двери",
        "установка двери",
        "межкомнатная дверь",
        "входная дверь",
        "балконная дверь",
        "балкон",
        "лоджия",
    ],

    "🧹 Клининг": [
        "уборка",
        "уборку",
        "уборщица",
        "уборщик",
        "клининг",
        "клининг компания",
        "клинер",
        "домработница",
        "домработник",
        "генеральная уборка",
        "поддерживающая уборка",
        "убрать квартиру",
        "убрать дом",
        "помыть квартиру",
        "помыть окна",
    ],

    "🔨 Сборка / монтаж": [
        "сборка",
        "собрать",
        "собрать мебель",
        "монтаж",
        "монтажник",
        "установка",
        "установить",
        "повесить",
        "прикрутить",
        "закрепить",
        "смонтировать",
        "мастер на час",
        "муж на час",
        "мужчина на час",
        "рабочий на час",
    ],

    "🎨 Потолки / полы": [
        "потолок",
        "потолки",
        "натяжной потолок",
        "натяжные потолки",
        "потолочник",
        "ламинат",
        "ламината",
        "паркет",
        "паркетная доска",
        "пол",
        "полы",
        "напольное покрытие",
        "укладка ламината",
        "укладка паркета",
        "линолеум",
        "плинтус",
    ],

    "📺 Бытовая техника": [
        "стиральная машина",
        "стиралка",
        "посудомойка",
        "посудомоечная машина",
        "холодильник",
        "морозильник",
        "духовка",
        "духовой шкаф",
        "плита",
        "варочная панель",
        "микроволновка",
        "микроволновая печь",
        "телевизор",
        "телевизор ремонт",
        "мастер по бытовой технике",
        "ремонт техники",
    ],

    "💻 Компьютеры / интернет": [
        "компьютер",
        "ноутбук",
        "ноут",
        "принтер",
        "роутер",
        "вайфай",
        "wi-fi",
        "wifi",
        "интернет",
        "настроить интернет",
        "настроить роутер",
        "компьютерный мастер",
        "мастер по компьютерам",
        "ремонт ноутбука",
        "ремонт компьютера",
    ],

    "🚚 Перевозки": [
        "перевозка",
        "перевезти",
        "перевезти мебель",
        "грузчики",
        "грузчик",
        "машина с грузчиками",
        "газель",
        "грузовое такси",
        "транспорт",
        "доставка",
        "вывоз мебели",
        "вывезти мебель",
        "вывоз мусора",
        "переезд",
        "переезды",
    ],

    "🌿 Дом / участок": [
        "садовник",
        "сад",
        "участок",
        "газон",
        "газона",
        "трава",
        "покос травы",
        "косить траву",
        "деревья",
        "обрезка деревьев",
        "ветки",
        "ландшафт",
        "ландшафтный дизайн",
        "уборка участка",
        "снег",
        "уборка снега",
        "чистка снега",
    ],

    "🔐 Замки / безопасность": [
        "замок",
        "замки",
        "сломался замок",
        "замена замка",
        "открыть дверь",
        "вскрыть дверь",
        "ключи",
        "ключ",
        "изготовление ключей",
        "домофон",
        "видеодомофон",
        "сигнализация",
        "видеонаблюдение",
        "камера наблюдения",
    ],

    "🪟 Жалюзи / шторы": [
        "жалюзи",
        "ролеты",
        "роллеты",
        "рулонные шторы",
        "шторы",
        "карниз",
        "карнизы",
        "римские шторы",
        "шторник",
        "установка жалюзи",
        "установка карниза",
    ],

    "🧼 Спецуборка": [
        "химчистка",
        "химчистку",
        "химчистка дивана",
        "химчистка мебели",
        "чистка дивана",
        "чистка ковра",
        "чистка матраса",
        "чистка после ремонта",
        "уборка после ремонта",
        "мойка после ремонта",
    ],

    "🔧 Разное": [
        "мастер",
        "специалист",
        "рабочий",
        "ремонтник",
        "мастер на час",
        "помощник по дому",
        "мелкий ремонт",
        "домашний мастер",
    ],
}


# ============================================================
# ФРАЗЫ ЗАПРОСА
# ============================================================

REQUEST_PHRASES = [
    "ищу",
    "нужен",
    "нужна",
    "нужно",
    "нужны",
    "требуется",
    "требуется мастер",
    "посоветуйте",
    "порекомендуйте",
    "кто знает",
    "кто может",
    "кто сможет",
    "к кому обратиться",
    "есть контакты",
    "есть номер",
    "подскажите мастера",
    "нужен специалист",
    "нужен человек",
    "можете посоветовать",
    "кто делал",
    "кто устанавливал",
    "кто ремонтировал",
    "кто знает хорошего",
    "ищу мастера",
    "ищу специалиста",
    "ищу человека",
    "дайте контакт",
    "дайте номер",
    "посоветуйте человека",
    "посоветуйте фирму",
    "посоветуйте компанию",
    "порекомендуйте мастера",
    "кто занимается",
    "кто занимается этим",
    "кто сможет помочь",
    "кто поможет",
    "нужна помощь",
    "может кто помочь",
    "может кто подсказать",
]


# ============================================================
# СИЛЬНЫЕ ПАТТЕРНЫ
# ============================================================

STRONG_PATTERNS = [
    r"\bнужен\s+(хороший\s+)?мастер\b",
    r"\bнужна\s+(хорошая\s+)?помощь\b",
    r"\bищу\s+(хорошего\s+)?мастера?\b",
    r"\bищу\s+(хорошего\s+)?специалиста\b",
    r"\bпосоветуйте\s+(хорошего\s+)?мастера?\b",
    r"\bпорекомендуйте\s+(хорошего\s+)?мастера?\b",
    r"\bкто\s+может\s+(сделать|установить|починить|ремонтировать)\b",
    r"\bкто\s+сможет\s+(сделать|установить|починить|ремонтировать)\b",
    r"\bк\s+кому\s+обратиться\b",
    r"\bесть\s+(контакт|контакты|номер|телефон)\b",
    r"\bдайте\s+(контакт|контакты|номер|телефон)\b",
    r"\bподскажите\s+(мастера|специалиста|человека)\b",
    r"\bкто\s+знает\s+(хорошего|нормального)\b",
    r"\bнужен\s+человек\s+на\b",
    r"\bкто\s+занимается\b",
]


# ============================================================
# ИГНОР
# ============================================================

IGNORE_PATTERNS = [
    "продам",
    "продаю",
    "продается",
    "продаётся",
    "продаем",
    "продаём",
    "отдам",
    "отдам даром",
    "бесплатно отдаю",
    "куплю",
    "купить",
    "ищу покупателя",
    "обмен",
]


# ============================================================
# ДОПОЛНИТЕЛЬНЫЕ СИГНАЛЫ
# ============================================================

URGENCY_WORDS = [
    "срочно",
    "сегодня",
    "завтра",
    "прямо сейчас",
    "как можно скорее",
    "очень срочно",
    "нужно быстро",
    "желательно сегодня",
    "желательно завтра",
]

PRICE_WORDS = [
    "сколько стоит",
    "сколько будет стоить",
    "цена",
    "стоимость",
    "бюджет",
    "по цене",
    "расценки",
    "сколько берете",
    "сколько берёте",
]

CONTACT_WORDS = [
    "телефон",
    "номер",
    "контакт",
    "контакты",
    "напишите",
    "в лс",
    "в личку",
    "личные сообщения",
]


# ============================================================
# DATACLASS
# ============================================================

@dataclass
class AnalysisResult:
    is_request: bool
    category: str
    score: int
    hot: bool


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DATABASE_FILE,
    check_same_thread=False
)

db.row_factory = sqlite3.Row


def init_db():
    cursor = db.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            chat_title TEXT,
            message_id INTEGER,
            sender_name TEXT,
            text TEXT,
            category TEXT,
            score INTEGER,
            hot INTEGER DEFAULT 0,
            link TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'new'
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            message_hash TEXT,
            created_at TEXT,
            UNIQUE(chat_id, message_hash)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    db.commit()


def set_setting(key: str, value: str):
    db.execute("""
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
    """, (key, value))

    db.commit()


def get_setting(key: str):
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,)
    ).fetchone()

    if not row:
        return None

    return row["value"]


def delete_setting(key: str):
    db.execute(
        "DELETE FROM settings WHERE key = ?",
        (key,)
    )

    db.commit()


def get_notification_target():
    """
    Сначала берем чат из базы, заданный командой /setchat.
    Если его нет — используем TG_NOTIFICATION_TARGET из .env.
    """

    value = get_setting("notification_target")

    if value:
        try:
            return int(value)
        except ValueError:
            return value

    if ENV_NOTIFICATION_TARGET:
        if re.fullmatch(r"-?\d+", ENV_NOTIFICATION_TARGET):
            return int(ENV_NOTIFICATION_TARGET)

        return ENV_NOTIFICATION_TARGET

    return "me"


def save_request(
    chat_id,
    chat_title,
    message_id,
    sender_name,
    text,
    category,
    score,
    hot,
    link,
):
    now = datetime.now(timezone.utc).isoformat()

    db.execute("""
        INSERT INTO requests (
            chat_id,
            chat_title,
            message_id,
            sender_name,
            text,
            category,
            score,
            hot,
            link,
            created_at,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
    """, (
        chat_id,
        chat_title,
        message_id,
        sender_name,
        text,
        category,
        score,
        int(hot),
        link,
        now,
    ))

    db.commit()

    row = db.execute(
        "SELECT last_insert_rowid() AS id"
    ).fetchone()

    return row["id"]


def is_duplicate(chat_id, text):
    message_hash = hashlib.sha256(
        text.strip().lower().encode("utf-8")
    ).hexdigest()

    row = db.execute("""
        SELECT id
        FROM processed_messages
        WHERE chat_id = ?
          AND message_hash = ?
          AND created_at >= ?
        LIMIT 1
    """, (
        chat_id,
        message_hash,
        (
            datetime.now(timezone.utc)
            - timedelta(seconds=DEDUP_SECONDS)
        ).isoformat(),
    )).fetchone()

    if row:
        return True

    try:
        db.execute("""
            INSERT INTO processed_messages (
                chat_id,
                message_hash,
                created_at
            )
            VALUES (?, ?, ?)
        """, (
            chat_id,
            message_hash,
            datetime.now(timezone.utc).isoformat(),
        ))

        db.commit()

    except sqlite3.IntegrityError:
        return True

    return False


# ============================================================
# НОРМАЛИЗАЦИЯ
# ============================================================

def normalize(text: str) -> str:
    text = text.lower()
    text = text.replace("ё", "е")

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text


# ============================================================
# АНАЛИЗ СООБЩЕНИЯ
# ============================================================

def analyze_message(text: str) -> AnalysisResult:
    normalized = normalize(text)

    # Сначала проверяем очевидный мусор
    for phrase in IGNORE_PATTERNS:
        if phrase in normalized:
            return AnalysisResult(
                is_request=False,
                category="",
                score=0,
                hot=False,
            )

    found_categories = []

    for category, keywords in CATEGORIES.items():
        for keyword in keywords:
            if keyword in normalized:
                found_categories.append(category)
                break

    if not found_categories:
        return AnalysisResult(
            is_request=False,
            category="",
            score=0,
            hot=False,
        )

    score = 3

    # Фразы запроса
    request_phrase_found = False

    for phrase in REQUEST_PHRASES:
        if phrase in normalized:
            request_phrase_found = True
            score += 5
            break

    # Сильные конструкции
    strong_match = False

    for pattern in STRONG_PATTERNS:
        if re.search(pattern, normalized):
            strong_match = True
            score += 3
            break

    # Срочность
    for word in URGENCY_WORDS:
        if word in normalized:
            score += 2
            break

    # Цена
    for word in PRICE_WORDS:
        if word in normalized:
            score += 1
            break

    # Контакты
    for word in CONTACT_WORDS:
        if word in normalized:
            score += 1
            break

    # Несколько услуг сразу
    if len(found_categories) >= 2:
        score += 1

    # Условие заявки
    is_request = (
        score >= MIN_SCORE
        and (
            request_phrase_found
            or strong_match
        )
    )

    hot = (
        is_request
        and score >= HOT_SCORE
    )

    if found_categories:
        if len(found_categories) == 1:
            category = found_categories[0]
        else:
            category = " + ".join(found_categories[:3])
    else:
        category = "🔧 Разное"

    return AnalysisResult(
        is_request=is_request,
        category=category,
        score=score,
        hot=hot,
    )


# ============================================================
# ПОЛУЧЕНИЕ НАЗВАНИЯ ЧАТА
# ============================================================

async def get_chat_title(event):
    try:
        chat = await event.get_chat()

        title = getattr(chat, "title", None)

        if title:
            return title

        first_name = getattr(chat, "first_name", None)
        last_name = getattr(chat, "last_name", None)

        name = " ".join(
            x for x in [first_name, last_name]
            if x
        )

        if name:
            return name

    except Exception:
        pass

    return "Неизвестный чат"


# ============================================================
# ИМЯ ОТПРАВИТЕЛЯ
# ============================================================

async def get_sender_name(event):
    try:
        sender = await event.get_sender()

        if not sender:
            return "Неизвестный пользователь"

        first_name = getattr(
            sender,
            "first_name",
            ""
        ) or ""

        last_name = getattr(
            sender,
            "last_name",
            ""
        ) or ""

        username = getattr(
            sender,
            "username",
            None
        )

        name = " ".join(
            x for x in [first_name, last_name]
            if x
        ).strip()

        if username:
            if name:
                return f"{name} (@{username})"

            return f"@{username}"

        return name or "Неизвестный пользователь"

    except Exception:
        return "Неизвестный пользователь"


# ============================================================
# ССЫЛКА НА СООБЩЕНИЕ
# ============================================================

async def get_message_link(event):
    try:
        return await event.message.get_link()
    except Exception:
        pass

    try:
        chat = await event.get_chat()

        username = getattr(
            chat,
            "username",
            None
        )

        if username:
            return (
                f"https://t.me/{username}/"
                f"{event.message.id}"
            )

    except Exception:
        pass

    return None


# ============================================================
# ФОРМАТ УВЕДОМЛЕНИЯ
# ============================================================

def build_notification(
    request_id,
    chat_title,
    sender_name,
    text,
    category,
    score,
    hot,
):
    if len(text) > MAX_MESSAGE_LENGTH:
        text = text[:MAX_MESSAGE_LENGTH] + "..."

    hot_text = "🔥 ГОРЯЧАЯ ЗАЯВКА" if hot else "📩 Новая заявка"

    return (
        f"{hot_text}\n\n"
        f"📂 Категория: {category}\n"
        f"⭐ Балл: {score}\n"
        f"💬 Чат: {chat_title}\n"
        f"👤 Автор: {sender_name}\n"
        f"🆔 Заявка: #{request_id}\n\n"
        f"📝 Сообщение:\n"
        f"{text}"
    )


# ============================================================
# ОТПРАВКА УВЕДОМЛЕНИЯ
# ============================================================

async def send_notification(
    request_id,
    chat_title,
    sender_name,
    text,
    category,
    score,
    hot,
    link,
):
    target = get_notification_target()

    message = build_notification(
        request_id=request_id,
        chat_title=chat_title,
        sender_name=sender_name,
        text=text,
        category=category,
        score=score,
        hot=hot,
    )

    buttons = []

    first_row = []

    if link:
        first_row.append(
            Button.url(
                "🔗 Открыть",
                link
            )
        )

    if first_row:
        buttons.append(first_row)

    buttons.append([
        Button.inline(
            "👍 Интересно",
            data=f"interesting:{request_id}"
        ),
        Button.inline(
            "❌ Игнор",
            data=f"ignored:{request_id}"
        ),
    ])

    try:
        await client.send_message(
            target,
            message,
            buttons=buttons,
            link_preview=False,
        )

        logger.info(
            "Уведомление отправлено. target=%s request_id=%s",
            target,
            request_id,
        )

    except FloodWaitError as e:
        logger.warning(
            "FloodWait: ждем %s секунд",
            e.seconds,
        )

        await asyncio.sleep(e.seconds)

        await client.send_message(
            target,
            message,
            buttons=buttons,
            link_preview=False,
        )

    except Exception as e:
        logger.exception(
            "Ошибка отправки уведомления: %s",
            e,
        )


# ============================================================
# ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ============================================================

@client.on(events.NewMessage)
async def message_handler(event):

    try:

        # Не анализируем собственные сообщения
        if IGNORE_OUTGOING and event.out:
            return

        # Только группы
        if ONLY_GROUPS:
            if not event.is_group:
                return

        text = event.raw_text

        if not text:
            return

        text = text.strip()

        if len(text) < 5:
            return

        # Анализ
        result = analyze_message(text)

        if not result.is_request:
            return

        # ID чата
        chat_id = event.chat_id

        # Дедупликация
        if is_duplicate(chat_id, text):
            logger.info(
                "Дубликат пропущен: chat=%s",
                chat_id,
            )
            return

        # Информация
        chat_title = await get_chat_title(event)

        sender_name = await get_sender_name(event)

        link = await get_message_link(event)

        # Сохраняем
        request_id = save_request(
            chat_id=chat_id,
            chat_title=chat_title,
            message_id=event.message.id,
            sender_name=sender_name,
            text=text,
            category=result.category,
            score=result.score,
            hot=result.hot,
            link=link,
        )

        logger.info(
            "Найдена заявка #%s | %s | score=%s",
            request_id,
            result.category,
            result.score,
        )

        # Отправляем уведомление
        await send_notification(
            request_id=request_id,
            chat_title=chat_title,
            sender_name=sender_name,
            text=text,
            category=result.category,
            score=result.score,
            hot=result.hot,
            link=link,
        )

    except Exception as e:
        logger.exception(
            "Ошибка обработки сообщения: %s",
            e,
        )


# ============================================================
# /setchat
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/setchat$"
    )
)
async def setchat_handler(event):

    if not event.out:
        return

    try:
        chat = await event.get_chat()

        chat_id = event.chat_id

        title = getattr(
            chat,
            "title",
            None
        )

        if not title:
            first_name = getattr(
                chat,
                "first_name",
                None
            )

            last_name = getattr(
                chat,
                "last_name",
                None
            )

            title = " ".join(
                x for x in [first_name, last_name]
                if x
            )

        if not title:
            title = "Этот чат"

        # Записываем ID в базу
        set_setting(
            "notification_target",
            str(chat_id)
        )

        await client.send_message(
            event.chat_id,
            (
                "✅ Готово!\n\n"
                f"Этот чат назначен для уведомлений.\n"
                f"Название: {title}\n"
                f"ID: {chat_id}\n\n"
                "Теперь найденные заявки будут "
                "приходить сюда."
            )
        )

        logger.info(
            "Новый чат уведомлений: %s (%s)",
            title,
            chat_id,
        )

    except Exception as e:
        logger.exception(
            "Ошибка /setchat: %s",
            e,
        )


# ============================================================
# /unsetchat
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/unsetchat$"
    )
)
async def unsetchat_handler(event):

    if not event.out:
        return

    delete_setting(
        "notification_target"
    )

    await client.send_message(
        event.chat_id,
        (
            "✅ Настройка удалена.\n\n"
            "Теперь уведомления снова будут "
            "отправляться в чат из TG_NOTIFICATION_TARGET "
            "в .env."
        )
    )


# ============================================================
# /target
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/target$"
    )
)
async def target_handler(event):

    if not event.out:
        return

    target = get_notification_target()

    db_target = get_setting(
        "notification_target"
    )

    if db_target:
        source = "назначен через /setchat"
    else:
        source = "взят из .env"

    await client.send_message(
        event.chat_id,
        (
            "🎯 Текущий чат уведомлений\n\n"
            f"ID: {target}\n"
            f"Источник: {source}"
        )
    )


# ============================================================
# /stats
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/stats$"
    )
)
async def stats_handler(event):

    if not event.out:
        return

    since = (
        datetime.now(timezone.utc)
        - timedelta(hours=24)
    ).isoformat()

    total = db.execute("""
        SELECT COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
    """, (since,)).fetchone()["count"]

    hot = db.execute("""
        SELECT COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
          AND hot = 1
    """, (since,)).fetchone()["count"]

    interesting = db.execute("""
        SELECT COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
          AND status = 'interesting'
    """, (since,)).fetchone()["count"]

    ignored = db.execute("""
        SELECT COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
          AND status = 'ignored'
    """, (since,)).fetchone()["count"]

    rows = db.execute("""
        SELECT category, COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
        GROUP BY category
        ORDER BY count DESC
        LIMIT 10
    """, (since,)).fetchall()

    lines = [
        "📊 Статистика за последние 24 часа",
        "",
        f"📩 Всего заявок: {total}",
        f"🔥 Горячих: {hot}",
        f"👍 Интересно: {interesting}",
        f"❌ Игнор: {ignored}",
    ]

    if rows:
        lines.append("")
        lines.append("📂 По категориям:")

        for row in rows:
            lines.append(
                f"• {row['category']}: {row['count']}"
            )

    await client.send_message(
        event.chat_id,
        "\n".join(lines)
    )


# ============================================================
# /requests
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/requests$"
    )
)
async def requests_handler(event):

    if not event.out:
        return

    rows = db.execute("""
        SELECT
            id,
            chat_title,
            category,
            score,
            hot,
            text
        FROM requests
        WHERE status = 'new'
        ORDER BY id DESC
        LIMIT 10
    """).fetchall()

    if not rows:
        await client.send_message(
            event.chat_id,
            "📭 Новых заявок нет."
        )

        return

    lines = [
        "📋 Последние 10 новых заявок:",
        ""
    ]

    for row in rows:
        text = row["text"].replace(
            "\n",
            " "
        )

        if len(text) > 120:
            text = text[:120] + "..."

        hot = "🔥 " if row["hot"] else ""

        lines.append(
            f"{hot}#{row['id']} | "
            f"{row['category']} | "
            f"{row['score']} баллов"
        )

        lines.append(
            f"💬 {row['chat_title']}"
        )

        lines.append(
            f"📝 {text}"
        )

        lines.append("")

    await client.send_message(
        event.chat_id,
        "\n".join(lines)
    )


# ============================================================
# /help
# ============================================================

@client.on(
    events.NewMessage(
        pattern=r"^/help$"
    )
)
async def help_handler(event):

    if not event.out:
        return

    text = """
🤖 TELEGRAM MONITOR

Команды:

/setchat
Назначить текущий чат для уведомлений.

/target
Показать текущий чат уведомлений.

/unsetchat
Удалить назначенный чат.

/stats
Статистика за последние 24 часа.

/requests
Последние найденные заявки.

/help
Показать эту справку.

Как настроить чат уведомлений:

1. Открой нужный закрытый чат.
2. Отправь туда:
/setchat
3. Бот запомнит этот чат.
4. После этого все найденные заявки будут приходить туда.

Чтобы изменить чат:
открой новый чат и снова отправь:
/setchat
"""

    await client.send_message(
        event.chat_id,
        text
    )


# ============================================================
# КНОПКИ
# ============================================================

@client.on(
    events.CallbackQuery(
        pattern=rb"^(interesting|ignored):(\d+)$"
    )
)
async def callback_handler(event):

    try:
        data = event.data.decode(
            "utf-8"
        )

        action, request_id = data.split(
            ":",
            1
        )

        request_id = int(request_id)

        if action == "interesting":
            status = "interesting"
            text = "👍 Отмечено как интересное."

        else:
            status = "ignored"
            text = "❌ Заявка помечена как игнор."

        db.execute("""
            UPDATE requests
            SET status = ?
            WHERE id = ?
        """, (
            status,
            request_id,
        ))

        db.commit()

        await event.answer(
            text,
            alert=False
        )

        logger.info(
            "Заявка #%s -> %s",
            request_id,
            status,
        )

    except Exception as e:
        logger.exception(
            "Ошибка callback: %s",
            e,
        )

        await event.answer(
            "Ошибка",
            alert=True
        )


# ============================================================
# ЕЖЕДНЕВНАЯ СВОДКА
# ============================================================

async def daily_summary_loop():

    while True:

        try:
            now = datetime.now()

            target = now.replace(
                hour=DAILY_SUMMARY_HOUR,
                minute=DAILY_SUMMARY_MINUTE,
                second=0,
                microsecond=0,
            )

            if target <= now:
                target += timedelta(
                    days=1
                )

            seconds = (
                target - now
            ).total_seconds()

            logger.info(
                "Следующая сводка через %.0f секунд",
                seconds,
            )

            await asyncio.sleep(
                seconds
            )

            await send_daily_summary()

        except asyncio.CancelledError:
            return

        except Exception as e:
            logger.exception(
                "Ошибка ежедневной сводки: %s",
                e,
            )

            await asyncio.sleep(
                60
            )


async def send_daily_summary():

    since = (
        datetime.now(timezone.utc)
        - timedelta(days=1)
    ).isoformat()

    total = db.execute("""
        SELECT COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
    """, (since,)).fetchone()["count"]

    hot = db.execute("""
        SELECT COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
          AND hot = 1
    """, (since,)).fetchone()["count"]

    interesting = db.execute("""
        SELECT COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
          AND status = 'interesting'
    """, (since,)).fetchone()["count"]

    rows = db.execute("""
        SELECT category, COUNT(*) AS count
        FROM requests
        WHERE created_at >= ?
        GROUP BY category
        ORDER BY count DESC
        LIMIT 10
    """, (since,)).fetchall()

    lines = [
        "📊 ЕЖЕДНЕВНАЯ СВОДКА",
        "",
        f"📩 Заявок: {total}",
        f"🔥 Горячих: {hot}",
        f"👍 Интересных: {interesting}",
    ]

    if rows:
        lines.append("")
        lines.append("📂 Категории:")

        for row in rows:
            lines.append(
                f"• {row['category']}: {row['count']}"
            )

    await client.send_message(
        get_notification_target(),
        "\n".join(lines)
    )


# ============================================================
# ЗАПУСК
# ============================================================

async def main():

    init_db()

    logger.info(
        "База данных готова: %s",
        DATABASE_FILE,
    )

    target = get_notification_target()

    logger.info(
        "Текущий чат уведомлений: %s",
        target,
    )

    # На сервере НЕ вызываем интерактивный client.start():
    # Bothost не сможет спросить номер/код/пароль.
    await client.connect()

    if not await client.is_user_authorized():
        raise RuntimeError(
            "Telegram-сессия не авторизована. "
            "Проверь SESSION_STRING: сгенерируй новый String Session локально "
            "и заново добавь его в переменные Bothost."
        )

    me = await client.get_me()

    logger.info(
        "Авторизован как: %s (id=%s)",
        getattr(me, "username", None)
        or getattr(me, "first_name", None)
        or me.id,
        me.id,
    )

    # Проверяем, что Telegram действительно видит диалоги аккаунта.
    dialogs_count = 0
    async for _dialog in client.iter_dialogs(limit=1):
        dialogs_count += 1

    logger.info(
        "Проверка диалогов: %s",
        "OK" if dialogs_count else "диалоги не найдены"
    )

    print()
    print("=" * 60)
    print("TELEGRAM MONITOR ЗАПУЩЕН")
    print("=" * 60)
    print()
    print(f"Чат уведомлений: {target}")
    print()
    print("Команды:")
    print("/setchat  - назначить текущий чат")
    print("/target   - показать чат уведомлений")
    print("/unsetchat - убрать назначение")
    print("/stats    - статистика")
    print("/requests - последние заявки")
    print("/help     - помощь")
    print()
    print("Мониторинг новых сообщений в группах запущен.")
    print("=" * 60)
    print()

    summary_task = asyncio.create_task(
        daily_summary_loop()
    )

    try:
        await client.run_until_disconnected()

    finally:
        summary_task.cancel()

        try:
            await summary_task
        except asyncio.CancelledError:
            pass


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(
            main()
        )

    except KeyboardInterrupt:
        print()
        print("Мониторинг остановлен.")