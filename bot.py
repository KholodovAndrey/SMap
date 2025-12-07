import json
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup,
    CallbackQuery
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

# Создаем директорию для данных, если её нет
os.makedirs("data", exist_ok=True)

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
        {"id": 10, "name": "Коридоры и рекреации", "emoji": "🚪", "description": "Общие помещения"}
    ])

def get_feedbacks() -> List[Dict]:
    """Получить все жалобы и предложения"""
    return load_json(FEEDBACKS_FILE, [])

def save_feedback(feedback_type: str, location_id: int, text: str, user_id: Optional[int] = None, username: Optional[str] = None) -> None:
    """Сохранить новое обращение"""
    try:
        feedbacks = get_feedbacks()
        
        # Генерируем анонимный ID для пользователя (только для публичного просмотра)
        # В реальных данных сохраняем настоящий user_id для администраторов
        public_user_id = f"user_{len(feedbacks) + 1000}"
        
        new_feedback = {
            "id": len(feedbacks) + 1,
            "type": feedback_type,
            "type_emoji": "🔴" if feedback_type == "complaint" else "🟢",
            "type_text": "Жалоба" if feedback_type == "complaint" else "Предложение",
            "location_id": location_id,
            "text": text,
            # Для администраторов сохраняем реальные данные
            "real_user_id": user_id,
            "real_username": username,
            # Для публичного просмотра - анонимные данные
            "public_user_id": public_user_id,
            "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "timestamp": datetime.now().isoformat(),
            "status": "новое"
        }
        
        feedbacks.append(new_feedback)
        save_json(FEEDBACKS_FILE, feedbacks)
        logger.info(f"Сохранено обращение #{new_feedback['id']} от пользователя {user_id} ({username})")
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
    # Убираем упоминания пользователей (@username)
    import re
    text = re.sub(r'@\w+', '[пользователь]', text)
    # Убираем ссылки
    text = re.sub(r'https?://\S+', '[ссылка]', text)
    # Обрезаем если слишком длинный
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text

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
            # Для просмотра обращений конкретной локации
            callback_data = f"view_loc_{loc['id']}"
        elif feedback_type:
            # Для добавления обращения определенного типа
            callback_data = f"add_{feedback_type}_loc_{loc['id']}"
        else:
            # Для общего просмотра (статистика + детали)
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
    
    # Определяем префикс для callback_data
    prefix = "complaints" if feedback_type == "complaint" else "suggestions"
    
    # Кнопки навигации
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
    
    # Кнопка возврата
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
• 📊 <b>Посмотреть существующие обращения</b>

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
• <b>Посмотреть обращения</b> - просмотр жалоб и предложений

<b>Конфиденциальность:</b>
✅ Все обращения <b>анонимны</b>
✅ Ваши личные данные <b>не видны</b> другим пользователям
✅ Администрация видит только содержание обращений

<b>Как пользоваться:</b>
1. Нажмите "Оставить жалобу" или "Внести предложение"
2. Выберите школьную локацию из списка
3. Подробно опишите проблему или предложение
4. Отправьте сообщение - ваше обращение будет сохранено

