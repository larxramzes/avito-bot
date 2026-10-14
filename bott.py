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

# Бесплатный публичный прокси (можно будет при желании заменить на другой)
# Если этот прокси перестанет отвечать, запросы временно пойдут напрямую
PROXIES = {
    "http": "http://190.61.88.147:8080",
    "https": "http://190.61.88.147:8080"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://m.avito.ru/",
    "Connection": "keep-alive"
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
    "meta quest 3": 32000, "oculus quest 2": 12000, "ps vr2": 28000, "pico 4": 
