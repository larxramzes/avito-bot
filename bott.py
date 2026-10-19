import asyncio
import html as html_lib
import logging
import os
import random
import re
import sqlite3
import sys
import time
import threading
from dataclasses import dataclass
from datetime import datetime

from aiohttp import web
from curl_cffi.requests import AsyncSession
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8968394996:AAEpGiADwUoqHZ1KqCA1MllKhFOXOF6OA10"
DB_PATH = "safe_items.db"
PORT = int(os.environ.get("PORT", 10000))

DEFAULT_CHAT_ID = 1128246797

PROXY_LIST = [f"http://W3KAbVZ7rM:ruTTPneIyG@proxy.proxyma.io:{p}" for p in range(10000, 10100)]
banned_proxies = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
    force=True,
)

@dataclass
class ScannerState:
    active_chat_id: int | None = DEFAULT_CHAT_ID
    is_scanning: bool = True
    stats_checked: int = 0
    stats_found: int = 0
    current_ip: str = "Определяется..."
    target_idx: int = 0
    last_error: str = "Нет ошибок"
    last_activity_time: float = time.time()

state = ScannerState()

# ================= БАЗА =================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_ads (ad_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def is_seen(ad_id: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_ads WHERE ad_id = ?", (ad_id,))
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

def mark_seen(ad_id: str):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("INSERT OR IGNORE INTO seen_ads (ad_id) VALUES (?)", (ad_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

# ================= КАТЕГОРИИ =================
TARGETS = [
    ("Телефоны", "https://m.avito.ru/kazan/telefony?s=104"),
    ("Приставки и игры", "https://m.avito.ru/kazan/igry_pristavki_i_programmy?s=104"),
    ("Ноутбуки", "https://m.avito.ru/kazan/noutbuki?s=104"),
    ("Планшеты", "https://m.avito.ru/kazan/planshety_i_elektronnye_knigi?s=104"),
    ("Аудио и видео (ТВ / Колонки)", "https://m.avito.ru/kazan/audio_i_video?s=104"),
    ("Бытовая техника (Пылесосы / Dyson)", "https://m.avito.ru/kazan/bytovaya_tehnika?s=104"),
    ("Товары для ПК (Видеокарты / Мониторы)", "https://m.avito.ru/kazan/tovary_dlya_kompyutera?s=104"),
]

# ... (Сюда вставьте ваш огромный список PRICE_RULES, OTHER_CITIES, TRASH_WORDS, SCAM_WORDS из предыдущего кода) ...
# Для экономии места я их пропущу, но в вашем файле они должны быть на месте!
# Просто скопируйте их из предыдущего сообщения.

PRICE_RULES = [] # ВСТАВЬТЕ СЮДА ВАШИ ПРАВИЛА
OTHER_CITIES = [] # ВСТАВЬТЕ СЮДА
TRASH_WORDS = [] # ВСТАВЬТЕ СЮДА
SCAM_WORDS = [] # ВСТАВЬТЕ СЮДА


def normalize_html(raw_html: str) -> str:
    text = html_lib.unescape(raw_html)
    text = (
        text.replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("%2F", "/")
        .replace("%2f", "/")
        .replace("\\/", "/")
        .replace('\\"', '"')
    )
    for marker in ("itemsCarousel", "Доставка из других", "Другие города"):
        idx = text.find(marker)
        if idx != -1 and idx > 15000:
            text = text[:idx]
    return text

def extract_links_from_html(clean_html: str) -> list:
    links = []
    pattern = re.compile(r"/kazan/([a-z0-9_-]+)/([a-z0-9_-]+?_(\d{8,12}))(?![0-9])", re.IGNORECASE)
    for m in pattern.finditer(clean_html):
        cat_path = m.group(1).lower()
        raw_slug = m.group(2)
        ad_id = m.group(3)
        if cat_path in ("prof", "favorites", "login", "search", "user"):
            continue
        links.append((cat_path, raw_slug, ad_id, m.start()))

    seen_ids = set()
    unique_links = []
    for cat_path, raw_slug, ad_id, pos in links:
        if ad_id not in seen_ids:
            seen_ids.add(ad_id)
            unique_links.append((cat_path, raw_slug, ad_id, pos))
    return unique_links

def extract_card_details(raw_window: str, slug: str) -> dict:
    img_match = re.search(r'(https?://\d+\.img\.avito\.st/image/1/[A-Za-z0-9_\-\./]+)', raw_window)
    image_url = img_match.group(1) if img_match else None

    title_match = re.search(r'"title"\s*:\s*"([^"]{4,90})"', raw_window)
    if not title_match:
        title_match = re.search(r'title="([^"]{4,90})"', raw_window)
    if title_match:
        title = html_lib.unescape(title_match.group(1)).strip()
    else:
        title = re.sub(r'_\d{8,12}$', '', slug).replace('_', ' ').title()

    desc_match = re.search(r'"description"\s*:\s*"([^"]{10,450})"', raw_window)
    if not desc_match:
        desc_match = re.search(r'<meta[^>]+itemprop="description"[^>]+content="([^"]{10,450})"', raw_window)
    desc = html_lib.unescape(desc_match.group(1)).replace("\\n", " ").strip() if desc_match else "Свежее объявление в Казани."
    return {"title": title, "image_url": image_url, "description": desc[:300]}

async def fetch_smart(url: str):
    now = time.time()
    
    if len(banned_proxies) > 500:
        for k in list(banned_proxies.keys()):
            if banned_proxies[k] < now:
                del banned_proxies[k]

    available = [p for p in PROXY_LIST if p not in banned_proxies or banned_proxies[p] < now]

    if not available:
        banned_proxies.clear()
        available = PROXY_LIST.copy()

    random.shuffle(available)
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
    }

    for proxy in available[:12]:
        if not state.is_scanning:
            break
        port_str = proxy.split(':')[-1]
        try:
            async def _do_request():
                async with AsyncSession(impersonate="chrome", proxies={"http": proxy, "https": proxy}) as session:
                    return await session.get(url, headers=headers, timeout=4.0)

            resp = await asyncio.wait_for(_do_request(), timeout=6.0)

            if resp.status_code == 200 and len(resp.text) > 15000:
                clean_html = normalize_html(resp.text)
                unique_links = extract_links_from_html(clean_html)

                if not unique_links:
                    banned_proxies[proxy] = now + 600
                    continue

                state.current_ip = f"Порт {port_str}"
                state.last_activity_time = time.time()
                logging.info(f"📄 Успех на порту {port_str}! Найдено карточек Казани: {len(unique_links)}")
                return clean_html, unique_links
            else:
                banned_proxies[proxy] = now + 300
        except asyncio.TimeoutError:
            banned_proxies[proxy] = now + 300
            logging.warning(f"⏰ Прокси {port_str} завис (Таймаут 6с), бан на 5 мин.")
        except Exception:
            banned_proxies[proxy] = now + 300

    return None, []

async def send_deal_card(bot: Bot, chat_id: int, details: dict, price_val: int, matched_rule: dict, full_url: str) -> bool:
    profit = matched_rule["market"] - price_val
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    caption = (
        f"<b>{html_lib.escape(details['title'])}</b>\n"
        f"💵 <b>{price_val:,} ₽</b>  <i>(Рынок: ~{matched_rule['market']:,} ₽ | 🔥 +{profit:,} ₽)</i>\n"
        f"🌍 Казань\n\n"
        f"<blockquote>{html_lib.escape(details['description'])}</blockquote>\n\n"
        f"🎯 Категория: {matched_rule['name']}\n"
        f"🔗 <a href=\"{full_url}\">Прямая ссылка на Авито</a>\n\n"
        f"📅 {now_str}"
    ).replace(",", " ")

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть в Казани", url=full_url)]])

    try:
        if details.get("image_url"):
            await bot.send_photo(chat_id=chat_id, photo=details["image_url"], caption=caption, parse_mode="HTML", reply_markup=kb)
        else:
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML", reply_markup=kb)
        logging.info(f"🚀 ОТПРАВЛЕНО В ЧАТ: {details['title']} за {price_val} ₽!")
        return True
    except Exception as e:
        logging.warning(f"⚠️ Ошибка отправки фото, шлю текстом: {e}")
        try:
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=False)
            logging.info(f"🚀 ОТПРАВЛЕНО В ЧАТ (ТЕКСТОМ): {details['title']} за {price_val} ₽!")
            return True
        except Exception as e2:
            state.last_error = str(e2)[:120]
            logging.error(f"❌ Критическая ошибка отправки в ТГ: {e2}")
            return False

