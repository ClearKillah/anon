import os
import logging
import asyncio
import signal
from datetime import datetime
import uuid
import pathlib
from typing import Dict, Optional, Set, List, Tuple, Union, Any, cast
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, InputMediaAnimation
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

ONBOARDING_TEXT = "👨🏻\\-💻 Добро пожаловать в DOX: Анонимный Чат\n\n📝 Заполните быстро анкету, обычно это занимает 9 секунд и на 49% повышает качество поиска собеседников\\!\n\nВы можете изменить ее в любой момент в настройках\\."

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
        self.store_media_in_db: bool = True
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
    
    async def create_chat(self, user_id: int, partner_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
        """Create a new chat between two users."""
        try:
            # Add users to active chats
            self.state.active_chats[user_id] = partner_id
            self.state.active_chats[partner_id] = user_id
            
            # Get user profiles
            user_profile = await db.get_user_profile(user_id)
            partner_profile = await db.get_user_profile(partner_id)
            
            # Get user interests
            user_interests = await db.get_user_interests(user_id)
            partner_interests = await db.get_user_interests(partner_id)
            
            # Format profile information
            user_profile_text = await self._format_profile_info(user_profile, user_interests)
            partner_profile_text = await self._format_profile_info(partner_profile, partner_interests)
            
            # Create chat in database
            chat_id = await db.create_chat(user_id, partner_id)
            
            # Get the search message from state
            user_message_id = self.state.main_message_ids.get(user_id)
            partner_message_id = self.state.main_message_ids.get(partner_id)
            
            # Create keyboard with chat controls
            keyboard = CHAT_CONTROL_KEYBOARD
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Edit existing messages to show chat started
            chat_started_text = (
                "🎯 *Собеседник найден\\!*\n\n"
                f"{partner_profile_text}\n\n"
                "Используйте кнопки ниже для управления чатом\\."
            )
            
            try:
                if user_message_id:
                    # Сначала изменяем сообщение на текстовое
                    await context.bot.edit_message_text(
                        chat_id=user_id,
                        message_id=user_message_id,
                        text=chat_started_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Закрепляем измененное сообщение
                    await context.bot.pin_chat_message(
                        chat_id=user_id,
                        message_id=user_message_id,
                        disable_notification=True
                    )
                    # Удаляем уведомление о закреплении
                    await asyncio.sleep(1)
                    await delete_pin_message(user_id, context)
            except Exception as e:
                logger.error(f"Error editing/pinning message for user {user_id}: {e}")
            
            chat_started_text = (
                "🎯 *Собеседник найден\\!*\n\n"
                f"{user_profile_text}\n\n"
                "Используйте кнопки ниже для управления чатом\\."
            )
            
            try:
                if partner_message_id:
                    # Сначала изменяем сообщение на текстовое
                    await context.bot.edit_message_text(
                        chat_id=partner_id,
                        message_id=partner_message_id,
                        text=chat_started_text,
                        parse_mode="MarkdownV2",
                        reply_markup=reply_markup
                    )
                    # Закрепляем измененное сообщение
                    await context.bot.pin_chat_message(
                        chat_id=partner_id,
                        message_id=partner_message_id,
                        disable_notification=True
                    )
                    # Удаляем уведомление о закреплении
                    await asyncio.sleep(1)
                    await delete_pin_message(partner_id, context)
            except Exception as e:
                logger.error(f"Error editing/pinning message for partner {partner_id}: {e}")
            
            return chat_id
            
        except Exception as e:
            logger.error(f"Error creating chat: {e}")
            # Clean up if chat creation fails
            self.state.active_chats.pop(user_id, None)
            self.state.active_chats.pop(partner_id, None)
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
            # Unpin messages for both users
            await context.bot.unpin_all_chat_messages(chat_id=user_id)
            await context.bot.unpin_all_chat_messages(chat_id=partner_id)
            
            # Delete pin notifications
            try:
                # Try to delete pin notifications multiple times
                for _ in range(3):
                    await delete_pin_message(user_id, context)
                    await delete_pin_message(partner_id, context)
                    await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error deleting pin messages: {e}")
        except Exception as e:
            logger.error(f"Error unpinning messages: {e}")
        
        # Clear messages from both users
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)
        
        # Remove from active chats dictionary
        if user_id in self.state.active_chats:
            del self.state.active_chats[user_id]
        if partner_id in self.state.active_chats:
            del self.state.active_chats[partner_id]
        
        # Remove first message records
        if user_id in self.state.first_messages:
            del self.state.first_messages[user_id]
        if partner_id in self.state.first_messages:
            del self.state.first_messages[partner_id]
        
        # Показываем сообщение только партнёру (пользователь будет перенаправлен на главный экран)
        end_chat_keyboard = [
            [InlineKeyboardButton("🏠 Домой", callback_data="home")],
            [InlineKeyboardButton("🔍 Начать поиск", callback_data="search_chat")],
            [InlineKeyboardButton("👤 Профиль", callback_data="view_profile")],
            [InlineKeyboardButton("❓ Поддержка", url="https://t.me/DoxGames_bot")]
        ]
        reply_markup = InlineKeyboardMarkup(end_chat_keyboard)
        
        await update_main_message(
            partner_id,
            context,
            "❌ *Чат завершен собеседником\\.*\n\n"
            "Диалог был завершен вашим собеседником\\. Нажмите кнопку *Начать поиск* для поиска нового собеседника\\.",
            reply_markup
        )
        
        return True
    
    async def find_match(self, user_id: int) -> Optional[int]:
        """
        Find a matching chat partner for a user.
        
        Args:
            user_id: Telegram user ID
            
        Returns:
            Partner ID if found, None otherwise
        """
        # Получаем пользователей, ищущих собеседника, из базы данных
        waiting_users = await db.get_searching_users()
        
        # Исключаем пользователей, которые уже в чате
        active_users = set()
        for uid in waiting_users:
            if uid in self.state.active_chats or await db.get_active_chat(uid):
                active_users.add(uid)
        
        waiting_users = [uid for uid in waiting_users if uid not in active_users]
        
        # Убираем текущего пользователя из списка ожидающих
        waiting_users = [uid for uid in waiting_users if uid != user_id]
        
        if not waiting_users:
            return None
            
        # Check if user has a profile for better matching
        has_profile = await db.has_completed_profile(user_id)
        
        if has_profile:
            # Try to find a match based on profile preferences
            user_profile = await db.get_user_profile(user_id)
            user_interests = await db.get_user_interests(user_id)
            
            best_match = None
            max_common_interests = -1
            
            for waiting_user_id in waiting_users:
                # Get waiting user profile and interests
                waiting_user_has_profile = await db.has_completed_profile(waiting_user_id)
                
                if waiting_user_has_profile:
                    waiting_user_profile = await db.get_user_profile(waiting_user_id)
                    waiting_user_interests = await db.get_user_interests(waiting_user_id)
                    
                    # Check gender preference match if specified
                    gender_match = True
                    
                    if user_profile and waiting_user_profile:
                        # Check if user is looking for specific gender and waiting user fits
                        if (user_profile.get('looking_for') and 
                            user_profile['looking_for'].lower() != 'any' and
                            waiting_user_profile.get('gender') and
                            user_profile['looking_for'].lower() != waiting_user_profile['gender'].lower()):
                            gender_match = False
                        
                        # Check if waiting user is looking for specific gender and user fits
                        if (waiting_user_profile.get('looking_for') and 
                            waiting_user_profile['looking_for'].lower() != 'any' and
                            user_profile.get('gender') and
                            waiting_user_profile['looking_for'].lower() != user_profile['gender'].lower()):
                            gender_match = False
                    
                    if gender_match:
                        # Calculate common interests
                        common_interests = set(user_interests).intersection(set(waiting_user_interests))
                        if len(common_interests) > max_common_interests:
                            max_common_interests = len(common_interests)
                            best_match = waiting_user_id
            
            if best_match:
                return best_match
        
        # Если нет подходящих совпадений по профилю или у пользователя нет профиля,
        # просто берем первого доступного пользователя из списка ожидающих
        return waiting_users[0]

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
        
        # Create media type subdirectory if storing locally
        if not state.store_media_in_db:
            # Create directory structure
            media_type_dir = os.path.join(MEDIA_DIR, message_type)
            pathlib.Path(media_type_dir).mkdir(parents=True, exist_ok=True)
            local_path = os.path.join(media_type_dir, new_filename)
        else:
            local_path = None
        
        # If storing in database, download file content
        if state.store_media_in_db:
            media_content = await tg_file.download_as_bytearray()
            return local_path, unique_id, media_content, mime_type
        
        # If storing locally, save to disk
        else:
            # Download to local path
            await tg_file.download_to_drive(custom_path=local_path)
            logger.info(f"Downloaded {message_type} to {local_path}")
            return local_path, unique_id, None, mime_type
    
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
    
    # Отправляем приветственное сообщение с гифкой
    keyboard = [
        [InlineKeyboardButton("✍️ Начать", callback_data="setup_profile")],
        [InlineKeyboardButton("↩️ Пропустить настройку", callback_data="skip_profile_setup")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_animation(
            chat_id=user_id,
            animation=ONBOARDING_GIF,
            caption=ONBOARDING_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error sending animation: {e}")
        # В случае ошибки отправляем текстовое сообщение
        await context.bot.send_message(
            chat_id=user_id,
            text=ONBOARDING_TEXT,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup
        )

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
    
    # Check if user is already in an active chat
    if user_id in state.active_chats:
        partner_id = state.active_chats[user_id]
        
        # Update main message with chat controls
        keyboard = CHAT_CONTROL_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_media(
                media=InputMediaAnimation(
                    media=MAIN_MENU_GIF,
                    caption="Вы уже находитесь в активном чате\\. Используйте кнопки ниже для управления чатом\\.",
                    parse_mode="MarkdownV2"
                ),
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Error editing message: {e}")
        return

    # Проверяем статус поиска также из базы данных
    searching_users = await db.get_searching_users()
    is_searching = user_id in searching_users or user_id in state.users_searching
    
    # Check if user is already searching
    if is_searching:
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
        except Exception as e:
            logger.error(f"Error editing message: {e}")
        return
    
    # Try to find a match for this user
    partner_id = await chat_manager.find_match(user_id)
    
    if partner_id:
        # Remove partner from searching list both locally and in DB
        state.users_searching.discard(partner_id)
        await db.set_user_searching(partner_id, False)
        
        # Save message IDs before creating chat
        state.main_message_ids[user_id] = query.message.message_id
        state.main_message_ids[partner_id] = state.main_message_ids.get(partner_id)
        
        # Create a new chat between these users
        await chat_manager.create_chat(user_id, partner_id, context)
    else:
        # No partner found, start searching
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
            # Save message ID in state
            state.main_message_ids[user_id] = query.message.message_id
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            return
            
        # Start the search process
        await chat_manager.start_search(user_id, context, skip_message=True)

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
    
    # Use the chat manager to cancel the search
    await chat_manager.cancel_search(user_id, context)
    
    # Return to main menu by editing the current message
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
        # Попытка удалить по сохраненному ID
        pin_message_id = await db.get_pin_message_id(user_id)
        if pin_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=user_id,
                    message_id=pin_message_id
                )
                logger.info(f"Deleted pin notification message {pin_message_id} for user {user_id}")
                await db.update_pin_message_id(user_id, None)
            except Exception as e:
                logger.error(f"Error deleting pin notification by ID: {e}")
        
        # Проактивный поиск сообщений о закреплении
        try:
            # Получить информацию о чате
            chat = await context.bot.get_chat(user_id)
            
            # Если в чате есть закрепленное сообщение
            if chat.pinned_message:
                pinned_message_id = chat.pinned_message.message_id
                
                # Получаем возможные ID сообщений уведомлений (обычно появляются сразу после закрепленного)
                possible_notification_ids = [
                    pinned_message_id + 1,
                    pinned_message_id + 2,
                    pinned_message_id + 3
                ]
                
                # Пытаемся удалить каждое возможное уведомление
                for msg_id in possible_notification_ids:
                    try:
                        await context.bot.delete_message(chat_id=user_id, message_id=msg_id)
                        logger.info(f"Proactively deleted potential pin notification: {msg_id}")
                    except Exception:
                        # Игнорируем ошибки, так как мы просто пытаемся угадать ID
                        pass
        except Exception as e:
            logger.error(f"Error in proactive pin notification cleanup: {e}")
            
    except Exception as e:
        logger.error(f"Error handling pin message deletion for user {user_id}: {e}")

async def skip_chat_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle skip chat button press."""
    query = update.callback_query
    if not query:
        return
        
    await query.answer()
    user_id = query.from_user.id
    
    # End current chat
    success = await chat_manager.end_chat(user_id, context)
    if not success:
        return
        
    # Start searching for new chat
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
        
    # Start the search process
    await chat_manager.start_search(user_id, context, skip_message=True)

async def stop_chat_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle stop chat button press."""
    query = update.callback_query
    if not query:
        return
        
    await query.answer()
    user_id = query.from_user.id
    
    # End current chat
    success = await chat_manager.end_chat(user_id, context)
    if not success:
        return
        
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

class MediaHandler:
    """
    Class to handle different types of media files in messages.
    Encapsulates logic for processing and forwarding media messages.
    """
    
    def __init__(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, partner_id: int):
        """
        Initialize the media handler.
        
        Args:
            context: Telegram bot context
            chat_id: Database chat ID
            user_id: Sender user ID
            partner_id: Receiver (partner) user ID
        """
        self.context = context
        self.chat_id = chat_id
        self.user_id = user_id
        self.partner_id = partner_id
    
    async def handle_text(self, text: str) -> Optional[Message]:
        """
        Handle text message.
        
        Args:
            text: Message text
            
        Returns:
            Sent message to partner
        """
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=text,
            message_type='text'
        )
        
        # Forward message to partner
        partner_message = await self.context.bot.send_message(
            chat_id=self.partner_id,
            text=text
        )
        
        return partner_message
    
    async def handle_photo(self, photo, caption: Optional[str] = None) -> Optional[Message]:
        """
        Handle photo message.
        
        Args:
            photo: Photo object
            caption: Optional caption text
            
        Returns:
            Sent message to partner
        """
        # Get highest resolution photo
        file_id = photo[-1].file_id
        
        # Download and store the photo
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'photo'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=caption,
            message_type='photo',
            file_id=file_id,
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward photo to partner
        partner_message = await self.context.bot.send_photo(
            chat_id=self.partner_id,
            photo=file_id,
            caption=caption
        )
        
        return partner_message
    
    async def handle_video(self, video, caption: Optional[str] = None) -> Optional[Message]:
        """
        Handle video message.
        
        Args:
            video: Video object
            caption: Optional caption text
            
        Returns:
            Sent message to partner
        """
        file_id = video.file_id
        
        # Download and store the video
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'video'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=caption,
            message_type='video',
            file_id=file_id,
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward video to partner
        partner_message = await self.context.bot.send_video(
            chat_id=self.partner_id,
            video=file_id,
            caption=caption
        )
        
        return partner_message
    
    async def handle_voice(self, voice) -> Optional[Message]:
        """
        Handle voice message.
        
        Args:
            voice: Voice object
            
        Returns:
            Sent message to partner
        """
        file_id = voice.file_id
        
        # Download and store the voice message
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'voice'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=None,
            message_type='voice',
            file_id=file_id,
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward voice message to partner
        partner_message = await self.context.bot.send_voice(
            chat_id=self.partner_id,
            voice=file_id
        )
        
        return partner_message
    
    async def handle_sticker(self, sticker) -> Optional[Message]:
        """
        Handle sticker message.
        
        Args:
            sticker: Sticker object
            
        Returns:
            Sent message to partner
        """
        file_id = sticker.file_id
        
        # Download and store the sticker
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'sticker'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=None,
            message_type='sticker',
            file_id=file_id,
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward sticker to partner
        partner_message = await self.context.bot.send_sticker(
            chat_id=self.partner_id,
            sticker=file_id
        )
        
        return partner_message
    
    async def handle_video_note(self, video_note) -> Optional[Message]:
        """
        Handle video note message.
        
        Args:
            video_note: VideoNote object
            
        Returns:
            Sent message to partner
        """
        file_id = video_note.file_id
        
        # Download and store the video note
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'video_note'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=None,
            message_type='video_note',
            file_id=file_id, 
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward video note to partner
        partner_message = await self.context.bot.send_video_note(
            chat_id=self.partner_id,
            video_note=file_id
        )
        
        return partner_message
        
    async def handle_animation(self, animation, caption: Optional[str] = None) -> Optional[Message]:
        """
        Handle animation (GIF) message.
        
        Args:
            animation: Animation object
            caption: Optional caption text
            
        Returns:
            Sent message to partner
        """
        file_id = animation.file_id
        
        # Download and store the animation
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'animation'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=caption,
            message_type='animation',
            file_id=file_id,
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward animation to partner
        partner_message = await self.context.bot.send_animation(
            chat_id=self.partner_id,
            animation=file_id,
            caption=caption
        )
        
        return partner_message
        
    async def handle_audio(self, audio, caption: Optional[str] = None) -> Optional[Message]:
        """
        Handle audio message.
        
        Args:
            audio: Audio object
            caption: Optional caption text
            
        Returns:
            Sent message to partner
        """
        file_id = audio.file_id
        
        # Download and store the audio
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'audio'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=caption,
            message_type='audio',
            file_id=file_id,
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward audio to partner
        partner_message = await self.context.bot.send_audio(
            chat_id=self.partner_id,
            audio=file_id,
            caption=caption
        )
        
        return partner_message
        
    async def handle_document(self, document, caption: Optional[str] = None) -> Optional[Message]:
        """
        Handle document message.
        
        Args:
            document: Document object
            caption: Optional caption text
            
        Returns:
            Sent message to partner
        """
        file_id = document.file_id
        
        # Download and store the document
        local_path, unique_id, file_content, mime_type = await download_media_file(
            self.context, file_id, 'document'
        )
        
        # Store message in database
        message_id = await db.add_message(
            chat_id=self.chat_id,
            sender_id=self.user_id,
            content=caption,
            message_type='document',
            file_id=file_id,
            local_file_path=local_path,
            file_name=unique_id,
            mime_type=mime_type,
            file_content=file_content
        )
        
        # Forward document to partner
        partner_message = await self.context.bot.send_document(
            chat_id=self.partner_id,
            document=file_id,
            caption=caption
        )
        
        return partner_message
        
    async def handle_unsupported(self) -> None:
        """
        Handle unsupported message type.
        
        Returns:
            None
        """
        await self.context.bot.send_message(
            chat_id=self.user_id,
            text="⚠️ Этот тип сообщений не поддерживается."
        )
        return None

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages."""
    
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    try:
        # Check if this user is in an active chat
        if user_id not in state.active_chats:
            # User is not in active chat, show message
            reply_markup = InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            await update_main_message(
                user_id,
                context,
                "Вы не находитесь в активном чате\\. Нажмите кнопку *Начать поиск* для поиска собеседника\\.",
                reply_markup
            )
            return
        
        # Get the partner ID from active chats
        partner_id = state.active_chats[user_id]
        
        # Get active chat ID from database
        chat_result = await db.get_active_chat(user_id)
        if not chat_result:
            # Chat not found in database, clean up memory and show message
            if user_id in state.active_chats:
                del state.active_chats[user_id]
            if partner_id in state.active_chats:
                del state.active_chats[partner_id]
            
            reply_markup = InlineKeyboardMarkup(MAIN_MENU_KEYBOARD)
            await update_main_message(
                user_id,
                context,
                "Ваш чат был завершен\\. Нажмите кнопку *Начать поиск* для поиска нового собеседника\\.",
                reply_markup
            )
            return
        
        chat_id, _ = chat_result
        
        # Store message in user_messages for possible later deletion
        if update.message:
            if user_id not in state.user_messages:
                state.user_messages[user_id] = []
            state.user_messages[user_id].append(update.message.message_id)
        
        # Create media handler
        media_handler = MediaHandler(context, chat_id, user_id, partner_id)
        
        # Process different message types
        partner_message = None
        message = update.message
        
        if message.text:
            partner_message = await media_handler.handle_text(message.text)
        elif message.photo:
            partner_message = await media_handler.handle_photo(message.photo, message.caption)
        elif message.video:
            partner_message = await media_handler.handle_video(message.video, message.caption)
        elif message.voice:
            partner_message = await media_handler.handle_voice(message.voice)
        elif message.sticker:
            partner_message = await media_handler.handle_sticker(message.sticker)
        elif message.video_note:
            partner_message = await media_handler.handle_video_note(message.video_note)
        elif message.animation:
            partner_message = await media_handler.handle_animation(message.animation, message.caption)
        elif message.audio:
            partner_message = await media_handler.handle_audio(message.audio, message.caption)
        elif message.document:
            partner_message = await media_handler.handle_document(message.document, message.caption)
        else:
            await media_handler.handle_unsupported()
        
        # Store partner's message for possible later deletion
        if partner_message:
            if partner_id not in state.user_messages:
                state.user_messages[partner_id] = []
            state.user_messages[partner_id].append(partner_message.message_id)
        
    except Exception as e:
        # В случае ошибки тоже снимаем флаг
        if user_id in state.chat_initialization:
            state.chat_initialization[user_id] = False
        logger.error(f"Error handling message from {user_id}: {e}")
        
        # Используем update_main_message для показа ошибки вместо отправки нового сообщения
        keyboard = CHAT_CONTROL_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            user_id,
            context,
            "Произошла ошибка при отправке сообщения. Попробуйте еще раз или используйте кнопки ниже для управления чатом.",
            reply_markup
        )

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /stop command."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # Check if user is in chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return

    chat_id, partner_id = active_chat
    
    # Remove both users from chat
    await db.remove_chat(chat_id)
    
    # Send messages to both users
    keyboard = MAIN_MENU_KEYBOARD
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=user_id,
        text="Чат завершен. Нажмите кнопку ниже, чтобы начать новый поиск.",
        reply_markup=reply_markup
    )
    
    await context.bot.send_message(
        chat_id=partner_id,
        text="Собеседник завершил чат. Нажмите кнопку ниже, чтобы начать новый поиск.",
        reply_markup=reply_markup
    )

async def pin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pin a message that was replied to."""
    if not update.message or not update.effective_user or not update.message.reply_to_message:
        return

    user_id = update.effective_user.id
    
    # Check if user is in chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        # Используем update_main_message вместо reply_text
        keyboard = MAIN_MENU_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            user_id,
            context,
            "Вы не находитесь в активном чате. Нажмите кнопку ниже, чтобы начать поиск.",
            reply_markup
        )
        
        # Удаляем команду
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=update.message.message_id)
        except Exception as e:
            logger.error(f"Error deleting pin command: {e}")
            
        return

    chat_id, partner_id = active_chat
    
    try:
        # Store the original command message to delete it later
        command_message = update.message
        command_message_id = command_message.message_id
        
        # Pin the message with notifications disabled
        message_to_pin = update.message.reply_to_message
        await message_to_pin.pin(disable_notification=True)
        
        # Wait for pin notification to appear and delete it
        await asyncio.sleep(1)
        
        # Try to delete pin notification several times
        for attempt in range(5):
            try:
                # Delete the original command
                await context.bot.delete_message(chat_id=user_id, message_id=command_message_id)
            except Exception as e:
                logger.error(f"Error deleting command message: {e}")
            
            # Try to delete pin notification
            await delete_pin_message(user_id, context)
            
            await asyncio.sleep(0.5)
        
        # Get current keyboard
        keyboard = CHAT_CONTROL_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Update partner's main message with notification
        await update_main_message(
            partner_id,
            context,
            "Собеседник закрепил сообщение!\nВы в чате с собеседником.",
            reply_markup
        )
        
    except Exception as e:
        logger.error(f"Error pinning message: {e}")
        
        # Используем update_main_message вместо reply_text
        keyboard = CHAT_CONTROL_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            user_id,
            context,
            "Не удалось закрепить сообщение. Вы в чате с собеседником.",
            reply_markup
        )
        
        # Удаляем команду
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=update.message.message_id)
        except Exception as e:
            logger.error(f"Error deleting pin command after error: {e}")

