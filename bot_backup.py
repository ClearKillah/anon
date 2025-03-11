import os
import logging
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

async def delete_messages(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Delete all messages for a user."""
    if user_id in USER_MESSAGES:
        for message_id in USER_MESSAGES[user_id]:
            try:
                await context.bot.delete_message(chat_id=user_id, message_id=message_id)
            except Exception as e:
                logger.error(f"Error deleting message: {e}")
        USER_MESSAGES[user_id] = []

async def update_main_message(user_id: int, context: ContextTypes.DEFAULT_TYPE, new_text: str, keyboard=None):
    """Update or send the main message for a user."""
    try:
        chat_data = context.chat_data
        logger.info(f"Updating main message for user {user_id}. Current chat_data: {chat_data}")
        
        if 'main_message_id' in chat_data:
            try:
                logger.info(f"Attempting to edit message {chat_data['main_message_id']} for user {user_id}")
                await context.bot.edit_message_text(
                    text=new_text,
                    chat_id=user_id,
                    message_id=chat_data['main_message_id'],
                    reply_markup=keyboard
                )
                logger.info(f"Successfully edited message for user {user_id}")
            except Exception as e:
                logger.error(f"Error updating message for user {user_id}: {e}")
                # If editing fails, send a new message
                logger.info(f"Sending new message for user {user_id} after edit failure")
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text=new_text,
                    reply_markup=keyboard
                )
                chat_data['main_message_id'] = message.message_id
                logger.info(f"New message sent with ID {message.message_id} for user {user_id}")
        else:
            logger.info(f"No main_message_id found for user {user_id}, sending new message")
            message = await context.bot.send_message(
                chat_id=user_id,
                text=new_text,
                reply_markup=keyboard
            )
            chat_data['main_message_id'] = message.message_id
            logger.info(f"New message sent with ID {message.message_id} for user {user_id}")
    except Exception as e:
        logger.error(f"Unexpected error in update_main_message for user {user_id}: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send initial message with search button."""
    try:
        user_id = update.effective_chat.id
        logger.info(f"User {user_id} started the bot")
        
        # Initialize message list for user if not exists
        if user_id not in USER_MESSAGES:
            USER_MESSAGES[user_id] = []
            logger.info(f"Initialized message list for user {user_id}")
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Найти собеседника", callback_data="search")]
        ])
        
        # Delete old main message if exists
        if 'main_message_id' in context.chat_data:
            try:
                logger.info(f"Deleting old message {context.chat_data['main_message_id']} for user {user_id}")
                await context.bot.delete_message(
                    chat_id=user_id,
                    message_id=context.chat_data['main_message_id']
                )
            except Exception as e:
                logger.error(f"Error deleting old main message for user {user_id}: {e}")
        
        # Send new message
        message = await context.bot.send_message(
            chat_id=user_id,
            text="👋 Добро пожаловать в анонимный чат!\nНажмите кнопку ниже, чтобы найти собеседника.",
            reply_markup=keyboard
        )
        logger.info(f"Sent start message {message.message_id} to user {user_id}")
        
        # Update main message ID
        context.chat_data['main_message_id'] = message.message_id
    except Exception as e:
        logger.error(f"Unexpected error in start: {e}")