async def parse_electronics(bot: Bot):
    cat_name, url = TARGETS[state.target_idx % len(TARGETS)]
    state.target_idx += 1

    logging.info(f"🕵️ Сканируем категорию: {cat_name}...")
    
    try:
        clean_html, unique_links = await asyncio.wait_for(fetch_smart(url), timeout=20.0)
    except asyncio.TimeoutError:
        logging.warning(f"⏰ Таймаут сканирования '{cat_name}', идем дальше...")
        return
    except Exception as e:
        state.last_error = f"Fetch error: {e}"[:120]
        return

    if not clean_html or not unique_links:
        logging.warning(f"❌ Не удалось пробить категорию '{cat_name}' в этом проходе, переключаемся...")
        return

    new_checked = 0
    matched_in_pass = 0

    for cat_path, raw_slug, ad_id, pos in unique_links:
        slug = re.sub(r"^(2f|_)+", "", raw_slug, flags=re.IGNORECASE)

        if is_seen(ad_id):
            continue

        state.stats_checked += 1
        new_checked += 1

        narrow = clean_html[max(0, pos - 350) : pos + 550]
        combined = f"{slug.lower()} {narrow.lower()}"

        if any(c in combined for c in OTHER_CITIES) or any(t in combined for t in TRASH_WORDS) or any(s in combined for s in SCAM_WORDS):
            mark_seen(ad_id)
            continue

        price_val = None
        for pp in [
            r'"priceDetailed"\s*:\s*\{\s*"value"\s*:\s*(\d{3,7})',
            r'itemprop="price"[^>]*content="(\d{3,7})"',
            r'"price"\s*:\s*(\d{3,7})',
            r'"value"\s*:\s*(\d{3,7})\s*,\s*"valueOld"',
            r'(\d{1,3}(?:[\s\xa0\u202f]\d{3})+)\s*₽',
        ]:
            pm = re.search(pp, narrow)
            if pm:
                digits = re.sub(r"\D", "", pm.group(1))
                if digits:
                    val = int(digits)
                    if 1000 <= val <= 350000:
                        price_val = val
                        break

        if not price_val:
            continue

        matched_rule = next((r for r in PRICE_RULES if any(k in combined for k in r["keys"])), None)
        if not matched_rule or price_val < (matched_rule["max_buy"] * 0.3) or price_val < 1200:
            mark_seen(ad_id)
            continue

        if price_val <= matched_rule["max_buy"]:
            mark_seen(ad_id)
            details = extract_card_details(narrow, slug)
            full_url = f"https://www.avito.ru/kazan/{cat_path}/{slug}"

            if state.active_chat_id:
                sent_ok = await send_deal_card(bot, state.active_chat_id, details, price_val, matched_rule, full_url)
                if sent_ok:
                    state.stats_found += 1
                    matched_in_pass += 1

    logging.info(f"📊 Итог '{cat_name}': новых проверено {new_checked} | отправлено в чат: {matched_in_pass} (Всего проверено: {state.stats_checked})")