async def unpin_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unpin the current pinned message."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # Check if user is in chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return

    chat_id, partner_id = active_chat
    
    try:
        # Get chat and unpin message
        chat = await context.bot.get_chat(user_id)
        if chat.pinned_message:
            await chat.unpin_message()
            
            # Delete pin notification messages
            await delete_pin_message(user_id, context)
            await delete_pin_message(partner_id, context)
            
            # Send notifications
            await context.bot.send_message(
                chat_id=user_id,
                text="Сообщение откреплено!"
            )
            await context.bot.send_message(
                chat_id=partner_id,
                text="Собеседник открепил сообщение!"
            )
        else:
            await update.message.reply_text("Нет закрепленных сообщений.")
            
    except Exception as e:
        logger.error(f"Error unpinning message: {e}")
        await update.message.reply_text("Не удалось открепить сообщение.")

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
    if not update.message:
        return
    
    # Убедимся, что это сообщение от Telegram, а не от пользователя
    is_system_message = update.message.from_user and update.message.from_user.id == 777000
    is_pinned_update = update.message.pinned_message is not None
    
    # Check if this is a pin notification (multiple variants)
    pin_messages = [
        "Закреплено сообщение",
        "Pinned message",
        "Сообщение закреплено",
        "Message pinned",
        "pinned",
        "закреплено"
    ]
    
    # Проверка на системное сообщение о закреплении
    is_pin_message = update.message.text and any(text.lower() in update.message.text.lower() for text in pin_messages)
    
    # Если это обновление о закреплении или похоже на уведомление о закреплении
    if is_pinned_update or is_system_message or is_pin_message:
        try:
            logger.info(f"Found pin notification: {update.message.text or 'No text'}, message_id: {update.message.message_id}")
            
            # Delete the pin notification immediately
            await update.message.delete()
            logger.info(f"Successfully deleted pin notification message {update.message.message_id}")
            
            # Store this message ID in case we need to delete it later
            if update.effective_chat:
                user_id = update.effective_chat.id
                await db.update_pin_message_id(user_id, update.message.message_id)
        except Exception as e:
            logger.error(f"Error deleting pin notification: {e}")
    
    # Если это не уведомление о закреплении, но похоже на сервисное сообщение, проверим еще раз
    elif update.message.text and len(update.message.text) < 100 and not update.message.reply_to_message:
        # Проверим, не похоже ли это на автоматическое сообщение
        if any(phrase in update.message.text.lower() for phrase in ["bot", "telegram", "message", "сообщение"]):
            try:
                # Проверим, не слишком ли активно сообщения пользователя
                if update.message.from_user and update.message.from_user.id != 777000:
                    active_chat = await db.get_active_chat(update.message.from_user.id)
                    if active_chat:
                        # Если это обычный чат, не будем удалять сообщение
                        return
                
                logger.info(f"Found potential service message: {update.message.text}")
                await update.message.delete()
                logger.info(f"Deleted potential service message {update.message.message_id}")
            except Exception as e:
                logger.error(f"Error handling potential service message: {e}")

