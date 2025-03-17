import os
import logging
import asyncio
import signal
from datetime import datetime
import uuid
import pathlib
from typing import Dict, Optional, Set, List, Tuple, Union, Any, cast
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, InputMediaAnimation, InputMediaPhoto
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv
from database import db
import aiofiles

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Common text constants
MAIN_MENU_TEXT = "*👨🏻‍💻 DOX: Анонимный Чат*\n\n" \
                 "• Полностью бесплатно;\n" \
                 "• 102% анонимности;\n" \
                 "• После окончания диалога, чат сразу удаляется\\."

ONBOARDING_TEXT = "*👨🏻‍💻 Добро пожаловать в DOX: Анонимный Чат*\n\n📝 Заполните быстро анкету, обычно это занимает 9 секунд и на 49% повышает качество поиска собеседников\\!\n\nВы можете изменить ее в любой момент в настройках\\."

# GIF URLs
ONBOARDING_GIF = "https://media.giphy.com/media/6L53JiITO021awrmsj/giphy.gif"
MAIN_MENU_GIF = "https://media.giphy.com/media/bYBaveMs5QvWkuhAmj/giphy.gif"

# Common keyboard layouts
MAIN_MENU_KEYBOARD = [
    [InlineKeyboardButton("🔍 Начать поиск", callback_data="search_chat")],
    [InlineKeyboardButton("👤 Профиль", callback_data="view_profile")],
    [InlineKeyboardButton("❓ Поддержка", url="https://t.me/DoxGames_bot")]
]

CHAT_CONTROL_KEYBOARD = [
    [
        InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
        InlineKeyboardButton("Завершить", callback_data="stop_chat"),
    ]
]

SEARCH_KEYBOARD = [[InlineKeyboardButton("❌ Отменить поиск", callback_data="cancel_search")]]

PROFILE_SETUP_KEYBOARD = [
    [InlineKeyboardButton("👤 Настроить профиль", callback_data="setup_profile")],
    [InlineKeyboardButton("↩️ Пропустить настройку", callback_data="skip_profile_setup")]
]

class BotState:
    """Class to manage bot state instead of using global variables."""
    
    def __init__(self):
        # Users currently searching for a chat
        self.users_searching: Set[int] = set()
        # Dictionary of active chats: user_id -> partner_id
        self.active_chats: Dict[int, int] = {}
        # Dictionary to store message IDs for each user
        self.user_messages: Dict[int, List[int]] = {}
        # Dictionary to store main message ID for each user: user_id -> message_id
        self.main_message_ids: Dict[int, int] = {}
        # Dictionary to store pin notification message IDs: user_id -> message_id
        self.pin_message_ids: Dict[int, int] = {}
        # Dictionary to store ID of first messages to protect them from deletion
        self.first_messages: Dict[int, int] = {}
        # Flag indicating if user is in chat initialization state
        # In this state messages are not deleted
        self.chat_initialization: Dict[int, bool] = {}
        # Whether to store media in the database (True) or on disk (False)
        self.store_media_in_db: bool = False
        # Dictionary to track if animation was shown to user
        self.animation_shown: Dict[int, bool] = {}