# ================= ФОНОВЫЙ ПРОЦЕСС =================
async def background_scanner(bot: Bot):
    while True:
        try:
            if not state.is_scanning or not state.active_chat_id:
                await asyncio.sleep(1)
                continue

            state.last_activity_time = time.time()
            
            await asyncio.wait_for(parse_electronics(bot), timeout=45.0)

            pause_sec = random.randint(20, 38)
            logging.info(f"⏳ Пауза {pause_sec} сек перед следующей категорией...")
            
            for _ in range(pause_sec):
                if not state.is_scanning or not state.active_chat_id:
                    break
                await asyncio.sleep(1)
                state.last_activity_time = time.time()
                
        except asyncio.TimeoutError:
            state.last_error = "Глобальный таймаут цикла"
            logging.warning("⏰ Цикл превысил 45 секунд, принудительно прерван.")
            await asyncio.sleep(5)
        except Exception as e:
            state.last_error = str(e)[:120]
            logging.error(f"⚠️ Ошибка в фоновом сканере: {e}")
            await asyncio.sleep(10)

# ================= 🛡️ СТОРОЖЕВОЙ ТАЙМЕР =================
def watchdog_worker():
    """
    Этот таймер работает в отдельном потоке (не в asyncio), 
    поэтому он сработает, даже если Event Loop полностью завис из-за curl_cffi.
    """
    while True:
        time.sleep(30) # Проверка каждые 30 секунд
        if not state.is_scanning:
            continue
            
        if time.time() - state.last_activity_time > 180: # Если нет активности 3 минуты
            logging.critical("🚨 ВНИМАНИЕ: Асинхронный цикл завис! Принудительный перезапуск контейнера...")
            # os._exit(1) немедленно убивает процесс. Render автоматически перезапустит его.
            os._exit(1) 

