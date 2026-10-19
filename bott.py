import asyncio
from concurrent.futures import ThreadPoolExecutor
import html as html_lib
import json
import logging
import os
import random
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from aiohttp import web
from curl_cffi import requests as cffi_requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8968394996:AAEpGiADwUoqHZ1KqCA1MllKhFOXOF6OA10"
DB_PATH = "safe_items.db"
PORT = int(os.environ.get("PORT", 10000))
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://avito-bot-2-3iwt.onrender.com/webapp")

DEFAULT_CHAT_ID = 1128246797

# 🔥 Пул из 100 прокси (10000 - 10099)
PROXY_LIST = [f"http://W3KAbVZ7rM:ruTTPneIyG@proxy.proxyma.io:{p}" for p in range(10000, 10100)]
banned_proxies = {}

ThreadPool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="AvitoWorker")

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

# ================= 🗄️ ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ И МАТРИЦЫ ЦЕН =================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_ads (ad_id TEXT PRIMARY KEY)")
    
    # Таблица для товаров и цен (чтобы управлять ими из Mini App)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            keys TEXT,
            max_buy INTEGER,
            market INTEGER
        )
    """)
    
    # Таблица для логирования статистики по дням
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            checked INTEGER,
            found INTEGER
        )
    """)
    
    # Заполняем дефолтными правилами Казани, если таблица пустая
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM price_rules")
    if cur.fetchone()[0] == 0:
        default_rules = [
            ("Геймпад DualSense / Xbox", "dualsense,дуалсенс,геймпад ps5,геймпад xbox,джойстик ps5", 3500, 5300),
            ("iPhone 16 Pro / Max", "16_pro,16 pro,16pro,16_promax", 62000, 79000),
            ("iPhone 16 / Plus", "iphone_16,iphone 16,айфон 16,16_plus", 42000, 56000),
            ("iPhone 15 Pro / Max", "15_pro,15 pro,15pro,15_promax", 48000, 63000),
            ("iPhone 14 Pro / Max", "14_pro,14 pro,14pro,14_promax", 38000, 50000),
            ("PlayStation 5 (Slim / Fat)", "playstation_5,playstation 5,ps5,пс5,плойка 5", 28000, 38000),
            ("Steam Deck / ROG Ally", "steam_deck,steam deck,стим дек,rog_ally,rog ally", 24000, 36000),
            ("Стайлер / Фен Dyson", "dyson,дайсон,airwrap,supersonic", 12000, 21000),
            ("Видеокарта RTX 3060 / 4060", "rtx_3060,rtx_4060,rtx 3060,rtx 4060", 18000, 27000),
            ("Яндекс Станция Макс / 2", "яндекс станция макс,яндекс станция 2,станция макс", 6500, 12000),
        ]
        cur.executemany("INSERT OR IGNORE INTO price_rules (name, keys, max_buy, market) VALUES (?, ?, ?, ?)", default_rules)
        conn.commit()
    conn.close()

def get_price_rules() -> list:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT name, keys, max_buy, market FROM price_rules")
        rows = cur.fetchall()
        conn.close()
        return [{"name": r[0], "keys": [k.strip() for k in r[1].split(",")], "max_buy": r[2], "market": r[3]} for r in rows]
    except Exception:
        return []

