import asyncio
import sqlite3
import re
import random
import os
from aiohttp import web
from curl_cffi import requests as cffi_requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeDefault
from aiogram.exceptions import TelegramNetworkError

TOKEN = "8968394996:AAEpGiADwUoqHZ1KqCA1MllKhFOXOF6OA10"

bot = Bot(token=TOKEN)
dp = Dispatcher()

active_chat_id = 1128246797
is_scanning = True
total_scanned = 0
deals_found = 0

def init_db():
    conn = sqlite3.connect("safe_items.db")
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS seen_ads (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()

init_db()

main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика снайпера"), KeyboardButton(text="💡 Помощь")],
        [KeyboardButton(text="🛑 Остановить сканер"), KeyboardButton(text="🚀 Запустить сканер")]
    ],
    resize_keyboard=True
)

PRICE_RULES = [
    (["iphone_18", "18_pro", "18_promax"], "iPhone 18 Серия", 75000, 110000),
    (["iphone_17", "17_pro", "17_promax"], "iPhone 17 Серия", 65000, 95000),
    (["iphone_16", "16_pro", "16_promax", "16_plus"], "iPhone 16 Серия", 55000, 85000),
    (["iphone_15", "15_pro", "15_promax", "15_plus"], "iPhone 15 Серия", 42000, 72000),
    (["iphone_14", "14_pro", "14_promax", "14_plus"], "iPhone 14 Серия", 30000, 55000),
    (["iphone_13", "13_pro", "13_promax", "13_mini"], "iPhone 13 Серия", 22000, 40000),
    (["iphone_12", "12_pro", "12_promax", "12_mini"], "iPhone 12 Серия", 16000, 30000),
    (["iphone_11", "11_pro", "11_promax"], "iPhone 11 Серия", 12000, 22000),
    (["s26", "galaxy_s26", "ultra_26"], "Samsung Galaxy S26 Ultra", 65000, 95000),
    (["s25", "galaxy_s25", "ultra_25"], "Samsung Galaxy S25 Серия", 52000, 80000),
    (["s24", "s23", "s22", "s21", "ultra", "fold", "flip"], "Samsung Galaxy Флагман", 30000, 65000),
    (["oneplus"], "OnePlus Flagship", 40000, 70000),
    (["ipad"], "iPad / Pro / Air", 25000, 42000),
    (["rtx"], "Игровой ноутбук RTX", 40000, 65000),
    (["macbook"], "MacBook", 38000, 70000),
    (["dualsense", "геймпад"], "Геймпад PS5", 4000, 5800),
    (["пылесос", "xiaomi"], "Робот-пылесос Xiaomi", 3000, 7500),
    (["телевизор"], "Телевизор (Ремонт)", 8000, 15000),
    (["ps5", "playstation_5"], "PlayStation 5", 30000, 41000)
]

TARGETS = [
    ("📱 Смартфоны и Флагманы", "https://m.avito.ru/kazan/telefony?q=iphone+samsung+oneplus&s=104", "telefony"),
    ("💻 Ноутбуки", "https://m.avito.ru/kazan/noutbuki?q=rtx+macbook+gaming&s=104", "noutbuki"),
    ("🍏 Планшеты", "https://m.avito.ru/kazan/planshety_i_elektronnye_knigi?q=ipad+pro+air&s=104", "planshety_i_elektronnye_knigi"),
    ("🎮 Приставки", "https://m.avito.ru/kazan/igry_pristavki_i_programmy?q=ps5+xbox+dualsense&s=104", "igry_pristavki_i_programmy")
]

current_target_idx = 0
TRASH_WORDS = ["чехол", "стекло", "кабель", "зарядка", "коробка", "аренда", "прокат", "скупка", "выкуп", "копия", "реплика", "муляж"]

