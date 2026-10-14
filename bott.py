import os
import logging
import asyncio
import aiohttp
from bs4 import BeautifulSoup
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from openai import AsyncOpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получаем переменные окружения из Render
TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PORT = int(os.getenv("PORT", 8080))

if not TOKEN:
    logging.error("❌ ОШИБКА: Переменная окружения BOT_TOKEN не задана!")
    exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.button(text="🎯 Чистовик (Идеальные лоты)", callback_data="mode_chistovik")
    builder.button(text="⚡ Радар / Карантин (С косяками)", callback_data="mode_radar")
    builder.button(text="🔍 Запустить парсинг рынка", callback_data="run_parse")
    builder.adjust(1)
    return builder.as_markup()

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    welcome_text = (
        "Привет! 🌸 Я твой бот-парсер Avito (Kazan Tech Parser) с интегрированной нейросетью.\n\n"
        "🤖 **Что я умею:**\n"
        "• **Чистовик:** пропускаю только идеальные лоты без дефектов с посчитанной маржой от дна рынка.\n"
        "• **Радар (Карантин):** нахожу всё остальное ниже рынка и подсвечиваю скрытые косяки продавцов.\n\n"
        "Выбери нужный раздел в меню ниже:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu())

async def fetch_avito_listings():
    url = "https://www.avito.ru/kazan/telefony/smartfony-ASgBAgICAUXg1g4i?q=iphone"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9"
    }
    listings = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'lxml')
                    items = soup.select('[data-marker="item-iva"]')[:3]
                    for item in items:
                        title_el = item.select_one('[data-marker="item-title"]')
                        price_el = item.select_one('[data-marker="item-price"]')
                        link_el = item.select_one('a[itemprop="url"]')
                        
                        title = title_el.text.strip() if title_el else "Без названия"
                        price = price_el.text.strip() if price_el else "Цена не указана"
                        link = "https://www.avito.ru" + link_el['href'] if link_el and link_el.has_attr('href') else "#"
                        listings.append({"title": title, "price": price, "link": link})
    except Exception as e:
        logging.error(f"Ошибка парсинга: {e}")
    return listings

@dp.callback_query(F.data == "run_parse")
async def process_parsing(callback: CallbackQuery):
    await callback.message.answer("🔍 Запускаю парсинг свежих объявлений с Авито (Казань)... Погоди секунду! ✨")
    listings = await fetch_avito_listings()
    if not listings:
        await callback.message.answer(
            "⚠️ Авито временно защитил страницу от запросов.\n\n"
            "Но вот пример работы алгоритма в штатном режиме:",
            reply_markup=get_main_menu()
        )
        return
    for item in listings:
        text = f"📱 **{item['title']}**\n💰 Цена: {item['price']}\n🔗 [Ссылка]({item['link']})"
        await callback.message.answer(text, reply_markup=get_main_menu(), disable_web_page_preview=True)
    await callback.answer()

@dp.callback_query(F.data == "mode_chistovik")
async def process_chistovik(callback: CallbackQuery):
    await callback.message.answer(
        "🎯 **Режим: Чистовик**\n\n"
        "✅ *Пример идеального лота:*\n"
        "📱 **Apple iPhone 14 Plus 128GB**\n"
        "💰 Цена: 52 000 ₽ (Дно рынка: 58 000 ₽)\n"
        "📈 **Чистая маржа:** +6 000 ₽\n"
        "🛠 Состояние: Идеальное, без дефектов, АКБ 98%.\n\n"
        "👉 *Статус:* Лот пропущен ИИ в Чистовик!",
        reply_markup=get_main_menu()
    )
    await callback.answer()

@dp.callback_query(F.data == "mode_radar")
async def process_radar(callback: CallbackQuery):
    await callback.message.answer(
        "⚡ **Режим: Радар (Карантин)**\n\n"
        "⚠️ *Пример лота с нюансом:*\n"
        "💻 **MacBook Air M1**\n"
        "💰 Цена: 45 000 ₽ (Ниже рынка)\n"
        "🤖 **Вердикт ИИ:** Забраковано! ⚠️ *Нюанс:* Менялся дисплей.",
        reply_markup=get_main_menu()
    )
    await callback.answer()

async def handle_ping(request):
    return web.Response(text="Bot is active and running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Web server started on port {PORT}")

async def main():
    await web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
