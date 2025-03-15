import os
import logging
import asyncio
import signal
from datetime import datetime
import uuid
import pathlib
from typing import Dict, Optional, Set, List, Tuple, Union, Any, cast

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
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

# Global variables
USERS_SEARCHING: Set[int] = set()  # Users currently searching for a chat
ACTIVE_CHATS: Dict[int, int] = {}  # Dictionary of active chats: user_id -> partner_id
USER_MESSAGES: Dict[int, List[int]] = {}  # Dictionary to store message IDs for each user
MAIN_MESSAGE_IDS: Dict[int, int] = {}  # Dictionary to store main message ID for each user: user_id -> message_id
PIN_MESSAGE_IDS: Dict[int, int] = {}  # Dictionary to store pin notification message IDs: user_id -> message_id
# Dictionary to store ID of first messages to protect them from deletion
FIRST_MESSAGES: Dict[int, int] = {}  # user_id -> first message in active chat
# Flag indicating if user is in chat initialization state
# In this state messages are not deleted
CHAT_INITIALIZATION: Dict[int, bool] = {}  # user_id -> bool

# Directory for storing media files
MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# Constant for controlling media storage
# If True, media will be stored in the database
# If False, local disk storage will be used
STORE_MEDIA_IN_DB = True

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
        Tuple containing:
        - Optional[str]: Path to the saved file (None if error)
        - str: File extension
        - Optional[bytes]: File content (if STORE_MEDIA_IN_DB is True, None otherwise)
        - Optional[str]: MIME type of the file
    """
    # Create directories for each media type if they don't exist
    media_type_dir = os.path.join(MEDIA_DIR, message_type)
    os.makedirs(media_type_dir, exist_ok=True)
    
    # Extension mappings for different media types
    extensions = {
        "photo": ".jpg",
        "video": ".mp4",
        "voice": ".ogg",
        "sticker": ".webp",
        "video_note": ".mp4"
    }
    extension = extensions.get(message_type, "")
    
    # MIME type mappings for different media types
    mime_types = {
        "photo": "image/jpeg",
        "video": "video/mp4",
        "voice": "audio/ogg",
        "sticker": "image/webp",
        "video_note": "video/mp4"
    }
    mime_type = mime_types.get(message_type, "application/octet-stream")
    
    try:
        # Get file from Telegram
        file = await context.bot.get_file(file_id)
        
        # Try to get extension from URL if available
        if file.file_path and "." in file.file_path:
            orig_extension = pathlib.Path(file.file_path).suffix
            if orig_extension:
                extension = orig_extension
        
        # Generate unique filename
        unique_filename = f"{uuid.uuid4()}{extension}"
        file_path = os.path.join(media_type_dir, unique_filename)
        
        file_content = None
        
        # If we're storing in database, we need to get binary content of the file
        if STORE_MEDIA_IN_DB:
            # Download to temporary file first, then read its contents
            await file.download_to_drive(custom_path=file_path)
            
            # Read file content
            async with aiofiles.open(file_path, mode='rb') as f:
                file_content = await f.read()
            
            logger.info(f"Downloaded {message_type} to database, size: {len(file_content)} bytes")
        else:
            # Download file to disk only
            await file.download_to_drive(custom_path=file_path)
            logger.info(f"Downloaded {message_type} to {file_path}")
        
        return file_path, extension, file_content, mime_type
    except Exception as e:
        logger.error(f"Error downloading {message_type}: {e}")
        return None, extension, None, mime_type

async def delete_messages(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delete all non-protected messages for a user.
    
    Args:
        user_id: The user ID to delete messages for
        context: Telegram bot context
    """
    if user_id in USER_MESSAGES and USER_MESSAGES[user_id]:
        # Create a copy of the list to avoid iteration and modification issues
        messages_to_delete = USER_MESSAGES[user_id].copy()
        deleted_count = 0
        skipped_count = 0
        
        # Check for first message presence before deletion
        first_message_id = FIRST_MESSAGES.get(user_id)
        if first_message_id:
            logger.info(f"Found first message protection for user {user_id}: {first_message_id}")
        
        # Check if user is in chat initialization state
        if CHAT_INITIALIZATION.get(user_id, False):
            logger.info(f"User {user_id} is in chat initialization state, skipping message deletion")
            return
        
        for message_id in messages_to_delete:
            # Don't delete the first message if it exists in FIRST_MESSAGES
            if user_id in FIRST_MESSAGES and message_id == FIRST_MESSAGES[user_id]:
                logger.info(f"Skipping deletion of first message {message_id} for user {user_id}")
                skipped_count += 1
                continue
                
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                deleted_count += 1
                # Remove message ID from user's list
                if message_id in USER_MESSAGES[user_id]:
                    USER_MESSAGES[user_id].remove(message_id)
            except Exception as e:
                logger.error(f"Error deleting message {message_id} for user {user_id}: {e}")
                # If message couldn't be deleted due to error, remove it from the list too
                if message_id in USER_MESSAGES[user_id]:
                    USER_MESSAGES[user_id].remove(message_id)
        
        logger.info(f"Deleted {deleted_count} messages and skipped {skipped_count} for user {user_id}")
        
        # Update message list, keeping only the first message if it exists
        if user_id in FIRST_MESSAGES:
            first_msg = FIRST_MESSAGES[user_id]
            if first_msg not in USER_MESSAGES[user_id]:
                USER_MESSAGES[user_id].append(first_msg)
                logger.info(f"Re-added first message {first_msg} to USER_MESSAGES for user {user_id}")