async def search_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the search button press."""
    try:
        query = update.callback_query
        user_id = update.effective_user.id
        
        logger.info(f"User {user_id} is searching for a chat partner")
        
        if user_id in ACTIVE_CHATS:
            await query.answer("Вы уже в чате!")
            logger.info(f"User {user_id} is already in a chat with {ACTIVE_CHATS[user_id]}")
            return

        await query.answer()
        
        # Remove from searching if already searching
        USERS_SEARCHING.discard(user_id)
        
        # Look for available chat partner
        partner_id = None
        for searching_user in USERS_SEARCHING:
            if searching_user != user_id:
                partner_id = searching_user
                USERS_SEARCHING.remove(partner_id)
                logger.info(f"Found partner {partner_id} for user {user_id}")
                break
        
        if partner_id is None:
            # No partner found, add user to searching list
            USERS_SEARCHING.add(user_id)
            logger.info(f"No partner found for user {user_id}, added to searching list")
            
            # Use simple send_message instead of update_main_message
            try:
                # Delete old main message if exists
                if 'main_message_id' in context.chat_data:
                    try:
                        await context.bot.delete_message(
                            chat_id=user_id,
                            message_id=context.chat_data['main_message_id']
                        )
                    except Exception as e:
                        logger.error(f"Error deleting old message: {e}")
                
                # Send new message
                keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Отменить поиск", callback_data="cancel_search")]])
                message = await context.bot.send_message(
                    chat_id=user_id,
                    text="🔍 Поиск собеседника...\nПожалуйста, подождите.",
                    reply_markup=keyboard
                )
                context.chat_data['main_message_id'] = message.message_id
                logger.info(f"Sent search message with ID {message.message_id} to user {user_id}")
            except Exception as e:
                logger.error(f"Error sending search message: {e}")
        else:
            # Partner found, create chat
            ACTIVE_CHATS[user_id] = partner_id
            ACTIVE_CHATS[partner_id] = user_id
            logger.info(f"Created chat between user {user_id} and partner {partner_id}")
            
            # Initialize message lists if not exist
            if user_id not in USER_MESSAGES:
                USER_MESSAGES[user_id] = []
            if partner_id not in USER_MESSAGES:
                USER_MESSAGES[partner_id] = []
            
            # Create keyboard for both users
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Пропустить", callback_data="skip"),
                    InlineKeyboardButton("Завершить", callback_data="end")
                ]
            ])
            
            # Handle initiator (user_id) - Update the search message
            try:
                # Delete old main message if exists
                if 'main_message_id' in context.chat_data:
                    try:
                        await context.bot.delete_message(
                            chat_id=user_id,
                            message_id=context.chat_data['main_message_id']
                        )
                    except Exception as e:
                        logger.error(f"Error deleting old message: {e}")
                
                # Send a new message
                user_message = await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ Собеседник найден! Можете начинать общение.",
                    reply_markup=keyboard
                )
                context.chat_data['main_message_id'] = user_message.message_id
                logger.info(f"Sent new message {user_message.message_id} to initiator {user_id}")
                
                # Pin the message
                try:
                    await context.bot.pin_chat_message(
                        chat_id=user_id,
                        message_id=user_message.message_id,
                        disable_notification=True
                    )
                    logger.info(f"Pinned message for initiator {user_id}")
                except Exception as e:
                    logger.error(f"Error pinning message for initiator {user_id}: {e}")
            except Exception as e:
                logger.error(f"Error handling initiator {user_id}: {e}")
            
            # Clear any previous messages (except the main message)
            await delete_messages(user_id, context)
            await delete_messages(partner_id, context)
            
            # Handle partner
            try:
                logger.info(f"Sending new message to partner {partner_id}")
                
                # Simply send new message to partner without trying to delete old one
                partner_message = await context.bot.send_message(
                    chat_id=partner_id,
                    text="✅ Собеседник найден! Можете начинать общение.",
                    reply_markup=keyboard
                )
                logger.info(f"Sent message {partner_message.message_id} to partner {partner_id}")
                
                # Pin the message
                try:
                    logger.info(f"Pinning message {partner_message.message_id} for partner {partner_id}")
                    await context.bot.pin_chat_message(
                        chat_id=partner_id,
                        message_id=partner_message.message_id,
                        disable_notification=True
                    )
                except Exception as e:
                    logger.error(f"Error pinning message for partner {partner_id}: {e}")
            except Exception as e:
                logger.error(f"Error handling partner {partner_id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in search_chat: {e}")

async def cancel_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel ongoing search."""
    try:
        query = update.callback_query
        user_id = update.effective_user.id
        
        logger.info(f"User {user_id} is canceling search")
        
        USERS_SEARCHING.discard(user_id)
        logger.info(f"Removed user {user_id} from searching list")
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Найти собеседника", callback_data="search")]
        ])
        
        await query.answer()
        
        # Delete old main message if exists
        if 'main_message_id' in context.chat_data:
            try:
                logger.info(f"Deleting old message {context.chat_data['main_message_id']} for user {user_id}")
                await context.bot.delete_message(
                    chat_id=user_id,
                    message_id=context.chat_data['main_message_id']
                )
            except Exception as e:
                logger.error(f"Error deleting old main message for user {user_id}: {e}")
        
        # Send new message
        message = await context.bot.send_message(
            chat_id=user_id,
            text="🔍 Поиск отменен.\nНажмите кнопку ниже, чтобы начать новый поиск.",
            reply_markup=keyboard
        )
        logger.info(f"Sent cancel message {message.message_id} to user {user_id}")
        
        # Update main message ID
        context.chat_data['main_message_id'] = message.message_id
    except Exception as e:
        logger.error(f"Unexpected error in cancel_search: {e}")

