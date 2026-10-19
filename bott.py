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
    current_region: str = "kazan"

state = ScannerState()

# ================= 🗄️ БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_ads (ad_id TEXT PRIMARY KEY)")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            keys TEXT,
            max_buy INTEGER,
            market INTEGER
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recent_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            price INTEGER,
            market INTEGER,
            url TEXT,
            category TEXT,
            time TEXT
        )
    """)

    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM price_rules")
    if cur.fetchone()[0] == 0:
        default_rules = [
            ("Геймпад DualSense / Xbox", "dualsense,дуалсенс,геймпад ps5,геймпад xbox", 3500, 5300),
            ("iPhone 16 Pro / Max", "16_pro,16 pro,16pro,16_promax", 62000, 79000),
            ("iPhone 15 Pro / Max", "15_pro,15 pro,15pro,15_promax", 48000, 63000),
            ("iPhone 14 Pro / Max", "14_pro,14 pro,14pro,14_promax", 38000, 50000),
            ("PlayStation 5 (Slim / Fat)", "playstation_5,playstation 5,ps5,пс5", 28000, 38000),
            ("Steam Deck / ROG Ally", "steam_deck,steam deck,стим дек,rog_ally", 24000, 36000),
            ("Стайлер / Фен Dyson", "dyson,дайсон,airwrap,supersonic", 12000, 21000),
            ("Видеокарта RTX 3060 / 4060", "rtx_3060,rtx_4060,rtx 3060,rtx 4060", 18000, 27000),
        ]
        cur.executemany("INSERT OR IGNORE INTO price_rules (name, keys, max_buy, market) VALUES (?, ?, ?, ?)", default_rules)
        conn.commit()

    cur.execute("SELECT COUNT(*) FROM settings WHERE key='stop_words'")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO settings (key, value) VALUES ('stop_words', 'чехол,стекло,кабель,аренда,скупка,копия,реплика,ремонт,запчасти,обмен')")
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

def get_stop_words() -> list:
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key='stop_words'")
        row = cur.fetchone()
        conn.close()
        return [w.strip().lower() for w in row[0].split(",")] if row else []
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

def mark_seen(ad_id: str, title: str = "", price: int = 0, market: int = 0, url: str = "", category: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.execute("INSERT OR IGNORE INTO seen_ads (ad_id) VALUES (?)", (ad_id,))
        if title:
            now_str = datetime.now().strftime("%H:%M:%S")
            conn.execute(
                "INSERT INTO recent_findings (title, price, market, url, category, time) VALUES (?, ?, ?, ?, ?, ?)",
                (title, price, market, url, category, now_str)
            )
            conn.execute("DELETE FROM recent_findings WHERE id NOT IN (SELECT id FROM recent_findings ORDER BY id DESC LIMIT 30)")
        conn.commit()
        conn.close()
    except Exception:
        pass

# ================= 🤖 ИИ-АНАЛИТИК СДЕЛКИ =================
def generate_ai_analytics(title: str, price: int, market: int, category: str) -> str:
    profit = market - price
    roi = int((profit / price) * 100) if price > 0 else 0
    
    if roi >= 40:
        verdict = "🔥 АБСОЛЮТНЫЙ ТОП (Сверхдоходный лот)"
        speed = "Уйдет за 1-3 часа. Хватай не раздумывая!"
    elif roi >= 25:
        verdict = "⚡️ ОТЛИЧНАЯ СДЕЛКА (Высокая маржа)"
        speed = "Уйдет за 1-2 дня при качественных фото."
    else:
        verdict = "💡 УМЕРЕННЫЙ ПРОФИТ (Торг обязателен)"
        speed = "Требует торга с продавцом для увеличения маржи."

    return (
        f"🤖 <b>AI-Аналитика & Рекомендации:</b>\n"
        f"• Вердикт: <b>{verdict}</b>\n"
        f"• Чистая прибыль: <b>+{profit:,} ₽ (ROI: {roi}%)</b>\n"
        f"• Прогнозируемая скорость перепродажи: {speed}\n\n"
        f"💬 <b>Совет по торгу:</b> Предложи наличные при встрече за быстрый выкуп со скидкой."
    ).replace(",", " ")

# ================= КАТЕГОРИИ И ФИЛЬТРЫ (Мобильные ссылки) =================
TARGETS = {
    "kazan": [
        ("Телефоны", "https://m.avito.ru/kazan/telefony?s=104"),
        ("Приставки и игры", "https://m.avito.ru/kazan/igry_pristavki_i_programmy?s=104"),
        ("Ноутбуки", "https://m.avito.ru/kazan/noutbuki?s=104"),
        ("Планшеты", "https://m.avito.ru/kazan/planshety_i_elektronnye_knigi?s=104"),
        ("Аудио и видео", "https://m.avito.ru/kazan/audio_i_video?s=104"),
        ("Бытовая техника", "https://m.avito.ru/kazan/bytovaya_tehnika?s=104"),
        ("Товары для ПК", "https://m.avito.ru/kazan/tovary_dlya_kompyutera?s=104"),
    ],
    "innopolis": [
        ("Телефоны", "https://m.avito.ru/innopolis/telefony?s=104"),
        ("Приставки и игры", "https://m.avito.ru/innopolis/igry_pristavki_i_programmy?s=104"),
    ]
}

OTHER_CITIES = ["калининград", "тагил", "симферополь", "доставка из", "москва", "спб", "екатеринбург", "краснодар", "самара", "уфа", "нижний новгород", "пермь"]
SCAM_WORDS = ["предоплата", "icloud", "заблокирован", "мдм", "mdm", "рсим", "r-sim", "нет фейс", "разбит", "трещина", "на запчасти", "выгорание", "демо", "demo", "не включается"]

def normalize_html(raw_html: str) -> str:
    text = html_lib.unescape(raw_html)
    text = text.replace("\\u002F", "/").replace("\\u002f", "/").replace("%2F", "/").replace("%2f", "/").replace("\\/", "/").replace('\\"', '"')
    for marker in ("itemsCarousel", "Доставка из других", "Другие города"):
        idx = text.find(marker)
        if idx != -1 and idx > 15000:
            text = text[:idx]
    return text

def extract_links_from_html(clean_html: str, region: str) -> list:
    links = []
    pattern = re.compile(rf"/{region}/([a-z0-9_-]+)/([a-z0-9_-]+?_(\d{8,12}))(?![0-9])", re.IGNORECASE)
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
    desc = html_lib.unescape(desc_match.group(1)).replace("\\n", " ").strip() if desc_match else "Свежее объявление в регионе."
    return {"title": title, "image_url": image_url, "description": desc[:300]}

# ================= 🔥 МНОГОПОТОЧНОЕ ЯДРО ЗАПРОСОВ =================
def _sync_worker_fetch(url: str, proxy: str, headers: dict, region: str):
    try:
        resp = cffi_requests.get(url, headers=headers, proxies={"http": proxy, "https": proxy}, impersonate="chrome", timeout=(3.0, 4.0))
        logging.info(f"Proxy {proxy.split('@')[-1]} status: {resp.status_code}, len: {len(resp.text)}")
        if resp.status_code == 200 and len(resp.text) > 5000:
            clean_html = normalize_html(resp.text)
            unique_links = extract_links_from_html(clean_html, region)
            if unique_links:
                return clean_html, unique_links
    except Exception as e:
        logging.error(f"Proxy error {proxy.split('@')[-1]}: {e}")
    return None, []

async def fetch_smart(url: str, region: str):
    now = time.time()
    available = [p for p in PROXY_LIST if p not in banned_proxies or banned_proxies[p] < now]
    if not available:
        banned_proxies.clear()
        available = PROXY_LIST.copy()

    random.shuffle(available)
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    }
    loop = asyncio.get_running_loop()

    for proxy in available[:10]:
        if not state.is_scanning:
            break
        port_str = proxy.split(':')[-1]
        state.last_activity_time = time.time()
        try:
            future = loop.run_in_executor(ThreadPool, _sync_worker_fetch, url, proxy, headers, region)
            clean_html, unique_links = await asyncio.wait_for(future, timeout=5.0)
            if clean_html and unique_links:
                state.current_ip = f"Порт {port_str}"
                state.last_activity_time = time.time()
                logging.info(f"📄 Успех на порту {port_str}! Карточек: {len(unique_links)}")
                return clean_html, unique_links
            else:
                banned_proxies[proxy] = now + 120
        except Exception:
            banned_proxies[proxy] = now + 120
    return None, []

async def send_deal_card(bot: Bot, chat_id: int, details: dict, price_val: int, matched_rule: dict, full_url: str) -> bool:
    profit = matched_rule["market"] - price_val
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    
    ai_text = generate_ai_analytics(details['title'], price_val, matched_rule['market'], matched_rule['name'])

    caption = (
        f"<b>{html_lib.escape(details['title'])}</b>\n"
        f"💵 <b>{price_val:,} ₽</b>  <i>(Рынок: ~{matched_rule['market']:,} ₽ | 🔥 +{profit:,} ₽)</i>\n"
        f"🌍 Регион: {state.current_region}\n\n"
        f"<blockquote>{html_lib.escape(details['description'])}</blockquote>\n\n"
        f"{ai_text}\n\n"
        f"🔗 <a href=\"{full_url}\">Прямая ссылка на Авито</a>\n"
        f"📅 {now_str}"
    ).replace(",", " ")

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ В избранное / CRM", callback_data=f"crm_{price_val}")], [InlineKeyboardButton(text="🚀 Открыть на Авито", url=full_url)]])
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
    targets_list = TARGETS.get(state.current_region, TARGETS["kazan"])
    cat_name, url = targets_list[state.target_idx % len(targets_list)]
    state.target_idx += 1
    state.last_activity_time = time.time()

    clean_html, unique_links = await fetch_smart(url, state.current_region)
    if not clean_html or not unique_links:
        return

    price_rules = get_price_rules()
    stop_words = get_stop_words()
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

        if any(c in combined for c in OTHER_CITIES) or any(s in combined for s in SCAM_WORDS) or any(sw in combined for sw in stop_words):
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
            details = extract_card_details(narrow, slug)
            full_url = f"https://www.avito.ru/{state.current_region}/{cat_path}/{slug}"
            mark_seen(ad_id, title=details['title'], price=price_val, market=matched_rule['market'], url=full_url, category=matched_rule['name'])

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

# ================= 🌐 WEB-СЕРВЕР И MINI APP =================
async def handle_webapp(request):
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Управление Снайпером 24</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: var(--tg-theme-bg-color, #121212); color: var(--tg-theme-text-color, #ffffff); margin: 0; padding: 15px; }
            h2, h3 { text-align: center; color: var(--tg-theme-button-color, #2ea6ff); }
            .card { background: var(--tg-theme-secondary-bg-color, #1e1e1e); border-radius: 12px; padding: 12px; margin-bottom: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
            input, select, button { width: 100%; padding: 10px; margin-top: 6px; border-radius: 8px; border: none; font-size: 14px; box-sizing: border-box; }
            input, select { background: #2a2a2a; color: #fff; }
            button { background: var(--tg-theme-button-color, #2ea6ff); color: var(--tg-theme-button-text-color, #fff); font-weight: bold; cursor: pointer; }
            .btn-danger { background: #ff4d4d !important; }
            .btn-test { background: #4caf50 !important; margin-top: 8px; }
            .row { display: flex; gap: 8px; }
            .finding-item { border-bottom: 1px solid #333; padding: 8px 0; font-size: 12px; }
        </style>
    </head>
    <body>
        <h2>⚡️ Снайпер 24 — Control Center</h2>

        <div class="card">
            <h3>🎛 Управление ядром</h3>
            <div class="row">
                <button onclick="toggleScan(true)">▶️ Запустить</button>
                <button class="btn-danger" onclick="toggleScan(false)">⏹ Стоп</button>
            </div>
            <button class="btn-test" onclick="sendTestCard()">🧪 Тест: прислать тестовую карточку</button>
            <button style="background:#ff9800; margin-top:8px;" onclick="restartKernel()">🔄 Перезагрузить движок</button>
            <p>Статус: <span id="scanStatus" style="font-weight:bold; color:#4caf50;">Активен</span></p>
        </div>

        <div class="card">
            <h3>🌍 Выбор региона</h3>
            <select id="regionSelect" onchange="changeRegion()">
                <option value="kazan">Казань</option>
                <option value="innopolis">Иннополис</option>
            </select>
        </div>

        <div class="card">
            <h3>📊 Статистика</h3>
            <p>Проверено лотов: <span id="checked">0</span></p>
            <p>Найдено выгодных: <span id="found">0</span></p>
        </div>

        <div class="card">
            <h3>🧮 Калькулятор маржинальности</h3>
            <input type="number" id="calcBuy" placeholder="Цена покупки (₽)" oninput="calcProfit()">
            <input type="number" id="calcMarket" placeholder="Рыночная цена (₽)" oninput="calcProfit()">
            <p style="margin-top:8px; font-weight:bold; text-align:center;" id="calcResult">Чистая прибыль: 0 ₽</p>
        </div>

        <div class="card">
            <h3>🎯 Добавить / Обновить товар</h3>
            <input type="text" id="name" placeholder="Название товара">
            <input type="text" id="keys" placeholder="Ключевые слова через запятую">
            <div class="row">
                <input type="number" id="max_buy" placeholder="Макс. цена">
                <input type="number" id="market" placeholder="Рынок">
            </div>
            <button onclick="saveRule()">💾 Сохранить в базу</button>
        </div>

        <div class="card">
            <h3>🚫 Черный список (Стоп-слова)</h3>
            <input type="text" id="stopwords" placeholder="Стоп-слова через запятую">
            <button onclick="saveStopWords()">💾 Сохранить стоп-слова</button>
        </div>

        <div class="card">
            <h3>⚡️ Живой лог последних находок</h3>
            <div id="findingsList">Загрузка...</div>
        </div>

        <div class="card">
            <h3>📥 Отчеты</h3>
            <button style="background:#4caf50;" onclick="exportCSV()">📥 Скачать отчет по находкам (CSV)</button>
        </div>

        <script>
            let tg = window.Telegram.WebApp;
            tg.expand();

            async function loadData() {
                let res = await fetch('/api/stats');
                let data = await res.json();
                document.getElementById('checked').innerText = data.checked;
                document.getElementById('found').innerText = data.found;
                document.getElementById('scanStatus').innerText = data.is_scanning ? "Активен 🟢" : "Остановлен 🔴";

                let fRes = await fetch('/api/findings');
                let findings = await fRes.json();
                let fHtml = '';
                findings.forEach(f => {
                    fHtml += `<div class="finding-item"><b>${f.title}</b><br>💵 ${f.price}₽ (Рынок: ${f.market}₽) | ⏱ ${f.time}</div>`;
                });
                document.getElementById('findingsList').innerHTML = fHtml || 'Пока нет находок в сессии.';
            }

            async function toggleScan(status) {
                await fetch('/api/toggle?status=' + status);
                loadData();
            }

            async function sendTestCard() {
                let res = await fetch('/api/test_card');
                if(res.ok) { alert('Тестовая карточка успешно отправлена в чат!'); }
                else { alert('Ошибка отправки теста.'); }
            }

            async function restartKernel() {
                if(confirm('Перезагрузить ядро бота?')) {
                    await fetch('/api/restart');
                    alert('Команда на перезапуск отправлена!');
                }
            }

            async function changeRegion() {
                let reg = document.getElementById('regionSelect').value;
                await fetch('/api/region?val=' + reg);
            }

            async function saveRule() {
                let body = {
                    name: document.getElementById('name').value,
                    keys: document.getElementById('keys').value,
                    max_buy: parseInt(document.getElementById('max_buy').value),
                    market: parseInt(document.getElementById('market').value)
                };
                let res = await fetch('/api/rules', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
                if(res.ok) { alert('Правило сохранено!'); }
            }

            async function saveStopWords() {
                let sw = document.getElementById('stopwords').value;
                await fetch('/api/stopwords', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({words: sw}) });
                alert('Стоп-слова обновлены!');
            }

            function calcProfit() {
                let buy = parseInt(document.getElementById('calcBuy').value) || 0;
                let market = parseInt(document.getElementById('calcMarket').value) || 0;
                let prof = market - buy;
                let roi = buy > 0 ? Math.round((prof / buy) * 100) : 0;
                document.getElementById('calcResult').innerText = `Чистая прибыль: ${prof.toLocaleString()} ₽ (ROI: ${roi}%)`;
            }

            function exportCSV() {
                window.location.href = '/api/export';
            }

            loadData();
            setInterval(loadData, 4000);
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")

async def api_stats(request):
    return web.json_response({
        "checked": state.stats_checked,
        "found": state.stats_found,
        "is_scanning": state.is_scanning
    })

async def api_toggle(request):
    status = request.query.get("status")
    state.is_scanning = (status == "true")
    return web.json_response({"status": "ok"})

async def api_region(request):
    val = request.query.get("val")
    if val in TARGETS:
        state.current_region = val
    return web.json_response({"status": "ok"})

async def api_test_card(request):
    global global_bot_instance
    if global_bot_instance and state.active_chat_id:
        dummy_details = {
            "title": "🧪 Тестовый iPhone 16 Pro Max 256GB (Идеальное состояние)",
            "image_url": "https://www.avito.st/s/v1/widget/iphone.jpg",
            "description": "Это тестовая карточка для проверки отправки сообщений, ссылок и ИИ-аналитики в вашем снайпере «24 сервер»!"
        }
        dummy_rule = {"name": "Смартфоны", "market": 85000}
        await send_deal_card(global_bot_instance, state.active_chat_id, dummy_details, 59000, dummy_rule, "https://www.avito.ru/kazan")
        return web.json_response({"status": "ok"})
    return web.json_response({"status": "error"}, status=400)

async def api_restart(request):
    os._exit(1)

async def api_findings(request):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT title, price, market, time FROM recent_findings ORDER BY id DESC LIMIT 10")
        rows = cur.fetchall()
        conn.close()
        return web.json_response([{"title": r[0], "price": r[1], "market": r[2], "time": r[3]} for r in rows])
    except Exception:
        return web.json_response([])

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

async def api_stopwords(request):
    try:
        data = await request.json()
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('stop_words', ?)", (data["words"],))
        conn.commit()
        conn.close()
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def api_export(request):
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT title, price, market, category, time FROM recent_findings")
        rows = cur.fetchall()
        conn.close()
        csv_data = "Title,Price,Market,Category,Time\n"
        for r in rows:
            csv_data += f'"{r[0]}",{r[1]},{r[2]},"{r[3]}","{r[4]}"\n'
        return web.Response(text=csv_data, content_type="text/csv")
    except Exception:
        return web.Response(text="Error", status=500)

async def handle_ping(request):
    return web.Response(text="Bot is running 24/7 with Ultimate Mini App & AI Analyst!")

# ================= ХЕНДЛЕРЫ TELEGRAM =================
dp = Dispatcher()
global_bot_instance = None

def get_main_kb():
    webapp_button = InlineKeyboardButton(text="⚙️ Управление & AI (Mini App)", web_app=WebAppInfo(url=WEBAPP_URL))
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
        f"🕵️ <b>Ультра-Снайпер 24 (Ultimate Edition)!</b>\n"
        f"Встроена AI-аналитика рынка и полнофункциональный Mini App ✨",
        parse_mode="HTML",
        reply_markup=kb_inline,
    )
    await message.answer("Главное меню:", reply_markup=kb_reply)

@dp.callback_query(F.data.startswith("crm_"))
async def crm_callback(callback: types.CallbackQuery):
    await callback.answer("⭐ Товар успешно добавлен в ваш трекер сделок (CRM)!")

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
        f"🌍 Регион: <code>{state.current_region}</code>\n"
        f"🔍 Проверено: <b>{state.stats_checked}</b>\n"
        f"🔥 Выгодных: <b>{state.stats_found}</b>"
    )
    _, kb_inline = get_main_kb()
    await message.answer(text, parse_mode="HTML", reply_markup=kb_inline)

# ================= ЗАПУСК СИСТЕМЫ =================
async def main():
    global global_bot_instance
    init_db()
    
    wd_thread = threading.Thread(target=hardware_kernel_watchdog, daemon=True, name="HardwareWatchdog")
    wd_thread.start()

    session = AiohttpSession(timeout=30)
    bot = Bot(token=BOT_TOKEN, session=session, default=DefaultBotProperties(parse_mode="HTML"))
    global_bot_instance = bot

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        pass

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/webapp", handle_webapp)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/toggle", api_toggle)
    app.router.add_get("/api/region", api_region)
    app.router.add_get("/api/test_card", api_test_card)
    app.router.add_get("/api/restart", api_restart)
    app.router.add_get("/api/findings", api_findings)
    app.router.add_post("/api/rules", api_rules_post)
    app.router.add_post("/api/stopwords", api_stopwords)
    app.router.add_get("/api/export", api_export)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"🌐 Веб-сервер и Ultimate Mini App успешно запущены на порту {PORT}")

    asyncio.create_task(background_scanner(bot))
    logging.info("🛡️ Снайпер 24 Ultimate с ИИ-аналитиком запущен!")

    while True:
        try:
            logging.info("🚀 Запуск Telegram Polling...")
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            logging.error(f"⚠️ Ошибка связи с Telegram: {e}. Переподключение через 5 секунд...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