async def media_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show statistics about saved media files."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # Check if user is in chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return

    chat_id, partner_id = active_chat
    
    try:
        # Получаем все медиа-сообщения в чате
        media_messages = await db.get_chat_media(chat_id)
        
        if not media_messages:
            await update.message.reply_text("В текущем чате нет медиафайлов.")
            return
        
        # Счетчики статистики
        media_stats = {}  # Статистика по типам сообщений
        db_files_count = 0  # Количество файлов в базе данных
        local_files_count = 0  # Количество файлов на диске
        
        # Проверяем наличие и размеры медиафайлов
        total_db_size = 0  # Размер файлов в базе данных в байтах
        total_local_size = 0  # Размер файлов на диске в байтах
        
        # Список запросов для получения размеров файлов из базы данных
        msg_ids = [msg['id'] for msg in media_messages]
        
        # Для каждого сообщения получаем информацию о наличии контента в базе данных
        for msg in media_messages:
            msg_id = msg['id']
            msg_type = msg['message_type']
            
            # Статистика по типам
            if msg_type not in media_stats:
                media_stats[msg_type] = 0
            media_stats[msg_type] += 1
            
            # Проверяем наличие в базе данных
            media_content = await db.get_media_content(msg_id)
            if media_content and media_content[0]:  # Если есть бинарные данные
                db_files_count += 1
                total_db_size += len(media_content[0])
            
            # Проверяем наличие на диске
            if msg['local_file_path'] and os.path.exists(msg['local_file_path']):
                local_files_count += 1
                total_local_size += os.path.getsize(msg['local_file_path'])
        
        # Формируем ответ
        stats_text = "📊 Статистика медиафайлов в чате:\n\n"
        
        # Статистика по типам медиа
        for media_type, count in media_stats.items():
            emoji = {
                'photo': '🖼️',
                'video': '🎬',
                'voice': '🎤',
                'sticker': '🎭',
                'video_note': '🎥'
            }.get(media_type, '📎')
            
            stats_text += f"{emoji} {media_type}: {count}\n"
        
        total_media = len(media_messages)
        stats_text += f"\n📁 Всего медиафайлов: {total_media}"
        
        # Информация о хранении в базе данных
        if db_files_count > 0:
            stats_text += f"\n💾 Сохранено в базе данных: {db_files_count} ({int(db_files_count/total_media*100)}%)"
            
            # Размер в базе данных
            if total_db_size > 0:
                if total_db_size < 1024:
                    db_size_str = f"{total_db_size} B"
                elif total_db_size < 1024 * 1024:
                    db_size_str = f"{total_db_size / 1024:.1f} KB"
                else:
                    db_size_str = f"{total_db_size / (1024 * 1024):.1f} MB"
                    
                stats_text += f"\n📊 Размер в базе данных: {db_size_str}"
        
        # Информация о локальном хранении
        if local_files_count > 0:
            stats_text += f"\n📂 Сохранено локально: {local_files_count} ({int(local_files_count/total_media*100)}%)"
            
            # Размер на диске
            if total_local_size > 0:
                if total_local_size < 1024:
                    local_size_str = f"{total_local_size} B"
                elif total_local_size < 1024 * 1024:
                    local_size_str = f"{total_local_size / 1024:.1f} KB"
                else:
                    local_size_str = f"{total_local_size / (1024 * 1024):.1f} MB"
                    
                stats_text += f"\n📊 Размер локальных файлов: {local_size_str}"
        
        # Информация о настройках хранения
        stats_text += f"\n\n⚙️ Режим хранения медиафайлов: {'В базе данных' if state.store_media_in_db else 'На диске'}"
        
        await update.message.reply_text(stats_text)
        
    except Exception as e:
        logger.error(f"Error getting media stats: {e}")
        await update.message.reply_text("Произошла ошибка при получении статистики медиафайлов.")