def is_seen(ad_id: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM seen_ads WHERE ad_id = ?", (ad_id,))
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

def mark_seen(ad_id: str):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.execute("INSERT OR IGNORE INTO seen_ads (ad_id) VALUES (?)", (ad_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass

# ================= КАТЕГОРИИ И ФИЛЬТРЫ =================
TARGETS = [
    ("Телефоны", "https://m.avito.ru/kazan/telefony?s=104"),
    ("Приставки и игры", "https://m.avito.ru/kazan/igry_pristavki_i_programmy?s=104"),
    ("Ноутбуки", "https://m.avito.ru/kazan/noutbuki?s=104"),
    ("Планшеты", "https://m.avito.ru/kazan/planshety_i_elektronnye_knigi?s=104"),
    ("Аудио и видео", "https://m.avito.ru/kazan/audio_i_video?s=104"),
    ("Бытовая техника", "https://m.avito.ru/kazan/bytovaya_tehnika?s=104"),
    ("Товары для ПК", "https://m.avito.ru/kazan/tovary_dlya_kompyutera?s=104"),
]

OTHER_CITIES = ["калининград", "тагил", "симферополь", "доставка из", "москва", "спб", "екатеринбург", "краснодар", "самара", "уфа", "нижний новгород", "пермь"]
TRASH_WORDS = ["чехол", "стекло", "кабель", "коробка", "аренда", "скупка", "выкуп", "копия", "реплика", "муляж", "кастом", "пульт", "ремонт", "запчасти", "обмен", "рассрочка", "аккаунт"]
SCAM_WORDS = ["предоплата", "icloud", "заблокирован", "мдм", "mdm", "рсим", "r-sim", "нет фейс", "разбит", "трещина", "на запчасти", "выгорание", "демо", "demo", "не включается"]

def normalize_html(raw_html: str) -> str:
    text = html_lib.unescape(raw_html)
    text = text.replace("\\u002F", "/").replace("\\u002f", "/").replace("%2F", "/").replace("%2f", "/").replace("\\/", "/").replace('\\"', '"')
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
    title = html_lib.unescape(title_match.group(1)).strip() if title_match else re.sub(r'_\d{8,12}$', '', slug).replace('_', ' ').title()

    desc_match = re.search(r'"description"\s*:\s*"([^"]{10,450})"', raw_window)
    desc = html_lib.unescape(desc_match.group(1)).replace("\\n", " ").strip() if desc_match else "Свежее объявление в Казани."
    return {"title": title, "image_url": image_url, "description": desc[:300]}

# ================= МНОГОПОТОЧНОЕ ЯДРО ЗАПРОСОВ =================
def _sync_worker_fetch(url: str, proxy: str, headers: dict):
    resp = cffi_requests.get(url, headers=headers, proxies={"http": proxy, "https": proxy}, impersonate="chrome", timeout=(2.5, 3.5))
    if resp.status_code == 200 and len(resp.text) > 15000:
        clean_html = normalize_html(resp.text)
        unique_links = extract_links_from_html(clean_html)
        if unique_links:
            return clean_html, unique_links
    return None, []

async def fetch_smart(url: str):
    now = time.time()
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
    loop = asyncio.get_running_loop()

    for proxy in available[:5]:
        if not state.is_scanning:
            break
        port_str = proxy.split(':')[-1]
        state.last_activity_time = time.time()
        try:
            future = loop.run_in_executor(ThreadPool, _sync_worker_fetch, url, proxy, headers)
            clean_html, unique_links = await asyncio.wait_for(future, timeout=4.0)
            if clean_html and unique_links:
                state.current_ip = f"Порт {port_str}"
                state.last_activity_time = time.time()
                return clean_html, unique_links
            else:
                banned_proxies[proxy] = now + 300
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
        return True
    except Exception:
        try:
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=False)
            return True
        except Exception:
            return False

async def parse_electronics(bot: Bot):
    cat_name, url = TARGETS[state.target_idx % len(TARGETS)]
    state.target_idx += 1
    state.last_activity_time = time.time()

    clean_html, unique_links = await fetch_smart(url)
    if not clean_html or not unique_links:
        return

    price_rules = get_price_rules()
    new_checked = 0
    matched_in_pass = 0

    for cat_path, raw_slug, ad_id, pos in unique_links:
        state.last_activity_time = time.time()
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

        matched_rule = next((r for r in price_rules if any(k in combined for k in r["keys"])), None)
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

async def background_scanner(bot: Bot):
    while True:
        try:
            if not state.is_scanning or not state.active_chat_id:
                state.last_activity_time = time.time()
                await asyncio.sleep(1)
                continue

            state.last_activity_time = time.time()
            await parse_electronics(bot)

            pause_sec = random.randint(15, 28)
            for _ in range(pause_sec):
                state.last_activity_time = time.time()
                if not state.is_scanning or not state.active_chat_id:
                    break
                await asyncio.sleep(1)
        except Exception:
            await asyncio.sleep(5)

def hardware_kernel_watchdog():
    while True:
        time.sleep(15)
        if not state.is_scanning:
            state.last_activity_time = time.time()
            continue
        if time.time() - state.last_activity_time > 90:
            os._exit(1)

# ================= 🌐 WEB-СЕРВЕР И API ДЛЯ MINI APP =================
async def handle_webapp(request):
    """Отдает красивый интерфейс Telegram Mini App прямо с сервера"""
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Управление Снайпером</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: var(--tg-theme-bg-color, #121212); color: var(--tg-theme-text-color, #ffffff); margin: 0; padding: 15px; }
            h2 { text-align: center; color: var(--tg-theme-button-color, #2ea6ff); }
            .card { background: var(--tg-theme-secondary-bg-color, #1e1e1e); border-radius: 12px; padding: 12px; margin-bottom: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
            input, button { width: 100%; padding: 10px; margin-top: 6px; border-radius: 8px; border: none; font-size: 14px; box-sizing: border-box; }
            input { background: #2a2a2a; color: #fff; }
            button { background: var(--tg-theme-button-color, #2ea6ff); color: var(--tg-theme-button-text-color, #fff); font-weight: bold; cursor: pointer; }
            .row { display: flex; gap: 8px; }
        </style>
    </head>
    <body>
        <h2>⚡️ Снайпер 24 - Панель</h2>
        <div class="card">
            <h3>📊 Статистика</h3>
            <p>Проверено лотов: <span id="checked">0</span></p>
            <p>Найдено выгодных: <span id="found">0</span></p>
            <p>Активные прокси: <span id="proxies">100 / 100</span></p>
        </div>
        <div class="card">
            <h3>🎯 Добавить / Изменить товар</h3>
            <input type="text" id="name" placeholder="Название (например, iPhone 17)">
            <input type="text" id="keys" placeholder="Ключи (через запятую)">
            <div class="row">
                <input type="number" id="max_buy" placeholder="Макс. цена">
                <input type="number" id="market" placeholder="Рынок">
            </div>
            <button onclick="saveRule()">💾 Сохранить в базу</button>
        </div>
        <div class="card">
            <h3>📋 Активные правила цен</h3>
            <div id="rulesList">Загрузка...</div>
        </div>
        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();

            async function loadData() {
                let res = await fetch('/api/stats');
                let data = await res.json();
                document.getElementById('checked').innerText = data.checked;
                document.getElementById('found').innerText = data.found;

                let rRes = await fetch('/api/rules');
                let rules = await rRes.json();
                let html = '';
                rules.forEach(r => {
                    html += `<div style="border-bottom:1px solid #333; padding:6px 0;"><b>${r.name}</b><br>🛍 Макс: ${r.max_buy}₽ | 📈 Рынок: ${r.market}₽</div>`;
                });
                document.getElementById('rulesList').innerText = rules.length ? '' : 'Нет правил';
                document.getElementById('rulesList').innerHTML = html;
            }

            async function saveRule() {
                let body = {
                    name: document.getElementById('name').value,
                    keys: document.getElementById('keys').value,
                    max_buy: parseInt(document.getElementById('max_buy').value),
                    market: parseInt(document.getElementById('market').value)
                };
                let res = await fetch('/api/rules', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(body)
                });
                if(res.ok) { alert('Успешно сохранено!'); loadData(); }
            }
            loadData();
            setInterval(loadData, 5000);
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")

async def api_stats(request):
    return web.json_response({"checked": state.stats_checked, "found": state.stats_found})

async def api_rules_get(request):
    return web.json_response(get_price_rules())

async def api_rules_post(request):
    try:
        data = await request.json()
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO price_rules (name, keys, max_buy, market) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET keys=excluded.keys, max_buy=excluded.max_buy, market=excluded.market",
            (data["name"], data["keys"], data["max_buy"], data["market"])
        )
        conn.commit()
        conn.close()
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_ping(request):
    return web.Response(text="Bot is running 24/7 with Telegram Web App!")

# ================= ХЕНДЛЕРЫ TELEGRAM =================
dp = Dispatcher()

def get_main_kb():
    webapp_button = InlineKeyboardButton(text="⚙️ Управление (Mini App)", web_app=WebAppInfo(url=WEBAPP_URL))
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="▶️ Запустить сканер"), KeyboardButton(text="⏹ Остановить сканер")],
            [KeyboardButton(text="📊 Статистика")],
        ],
        resize_keyboard=True,
    ), InlineKeyboardMarkup(inline_keyboard=[[webapp_button]])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    state.active_chat_id = message.chat.id
    state.is_scanning = True
    kb_reply, kb_inline = get_main_kb()
    await message.answer(
        f"🕵️ <b>Ультра-Снайпер 24 (Mini App активирован)!</b>\n"
        f"Открой панель управления кнопкой ниже ✨",
        parse_mode="HTML",
        reply_markup=kb_inline,
    )
    await message.answer("Главное меню:", reply_markup=kb_reply)

@dp.message(F.text.contains("Запустить"))
async def start_scan(message: types.Message):
    state.active_chat_id = message.chat.id
    state.is_scanning = True
    await message.answer("🕵️ <b>Сканирование активно!</b>", parse_mode="HTML")

@dp.message(F.text.contains("Остановить"))
async def stop_scan(message: types.Message):
    state.is_scanning = False
    await message.answer("⏸ <b>Сканер остановлен.</b> 🌸", parse_mode="HTML")

@dp.message(F.text.contains("Статистика"))
async def show_stats(message: types.Message):
    status = "🟢 Работает" if state.is_scanning else "🔴 На паузе"
    text = (
        f"📊 <b>Статистика Снайпера 24:</b>\n\n"
        f"Статус: <b>{status}</b>\n"
        f"💬 Чат: <code>{state.active_chat_id}</code>\n"
        f"🌐 Порт: <code>{state.current_ip}</code>\n"
        f"🔍 Проверено: <b>{state.stats_checked}</b>\n"
        f"🔥 Выгодных: <b>{state.stats_found}</b>"
    )
    _, kb_inline = get_main_kb()
    await message.answer(text, parse_mode="HTML", reply_markup=kb_inline)

# ================= ЗАПУСК СИСТЕМЫ =================
async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/webapp", handle_webapp)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/rules", api_rules_get)
    app.router.add_post("/api/rules", api_rules_post)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"🌐 Веб-сервер и Mini App запущены на порту {PORT}")

async def on_startup(bot: Bot):
    init_db()
    wd_thread = threading.Thread(target=hardware_kernel_watchdog, daemon=True, name="HardwareWatchdog")
    wd_thread.start()
    asyncio.create_task(background_scanner(bot))
    logging.info("🛡️ Снайпер 24 с Mini App успешно запущен!")

async def main():
    session = AiohttpSession(timeout=30)
    bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))

    await web_server()
    await on_startup(bot)

    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
        except Exception:
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
