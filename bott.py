import asyncio
import logging
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive"
}

WHITELIST_KEYWORDS = [
    "iphone", "айфон", "ps5", "playstation", "xbox", "nintendo", "steam deck",
    "switch", "ipad", "айпад", "apple watch", "macbook", "макбук", "airpods", "imac",
    "dyson", "дайсон", "samsung", "самсунг", "galaxy", "xiaomi", "poco", "pixel",
    "oneplus", "huawei", "honor", "realme", "infinix", "tecno",
    "видеокарта", "rtx", "gtx", "rx", "ryzen", "intel", "core", "процессор",
    "ноутбук", "asus", "lenovo", "msi", "acer", "hp", "dell", "legion", "rog",
    "gopro", "dji", "sony", "canon", "nikon", "fujifilm", "камера", "объектив",
    "jbl", "marshall", "колонки", "наушники", "yandex", "яндекс", "станция", "алиса",
    "робот-пылесос", "dreame", "roborock", "garmin", "logitech", "телевизор", "тв", "lg"
]

BLACKLIST_KEYWORDS = [
    "запчасти", "на разбор", "icloud", "заблокирован", "копия", "реплика", "подделка",
    "донор", "чехол", "стекло", "акб", "коробка", "в ремонте", "залитик", "на запчасти", 
    "без комплекта", "муляж", "коробка от", "разбит", "матрица", "полосы", "пятно", "трещина"
]

MAX_DEAL_PRICES = {
    "iphone 16 pro max": 55000, "iphone 16 pro": 50000, "iphone 16": 35000,
    "iphone 15 pro max": 40000, "iphone 15 pro": 35000, "iphone 15": 30000,
    "iphone 14 pro max": 25000, "iphone 14 pro": 25000, "iphone 14 plus": 20000, "iphone 14": 18000,
    "iphone 13 pro max": 30000, "iphone 13 pro": 28000, "iphone 13": 15000, "iphone 12": 10000,
    "ps5": 45000, "playstation 5": 45000, "xbox series x": 20000, "steam deck": 28000
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
        response = requests.get(MONITORED_URL, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return []
            
        soup = BeautifulSoup(response.text, "html.parser")
        ads = []
        
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
        await asyncio.sleep(30)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global my_chat_id
    my_chat_id = message.chat.id
    await message.answer(f"👋 Бот запущен! ID: {my_chat_id}. Нажми /check ⚡️")

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
