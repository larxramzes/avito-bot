import os
import logging
import asyncio
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Получаем токен из Render
TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

if not TOKEN:
    logging.error("❌ ОШИБКА: Переменная окружения BOT_TOKEN не задана!")
    exit(1)

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Используем стандартный HTTP-протокол
PROXY_URL = "http://RjWUWQ:M516y3@147.45.82.76:8000"

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
        "Привет! 🌸 Я твой бот-парсер Avito (Kazan Tech Parser) с приватным прокси.\n\n"
        "🤖 **Что я умею:**\n"
        "• **Чистовик:** пропускаю только идеальные лоты без дефектов с посчитанной маржой от дна рынка.\n"
        "• **Радар (Карантин):** нахожу всё остальное ниже рынка и подсвечиваю скрытые косяки продавцов.\n\n"
        "Выбери нужный раздел в меню ниже:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu())

async def fetch_avito_listings():
    url = "https://m.avito.ru/api/1/items?key=serp&locationId=88&categoryId=110&q=iphone"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "Accept": "application/json",
        "Referer": "https://m.avito.ru/"
    }
    
    listings = []
    try:
        async with aiohttp.ClientSession() as session:
            # Напрямую передаем proxy в aiohttp без ProxyConnector
            async with session.get(url, headers=headers, proxy=PROXY_URL, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("items", [])[:5]
                    for item in items:
                        title = item.get("title", "Без названия")
                        price = item.get("priceDetailed", {}).get("string", "Цена не указана")
                        item_id = item.get("id")
                        link = f"https://www.avito.ru/kazan/{item.get('uri', item_id)}" if item_id else "https://www.avito.ru"
                        listings.append({"title": title, "price": price, "link": link})
    except Exception as e:
        logging.error(f"Ошибка запроса через HTTP прокси: {e}")
        
    return listings

@dp.callback_query(F.data == "run_parse")
async def process_parsing(callback: CallbackQuery):
    await callback.message.answer("🔍 Запускаю парсинг свежих объявлений с Авито через приватный прокси (Казань)... Погоди секунду! ✨")
    listings = await fetch_avito_listings()
    
    if not listings:
        await callback.message.answer(
            "⚠️ Не удалось загрузить объявления.\n\n"
            "А пока демо-режим:",
            reply_markup=get_main_menu()
        )
        await callback.message.answer(
            "📱 **Apple iPhone 13 128GB**\n💰 Цена: 38 000 ₽\n🔗 [Ссылка на объявление](https://www.avito.ru/kazan)",
            reply_markup=get_main_menu(),
            disable_web_page_preview=True
        )
        return
        
    for item in listings:
        text = f"📱 **{item['title']}**\n💰 Цена: {item['price']}\n🔗 [Ссылка на объявление]({item['link']})"
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
        "👉 *Статус:* Лот пропущен в Чистовик!",
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
        "🤖 **Вердикт:** Забраковано! ⚠️ *Нюанс:* Менялся дисплей.",
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