def evaluate_deal(text_lower, price_val):
    if price_val < 1000:
        return None
    
    if "пылесос" in text_lower and price_val <= 3000:
        return {"tag": "🤖 <b>РОБОТ-ПЫЛЕСОС XIAOMI ДО 3 000 ₽!</b>", "market": "~7 000 ₽", "profit": "+4 000 ₽ 💰"}
    
    if "телевизор" in text_lower and price_val <= 8000:
        return {"tag": "📺 <b>ТЕЛЕВИЗОР ДО 8 000 ₽ (РЕМОНТ)</b>", "market": "~17 000 ₽", "profit": "+9 000 ₽ 💰"}

    for keywords, model_name, max_buy, market_price in PRICE_RULES:
        if any(kw in text_lower for kw in keywords):
            if price_val <= max_buy:
                profit = market_price - price_val
                return {
                    "tag": f"🔥 <b>ТОП-СДЕЛКА ({model_name})</b>",
                    "market": f"~{market_price:,} ₽".replace(",", " "),
                    "profit": f"+{profit:,} ₽ 💰".replace(",", " ")
                }
            return None
    return None

def fetch_avito(cat_name, target_url, cat_path):
    global total_scanned
    ads = []
    try:
        session = cffi_requests.Session(impersonate="chrome120")
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://m.avito.ru/kazan",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "Sec-Ch-Ua-Mobile": "?1",
            "Sec-Ch-Ua-Platform": '"iOS"'
        }
        resp = session.get(target_url, headers=headers, timeout=30)
        print(f"📥 Скан [{cat_name}] | Статус: {resp.status_code} | Размер: {len(resp.text)} байт")

        if resp.status_code != 200:
            return []

        html = resp.text
        ad_ids = re.findall(r'_(\d{8,10})\b', html)
        unique_ids = list(set(ad_ids))
        print(f"✅ Найдено ID товаров: {len(unique_ids)}")

        for ad_id in unique_ids:
            total_scanned += 1
            pos = html.find(ad_id)
            if pos == -1:
                continue
                
            snippet = html[max(0, pos - 400):pos + 600]
            snippet_lower = snippet.lower()
            
            m_slug = re.search(r'([a-zA-Zа-яА-Я0-9_-]+?_' + ad_id + ')', snippet)
            slug = m_slug.group(1).lower() if m_slug else f"item_{ad_id}"
            title = slug.replace("_", " ").upper()
            
            full_url = f"https://m.avito.ru/kazan/{cat_path}/{slug}"

            if any(w in slug or w in snippet_lower for w in TRASH_WORDS):
                continue

            price_val = 0
            p_match = re.search(r'(?:"price"\s*:\s*(\d{3,7})|(\d[\d\s\xa0]{2,7})\s*[₽рp])', snippet)
            if p_match:
                raw_str = p_match.group(1) or p_match.group(2)
                raw_p = re.sub(r'\D', '', raw_str)
                if raw_p:
                    price_val = int(raw_p)

            if price_val > 0:
                verdict = evaluate_deal(snippet_lower, price_val)
                if not verdict:
                    continue
                price_display = f"{price_val:,} ₽".replace(",", " ")
            else:
                continue

            ads.append({
                "id": ad_id,
                "category": cat_name,
                "title": title if len(title) > 5 else f"Лот #{ad_id}",
                "price": price_display,
                "tag": verdict["tag"],
                "market": verdict["market"],
                "profit": verdict["profit"],
                "url": full_url
            })
            if len(ads) >= 5:
                break
    except Exception as e:
        print(f"⚠️ Ошибка сети: {e}")
    return ads