# ================= ХЕНДЛЕРЫ =================
dp = Dispatcher()

def get_main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="▶️ Запустить сканер"), KeyboardButton(text="⏹ Остановить сканер")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🧪 Тест отправки")],
        ],
        resize_keyboard=True,
    )

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    state.active_chat_id = message.chat.id
    state.is_scanning = True
    state.last_activity_time = time.time()
    await message.answer(
        f"🕵️ <b>Ультра-Снайпер 2026 запущен (Потоковая защита активна)!</b>\n"
        f"Чат привязан: <code>{message.chat.id}</code> ✨",
        parse_mode="HTML",
        reply_markup=get_main_kb(),
    )

@dp.message(F.text.contains("Запустить"))
async def start_scan(message: types.Message):
    state.active_chat_id = message.chat.id
    state.is_scanning = True
    state.last_activity_time = time.time()
    await message.answer(
        f"🕵️ <b>Сканирование активно!</b> Сторожевой таймер следит за процессом ✨",
        parse_mode="HTML",
        reply_markup=get_main_kb(),
    )

@dp.message(F.text.contains("Остановить"))
async def stop_scan(message: types.Message):
    state.is_scanning = False
    await message.answer("⏸ <b>Сканер остановлен.</b> 🌸", parse_mode="HTML", reply_markup=get_main_kb())

@dp.message(F.text.contains("Тест"))
async def test_send(message: types.Message):
    state.active_chat_id = message.chat.id
    test_details = {
        "title": "Тестовый iPhone 16 Pro 256 ГБ (Проверка связи)",
        "image_url": None,
        "description": "Автоперезапуск и сторожевой таймер работают идеально!",
    }
    test_rule = {"name": "iPhone 16 Pro / Max", "max_buy": 62000, "market": 79000}
    await send_deal_card(bot=message.bot, chat_id=message.chat.id, details=test_details, price_val=55000, matched_rule=test_rule, full_url="https://www.avito.ru/kazan/telefony")

@dp.message(F.text.contains("Статистика"))
async def show_stats(message: types.Message):
    status = "🟢 Работает (Защита ON)" if state.is_scanning else "🔴 На паузе"
    now = time.time()
    active_count = len([p for p in PROXY_LIST if p not in banned_proxies or banned_proxies[p] < now])

    text = (
        f"📊 <b>Статистика Снайпера 2026:</b>\n\n"
        f"Статус: <b>{status}</b>\n"
        f"💬 Чат отправки: <code>{state.active_chat_id}</code>\n"
        f"🌐 <b>Последний порт:</b> <code>{state.current_ip}</code>\n"
        f"🟢 Активных прокси: <b>{active_count} / {len(PROXY_LIST)}</b>\n"
        f"🔍 Проверено новых лотов: <b>{state.stats_checked}</b>\n"
        f"🔥 Отправлено выгодных: <b>{state.stats_found}</b>\n"
        f"🛠 Последняя ошибка: <code>{html_lib.escape(state.last_error)}</code>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_kb())

# ================= ВЕБ-СЕРВЕР =================
async def handle_ping(request):
    return web.Response(text="Bot is running 24/7")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"🌐 Веб-сервер запущен на порту {PORT}")

# ================= ЗАПУСК =================
async def on_startup(bot: Bot):
    init_db()
    
    # Запускаем сторожевой таймер в ОТДЕЛЬНОМ ПОТОКЕ
    threading.Thread(target=watchdog_worker, daemon=True).start()
    
    asyncio.create_task(background_scanner(bot))
    logging.info("🛡️ Снайпер 2026 запущен с потоковой защитой!")

async def main():
    session = AiohttpSession(timeout=60)
    bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))

    await web_server()
    await on_startup(bot)

    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            logging.error(f"⚠️ Ошибка связи с Telegram: {e}. Переподключение через 5 секунд...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
