import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
import hashlib
import shutil

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup,
    CallbackQuery, BufferedInputFile, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

# ==================== ЗАГРУЗКА КОНФИГУРАЦИИ ====================
load_dotenv()

# Конфигурация из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(id_.strip()) for id_ in os.getenv("ADMIN_IDS", "").split(",") if id_.strip()]
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не указан в .env файле!")

# Настройка логирования
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Файлы для хранения данных
LOCATIONS_FILE = "data/locations.json"
FEEDBACKS_FILE = "data/feedbacks.json"
COORDINATES_FILE = "data/map_coordinates.json"

# Папки для карт
MAP_IMAGE = "images/school_map.png"
MAP_CACHE_DIR = "images/cache/"
GENERATED_MAPS_DIR = "images/generated/"

# Время кэширования карты в секундах (5 минут)
MAP_CACHE_TIME = 300

# Шрифты для карты (пробуем разные варианты)
FONT_PATHS = [
    "arial.ttf",
    "arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc"
]

# Создаем директории для данных, если их нет
os.makedirs("data", exist_ok=True)
os.makedirs(MAP_CACHE_DIR, exist_ok=True)
os.makedirs(GENERATED_MAPS_DIR, exist_ok=True)

# Инициализация бота с правильными параметрами для aiogram 3.7+
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher(storage=MemoryStorage())

# ==================== МОДЕЛИ ДАННЫХ ====================
class FeedbackStates(StatesGroup):
    """Состояния для FSM"""
    choosing_type = State()
    choosing_location = State()
    entering_text = State()

# ==================== УТИЛИТЫ ДЛЯ РАБОТЫ С ДАННЫХ ====================
def load_json(file_path: str, default: list = None) -> List:
    """Загрузка данных из JSON файла"""
    if default is None:
        default = []
    if not os.path.exists(file_path):
        save_json(file_path, default)
        return default
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка при чтении JSON {file_path}: {e}")
        return default
    except Exception as e:
        logger.error(f"Ошибка при загрузке {file_path}: {e}")
        return default

def save_json(file_path: str, data: list) -> None:
    """Сохранение данных в JSON файл"""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"Ошибка при сохранении {file_path}: {e}")

def get_locations() -> List[Dict]:
    """Получить список локаций"""
    return load_json(LOCATIONS_FILE, [
        {"id": 1, "name": "Главный корпус", "emoji": "🏫", "description": "Основное здание школы"},
        {"id": 2, "name": "Столовая", "emoji": "🍽", "description": "Помещение для приема пищи"},
        {"id": 3, "name": "Спортивный зал", "emoji": "⚽", "description": "Зал для занятий спортом"},
        {"id": 4, "name": "Библиотека", "emoji": "📚", "description": "Школьная библиотека"},
        {"id": 5, "name": "Компьютерный класс", "emoji": "🖥️", "description": "Класс с компьютерами"},
        {"id": 6, "name": "Школьный двор", "emoji": "🌳", "description": "Территория вокруг школы"},
        {"id": 7, "name": "Раздевалки", "emoji": "🚿", "description": "Раздевалки и душевые"},
        {"id": 8, "name": "Кабинеты химии/физики", "emoji": "🧪", "description": "Специализированные кабинеты"},
        {"id": 9, "name": "Актовый зал", "emoji": "🎭", "description": "Зал для мероприятий"},
        {"id": 10, "name": "Коридоры и рекреации", "emoji": "🚪", "description": "Общие помещения"},
        {"id": 11, "name": "Турникеты", "emoji": "🎫", "description": "Входные турникеты и система контроля доступа"},
        {"id": 12, "name": "Бассейн", "emoji": "🏊", "description": "Школьный бассейн и раздевалки при нем"}
    ])

def get_feedbacks() -> List[Dict]:
    """Получить все жалобы и предложения"""
    return load_json(FEEDBACKS_FILE, [])

def save_feedback(feedback_type: str, location_id: int, text: str, user_id: Optional[int] = None, username: Optional[str] = None) -> None:
    """Сохранить новое обращение"""
    try:
        feedbacks = get_feedbacks()
        
        # Генерируем анонимный ID для пользователя (только для публичный просмотра)
        public_user_id = f"user_{len(feedbacks) + 1000}"
        
        new_feedback = {
            "id": len(feedbacks) + 1,
            "type": feedback_type,
            "type_emoji": "🔴" if feedback_type == "complaint" else "🟢",
            "type_text": "Жалоба" if feedback_type == "complaint" else "Предложение",
            "location_id": location_id,
            "text": text,
            "real_user_id": user_id,
            "real_username": username,
            "public_user_id": public_user_id,
            "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "timestamp": datetime.now().isoformat(),
            "status": "новое"
        }
        
        feedbacks.append(new_feedback)
        save_json(FEEDBACKS_FILE, feedbacks)
        logger.info(f"Сохранено обращение #{new_feedback['id']} от пользователя {user_id} ({username})")
        
        # ОЧИЩАЕМ КЭШ КАРТЫ при сохранении нового обращения
        cleanup_cache_completely()
        
    except Exception as e:
        logger.error(f"Ошибка при сохранении обращения: {e}")

def get_feedback_counts() -> Dict[int, Dict[str, int]]:
    """Получить количество жалоб и предложений по локациям"""
    feedbacks = get_feedbacks()
    counts = {}
    
    for feedback in feedbacks:
        loc_id = feedback["location_id"]
        if loc_id not in counts:
            counts[loc_id] = {"complaints": 0, "suggestions": 0}
        
        if feedback["type"] == "complaint":
            counts[loc_id]["complaints"] += 1
        else:
            counts[loc_id]["suggestions"] += 1
    
    return counts

def get_location_name(location_id: int) -> str:
    """Получить название локации по ID"""
    locations = get_locations()
    for loc in locations:
        if loc["id"] == location_id:
            return f"{loc['emoji']} {loc['name']}"
    return f"📍 Локация #{location_id}"

def get_location_full_info(location_id: int) -> Dict:
    """Получить полную информацию о локации"""
    locations = get_locations()
    for loc in locations:
        if loc["id"] == location_id:
            return loc
    return {"id": location_id, "name": f"Локация #{location_id}", "emoji": "📍", "description": "Неизвестная локация"}

def anonymize_text(text: str, max_length: int = 200) -> str:
    """Анонимизировать текст, убирая возможные личные данные"""
    import re
    text = re.sub(r'@\w+', '[пользователь]', text)
    text = re.sub(r'https?://\S+', '[ссылка]', text)
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text

