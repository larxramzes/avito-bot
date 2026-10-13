import asyncio
import logging
import time
import re
import os
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = "8993905772:AAF8P_kD-rXCVKF1pLwe-0CA4SbFXrwvog4"
MONITORED_URL = "https://m.avito.ru/kazan/bytovaya_elektronika?s=104"

# Улучшенные заголовки под настоящий мобильный браузер Сафари (iPhone)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0"
}

WHITELIST_KEYWORDS = [
    "iphone", "айфон", "ps5", "playstation", "xbox", "nintendo", "steam deck",
    "switch", "ipad", "айпад", "apple watch", "macbook", "макбук", "airpods", "imac",
    "dyson", "дайсон", "samsung", "самсунг", "galaxy", "xiaomi", "poco", "pixel",
    "oneplus", "huawei", "honor", "realme", "infinix", "tecno",
    "видеокарта", "rtx", "gtx", "rx", "ryzen", "intel", "core", "процессор",
    "ноутбук", "asus", "lenovo", "msi", "acer", "hp", "dell", "legion", "rog", "matebook",
    "gopro", "dji", "sony", "canon", "nikon", "fujifilm", "камера", "объектив",
    "vr", "oculus", "meta quest", "vision pro", "pico",
    "jbl", "marshall", "колонки", "наушники", "yandex", "яндекс", "станция", "алиса",
    "робот-пылесос", "dreame", "roborock", "garmin", "гармин", "logitech",
    "телевизор", "тв", "tv", "lg", "smart tv", "смарт тв", "подсветка", "пылесос"
]

BLACKLIST_KEYWORDS = [
    "запчасти", "на разбор", "icloud", "заблокирован", "копия", "реплика", "подделка",
    "донор", "чехол", "стекло", "акб", "коробка", "в ремонте", "залитик", "на запчасти", 
    "без комплекта", "муляж", "коробка от", "разбит", "матрица", "полосы", "пятно", "трещина"
]