class ChatManager:
    """
    Class to manage chat operations like creation, search, and termination.
    """
    
    def __init__(self, state: BotState):
        """
        Initialize the chat manager with a reference to the bot state.
        
        Args:
            state: The bot state object
        """
        self.state = state
    
    async def start_search(self, user_id: int, context: ContextTypes.DEFAULT_TYPE, skip_message: bool = False) -> None:
        """
        Start searching for a chat partner for the user.
        
        Args:
            user_id: Telegram user ID
            context: Telegram bot context
            skip_message: If True, skip sending the search message (used when message is already shown)
        """
        # Проверяем, не находится ли пользователь уже в чате
        if user_id in self.state.active_chats or await db.get_active_chat(user_id):
            keyboard = CHAT_CONTROL_KEYBOARD
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update_main_message(
                user_id,
                context,
                "Вы уже находитесь в активном чате\\. Используйте кнопки ниже для управления чатом\\.",
                reply_markup
            )
            return
            
        # Проверяем, не ищет ли пользователь уже собеседника
        searching_users = await db.get_searching_users()
        if user_id in searching_users:
            keyboard = SEARCH_KEYBOARD
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update_main_message(
                user_id,
                context,
                "🔍 *Поиск собеседника*\n\n"
                "Вы уже ищете собеседника\\. Пожалуйста, подождите\\.\n\n"
                "Когда кто\\-то будет найден, я вам сообщу\\.",
                reply_markup
            )
            return
            
        # Add user to searching list
        self.state.users_searching.add(user_id)
        
        # Also mark user as searching in the database
        await db.set_user_searching(user_id, True)
        
        # Update main message with search status only if not skipped
        if not skip_message:
            keyboard = SEARCH_KEYBOARD
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update_main_message(
                user_id,
                context,
                "🔍 *Поиск собеседника*\n\n"
                "Ищем для вас собеседника\\.\n"
                "Это может занять некоторое время\\.\n\n"
                "Когда кто\\-то будет найден, я вам сообщу\\.",
                reply_markup
            )
    
    async def cancel_search(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Cancel an ongoing search for a chat partner.
        
        Args:
            user_id: Telegram user ID
            context: Telegram bot context
        """
        # Проверяем, находится ли пользователь в поиске
        searching_users = await db.get_searching_users()
        if user_id not in searching_users and user_id not in self.state.users_searching:
            return
            
        # Remove user from searching list
        self.state.users_searching.discard(user_id)
        
        # Update database status
        await db.set_user_searching(user_id, False)
    
    async def find_match(self, user_id: int) -> Optional[int]:
        """
        Find a matching chat partner for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Partner ID if found, None otherwise
        """
        try:
            # Получаем пользователей, ищущих собеседника
            waiting_users = await db.get_searching_users()
            logger.info(f"User {user_id} searching for partner. Waiting users: {waiting_users}")
            
            # Исключаем пользователей, которые уже в чате
            active_users = set()
            for uid in waiting_users:
                if uid in self.state.active_chats or await db.get_active_chat(uid):
                    active_users.add(uid)
                    logger.info(f"User {uid} is in active chat, excluding from search")
            
            waiting_users = [uid for uid in waiting_users if uid not in active_users and uid != user_id]
            logger.info(f"Filtered waiting users for {user_id}: {waiting_users}")
            
            if not waiting_users:
                logger.info(f"No waiting users found for {user_id}")
                return None

            # Проверяем профиль пользователя
            has_profile = await db.has_completed_profile(user_id)
            logger.info(f"User {user_id} has profile: {has_profile}")
            
            if has_profile:
                # Получаем профиль и интересы пользователя
                user_profile = await db.get_user_profile(user_id)
                user_interests = await db.get_user_interests(user_id)
                logger.info(f"User {user_id} profile: {user_profile}, interests: {user_interests}")
                
                # Ищем лучшего совпадения
                best_match = None
                max_common_interests = -1
                
                for waiting_user_id in waiting_users:
                    # Проверяем профиль ожидающего пользователя
                    waiting_user_has_profile = await db.has_completed_profile(waiting_user_id)
                    logger.info(f"Waiting user {waiting_user_id} has profile: {waiting_user_has_profile}")
                    
                    if waiting_user_has_profile:
                        waiting_user_profile = await db.get_user_profile(waiting_user_id)
                        waiting_user_interests = await db.get_user_interests(waiting_user_id)
                        logger.info(f"Waiting user {waiting_user_id} profile: {waiting_user_profile}, interests: {waiting_user_interests}")
                        
                        # Проверяем соответствие по полу
                        gender_match = True
                        
                        if user_profile and waiting_user_profile:
                            # Проверяем предпочтения пользователя
                            if (user_profile.get('looking_for') and 
                                user_profile['looking_for'].lower() != 'any' and
                                waiting_user_profile.get('gender') and
                                user_profile['looking_for'].lower() != waiting_user_profile['gender'].lower()):
                                gender_match = False
                                logger.info(f"Gender mismatch for user {user_id} and {waiting_user_id}")
                            
                            # Проверяем предпочтения ожидающего пользователя
                            if (waiting_user_profile.get('looking_for') and 
                                waiting_user_profile['looking_for'].lower() != 'any' and
                                user_profile.get('gender') and
                                waiting_user_profile['looking_for'].lower() != user_profile['gender'].lower()):
                                gender_match = False
                                logger.info(f"Gender mismatch for user {waiting_user_id} and {user_id}")
                        
                        if gender_match:
                            # Считаем общие интересы
                            common_interests = set(user_interests).intersection(set(waiting_user_interests))
                            logger.info(f"Common interests between {user_id} and {waiting_user_id}: {common_interests}")
                            
                            if len(common_interests) > max_common_interests:
                                max_common_interests = len(common_interests)
                                best_match = waiting_user_id
                                logger.info(f"New best match found: {waiting_user_id} with {max_common_interests} common interests")
            
            if best_match:
                # Проверяем, что партнер все еще в поиске
                searching_users = await db.get_searching_users()
                if best_match not in searching_users:
                    logger.info(f"Best match {best_match} is no longer searching")
                    return None
                
                # Помечаем партнера как найденного
                await db.set_user_searching(best_match, False)
                self.state.users_searching.discard(best_match)
                logger.info(f"Found best match {best_match} for user {user_id}")
                return best_match
        
            # Если нет подходящих совпадений по профилю или у пользователя нет профиля,
            # берем первого доступного пользователя из списка ожидающих
            partner_id = waiting_users[0]
            logger.info(f"No profile match found, using first available partner: {partner_id}")
            
            # Проверяем, что партнер все еще в поиске
            searching_users = await db.get_searching_users()
            if partner_id not in searching_users:
                logger.info(f"Partner {partner_id} is no longer searching")
                return None
            
            # Помечаем партнера как найденного
            await db.set_user_searching(partner_id, False)
            self.state.users_searching.discard(partner_id)
            logger.info(f"Found partner {partner_id} for user {user_id}")
            return partner_id
            
        except Exception as e:
            logger.error(f"Error in find_match for user {user_id}: {e}")
            return None
    
    async def create_chat(self, user_id: int, partner_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """Create a new chat between two users."""
        try:
            logger.info(f"Creating chat between {user_id} and {partner_id}")
            
            # Create chat in database first
            chat_id = await db.create_chat(user_id, partner_id)
            if not chat_id:
                logger.error(f"Failed to create chat in database for {user_id} and {partner_id}")
                return None
                
            logger.info(f"Created chat in database with ID: {chat_id}")
            
            # Only after successful chat creation, update searching status
            await db.set_user_searching(user_id, False)
            await db.set_user_searching(partner_id, False)
            self.state.users_searching.discard(user_id)
            self.state.users_searching.discard(partner_id)
                
            # Add users to active chats
            self.state.active_chats[user_id] = partner_id
            self.state.active_chats[partner_id] = user_id
            logger.info(f"Added users to active chats: {user_id} <-> {partner_id}")
            
            # Get user profiles
            user_profile = await db.get_user_profile(user_id)
            partner_profile = await db.get_user_profile(partner_id)
            logger.info(f"Got profiles - User: {user_profile}, Partner: {partner_profile}")
            
            # Get user interests
            user_interests = await db.get_user_interests(user_id)
            partner_interests = await db.get_user_interests(partner_id)
            logger.info(f"Got interests - User: {user_interests}, Partner: {partner_interests}")
            
            # Format profile information
            user_profile_text = await self._format_profile_info(user_profile, user_interests)
            partner_profile_text = await self._format_profile_info(partner_profile, partner_interests)
            
            # Get the search message from state
            user_message_id = self.state.main_message_ids.get(user_id)
            partner_message_id = self.state.main_message_ids.get(partner_id)
            logger.info(f"Message IDs - User: {user_message_id}, Partner: {partner_message_id}")
            
            # Create keyboard with chat controls
            keyboard = CHAT_CONTROL_KEYBOARD
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Edit existing messages to show chat started
            chat_started_text = (
                "🎯 *Собеседник найден\\!*\n\n"
                f"{partner_profile_text}\n\n"
                "Используйте кнопки ниже для управления чатом\\."
            )
            
            # Обрабатываем сообщение для первого пользователя
            try:
                if user_message_id:
                    message = await context.bot.edit_message_media(
                        chat_id=user_id,
                        message_id=user_message_id,
                        media=InputMediaAnimation(
                            media=MAIN_MENU_GIF,
                            caption=chat_started_text,
                            parse_mode="MarkdownV2"
                        ),
                        reply_markup=reply_markup
                    )
                    # Закрепляем сообщение
                    await context.bot.pin_chat_message(
                        chat_id=user_id,
                        message_id=message.message_id,
                        disable_notification=True
                    )
                    logger.info(f"Updated and pinned message for user {user_id}")
            except Exception as e:
                logger.error(f"Error updating message for user {user_id}: {e}")
            
            # Обрабатываем сообщение для второго пользователя
            try:
                if partner_message_id:
                    message = await context.bot.edit_message_media(
                        chat_id=partner_id,
                        message_id=partner_message_id,
                        media=InputMediaAnimation(
                            media=MAIN_MENU_GIF,
                            caption=chat_started_text,
                            parse_mode="MarkdownV2"
                        ),
                        reply_markup=reply_markup
                    )
                    # Закрепляем сообщение
                    await context.bot.pin_chat_message(
                        chat_id=partner_id,
                        message_id=message.message_id,
                        disable_notification=True
                    )
                    logger.info(f"Updated and pinned message for partner {partner_id}")
            except Exception as e:
                logger.error(f"Error updating message for partner {partner_id}: {e}")
            
            return chat_id
            
        except Exception as e:
            logger.error(f"Error in create_chat: {e}")
            return None
    
    async def _format_profile_info(self, profile, interests):
        """
        Format profile information for display.
        
        Args:
            profile: User profile data
            interests: User interests
            
        Returns:
            Formatted profile text
        """
        if not profile:
            return "Профиль не настроен\\."
        
        profile_text = ""
        
        # Gender
        if profile.get('gender'):
            gender_text = {
                'male': "👨 Мужской",
                'female': "👩 Женский",
                'other': "🧑 Другой"
            }.get(profile['gender'], "Не указан")
            profile_text += f"• *Пол:* {gender_text}\n"
        
        # Age
        if profile.get('age'):
            profile_text += f"• *Возраст:* {profile['age']}\n"
            
        # Looking for
        if profile.get('looking_for'):
            looking_for_text = {
                'male': "👨 Мужской",
                'female': "👩 Женский",
                'any': "👥 Любой"
            }.get(profile['looking_for'], "Не указано")
            profile_text += f"• *Ищет:* {looking_for_text}\n"
        
        # Interests
        if interests:
            # Экранируем специальные символы для MarkdownV2
            escaped_interests = [interest.replace('.', '\\.').replace('-', '\\-').replace('!', '\\!').replace('(', '\\(').replace(')', '\\)') for interest in interests]
            profile_text += f"• *Интересы:* {', '.join(escaped_interests)}"
        
        return profile_text
    
    async def end_chat(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        End a chat for a user.
        
        Args:
            user_id: Telegram user ID
            context: Telegram bot context
            
        Returns:
            True if chat was ended successfully, False otherwise
        """
        # Check if user is in an active chat
        if user_id not in self.state.active_chats:
            return False
        
        partner_id = self.state.active_chats[user_id]
        
        # Get active chat from database
        chat_result = await db.get_active_chat(user_id)
        if not chat_result:
            return False
        
        chat_id, _ = chat_result
        
        # End chat in database
        result = await db.end_chat(chat_id)
        if not result:
            return False
        
        # Automatically unpin messages for both users
        try:
            # Unpin all messages for both users
            # (Это останется для тех случаев, когда функция end_chat вызывается напрямую)
            await context.bot.unpin_all_chat_messages(chat_id=user_id)
            if partner_id:
                await context.bot.unpin_all_chat_messages(chat_id=partner_id)
            
            # Удаляем только уведомления о закреплении, но не сами закрепленные сообщения
            # (Удаление самих сообщений делают функции stop_chat_new и skip_chat_new)
            try:
                await delete_pin_message(user_id, context)
                if partner_id:
                    await delete_pin_message(partner_id, context)
            except Exception as e:
                logger.error(f"Error deleting pin messages: {e}")
        except Exception as e:
            logger.error(f"Error unpinning messages: {e}")
        
        # Remove from active chats dictionary
        if user_id in self.state.active_chats:
            del self.state.active_chats[user_id]
        if partner_id in self.state.active_chats:
            del self.state.active_chats[partner_id]
        
        # Удаление сообщений происходит в функциях stop_chat_new и skip_chat_new
        
        return True

# Create a global instance of the BotState class
state = BotState()

# Create a global instance of the ChatManager class
chat_manager = ChatManager(state)

# Directory for storing media files
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# Constants for profile setup states
PROFILE_SETUP_NONE = "none"
PROFILE_SETUP_GENDER = "gender"
PROFILE_SETUP_LOOKING_FOR = "looking_for"
PROFILE_SETUP_AGE = "age"
PROFILE_SETUP_INTERESTS = "interests"
PROFILE_SETUP_COMPLETE = "complete"

def escape_markdown_v2(text: str) -> str:
    """
    Escape special characters for MarkdownV2 format.
    
    Args:
        text: Text to escape
        
    Returns:
        Escaped text
    """
    # Characters that need escaping in MarkdownV2
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in special_chars:
        text = text.replace(char, '\\' + char)
    
    return text

async def download_media_file(
    context: ContextTypes.DEFAULT_TYPE, 
    file_id: str, 
    message_type: str
) -> Tuple[Optional[str], str, Optional[bytes], Optional[str]]:
    """
    Download a media file from Telegram and return the file path and content.
    
    Args:
        context: Telegram bot context
        file_id: File ID in Telegram
        message_type: Message type (photo, video, voice, sticker, video_note)
        
    Returns:
        Tuple of (file_path, unique_id, file_content, file_name)
        If state.store_media_in_db is True, file_content contains the file bytes
        If state.store_media_in_db is False, file_path contains the local path
    """
    try:
        # Generate a unique identifier for the file
        unique_id = str(uuid.uuid4())
        
        # Extension and MIME type mappings for different media types
        extensions = {
            "photo": ".jpg",
            "video": ".mp4",
            "voice": ".ogg",
            "sticker": ".webp",
            "video_note": ".mp4",
            "animation": ".mp4",
            "audio": ".mp3",
            "document": ""  # Will be determined from file path
        }
    
        mime_types = {
            "photo": "image/jpeg",
            "video": "video/mp4",
                "voice": "audio/ogg",
        "sticker": "image/webp",
            "video_note": "video/mp4",
            "animation": "video/mp4",
            "audio": "audio/mpeg",
            "document": "application/octet-stream"  # Default
    }
    
        # Get file from Telegram
        tg_file = await context.bot.get_file(file_id)
        
        # Determine file extension and original filename
        file_path = tg_file.file_path
        original_filename = os.path.basename(file_path)
        
        # Try to get extension from file path
        file_ext = os.path.splitext(file_path)[1]
        if not file_ext:
            # If no extension in original path, use the mapping
            file_ext = extensions.get(message_type, "")
        
        # Determine mime type
        mime_type = mime_types.get(message_type, "application/octet-stream")
        
        # For documents, try to infer mime type based on extension
        if message_type == "document" and file_ext:
            # Simple extension to mime type mapping for common formats
            ext_to_mime = {
                ".pdf": "application/pdf",
                ".doc": "application/msword",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xls": "application/vnd.ms-excel",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".ppt": "application/vnd.ms-powerpoint",
                ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ".zip": "application/zip",
                ".rar": "application/x-rar-compressed",
                ".7z": "application/x-7z-compressed",
                ".txt": "text/plain",
                ".json": "application/json",
                ".xml": "application/xml",
                ".html": "text/html",
                ".css": "text/css",
                ".js": "application/javascript",
                ".csv": "text/csv"
            }
            mime_type = ext_to_mime.get(file_ext.lower(), "application/octet-stream")
        
        # Create a file name with the unique ID
        new_filename = f"{unique_id}{file_ext}"
        
        # Define the media directory - using relative path instead of absolute
        MEDIA_DIR = "media"
        
        # Create media type subdirectory if storing locally
        # Create directory structure
        media_type_dir = os.path.join(MEDIA_DIR, message_type)
        pathlib.Path(media_type_dir).mkdir(parents=True, exist_ok=True)
        local_path = os.path.join(media_type_dir, new_filename)
        
        # Download both file content for database and save to disk for backup
        media_content = await tg_file.download_as_bytearray()
        
        # Always save to disk
        await tg_file.download_to_drive(custom_path=local_path)
        logger.info(f"Downloaded {message_type} to {local_path}")
        
        # Return both the local path and file content
        return local_path, unique_id, media_content, mime_type
    
    except Exception as e:
        logger.error(f"Error downloading media file: {e}")
        return None, "", None, "application/octet-stream"

async def delete_messages(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delete pending messages for a user.
    
    Args:
        user_id: Telegram user ID
        context: Telegram bot context
    """
    if user_id not in state.user_messages:
        return
    
    # Skip message deletion if user is in chat initialization state
    if user_id in state.chat_initialization and state.chat_initialization[user_id]:
            return
        
    # If user has a first message in active chat, protect it from deletion
    protected_message_id = state.first_messages.get(user_id)
    
    message_ids_to_delete = []
    for message_id in state.user_messages[user_id]:
        # Skip protected message
        if protected_message_id and message_id == protected_message_id:
                continue
        message_ids_to_delete.append(message_id)
                
    for message_id in message_ids_to_delete:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=message_id)
            except Exception as e:
                logger.warning(f"Could not delete message {message_id} for user {user_id}: {e}")
    
    # Keep only protected message in user_messages
    if protected_message_id:
        state.user_messages[user_id] = [protected_message_id]
    else:
        state.user_messages[user_id] = []

async def clear_all_messages(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Clear all messages for a user, including protected ones.
    
    Args:
        user_id: Telegram user ID
        context: Telegram bot context
    """
    if user_id not in state.user_messages:
            return
            
    for message_id in state.user_messages[user_id]:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=message_id)
            except Exception as e:
                logger.warning(f"Could not delete message {message_id} for user {user_id}: {e}")
    
    # Clear all stored messages
    state.user_messages[user_id] = []
    
    # Clear first message reference if exists
    if user_id in state.first_messages:
        del state.first_messages[user_id]

async def update_main_message(
    user_id: int, 
    context: ContextTypes.DEFAULT_TYPE, 
    new_text: str, 
    keyboard: Optional[InlineKeyboardMarkup] = None
) -> None:
    """
    Update or send the main menu message for a user.
    
    Args:
        user_id: Telegram user ID
        context: Telegram bot context
        new_text: New message text
        keyboard: Optional inline keyboard
    """
    try:
        # Get message_id from database first
        message_id = await db.get_main_message_id(user_id)
        
        # If not in database, check memory cache
        if message_id is None and user_id in state.main_message_ids:
            message_id = state.main_message_ids[user_id]
        
        # If message ID exists, try to edit the message
        if message_id:
            try:
                # Make sure text is properly escaped for MarkdownV2
                # Only escape if the text doesn't already contain escaped chars
                if '\\' not in new_text:
                    new_text = escape_markdown_v2(new_text)
                
                # Try to edit existing message
                message = await context.bot.edit_message_text(
                    chat_id=user_id,
                    message_id=message_id,
                    text=new_text,
                    reply_markup=keyboard,
                    parse_mode="MarkdownV2"
                )
                
                # Make sure we store this message ID
                state.main_message_ids[user_id] = message.message_id
                await db.update_main_message_id(user_id, message.message_id)
                
                # Add to user messages if not already there
                if user_id not in state.user_messages:
                    state.user_messages[user_id] = []
                
                if message.message_id not in state.user_messages[user_id]:
                    state.user_messages[user_id].append(message.message_id)
                
                return
            except Exception as e:
                logger.warning(f"Could not edit main message for user {user_id}: {e}")
                # Fall through to sending a new message
        
        # Make sure text is properly escaped for MarkdownV2
        # Only escape if the text doesn't already contain escaped chars
        if '\\' not in new_text:
            new_text = escape_markdown_v2(new_text)
            
        # Send a new message if editing fails or no message ID exists
        message = await context.bot.send_message(
            chat_id=user_id,
            text=new_text,
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )
            
        # Store the new message ID
        state.main_message_ids[user_id] = message.message_id
        await db.update_main_message_id(user_id, message.message_id)
        
        # Add to user messages
        if user_id not in state.user_messages:
            state.user_messages[user_id] = []
        state.user_messages[user_id].append(message.message_id)
    
    except Exception as e:
        logger.error(f"Error updating main message for user {user_id}: {e}")

async def home_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command handler for /home and callback for 'home' button."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        # Редактируем существующее сообщение
        try:
            await query.edit_message_media(
                media=InputMediaAnimation(
                    media=MAIN_MENU_GIF,
                    caption=MAIN_MENU_TEXT,
                    parse_mode="MarkdownV2"
                ),
                reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            )
        except Exception as e:
            logger.error(f"Error editing animation message: {e}")
            # Если не удалось отредактировать с гифкой, редактируем текст
            await query.edit_message_text(
                text=MAIN_MENU_TEXT,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            )
    elif update.message and update.effective_user:
        user_id = update.effective_user.id
        # Для прямой команды отправляем новое сообщение
        try:
            await context.bot.send_animation(
                chat_id=user_id,
                animation=MAIN_MENU_GIF,
                caption=MAIN_MENU_TEXT,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            )
        except Exception as e:
            logger.error(f"Error sending animation: {e}")
            await context.bot.send_message(
                chat_id=user_id,
                text=MAIN_MENU_TEXT,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            )
    else:
        return

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler."""
    user = update.effective_user
    if not user:
        return

    user_id = user.id
    
    # Add user to database
    await db.add_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # Проверяем, заполнена ли анкета
    profile_state, _ = await db.get_profile_setup_state(user_id)
    
    # Если анкета заполнена, отправляем сразу на главное меню
    if profile_state == PROFILE_SETUP_COMPLETE:
        # Перенаправляем на home_command
        if update.message:
            # Создаем фейковый объект Update с message
            fake_update = Update(update_id=update.update_id, message=update.message)
            await home_command(fake_update, context)
        else:
            # Создаем фейковый объект Update с callback_query
            fake_update = Update(update_id=update.update_id, callback_query=update.callback_query)
            await home_command(fake_update, context)
        return
    
    # Если анкета не заполнена, показываем стандартное приветствие
    # Создаем клавиатуру
    keyboard = [
        [InlineKeyboardButton("✍️ Начать", callback_data="setup_profile")],
        [InlineKeyboardButton("↩️ Пропустить настройку", callback_data="skip_profile_setup")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Определяем, есть ли сообщение для удаления
    message_to_delete = None
    if update.callback_query and update.callback_query.message:
        # Если это callback query
        message_to_delete = update.callback_query.message
    
    try:
        # Если есть сообщение для удаления, удаляем его
        if message_to_delete:
            await message_to_delete.delete()
        
        # Отправляем новое сообщение с анимацией
        message = await context.bot.send_animation(
            chat_id=user_id,
            animation=ONBOARDING_GIF,
            caption=ONBOARDING_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        
        # Сохраняем ID сообщения
        state.main_message_ids[user_id] = message.message_id
    except Exception as e:
        logger.error(f"Error handling start command: {e}")
        # В случае ошибки отправляем текстовое сообщение
        try:
            message = await context.bot.send_message(
                chat_id=user_id,
                text=ONBOARDING_TEXT,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            state.main_message_ids[user_id] = message.message_id
        except Exception as e:
            logger.error(f"Error sending fallback message: {e}")

async def search_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Start searching for a chat partner.
    
    This function is triggered when a user clicks the "Start Search" button.
    It either finds a waiting user to chat with or puts the current user
    in the waiting list.
    """
    query = update.callback_query
    if not query or not query.from_user:
        return
    
    await query.answer()
    user_id = query.from_user.id
    
    logger.info(f"User {user_id} started search")
    
    # Check if user is already in an active chat
    if user_id in state.active_chats:
        partner_id = state.active_chats[user_id]
        logger.info(f"User {user_id} is already in chat with {partner_id}")
        
        # Get partner's profile and interests
        partner_profile = await db.get_user_profile(partner_id)
        partner_interests = await db.get_user_interests(partner_id)
        
        # Format partner's profile information
        partner_profile_text = await chat_manager._format_profile_info(partner_profile, partner_interests)
        
        # Create chat control keyboard
        keyboard = CHAT_CONTROL_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_media(
                media=InputMediaAnimation(
                    media=MAIN_MENU_GIF,
                    caption=f"🎯 *Текущий собеседник*\n\n{partner_profile_text}\n\nИспользуйте кнопки ниже для управления чатом\\.",
                    parse_mode="MarkdownV2"
                ),
                reply_markup=reply_markup
            )
            
            # Save message ID in state
            state.main_message_ids[user_id] = query.message.message_id
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            
        return

    # Проверяем статус поиска также из базы данных
    searching_users = await db.get_searching_users()
    is_searching = user_id in searching_users or user_id in state.users_searching
    
    logger.info(f"User {user_id} search status - DB: {user_id in searching_users}, State: {user_id in state.users_searching}")
    
    # Check if user is already searching
    if is_searching:
        logger.info(f"User {user_id} is already searching")
        # Update main message with search controls
        keyboard = SEARCH_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_media(
                media=InputMediaAnimation(
                    media=MAIN_MENU_GIF,
                    caption="Вы уже ищете собеседника\\. Пожалуйста, подождите\\.\n\nКогда собеседник будет найден, я вам сообщу\\.",
                    parse_mode="MarkdownV2"
                ),
                reply_markup=reply_markup
            )
            
            # Save message ID in state
            state.main_message_ids[user_id] = query.message.message_id
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            
        return
    
    # Add user to searching list first
    state.users_searching.add(user_id)
    await db.set_user_searching(user_id, True)
    logger.info(f"Added user {user_id} to searching lists")
    
    # Try to find a match for this user
    partner_id = await chat_manager.find_match(user_id)
    logger.info(f"Search result for user {user_id}: partner_id={partner_id}")
    
    if partner_id:
        logger.info(f"Found partner {partner_id} for user {user_id}")
        # Save message IDs before creating chat
        state.main_message_ids[user_id] = query.message.message_id
        state.main_message_ids[partner_id] = state.main_message_ids.get(partner_id)
        
        # Create a new chat between these users
        chat_id = await chat_manager.create_chat(user_id, partner_id, context)
        if not chat_id:
            logger.error(f"Failed to create chat between {user_id} and {partner_id}")
            # If chat creation failed, put users back in search
            state.users_searching.add(user_id)
            state.users_searching.add(partner_id)
            await db.set_user_searching(user_id, True)
            await db.set_user_searching(partner_id, True)
            
            # Update message with search status
            keyboard = SEARCH_KEYBOARD
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            try:
                await query.edit_message_media(
                    media=InputMediaAnimation(
                        media=MAIN_MENU_GIF,
                        caption="🔍 *Поиск собеседника*\n\n"
                        "Ищем для вас собеседника\\.\n"
                        "Это может занять некоторое время\\.\n\n"
                        "Когда кто\\-то будет найден, я вам сообщу\\.",
                        parse_mode="MarkdownV2"
                    ),
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.error(f"Error updating message after failed chat creation: {e}")
    else:
        logger.info(f"No partner found for user {user_id}, staying in search")
        # No partner found, update message with search status
        keyboard = SEARCH_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_media(
                media=InputMediaAnimation(
                    media=MAIN_MENU_GIF,
                    caption="🔍 *Поиск собеседника*\n\n"
                    "Ищем для вас собеседника\\.\n"
                    "Это может занять некоторое время\\.\n\n"
                    "Когда кто\\-то будет найден, я вам сообщу\\.",
                    parse_mode="MarkdownV2"
                ),
                reply_markup=reply_markup
            )
            
            # Save message ID in state
            state.main_message_ids[user_id] = query.message.message_id
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            return

async def cancel_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Cancel the search for a chat partner.

    This function is triggered when a user clicks the "Cancel Search" button.
    It removes the user from the waiting list and returns them to the main menu.
    """
    query = update.callback_query
    if not query or not query.from_user:
        return
    
    await query.answer()
    user_id = query.from_user.id
    
    # Remove user from searching list
    state.users_searching.discard(user_id)
    # Also mark user as not searching in the database
    await db.set_user_searching(user_id, False)
    
    # Return to main menu
    keyboard = MAIN_MENU_KEYBOARD
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await query.edit_message_media(
            media=InputMediaAnimation(
                media=MAIN_MENU_GIF,
                caption=MAIN_MENU_TEXT,
                parse_mode="MarkdownV2"
            ),
            reply_markup=reply_markup
        )
        # Save message ID in state
        state.main_message_ids[user_id] = query.message.message_id
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        return

async def delete_pin_message(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Delete the pin message using stored message ID."""
    try:
        # Получаем информацию о чате
        chat = await context.bot.get_chat(user_id)
        
        # Если в чате есть закрепленное сообщение
        if chat.pinned_message:
            pinned_message_id = chat.pinned_message.message_id
            logger.info(f"Found pinned message {pinned_message_id} for user {user_id}")
            
            # Пробуем удалить несколько сообщений после закрепленного
            for i in range(1, 5):
                try:
                    await context.bot.delete_message(
                        chat_id=user_id,
                        message_id=pinned_message_id + i
                    )
                    logger.info(f"Successfully deleted message {pinned_message_id + i} for user {user_id}")
                except Exception:
                    continue
                    
    except Exception as e:
        logger.error(f"Error handling pin message deletion for user {user_id}: {e}")

async def pin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pin a message in the chat."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # Проверяем, находится ли пользователь в активном чате
    if user_id not in state.active_chats:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return
    
    # Проверяем, есть ли сообщение для закрепления (ответ на сообщение)
    if not update.message.reply_to_message:
        await update.message.reply_text("Для закрепления сообщения ответьте на него командой /pin")
        return
    
    message_to_pin = update.message.reply_to_message
    
    try:
        # Закрепляем сообщение
        await context.bot.pin_chat_message(
            chat_id=user_id,
            message_id=message_to_pin.message_id,
            disable_notification=True
        )
        
        # Удаляем уведомление о закреплении
        await asyncio.sleep(1)
        await delete_pin_message(user_id, context)
        
        logger.info(f"Successfully pinned message {message_to_pin.message_id} for user {user_id}")
    except Exception as e:
        logger.error(f"Error pinning message: {e}")
        await update.message.reply_text("Не удалось закрепить сообщение.")

async def unpin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unpin all messages in the chat."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # Проверяем, находится ли пользователь в активном чате
    if user_id not in state.active_chats:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return
    
    try:
        # Открепляем все сообщения
        await context.bot.unpin_all_chat_messages(chat_id=user_id)
        
        # Удаляем уведомления о закреплении
        await delete_pin_message(user_id, context)
        
        await update.message.reply_text("Все закрепленные сообщения откреплены.")
        logger.info(f"Successfully unpinned all messages for user {user_id}")
    except Exception as e:
        logger.error(f"Error unpinning messages: {e}")
        await update.message.reply_text("Не удалось открепить сообщения.")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop command handler. Ends the current chat."""
    if not update.message or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Проверяем, находится ли пользователь в активном чате
    if user_id not in state.active_chats:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return
    
    # Получаем ID партнера перед завершением чата
    partner_id = state.active_chats.get(user_id)
    
    # Очищаем все сообщения у обоих пользователей
    await clear_all_messages(user_id, context)
    if partner_id:
        await clear_all_messages(partner_id, context)
    
    # End current chat
    success = await chat_manager.end_chat(user_id, context)
    if not success:
        await update.message.reply_text("Не удалось завершить чат.")
        return
    
    # Return to main menu
    keyboard = MAIN_MENU_KEYBOARD
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        message = await context.bot.send_animation(
            chat_id=user_id,
            animation=MAIN_MENU_GIF,
            caption=MAIN_MENU_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        # Save message ID in state
        state.main_message_ids[user_id] = message.message_id
    except Exception as e:
        logger.error(f"Error sending animation: {e}")
        # В случае ошибки отправляем текстовое сообщение
        message = await context.bot.send_message(
            chat_id=user_id,
            text=MAIN_MENU_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        state.main_message_ids[user_id] = message.message_id

async def skip_chat_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Skip the current chat and immediately start searching for a new one.
    
    This function is triggered when a user clicks the "Skip Chat" button.
    It ends the current chat and automatically starts searching for a new chat partner.
    """
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    logger.info(f"[C1] User {user_id} pressed SKIP chat button")
    
    # Проверяем, есть ли активный чат
    if user_id not in state.active_chats:
        logger.warning(f"[C1] User {user_id} tried to skip chat but has no active chat")
        await query.message.reply_text("❌ У вас нет активного чата")
        return
    
    # Получаем ID партнера
    partner_id = state.active_chats[user_id]
    logger.info(f"[C2] Partner {partner_id} will be notified about chat skip")
    
    # Отправляем сообщение партнеру
    if partner_id:
        try:
            partner_message = await context.bot.send_animation(
                chat_id=partner_id,
                animation=MAIN_MENU_GIF,
                caption="❌ *Ваш собеседник покинул чат*\n\n"
                     "Выберите действие:",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Новый собеседник", callback_data="search_chat")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="home")]
                ])
            )
            state.main_message_ids[partner_id] = partner_message.message_id
        except Exception as e:
            logger.error(f"Error sending message to partner: {e}")

    # Очищаем историю чата
    await clear_all_messages(user_id, context)
    if partner_id:
        await clear_all_messages(partner_id, context)

    # Завершаем текущий чат
    if partner_id:
        # Удаляем из активных чатов
        if user_id in state.active_chats:
            del state.active_chats[user_id]
        if partner_id in state.active_chats:
            del state.active_chats[partner_id]
        
        # Обновляем статус чата в базе данных
        chat_result = await db.get_active_chat(user_id)
        if chat_result:
            chat_id, _ = chat_result
            await db.end_chat(chat_id)
            
        # Очищаем сообщения из state
        if user_id in state.user_messages:
            state.user_messages[user_id] = []
        if partner_id in state.user_messages:
            state.user_messages[partner_id] = []

    # Добавляем пользователя в список поиска
    state.users_searching.add(user_id)
    await db.set_user_searching(user_id, True)
    
    try:
        # Открепляем сообщение и удаляем уведомление о закреплении
        try:
            await context.bot.unpin_all_chat_messages(chat_id=user_id)
            await delete_pin_message(user_id, context)
        except Exception as e:
            logger.error(f"Error unpinning message: {e}")
            
        # Редактируем существующее сообщение
        await query.edit_message_media(
            media=InputMediaAnimation(
                media=MAIN_MENU_GIF,
                caption="🔍 *Поиск собеседника*\n\n"
                     "Ищем для вас собеседника\\.\n"
                     "Это может занять некоторое время\\.\n\n"
                     "Когда кто\\-то будет найден, я вам сообщу\\.",
                parse_mode="MarkdownV2"
            ),
            reply_markup=InlineKeyboardMarkup(SEARCH_KEYBOARD)
        )
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        # В случае ошибки редактирования отправляем новое сообщение
        await update_main_message(
            user_id,
            context,
            "🔍 *Поиск собеседника*\n\n"
            "Ищем для вас собеседника\\.\n"
            "Это может занять некоторое время\\.\n\n"
            "Когда кто\\-то будет найден, я вам сообщу\\.",
            InlineKeyboardMarkup(SEARCH_KEYBOARD)
        )
    logger.info(f"[C1] User {user_id} started searching for new chat")