# ==================== УТИЛИТЫ ДЛЯ РАБОТЫ С КАРТОЙ ====================
def load_coordinates() -> Dict:
    """Загрузить координаты для карты"""
    default_coordinates = {
        "1": {"x": 400, "y": 300, "name": "Главный корпус"},
        "2": {"x": 200, "y": 200, "name": "Столовая"},
        "3": {"x": 600, "y": 200, "name": "Спортзал"},
        "4": {"x": 150, "y": 400, "name": "Библиотека"},
        "5": {"x": 650, "y": 400, "name": "Компьютерный класс"},
        "6": {"x": 400, "y": 100, "name": "Школьный двор"},
        "7": {"x": 700, "y": 300, "name": "Раздевалки"},
        "8": {"x": 300, "y": 500, "name": "Кабинеты химии/физики"},
        "9": {"x": 500, "y": 500, "name": "Актовый зал"},
        "10": {"x": 400, "y": 400, "name": "Коридоры"},
        "11": {"x": 100, "y": 300, "name": "Турникеты"},
        "12": {"x": 600, "y": 100, "name": "Бассейн"}
    }
    
    if not os.path.exists(COORDINATES_FILE):
        save_json(COORDINATES_FILE, default_coordinates)
        logger.info(f"Создан файл с координатами: {COORDINATES_FILE}")
        return default_coordinates
    
    coordinates = load_json(COORDINATES_FILE, default_coordinates)
    
    # Проверяем, есть ли все локации в координатах
    locations = get_locations()
    for loc in locations:
        loc_id_str = str(loc["id"])
        if loc_id_str not in coordinates:
            # Если локации нет в координатах, создаем ее в центре карты (предполагаем карту 1024x1024)
            coordinates[loc_id_str] = {
                "x": 512,  # Центр по X
                "y": 512,  # Центр по Y
                "name": loc["name"]
            }
            logger.info(f"Добавлены координаты для локации {loc['name']} (ID: {loc['id']})")
    
    # НЕ сохраняем обратно, чтобы координаты не менялись при перезапуске
    return coordinates

def get_cached_map() -> Optional[str]:
    """Получить кэшированную карту если она свежая"""
    try:
        cache_files = sorted(os.listdir(MAP_CACHE_DIR))
        if not cache_files:
            return None
        
        latest_map = f"{MAP_CACHE_DIR}{cache_files[-1]}"
        file_time = os.path.getmtime(latest_map)
        
        if datetime.now().timestamp() - file_time < MAP_CACHE_TIME:
            return latest_map
    except:
        pass
    return None

def cleanup_old_cache(max_files=5):
    """Удалить старые кэшированные карты"""
    try:
        cache_files = sorted(os.listdir(MAP_CACHE_DIR))
        if len(cache_files) > max_files:
            for old_file in cache_files[:-max_files]:
                try:
                    os.remove(f"{MAP_CACHE_DIR}{old_file}")
                    logger.debug(f"Удален старый кэш: {old_file}")
                except:
                    pass
    except Exception as e:
        logger.debug(f"Ошибка при очистке кэша: {e}")

def cleanup_cache_completely():
    """Полностью очистить кэш карт"""
    try:
        if os.path.exists(MAP_CACHE_DIR):
            for filename in os.listdir(MAP_CACHE_DIR):
                file_path = os.path.join(MAP_CACHE_DIR, filename)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                except Exception as e:
                    logger.debug(f"Ошибка при удалении файла {file_path}: {e}")
        logger.info("✅ Кэш карт полностью очищен")
    except Exception as e:
        logger.error(f"Ошибка при полной очистке кэша: {e}")

def load_font_with_fallback(font_size: int):
    """Загрузить шрифт с поддержкой эмодзи"""
    for font_path in FONT_PATHS:
        try:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, font_size)
                logger.debug(f"Загружен шрифт: {font_path}, размер: {font_size}")
                return font
        except Exception as e:
            logger.debug(f"Не удалось загрузить шрифт {font_path}: {e}")
    
    # Если не нашли подходящий шрифт, используем стандартный
    logger.warning(f"Не найден подходящий шрифт. Используем стандартный.")
    return ImageFont.load_default(size=font_size)

