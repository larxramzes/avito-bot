import asyncio
from concurrent.futures import ThreadPoolExecutor
import html as html_lib
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
)
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8968394996:AAEpGiADwUoqHZ1KqCA1MllKhFOXOF6OA10"
DB_PATH = "safe_items.db"
PORT = int(os.environ.get("PORT", 10000))

DEFAULT_CHAT_ID = 1128246797

# 🔥 Точный пул из 100 прокси (порты 10000 - 10099)
PROXY_LIST = [f"http://W3KAbVZ7rM:ruTTPneIyG@proxy.proxyma.io:{p}" for p in range(10000, 10100)]
banned_proxies = {}

# 🔥 Пул изолированных системных потоков для C-движка curl_cffi
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
    core_restarts: int = 0

state = ScannerState()

# ================= БАЗА =================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE IF NOT EXISTS seen_ads (ad_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

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

# ================= КАТЕГОРИИ И АКТУАЛЬНЫЙ РЫНОК КАЗАНИ =================
TARGETS = [
    ("Телефоны", "https://m.avito.ru/kazan/telefony?s=104"),
    ("Приставки и игры", "https://m.avito.ru/kazan/igry_pristavki_i_programmy?s=104"),
    ("Ноутбуки", "https://m.avito.ru/kazan/noutbuki?s=104"),
    ("Планшеты", "https://m.avito.ru/kazan/planshety_i_elektronnye_knigi?s=104"),
    ("Аудио и видео (ТВ / Колонки)", "https://m.avito.ru/kazan/audio_i_video?s=104"),
    ("Бытовая техника (Пылесосы / Dyson)", "https://m.avito.ru/kazan/bytovaya_tehnika?s=104"),
    ("Товары для ПК (Видеокарты / Мониторы)", "https://m.avito.ru/kazan/tovary_dlya_kompyutera?s=104"),
]