async def stop_chat_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the stop chat button press"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    logger.info(f"[C1] User {user_id} pressed STOP chat button")
    
    # Проверяем, есть ли активный чат
    if user_id not in state.active_chats:
        logger.warning(f"[C1] User {user_id} tried to stop chat but has no active chat")
        await query.message.reply_text("❌ У вас нет активного чата")
        return
    
    # Получаем ID партнера
    partner_id = state.active_chats[user_id]
    logger.info(f"[C2] Partner {partner_id} will be notified about chat end")
    
    # Уведомляем партнера о том, что пользователь покинул чат
    if partner_id:
        try:
            partner_message = await context.bot.send_animation(
                chat_id=partner_id,
                animation=MAIN_MENU_GIF,
                caption="❌ *Ваш собеседник покинул чат*\n\n"
                     "Выберите действие:",
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔍 Новый собеседник", callback_data="search_chat")],
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="home")]
                ])
            )
            state.main_message_ids[partner_id] = partner_message.message_id
        except Exception as e:
            logger.error(f"Error sending message to partner: {e}")

    # Очищаем историю чата
    await clear_all_messages(user_id, context)
    if partner_id:
        await clear_all_messages(partner_id, context)
        
    # Очищаем сообщения из state
    if user_id in state.user_messages:
        state.user_messages[user_id] = []
    if partner_id and partner_id in state.user_messages:
        state.user_messages[partner_id] = []
    
    # Завершаем чат
    success = await chat_manager.end_chat(user_id, context)
    
    if success:
        logger.info(f"[CHAT] Successfully ended chat between C1={user_id} and C2={partner_id}")
        # Открепляем сообщение и удаляем уведомление о закреплении
        try:
            await context.bot.unpin_all_chat_messages(chat_id=user_id)
            await delete_pin_message(user_id, context)
            if partner_id:
                await context.bot.unpin_all_chat_messages(chat_id=partner_id)
                await delete_pin_message(partner_id, context)
        except Exception as e:
            logger.error(f"Error unpinning messages and deleting pin notifications: {e}")
            
        # Возвращаемся в главное меню
        await home_command(update, context)
        logger.info(f"[C1] User {user_id} returned to main menu")
    else:
        logger.error(f"[CHAT] Failed to end chat for users C1={user_id}, C2={partner_id}")
        await query.message.reply_text("❌ Произошла ошибка при завершении чата")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear chat history for both users."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # Check if user is in chat
    if user_id not in state.active_chats:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return
    
    partner_id = state.active_chats[user_id]
    
    # Double-check with database
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        # Clean up state if database doesn't have the chat
        if user_id in state.active_chats:
            del state.active_chats[user_id]
        if partner_id in state.active_chats:
            del state.active_chats[partner_id]
        
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return

    chat_id, _ = active_chat
    
    try:
        # Устанавливаем флаг инициализации чата для обоих пользователей
        state.chat_initialization[user_id] = True
        state.chat_initialization[partner_id] = True
        
        # Сохраняем ID команды, чтобы не удалять ее дважды
        command_message_id = update.message.message_id
        
        # Сохраняем информацию о первых сообщениях перед очисткой
        logger.info(f"Before clearing history - FIRST_MESSAGES: {state.first_messages}")
        logger.info(f"Before clearing history - USER_MESSAGES for {user_id}: {state.user_messages.get(user_id, [])}")
        logger.info(f"Before clearing history - USER_MESSAGES for {partner_id}: {state.user_messages.get(partner_id, [])}")
        
        # Временно сохраняем ID первых сообщений
        user_first_msg = state.first_messages.get(user_id)
        partner_first_msg = state.first_messages.get(partner_id)
        
        # Delete all messages except first ones from Telegram only
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)
        
        # Get current keyboard
        keyboard = CHAT_CONTROL_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Update main messages with notification
        await update_main_message(
            user_id,
            context,
            "✅ *История чата очищена*\n\n"
            "Все сообщения были удалены\\. Вы можете продолжить общение\\.",
            reply_markup
        )
        
        await update_main_message(
            partner_id,
            context,
            "✅ *История чата очищена собеседником*\n\n"
            "Все сообщения были удалены вашим собеседником\\. Вы можете продолжить общение\\.",
            reply_markup
        )
        
        # Снимаем флаг инициализации чата для обоих пользователей
        state.chat_initialization[user_id] = False
        state.chat_initialization[partner_id] = False
            
    except Exception as e:
        # Снимаем флаг инициализации чата в случае ошибки
        state.chat_initialization[user_id] = False
        if partner_id:
            state.chat_initialization[partner_id] = False
        logger.error(f"Error clearing history: {e}")
        await update.message.reply_text("Не удалось очистить историю чата.")