async def resend_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resend the last media file from database or local storage."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    
    # Check if user is in chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return

    chat_id, partner_id = active_chat
    
    try:
        # Получаем последнее медиа-сообщение
        media_messages = await db.get_chat_media(chat_id)
        
        if not media_messages:
            await update.message.reply_text("В текущем чате нет медиафайлов.")
            return
        
        # Берем первое (самое новое) медиа-сообщение
        media_message = media_messages[0]
        message_id = media_message['id']
        message_type = media_message['message_type']
        
        # Получаем информацию о медиа
        media_info = await db.get_message_media(message_id)
        
        # Проверяем, есть ли файл в базе данных
        media_content = await db.get_media_content(message_id)
        
        if media_content:
            file_content, file_name, mime_type = media_content
            if file_content:
                await update.message.reply_text(f"Повторная отправка {message_type} из базы данных...")
                
                # Создаем временный файл для отправки
                temp_file_path = os.path.join(MEDIA_DIR, "temp", file_name or f"temp_{uuid.uuid4()}")
                os.makedirs(os.path.dirname(temp_file_path), exist_ok=True)
                
                # Записываем содержимое файла
                async with aiofiles.open(temp_file_path, 'wb') as f:
                    await f.write(file_content)
                
                # Отправляем файл
                with open(temp_file_path, 'rb') as file:
                    if message_type == "photo":
                        await context.bot.send_photo(chat_id=user_id, photo=file)
                    elif message_type == "video":
                        await context.bot.send_video(chat_id=user_id, video=file)
                    elif message_type == "voice":
                        await context.bot.send_voice(chat_id=user_id, voice=file)
                    elif message_type == "sticker":
                        # Для стикеров лучше использовать file_id
                        if media_info['file_id']:
                            await context.bot.send_sticker(chat_id=user_id, sticker=media_info['file_id'])
                        else:
                            await context.bot.send_document(chat_id=user_id, document=file)
                    elif message_type == "video_note":
                        await context.bot.send_video_note(chat_id=user_id, video_note=file)
                
                # Удаляем временный файл
                try:
                    os.remove(temp_file_path)
                except Exception as e:
                    logger.error(f"Error removing temp file: {e}")
                
                await update.message.reply_text(f"Медиафайл успешно отправлен из базы данных.")
                return
        
        # Если файла в базе нет или не удалось его использовать, пробуем локальное хранилище
        if media_info['local_file_path'] and os.path.exists(media_info['local_file_path']):
            file_path = media_info['local_file_path']
            await update.message.reply_text(f"Повторная отправка {message_type} из локального хранилища...")
            
            with open(file_path, 'rb') as file:
                if message_type == "photo":
                    await context.bot.send_photo(chat_id=user_id, photo=file)
                elif message_type == "video":
                    await context.bot.send_video(chat_id=user_id, video=file)
                elif message_type == "voice":
                    await context.bot.send_voice(chat_id=user_id, voice=file)
                elif message_type == "sticker":
                    # Для стикеров лучше использовать file_id
                    await context.bot.send_sticker(chat_id=user_id, sticker=media_info['file_id'])
                elif message_type == "video_note":
                    await context.bot.send_video_note(chat_id=user_id, video_note=file)
            
            await update.message.reply_text(f"Медиафайл успешно отправлен из локального хранилища.\nПуть: {file_path}")
            return
        
        # Если нет ни в базе, ни локально, используем file_id из Telegram
        if media_info['file_id']:
            await update.message.reply_text(f"Повторная отправка {message_type} через Telegram API...")
            
            file_id = media_info['file_id']
            if message_type == "photo":
                await context.bot.send_photo(chat_id=user_id, photo=file_id)
            elif message_type == "video":
                await context.bot.send_video(chat_id=user_id, video=file_id)
            elif message_type == "voice":
                await context.bot.send_voice(chat_id=user_id, voice=file_id)
            elif message_type == "sticker":
                await context.bot.send_sticker(chat_id=user_id, sticker=file_id)
            elif message_type == "video_note":
                await context.bot.send_video_note(chat_id=user_id, video_note=file_id)
            
            await update.message.reply_text("Медиафайл успешно отправлен через Telegram API.")
            return
        
        # Если не получилось отправить медиа
        await update.message.reply_text("Не удалось найти или отправить медиафайл.")
        
    except Exception as e:
        logger.error(f"Error resending media: {e}")
        await update.message.reply_text("Произошла ошибка при отправке медиафайла.")