PRICE_RULES = [
    {"name": "Геймпад DualSense / Xbox", "keys": ["dualsense", "дуалсенс", "геймпад ps5", "геймпад xbox", "джойстик ps5"], "max_buy": 3500, "market": 5300},
    {"name": "iPhone 18 Pro / Max", "keys": ["18_pro", "18 pro", "18pro", "18_promax"], "max_buy": 90000, "market": 115000},
    {"name": "iPhone 18 / Plus / Air", "keys": ["iphone_18", "iphone 18", "айфон 18"], "max_buy": 65000, "market": 85000},
    {"name": "iPhone 17 Pro / Max", "keys": ["17_pro", "17 pro", "17pro", "17_promax"], "max_buy": 75000, "market": 95000},
    {"name": "iPhone 17 / Air / Plus", "keys": ["iphone_17", "iphone 17", "айфон 17", "17_air", "17 air"], "max_buy": 52000, "market": 68000},
    {"name": "iPhone 16 Pro / Max", "keys": ["16_pro", "16 pro", "16pro", "16_promax"], "max_buy": 62000, "market": 79000},
    {"name": "iPhone 16 / Plus / 16e", "keys": ["iphone_16", "iphone 16", "айфон 16", "16_plus", "16e"], "max_buy": 42000, "market": 56000},
    {"name": "iPhone 15 Pro / Max", "keys": ["15_pro", "15 pro", "15pro", "15_promax"], "max_buy": 48000, "market": 63000},
    {"name": "iPhone 15 / Plus", "keys": ["iphone_15", "iphone 15", "айфон 15"], "max_buy": 35000, "market": 46000},
    {"name": "iPhone 14 Pro / Max", "keys": ["14_pro", "14 pro", "14pro", "14_promax"], "max_buy": 38000, "market": 50000},
    {"name": "iPhone 14 / Plus", "keys": ["iphone_14", "iphone 14", "айфон 14"], "max_buy": 27000, "market": 37000},
    {"name": "iPhone 13 Pro / Max", "keys": ["13_pro", "13 pro", "13pro", "13_promax"], "max_buy": 29000, "market": 39000},
    {"name": "iPhone 13 / Mini", "keys": ["iphone_13", "iphone 13", "айфон 13"], "max_buy": 21000, "market": 29000},
    {"name": "iPhone 12 Pro / Max", "keys": ["12_pro", "12 pro", "12pro", "12_promax"], "max_buy": 19500, "market": 27000},
    {"name": "iPhone 12 / Mini", "keys": ["iphone_12", "iphone 12", "айфон 12"], "max_buy": 14000, "market": 20000},
    {"name": "iPhone 11 Pro / Max", "keys": ["11_pro", "11 pro", "11pro"], "max_buy": 13000, "market": 18500},
    {"name": "iPhone 11", "keys": ["iphone_11", "iphone 11", "айфон 11"], "max_buy": 10500, "market": 15000},
    {"name": "iPhone XS / XR / SE 2022", "keys": ["iphone_xr", "iphone xr", "айфон хр", "iphone_xs", "iphone xs", "iphone se 2022", "iphone_se_2022", "iphone se 3"], "max_buy": 8000, "market": 12000},
    {"name": "Samsung S25 / S26 Ultra", "keys": ["s25_ultra", "s26_ultra", "s25 ultra", "s26 ultra"], "max_buy": 58000, "market": 78000},
    {"name": "Samsung S25 / S26 / Fold 6-7", "keys": ["galaxy_s25", "galaxy_s26", "galaxy s25", "galaxy s26", "fold_6", "fold_7", "fold 6", "fold 7", "flip_6", "flip 6"], "max_buy": 40000, "market": 56000},
    {"name": "Samsung S23 / S24 Ultra", "keys": ["s23_ultra", "s24_ultra", "s23 ultra", "s24 ultra"], "max_buy": 39000, "market": 55000},
    {"name": "Samsung S23 / S24 / Fold 4-5 / Flip", "keys": ["galaxy_s23", "galaxy_s24", "galaxy s23", "galaxy s24", "z_fold", "z_flip", "z fold", "z flip", "s24 fe", "s23 fe"], "max_buy": 26000, "market": 38000},
    {"name": "Samsung S21 / S22 / Ultra / FE", "keys": ["galaxy_s21", "galaxy_s22", "galaxy s21", "galaxy s22", "s21_fe", "s21 fe", "s22_ultra", "s22 ultra"], "max_buy": 17000, "market": 25000},
    {"name": "OnePlus 15 / 14 / 13", "keys": ["oneplus_15", "oneplus_14", "oneplus_13", "oneplus 15", "oneplus 14", "oneplus 13", "ванплас 15", "ванплас 14", "ванплас 13"], "max_buy": 38000, "market": 52000},
    {"name": "Xiaomi 15 / 16 / Ultra", "keys": ["xiaomi_15", "xiaomi_16", "xiaomi 15", "xiaomi 16", "14_ultra", "14 ultra", "15_ultra", "15 ultra"], "max_buy": 32000, "market": 46000},
    {"name": "Xiaomi 13 / 14 / 13T / 14T Pro", "keys": ["xiaomi_13", "xiaomi_14", "xiaomi 13", "xiaomi 14", "13t_pro", "14t_pro", "13t pro", "14t pro", "14t"], "max_buy": 19000, "market": 28000},
    {"name": "Poco F6 / F7 / X6-X7 Pro / Note 13-14 Pro+", "keys": ["poco_f5", "poco_f6", "poco_f7", "poco f5", "poco f6", "poco f7", "poco x6 pro", "poco_x6_pro", "poco x7 pro", "poco_x7_pro", "note_13_pro", "note 13 pro", "note_14_pro", "note 14 pro"], "max_buy": 14000, "market": 21000},
    {"name": "OnePlus 11 / 12 / Ace 3-5", "keys": ["oneplus_11", "oneplus_12", "oneplus 11", "oneplus 12", "ванплас", "oneplus ace"], "max_buy": 23000, "market": 34000},
    {"name": "Google Pixel 9 / 10 Pro / XL", "keys": ["pixel_9", "pixel_10", "pixel 9", "pixel 10", "пиксель 9", "пиксель 10"], "max_buy": 35000, "market": 48000},
    {"name": "Google Pixel 7 / 8 / Pro", "keys": ["pixel_7", "pixel_8", "pixel 7", "pixel 8", "гугл пиксель", "пиксель 7", "пиксель 8"], "max_buy": 20000, "market": 30000},
    {"name": "Флагман Vivo X100/X200 / Honor Magic / Pura", "keys": ["vivo_x100", "vivo_x200", "vivo x100", "vivo x200", "magic_6", "magic_7", "magic 6", "magic 7", "pura_70", "pura 70", "realme_gt6", "realme gt 6", "nothing_phone_2", "nothing phone 2"], "max_buy": 30000, "market": 44000},
    {"name": "Apple Watch Ultra 1 / 2 / 3", "keys": ["watch_ultra", "watch ultra", "вотч ультра"], "max_buy": 28000, "market": 40000},
    {"name": "Apple Watch S8 / S9 / S10 / S11 / SE", "keys": ["apple_watch_8", "apple_watch_9", "apple_watch_10", "apple watch 8", "apple watch 9", "apple watch 10", "apple watch 11", "apple watch se", "apple_watch_se", "apple watch s8", "apple watch s9", "apple watch s10"], "max_buy": 11000, "market": 16500},
    {"name": "iPad Pro / Air (M1/M2/M3/M4)", "keys": ["ipad_pro", "ipad_air", "ipad pro", "ipad air"], "max_buy": 23000, "market": 37000},
    {"name": "iPad 11 (2025) / 9 / 10", "keys": ["ipad_11", "ipad 11", "айпад 11", "ipad 2025", "ipad_9", "ipad_10", "ipad 9", "ipad 10", "айпад 9", "айпад 10", "ipad 10.2", "ipad 10.9"], "max_buy": 16000, "market": 24000},
    {"name": "Игровой ноутбук RTX 3060-4070", "keys": ["rtx_3060", "rtx_3070", "rtx_4060", "rtx_4070", "rtx 3060", "rtx 3070", "rtx 4060", "rtx 4070", "rtx_4050", "rtx 4050"], "max_buy": 42000, "market": 62000},
    {"name": "Игровой ноутбук GTX / RTX 3050", "keys": ["rtx_3050", "rtx 3050", "gtx_1650", "gtx 1650", "gtx_1660", "gtx 1660"], "max_buy": 28000, "market": 40000},
    {"name": "MacBook Air / Pro (M1/M2/M3/M4)", "keys": ["macbook_air", "macbook_pro", "macbook air", "macbook pro", "макбук", "macbook m1", "macbook m2", "macbook m3"], "max_buy": 38000, "market": 55000},
    {"name": "PlayStation 5 Pro", "keys": ["ps5_pro", "ps5 pro", "playstation 5 pro", "пс5 про"], "max_buy": 42000, "market": 58000},
    {"name": "PlayStation 5 (PS5 Slim / Fat)", "keys": ["playstation_5", "playstation 5", "ps5", "пс5", "плойка 5"], "max_buy": 28000, "market": 38000},
    {"name": "PlayStation 4 Pro", "keys": ["ps4_pro", "ps4 pro", "playstation 4 pro", "пс4 про"], "max_buy": 15000, "market": 22000},
    {"name": "PlayStation 4 Slim / Fat", "keys": ["playstation_4", "playstation 4", "ps4", "пс4", "плойка 4"], "max_buy": 11000, "market": 16000},
    {"name": "Xbox Series X", "keys": ["series_x", "series x"], "max_buy": 26000, "market": 37000},
    {"name": "Xbox Series S", "keys": ["series_s", "series s"], "max_buy": 15000, "market": 22000},
    {"name": "Steam Deck / ROG Ally / Switch 2", "keys": ["steam_deck", "steam deck", "стим дек", "rog_ally", "rog ally", "switch_2", "switch 2", "свитч 2"], "max_buy": 24000, "market": 36000},
    {"name": "Nintendo Switch OLED", "keys": ["switch_oled", "switch oled", "свитч олед"], "max_buy": 14000, "market": 20000},
    {"name": "Nintendo Switch Lite / V2", "keys": ["nintendo_switch", "nintendo switch", "нинтендо свитч", "switch lite", "switch_lite"], "max_buy": 9000, "market": 13500},
    {"name": "Робот-пылесос Xiaomi / Roborock", "keys": ["робот пылесос", "робот-пылесос", "robot_vacuum", "roborock", "роборок", "mijia", "mi robot", "dreame", "пылесос xiaomi", "пылесос сяоми"], "max_buy": 3000, "market": 6500},
    {"name": "Телевизор Smart TV / 4K", "keys": ["телевизор", "televizor", "smart_tv", "smart tv", "смарт тв"], "max_buy": 9000, "market": 15000},
    {"name": "Стайлер / Фен Dyson", "keys": ["dyson", "дайсон", "airwrap", "supersonic"], "max_buy": 12000, "market": 21000},
    {"name": "Кофемашина DeLonghi / Philips", "keys": ["кофемашина", "kofemashina", "delonghi", "делонги", "philips ep"], "max_buy": 10000, "market": 18000},
    {"name": "Видеокарта RTX 3060Ti / 3070 / 4060 / 4070", "keys": ["видеокарта rtx 3060", "видеокарта rtx 3070", "видеокарта rtx 4060", "видеокарта rtx 4070", "geforce rtx 3060", "geforce rtx 3070", "geforce rtx 4060"], "max_buy": 18000, "market": 27000},
    {"name": "Видеокарта RTX 2060 / 3050 / 1660S / RX 6600", "keys": ["rtx_2060", "rtx 2060", "gtx_1660_super", "1660 super", "1660 ti", "rx_6600", "rx 6600"], "max_buy": 9500, "market": 14500},
    {"name": "Игровой монитор 144Hz / 165Hz / 240Hz", "keys": ["144гц", "144hz", "165гц", "165hz", "240гц", "240hz", "игровой монитор", "igrovoy_monitor"], "max_buy": 7500, "market": 13000},
    {"name": "Яндекс Станция Макс / Миди / 2 / Дуо", "keys": ["яндекс станция макс", "яндекс станция 2", "яндекс станция миди", "станция макс", "stantsiya_maks", "станция дуо"], "max_buy": 6500, "market": 12000},
    {"name": "Экшн-камера GoPro 9-13 / DJI Osmo", "keys": ["gopro_9", "gopro_10", "gopro_11", "gopro_12", "gopro_13", "gopro 9", "gopro 10", "gopro 11", "gopro 12", "gopro 13", "osmo_action", "osmo pocket"], "max_buy": 12000, "market": 20000},
    {"name": "Колонка JBL Charge / Xtreme / PartyBox", "keys": ["jbl_charge", "jbl charge", "jbl_xtreme", "jbl xtreme", "jbl_partybox", "jbl partybox"], "max_buy": 5500, "market": 10000},
]