def generate_map_image(output_path: str, feedback_counts: Dict[int, Dict[str, int]]) -> bool:
    """Сгенерировать карту с цветными числами"""
    try:
        coordinates = load_coordinates()
        
        # Пробуем открыть основную карту
        try:
            map_img = Image.open(MAP_IMAGE)
            map_width, map_height = map_img.size
            logger.info(f"Загружена карта: {MAP_IMAGE} ({map_width}x{map_height})")
            
            # Конвертируем в RGBA для поддержки прозрачности
            if map_img.mode != 'RGBA':
                map_img = map_img.convert('RGBA')
                
        except FileNotFoundError:
            # Создаем простую заглушку
            map_width, map_height = 1024, 1024
            map_img = Image.new('RGBA', (map_width, map_height), color=(240, 240, 240, 255))
            draw = ImageDraw.Draw(map_img)
            
            # Загружаем шрифт для заглушки
            try:
                font = load_font_with_fallback(36)
            except:
                font = ImageFont.load_default()
            
            draw.rectangle([50, 50, 974, 974], outline=(200, 200, 200), width=2)
            
            # Многострочный текст для заглушки
            text_lines = [
                "Карта школы",
                "(загрузите school_map.png)",
                "в папку images/"
            ]
            
            # Рисуем каждую строку отдельно
            for i, line in enumerate(text_lines):
                draw.text(
                    (map_width//2, map_height//2 - 40 + i*40),
                    line,
                    fill=(100, 100, 100),
                    font=font,
                    anchor='mm',
                    align='center'
                )
            
            logger.warning(f"Карта не найдена: {MAP_IMAGE}. Используется заглушка.")
        
        draw = ImageDraw.Draw(map_img)
        
        # Загружаем шрифты разных размеров (МЕНЬШИЙ ШРИФТ)
        base_font_size = max(20, min(map_width, map_height) // 30)  # Уменьшили шрифт
        font_large = load_font_with_fallback(base_font_size)
        font_medium = load_font_with_fallback(base_font_size - 6)
        
        # Рисуем маркеры для каждой локации
        markers_drawn = 0
        for loc_id_str, coords in coordinates.items():
            try:
                loc_id = int(loc_id_str)
            except ValueError:
                continue
                
            counts = feedback_counts.get(loc_id, {"complaints": 0, "suggestions": 0})
            
            # Если нет обращений, пропускаем
            if counts["complaints"] == 0 and counts["suggestions"] == 0:
                continue
            
            # Координаты из файла (БЕЗ МАСШТАБИРОВАНИЯ)
            x = coords.get("x", 512)
            y = coords.get("y", 512)
            
            # Проверяем, чтобы координаты не выходили за пределы карты
            x = max(50, min(x, map_width - 50))
            y = max(50, min(y, map_height - 50))
            
            logger.debug(f"Локация {loc_id}: координаты ({x}, {y}), жалобы={counts['complaints']}, предложения={counts['suggestions']}")
            
            # Определяем, что будем рисовать
            has_complaints = counts['complaints'] > 0
            has_suggestions = counts['suggestions'] > 0
            
            if has_complaints and has_suggestions:
                # И жалобы и предложения - рисуем два числа разных цветов
                complaint_text = str(counts['complaints'])
                suggestion_text = str(counts['suggestions'])
                separator = "/"
                
                # Получаем размеры текста
                try:
                    complaint_bbox = draw.textbbox((0, 0), complaint_text, font=font_large)
                    complaint_width = complaint_bbox[2] - complaint_bbox[0]
                    complaint_height = complaint_bbox[3] - complaint_bbox[1]
                    
                    separator_bbox = draw.textbbox((0, 0), separator, font=font_large)
                    separator_width = separator_bbox[2] - separator_bbox[0]
                    separator_height = separator_bbox[3] - separator_bbox[1]
                    
                    suggestion_bbox = draw.textbbox((0, 0), suggestion_text, font=font_large)
                    suggestion_width = suggestion_bbox[2] - suggestion_bbox[0]
                    suggestion_height = suggestion_bbox[3] - suggestion_bbox[1]
                except Exception as e:
                    logger.warning(f"Ошибка при расчете размеров текста: {e}")
                    # Приблизительные размеры
                    complaint_width = len(complaint_text) * (font_large.size // 2)
                    suggestion_width = len(suggestion_text) * (font_large.size // 2)
                    complaint_height = suggestion_height = font_large.size
                    separator_width = font_large.size // 4
                
                # Общая ширина
                total_width = complaint_width + separator_width + suggestion_width
                text_height = max(complaint_height, suggestion_height, separator_height)
                
                # Позиции для каждого элемента
                complaint_x = x - total_width//2 + complaint_width//2
                separator_x = complaint_x + complaint_width//2 + separator_width//2
                suggestion_x = separator_x + separator_width//2 + suggestion_width//2
                
                # Рисуем фон
                padding = max(10, min(map_width, map_height) // 50)  # Уменьшили отступы
                rect_x1 = x - total_width//2 - padding
                rect_y1 = y - text_height//2 - padding
                rect_x2 = x + total_width//2 + padding
                rect_y2 = y + text_height//2 + padding
                
                # Проверяем границы
                if rect_x1 < 10:
                    offset = abs(rect_x1) + 10
                    rect_x1 += offset
                    rect_x2 += offset
                    complaint_x += offset
                    separator_x += offset
                    suggestion_x += offset
                    x += offset
                if rect_x2 > map_width - 10:
                    offset = rect_x2 - map_width + 10
                    rect_x1 -= offset
                    rect_x2 -= offset
                    complaint_x -= offset
                    separator_x -= offset
                    suggestion_x -= offset
                    x -= offset
                if rect_y1 < 10:
                    offset = abs(rect_y1) + 10
                    rect_y1 += offset
                    rect_y2 += offset
                    y += offset
                if rect_y2 > map_height - 10:
                    offset = rect_y2 - map_height + 10
                    rect_y1 -= offset
                    rect_y2 -= offset
                    y -= offset
                
                # Полупрозрачный белый фон с черной рамкой
                draw.rectangle(
                    [rect_x1, rect_y1, rect_x2, rect_y2],
                    fill=(255, 255, 255, 230),  # Полупрозрачный белый
                    outline=(0, 0, 0, 255),  # Черная рамка
                    width=1  # Тоньше рамка
                )
                
                # Рисуем текст
                try:
                    # Жалобы (красный цвет)
                    draw.text(
                        (complaint_x, y),
                        complaint_text,
                        fill=(220, 0, 0, 255),  # Красный текст
                        font=font_large,
                        anchor='mm'
                    )
                    
                    # Разделитель (черный)
                    draw.text(
                        (separator_x, y),
                        separator,
                        fill=(0, 0, 0, 255),  # Черный текст
                        font=font_large,
                        anchor='mm'
                    )
                    
                    # Предложения (зеленый цвет)
                    draw.text(
                        (suggestion_x, y),
                        suggestion_text,
                        fill=(0, 180, 0, 255),  # Зеленый текст
                        font=font_large,
                        anchor='mm'
                    )
                except Exception as e:
                    logger.warning(f"Ошибка при рисовании текста: {e}")
                    # Альтернатива
                    fallback_text = f"{counts['complaints']}/{counts['suggestions']}"
                    draw.text(
                        (x, y),
                        fallback_text,
                        fill=(0, 0, 0, 255),
                        font=font_medium,
                        anchor='mm'
                    )
                
                display_text = f"{counts['complaints']}/{counts['suggestions']}"
                
            elif has_complaints:
                # Только жалобы - рисуем красное число
                complaint_text = str(counts['complaints'])
                
                # Получаем размеры текста
                try:
                    text_bbox = draw.textbbox((0, 0), complaint_text, font=font_large)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]
                except:
                    text_width = len(complaint_text) * (font_large.size // 2)
                    text_height = font_large.size
                
                # Рисуем фон
                padding = max(10, min(map_width, map_height) // 50)
                rect_x1 = x - text_width//2 - padding
                rect_y1 = y - text_height//2 - padding
                rect_x2 = x + text_width//2 + padding
                rect_y2 = y + text_height//2 + padding
                
                # Проверяем границы
                if rect_x1 < 10:
                    offset = abs(rect_x1) + 10
                    rect_x1 += offset
                    rect_x2 += offset
                    x += offset
                if rect_x2 > map_width - 10:
                    offset = rect_x2 - map_width + 10
                    rect_x1 -= offset
                    rect_x2 -= offset
                    x -= offset
                if rect_y1 < 10:
                    offset = abs(rect_y1) + 10
                    rect_y1 += offset
                    rect_y2 += offset
                    y += offset
                if rect_y2 > map_height - 10:
                    offset = rect_y2 - map_height + 10
                    rect_y1 -= offset
                    rect_y2 -= offset
                    y -= offset
                
                # Полупрозрачный белый фон с черной рамкой
                draw.rectangle(
                    [rect_x1, rect_y1, rect_x2, rect_y2],
                    fill=(255, 255, 255, 230),
                    outline=(0, 0, 0, 255),
                    width=1
                )
                
                # Рисуем текст (красный цвет для жалоб)
                draw.text(
                    (x, y),
                    complaint_text,
                    fill=(220, 0, 0, 255),  # Красный текст
                    font=font_large,
                    anchor='mm'
                )
                
                display_text = f"{counts['complaints']}"
                
            elif has_suggestions:
                # Только предложения - рисуем зеленое число
                suggestion_text = str(counts['suggestions'])
                
                # Получаем размеры текста
                try:
                    text_bbox = draw.textbbox((0, 0), suggestion_text, font=font_large)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]
                except:
                    text_width = len(suggestion_text) * (font_large.size // 2)
                    text_height = font_large.size
                
                # Рисуем фон
                padding = max(10, min(map_width, map_height) // 50)
                rect_x1 = x - text_width//2 - padding
                rect_y1 = y - text_height//2 - padding
                rect_x2 = x + text_width//2 + padding
                rect_y2 = y + text_height//2 + padding
                
                # Проверяем границы
                if rect_x1 < 10:
                    offset = abs(rect_x1) + 10
                    rect_x1 += offset
                    rect_x2 += offset
                    x += offset
                if rect_x2 > map_width - 10:
                    offset = rect_x2 - map_width + 10
                    rect_x1 -= offset
                    rect_x2 -= offset
                    x -= offset
                if rect_y1 < 10:
                    offset = abs(rect_y1) + 10
                    rect_y1 += offset
                    rect_y2 += offset
                    y += offset
                if rect_y2 > map_height - 10:
                    offset = rect_y2 - map_height + 10
                    rect_y1 -= offset
                    rect_y2 -= offset
                    y -= offset
                
                # Полупрозрачный белый фон с черной рамкой
                draw.rectangle(
                    [rect_x1, rect_y1, rect_x2, rect_y2],
                    fill=(255, 255, 255, 230),
                    outline=(0, 0, 0, 255),
                    width=1
                )
                
                # Рисуем текст (зеленый цвет для предложений)
                draw.text(
                    (x, y),
                    suggestion_text,
                    fill=(0, 180, 0, 255),  # Зеленый текст
                    font=font_large,
                    anchor='mm'
                )
                
                display_text = f"{counts['suggestions']}"
            else:
                continue
            
            markers_drawn += 1
            logger.debug(f"Нарисован маркер для локации {loc_id} на координатах ({x}, {y}): {display_text}")
        
        # Сохраняем карту с высоким качеством
        # Конвертируем RGBA в RGB перед сохранением как JPEG
        if map_img.mode == 'RGBA':
            map_img.convert('RGB').save(output_path, quality=95, optimize=True)
        else:
            # Если уже в режиме RGB, сохраняем как есть
            map_img.save(output_path, quality=95, optimize=True)
            
        logger.info(f"✅ Сгенерирована новая карта: {output_path}. Маркеров: {markers_drawn}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка при генерации карты: {e}", exc_info=True)
        return False

def generate_map_with_cache() -> str:
    """Сгенерировать карту с кэшированием"""
    # Всегда генерируем новую карту (отключаем кэш)
    # cached_map = get_cached_map()
    # if cached_map:
    #     logger.info(f"Используем кэшированную карту: {cached_map}")
    #     return cached_map
    
    # Генерируем новую карту
    feedback_counts = get_feedback_counts()
    
    # Логируем статистику обращений
    logger.info(f"Статистика обращений для карты: {feedback_counts}")
    
    # Создаем уникальное имя файла с временной меткой
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"{MAP_CACHE_DIR}map_{timestamp}.jpg"
    
    if generate_map_image(output_path, feedback_counts):
        cleanup_old_cache()
        return output_path
    else:
        # Если не удалось сгенерировать, возвращаем пустую строку
        return ""

# ==================== УТИЛИТЫ ДЛЯ ОТПРАВКИ СООБЩЕНИЙ ====================
async def safe_edit_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML"
) -> bool:
    """Безопасное редактирование сообщения с обработкой ошибок"""
    try:
        await callback.message.edit_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
        return True
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return True
        logger.warning(f"Ошибка при редактировании сообщения: {e}")
        return False
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при редактировании сообщения: {e}")
        return False

async def safe_answer(
    callback: CallbackQuery,
    text: Optional[str] = None,
    show_alert: bool = False
) -> bool:
    """Безопасный ответ на callback_query"""
    try:
        await callback.answer(text=text, show_alert=show_alert)
        return True
    except Exception as e:
        logger.warning(f"Ошибка при ответе на callback: {e}")
        return False

async def safe_send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "HTML"
) -> bool:
    """Безопасная отправка сообщения с обработкой ошибок"""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )
        return True
    except TelegramForbiddenError:
        logger.warning(f"Пользователь {chat_id} деактивирован или заблокировал бота")
        return False
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения пользователю {chat_id}: {e}")
        return False

async def safe_send_photo(
    chat_id: int,
    photo_path: str,
    caption: str = "",
    reply_markup: Optional[InlineKeyboardMarkup] = None
) -> bool:
    """Безопасная отправка фото"""
    try:
        if not os.path.exists(photo_path):
            logger.error(f"Фото не найдено: {photo_path}")
            return False
        
        # Используем FSInputFile для отправки файла
        photo = FSInputFile(photo_path)
        
        # Отправляем фото с явным указанием parse_mode=None
        # Это отключает HTML-парсинг для подписей
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=None  # КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: отключаем парсинг HTML
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка при отправке фото: {e}")
        return False

# ==================== КЛАВИАТУРЫ ====================
def get_main_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура главного меню"""
    buttons = [
        [InlineKeyboardButton(text="📊 Посмотреть обращения", callback_data="view_feedbacks")],
        [
            InlineKeyboardButton(text="📝 Оставить жалобу", callback_data="add_complaint"),
            InlineKeyboardButton(text="💡 Внести предложение", callback_data="add_suggestion")
        ],
        [
            InlineKeyboardButton(text="🔴 Показать все жалобы", callback_data="show_all_complaints"),
            InlineKeyboardButton(text="🟢 Показать все предложения", callback_data="show_all_suggestions")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_locations_keyboard(feedback_type: str = None, view_only: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура выбора локации"""
    locations = get_locations()
    feedback_counts = get_feedback_counts()
    
    buttons = []
    
    for loc in locations:
        counts = feedback_counts.get(loc["id"], {"complaints": 0, "suggestions": 0})
        
        # Форматируем текст кнопки
        complaints_text = f"🔴{counts['complaints']}" if counts['complaints'] > 0 else ""
        suggestions_text = f"🟢{counts['suggestions']}" if counts['suggestions'] > 0 else ""
        
        stats_text = ""
        if complaints_text and suggestions_text:
            stats_text = f" ({complaints_text} {suggestions_text})"
        elif complaints_text:
            stats_text = f" ({complaints_text})"
        elif suggestions_text:
            stats_text = f" ({suggestions_text})"
        
        button_text = f"{loc['emoji']} {loc['name']}{stats_text}"
        
        # Формируем callback_data
        if view_only:
            callback_data = f"view_loc_{loc['id']}"
        elif feedback_type:
            callback_data = f"add_{feedback_type}_loc_{loc['id']}"
        else:
            callback_data = f"loc_details_{loc['id']}"
        
        buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
    
    # Добавляем кнопки действий
    if not view_only and not feedback_type:
        buttons.append([
            InlineKeyboardButton(text="🔴 Все жалобы", callback_data="show_all_complaints"),
            InlineKeyboardButton(text="🟢 Все предложения", callback_data="show_all_suggestions")
        ])
    
    # Кнопка возврата
    if feedback_type or view_only:
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    else:
        buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_back_keyboard(target: str = "main") -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой назад"""
    text = "🔙 Назад"
    callback_data = "back_to_main"
    
    if target == "view_feedbacks":
        text = "🔙 К списку локаций"
        callback_data = "view_feedbacks"
    elif target == "add_feedback":
        text = "🔙 Выбрать другую локацию"
        callback_data = "add_feedback"
    
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=callback_data)
    ]])

def get_feedback_type_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора типа обращения"""
    buttons = [
        [
            InlineKeyboardButton(text="🔴 Оставить жалобу", callback_data="add_complaint"),
            InlineKeyboardButton(text="🟢 Внести предложение", callback_data="add_suggestion")
        ],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_pagination_keyboard(page: int, total_pages: int, location_id: int, feedback_type: str) -> InlineKeyboardMarkup:
    """Клавиатура пагинации для просмотра жалоб/предложений"""
    buttons = []
    
    prefix = "complaints" if feedback_type == "complaint" else "suggestions"
    
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(
            text="⬅️ Назад", 
            callback_data=f"view_{prefix}_loc_{location_id}_page_{page-1}"
        ))
    
    nav_buttons.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="current_page"))
    
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперед ➡️", 
            callback_data=f"view_{prefix}_loc_{location_id}_page_{page+1}"
        ))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад к локации", callback_data=f"view_loc_{location_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ОБРАБОТЧИКИ ====================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    try:
        welcome_text = """
<b>🏫 Школьный портал улучшений</b> 🎯

<u>Добро пожаловать!</u> 👋

Здесь вы можете:
• 📝 <b>Оставить жалобу</b> на проблему
• 💡 <b>Предложить улучшение</b>
• 📊 <b>Посмотреть обращения</b> (с картой)

<b>Конфиденциальность:</b>
Все обращения анонимны. Ваши личные данные не отображаются другим пользователям.

Вместе мы сделаем нашу школу лучше! 🌟
"""
        
        await safe_send_message(
            chat_id=message.chat.id,
            text=welcome_text,
            reply_markup=get_main_keyboard()
        )
        logger.info(f"Новый пользователь: {message.from_user.id} (@{message.from_user.username})")
        
    except Exception as e:
        logger.error(f"Ошибка в обработчике /start: {e}")

@dp.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = """
<b>📖 Справка по боту</b>

<b>Основные функции:</b>
• <b>Оставить жалобу</b> - сообщить о проблеме
• <b>Внести предложение</b> - предложить улучшение
• <b>Посмотреть обращения</b> - просмотр жалоб и предложений с картой

<b>Конфиденциальность:</b>
✅ Все обращения <b>анонимны</b>
✅ Ваши личные данные <b>не видны</b> другим пользователям
✅ Администрация видит только содержание обращений

<b>Как пользоваться:</b>
1. Нажмите "Оставить жалобу" или "Внести предложение"
2. Выберите школьную локацию из списка
3. Подробно опишите проблему или предложение
4. Отправьте сообщение - ваше обращение будет сохранено

<b>Карта обращений:</b>
При просмотре обращений вы увидите карту школы с отметками:
🔴X - количество жалоб по локации (красный эмодзи)
🟢Y - количество предложений по локации (зеленый эмодзи)
🔴X 🟢Y - и жалобы и предложения

<b>Статистика в кнопках:</b>
"🏫 Главный корпус (🔴3 🟢5)" означает:
• 3 жалобы по главному корпусу
• 5 предложений по главному корпусу

По всем вопросам обращайтесь к администраторам.
"""
    
    await safe_send_message(
        chat_id=message.chat.id,
        text=help_text,
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await safe_answer(callback)
    await state.clear()
    
    main_text = """
<b>🏫 Главное меню</b>

Выберите действие:

<b>Конфиденциальность:</b>
✅ Все обращения анонимны
"""
    
    await safe_edit_message(
        callback=callback,
        text=main_text,
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data == "view_feedbacks")
async def view_feedbacks(callback: CallbackQuery):
    """Просмотр обращений по локациям (сначала показываем карту)"""
    await safe_answer(callback, text="🗺️ Генерируем актуальную карту...")
    
    try:
        # Загружаем координаты (создаем файл если его нет)
        load_coordinates()
        
        # Генерируем карту (ВСЕГДА НОВУЮ)
        map_path = generate_map_with_cache()
        
        if map_path and os.path.exists(map_path):
            # Отправляем карту с обновленной подписью
            map_caption = """🗺️ Карта обращений по школе

Как читать карту:
• 🟥 Красные числа — количество жалоб
• 🟩 Зеленые числа — количество предложений
• 🟥3/🟩5 — 3 жалобы и 5 предложений

Примеры:
• 5 — 5 жалоб (красный цвет)
• 3 — 3 предложения (зеленый цвет)
• 2/4 — 2 жалобы и 4 предложения

Обновлено: {}
""".format(datetime.now().strftime("%d.%m.%Y %H:%M"))
            
            success = await safe_send_photo(
                chat_id=callback.message.chat.id,
                photo_path=map_path,
                caption=map_caption,
            )
            
            if not success:
                await callback.message.answer(
                    "⚠️ Не удалось отправить карту. Возможно, файл поврежден.",
                )
        else:
            # Если карта не сгенерирована, показываем текст
            await callback.message.answer(
                "⚠️ Карта школы пока не загружена. Пожалуйста, загрузите файл school_map.png в папку images/",
            )
        
        # После карты показываем список локаций
        text = """
<b>📊 Просмотр обращений</b>

Выберите локацию для просмотра жалоб и предложений:

<b>Формат кнопок:</b>
🏫 Название локации (🔴X 🟢Y)
• X - количество жалоб
• Y - количество предложений

<b>Конфиденциальность:</b>
✅ Все обращения отображаются анонимно
✅ Личные данные пользователей скрыты

Нажмите на локацию, чтобы увидеть детали.
"""
        
        await safe_send_message(
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=get_locations_keyboard(view_only=True)
        )
        
    except Exception as e:
        logger.error(f"Ошибка в view_feedbacks: {e}")
        await safe_answer(callback, text="❌ Произошла ошибка при загрузке карты.", show_alert=True)

@dp.callback_query(F.data.startswith("loc_details_"))
async def location_details(callback: CallbackQuery):
    """Детальная информация о локации"""
    await safe_answer(callback)
    
    try:
        location_id = int(callback.data.split("_")[2])
        location = get_location_full_info(location_id)
        feedback_counts = get_feedback_counts()
        counts = feedback_counts.get(location_id, {"complaints": 0, "suggestions": 0})
        
        text = f"""
<b>{location['emoji']} {location['name']}</b>

<b>Описание:</b> {location['description']}

<b>Статистика обращений:</b>
🔴 Жалобы: {counts['complaints']}
🟢 Предложения: {counts['suggestions']}
📊 Всего: {counts['complaints'] + counts['suggestions']}

Выберите действие для этой локации:
"""
        
        buttons = [
            [
                InlineKeyboardButton(text="🔴 Посмотреть жалобы", callback_data=f"view_complaints_loc_{location_id}_page_1"),
                InlineKeyboardButton(text="🟢 Посмотреть предложения", callback_data=f"view_suggestions_loc_{location_id}_page_1")
            ],
            [
                InlineKeyboardButton(text="📝 Оставить жалобу", callback_data=f"add_complaint_loc_{location_id}"),
                InlineKeyboardButton(text="💡 Внести предложение", callback_data=f"add_suggestion_loc_{location_id}")
            ],
            [InlineKeyboardButton(text="🔙 К списку локаций", callback_data="view_feedbacks")]
        ]
        
        await safe_edit_message(
            callback=callback,
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        
    except Exception as e:
        logger.error(f"Ошибка в location_details: {e}")
        await safe_answer(callback, text="❌ Произошла ошибка. Попробуйте еще раз.", show_alert=True)

@dp.callback_query(F.data.startswith("view_loc_"))
async def view_location_feedbacks(callback: CallbackQuery):
    """Просмотр обращений для конкретной локации"""
    await safe_answer(callback)
    
    try:
        location_id = int(callback.data.split("_")[2])
        location = get_location_full_info(location_id)
        feedbacks = get_feedbacks()
        location_feedbacks = [f for f in feedbacks if f["location_id"] == location_id]
        
        if not location_feedbacks:
            text = f"""
<b>{location['emoji']} {location['name']}</b>

📭 <b>Обращений пока нет</b>
Будьте первым, кто оставит анонимное обращение для этой локации! ✨
"""
            
            buttons = [
                [
                    InlineKeyboardButton(text="📝 Оставить жалобу", callback_data=f"add_complaint_loc_{location_id}"),
                    InlineKeyboardButton(text="💡 Внести предложение", callback_data=f"add_suggestion_loc_{location_id}")
                ],
                [InlineKeyboardButton(text="🔙 К списку локаций", callback_data="view_feedbacks")]
            ]
            
            await safe_edit_message(
                callback=callback,
                text=text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
            return
        
        complaints = [f for f in location_feedbacks if f["type"] == "complaint"]
        suggestions = [f for f in location_feedbacks if f["type"] == "suggestion"]
        
        text = f"""
<b>{location['emoji']} {location['name']}</b>

<b>Статистика:</b>
🔴 Жалобы: {len(complaints)}
🟢 Предложения: {len(suggestions)}
📊 Всего обращений: {len(location_feedbacks)}

<b>Конфиденциальность:</b>
✅ Все обращения отображаются анонимно

Выберите, что хотите просмотреть:
"""
        
        buttons = [
            [
                InlineKeyboardButton(text=f"🔴 Жалобы ({len(complaints)})", callback_data=f"view_complaints_loc_{location_id}_page_1"),
                InlineKeyboardButton(text=f"🟢 Предложения ({len(suggestions)})", callback_data=f"view_suggestions_loc_{location_id}_page_1")
            ],
            [
                InlineKeyboardButton(text="📝 Оставить жалобу", callback_data=f"add_complaint_loc_{location_id}"),
                InlineKeyboardButton(text="💡 Внести предложение", callback_data=f"add_suggestion_loc_{location_id}")
            ],
            [InlineKeyboardButton(text="🔙 К списку локаций", callback_data="view_feedbacks")]
        ]
        
        await safe_edit_message(
            callback=callback,
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        
    except Exception as e:
        logger.error(f"Ошибка в view_location_feedbacks: {e}")
        await safe_answer(callback, text="❌ Произошла ошибка при загрузке обращений.", show_alert=True)

@dp.callback_query(F.data.startswith("view_complaints_loc_") | F.data.startswith("view_suggestions_loc_"))
async def view_feedbacks_by_type(callback: CallbackQuery):
    """Просмотр жалоб или предложений для локации с пагинацияцией"""
    await safe_answer(callback)
    
    try:
        callback_data = callback.data
        
        if "complaints" in callback_data:
            feedback_type = "complaint"
            prefix = "view_complaints_loc_"
        else:
            feedback_type = "suggestion"
            prefix = "view_suggestions_loc_"
        
        data_without_prefix = callback_data[len(prefix):]
        parts = data_without_prefix.split('_')
        
        location_id = int(parts[0])
        
        page = 1
        for i in range(len(parts)):
            if parts[i] == "page" and i + 1 < len(parts):
                try:
                    page = int(parts[i + 1])
                except ValueError:
                    page = 1
                break
        
        location = get_location_full_info(location_id)
        feedbacks = get_feedbacks()
        
        filtered_feedbacks = [
            f for f in feedbacks 
            if f["type"] == feedback_type and f["location_id"] == location_id
        ]
        
        if not filtered_feedbacks:
            type_text = "жалоб" if feedback_type == "complaint" else "предложений"
            text = f"""
<b>{location['emoji']} {location['name']}</b>

📭 <b>Нет {type_text}</b>
"""
            
            buttons = [[InlineKeyboardButton(text="🔙 Назад", callback_data=f"view_loc_{location_id}")]]
            await safe_edit_message(
                callback=callback,
                text=text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
            return
        
        items_per_page = 5
        total_items = len(filtered_feedbacks)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        page_feedbacks = filtered_feedbacks[start_idx:end_idx]
        
        type_text = "жалоб" if feedback_type == "complaint" else "предложений"
        type_emoji = "🔴" if feedback_type == "complaint" else "🟢"
        
        text = f"""
<b>{location['emoji']} {location['name']}</b>
<b>{type_emoji} {type_text.capitalize()} (страница {page}/{total_pages})</b>

<b>Конфиденциальность:</b>
✅ Все обращения отображаются анонимно

"""
        
        for i, fb in enumerate(page_feedbacks, start=start_idx + 1):
            safe_text = anonymize_text(fb['text'])
            text += f"""
<b>{i}. {type_emoji} {fb['date']}</b>
<b>Текст:</b> {safe_text}
"""
        
        await safe_edit_message(
            callback=callback,
            text=text,
            reply_markup=get_pagination_keyboard(page, total_pages, location_id, feedback_type)
        )
        
    except Exception as e:
        logger.error(f"Ошибка в view_feedbacks_by_type: {e}", exc_info=True)
        await safe_answer(callback, text="❌ Произошла ошибка при загрузке обращений.", show_alert=True)

@dp.callback_query(F.data.in_(["add_complaint", "add_suggestion"]))
async def add_feedback_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления обращения"""
    await safe_answer(callback)
    
    feedback_type = "complaint" if callback.data == "add_complaint" else "suggestion"
    type_text = "жалобу" if feedback_type == "complaint" else "предложение"
    
    await state.update_data(feedback_type=feedback_type)
    await state.set_state(FeedbackStates.choosing_location)
    
    text = f"""
<b>📝 Оставить {type_text}</b>

Выберите школьную локацию, к которой относится обращение:

<b>Формат кнопок:</b>
🏫 Название локации (🔴X 🟢Y)
• X - количество существующих жалоб
• Y - количество существующих предложений

<b>Конфиденциальность:</b>
✅ Ваше обращение будет полностью анонимным
✅ Другие пользователи не увидят ваши данные

<b>Новые локации:</b>
🎫 Турникеты - проблемы с доступом, картами, турникетами
🏊 Бассейн - качество воды, температура, безопасность
"""
    
    await safe_edit_message(
        callback=callback,
        text=text,
        reply_markup=get_locations_keyboard(feedback_type=feedback_type)
    )

@dp.callback_query(F.data.startswith(("add_complaint_loc_", "add_suggestion_loc_")))
async def add_feedback_to_location(callback: CallbackQuery, state: FSMContext):
    """Добавление обращения к конкретной локации"""
    await safe_answer(callback)
    
    try:
        parts = callback.data.split("_")
        feedback_type = parts[1]
        location_id = int(parts[3])
        
        await state.update_data(
            feedback_type=feedback_type,
            location_id=location_id
        )
        await state.set_state(FeedbackStates.entering_text)
        
        location = get_location_full_info(location_id)
        type_text = "жалобу" if feedback_type == "complaint" else "предложение"
        
        text = f"""
<b>📝 Оставить {type_text}</b>

<b>Локация:</b> {location['emoji']} {location['name']}
<b>Описание локации:</b> {location['description']}

<b>✏️ Введите текст {type_text}:</b>

<b>Для жалобы укажите:</b>
• Что именно не устраивает
• Где конкретно находится проблема
• Когда она возникла

<b>Для предложения укажите:</b>
• Что именно можно улучшить
• Как это можно реализовать
• Какая будет польза

<b>Примеры для новых локаций:</b>
🎫 <b>Турникеты:</b> не срабатывает карта, медленная работа, застревание
🏊 <b>Бассейн:</b> холодная вода, скользкий пол, не работает душ

<b>Конфиденциальность:</b>
✅ Ваше обращение будет полностью анонимным
✅ Не указывайте личные данные в тексте

<b>Максимальная длина:</b> 1000 символов
"""
        
        buttons = [[
            InlineKeyboardButton(text="🔙 Выбрать другую локацию", callback_data=f"add_{feedback_type}")
        ]]
        
        await safe_edit_message(
            callback=callback,
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        
    except Exception as e:
        logger.error(f"Ошибка в add_feedback_to_location: {e}")
        await safe_answer(callback, text="❌ Произошла ошибка. Попробуйте еще раз.", show_alert=True)

@dp.callback_query(F.data.startswith("add_") and F.data.endswith(("complaint", "suggestion")))
async def add_feedback_type_only(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопок добавления без указания локации"""
    await safe_answer(callback)
    
    feedback_type = "complaint" if "complaint" in callback.data else "suggestion"
    type_text = "жалобу" if feedback_type == "complaint" else "предложение"
    
    await state.update_data(feedback_type=feedback_type)
    await state.set_state(FeedbackStates.choosing_location)
    
    text = f"""
<b>📝 Оставить {type_text}</b>

Выберите школьную локацию, к которой относится обращение:

<b>Конфиденциальность:</b>
✅ Ваше обращение будет полностью анонимным

<b>Новые локации:</b>
🎫 Турникеты
🏊 Бассейн
"""
    
    await safe_edit_message(
        callback=callback,
        text=text,
        reply_markup=get_locations_keyboard(feedback_type=feedback_type)
    )

@dp.message(FeedbackStates.entering_text)
async def enter_feedback_text(message: Message, state: FSMContext):
    """Обработка введенного текста обращения"""
    try:
        if len(message.text.strip()) < 5:
            await safe_send_message(
                chat_id=message.chat.id,
                text="❌ <b>Текст слишком короткий!</b>\n"
                     "Пожалуйста, опишите проблему или предложение подробнее (минимум 5 символов):",
                reply_markup=get_back_keyboard("add_feedback")
            )
            return
        
        if len(message.text) > 1000:
            await safe_send_message(
                chat_id=message.chat.id,
                text="❌ <b>Текст слишком длинный!</b>\n"
                     "Максимальная длина — 1000 символов.\n"
                     "Пожалуйста, введите более краткий текст:",
                reply_markup=get_back_keyboard("add_feedback")
            )
            return
        
        state_data = await state.get_data()
        feedback_type = state_data["feedback_type"]
        location_id = state_data["location_id"]
        
        save_feedback(
            feedback_type=feedback_type,
            location_id=location_id,
            text=message.text.strip(),
            user_id=message.from_user.id,
            username=message.from_user.username
        )
        
        location = get_location_full_info(location_id)
        type_text = "жалоба" if feedback_type == "complaint" else "предложение"
        type_emoji = "🔴" if feedback_type == "complaint" else "🟢"
        
        confirmation_text = f"""
<b>✅ {type_emoji} {type_text.capitalize()} сохранена!</b>

<b>Локация:</b> {location['emoji']} {location['name']}
<b>Тип:</b> {type_text}
<b>Дата:</b> {datetime.now().strftime("%d.%m.%Y %H:%M")}

<b>Ваш текст:</b>
<code>{anonymize_text(message.text, 200)}</code>

<b>Конфиденциальность:</b>
✅ Ваше обращение <b>анонимно</b>
✅ Другие пользователи не увидят ваши данные

<b>Спасибо за ваш вклад в улучшение школы!</b> 🌟
Ваше обращение будет рассмотрено администрацией.
"""
        
        await safe_send_message(
            chat_id=message.chat.id,
            text=confirmation_text,
            reply_markup=get_main_keyboard()
        )
        
        await state.clear()
        
        await notify_admins_about_new_feedback(
            location_id=location_id,
            feedback_type=feedback_type,
            text=message.text,
            user_id=message.from_user.id,
            username=message.from_user.username
        )
        
    except Exception as e:
        logger.error(f"Ошибка в enter_feedback_text: {e}")
        await safe_send_message(
            chat_id=message.chat.id,
            text="❌ <b>Произошла ошибка при сохранении обращения.</b>\nПопробуйте позже или обратитесь к администратору.",
            reply_markup=get_main_keyboard()
        )
        await state.clear()

async def notify_admins_about_new_feedback(location_id: int, feedback_type: str, text: str, user_id: int, username: str):
    """Отправка уведомления администраторам о новом обращении"""
    try:
        if not ADMIN_IDS:
            return
            
        location = get_location_full_info(location_id)
        type_text = "жалоба" if feedback_type == "complaint" else "предложение"
        type_emoji = "🔴" if feedback_type == "complaint" else "🟢"
        
        notification_text = f"""
<b>📢 Новое обращение! (Админ)</b>

<b>Тип:</b> {type_emoji} {type_text}
<b>Локация:</b> {location['emoji']} {location['name']}
<b>От пользователя:</b> @{username if username else 'без username'} (ID: {user_id})
<b>Дата:</b> {datetime.now().strftime("%d.%m.%Y %H:%M")}

<b>Текст обращения:</b>
<code>{text[:500]}{'...' if len(text) > 500 else ''}</code>

<b>Примечание:</b> Для пользователей обращение отображается анонимно.
"""
        
        for admin_id in ADMIN_IDS:
            await safe_send_message(
                chat_id=admin_id,
                text=notification_text
            )
            
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администраторам: {e}")

@dp.callback_query(F.data.startswith(("show_all_complaints", "show_all_suggestions")))
async def show_all_feedbacks(callback: CallbackQuery):
    """Показать все жалобы или предложения"""
    await safe_answer(callback)
    
    try:
        feedback_type = "complaint" if "complaints" in callback.data else "suggestion"
        type_text = "жалобы" if feedback_type == "complaint" else "предложения"
        type_emoji = "🔴" if feedback_type == "complaint" else "🟢"
        
        feedbacks = get_feedbacks()
        filtered_feedbacks = [f for f in feedbacks if f["type"] == feedback_type]
        
        if not filtered_feedbacks:
            text = f"""
<b>{type_emoji} {type_text.capitalize()}</b>

📭 <b>Пока нет ни одной {type_text}</b>
"""
            
            buttons = [[InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]]
            await safe_edit_message(
                callback=callback,
                text=text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            )
            return
        
        locations = get_locations()
        location_map = {loc["id"]: f"{loc['emoji']} {loc['name']}" for loc in locations}
        
        text = f"""
<b>{type_emoji} Все {type_text} ({len(filtered_feedbacks)})</b>

<b>Конфиденциальность:</b>
✅ Все обращения отображаются анонимно

"""
        
        recent_feedbacks = sorted(filtered_feedbacks, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]
        
        for fb in recent_feedbacks:
            location_name = location_map.get(fb["location_id"], f"Локация #{fb['location_id']}")
            safe_text = anonymize_text(fb['text'], 100)
            
            text += f"""
<b>{location_name}</b>
<i>{fb['date']}</i>
<code>{safe_text}</code>
"""
        
        text += f"\n<b>📊 Статистика по локациям:</b>\n"
        
        feedback_counts = get_feedback_counts()
        for loc_id, counts in sorted(feedback_counts.items()):
            if feedback_type == "complaint" and counts["complaints"] > 0:
                loc_name = location_map.get(loc_id, f"Локация #{loc_id}")
                text += f"\n{loc_name}: 🔴{counts['complaints']}"
            elif feedback_type == "suggestion" and counts["suggestions"] > 0:
                loc_name = location_map.get(loc_id, f"Локация #{loc_id}")
                text += f"\n{loc_name}: 🟢{counts['suggestions']}"
        
        buttons = [
            [InlineKeyboardButton(text="📊 Посмотреть по локациям", callback_data="view_feedbacks")],
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")]
        ]
        
        await safe_edit_message(
            callback=callback,
            text=text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        
    except Exception as e:
        logger.error(f"Ошибка в show_all_feedbacks: {e}")
        await safe_answer(callback, text="❌ Произошла ошибка при загрузке обращений.", show_alert=True)

@dp.callback_query(F.data == "cancel")
async def cancel_feedback(callback: CallbackQuery, state: FSMContext):
    """Отмена создания обращения"""
    await safe_answer(callback)
    await state.clear()
    
    cancel_text = """
<b>❌ Создание обращения отменено</b>

Вы всегда можете вернуться и создать новое обращение.

<b>Конфиденциальность:</b>
✅ Все обращения анонимны
"""
    
    await safe_edit_message(
        callback=callback,
        text=cancel_text,
        reply_markup=get_main_keyboard()
    )

# ==================== АДМИН КОМАНДЫ ====================
@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика по обращениям (для админов)"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    feedbacks = get_feedbacks()
    feedback_counts = get_feedback_counts()
    locations = get_locations()
    
    total_complaints = sum(counts["complaints"] for counts in feedback_counts.values())
    total_suggestions = sum(counts["suggestions"] for counts in feedback_counts.values())
    
    text = f"""
<b>📈 Статистика обращений (Админ)</b>

<b>Общая статистика:</b>
🔴 Всего жалоб: {total_complaints}
🟢 Всего предложений: {total_suggestions}
📊 Всего обращений: {len(feedbacks)}

<b>Статистика по локациям:</b>
"""
    
    for loc in locations:
        counts = feedback_counts.get(loc["id"], {"complaints": 0, "suggestions": 0})
        if counts["complaints"] > 0 or counts["suggestions"] > 0:
            text += f"\n{loc['emoji']} {loc['name']}: 🔴{counts['complaints']} 🟢{counts['suggestions']}"
    
    text += f"\n\n<b>Последние обращения (с данными пользователей):</b>"
    
    recent_feedbacks = sorted(feedbacks, key=lambda x: x.get("timestamp", ""), reverse=True)[:5]
    for fb in recent_feedbacks:
        loc_name = get_location_name(fb["location_id"])
        type_emoji = "🔴" if fb["type"] == "complaint" else "🟢"
        username = f"@{fb['real_username']}" if fb.get('real_username') else f"ID: {fb.get('real_user_id', 'N/A')}"
        text += f"\n\n{type_emoji} {loc_name} ({fb['date']})"
        text += f"\n<i>От:</i> {username}"
        text += f"\n<code>{fb['text'][:50]}...</code>"
    
    await message.answer(text, reply_markup=get_main_keyboard())

@dp.message(Command("export"))
async def cmd_export(message: Message):
    """Экспорт данных (для админов)"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return
    
    try:
        import csv
        from io import StringIO
        
        feedbacks = get_feedbacks()
        locations = get_locations()
        location_map = {loc["id"]: loc["name"] for loc in locations}
        
        output = StringIO()
        writer = csv.writer(output, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        
        writer.writerow(["ID", "Дата", "Тип", "Локация", "Текст", "ID пользователя", "Username", "Публичный ID", "Статус"])
        
        for fb in feedbacks:
            writer.writerow([
                fb["id"],
                fb["date"],
                "Жалоба" if fb["type"] == "complaint" else "Предложение",
                location_map.get(fb["location_id"], f"Локация #{fb['location_id']}"),
                fb["text"],
                fb.get("real_user_id", ""),
                fb.get("real_username", ""),
                fb.get("public_user_id", ""),
                fb.get("status", "новое")
            ])
        
        output.seek(0)
        csv_data = output.getvalue()
        
        with open("data/export.csv", "w", encoding="utf-8") as f:
            f.write(csv_data)
        
        # Отправляем файл с простой подписью
        with open("data/export.csv", "rb") as f:
            await message.answer_document(
                document=("feedbacks_export.csv", f),
                caption=f"""📊 Экспорт данных (Админ)

Обращений: {len(feedbacks)}
Дата экспорта: {datetime.now().strftime('%d.%m.%Y %H:%M')}

Примечание:
• Содержит реальные данные пользователей
• Для публичного просмотра используются анонимные ID"""
            )
            
    except Exception as e:
        logger.error(f"Ошибка при экспорте данных: {e}")
        await message.answer(f"❌ Ошибка при экспорте: {str(e)}")

# ==================== ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ====================
@dp.errors()
async def errors_handler(update, exception):
    """Глобальный обработчик ошибок"""
    if isinstance(exception, TelegramForbiddenError):
        logger.warning(f"Пользователь заблокировал бота или удалил аккаунт: {exception}")
        return True
    
    if isinstance(exception, TelegramBadRequest):
        if "message is not modified" in str(exception):
            logger.debug("Сообщение не было изменено")
            return True
        if "message can't be deleted" in str(exception):
            logger.debug("Сообщение не может быть удалено")
            return True
        logger.error(f"Ошибка Telegram API: {exception}")
        return True
    
    logger.error(f"Непредвиденная ошибка: {exception}", exc_info=True)
    return True

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Запуск бота"""
    logger.info("=" * 50)
    logger.info("🤖 Бот запускается...")
    logger.info(f"📊 Уровень логирования: {LOG_LEVEL}")
    logger.info(f"👑 Администраторы: {ADMIN_IDS}")
    
    # Загружаем координаты (создаем файл если его нет)
    coordinates = load_coordinates()
    logger.info(f"📁 Загружено координат локаций: {len(coordinates)}")
    
    locations = get_locations()
    logger.info(f"📁 Загружено локаций: {len(locations)}")
    
    logger.info("📍 Список локаций:")
    for loc in locations:
        logger.info(f"  {loc['emoji']} {loc['name']} (ID: {loc['id']})")
    
    feedbacks = get_feedbacks()
    logger.info(f"📁 Загружено обращений: {len(feedbacks)}")
    
    # Показываем статистику обращений
    feedback_counts = get_feedback_counts()
    logger.info("📊 Статистика обращений по локациям:")
    for loc_id, counts in feedback_counts.items():
        if counts["complaints"] > 0 or counts["suggestions"] > 0:
            logger.info(f"  Локация {loc_id}: жалобы={counts['complaints']}, предложения={counts['suggestions']}")
    
    # Проверяем наличие карты
    if os.path.exists(MAP_IMAGE):
        try:
            map_img = Image.open(MAP_IMAGE)
            map_width, map_height = map_img.size
            logger.info(f"🗺️ Карта найдена: {MAP_IMAGE} ({map_width}x{map_height})")
            map_img.close()
        except Exception as e:
            logger.error(f"Ошибка при открытии карты: {e}")
    else:
        logger.warning(f"⚠️ Карта не найдена: {MAP_IMAGE}. Создайте файл или используйте заглушку.")
    
    # ОЧИЩАЕМ КЭШ ПРИ ЗАПУСКЕ БОТА
    cleanup_cache_completely()
    logger.info("✅ Кэш карт очищен при запуске")
    
    logger.info("=" * 50)
    
    try:
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())