async def toggle_storage_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle between database and local storage mode for media files."""
    if not update.message or not update.effective_user:
        return
    
    # Проверяем, является ли пользователь администратором (можно настроить список админов)
    user_id = update.effective_user.id
    # Список администраторов, которые могут менять режим хранения
    admins = [user_id]  # В данном случае любой пользователь может переключать режим для своего бота
    
    if user_id not in admins:
        await update.message.reply_text("У вас нет прав для изменения режима хранения медиафайлов.")
        return
    
    # Toggle the storage mode
    state.store_media_in_db = not state.store_media_in_db
    
    mode_text = "базе данных" if state.store_media_in_db else "локальном хранилище"
    await update.message.reply_text(
        f"✅ Режим хранения медиафайлов изменен!\n\n"
        f"📦 Новый медиаконтент теперь будет сохраняться в {mode_text}.\n\n"
        f"📝 Примечание: это не повлияет на уже сохраненные файлы."
    )

async def import_media_to_db(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Import existing local media files into the database."""
    if not update.message or not update.effective_user:
        return
    
    # Проверяем, является ли пользователь администратором
    user_id = update.effective_user.id
    # Список администраторов
    admins = [user_id]  # В данном случае любой пользователь может импортировать свои медиа
    
    if user_id not in admins:
        await update.message.reply_text("У вас нет прав для импорта медиафайлов в базу данных.")
        return
    
    # Проверяем, находится ли пользователь в чате
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return

    chat_id, partner_id = active_chat
    
    # Отправляем начальное сообщение
    status_message = await update.message.reply_text("📥 Начинаю импорт медиафайлов в базу данных...")
    
    try:
        # Получаем все медиа-сообщения в чате
        media_messages = await db.get_chat_media(chat_id)
        
        if not media_messages:
            await status_message.edit_text("В текущем чате нет медиафайлов для импорта.")
            return
        
        # Счетчики
        total_files = len(media_messages)
        imported_files = 0
        skipped_files = 0
        already_in_db = 0
        failed_files = 0
        
        # Обновляем статус
        await status_message.edit_text(f"🔍 Найдено медиафайлов: {total_files}\n⏳ Импортирую...")
        
        # Для каждого сообщения проверяем наличие локального файла и импортируем в базу данных
        for msg in media_messages:
            msg_id = msg['id']
            msg_type = msg['message_type']
            local_path = msg['local_file_path']
            
            # Проверяем, есть ли уже содержимое в базе данных
            media_content = await db.get_media_content(msg_id)
            if media_content and media_content[0]:
                already_in_db += 1
                continue
            
            # Если есть локальный файл, импортируем его в базу данных
            if local_path and os.path.exists(local_path):
                try:
                    # Определяем MIME-тип на основе типа сообщения
                    mime_types = {
                        "photo": "image/jpeg",
                        "video": "video/mp4",
                        "voice": "audio/ogg",
                        "sticker": "image/webp",
                        "video_note": "video/mp4"
                    }
                    mime_type = mime_types.get(msg_type, "application/octet-stream")
                    
                    # Получаем имя файла
                    file_name = os.path.basename(local_path)
                    
                    # Читаем содержимое файла
                    async with aiofiles.open(local_path, 'rb') as f:
                        file_content = await f.read()
                    
                    # Сохраняем в базу данных
                    await db.save_media_to_db(msg_id, file_content, file_name, mime_type)
                    imported_files += 1
                    
                    # Периодически обновляем статус
                    if imported_files % 5 == 0 or imported_files == 1:
                        await status_message.edit_text(
                            f"⏳ Импортирую медиафайлы в базу данных...\n"
                            f"✅ Импортировано: {imported_files}/{total_files}\n"
                            f"⏭️ Пропущено (уже в БД): {already_in_db}\n"
                            f"❌ Ошибок: {failed_files}"
                        )
                except Exception as e:
                    logger.error(f"Error importing media file to DB: {e}")
                    failed_files += 1
            else:
                skipped_files += 1
        
        # Финальный отчет
        result_text = (
            f"✅ Импорт медиафайлов завершен!\n\n"
            f"📊 Результаты:\n"
            f"- Всего найдено: {total_files}\n"
            f"- Успешно импортировано: {imported_files}\n"
            f"- Уже были в базе данных: {already_in_db}\n"
            f"- Пропущено (нет локальных файлов): {skipped_files}\n"
            f"- Ошибок импорта: {failed_files}"
        )
        
        await status_message.edit_text(result_text)
        
    except Exception as e:
        logger.error(f"Error during media import to DB: {e}")
        await status_message.edit_text(f"❌ Произошла ошибка при импорте медиафайлов: {str(e)}")