OTHER_CITIES = [
    "калининград", "тагил", "симферополь", "доставка из", "москва", "спб",
    "екатеринбург", "краснодар", "самара", "уфа", "нижний новгород", "пермь", "воронеж"
]

TRASH_WORDS = [
    "чехол", "стекло", "кабель", "коробка", "аренда", "скупка", "выкуп", "копия", "реплика",
    "муляж", "кастом", "кастомные", "kastom", "пульт", "кронштейн", "холодильник", "стиральная",
    "клавиатура", "мышь", "стилус", "airpods", "ремонт", "запчасти", "обмен", "рассрочка",
    "шлем", "vr2", "vr 2", "ps vr", "диск", "подставка", "док-станция", "зарядная станция", "аккаунт", "прокат",
    "щетка", "щётка", "фильтр", "тряпка", "салфетка", "насадка", "контейнер", "бак для", "колесо для",
    "моторчик", "лидар", "кинескопный", "пузатый", "приставка для тв", "тв приставка", "антенна", "ремешок", "корпус для"
]

SCAM_WORDS = [
    "предоплата", "icloud", "заблокирован", "мдм", "mdm", "рсим", "r-sim", "нет фейс", "без фейс",
    "face id не", "разбит", "трещина", "на запчасти", "пятно", "полоса", "выгорание", "демо", "demo",
    "донор", "разбита матрица", "не включается", "сломан экран", "битый экран", "в лизинг"
]

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

