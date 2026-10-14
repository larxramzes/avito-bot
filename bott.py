import asyncio
import logging
import time
import re
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

# --- НАСТРОЙКИ ---
TOKEN = "8993905772:AAF8P_kD-rXCVKF1pLwe-0CA4SbFXrwvog4"

MONITORED_URL = "https://www.avito.ru/kazan/telefony?s=104"

# Ключевые слова ликвидной техники
WHITELIST_KEYWORDS = [
    "iphone", "айфон", "ps5", "playstation", "ipad", "айпад", 
    "airpods", "macbook", "макбук", "nintendo", "dyson", 
    "видеокарта", "rtx", "samsung", "самсунг"
]

# Стоп-слова (хлам и запчасти исключаем сразу)
BLACKLIST_KEYWORDS = [
    "запчасти", "на разбор", "icloud", "заблокирован", 
    "не работает", "разбит", "донор", "сломан", "чехол", "стекло", "акб", "коробка"
]

# Примерные рыночные цены в Казани для оценки маржи (от какой цены есть смысл перепродавать)
# Если цена товара НИЖЕ этого порога, значит потенциальная выгода как раз 10-15к+!
MAX_DEAL_PRICES = {
    "iphone 15": 55000,
    "iphone 14": 42000,
    "iphone 13": 33000,
    "iphone 12": 23000,
    "iphone 11": 17000,
    "ps5": 38000,
    "playstation 5": 38000,
    "dyson": 25000,
    "rtx": 25000
}

bot = Bot(token=TOKEN)
dp = Dispatcher()

sent_ads_cache = set()
driver = None
my_chat_id = None

logging.basicConfig(level=logging.INFO)

def init_browser():
    global driver
    if driver is not None:
        try:
            _ = driver.current_url
            return driver
        except Exception:
            driver = None
        
    options = Options()
    options.page_load_strategy = 'eager'
    options.add_argument(r"--user-data-dir=C:\avito_chrome_profile")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def extract_price(price_str: str) -> int:
    """Извлекает числовое значение цены из строки"""
    numbers = re.findall(r'\d+', price_str.replace(" ", "").replace("\xa0", ""))
    if numbers:
        return int("".join(numbers))
    return 0

def is_super_profitable(title: str, price_text: str) -> bool:
    """Проверяет, является ли предложение ультра-выгодным (маржа от 10-15к)"""
    title_lower = title.lower()
    
    # 1. Проверяем стоп-слова
    for word in BLACKLIST_KEYWORDS:
        if word in title_lower:
            return False
            
    # 2. Проверяем наличие ликвидных ключевых слов
    has_keyword = any(kw in title_lower for kw in WHITELIST_KEYWORDS)
    if not has_keyword:
        return False
        
    # 3. Извлекаем цену
    price = extract_price(price_text)
    if price <= 3000:  # Отсекаем подозрительно дешевый мусор или чехлы
        return False
        
    # 4. Проверяем по таблице маржинальности
    for model, max_price in MAX_DEAL_PRICES.items():
        if model in title_lower:
            if price <= max_price:
                return True # Цена ниже рынка на нужную дельту!
            else:
                return False # Дорого, перекупщику неинтересно
                
    # Если модель не из топа, но цена очень низкая (менее 15 000 руб. за электронику) — тоже пропускаем как потенциальный топ
    return price < 15000

def fetch_avito_persistent():
    global driver
    try:
        browser = init_browser()
        browser.get(MONITORED_URL)
        time.sleep(5)
        
        while "Доступ ограничен" in browser.page_source or "проблема с IP" in browser.page_source:
            logging.info("⚠️ На экране капча! Реши её в окне браузера... Жду 15 секунд...")
            time.sleep(15)
            
        html = browser.page_source
        soup = BeautifulSoup(html, "html.parser")
        ads = []
        
        items = soup.find_all("div", attrs={"data-marker": "item"})
        if not items:
            items = soup.select("div[class*='iva-item-root']")
            
        for item in items:
            try:
                title_tag = item.find("h3", attrs={"itemprop": "name"}) or item.find("a", attrs={"data-marker": "item-title"})
                if not title_tag:
                    continue
                title = title_tag.text.strip()
                
                link_tag = item.find("a", attrs={"data-marker": "item-title"})
                if not link_tag:
                    link_tag = item.find("a", href=True)
                if not link_tag:
                    continue
                    
                href = link_tag["href"]
                ad_url = href if href.startswith("http") else f"https://www.avito.ru{href}"
                ad_url = ad_url.split("?")[0]
                
                price_tag = item.find("meta", attrs={"itemprop": "price"})
                if price_tag and price_tag.get("content"):
                    price = price_tag["content"] + " ₽"
                else:
                    price_text = item.find("strong")
                    price = price_text.text.strip() if price_text else "Уточняйте"
                
                ad_id = ad_url.split("_")[-1]
                
                ads.append({
                    "id": ad_id,
                    "title": title,
                    "price": price,
                    "url": ad_url
                })
            except Exception:
                continue
                
        return ads
    except Exception as e:
        logging.info(f"Ошибка браузера: {e}")
        return []

async def run_check(target_chat_id=None):
    global my_chat_id
    chat_id = target_chat_id or my_chat_id
    
    logging.info("💎 Ищу супер-выгодные предложения с маржой от 10-15к...")
    ads = await asyncio.to_thread(fetch_avito_persistent)
    logging.info(f"Всего проанализировано объявлений: {len(ads)}")
    
    found_count = 0
    for ad in ads:
        if ad["id"] not in sent_ads_cache:
            sent_ads_cache.add(ad["id"])
            if is_super_profitable(ad["title"], ad["price"]):
                found_count += 1
                if chat_id:
                    message_text = (
                        f"💰 <b>Топ-выкуп! Потенциал маржи 10-15к+!</b>\n\n"
                        f"📦 <b>{ad['title']}</b>\n"
                        f"🏷 <b>Цена:</b> {ad['price']}\n\n"
                        f"🔗 <a href='{ad['url']}'>Быстро смотреть объявление</a>"
                    )
                    try:
                        await bot.send_message(
                            chat_id, 
                            message_text, 
                            parse_mode="HTML",
                            disable_web_page_preview=True
                        )
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logging.info(f"Ошибка отправки: {e}")
    return len(ads), found_count

async def background_monitor():
    await asyncio.sleep(5)
    logging.info("🚀 Монитор маржинальных лотов запущен!")
    while True:
        try:
            if my_chat_id:
                await run_check(my_chat_id)
        except Exception as e:
            logging.info(f"Ошибка в цикле: {e}")
        await asyncio.sleep(300)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global my_chat_id
    my_chat_id = message.chat.id
    await message.answer(f"👋 Привет, перекуп! ID сохранен ({my_chat_id}). Напиши /check, чтобы найти горячие лоты с маржой от 10к! 🔥")

@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    global my_chat_id
    my_chat_id = message.chat.id
    await message.answer("💎 Сканирую рынок Казани на предмет заниженных цен...")
    total, found = await run_check(my_chat_id)
    await message.answer(f"✅ Анализ завершен! Проверено лотов: {total}. Отобрано супер-выгодных: {found}. 🌸")

async def main():
    asyncio.create_task(background_monitor())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