async def init_db(application: Application) -> None:
    """Initialize the database connection and sync data with the bot state."""
    try:
        # Get database URL from environment variables
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            logger.error("Database URL not found in environment variables")
            return
        
        # Connect to the database
        await db.connect(database_url)
        
        # Synchronize with database state
        try:
            # Get searching users from database
            searching_users = await db.get_searching_users()
            logger.info(f"Found {len(searching_users)} users searching for chat in database")
            
            # Update local state with searching users
            for user_id in searching_users:
                state.users_searching.add(user_id)
                logger.info(f"User {user_id} added to local searching state")
                
            # Synchronize active chats
            active_chats = await db.get_all_active_chats()
            for user_id, partner_id in active_chats:
                state.active_chats[user_id] = partner_id
                state.active_chats[partner_id] = user_id
                logger.info(f"Active chat between {user_id} and {partner_id} added to local state")
                
            logger.info("Database synchronization completed successfully")
        except Exception as e:
            logger.error(f"Error synchronizing with database: {e}")
        
        # Reset animation_shown state at bot start
        global animation_shown
        animation_shown = {}
        logger.info("Animation shown state reset")
        
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise

async def cleanup_db(application: Application) -> None:
    """Cleanup database connections."""
    try:
        await db.disconnect()
        logger.info("Database connection closed successfully")
    except Exception as e:
        logger.error(f"Error closing database connection: {e}")

async def reset_animation_shown(user_id: int) -> None:
    """Reset the animation shown flag for a user."""
    state.animation_shown[user_id] = False

# Profile setup handlers
async def setup_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start profile setup process."""
    await UserProfileManager.setup_profile(update, context)

async def skip_profile_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skip profile setup and show main menu."""
    query = update.callback_query
    if not query:
        return
    
    await query.answer()
    user_id = query.from_user.id

    # Mark profile setup as skipped
    await db.update_profile_setup_state(user_id, PROFILE_SETUP_NONE, 0)

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

async def set_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle gender selection in profile setup."""
    await UserProfileManager.handle_gender_selection(update, context)

async def set_looking_for(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle looking for selection in profile setup."""
    await UserProfileManager.handle_looking_for_selection(update, context)

async def handle_age_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle age selection in profile setup."""
    await UserProfileManager.handle_age_selection(update, context)

async def toggle_interest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle selection of an interest."""
    await UserProfileManager.toggle_interest(update, context)

async def complete_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Complete profile setup."""
    await UserProfileManager.complete_profile(update, context)

async def view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View user profile."""
    await UserProfileManager.view_profile(update, context)

async def edit_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit user profile."""
    await UserProfileManager.edit_profile(update, context)

async def edit_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit gender in profile."""
    await UserProfileManager.edit_gender(update, context)