# ================= 🔥 МНОГОПОТОЧНОЕ ЯДРО ЗАПРОСОВ (ОПТИМИЗИРОВАНО) =================
def _sync_worker_fetch(url: str, proxy: str, headers: dict):
    resp = cffi_requests.get(
        url,
        headers=headers,
        proxies={"http": proxy, "https": proxy},
        impersonate="chrome",
        timeout=(2.5, 3.5),
    )
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

    # 🔥 Проверяем до 5 прокси за раз с быстрым таймаутом
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
                logging.info(f"📄 Успех на порту {port_str}! Найдено карточек Казани: {len(unique_links)}")
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
    state.last_activity_time = time.time()

    clean_html, unique_links = await fetch_smart(url)

    if not clean_html or not unique_links:
        logging.warning(f"❌ Не удалось пробить категорию '{cat_name}' в этом проходе, переключаемся...")
        return

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
            logging.info(f"⏳ Пауза {pause_sec} сек перед следующей категорией...")
            
            for _ in range(pause_sec):
                state.last_activity_time = time.time()
                if not state.is_scanning or not state.active_chat_id:
                    break
                await asyncio.sleep(1)
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            state.last_error = str(e)[:120]
            logging.error(f"⚠️ Ошибка в фоновом сканере: {e}")
            await asyncio.sleep(5)