async def background_scanner():
    global active_chat_id, is_scanning, current_target_idx, deals_found, total_scanned
    print("🚀 Золотой Каскадный Снайпер запущен!")
    while is_scanning:
        if active_chat_id:
            try:
                cat_name, url, cat_path = TARGETS[current_target_idx]
                current_target_idx = (current_target_idx + 1) % len(TARGETS)
                
                ads = await asyncio.to_thread(fetch_avito, cat_name, url, cat_path)
                if ads:
                    conn = sqlite3.connect("safe_items.db")
                    cursor = conn.cursor()
                    for ad in ads:
                        cursor.execute("SELECT id FROM seen_ads WHERE id = ?", (ad["id"],))
                        if not cursor.fetchone():
                            cursor.execute("INSERT INTO seen_ads (id) VALUES (?)", (ad["id"],))
                            conn.commit()
                            deals_found += 1
                            print(f"💎 Найдена годнота: {ad['title']} за {ad['price']}")
                            
                            kb = InlineKeyboardMarkup(
                                inline_keyboard=[[InlineKeyboardButton(text="🚀 Забрать лот на Авито", url=ad["url"])]]
                            )
                            caption = (
                                f"┏ {ad['tag']}\n"
                                f"┣ 📂 Категория: <b>{ad['category']}</b>\n"
                                f"┗━━━━━━━━━━━━━━━━━━━\n\n"
                                f"📦 <b>{ad['title']}</b>\n\n"
                                f"💵 <b>Цена:</b> <code>{ad['price']}</code>\n"
                                f"📊 <b>Рынок:</b> {ad['market']}\n"
                                f"📈 <b>Профит:</b> <b>{ad['profit']}</b>\n\n"
                                f"📉 <i>Проверено: {total_scanned} | Находок: {deals_found}</i>\n"
                                f"🔗 <a href='{ad['url']}'>Ссылка на объявление</a>"
                            )
                            try:
                                await bot.send_message(active_chat_id, caption, parse_mode="HTML", reply_markup=kb)
                                await asyncio.sleep(1)
                            except Exception:
                                pass
                    conn.close()
            except Exception as e:
                print(f"⚠️ Ошибка цикла: {e}")
        wait_t = random.randint(50, 75)
        print(f"⏳ Безопасная пауза каскада {wait_t} сек... (Просмотрено: {total_scanned})")
        await asyncio.sleep(wait_t)

async def handle_ping(request):
    return web.Response(text="Bot is running and sniping 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Веб-сервер запущен на порту {port}")

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="🚀 Запустить"),
        BotCommand(command="stats", description="📊 Статистика"),
        BotCommand(command="help", description="💡 Помощь"),
        BotCommand(command="stop", description="🛑 Пауза")
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global active_chat_id, is_scanning
    active_chat_id = message.chat.id
    is_scanning = True
    await message.answer("💎 <b>Снайпер запущен!</b> Мониторинг в облаке активен.", parse_mode="HTML", reply_markup=main_menu_keyboard)

@dp.message(lambda message: message.text == "📊 Статистика снайпера")
async def btn_stats_text(message: types.Message):
    await message.answer(f"📊 <b>Статистика:</b>\nПросмотрено: <code>{total_scanned}</code>\nНайдено: <code>{deals_found}</code>", parse_mode="HTML", reply_markup=main_menu_keyboard)

@dp.message(lambda message: message.text == "💡 Помощь")
async def btn_help_text(message: types.Message):
    await message.answer("💡 Бот круглосуточно сканирует Авито в Казани.", parse_mode="HTML", reply_markup=main_menu_keyboard)

@dp.message(lambda message: message.text == "🛑 Остановить сканер")
async def btn_stop_text(message: types.Message):
    global active_chat_id, is_scanning
    is_scanning = False
    active_chat_id = None
    await message.answer("🛑 Пауза.", parse_mode="HTML", reply_markup=main_menu_keyboard)

@dp.message(lambda message: message.text == "🚀 Запустить сканер")
async def btn_start_text(message: types.Message):
    global active_chat_id, is_scanning
    active_chat_id = message.chat.id
    is_scanning = True
    await message.answer("🚀 Погнали дальше!", parse_mode="HTML", reply_markup=main_menu_keyboard)

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    await btn_stats_text(message)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await cmd_help(message)

@dp.message(Command("stop"))
async def cmd_stop(message: types.Message):
    global active_chat_id, is_scanning
    is_scanning = False
    active_chat_id = None

async def main():
    await set_bot_commands()
    await start_web_server()
    asyncio.create_task(background_scanner())
    
    while True:
        try:
            await dp.start_polling(bot)
        except TelegramNetworkError:
            print("⚠️ Обрыв связи с Telegram, переподключение через 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"⚠️ Ошибка поллинга: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
