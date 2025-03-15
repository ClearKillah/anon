import os
import logging
import asyncio
import signal
from datetime import datetime
from typing import Dict, Optional, Set, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Global variables
USERS_SEARCHING = set()  # Users currently searching for a chat
ACTIVE_CHATS: Dict[int, int] = {}  # Dictionary of active chats: user_id -> partner_id
USER_MESSAGES: Dict[int, List[int]] = {}  # Dictionary to store message IDs for each user
MAIN_MESSAGE_IDS: Dict[int, int] = {}  # Dictionary to store main message ID for each user: user_id -> message_id
PIN_MESSAGE_IDS: Dict[int, int] = {}  # Dictionary to store pin notification message IDs: user_id -> message_id

async def delete_messages(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Delete all messages for a user."""
    if user_id in USER_MESSAGES:
        for message_id in USER_MESSAGES[user_id]:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=message_id)
            except Exception as e:
                logger.error(f"Error deleting message: {e}")
        USER_MESSAGES[user_id] = []

async def update_main_message(user_id: int, context: ContextTypes.DEFAULT_TYPE, new_text: str, keyboard=None) -> None:
    """Update the main message for a user."""
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
            except Exception as e:
                logger.error(f"Error editing message for user {user_id}: {e}")
                # If editing fails, send a new message
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text=new_text,
                    reply_markup=keyboard
                )
                MAIN_MESSAGE_IDS[user_id] = message.message_id
                logger.info(f"Sent new message with ID {message.message_id} for user {user_id}")
        else:
            # Send new message if no main message exists
            message = await context.bot.send_message(
                chat_id=user_id,
                text=new_text,
                reply_markup=keyboard
            )
            MAIN_MESSAGE_IDS[user_id] = message.message_id
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
    
    # Get all searching users
    searching_users = await db.get_searching_users()
    potential_partners = [uid for uid in searching_users if uid != user_id]

    if potential_partners:
        # Get random partner from searching users
        partner_id = potential_partners[0]
        
        # Create new chat
        chat_id = await db.create_chat(user_id, partner_id)
        
        # Set both users as not searching
        await db.set_user_searching(user_id, False)
        await db.set_user_searching(partner_id, False)

        # Clear previous chat history from Telegram (but keep in DB)
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)

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
        
        # Clear chat history from Telegram (but keep in DB)
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)
        
        # Delete pin messages
        try:
            # Try to delete pin notifications multiple times
            for _ in range(3):
                await delete_pin_message(user_id, context)
                await delete_pin_message(partner_id, context)
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error deleting pin messages: {e}")
        
        # End chat in database
        await db.end_chat(chat_id)

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

        await query.answer("Чат завершен")
    except Exception as e:
        logger.error(f"Error in stop_chat: {e}")
        await query.answer("Произошла ошибка при завершении чата")

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
        
        # Clear chat history from Telegram (but keep in DB)
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)
        
        # Delete pin messages
        try:
            # Try to delete pin notifications multiple times
            for _ in range(3):
                await delete_pin_message(user_id, context)
                await delete_pin_message(partner_id, context)
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error deleting pin messages: {e}")
        
        # End chat in database
        await db.end_chat(chat_id)

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

            try:
                # Pin messages for both users
                if user_id in MAIN_MESSAGE_IDS:
                    await context.bot.pin_chat_message(
                        chat_id=user_id,
                        message_id=MAIN_MESSAGE_IDS[user_id],
                        disable_notification=True
                    )
                
                if new_partner_id in MAIN_MESSAGE_IDS:
                    await context.bot.pin_chat_message(
                        chat_id=new_partner_id,
                        message_id=MAIN_MESSAGE_IDS[new_partner_id],
                        disable_notification=True
                    )
                
                # Wait a bit for pin notifications to appear
                await asyncio.sleep(2)
                
                # Try to delete pin notifications multiple times
                for _ in range(3):
                    await delete_pin_message(user_id, context)
                    await delete_pin_message(new_partner_id, context)
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"Error pinning messages: {e}")

        await query.answer("Поиск нового собеседника...")
        
    except Exception as e:
        logger.error(f"Error in skip_chat: {e}")
        await query.answer("Произошла ошибка при пропуске чата")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages."""
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    message_text = update.message.text
    message_id = update.message.message_id

    if not message_text:
        return

    # Check if user is in active chat
    active_chat = await db.get_active_chat(user_id)
    if not active_chat:
        keyboard = [[InlineKeyboardButton("Начать поиск", callback_data="search_chat")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Вы не находитесь в активном чате. Нажмите кнопку ниже, чтобы начать поиск собеседника.",
            reply_markup=reply_markup
        )
        return

    chat_id, partner_id = active_chat

    try:
        # Store message in database
        await db.add_message(chat_id, user_id, message_text)
        
        # Store original message ID for cleanup
        if user_id not in USER_MESSAGES:
            USER_MESSAGES[user_id] = []
        USER_MESSAGES[user_id].append(message_id)
        
        # Forward message to partner
        sent_message = await context.bot.send_message(
            chat_id=partner_id,
            text=message_text
        )
        
        # Store forwarded message ID for cleanup
        if partner_id not in USER_MESSAGES:
            USER_MESSAGES[partner_id] = []
        USER_MESSAGES[partner_id].append(sent_message.message_id)
        
        logger.info(f"Message forwarded from {user_id} to {partner_id}")
    except Exception as e:
        logger.error(f"Error handling message from {user_id}: {e}")
        await update.message.reply_text(
            "Произошла ошибка при отправке сообщения. Попробуйте еще раз или используйте /stop для завершения чата."
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
        # Delete all messages
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)
        
        # Clear messages from database
        await db.clear_chat_messages(chat_id)
        
        # Send notifications
        await context.bot.send_message(
            chat_id=user_id,
            text="История чата очищена!"
        )
        await context.bot.send_message(
            chat_id=partner_id,
            text="Собеседник очистил историю чата!"
        )
    except Exception as e:
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

async def init_db(application: Application) -> None:
    """Initialize database connection."""
    try:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise ValueError("DATABASE_URL environment variable is not set")
        await db.connect(dsn)
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
    # Create the Application
    application = Application.builder().token(os.getenv("BOT_TOKEN")).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("pin", pin_message))
    application.add_handler(CommandHandler("unpin", unpin_message))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CallbackQueryHandler(search_chat, pattern="^search_chat$"))
    application.add_handler(CallbackQueryHandler(cancel_search, pattern="^cancel_search$"))
    application.add_handler(CallbackQueryHandler(stop_chat, pattern="^stop_chat$"))
    application.add_handler(CallbackQueryHandler(skip_chat, pattern="^skip_chat$"))
    
    # Add handler for service messages (should be before general message handler)
    # Обрабатываем как обновления с закрепленными сообщениями, так и просто сервисные сообщения
    application.add_handler(MessageHandler(
        filters.StatusUpdate.PINNED_MESSAGE & filters.ChatType.PRIVATE,
        handle_service_message
    ))
    
    # Добавляем еще один обработчик для поиска по тексту сообщений о закреплении
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE & filters.Regex(r'(закреплено|pinned|message|сообщение)'),
        handle_service_message
    ))
    
    # General message handler should be last
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Run database initialization in the event loop
    application.post_init = init_db
    application.post_shutdown = cleanup_db

    # Start the bot
    print("Starting bot...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    print("\nBot stopped successfully!")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot stopped by user!")
    except Exception as e:
        logger.error(f"Fatal error: {e}") 