async def end_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End the current chat."""
    try:
        query = update.callback_query
        user_id = update.effective_user.id
        
        logger.info(f"User {user_id} is ending chat")
        
        if user_id not in ACTIVE_CHATS:
            await query.answer("У вас нет активного чата!")
            logger.info(f"User {user_id} has no active chat to end")
            return
        
        partner_id = ACTIVE_CHATS[user_id]
        logger.info(f"Ending chat between user {user_id} and partner {partner_id}")
        
        # Unpin messages for both users
        try:
            logger.info(f"Unpinning messages for user {user_id}")
            await context.bot.unpin_all_chat_messages(chat_id=user_id)
        except Exception as e:
            logger.error(f"Error unpinning messages for user {user_id}: {e}")
        
        try:
            logger.info(f"Unpinning messages for partner {partner_id}")
            await context.bot.unpin_all_chat_messages(chat_id=partner_id)
        except Exception as e:
            logger.error(f"Error unpinning messages for partner {partner_id}: {e}")
        
        # Remove chat for both users
        del ACTIVE_CHATS[user_id]
        del ACTIVE_CHATS[partner_id]
        logger.info(f"Removed chat entries for user {user_id} and partner {partner_id}")
        
        # Clear chat history
        await delete_messages(user_id, context)
        await delete_messages(partner_id, context)
        
        # Create keyboard for both users
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Найти собеседника", callback_data="search")]
        ])
        
        await query.answer()
        
        # Handle initiator (user_id)
        try:
            logger.info(f"Sending new message to initiator {user_id}")
            
            # Delete old main message if exists
            if 'main_message_id' in context.chat_data:
                try:
                    logger.info(f"Deleting old message {context.chat_data['main_message_id']} for initiator {user_id}")
                    await context.bot.delete_message(
                        chat_id=user_id,
                        message_id=context.chat_data['main_message_id']
                    )
                except Exception as e:
                    logger.error(f"Error deleting old main message for initiator {user_id}: {e}")
            
            # Send new message to initiator
            user_message = await context.bot.send_message(
                chat_id=user_id,
                text="Чат завершен.\nНажмите кнопку ниже, чтобы найти нового собеседника.",
                reply_markup=keyboard
            )
            logger.info(f"Sent message {user_message.message_id} to initiator {user_id}")
            
            # Update main message ID in context
            context.chat_data['main_message_id'] = user_message.message_id
        except Exception as e:
            logger.error(f"Error handling initiator {user_id}: {e}")
        
        # Handle partner
        try:
            logger.info(f"Sending new message to partner {partner_id}")
            
            # Delete old main message if exists for partner
            if partner_id in context.chat_data and 'main_message_id' in context.chat_data[partner_id]:
                try:
                    await context.bot.delete_message(
                        chat_id=partner_id,
                        message_id=context.chat_data[partner_id]['main_message_id']
                    )
                except Exception as e:
                    logger.error(f"Error deleting old message for partner: {e}")
            
            # Send new message to partner
            partner_message = await context.bot.send_message(
                chat_id=partner_id,
                text="Собеседник покинул чат.\nНажмите кнопку ниже, чтобы найти нового собеседника.",
                reply_markup=keyboard
            )
            logger.info(f"Sent message {partner_message.message_id} to partner {partner_id}")
            
            # Store the message ID in application's chat_data
            context.chat_data.setdefault(partner_id, {})['main_message_id'] = partner_message.message_id
        except Exception as e:
            logger.error(f"Error handling partner {partner_id}: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in end_chat: {e}")

async def skip_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Skip current chat and search for new partner."""
    await end_chat(update, context)
    await search_chat(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages and media."""
    user_id = update.effective_user.id
    
    if user_id not in ACTIVE_CHATS:
        return
    
    partner_id = ACTIVE_CHATS[user_id]
    message = update.message
    
    # Store message ID for later deletion
    if user_id not in USER_MESSAGES:
        USER_MESSAGES[user_id] = []
    USER_MESSAGES[user_id].append(message.message_id)
    
    # Forward different types of content
    sent_message = None
    if message.text:
        sent_message = await context.bot.send_message(chat_id=partner_id, text=message.text)
    elif message.voice:
        sent_message = await context.bot.send_voice(chat_id=partner_id, voice=message.voice.file_id)
    elif message.video:
        sent_message = await context.bot.send_video(chat_id=partner_id, video=message.video.file_id)
    elif message.photo:
        sent_message = await context.bot.send_photo(chat_id=partner_id, photo=message.photo[-1].file_id)
    elif message.video_note:
        sent_message = await context.bot.send_video_note(chat_id=partner_id, video_note=message.video_note.file_id)
    elif message.sticker:
        sent_message = await context.bot.send_sticker(chat_id=partner_id, sticker=message.sticker.file_id)
    elif message.document:
        sent_message = await context.bot.send_document(chat_id=partner_id, document=message.document.file_id)
    elif message.audio:
        sent_message = await context.bot.send_audio(chat_id=partner_id, audio=message.audio.file_id)
    elif message.animation:
        sent_message = await context.bot.send_animation(chat_id=partner_id, animation=message.animation.file_id)
    
    # Store the sent message ID for later deletion
    if sent_message and partner_id in USER_MESSAGES:
        USER_MESSAGES[partner_id].append(sent_message.message_id)

def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(os.getenv("BOT_TOKEN")).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(search_chat, pattern="^search$"))
    application.add_handler(CallbackQueryHandler(cancel_search, pattern="^cancel_search$"))
    application.add_handler(CallbackQueryHandler(end_chat, pattern="^end$"))
    application.add_handler(CallbackQueryHandler(skip_chat, pattern="^skip$"))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 