async def clear_all_messages(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delete ALL messages for a user, including the protected first message.
    
    Args:
        user_id: The user ID to delete all messages for
        context: Telegram bot context
    """
    if user_id in USER_MESSAGES and USER_MESSAGES[user_id]:
        # Check if user is in chat initialization state
        if CHAT_INITIALIZATION.get(user_id, False):
            logger.info(f"User {user_id} is in chat initialization state, skipping ALL message deletion")
            return
            
        # Create a copy of the list to avoid iteration and modification issues
        messages_to_delete = USER_MESSAGES[user_id].copy()
        deleted_count = 0
        
        for message_id in messages_to_delete:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                deleted_count += 1
            except Exception as e:
                logger.error(f"Error deleting message {message_id} for user {user_id}: {e}")
        
        logger.info(f"Deleted ALL {deleted_count} messages for user {user_id}")
        
        # Completely clear the message list
        USER_MESSAGES[user_id] = []
        # Remove first message record
        if user_id in FIRST_MESSAGES:
            del FIRST_MESSAGES[user_id]
            logger.info(f"Removed first message protection for user {user_id}")

async def update_main_message(
    user_id: int, 
    context: ContextTypes.DEFAULT_TYPE, 
    new_text: str, 
    keyboard: Optional[InlineKeyboardMarkup] = None
) -> None:
    """
    Update the main message for a user, creating a new one if it doesn't exist.
    
    Args:
        user_id: The user ID to update the message for
        context: Telegram bot context
        new_text: The new text to display
        keyboard: Optional inline keyboard markup to attach to the message
    """
    try:
        logger.info(f"Updating main message for user {user_id}")
        
        # Get user info from context
        chat = await context.bot.get_chat(user_id)
        
        # Add user to database if not exists
        await db.add_user(
            user_id=user_id,
            username=chat.username,
            first_name=chat.first_name,
            last_name=chat.last_name
        )
        
        if user_id in MAIN_MESSAGE_IDS:
            try:
                # Try to edit existing message
                await context.bot.edit_message_text(
                    text=new_text,
                    chat_id=user_id,
                    message_id=MAIN_MESSAGE_IDS[user_id],
                    reply_markup=keyboard
                )
                logger.info(f"Successfully edited message for user {user_id}")
                
                # Update message ID in database
                await db.update_main_message_id(user_id, MAIN_MESSAGE_IDS[user_id])
            except Exception as e:
                logger.error(f"Error editing message for user {user_id}: {e}")
                # If editing fails, send a new message
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text=new_text,
                    reply_markup=keyboard
                )
                MAIN_MESSAGE_IDS[user_id] = message.message_id
                
                # Update message ID in database
                await db.update_main_message_id(user_id, message.message_id)
                logger.info(f"Sent new message with ID {message.message_id} for user {user_id}")
        else:
            # Send new message if no main message exists
            message = await context.bot.send_message(
                chat_id=user_id,
                text=new_text,
                reply_markup=keyboard
            )
            MAIN_MESSAGE_IDS[user_id] = message.message_id
            
            # Update message ID in database
            await db.update_main_message_id(user_id, message.message_id)
            logger.info(f"Created new main message with ID {message.message_id} for user {user_id}")
    except Exception as e:
        logger.error(f"Unexpected error in update_main_message for user {user_id}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start command handler."""
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    
    # Add user to database
    await db.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )

    # Store command message for cleanup
    if user.id not in USER_MESSAGES:
        USER_MESSAGES[user.id] = []
    USER_MESSAGES[user.id].append(update.message.message_id)

    # Check if user is already in a chat
    active_chat = await db.get_active_chat(user.id)
    if active_chat:
        chat_id, partner_id = active_chat
        keyboard = [
            [
                InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
                InlineKeyboardButton("Завершить", callback_data="stop_chat"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await update_main_message(
            user.id,
            context,
            "Вы уже в чате с собеседником.\nИспользуйте кнопки ниже для управления чатом.",
            reply_markup
        )
        return

    # Check if user is already searching
    is_searching = user.id in await db.get_searching_users()
    if is_searching:
        keyboard = [[InlineKeyboardButton("Отменить поиск", callback_data="cancel_search")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = await update_main_message(
            user.id,
            context,
            "Идет поиск собеседника...",
            reply_markup
        )
        return

    keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send/update main message
    message = await update_main_message(
        user.id,
        context,
        "Добро пожаловать! Нажмите кнопку ниже, чтобы начать поиск собеседника.",
        reply_markup
    )

async def search_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the search_chat button click."""
    if not update.callback_query or not update.effective_user:
        return
    
    query = update.callback_query
    user = update.effective_user
    user_id = user.id
    
    # Add user to database if not exists
    await db.add_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # Check if user is already in chat
    active_chat = await db.get_active_chat(user_id)
    if active_chat:
        await query.answer("Вы уже находитесь в чате!")
        return

    # Check if user is already searching
    if user_id in await db.get_searching_users():
        await query.answer("Поиск уже идёт!")
        return

    # Set user as searching
    await db.set_user_searching(user_id, True)
    
    # Устанавливаем флаг инициализации чата для пользователя
    CHAT_INITIALIZATION[user_id] = True
    
    # Get all searching users
    searching_users = await db.get_searching_users()
    potential_partners = [uid for uid in searching_users if uid != user_id]

    if potential_partners:
        # Get random partner from searching users
        partner_id = potential_partners[0]
        
        # Устанавливаем флаг инициализации чата для партнера
        CHAT_INITIALIZATION[partner_id] = True
        
        # Create new chat
        chat_id = await db.create_chat(user_id, partner_id)
        
        # Set both users as not searching
        await db.set_user_searching(user_id, False)
        await db.set_user_searching(partner_id, False)

        # Логируем текущее состояние перед очисткой
        logger.info(f"Before chat start - FIRST_MESSAGES: {FIRST_MESSAGES}")
        logger.info(f"Before chat start - USER_MESSAGES for {user_id}: {USER_MESSAGES.get(user_id, [])}")
        logger.info(f"Before chat start - USER_MESSAGES for {partner_id}: {USER_MESSAGES.get(partner_id, [])}")
        
        # Временно сохраняем значения FIRST_MESSAGES перед очисткой
        user_first_msg = FIRST_MESSAGES.get(user_id)
        partner_first_msg = FIRST_MESSAGES.get(partner_id)
        
        # Очищаем данные о первых сообщениях при создании нового чата
        if user_id in FIRST_MESSAGES:
            logger.info(f"Removing first message protection for user {user_id}: {FIRST_MESSAGES[user_id]}")
            del FIRST_MESSAGES[user_id]
        if partner_id in FIRST_MESSAGES:
            logger.info(f"Removing first message protection for user {partner_id}: {FIRST_MESSAGES[partner_id]}")
            del FIRST_MESSAGES[partner_id]

        # Инициализируем или очищаем списки сообщений ПЕРЕД очисткой
        if user_id not in USER_MESSAGES:
            USER_MESSAGES[user_id] = []
        if partner_id not in USER_MESSAGES:
            USER_MESSAGES[partner_id] = []
            
        # Очистим существующие сообщения (полностью, включая первые сообщения)
        await clear_all_messages(user_id, context)
        await clear_all_messages(partner_id, context)
        
        # Еще раз проверяем, что списки сообщений пусты после очистки
        USER_MESSAGES[user_id] = []
        USER_MESSAGES[partner_id] = []
        
        # И проверяем, что первые сообщения точно удалены
        if user_id in FIRST_MESSAGES:
            del FIRST_MESSAGES[user_id]
        if partner_id in FIRST_MESSAGES:
            del FIRST_MESSAGES[partner_id]

        # Send messages to both users
        keyboard = [
            [
                InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
                InlineKeyboardButton("Завершить", callback_data="stop_chat"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Снимаем флаг инициализации чата для пользователей
        CHAT_INITIALIZATION[user_id] = False
        CHAT_INITIALIZATION[partner_id] = False
        
        # Update main messages for both users
        await update_main_message(
            user_id,
            context,
            "Собеседник найден! Можете начинать общение.",
            reply_markup
        )
        
        await update_main_message(
            partner_id,
            context,
            "Собеседник найден! Можете начинать общение.",
            reply_markup
        )

        try:
            # Pin messages for both users
            if user_id in MAIN_MESSAGE_IDS:
                await context.bot.pin_chat_message(
                    chat_id=user_id,
                    message_id=MAIN_MESSAGE_IDS[user_id],
                    disable_notification=True
                )
            
            if partner_id in MAIN_MESSAGE_IDS:
                await context.bot.pin_chat_message(
                    chat_id=partner_id,
                    message_id=MAIN_MESSAGE_IDS[partner_id],
                    disable_notification=True
                )
            
            # Wait a bit for pin notifications to appear
            await asyncio.sleep(2)
            
            # Try to delete pin notifications multiple times
            for _ in range(3):
                await delete_pin_message(user_id, context)
                await delete_pin_message(partner_id, context)
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Error pinning messages: {e}")

    else:
        # Update message to show searching status
        keyboard = [[InlineKeyboardButton("Отменить поиск", callback_data="cancel_search")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            user_id,
            context,
            "Поиск собеседника...",
            reply_markup
        )

    await query.answer()

async def cancel_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the cancel_search button click."""
    if not update.callback_query or not update.effective_user:
        return

    query = update.callback_query
    user = update.effective_user
    user_id = user.id

    # Add user to database if not exists
    await db.add_user(
        user_id=user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )

    # Remove user from searching state
    await db.set_user_searching(user_id, False)

    # Update message with initial search button
    keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update_main_message(
        user_id,
        context,
        "Поиск отменён. Нажмите кнопку ниже, чтобы начать поиск снова.",
        reply_markup
    )
    await query.answer("Поиск отменён")

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

async def stop_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the stop_chat button click."""
    if not update.callback_query or not update.effective_user:
        return

    query = update.callback_query
    user_id = update.effective_user.id

    # Get active chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await query.answer("У вас нет активного чата!")
        return

    chat_id, partner_id = active_chat

    try:
        # Unpin messages
        await context.bot.unpin_all_chat_messages(chat_id=user_id)
        await context.bot.unpin_all_chat_messages(chat_id=partner_id)
        
        # Delete pin messages
        try:
            # Try to delete pin notifications multiple times
            for _ in range(3):
                await delete_pin_message(user_id, context)
                await delete_pin_message(partner_id, context)
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error deleting pin messages: {e}")
        
        # End chat in database (messages will be kept)
        await db.end_chat(chat_id)

        # Удаляем все сообщения из Telegram для обоих пользователей
        # Принудительно удаляем все сообщения, игнорируя флаги инициализации
        if user_id in USER_MESSAGES:
            for message_id in USER_MESSAGES[user_id].copy():
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                except Exception as e:
                    logger.error(f"Error deleting message {message_id} for user {user_id}: {e}")
            USER_MESSAGES[user_id] = []
            
        if partner_id in USER_MESSAGES:
            for message_id in USER_MESSAGES[partner_id].copy():
                try:
                    await context.bot.delete_message(chat_id=partner_id, message_id=message_id)
                except Exception as e:
                    logger.error(f"Error deleting message {message_id} for partner {partner_id}: {e}")
            USER_MESSAGES[partner_id] = []

        # Update messages for both users
        keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update_main_message(
            user_id,
            context,
            "Чат завершен. Нажмите кнопку ниже, чтобы начать новый поиск.",
            reply_markup
        )
        
        await update_main_message(
            partner_id,
            context,
            "Собеседник завершил чат. Нажмите кнопку ниже, чтобы начать новый поиск.",
            reply_markup
        )

    except Exception as e:
        logger.error(f"Error stopping chat: {e}")
        await query.answer("Произошла ошибка при завершении чата.")

async def skip_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the skip_chat button click."""
    if not update.callback_query or not update.effective_user:
        return

    query = update.callback_query
    user_id = update.effective_user.id

    # Get active chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await query.answer("У вас нет активного чата!")
        return

    chat_id, partner_id = active_chat

    try:
        # Unpin messages
        await context.bot.unpin_all_chat_messages(chat_id=user_id)
        await context.bot.unpin_all_chat_messages(chat_id=partner_id)
        
        # Delete pin messages
        try:
            # Try to delete pin notifications multiple times
            for _ in range(3):
                await delete_pin_message(user_id, context)
                await delete_pin_message(partner_id, context)
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error deleting pin messages: {e}")
        
        # End chat in database (messages will be kept)
        await db.end_chat(chat_id)

        # Удаляем все сообщения из Telegram для обоих пользователей
        # Принудительно удаляем все сообщения, игнорируя флаги инициализации
        if user_id in USER_MESSAGES:
            for message_id in USER_MESSAGES[user_id].copy():
                try:
                    await context.bot.delete_message(chat_id=user_id, message_id=message_id)
                except Exception as e:
                    logger.error(f"Error deleting message {message_id} for user {user_id}: {e}")
            USER_MESSAGES[user_id] = []
            
        if partner_id in USER_MESSAGES:
            for message_id in USER_MESSAGES[partner_id].copy():
                try:
                    await context.bot.delete_message(chat_id=partner_id, message_id=message_id)
                except Exception as e:
                    logger.error(f"Error deleting message {message_id} for partner {partner_id}: {e}")
            USER_MESSAGES[partner_id] = []

        # Update message for skipped partner
        keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            partner_id,
            context,
            "Собеседник пропустил чат. Нажмите кнопку ниже, чтобы начать новый поиск.",
            reply_markup
        )

        # Automatically start searching for the user who skipped
        keyboard = [[InlineKeyboardButton("Отменить поиск", callback_data="cancel_search")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            user_id,
            context,
            "Поиск нового собеседника...",
            reply_markup
        )

        # Set user as searching
        await db.set_user_searching(user_id, True)

        # Try to find new partner immediately
        searching_users = await db.get_searching_users()
        potential_partners = [uid for uid in searching_users if uid != user_id]

        if potential_partners:
            new_partner_id = potential_partners[0]
            
            # Create new chat
            new_chat_id = await db.create_chat(user_id, new_partner_id)
            
            # Set both users as not searching
            await db.set_user_searching(user_id, False)
            await db.set_user_searching(new_partner_id, False)

            # Инициализируем списки сообщений для нового чата
            USER_MESSAGES[user_id] = []
            if new_partner_id not in USER_MESSAGES:
                USER_MESSAGES[new_partner_id] = []
            else:
                USER_MESSAGES[new_partner_id] = []

            # Send messages to both users
            keyboard = [
                [
                    InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
                    InlineKeyboardButton("Завершить", callback_data="stop_chat"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Update main messages for both users
            await update_main_message(
                user_id,
                context,
                "Собеседник найден! Можете начинать общение.",
                reply_markup
            )
            
            await update_main_message(
                new_partner_id,
                context,
                "Собеседник найден! Можете начинать общение.",
                reply_markup
            )
    except Exception as e:
        logger.error(f"Error in skip_chat: {e}")
        await query.answer("Произошла ошибка при пропуске чата")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    message = update.message
    message_id = message.message_id
    
    logger.info(f"Handling message {message_id} from user {user_id}")

    # Добавляем сохранение ID сообщения в самом начале функции
    # Это гарантирует, что сообщение не будет удалено до обработки
    if user_id not in USER_MESSAGES:
        USER_MESSAGES[user_id] = []
        logger.info(f"Initialized USER_MESSAGES list for user {user_id}")
    
    # Добавляем ID сообщения отправителя в список немедленно
    USER_MESSAGES[user_id].append(message_id)
    logger.info(f"Added message {message_id} to USER_MESSAGES for user {user_id}")
    
    # Установка флага инициализации чата для пользователя
    # Это предотвращает удаление сообщений во время обработки
    CHAT_INITIALIZATION[user_id] = True
    
    # Проверяем и логируем текущее состояние FIRST_MESSAGES
    if user_id in FIRST_MESSAGES:
        logger.info(f"User {user_id} already has first message: {FIRST_MESSAGES[user_id]}")
    
    # Если это первое сообщение пользователя, сохраняем его ID сразу
    if user_id not in FIRST_MESSAGES:
        FIRST_MESSAGES[user_id] = message_id
        logger.info(f"*** SAVED FIRST MESSAGE {message_id} for user {user_id} at start of processing ***")
    
    # Полное логирование состояния для отладки
    logger.info(f"Current FIRST_MESSAGES: {FIRST_MESSAGES}")
    logger.info(f"Current USER_MESSAGES: {USER_MESSAGES}")

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
        return

    # Check if user is in active chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update_main_message(
            user_id,
            context,
            "Вы не находитесь в активном чате. Нажмите кнопку ниже, чтобы начать поиск собеседника.",
            reply_markup
        )
        
        # Удаляем сообщение пользователя ТОЛЬКО если это НЕ первое сообщение
        try:
            # Проверяем, не первое ли это сообщение
            if user_id in FIRST_MESSAGES and message_id == FIRST_MESSAGES[user_id]:
                logger.info(f"Not deleting first message {message_id} for user {user_id}")
            else:
                # Не удаляем, если пользователь в режиме инициализации чата
                if not CHAT_INITIALIZATION.get(user_id, False):
                    await context.bot.delete_message(chat_id=user_id, message_id=message_id)
        except Exception as e:
            logger.error(f"Error deleting user message: {e}")
        
        # Снимаем флаг инициализации чата
        CHAT_INITIALIZATION[user_id] = False    
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
        if partner_id not in USER_MESSAGES:
            USER_MESSAGES[partner_id] = []
        
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
            CHAT_INITIALIZATION[partner_id] = True
            
            USER_MESSAGES[partner_id].append(sent_message.message_id)
            
            # Если это первое сообщение, полученное партнером, сохраняем его ID
            if partner_id not in FIRST_MESSAGES:
                FIRST_MESSAGES[partner_id] = sent_message.message_id
                logger.info(f"*** SAVED FIRST RECEIVED MESSAGE {sent_message.message_id} for user {partner_id} ***")
                
            logger.info(f"Message of type {message_type} forwarded from {user_id} to {partner_id}")
            logger.info(f"Partner's USER_MESSAGES now: {USER_MESSAGES[partner_id]}")
            logger.info(f"Partner's FIRST_MESSAGE: {FIRST_MESSAGES.get(partner_id, 'None')}")
            
            # Снимаем флаг инициализации чата для партнера
            CHAT_INITIALIZATION[partner_id] = False
        
        # Дополнительная проверка и логирование состояния FIRST_MESSAGES
        logger.info(f"Final FIRST_MESSAGES after processing: {FIRST_MESSAGES}")
        logger.info(f"Final USER_MESSAGES for {user_id}: {USER_MESSAGES[user_id]}")
        logger.info(f"Final USER_MESSAGES for {partner_id}: {USER_MESSAGES[partner_id]}")
        
        # В конце обработки снимаем флаг инициализации чата для отправителя
        CHAT_INITIALIZATION[user_id] = False
        
    except Exception as e:
        # В случае ошибки тоже снимаем флаг
        CHAT_INITIALIZATION[user_id] = False
        logger.error(f"Error handling message from {user_id}: {e}")
        
        # Используем update_main_message для показа ошибки вместо отправки нового сообщения
        keyboard = [
            [
                InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
                InlineKeyboardButton("Завершить", callback_data="stop_chat"),
            ]
        ]
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
    keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
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
        keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
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
        keyboard = [
            [
                InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
                InlineKeyboardButton("Завершить", callback_data="stop_chat"),
            ]
        ]
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
        keyboard = [
            [
                InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
                InlineKeyboardButton("Завершить", callback_data="stop_chat"),
            ]
        ]
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
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        await update.message.reply_text("Вы не находитесь в активном чате.")
        return

    chat_id, partner_id = active_chat
    
    try:
        # Устанавливаем флаг инициализации чата для обоих пользователей
        CHAT_INITIALIZATION[user_id] = True
        CHAT_INITIALIZATION[partner_id] = True
        
        # Сохраняем ID команды, чтобы не удалять ее дважды
        command_message_id = update.message.message_id
        
        # Сохраняем информацию о первых сообщениях перед очисткой
        logger.info(f"Before clearing history - FIRST_MESSAGES: {FIRST_MESSAGES}")
        logger.info(f"Before clearing history - USER_MESSAGES for {user_id}: {USER_MESSAGES.get(user_id, [])}")
        logger.info(f"Before clearing history - USER_MESSAGES for {partner_id}: {USER_MESSAGES.get(partner_id, [])}")
        
        # Временно сохраняем ID первых сообщений
        user_first_msg = FIRST_MESSAGES.get(user_id)
        partner_first_msg = FIRST_MESSAGES.get(partner_id)
        
        # Delete all messages except first ones from Telegram only
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)
        
        # Get current keyboard
        keyboard = [
            [
                InlineKeyboardButton("Пропустить", callback_data="skip_chat"),
                InlineKeyboardButton("Завершить", callback_data="stop_chat"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Update main messages with notification
        await update_main_message(
            user_id,
            context,
            "История чата очищена!\nВы в чате с собеседником.",
            reply_markup
        )
        
        await update_main_message(
            partner_id,
            context,
            "Собеседник очистил историю чата!\nВы в чате с собеседником.",
            reply_markup
        )
        
        # Delete the command message
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=command_message_id)
        except Exception as e:
            logger.error(f"Error deleting clear command message: {e}")
        
        # Снимаем флаг инициализации чата для обоих пользователей
        CHAT_INITIALIZATION[user_id] = False
        CHAT_INITIALIZATION[partner_id] = False
            
    except Exception as e:
        # Снимаем флаг инициализации чата в случае ошибки
        CHAT_INITIALIZATION[user_id] = False
        if partner_id:
            CHAT_INITIALIZATION[partner_id] = False
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
        stats_text += f"\n\n⚙️ Режим хранения медиафайлов: {'В базе данных' if STORE_MEDIA_IN_DB else 'На диске'}"
        
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
    
    global STORE_MEDIA_IN_DB
    STORE_MEDIA_IN_DB = not STORE_MEDIA_IN_DB
    
    mode_text = "базе данных" if STORE_MEDIA_IN_DB else "локальном хранилище"
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
    """Initialize database connection."""
    try:
        # Get database URL from environment variables
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise ValueError("DATABASE_URL environment variable is not set")
        
        await db.connect(db_url)
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

async def cleanup_db(application: Application) -> None:
    """Cleanup database connections."""
    try:
        await db.disconnect()
        logger.info("Database connection closed successfully")
    except Exception as e:
        logger.error(f"Error closing database connection: {e}")

def main() -> None:
    """Start the bot."""
    # Check for required environment variables
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN environment variable is not set")
        return
    
    # Initialize global variables
    global USERS_SEARCHING, ACTIVE_CHATS, USER_MESSAGES, MAIN_MESSAGE_IDS, PIN_MESSAGE_IDS, FIRST_MESSAGES, CHAT_INITIALIZATION
    
    # Clear global variables on startup
    USERS_SEARCHING = set()
    ACTIVE_CHATS = {}
    USER_MESSAGES = {}
    MAIN_MESSAGE_IDS = {}
    PIN_MESSAGE_IDS = {}
    FIRST_MESSAGES = {}
    CHAT_INITIALIZATION = {}
    
    # Create the Application
    application = Application.builder().token(bot_token).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("pin", pin_message))
    application.add_handler(CommandHandler("unpin", unpin_message))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("media_stats", media_stats))
    application.add_handler(CommandHandler("resend_media", resend_media))
    application.add_handler(CommandHandler("toggle_storage", toggle_storage_mode))
    application.add_handler(CommandHandler("import_media", import_media_to_db))
    
    # Add callback query handlers
    application.add_handler(CallbackQueryHandler(search_chat, pattern="^search_chat$"))
    application.add_handler(CallbackQueryHandler(cancel_search, pattern="^cancel_search$"))
    application.add_handler(CallbackQueryHandler(stop_chat, pattern="^stop_chat$"))
    application.add_handler(CallbackQueryHandler(skip_chat, pattern="^skip_chat$"))
    
    # Add handler for service messages (should be before general message handler)
    application.add_handler(MessageHandler(
        filters.StatusUpdate.PINNED_MESSAGE & filters.ChatType.PRIVATE,
        handle_service_message
    ))
    
    # Add handler for pinned message text search
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & filters.Regex(r'(закреплено|pinned|message|сообщение)'),
        handle_service_message
    ))
    
    # Add media message handlers
    application.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.VOICE | filters.Sticker.ALL | filters.VIDEO_NOTE) & filters.ChatType.PRIVATE,
        handle_message
    ))
    
    # General text message handler (should be last)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set database lifecycle hooks
    application.post_init = init_db
    application.post_shutdown = cleanup_db

    # Start the bot
    logger.info("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot stopped successfully!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user!")
    except Exception as e:
        logger.error(f"Fatal error: {e}") 