<b>Условные обозначения:</b>
🔴 - жалобы (проблемы, которые нужно решить)
🟢 - предложения (идеи для улучшения)
🏫🍽⚽ - эмодзи локаций

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
    """Просмотр обращений по локациям"""
    await safe_answer(callback)
    
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
    
    await safe_edit_message(
        callback=callback,
        text=text,
        reply_markup=get_locations_keyboard(view_only=True)
    )

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
        
        # Разделяем на жалобы и предложения
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
    """Просмотр жалоб или предложений для локации с пагинацией"""
    await safe_answer(callback)
    
    try:
        # Парсим callback_data: view_complaints_loc_1 или view_complaints_loc_1_page_1
        callback_data = callback.data
        
        # Определяем тип обращений (жалобы или предложения)
        if "complaints" in callback_data:
            feedback_type = "complaint"
            prefix = "view_complaints_loc_"
        else:
            feedback_type = "suggestion"
            prefix = "view_suggestions_loc_"
        
        # Убираем префикс, чтобы получить остальные данные
        data_without_prefix = callback_data[len(prefix):]
        
        # Разбираем оставшиеся части
        parts = data_without_prefix.split('_')
        
        # Первый элемент всегда location_id
        location_id = int(parts[0])
        
        # Ищем номер страницы (если есть)
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
        
        # Фильтруем обращения по типу и локации
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
        
        # Настраиваем пагинацию
        items_per_page = 5
        total_items = len(filtered_feedbacks)
        total_pages = (total_items + items_per_page - 1) // items_per_page
        page = max(1, min(page, total_pages))
        
        # Получаем данные для текущей страницы
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        page_feedbacks = filtered_feedbacks[start_idx:end_idx]
        
        # Формируем текст
        type_text = "жалоб" if feedback_type == "complaint" else "предложений"
        type_emoji = "🔴" if feedback_type == "complaint" else "🟢"
        
        text = f"""
<b>{location['emoji']} {location['name']}</b>
<b>{type_emoji} {type_text.capitalize()} (страница {page}/{total_pages})</b>

<b>Конфиденциальность:</b>
✅ Все обращения отображаются анонимно

"""
        
        for i, fb in enumerate(page_feedbacks, start=start_idx + 1):
            # Анонимизируем текст
            safe_text = anonymize_text(fb['text'])
            text += f"""
<b>{i}. {type_emoji} {fb['date']}</b>
<b>Текст:</b> {safe_text}
"""
        
        # Формируем клавиатуру с пагинацией
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
        feedback_type = parts[1]  # complaint или suggestion
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
        
        # Сохраняем обращение
        save_feedback(
            feedback_type=feedback_type,
            location_id=location_id,
            text=message.text.strip(),
            user_id=message.from_user.id,
            username=message.from_user.username
        )
        
        # Получаем информацию для подтверждения
        location = get_location_full_info(location_id)
        type_text = "жалоба" if feedback_type == "complaint" else "предложение"
        type_emoji = "🔴" if feedback_type == "complaint" else "🟢"
        
        # Отправляем подтверждение
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
        
        # Очищаем состояние
        await state.clear()
        
        # Отправляем уведомление администраторам (с реальными данными)
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
        
        # Группируем по локациям
        locations = get_locations()
        location_map = {loc["id"]: f"{loc['emoji']} {loc['name']}" for loc in locations}
        
        text = f"""
<b>{type_emoji} Все {type_text} ({len(filtered_feedbacks)})</b>

<b>Конфиденциальность:</b>
✅ Все обращения отображаются анонимно

"""
        
        # Берем последние 10 обращений
        recent_feedbacks = sorted(filtered_feedbacks, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]
        
        for fb in recent_feedbacks:
            location_name = location_map.get(fb["location_id"], f"Локация #{fb['location_id']}")
            safe_text = anonymize_text(fb['text'], 100)
            
            text += f"""
<b>{location_name}</b>
<i>{fb['date']}</i>
<code>{safe_text}</code>
"""
        
        # Добавляем статистику по локациям
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
        # Создаем CSV файл с обращениями
        import csv
        from io import StringIO
        
        feedbacks = get_feedbacks()
        locations = get_locations()
        location_map = {loc["id"]: loc["name"] for loc in locations}
        
        # Создаем CSV в памяти
        output = StringIO()
        writer = csv.writer(output, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        
        # Заголовки (админская версия с реальными данными)
        writer.writerow(["ID", "Дата", "Тип", "Локация", "Текст", "ID пользователя", "Username", "Публичный ID", "Статус"])
        
        # Данные
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
        
        # Создаем текстовый файл
        output.seek(0)
        csv_data = output.getvalue()
        
        # Сохраняем во временный файл
        with open("data/export.csv", "w", encoding="utf-8") as f:
            f.write(csv_data)
        
        # Отправляем файл
        with open("data/export.csv", "rb") as f:
            await message.answer_document(
                document=("feedbacks_export.csv", f),
                caption=f"""
📊 <b>Экспорт данных (Админ)</b>

<b>Обращений:</b> {len(feedbacks)}
<b>Дата экспорта:</b> {datetime.now().strftime('%d.%m.%Y %H:%M')}

<b>Примечание:</b>
• Содержит реальные данные пользователей
• Для публичного просмотра используются анонимные ID
"""
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
    logger.info("=" * 50)
    
    try:
        # Инициализация данных
        locations = get_locations()
        feedbacks = get_feedbacks()
        
        logger.info(f"📁 Загружено локаций: {len(locations)}")
        logger.info(f"📁 Загружено обращений: {len(feedbacks)}")
        
        # Запуск бота
        await dp.start_polling(bot, skip_updates=True)
        
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logger.info("Бот остановлен")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())