MAX_DEAL_PRICES = {
    "iphone 17 pro max": 80000, "iphone 17 pro": 65000, "iphone 17": 50000,
    "iphone 16 pro max": 55000, "iphone 16 pro": 50000, "iphone 16 plus": 40000, "iphone 16": 35000,
    "macbook air m4": 90000, "macbook air m3": 70000, "macbook pro m4": 120000, "macbook pro m3": 100000,
    "apple watch series 10": 28000, "apple watch ultra 3": 60000,
    "iphone 15 pro max": 40000, "iphone 15 pro": 35000, "iphone 15": 30000,
    "iphone 14 pro max": 25000, "iphone 14 pro": 25000, "iphone 14 plus": 20000, "iphone 14": 18000,
    "iphone 13 pro max": 30000, "iphone 13 pro": 28000, "iphone 13": 15000,
    "iphone 12": 10000, "iphone 11": 7000, "iphone se 2022": 4000,
    "ipad 11": 30000, "ipad pro m1": 40000, "ipad air 5": 30000, "ipad air 4": 20000, "ipad mini 6": 23000,
    "macbook air m2": 60000, "macbook air m1": 40000, "macbook pro m2": 75000, "macbook pro m1": 50000,
    "mac mini m2": 35000, "mac mini m1": 25000, "mac studio": 100000,
    "apple watch ultra 2": 45000, "apple watch ultra": 35000, "apple watch series 9": 20000,
    "apple watch series 8": 15000, "apple watch series 7": 11000,
    "airpods max": 25000, "airpods pro 2": 11000, "airpods 4": 8000, "airpods 3": 6000,
    "samsung s26 ultra": 70000, "samsung s26": 50000, "samsung s25 ultra": 70000, "samsung s25": 50000,
    "samsung s24 ultra": 60000, "samsung s24": 40000, "samsung s23 ultra": 45000, "samsung s23": 30000,
    "samsung s22 ultra": 28000, "samsung fold 6": 75000, "samsung fold 5": 55000, "samsung flip 6": 45000,
    "samsung a55": 18000, "samsung a54": 14000,
    "google pixel 10 pro": 75000, "google pixel 9 pro": 60000, "google pixel 8 pro": 40000, "google pixel 8": 28000, "google pixel 7 pro": 22000,
    "xiaomi 14 ultra": 60000, "xiaomi 14": 40000, "xiaomi 13": 25000,
    "poco f6 pro": 28000, "poco f6": 20000, "poco x6 pro": 15000, "redmi note 13 pro+": 16000,
    "oneplus 12": 45000, "oneplus 15": 55000, "oneplus ace 3": 22000,
    "huawei pura 70 ultra": 60000, "huawei pura 70": 40000, "honor magic 6 pro": 55000, "honor 200 pro": 30000,
    "galaxy tab s9 ultra": 60000, "galaxy tab s9": 40000, "galaxy tab s8": 25000, "xiaomi pad 6": 15000,
    "ps5 pro": 55000, "playstation 5 pro": 55000, "ps5": 45000, "playstation 5": 45000, "ps4 pro": 13000,
    "xbox series x": 20000, "xbox series s": 13000,
    "nintendo switch 2": 25000, "nintendo switch oled": 16000,
    "steam deck oled": 38000, "steam deck": 28000,
    "asus rog ally": 35000, "lenovo legion go": 40000,
    "meta quest 3": 32000, "oculus quest 2": 12000, "ps vr2": 28000, "pico 4": 18000,
    "logitech g29": 14000, "logitech g923": 18000,
    "rtx 5090": 180000, "rtx 5080": 110000, "rtx 5070": 65000,
    "rtx 4090": 110000, "rtx 4080 super": 75000, "rtx 4080": 70000, "rtx 4070 ti super": 60000,
    "rtx 4070 ti": 50000, "rtx 4070 super": 45000, "rtx 4070": 38000, "rtx 4060 ti": 25000, "rtx 4060": 18000,
    "rtx 3080 ti": 32000, "rtx 3080": 28000, "rtx 3070 ti": 22000, "rtx 3070": 20000, "rtx 3060 ti": 15000, "rtx 3060": 13000,
    "rx 7900 xtx": 70000, "rx 7900 xt": 55000, "rx 7800 xt": 38000, "rx 7700 xt": 30000, "rx 6700 xt": 18000,
    "ryzen 7 7800x3d": 25000, "ryzen 9 7950x": 35000, "ryzen 5 7500f": 9000,
    "core i9 14900k": 38000, "core i7 14700k": 28000, "core i5 13400f": 11000,
    "lenovo legion": 45000, "asus rog": 45000, "msi katana": 35000, "acer nitro": 30000,
    "huawei matebook 16s": 50000, "huawei matebook d16": 35000, "honor magicbook": 30000,
    "dyson airwrap": 25000, "dyson supersonic": 18000, "dyson v15": 30000, "dyson v12": 22000,
    "roborock s8 pro ultra": 60000, "roborock s8": 30000, "roborock q revo": 40000,
    "dreame l20 ultra": 50000, "dreame l10s ultra": 35000,
    "яндекс станция макс": 16000, "яндекс станция миди": 7000, "яндекс станция 2": 8000, "яндекс станция дуо макс": 25000,
    "jbl partybox 310": 30000, "jbl partybox 110": 18000, "jbl boombox 3": 22000,
    "marshall stanmore 3": 18000, "marshall kilburn 2": 13000, "marshall emberton 2": 7000,
    "sony wh-1000xm5": 20000, "sony wf-1000xm5": 12000,
    "sony a7 iv": 110000, "sony a7 iii": 65000, "sony a6700": 80000,
    "canon r6": 100000, "canon r50": 40000, "fujifilm x-t5": 110000,
    "dji mini 4 pro": 45000, "dji mini 3 pro": 35000, "dji mavic 3": 100000, "dji osmo pocket 3": 35000,
    "gopro hero 12": 22000, "gopro hero 11": 16000,
    "samsung galaxy watch 7": 15000, "samsung galaxy watch ultra": 35000,
    "huawei watch 4 pro": 22000, "garmin fenix 7": 35000, "garmin epix 2": 40000,
    "телевизор lg": 10000, "lg smart": 10000, "lg 43": 10000, "lg 42": 10000, "lg 50": 10000, "lg 55": 10000, "lg 32": 7000,
    "робот пылесос xiaomi": 3000, "пылесос xiaomi": 3000, "roborock": 3000, "xiaomi mi robot": 3000
}

