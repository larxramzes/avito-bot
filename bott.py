import asyncio
import html as html_lib
import logging
import random
import re
import sqlite3
import time
from datetime import datetime

from curl_cffi.requests import AsyncSession
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8968394996:AAEpGiADwUoqHZ1KqCA1MllKhFOXOF6OA10"

# 🔥 НОВЫЙ ПУЛ ИЗ 100 ПРОКСИ
PROXY_LIST = [f"http://W3KAbVZ7rM:ruTTPneIyG@proxy.proxyma.io:{p}" for p in range(10000, 10100)]

banned_proxies = {}
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ================= БАЗА =================
def init_db():
    conn = sqlite3.connect("safe_items.db")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_ads (ad_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

def is_seen(ad_id: str) -> bool:
    try:
        conn = sqlite3.connect("safe_items.db")
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_ads WHERE ad_id = ?", (ad_id,))
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception:
        init_db()
        return False

def mark_seen(ad_id: str):
    try:
        conn = sqlite3.connect("safe_items.db")
        conn.execute("INSERT OR IGNORE INTO seen_ads (ad_id) VALUES (?)", (ad_id,))
        conn.commit()
        conn.close()
    except Exception:
        init_db()

# ================= СОСТОЯНИЕ =================
class ScannerState:
    is_scanning: bool = False
    active_chat_id: int = None
    stats_checked: int = 0
    stats_found: int = 0
    current_ip: str = "Определяется..."

state = ScannerState()

# 🔥 МОБИЛЬНАЯ ВЕРСИЯ
ELECTRONICS_URL = "https://m.avito.ru/kazan/elektronika"

PRICE_RULES = [
    {"name": "Геймпад DualSense", "keys": ["dualsense", "дуалсенс", "геймпад"], "max_buy": 3500, "market": 5500},
    {"name": "iPhone 16 Pro / Max", "keys": ["16_pro", "16 pro", "16pro", "16_promax"], "max_buy": 75000, "market": 95000},
    {"name": "iPhone 16", "keys": ["iphone_16", "iphone 16", "айфон 16"], "max_buy": 52000, "market": 68000},
    {"name": "iPhone 15 Pro / Max", "keys": ["15_pro", "15 pro", "15pro", "15_promax"], "max_buy": 58000, "market": 76000},
    {"name": "iPhone 15", "keys": ["iphone_15", "iphone 15", "айфон 15"], "max_buy": 42000, "market": 55000},
    {"name": "iPhone 14 Pro / Max", "keys": ["14_pro", "14 pro", "14pro", "14_promax"], "max_buy": 45000, "market": 60000},
    {"name": "iPhone 14", "keys": ["iphone_14", "iphone 14", "айфон 14"], "max_buy": 32000, "market": 43000},
    {"name": "iPhone 13 Pro / Max", "keys": ["13_pro", "13 pro", "13pro", "13_promax"], "max_buy": 35000, "market": 48000},
    {"name": "iPhone 13", "keys": ["iphone_13", "iphone 13", "айфон 13"], "max_buy": 25000, "market": 34000},
    {"name": "iPad Pro / Air", "keys": ["ipad_pro", "ipad_air", "ipad pro", "ipad air"], "max_buy": 25000, "market": 42000},
    {"name": "Игровой ноутбук RTX", "keys": ["rtx_3060", "rtx_3070", "rtx_4060"], "max_buy": 48000, "market": 72000},
    {"name": "MacBook Air / Pro", "keys": ["macbook_air", "macbook_pro"], "max_buy": 42000, "market": 62000},
    {"name": "PlayStation 5", "keys": ["playstation_5", "playstation 5", "ps5"], "max_buy": 32000, "market": 42000},
]

OTHER_CITIES = ["калининград", "тагил", "симферополь", "доставка из", "москва", "спб", "екатеринбург", "краснодар", "самара", "уфа"]
TRASH_WORDS = ["чехол", "стекло", "кабель", "коробка", "аренда", "скупка", "выкуп", "копия", "реплика", "муляж", "кастом", "пульт", "кронштейн", "холодильник", "стиральная", "пылесос"]
SCAM_WORDS = ["предоплата", "icloud", "заблокирован", "мдм", "mdm", "рсим", "r-sim", "нет фейс", "разбит", "трещина", "на запчасти"]

def extract_card_details(raw_window: str, slug: str) -> dict:
    img_match = re.search(r'(https?://\d+\.img\.avito\.st/image/1/[A-Za-z0-9_\-\./]+)', raw_window)
    image_url = img_match.group(1) if img_match else None
    title_match = re.search(r'"title"\s*:\s*"([^"]{4,90})"', raw_window)
    if not title_match:
        title_match = re.search(r'title="([^"]{4,90})"', raw_window)
    title = html_lib.unescape(title_match.group(1)).strip() if title_match else re.sub(r'_\d{9,10}$', '', slug).replace('_', ' ').title()
    desc_match = re.search(r'"description"\s*:\s*"([^"]{10,450})"', raw_window)
    if not desc_match:
        desc_match = re.search(r'<meta[^>]+itemprop="description"[^>]+content="([^"]{10,450})"', raw_window)
    desc = html_lib.unescape(desc_match.group(1)).replace("\\n", " ").strip() if desc_match else "Без описания."
    return {"title": title, "image_url": image_url, "description": desc[:300]}

# 🕵️ УМНЫЙ ПЕРЕБОР С БАН-ЛИСТОМ
async def fetch_smart():
    now = time.time()
    available = [p for p in PROXY_LIST if p not in banned_proxies or banned_proxies[p] < now]
    
    if not available:
        logging.warning("🛑 Все 100 прокси в бане! Ждем 30 минут...")
        await asyncio.sleep(1800)
        return None
        
    random.shuffle(available)
    headers = {"Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8"}
    
    for proxy in available:
        try:
            async with AsyncSession(impersonate="chrome", proxies={"http": proxy, "https": proxy}) as session:
                resp = await session.get(ELECTRONICS_URL, headers=headers, timeout=25.0)
                
                if resp.status_code == 200:
                    logging.info(f"📄 Размер страницы: {len(resp.text)} байт")
                    
                    try:
                        async with AsyncSession(impersonate="chrome", proxies={"http": proxy, "https": proxy}) as ip_session:
                            ip_resp = await ip_session.get("https://api.ipify.org?format=json", timeout=10.0)
                            if ip_resp.status_code == 200:
                                state.current_ip = ip_resp.json().get("ip", "Неизвестно")
                    except: pass
                    
                    logging.info(f"✅ УСПЕХ на порту {proxy.split(':')[-1]} (IP: {state.current_ip})")
                    return resp.text
                elif resp.status_code in (429, 439, 403):
                    logging.warning(f"🚨 Бан (Код {resp.status_code}) на порту {proxy.split(':')[-1]}.")
                    banned_proxies[proxy] = now + 1800
                    await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(0.5)
        except Exception as e:
            logging.warning(f"🔌 Ошибка порта {proxy.split(':')[-1]}: {e}")
            await asyncio.sleep(0.5)
            
    return None

async def parse_electronics(bot: Bot):
    logging.info("🕵️ Ищем свободный прокси из 100...")
    html = await fetch_smart()
    
    if not html:
        return

    clean_html = html.replace("%2F", "/").replace("%2f", "/").replace("\\/", "/")
    
    # 🔥 УЛУЧШЕННЫЙ ПОИСК
    links = []
    
    pattern_html = re.compile(r"/(?:kazan|item)/([a-z0-9_]+)/([a-z0-9_-]+?_(\d{9,10}))\b", re.IGNORECASE)
    for m in pattern_html.finditer(clean_html):
        links.append((m.group(1), m.group(2), m.group(3), m.start()))
    
    if not links:
        pattern_json = re.compile(r'"url"\s*:\s*"([^"]*?/kazan/([a-z0-9_]+)/([a-z0-9_-]+?_(\d{9,10}))[^"]*)"', re.IGNORECASE)
        for m in pattern_json.finditer(clean_html):
            links.append((m.group(2), m.group(3), m.group(4), m.start()))

    logging.info(f"✅ Найдено ссылок: {len(links)} (IP: {state.current_ip})")

    for cat_path, raw_slug, ad_id, pos in links:
        slug = re.sub(r"^(2f|_)+", "", raw_slug, flags=re.IGNORECASE)

        if is_seen(ad_id): continue

        state.stats_checked += 1
        narrow = clean_html[max(0, pos - 400) : pos + 600]
        combined = f"{slug.lower()} {narrow.lower()}"

        if any(c in combined for c in OTHER_CITIES) or any(t in combined for t in TRASH_WORDS) or any(s in combined for s in SCAM_WORDS):
            mark_seen(ad_id)
            continue

        price_val = None
        for pp in [r'"priceDetailed"\s*:\s*\{\s*"value"\s*:\s*(\d{4,7})', r'"price"\s*:\s*(\d{4,7})', r'(\d{1,3}(?:[\s\xa0]\d{3})+)\s*₽']:
            pm = re.search(pp, narrow)
            if pm:
                digits = re.sub(r"\D", "", pm.group(1))
                if digits:
                    price_val = int(digits)
                    break

        if not price_val: continue

        matched_rule = next((r for r in PRICE_RULES if any(k in combined for k in r["keys"])), None)
        if not matched_rule or price_val < (matched_rule["max_buy"] * 0.3) or price_val < 2500:
            mark_seen(ad_id)
            continue

        if price_val <= matched_rule["max_buy"]:
            mark_seen(ad_id)
            state.stats_found += 1
            profit = matched_rule["market"] - price_val
            details = extract_card_details(narrow, slug)

            now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            full_url = f"https://www.avito.ru/kazan/{cat_path}/{slug}"

            caption = (
                f"<b>{html_lib.escape(details['title'])}</b>\n"
                f"💵 <b>{price_val:,} ₽</b>  <i>(Рынок: ~{matched_rule['market']:,} ₽ | 🔥 +{profit:,} ₽)</i>\n"
                f"🌍 Казань\n\n"
                f"<blockquote expandable>{html_lib.escape(details['description'])}</blockquote>\n\n"
                f"🎯 Категория: {matched_rule['name']}\n\n"
                f"📅 {now_str}"
            ).replace(",", " ")

            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть в Казани", url=full_url)]])

            if state.active_chat_id:
                try:
                    if details["image_url"]:
                        await bot.send_photo(chat_id=state.active_chat_id, photo=details["image_url"], caption=caption, parse_mode="HTML", reply_markup=kb)
                    else:
                        await bot.send_message(chat_id=state.active_chat_id, text=caption, parse_mode="HTML", reply_markup=kb)
                except Exception as e:
                    logging.warning(f"⚠️ Ошибка отправки в ТГ: {e}")

# ================= ФОН =================
async def background_scanner(bot: Bot):
    while True:
        try:
            if not state.is_scanning or not state.active_chat_id:
                await asyncio.sleep(1)
                continue

            await parse_electronics(bot)

            pause_sec = random.randint(120, 180)
            logging.info(f"⏳ Пауза {pause_sec} сек...")
            for _ in range(pause_sec):
                if not state.is_scanning: break
                await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"⚠️ Ошибка в сканере: {e}")
            await asyncio.sleep(10)