# ================= 🛡️ АППАРАТНЫЙ КОНТРОЛЬ ЯДРА (HARDWARE THREAD WATCHDOG) =================
def hardware_kernel_watchdog():
    while True:
        time.sleep(15)
        if not state.is_scanning:
            state.last_activity_time = time.time()
            continue

        idle_sec = time.time() - state.last_activity_time
        if idle_sec > 90:
            logging.critical(f"🚨 АППАРАТНЫЙ WATCHDOG: Ядро не отвечает {int(idle_sec)} сек! Экстренный перезапуск процесса...")
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
        f"🕵️ <b>Ультра-Снайпер 2026 (Оптимизированное ядро)!</b>\n"
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
        f"🕵️ <b>Сканирование активно!</b> Движок работает на 100 прокси ✨",
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
        "title": "Тестовый iPhone 16 Pro 256 ГБ (Оптимизированное ядро)",
        "image_url": None,
        "description": "Кнопки работают мгновенно!",
    }
    test_rule = {"name": "iPhone 16 Pro / Max", "max_buy": 62000, "market": 79000}
    await send_deal_card(bot=message.bot, chat_id=message.chat.id, details=test_details, price_val=55000, matched_rule=test_rule, full_url="https://www.avito.ru/kazan/telefony")

@dp.message(F.text.contains("Статистика"))
async def show_stats(message: types.Message):
    status = "🟢 Работает (Оптимизированное ядро)" if state.is_scanning else "🔴 На паузе"
    now = time.time()
    active_count = len([p for p in PROXY_LIST if p not in banned_proxies or banned_proxies[p] < now])
    idle_sec = int(now - state.last_activity_time)

    text = (
        f"📊 <b>Статистика Снайпера 2026:</b>\n\n"
        f"Статус: <b>{status}</b>\n"
        f"💓 Пульс ядра: <b>{idle_sec} сек назад</b>\n"
        f"💬 Чат отправки: <code>{state.active_chat_id}</code>\n"
        f"🌐 <b>Последний порт:</b> <code>{state.current_ip}</code>\n"
        f"🟢 Активных прокси: <b>{active_count} / {len(PROXY_LIST)}</b>\n"
        f"🔍 Проверено новых лотов: <b>{state.stats_checked}</b>\n"
        f"🔥 Отправлено выгодных: <b>{state.stats_found}</b>\n"
        f"🛠 Последняя ошибка: <code>{html_lib.escape(state.last_error)}</code>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_kb())

# ================= ВЕБ-СЕРВЕР ДЛЯ RENDER =================
async def handle_ping(request):
    return web.Response(text="Bot is running 24/7 with Optimized Core!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"🌐 Веб-сервер для Render запущен на порту {PORT}")

# ================= ЗАПУСК =================
async def on_startup(bot: Bot):
    init_db()
    wd_thread = threading.Thread(target=hardware_kernel_watchdog, daemon=True, name="HardwareWatchdog")
    wd_thread.start()
    
    asyncio.create_task(background_scanner(bot))
    logging.info("🛡️ Оптимизированный Снайпер 2026 успешно запущен!")

async def main():
    session = AiohttpSession(timeout=30)
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