bot = Bot(token=TOKEN)
dp = Dispatcher()

sent_ads_cache = set()
my_chat_id = None
request_lock = asyncio.Lock()

logging.basicConfig(level=logging.INFO)

def extract_price(price_str: str) -> int:
    numbers = re.findall(r'\d+', price_str.replace(" ", "").replace("\xa0", ""))
    return int("".join(numbers)) if numbers else 0

def is_super_profitable(title: str, price_text: str) -> bool:
    title_lower = title.lower()
    for word in BLACKLIST_KEYWORDS:
        if word in title_lower: return False
    if not any(kw in title_lower for kw in WHITELIST_KEYWORDS): return False
    price = extract_price(price_text)
    if price <= 500: return False
        
    for model, max_price in MAX_DEAL_PRICES.items():
        if model in title_lower:
            return price <= max_price
    return price < 15000 

def fetch_avito_requests():
    try:
        session = requests.Session()
        response = session.get(MONITORED_URL, headers=HEADERS, timeout=12)
        if response.status_code != 200: 
            return []
        
        soup = BeautifulSoup(response.text, "html.parser")
        ads = []
        
        # Универсальный поиск ссылок на объявления мобильной версии Авито
        for link in soup.find_all("a", href=True):
            href = link.get("href")
            match = re.search(r'_(\d+)$', href.split("?")[0])
            if match:
                ad_id = match.group(1)
                title_text = link.get("title", "") or link.text.strip()
                if len(title_text) < 4: continue
                
                price = "0 ₽"
                parent = link.find_parent("div")
                if parent:
                    pm = re.search(r'(\d+)[₽]', parent.text.replace("\xa0", "").replace(" ", ""))
                    if pm: price = pm.group(1) + " ₽"
                    
                full_url = href if href.startswith("http") else f"https://m.avito.ru{href}"
                ads.append({"id": ad_id, "title": title_text, "price": price, "url": full_url})
                
        return list({ad["id"]: ad for ad in ads}.values())
    except Exception as e:
        print(f"Error fetching: {e}")
        return []

async def run_check(target_chat_id=None):
    chat_id = target_chat_id or my_chat_id
    if request_lock.locked(): return 0, 0
    async with request_lock:
        ads = await asyncio.to_thread(fetch_avito_requests)
        found_count = 0
        for ad in ads:
            if ad["id"] not in sent_ads_cache:
                sent_ads_cache.add(ad["id"])
                if is_super_profitable(ad["title"], ad["price"]):
                    found_count += 1
                    if chat_id:
                        msg = (f"🔥 <b>Срочный выкуп (маржа 5-10к+)</b>\n\n"
                               f"📦 <b>{ad['title']}</b>\n"
                               f"🏷 <b>Цена:</b> {ad['price']}\n\n"
                               f"🔗 <a href='{ad['url']}'>Быстро смотреть объявление</a>")
                        try:
                            await bot.send_message(chat_id, msg, parse_mode="HTML", disable_web_page_preview=True)
                            await asyncio.sleep(0.5)
                        except: pass
        return len(ads), found_count

async def background_monitor():
    await asyncio.sleep(5)
    while True:
        try:
            if my_chat_id: await run_check(my_chat_id)
        except: pass
        await asyncio.sleep(20)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global my_chat_id
    my_chat_id = message.chat.id
    await message.answer(f"👋 Привет! Бот с обновленной маскировкой запущен ({my_chat_id}). Напиши /check! ⚡️")

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    global my_chat_id
    my_chat_id = message.chat.id
    total, found = await run_check(my_chat_id)
    await message.answer(f"✅ Проверено лотов: {total}. Отобрано топ-вариантов: {found}. 🌸")

async def handle(request):
    return web.Response(text="Bot is running!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    asyncio.create_task(web_server())
    asyncio.create_task(background_monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