# ================= ХЕНДЛЕРЫ =================
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    state.active_chat_id = message.chat.id
    state.is_scanning = True
    await message.answer(
        "🕵️ <b>Сторожевой Снайпер (100 прокси) запущен!</b>\n\n"
        "Бот перебирает прокси, меняет IP и ищет лоты. ✨",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="▶️ Запустить сканер"), KeyboardButton(text="⏹ Остановить сканер")], [KeyboardButton(text="📊 Статистика")]],
            resize_keyboard=True
        )
    )

@dp.message(F.text.contains("Запустить"))
async def start_scan(message: types.Message):
    state.active_chat_id = message.chat.id
    state.is_scanning = True
    await message.answer("🕵️ <b>Сторожевой режим активен!</b> ✨", parse_mode="HTML")

@dp.message(F.text.contains("Остановить"))
async def stop_scan(message: types.Message):
    state.is_scanning = False
    await message.answer("⏸ <b>Сканер остановлен.</b> 🌸", parse_mode="HTML")

@dp.message(F.text.contains("Статистика"))
async def show_stats(message: types.Message):
    status = "🟢 Работает" if state.is_scanning else "🔴 На паузе"
    now = time.time()
    active_count = len([p for p in PROXY_LIST if p not in banned_proxies or banned_proxies[p] < now])
    
    text = (
        f"📊 <b>Статистика:</b>\n\n"
        f"Статус: <b>{status}</b>\n"
        f"🌐 <b>Последний IP:</b> <code>{state.current_ip}</code>\n"
        f"🟢 Активных прокси: <b>{active_count} / {len(PROXY_LIST)}</b>\n"
        f"Проверено лотов: <b>{state.stats_checked}</b>\n"
        f"Найдено выгодных: <b>{state.stats_found}</b>\n"
    )
    await message.answer(text, parse_mode="HTML")

async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    asyncio.create_task(background_scanner(bot))
    logging.info("🕵️ Сторожевой бот запущен (100 прокси)!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
EOF
python bott.py