async def handle_service_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle service messages like pin notifications."""
    if not update.message or not update.effective_user:
        return
        
    user_id = update.effective_user.id
    message = update.message
    
    # Проверяем, является ли это сообщением о закреплении
    if message.pinned_message or (message.text and ("pinned" in message.text.lower() or "закрепил" in message.text.lower())):
        logger.info(f"Found pin notification message {message.message_id} for user {user_id}")
        try:
            # Сразу пытаемся удалить это сообщение
            await context.bot.delete_message(
                chat_id=user_id,
                message_id=message.message_id
            )
            logger.info(f"Successfully deleted pin notification {message.message_id}")
        except Exception as e:
            logger.error(f"Error deleting pin notification: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle user messages."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    message = update.message
    message_id = message.message_id
    
    logger.info(f"Handling message {message_id} from user {user_id}")

    # Добавляем сохранение ID сообщения в самом начале функции
    if user_id not in state.user_messages:
        state.user_messages[user_id] = []
    
    # Добавляем ID сообщения отправителя в список немедленно
    state.user_messages[user_id].append(message_id)
    logger.info(f"Added message {message_id} to user_messages for user {user_id}")
    
    # Установка флага инициализации чата для пользователя
    state.chat_initialization[user_id] = True
    
    # Если это первое сообщение пользователя, сохраняем его ID
    if user_id not in state.first_messages:
        state.first_messages[user_id] = message_id
        logger.info(f"Saved first message {message_id} for user {user_id}")
    
    # Определяем тип сообщения
    message_type = None
    content = None
    file_id = None
    
    if message.text:
        message_type = "text"
        content = message.text
    elif message.photo:
        message_type = "photo"
        file_id = message.photo[-1].file_id  # Берем самое большое разрешение
        content = message.caption  # Сохраняем подпись к фото, если есть
    elif message.video:
        message_type = "video"
        file_id = message.video.file_id
        content = message.caption  # Сохраняем подпись к видео, если есть
    elif message.voice:
        message_type = "voice"
        file_id = message.voice.file_id
    elif message.sticker:
        message_type = "sticker"
        file_id = message.sticker.file_id
    elif message.video_note:
        message_type = "video_note"
        file_id = message.video_note.file_id
    else:
        # Если тип сообщения не поддерживается, отправляем уведомление
        await context.bot.send_message(
            chat_id=user_id,
            text="Этот тип сообщений не поддерживается."
        )
        state.chat_initialization[user_id] = False
        return

    # Check if user is in active chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        keyboard = MAIN_MENU_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            user_id,
            context,
            "Вы не находитесь в активном чате\\. Используйте кнопки ниже, чтобы начать поиск собеседника\\.",
            reply_markup
        )
        
        # Снимаем флаг инициализации чата
        state.chat_initialization[user_id] = False    
        return

    chat_id, partner_id = active_chat

    try:
        # Скачиваем медиафайл, если это не текстовое сообщение
        local_file_path = None
        file_content = None
        file_name = None
        mime_type = None
        
        if message_type != "text" and file_id:
            # Получаем локальный путь, расширение, содержимое файла и MIME-тип
            local_file_path, extension, file_content, mime_type = await download_media_file(context, file_id, message_type)
            # Генерируем имя файла
            file_name = f"{message_type}_{uuid.uuid4()}{extension}"
        
        # Store message in database (все типы сообщений)
        await db.add_message(
            chat_id=chat_id, 
            sender_id=user_id, 
            content=content, 
            message_type=message_type, 
            file_id=file_id, 
            local_file_path=local_file_path,
            file_name=file_name,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Инициализируем список сообщений для партнера, если нужно
        if partner_id not in state.user_messages:
            state.user_messages[partner_id] = []
        
        # Forward message to partner based on type
        sent_message = None
        
        if message_type == "text":
            sent_message = await context.bot.send_message(
                chat_id=partner_id,
                text=content
            )
        elif message_type == "photo":
            sent_message = await context.bot.send_photo(
                chat_id=partner_id,
                photo=file_id,
                caption=message.caption
            )
        elif message_type == "video":
            sent_message = await context.bot.send_video(
                chat_id=partner_id,
                video=file_id,
                caption=message.caption
            )
        elif message_type == "voice":
            sent_message = await context.bot.send_voice(
                chat_id=partner_id,
                voice=file_id
            )
        elif message_type == "sticker":
            sent_message = await context.bot.send_sticker(
                chat_id=partner_id,
                sticker=file_id
            )
        elif message_type == "video_note":
            sent_message = await context.bot.send_video_note(
                chat_id=partner_id,
                video_note=file_id
            )
        
        # Если сообщение успешно отправлено, добавляем его ID в список получателя
        if sent_message:
            logger.info(f"Successfully sent message to partner {partner_id}, message_id: {sent_message.message_id}")
            
            # Устанавливаем флаг инициализации чата для партнера
            state.chat_initialization[partner_id] = True
            
            state.user_messages[partner_id].append(sent_message.message_id)
            
            # Если это первое сообщение, полученное партнером, сохраняем его ID
            if partner_id not in state.first_messages:
                state.first_messages[partner_id] = sent_message.message_id
                logger.info(f"Saved first received message {sent_message.message_id} for partner {partner_id}")
            
            # Снимаем флаг инициализации чата для партнера
            state.chat_initialization[partner_id] = False
        
        # В конце обработки снимаем флаг инициализации чата для отправителя
        state.chat_initialization[user_id] = False
        
    except Exception as e:
        # В случае ошибки тоже снимаем флаг
        state.chat_initialization[user_id] = False
        logger.error(f"Error handling message from {user_id}: {e}")
        # Отправляем сообщение об ошибке пользователю
        await context.bot.send_message(
            chat_id=user_id,
            text="Произошла ошибка при отправке сообщения. Пожалуйста, попробуйте еще раз."
        )

async def view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View user profile."""
    query = None
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
    elif update.message:
        user_id = update.message.from_user.id
    else:
        return

    # Get user profile for display
    profile_text = await UserProfileManager.get_user_profile_text(user_id)
 
    # Create keyboard for profile actions
    keyboard = [
        [InlineKeyboardButton("✏️ Редактировать профиль", callback_data="edit_profile")],
        [InlineKeyboardButton("🔍 Начать поиск", callback_data="search_chat")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="home")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
 
    # Show profile - текстом без гифки
    if query:
        try:
            # Удаляем предыдущее сообщение, чтобы избавиться от гифки
            await query.message.delete()
            
            # Отправляем новое текстовое сообщение
            message = await context.bot.send_message(
                chat_id=user_id,
                text=profile_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            
            # Сохраняем ID нового сообщения
            state.main_message_ids[user_id] = message.message_id
        except Exception as e:
            logger.error(f"Error sending profile message: {e}")
    else:
        # Для случая, когда открывается через команду /profile
        try:
            message = await context.bot.send_message(
                chat_id=user_id,
                text=profile_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            state.main_message_ids[user_id] = message.message_id
        except Exception as e:
            logger.error(f"Error sending profile message: {e}")

async def setup_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start profile setup process."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Update profile setup state to gender selection
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_GENDER, 1)
    
    # Вместо редактирования GIF-сообщения, удаляем его и отправляем новое текстовое сообщение
    try:
        await query.message.delete()
        # Отправляем новое сообщение с выбором пола
        await UserProfileManager.send_gender_selection(user_id, context)
    except Exception as e:
        logger.error(f"Error deleting onboarding message: {e}")
        # Пробуем отправить новое сообщение в любом случае
        await UserProfileManager.send_gender_selection(user_id, context)

async def skip_profile_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skip profile setup and go to main menu."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Mark profile setup as complete
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_COMPLETE, 5)
    
    # Удаляем старое сообщение с GIF
    try:
        await query.message.delete()
        
        # Отправляем новое сообщение с главным меню и GIF
        message = await context.bot.send_animation(
            chat_id=user_id,
            animation=MAIN_MENU_GIF,
            caption=MAIN_MENU_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
        )
        
        # Сохраняем ID сообщения
        state.main_message_ids[user_id] = message.message_id
    except Exception as e:
        logger.error(f"Error in skip_profile_setup: {e}")
        # В случае ошибки отправляем текстовое сообщение
        try:
            message = await context.bot.send_message(
                chat_id=user_id,
                text=MAIN_MENU_TEXT,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            )
            state.main_message_ids[user_id] = message.message_id
        except Exception as e2:
            logger.error(f"Error sending fallback main menu: {e2}")

async def set_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user gender in profile setup."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Extract gender from callback data
    gender = query.data.split('_')[1]
    
    # Save gender in database
    await db.save_user_profile(user_id, gender=gender)
    
    # Update profile setup state to looking for selection
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_LOOKING_FOR, 2)
    
    # Show looking for selection menu by editing the existing message
    await UserProfileManager.send_looking_for_selection(user_id, context, query=query, edit_message=True)