async def edit_looking_for(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit looking for preference in profile."""
    await UserProfileManager.edit_looking_for(update, context)

async def edit_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit age in profile."""
    await UserProfileManager.edit_age(update, context)

async def save_age_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save age edit in profile."""
    await UserProfileManager.save_age_edit(update, context)

async def edit_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit interests in profile."""
    await UserProfileManager.edit_interests(update, context)

async def save_gender_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save gender edit in profile."""
    await UserProfileManager.save_gender_edit(update, context)

async def save_looking_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Save looking for preference edit in profile."""
    await UserProfileManager.save_looking_for_edit(update, context)

class UserProfileManager:
    """
    Class to manage user profiles, including setup, editing, and viewing.
    """
    
    @staticmethod
    async def get_user_profile_text(user_id: int) -> str:
        """
        Get a formatted text representation of a user's profile.
        
        Args:
            user_id: The Telegram user ID
            
        Returns:
            Formatted profile text for display
        """
        profile = await db.get_user_profile(user_id)
        interests = await db.get_user_interests(user_id)
        
        if not profile:
            return "Профиль не настроен\\."
        
        profile_text = "*👤 Ваш профиль*\n\n"
        
        # Gender
        if profile.get('gender'):
            gender_text = {
                'male': "👨 Мужской",
                'female': "👩 Женский",
                'other': "🧑 Другой"
            }.get(profile['gender'], "Не указан")
            profile_text += f"• *Пол:* {gender_text}\n"
        else:
            profile_text += f"• *Пол:* Не указан\n"
        
        # Looking for
        if profile.get('looking_for'):
            looking_for_text = {
                'male': "👨 Мужской",
                'female': "👩 Женский",
                'any': "👥 Любой"
            }.get(profile['looking_for'], "Не указано")
            profile_text += f"• *Ищу:* {looking_for_text}\n"
        else:
            profile_text += f"• *Ищу:* Не указано\n"
        
        # Age
        if profile.get('age'):
            profile_text += f"• *Возраст:* {profile['age']}\n"
        else:
            profile_text += f"• *Возраст:* Не указан\n"
        
        # Interests
        if interests:
            interests_text = "\n".join([f"  • {interest}" for interest in interests])
            profile_text += f"• *Интересы:*\n{interests_text}"
        else:
            profile_text += f"• *Интересы:* Не указаны"
        
        return profile_text
    
    @staticmethod
    async def send_gender_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query = None) -> None:
        """
        Send gender selection menu to user.
        
        Args:
            user_id: Telegram user ID
            context: Telegram context
            is_edit: Whether this is during profile edit (True) or setup (False)
            query: Optional callback query for editing message
        """
        buttons = [
            [
                InlineKeyboardButton("👨 Мужской", callback_data=f"{'gender_edit' if is_edit else 'gender'}_male"),
                InlineKeyboardButton("👩 Женский", callback_data=f"{'gender_edit' if is_edit else 'gender'}_female")
            ],
            [InlineKeyboardButton("🧑 Другой", callback_data=f"{'gender_edit' if is_edit else 'gender'}_other")]
        ]
        
        if is_edit:
            buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="edit_profile")])
        
        keyboard = InlineKeyboardMarkup(buttons)
        
        text = "*Выберите ваш пол:*\n\n" \
               "Это поможет в поиске подходящего собеседника\\."

        if query:
            try:
                await query.edit_message_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="MarkdownV2"
                )
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    await query.edit_message_caption(
                        caption=text,
                        reply_markup=keyboard,
                        parse_mode="MarkdownV2"
                    )
                else:
                    raise
        else:
            await update_main_message(
                user_id,
                context,
                text,
                keyboard
            )
    
    @staticmethod
    async def send_looking_for_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query = None) -> None:
        """
        Send looking for preference selection menu to user.
        
        Args:
            user_id: Telegram user ID
            context: Telegram context
            is_edit: Whether this is during profile edit (True) or setup (False)
            query: Optional callback query for editing message
        """
        buttons = [
            [
                InlineKeyboardButton("👨 Мужской", callback_data=f"{'looking_for_edit' if is_edit else 'looking_for'}_male"),
                InlineKeyboardButton("👩 Женский", callback_data=f"{'looking_for_edit' if is_edit else 'looking_for'}_female")
            ],
            [InlineKeyboardButton("👥 Любой", callback_data=f"{'looking_for_edit' if is_edit else 'looking_for'}_any")]
        ]
        
        if is_edit:
            buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="edit_profile")])
        
        keyboard = InlineKeyboardMarkup(buttons)
        
        text = "*Кого вы ищете для общения?*\n\n" \
               "Выберите предпочитаемый пол собеседника\\."

        if query:
            try:
                await query.edit_message_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="MarkdownV2"
                )
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    await query.edit_message_caption(
                        caption=text,
                        reply_markup=keyboard,
                        parse_mode="MarkdownV2"
                    )
                else:
                    raise
        else:
            await update_main_message(
                user_id,
                context,
                text,
                keyboard
            )
    
    @staticmethod
    async def send_age_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query = None) -> None:
        """
        Send age selection menu to user.
        
        Args:
            user_id: Telegram user ID
            context: Telegram context
            is_edit: Whether this is during profile edit (True) or setup (False)
            query: Optional callback query for editing message
        """
        # Create age buttons in rows of 5 buttons each
        buttons = []
        current_row = []
        
        for age in range(18, 51):
            current_row.append(
                InlineKeyboardButton(str(age), callback_data=f"{'age_edit' if is_edit else 'age'}_{age}")
            )
            
            if len(current_row) == 5:
                buttons.append(current_row)
                current_row = []
        
        # Add any remaining buttons
        if current_row:
            buttons.append(current_row)
        
        # Add 50+ option
        buttons.append([InlineKeyboardButton("50+", callback_data=f"{'age_edit' if is_edit else 'age'}_50plus")])
        
        if is_edit:
            buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="edit_profile")])
        
        keyboard = InlineKeyboardMarkup(buttons)
        
        text = "*Укажите ваш возраст:*\n\n" \
               "Это поможет найти собеседника вашей возрастной группы\\."

        if query:
            try:
                await query.edit_message_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="MarkdownV2"
                )
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    await query.edit_message_caption(
                        caption=text,
                        reply_markup=keyboard,
                        parse_mode="MarkdownV2"
                    )
                else:
                    raise
        else:
            await update_main_message(
                user_id,
                context,
                text,
                keyboard
            )
    
    @staticmethod
    async def send_interests_selection(user_id: int, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False, query = None) -> None:
        """
        Send interests selection menu to user.
        
        Args:
            user_id: Telegram user ID
            context: Telegram context
            is_edit: Whether this is during profile edit (True) or setup (False)
            query: Optional callback query for editing message
        """
        # Get available interests from database
        all_interests = await db.get_all_interests()
        user_interests = await db.get_user_interests(user_id)
        
        # Create buttons for interests
        buttons = []
        
        for interest in all_interests:
            interest_name = interest['name']
            is_selected = interest_name in user_interests
            
            # Create button text with checkmark if selected
            button_text = f"{'✅' if is_selected else '❌'} {interest_name}"
            
            # Create button with callback data for toggling
            buttons.append([InlineKeyboardButton(
                button_text, 
                callback_data=f"toggle_interest_{interest_name}"
            )])
        
        # Add buttons for completing or going back
        if is_edit:
            buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="edit_profile")])
        else:
            buttons.append([InlineKeyboardButton("✅ Готово", callback_data="complete_profile")])
        
        keyboard = InlineKeyboardMarkup(buttons)
        
        text = "*Выберите ваши интересы:*\n\n" \
               "Нажмите на интересы, чтобы выбрать их\\. Нажмите повторно, чтобы отменить выбор\\.\n\n" \
               f"Выбрано: {len(user_interests) if user_interests else 0}"

        if query:
            try:
                await query.edit_message_text(
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="MarkdownV2"
                )
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    await query.edit_message_caption(
                        caption=text,
                        reply_markup=keyboard,
                        parse_mode="MarkdownV2"
                    )
                else:
                    raise
        else:
            await update_main_message(
                user_id,
                context,
                text,
                keyboard
            )
    
    @staticmethod
    async def setup_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Start the profile setup process.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return

        await query.answer()
        user_id = query.from_user.id
        
        # Set user as being in gender selection state
        await db.update_profile_setup_state(user_id, PROFILE_SETUP_GENDER, 1)
        
        # Show gender selection menu
        await UserProfileManager.send_gender_selection(user_id, context)
    
    @staticmethod
    async def handle_gender_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle gender selection in profile setup.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
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
        
        # Show looking for selection menu
        await UserProfileManager.send_looking_for_selection(user_id, context)
    
    @staticmethod
    async def handle_looking_for_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle looking for selection in profile setup.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return
    
        await query.answer()
        user_id = query.from_user.id
        
        # Extract looking for preference from callback data
        looking_for = query.data.split('_')[1]
        
        # Save looking for preference in database
        await db.save_user_profile(user_id, looking_for=looking_for)
        
        # Update profile setup state to age selection
        await db.update_profile_setup_state(user_id, PROFILE_SETUP_AGE, 3)
        
        # Show age selection menu
        await UserProfileManager.send_age_selection(user_id, context)
    
    @staticmethod
    async def handle_age_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle age selection in profile setup.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
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
        
        # Show interests selection menu
        await UserProfileManager.send_interests_selection(user_id, context)
    
    @staticmethod
    async def toggle_interest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Toggle selection of an interest.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
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
        is_edit = profile_state != PROFILE_SETUP_INTERESTS
        
        # Refresh interests menu
        await UserProfileManager.send_interests_selection(user_id, context, is_edit, query=query)
    
    @staticmethod
    async def complete_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Complete the profile setup process.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return
        
        await query.answer()
        user_id = query.from_user.id
        
        # Mark profile setup as complete
        await db.update_profile_setup_state(user_id, PROFILE_SETUP_COMPLETE, 5)
        
        # Отправляем главное меню с гифкой
        await send_main_menu_with_animation(user_id, context)
    
    @staticmethod
    async def view_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        View user profile.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
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
    
        # Show profile
        if query:
            try:
                await query.edit_message_text(
                    text=profile_text,
                    reply_markup=reply_markup,
                    parse_mode="MarkdownV2"
                )
            except telegram.error.BadRequest as e:
                if "There is no text in the message to edit" in str(e):
                    # Если сообщение содержит анимацию, редактируем caption
                    await query.edit_message_caption(
                        caption=profile_text,
                        reply_markup=reply_markup,
                        parse_mode="MarkdownV2"
                    )
                else:
                    raise
        else:
            await update_main_message(
                user_id,
                context,
                profile_text,
                reply_markup
            )
    
    @staticmethod
    async def edit_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Show profile edit menu.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
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
    
        # Show edit options
        try:
            await query.edit_message_text(
                text="*Редактирование профиля*\n\n"
                     "Выберите, что вы хотите изменить в вашем профиле:",
                reply_markup=reply_markup,
                parse_mode="MarkdownV2"
            )
        except telegram.error.BadRequest as e:
            if "There is no text in the message to edit" in str(e):
                # Если сообщение содержит анимацию, редактируем caption
                await query.edit_message_caption(
                    caption="*Редактирование профиля*\n\n"
                           "Выберите, что вы хотите изменить в вашем профиле:",
                    reply_markup=reply_markup,
                    parse_mode="MarkdownV2"
                )
            else:
                raise
    
    @staticmethod
    async def edit_gender(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Edit gender in profile.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return

        await query.answer()
        user_id = query.from_user.id
        
        # Show gender selection menu in edit mode
        await UserProfileManager.send_gender_selection(user_id, context, is_edit=True, query=query)
    
    @staticmethod
    async def edit_looking_for(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Edit looking for preference in profile.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return
        
        await query.answer()
        user_id = query.from_user.id
        
        # Show looking for selection menu in edit mode
        await UserProfileManager.send_looking_for_selection(user_id, context, is_edit=True, query=query)
    
    @staticmethod
    async def edit_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Edit age in profile.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return

        await query.answer()
        user_id = query.from_user.id
        
        # Show age selection menu in edit mode
        await UserProfileManager.send_age_selection(user_id, context, is_edit=True, query=query)
    
    @staticmethod
    async def edit_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Edit interests in profile.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return
    
        await query.answer()
        user_id = query.from_user.id

        # Show interests selection menu in edit mode
        await UserProfileManager.send_interests_selection(user_id, context, is_edit=True, query=query)
    
    @staticmethod
    async def save_gender_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Save gender edit in profile.

        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return
        
        await query.answer()
        user_id = query.from_user.id
    
    # Extract gender from callback data
        gender = query.data.split('_')[2]
    
        # Save gender in database
        await db.save_user_profile(user_id, gender=gender)
    
        # Return to profile edit menu
        await UserProfileManager.edit_profile(update, context)
    
    @staticmethod
    async def save_looking_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Save looking for preference edit in profile.

        Args:
            update: Telegram update
            context: Telegram context
        """
        query = update.callback_query
        if not query or not query.from_user:
            return
    
        await query.answer()
        user_id = query.from_user.id
    
        # Extract looking for preference from callback data
        looking_for = query.data.split('_')[2]
        
        # Save looking for preference in database
        await db.save_user_profile(user_id, looking_for=looking_for)
    
        # Return to profile edit menu
        await UserProfileManager.edit_profile(update, context)
    
    @staticmethod
    async def save_age_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Save age edit in profile.
        
        Args:
            update: Telegram update
            context: Telegram context
        """
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
        
        # Return to profile edit menu
        await UserProfileManager.edit_profile(update, context)

# Словарь для отслеживания, была ли показана гифка пользователю
animation_shown = {}

async def send_main_menu_with_animation(
    user_id: int, 
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Send animation and main menu message for a user.
    The animation is shown only once per session, while the menu message
    is created for editing in subsequent interactions.
    
    Args:
        user_id: Telegram user ID
        context: Telegram bot context
    """
    try:
        # Создаем клавиатуру
        keyboard = MAIN_MENU_KEYBOARD
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Очищаем предыдущие сообщения
        await delete_messages(user_id, context)
        
        # Проверяем, показывали ли мы анимацию этому пользователю
        if user_id not in state.animation_shown or not state.animation_shown[user_id]:
            # URL гифки
            animation_url = "https://media1.giphy.com/media/v1.Y2lkPTc5MGI3NjExbTB1ZWRwaGpiNW1vd3dpdzZoNnBweTRqYWNsODlmaHE4M2l0aXRndCZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/gdcnaVs40BdrmV62g8/giphy.gif"
            
            # Отправляем анимацию с текстом меню
            message = await context.bot.send_animation(
                chat_id=user_id,
                animation=animation_url,
                caption=MAIN_MENU_TEXT,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup
            )
            
            # Помечаем, что анимация показана
            state.animation_shown[user_id] = True
            
            # Сохраняем ID сообщения с анимацией как главное сообщение
            state.main_message_ids[user_id] = message.message_id
            await db.update_main_message_id(user_id, message.message_id)
            
            # Добавляем в список сообщений пользователя
            if user_id not in state.user_messages:
                state.user_messages[user_id] = []
            state.user_messages[user_id].append(message.message_id)
        else:
            # Если анимация уже была показана, просто отправляем текстовое сообщение с меню
            await update_main_message(
                user_id,
                context,
                MAIN_MENU_TEXT,
                reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending main menu with animation: {e}")

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
    application.add_handler(CallbackQueryHandler(start, pattern="^start$"))  # Добавляем обработчик для кнопки "Главное меню"
    application.add_handler(CallbackQueryHandler(home_command, pattern="^home$"))  # Добавляем обработчик для кнопки "Главное меню" в профиле
    
    # Register profile setup handlers
    application.add_handler(CallbackQueryHandler(setup_profile, pattern="^setup_profile$"))
    application.add_handler(CallbackQueryHandler(skip_profile_setup, pattern="^skip_profile_setup$"))
    application.add_handler(CallbackQueryHandler(set_gender, pattern="^gender_(?!edit_)"))
    application.add_handler(CallbackQueryHandler(set_looking_for, pattern="^looking_for_(?!edit_)"))
    application.add_handler(CallbackQueryHandler(handle_age_selection, pattern="^age_(?!edit_)"))
    application.add_handler(CallbackQueryHandler(toggle_interest, pattern="^toggle_interest_"))
    application.add_handler(CallbackQueryHandler(toggle_interest, pattern="^interest_"))
    application.add_handler(CallbackQueryHandler(complete_profile, pattern="^complete_profile$"))
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
        filters.StatusUpdate.PINNED_MESSAGE & filters.ChatType.PRIVATE,
        handle_service_message
    ))
    
    # Register handler for pinned message text search
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & filters.Regex(r'(закреплено|pinned|message|сообщение)'),
        handle_service_message
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