async def set_looking_for(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set looking for preference in profile setup."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Extract looking for preference from callback data
    looking_for = query.data.split('_')[2]
    
    # Save looking for preference in database
    await db.save_user_profile(user_id, looking_for=looking_for)
    
    # Update profile setup state to age selection
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_AGE, 3)
    
    # Show age selection menu by editing the existing message
    await UserProfileManager.send_age_selection(user_id, context, query=query, edit_message=True)

async def handle_age_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle age selection in profile setup."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Extract age from callback data
    age_data = query.data.split('_')[1]
    age = 50 if age_data == "50plus" else int(age_data)
    
    # Save age in database
    await db.save_user_profile(user_id, age=age)
    
    # Update profile setup state to interests selection
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_INTERESTS, 4)
    
    # Show interests selection menu by editing the existing message
    await UserProfileManager.send_interests_selection(user_id, context, query=query, edit_message=True)

async def toggle_interest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle selection of an interest."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    
    await query.answer()
    user_id = query.from_user.id
    
    # Extract interest name from callback data
    callback_data = query.data
    if callback_data.startswith("toggle_interest_"):
        interest_name = callback_data.replace("toggle_interest_", "")
    elif callback_data.startswith("interest_"):
        interest_name = callback_data.replace("interest_", "")
    else:
        logger.error(f"Unexpected callback data format: {callback_data}")
        return
    
    # Get current user interests
    user_interests = await db.get_user_interests(user_id)
    
    if interest_name in user_interests:
        # If already selected, remove it
        await db.remove_user_interest(user_id, interest_name)
    else:
        # If not selected, add it
        await db.save_user_interest(user_id, interest_name)
    
    # Get setup state
    profile_state, _ = await db.get_profile_setup_state(user_id)
    is_edit = profile_state == PROFILE_SETUP_COMPLETE
    
    # Refresh interests menu by editing the current message
    await UserProfileManager.send_interests_selection(user_id, context, is_edit, query=query, edit_message=True)

async def complete_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Complete the profile setup process."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Mark profile setup as complete
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_COMPLETE, 5)
    
    # Удаляем сообщение с выбором интересов
    try:
        await query.message.delete()
        
        # Отправляем новое сообщение с главным меню и GIF
        message = await context.bot.send_animation(
            chat_id=user_id,
            animation=MAIN_MENU_GIF,
            caption=MAIN_MENU_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
        )
        
        # Сохраняем ID сообщения
        state.main_message_ids[user_id] = message.message_id
    except Exception as e:
        logger.error(f"Error sending main menu after profile completion: {e}")
        # В случае ошибки отправляем текстовое сообщение
        try:
            message = await context.bot.send_message(
                chat_id=user_id,
                text=MAIN_MENU_TEXT,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            )
            state.main_message_ids[user_id] = message.message_id
        except Exception as e2:
            logger.error(f"Error sending fallback main menu: {e2}")

async def edit_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show profile edit menu."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
 
    # Create keyboard for profile edit options
    keyboard = [
        [InlineKeyboardButton("👤 Изменить пол", callback_data="edit_gender")],
        [InlineKeyboardButton("🔍 Изменить предпочтения", callback_data="edit_looking_for")],
        [InlineKeyboardButton("🔢 Изменить возраст", callback_data="edit_age")],
        [InlineKeyboardButton("🏷️ Изменить интересы", callback_data="edit_interests")],
        [InlineKeyboardButton("🔙 Назад", callback_data="view_profile")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Текст для редактирования профиля
    text = "*Редактирование профиля*\n\n" \
           "Выберите, что вы хотите изменить в вашем профиле:"
    
    try:
        # Всегда отправляем новое сообщение
        message = await context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
        
        # Сохраняем ID нового сообщения
        state.main_message_ids[user_id] = message.message_id
        
        # Удаляем предыдущее сообщение
        if query.message:
            try:
                await query.message.delete()
            except Exception as e:
                logger.error(f"Error deleting old message: {e}")
                
    except Exception as e:
        logger.error(f"Error sending edit profile message: {e}")

async def edit_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit gender in profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Show gender selection menu in edit mode
    await UserProfileManager.send_gender_selection(user_id, context, is_edit=True, query=query)

async def edit_looking_for(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit looking for preference in profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Show looking for preference selection menu in edit mode
    await UserProfileManager.send_looking_for_selection(user_id, context, is_edit=True, query=query)

async def edit_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit age in profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Show age selection menu in edit mode
    await UserProfileManager.send_age_selection(user_id, context, is_edit=True, query=query)

async def edit_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit interests in profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Show interests selection menu in edit mode
    await UserProfileManager.send_interests_selection(user_id, context, is_edit=True, query=query)

async def save_gender_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save gender edit in profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    
    await query.answer()
    user_id = query.from_user.id

    # Extract gender from callback data
    gender = query.data.split('_')[2]
    
    # Save gender in database
    await db.save_user_profile(user_id, gender=gender)
    
    # Show confirmation message
    try:
        await query.message.delete()
        
        # Return to profile edit menu
        await edit_profile(update, context)
        
    except Exception as e:
        logger.error(f"Error after saving gender: {e}")
        # Try to just edit the message
        try:
            # Create keyboard for profile edit options
            keyboard = [
                [InlineKeyboardButton("👤 Изменить пол", callback_data="edit_gender")],
                [InlineKeyboardButton("🔍 Изменить предпочтения", callback_data="edit_looking_for")],
                [InlineKeyboardButton("🔢 Изменить возраст", callback_data="edit_age")],
                [InlineKeyboardButton("🏷️ Изменить интересы", callback_data="edit_interests")],
                [InlineKeyboardButton("🔙 Назад", callback_data="view_profile")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Текст для редактирования профиля
            text = "*Редактирование профиля*\n\n" \
                   "✅ Пол успешно изменен!\n\n" \
                   "Выберите, что вы хотите изменить в вашем профиле:"
            
            await query.edit_message_text(
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e2:
            logger.error(f"Error editing message after gender save: {e2}")

async def save_looking_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save looking for preference edit in profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    
    await query.answer()
    user_id = query.from_user.id

    # Extract looking_for preference from callback data
    looking_for = query.data.split('_')[3]
    
    # Save looking_for preference in database
    await db.save_user_profile(user_id, looking_for=looking_for)
    
    # Show confirmation message
    try:
        await query.message.delete()
        
        # Return to profile edit menu
        await edit_profile(update, context)
        
    except Exception as e:
        logger.error(f"Error after saving looking_for: {e}")
        # Try to just edit the message
        try:
            # Create keyboard for profile edit options
            keyboard = [
                [InlineKeyboardButton("👤 Изменить пол", callback_data="edit_gender")],
                [InlineKeyboardButton("🔍 Изменить предпочтения", callback_data="edit_looking_for")],
                [InlineKeyboardButton("🔢 Изменить возраст", callback_data="edit_age")],
                [InlineKeyboardButton("🏷️ Изменить интересы", callback_data="edit_interests")],
                [InlineKeyboardButton("🔙 Назад", callback_data="view_profile")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Текст для редактирования профиля
            text = "*Редактирование профиля*\n\n" \
                   "✅ Предпочтения успешно изменены!\n\n" \
                   "Выберите, что вы хотите изменить в вашем профиле:"
            
            await query.edit_message_text(
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e2:
            logger.error(f"Error editing message after looking_for save: {e2}")

async def save_age_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save age edit in profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    
    await query.answer()
    user_id = query.from_user.id

    # Extract age from callback data
    age_data = query.data.split('_')[2]
    age = 50 if age_data == "50plus" else int(age_data)
    
    # Save age in database
    await db.save_user_profile(user_id, age=age)
    
    # Show confirmation message
    try:
        await query.message.delete()
        
        # Return to profile edit menu
        await edit_profile(update, context)
        
    except Exception as e:
        logger.error(f"Error after saving age: {e}")
        # Try to just edit the message
        try:
            # Create keyboard for profile edit options
            keyboard = [
                [InlineKeyboardButton("👤 Изменить пол", callback_data="edit_gender")],
                [InlineKeyboardButton("🔍 Изменить предпочтения", callback_data="edit_looking_for")],
                [InlineKeyboardButton("🔢 Изменить возраст", callback_data="edit_age")],
                [InlineKeyboardButton("🏷️ Изменить интересы", callback_data="edit_interests")],
                [InlineKeyboardButton("🔙 Назад", callback_data="view_profile")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Текст для редактирования профиля
            text = "*Редактирование профиля*\n\n" \
                   "✅ Возраст успешно изменен!\n\n" \
                   "Выберите, что вы хотите изменить в вашем профиле:"
            
            await query.edit_message_text(
                text=text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
        except Exception as e2:
            logger.error(f"Error editing message after age save: {e2}")

async def media_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show media statistics."""
    # Сюда добавить код показа статистики по медиа
    pass

async def resend_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resend media from history."""
    # Сюда добавить код повторной отправки медиа
    pass

async def toggle_storage_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between storing media in DB or on disk."""
    if not update.message or not update.effective_user:
        return
        
    user_id = update.effective_user.id
    
    # Check if user is admin (better to implement proper admin check)
    
    # Toggle the storage mode
    state.store_media_in_db = not state.store_media_in_db
    
    # Inform about the current mode
    mode_text = "в базе данных" if state.store_media_in_db else "на диске в папке /media"
    await update.message.reply_text(
        f"Режим хранения медиафайлов изменен. Текущий режим: {mode_text}.\n\n"
        f"Сообщения всегда сохраняются в базе данных."
    )
    
    logger.info(f"Storage mode toggled to: {'DB' if state.store_media_in_db else 'DISK'}")

async def import_media_to_db(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Import media from disk to database."""
    # Сюда добавить код импорта медиа с диска в базу данных
    pass

async def init_db(application: Application) -> None:
    """Initialize database connection."""
    # Сюда добавить код инициализации базы данных
    pass

async def cleanup_db(application: Application) -> None:
    """Close database connection."""
    # Сюда добавить код закрытия соединения с базой данных
    pass

class UserProfileManager:
    """Class to manage user profile operations."""
    
    @staticmethod
    async def get_user_profile_text(user_id: int) -> str:
        """Get formatted profile text for display."""
        # Получаем профиль пользователя из базы данных
        user_profile = await db.get_user_profile(user_id)
        user_interests = await db.get_user_interests(user_id)
        
        if not user_profile:
            return "*Ваш профиль*\n\nПрофиль не заполнен\\."
        
        profile_text = "*Ваш профиль*\n\n"
        
        # Gender
        if user_profile.get('gender'):
            gender_text = {
                'male': "👨 Мужской",
                'female': "👩 Женский",
                'other': "🧑 Другой"
            }.get(user_profile['gender'], "Не указан")
            profile_text += f"• *Пол:* {gender_text}\n"
        
        # Age
        if user_profile.get('age'):
            profile_text += f"• *Возраст:* {user_profile['age']}\n"
            
        # Looking for
        if user_profile.get('looking_for'):
            looking_for_text = {
                'male': "👨 Мужской",
                'female': "👩 Женский",
                'any': "👥 Любой"
            }.get(user_profile['looking_for'], "Не указано")
            profile_text += f"• *Ищет:* {looking_for_text}\n"
        
        # Interests
        if user_interests:
            # Экранируем специальные символы для MarkdownV2
            escaped_interests = [interest.replace('.', '\\.').replace('-', '\\-').replace('!', '\\!').replace('(', '\\(').replace(')', '\\)') for interest in user_interests]
            profile_text += f"• *Интересы:* {', '.join(escaped_interests)}"
        
        return profile_text
    
    @staticmethod
    async def send_gender_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query=None, edit_message=False) -> None:
        """Send gender selection menu."""
        # Create keyboard for gender selection
        if is_edit:
            keyboard = [
                [InlineKeyboardButton("👨 Мужской", callback_data="gender_edit_male")],
                [InlineKeyboardButton("👩 Женский", callback_data="gender_edit_female")],
                [InlineKeyboardButton("🧑 Другой", callback_data="gender_edit_other")],
                [InlineKeyboardButton("🔙 Назад", callback_data="edit_profile")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("👨 Мужской", callback_data="gender_male")],
                [InlineKeyboardButton("👩 Женский", callback_data="gender_female")],
                [InlineKeyboardButton("🧑 Другой", callback_data="gender_other")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "*Укажите ваш пол*\n\n" \
               "Этот параметр будет виден всем вашим собеседникам\\."
        
        # If editing or requested to edit the message
        if (is_edit or edit_message) and query:
            try:
                # If we need to edit the message instead of deleting it
                if edit_message:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state if needed
                    if query.message:
                        state.main_message_ids[user_id] = query.message.message_id
                else:
                    # Delete the previous message if needed
                    await query.message.delete()
                    message = await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state
                    state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending gender selection message: {e}")
                # Fallback to edit the message
                try:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                except Exception as e2:
                    logger.error(f"Error editing message for gender selection: {e2}")
        else:
            # For initial setup, send a new message
            try:
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
                # Save message ID in state
                state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending gender selection message: {e}")
    
    @staticmethod
    async def send_looking_for_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query=None, edit_message=False) -> None:
        """Send looking for selection menu."""
        # Create keyboard for looking for preference selection
        if is_edit:
            keyboard = [
                [InlineKeyboardButton("👨 Мужской", callback_data="looking_for_edit_male")],
                [InlineKeyboardButton("👩 Женский", callback_data="looking_for_edit_female")],
                [InlineKeyboardButton("👥 Любой", callback_data="looking_for_edit_any")],
                [InlineKeyboardButton("🔙 Назад", callback_data="edit_profile")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("👨 Мужской", callback_data="looking_for_male")],
                [InlineKeyboardButton("👩 Женский", callback_data="looking_for_female")],
                [InlineKeyboardButton("👥 Любой", callback_data="looking_for_any")]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "*Кого вы ищете?*\n\n" \
               "Укажите пол предпочитаемых собеседников\\."
        
        # If editing or requested to edit the message
        if (is_edit or edit_message) and query:
            try:
                # If we need to edit the message instead of deleting it
                if edit_message:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state if needed
                    if query.message:
                        state.main_message_ids[user_id] = query.message.message_id
                else:
                    # Delete the previous message if needed
                    await query.message.delete()
                    message = await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state
                    state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending looking_for selection message: {e}")
                # Fallback to edit the message
                try:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                except Exception as e2:
                    logger.error(f"Error editing message for looking_for selection: {e2}")
        else:
            # For initial setup, send a new message
            try:
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
                # Save message ID in state
                state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending looking_for selection message: {e}")
    
    @staticmethod
    async def send_age_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query=None, edit_message=False) -> None:
        """Send age selection menu."""
        # Create keyboard for age selection
        keyboard = []
        
        # Create rows of age options (3 per row)
        age_options = []
        for age in range(18, 50, 5):
            row = []
            for i in range(5):
                current_age = age + i
                if current_age >= 50:
                    break
                    
                if is_edit:
                    callback_data = f"age_edit_{current_age}"
                else:
                    callback_data = f"age_{current_age}"
                    
                row.append(InlineKeyboardButton(f"{current_age}", callback_data=callback_data))
                
            if row:
                keyboard.append(row)
        
        # Add 50+ option and back button
        if is_edit:
            keyboard.append([InlineKeyboardButton("50+", callback_data="age_edit_50plus")])
            keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="edit_profile")])
        else:
            keyboard.append([InlineKeyboardButton("50+", callback_data="age_50plus")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "*Укажите ваш возраст*\n\n" \
               "Этот параметр будет виден всем вашим собеседникам\\."
        
        # If editing or requested to edit the message
        if (is_edit or edit_message) and query:
            try:
                # If we need to edit the message instead of deleting it
                if edit_message:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state if needed
                    if query.message:
                        state.main_message_ids[user_id] = query.message.message_id
                else:
                    # Delete the previous message if needed
                    await query.message.delete()
                    message = await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state
                    state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending age selection message: {e}")
                # Fallback to edit the message
                try:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                except Exception as e2:
                    logger.error(f"Error editing message for age selection: {e2}")
        else:
            # For initial setup, send a new message
            try:
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
                # Save message ID in state
                state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending age selection message: {e}")
    
    @staticmethod
    async def send_interests_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query=None, edit_message=False) -> None:
        """Send interests selection menu."""
        # Default list of available interests
        available_interests = [
            "🗣️ Общение", "💋 Флирт", "🔥 Темки"
        ]
        
        # Fetch user's current interests
        user_interests = await db.get_user_interests(user_id)
        
        # Create keyboard
        keyboard = []
        for interest in available_interests:
            # Check if this interest is already selected by the user
            is_selected = interest in user_interests
            
            # Format the button text to indicate selection
            button_text = f"✅ {interest}" if is_selected else f"☑️ {interest}"
            
            # Create callback data
            callback_data = f"toggle_interest_{interest}"
            
            # Add button to keyboard
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        # Add completion or back button
        if is_edit:
            keyboard.append([InlineKeyboardButton("🔙 Готово", callback_data="edit_profile")])
        else:
            keyboard.append([InlineKeyboardButton("✅ Завершить настройку", callback_data="complete_profile")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Format instructions text
        selected_count = len(user_interests) if user_interests else 0
        
        if is_edit:
            title = "*Редактирование интересов*"
        else:
            title = "*Выберите ваши интересы*"
        
        text = f"{title}\n\n" \
               f"Выбрано интересов: {selected_count}\n\n" \
               "Нажмите на интерес, чтобы добавить/убрать его из своего профиля\\. " \
               "Интересы будут видны вашим собеседникам и помогут найти людей с похожими увлечениями\\."
        
        # If editing or requested to edit the message
        if (is_edit or edit_message) and query:
            try:
                # If we need to edit the message instead of deleting it
                if edit_message:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state if needed
                    if query.message:
                        state.main_message_ids[user_id] = query.message.message_id
                else:
                    # Delete the previous message if needed
                    await query.message.delete()
                    message = await context.bot.send_message(
                        chat_id=user_id,
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Save message ID in state
                    state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending interests selection message: {e}")
                # Try to edit the message instead
                try:
                    await query.edit_message_text(
                        text=text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                except Exception as e2:
                    logger.error(f"Error editing message for interests selection: {e2}")
        else:
            # For initial setup, send a new message
            try:
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup
                )
                # Save message ID in state
                state.main_message_ids[user_id] = message.message_id
            except Exception as e:
                logger.error(f"Error sending interests selection message: {e}")

async def save_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save user interests and continue to complete profile."""
    query = update.callback_query
    if not query or not query.from_user:
        return

    await query.answer()
    user_id = query.from_user.id
    
    # Update profile setup state to complete
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_COMPLETE, 5)
    
    # Complete the profile setup
    await complete_profile(update, context)

def main() -> None:
    """Start the bot."""
    # Parse environment variables
    bot_token = os.getenv("BOT_TOKEN")
    database_url = os.getenv("DATABASE_URL")
    
    if not bot_token:
        logger.error("Bot token not found in environment variables")
        return
    
    if not database_url:
        logger.error("Database URL not found in environment variables")
        return
    
    # Create the Application and pass it the bot's token
    application = Application.builder().token(bot_token).build()

    # Connect to the database
    asyncio.get_event_loop().run_until_complete(db.connect(database_url))
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("home", home_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("pin", pin_message))
    application.add_handler(CommandHandler("unpin", unpin_message))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("media_stats", media_stats))
    application.add_handler(CommandHandler("resend_media", resend_media))
    application.add_handler(CommandHandler("toggle_storage", toggle_storage_mode))
    application.add_handler(CommandHandler("import_media", import_media_to_db))
    application.add_handler(CommandHandler("profile", view_profile))
    
    # Register callback query handlers
    application.add_handler(CallbackQueryHandler(search_chat, pattern="^search_chat$"))
    application.add_handler(CallbackQueryHandler(cancel_search, pattern="^cancel_search$"))
    application.add_handler(CallbackQueryHandler(stop_chat_new, pattern="^stop_chat$"))
    application.add_handler(CallbackQueryHandler(skip_chat_new, pattern="^skip_chat$"))
    application.add_handler(CallbackQueryHandler(start, pattern="^start$"))
    application.add_handler(CallbackQueryHandler(home_command, pattern="^home$"))
    
    # Register profile setup handlers
    application.add_handler(CallbackQueryHandler(setup_profile, pattern="^setup_profile$"))
    application.add_handler(CallbackQueryHandler(skip_profile_setup, pattern="^skip_profile_setup$"))
    application.add_handler(CallbackQueryHandler(set_gender, pattern="^gender_(?!edit_)"))
    application.add_handler(CallbackQueryHandler(set_looking_for, pattern="^looking_for_(?!edit_)"))
    application.add_handler(CallbackQueryHandler(handle_age_selection, pattern="^age_(?!edit_)"))
    application.add_handler(CallbackQueryHandler(toggle_interest, pattern="^toggle_interest_"))
    application.add_handler(CallbackQueryHandler(toggle_interest, pattern="^interest_"))
    application.add_handler(CallbackQueryHandler(complete_profile, pattern="^complete_profile$"))
    
    # Register callback handlers for main menu
    application.add_handler(CallbackQueryHandler(view_profile, pattern="^view_profile$"))
    application.add_handler(CallbackQueryHandler(edit_profile, pattern="^edit_profile$"))
    application.add_handler(CallbackQueryHandler(edit_gender, pattern="^edit_gender$"))
    application.add_handler(CallbackQueryHandler(edit_looking_for, pattern="^edit_looking_for$"))
    application.add_handler(CallbackQueryHandler(edit_age, pattern="^edit_age$"))
    application.add_handler(CallbackQueryHandler(edit_interests, pattern="^edit_interests$"))
    
    # Register new handlers for saving profile edits
    application.add_handler(CallbackQueryHandler(save_gender_edit, pattern="^gender_edit_"))
    application.add_handler(CallbackQueryHandler(save_looking_for_edit, pattern="^looking_for_edit_"))
    application.add_handler(CallbackQueryHandler(save_age_edit, pattern="^age_edit_"))
    
    # Register handler for service messages (should be before general message handler)
    application.add_handler(MessageHandler(
        (filters.StatusUpdate.PINNED_MESSAGE | 
         (filters.TEXT & filters.Regex(r'(?i).*(закрепил|pinned).*'))) & 
        filters.ChatType.PRIVATE,
        handle_service_message,
        block=False  # Важно: не блокируем другие обработчики
    ))
    
    # Register media message handlers
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.Sticker.ALL | filters.VIDEO_NOTE) & filters.ChatType.PRIVATE,
        handle_message
    ))
    
    # General text message handler (should be last)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set database lifecycle hooks
    application.post_init = init_db
    application.post_shutdown = cleanup_db

    # Run the bot until the user presses Ctrl-C
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
        main()