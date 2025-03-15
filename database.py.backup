import asyncpg
import logging
from typing import Optional, List, Tuple, Union, Dict, Any, cast
from datetime import datetime
import os
import aiofiles
import aiosqlite

logger = logging.getLogger(__name__)

class Database:
    """Database manager for the anonymous chat application."""
    
    def __init__(self):
        """Initialize the database manager with no connection."""
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self, dsn: str) -> None:
        """
        Connect to the database.
        
        Args:
            dsn: Database connection string
            
        Raises:
            Exception: If connection fails
        """
        try:
            self.pool = await asyncpg.create_pool(dsn)
            # await self.drop_tables()  # Drop existing tables - uncomment if needed
            await self.create_tables()  # Create tables with new schema
            await self.migrate_tables()  # Perform migrations if necessary
            logger.info("Successfully connected to the database")
        except Exception as e:
            logger.error(f"Error connecting to the database: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from the database and release resources."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Successfully disconnected from the database")

    async def create_tables(self) -> None:
        """
        Create database tables if they don't exist.
        
        Tables:
        - users: Store user information
        - ended_chats: Store information about ended chat sessions
        - active_chats: Store information about currently active chats
        - messages: Store chat messages including media content
        - user_state: Store user state information
        - user_profile: Store user profile information
        - user_interests: Store user interests
        - interests: Store available interests
        - user_ratings: Store user ratings
        """
        if not self.pool:
            logger.error("Cannot create tables: database connection not established")
            return
            
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Create users table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username VARCHAR(255),
                        first_name VARCHAR(255),
                        last_name VARCHAR(255),
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Create ended_chats table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS ended_chats (
                        chat_id INTEGER PRIMARY KEY,
                        user_id_1 BIGINT REFERENCES users(user_id),
                        user_id_2 BIGINT REFERENCES users(user_id),
                        started_at TIMESTAMP,
                        ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

                # Create active_chats table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS active_chats (
                        chat_id SERIAL PRIMARY KEY,
                        user_id_1 BIGINT REFERENCES users(user_id),
                        user_id_2 BIGINT REFERENCES users(user_id),
                        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(user_id_1),
                        UNIQUE(user_id_2)
                    )
                ''')

                # Create messages table with structure to store file content
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id SERIAL PRIMARY KEY,
                        chat_id INTEGER,
                        sender_id BIGINT REFERENCES users(user_id),
                        content TEXT,
                        message_type VARCHAR(20) DEFAULT 'text', -- type: text, photo, video, voice, sticker, video_note
                        file_id TEXT, -- for storing file ID in Telegram
                        local_file_path TEXT, -- for storing path to local file
                        file_content BYTEA, -- for storing the media file content in database
                        file_name TEXT, -- file name
                        mime_type TEXT, -- MIME type of the file
                        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (chat_id) REFERENCES active_chats(chat_id) ON DELETE SET NULL
                    )
                ''')

                # Create user_state table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_state (
                        user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
                        is_searching BOOLEAN DEFAULT FALSE,
                        main_message_id BIGINT,
                        pin_message_id BIGINT,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        profile_setup_state TEXT DEFAULT NULL,
                        setup_step INT DEFAULT 0
                    )
                ''')
                
                # Create user_profile table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_profile (
                        user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
                        gender TEXT,
                        looking_for TEXT,
                        age INT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create interests table
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS interests (
                        id SERIAL PRIMARY KEY,
                        name TEXT UNIQUE,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create user_interests table (many-to-many relationship)
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS user_interests (
                        user_id BIGINT REFERENCES users(user_id),
                        interest_id INT REFERENCES interests(id),
                        PRIMARY KEY (user_id, interest_id)
                    )
                ''')
                
                # Insert default interests
                default_interests = ["🗣️ Общение", "💋 Флирт", "🔥 Темки"]
                for interest in default_interests:
                    await conn.execute('''
                        INSERT INTO interests (name) 
                        VALUES ($1) 
                        ON CONFLICT (name) DO NOTHING
                    ''', interest)
                
                # Get IDs of our new interests
                общение_new_id = await conn.fetchval("SELECT id FROM interests WHERE name = '🗣️ Общение'")
                флирт_id = await conn.fetchval("SELECT id FROM interests WHERE name = '💋 Флирт'")
                темки_id = await conn.fetchval("SELECT id FROM interests WHERE name = '🔥 Темки'")
                
                # Check if old interests exist and get their IDs
                общение_id = await conn.fetchval("SELECT id FROM interests WHERE name = 'общение'")
                секс_id = await conn.fetchval("SELECT id FROM interests WHERE name = 'секс'")
                
                # Update references for old standard interests
                if общение_id and общение_new_id:
                    # Get all users who have the old interest
                    rows = await conn.fetch('''
                        SELECT user_id FROM user_interests WHERE interest_id = $1
                    ''', общение_id)
                    
                    # For each user, add the new interest if they don't already have it
                    for row in rows:
                        user_id = row['user_id']
                        # Check if user already has this interest
                        has_interest = await conn.fetchval('''
                            SELECT EXISTS(
                                SELECT 1 FROM user_interests 
                                WHERE user_id = $1 AND interest_id = $2
                            )
                        ''', user_id, общение_new_id)
                        
                        # If they don't have it yet, add it
                        if not has_interest:
                            await conn.execute('''
                                INSERT INTO user_interests (user_id, interest_id)
                                VALUES ($1, $2)
                            ''', user_id, общение_new_id)
                    
                    # Delete old user_interests entries
                    await conn.execute('''
                        DELETE FROM user_interests WHERE interest_id = $1
                    ''', общение_id)
                
                if секс_id and флирт_id:
                    # Get all users who have the old interest
                    rows = await conn.fetch('''
                        SELECT user_id FROM user_interests WHERE interest_id = $1
                    ''', секс_id)
                    
                    # For each user, add the new interest if they don't already have it
                    for row in rows:
                        user_id = row['user_id']
                        # Check if user already has this interest
                        has_interest = await conn.fetchval('''
                            SELECT EXISTS(
                                SELECT 1 FROM user_interests 
                                WHERE user_id = $1 AND interest_id = $2
                            )
                        ''', user_id, флирт_id)
                        
                        # If they don't have it yet, add it
                        if not has_interest:
                            await conn.execute('''
                                INSERT INTO user_interests (user_id, interest_id)
                                VALUES ($1, $2)
                            ''', user_id, флирт_id)
                    
                    # Delete old user_interests entries
                    await conn.execute('''
                        DELETE FROM user_interests WHERE interest_id = $1
                    ''', секс_id)
                
                # Migrate any other interests to "🔥 Темки" if they're not already in our new list
                if темки_id:
                    # First get all user IDs with interests that need migration
                    rows = await conn.fetch('''
                        SELECT DISTINCT ui.user_id 
                        FROM user_interests ui
                        JOIN interests i ON ui.interest_id = i.id
                        WHERE i.name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки', 'общение', 'секс')
                    ''')
                    
                    # For each user, add the "🔥 Темки" interest if they don't already have it
                    for row in rows:
                        user_id = row['user_id']
                        # Check if user already has this interest
                        has_interest = await conn.fetchval('''
                            SELECT EXISTS(
                                SELECT 1 FROM user_interests 
                                WHERE user_id = $1 AND interest_id = $2
                            )
                        ''', user_id, темки_id)
                        
                        # If they don't have it yet, add it
                        if not has_interest:
                            await conn.execute('''
                                INSERT INTO user_interests (user_id, interest_id)
                                VALUES ($1, $2)
                            ''', user_id, темки_id)
                    
                    # Delete user_interests entries for interests that are not in our new list
                    await conn.execute('''
                        DELETE FROM user_interests 
                        WHERE interest_id IN (
                            SELECT id FROM interests 
                            WHERE name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки')
                        )
                    ''')
                
                # Now delete the old interests that we've migrated
                if общение_id:
                    await conn.execute("DELETE FROM interests WHERE id = $1", общение_id)
                if секс_id:
                    await conn.execute("DELETE FROM interests WHERE id = $1", секс_id)
                
                # Safely delete interests that are not in our new list and not referenced by any users
                await conn.execute('''
                    DELETE FROM interests 
                    WHERE name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки')
                    AND id NOT IN (SELECT DISTINCT interest_id FROM user_interests)
                ''')
                
                logger.info("Database tables created or already exist")

    async def drop_tables(self) -> None:
        """
        Drop all existing tables from the database.
        Warning: This will delete all data!
        """
        if not self.pool:
            logger.error("Cannot drop tables: database connection not established")
            return
            
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute('''
                    DROP TABLE IF EXISTS messages CASCADE;
                    DROP TABLE IF EXISTS active_chats CASCADE;
                    DROP TABLE IF EXISTS ended_chats CASCADE;
                    DROP TABLE IF EXISTS user_state CASCADE;
                    DROP TABLE IF EXISTS users CASCADE;
                ''')
                logger.info("All tables dropped successfully")

    # User operations
    async def add_user(self, 
                      user_id: int, 
                      username: Optional[str], 
                      first_name: Optional[str], 
                      last_name: Optional[str]) -> None:
        """
        Add a new user or update existing one.
        
        Args:
            user_id: Telegram user ID
            username: Optional Telegram username
            first_name: Optional user's first name
            last_name: Optional user's last name
        """
        if not self.pool:
            logger.error(f"Cannot add user {user_id}: database connection not established")
            return
            
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET 
                        username = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        last_name = EXCLUDED.last_name
                ''', user_id, username, first_name, last_name)
                logger.info(f"User {user_id} added or updated")
        except Exception as e:
            logger.error(f"Error adding/updating user {user_id}: {e}")

    # Chat operations
    async def create_chat(self, user_id_1: int, user_id_2: int) -> Optional[int]:
        """
        Create a new chat between two users.
        
        Args:
            user_id_1: First user's Telegram ID
            user_id_2: Second user's Telegram ID
            
        Returns:
            The chat ID if created successfully, None otherwise
        """
        if not self.pool:
            logger.error(f"Cannot create chat: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                chat_id = await conn.fetchval('''
                    INSERT INTO active_chats (user_id_1, user_id_2)
                    VALUES ($1, $2)
                    RETURNING chat_id
                ''', user_id_1, user_id_2)
                logger.info(f"Created chat {chat_id} between users {user_id_1} and {user_id_2}")
                return chat_id
        except Exception as e:
            logger.error(f"Error creating chat between {user_id_1} and {user_id_2}: {e}")
            return None

    async def get_active_chat(self, user_id: int) -> Optional[Tuple[int, int]]:
        """
        Get active chat for a user.
        
        Args:
            user_id: User's Telegram ID
            
        Returns:
            A tuple (chat_id, partner_id) if active chat exists, None otherwise
        """
        if not self.pool:
            logger.error(f"Cannot get active chat for user {user_id}: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT chat_id, user_id_1, user_id_2
                    FROM active_chats
                    WHERE user_id_1 = $1 OR user_id_2 = $1
                ''', user_id)
                
                if row:
                    partner_id = row['user_id_2'] if user_id == row['user_id_1'] else row['user_id_1']
                    return row['chat_id'], partner_id
                return None
        except Exception as e:
            logger.error(f"Error getting active chat for user {user_id}: {e}")
            return None

    async def end_chat(self, chat_id: int) -> bool:
        """
        End a chat by moving it to ended_chats and removing from active_chats.
        
        Args:
            chat_id: The ID of the chat to end
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot end chat {chat_id}: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Check if the chat exists
                    chat = await conn.fetchrow('SELECT * FROM active_chats WHERE chat_id = $1', chat_id)
                    if not chat:
                        logger.warning(f"Cannot end chat {chat_id}: chat does not exist")
                        return False
                    
                    # Move chat to ended_chats
                    await conn.execute('''
                        INSERT INTO ended_chats (chat_id, user_id_1, user_id_2, started_at)
                        SELECT chat_id, user_id_1, user_id_2, started_at
                        FROM active_chats
                        WHERE chat_id = $1
                    ''', chat_id)
                    
                    # Remove from active_chats (but keep messages in the database)
                    await conn.execute('DELETE FROM active_chats WHERE chat_id = $1', chat_id)
                    
                    logger.info(f"Successfully ended chat {chat_id}")
                    return True
        except Exception as e:
            logger.error(f"Error ending chat {chat_id}: {e}")
            return False

    async def remove_chat(self, chat_id: int) -> bool:
        """
        Remove a chat and all its messages permanently.
        
        Args:
            chat_id: The ID of the chat to remove
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot remove chat {chat_id}: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # Start transaction
                async with conn.transaction():
                    # Delete messages first (due to foreign key constraint)
                    await conn.execute('DELETE FROM messages WHERE chat_id = $1', chat_id)
                    # Then delete the chat
                    result = await conn.execute('DELETE FROM active_chats WHERE chat_id = $1', chat_id)
                    
                    affected = result.split()[-1] if hasattr(result, 'split') else "0"
                    if affected != "0":
                        logger.info(f"Successfully removed chat {chat_id} and all its messages")
                        return True
                    else:
                        logger.warning(f"No chat with ID {chat_id} found to remove")
                        return False
        except Exception as e:
            logger.error(f"Error removing chat {chat_id}: {e}")
            return False

    async def clear_chat_messages(self, chat_id: int) -> bool:
        """
        Clear all messages from a chat and delete local media files.
        
        Args:
            chat_id: The ID of the chat to clear messages from
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot clear chat messages for chat {chat_id}: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # Get paths to local files before deleting messages
                media_files = await conn.fetch('''
                    SELECT local_file_path FROM messages 
                    WHERE chat_id = $1 AND local_file_path IS NOT NULL
                ''', chat_id)
                
                # Delete local files
                for row in media_files:
                    file_path = row['local_file_path']
                    if file_path and os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            logging.info(f"Deleted local file: {file_path}")
                        except Exception as e:
                            logging.error(f"Error deleting local file {file_path}: {e}")
                
                # Delete messages from database
                result = await conn.execute('DELETE FROM messages WHERE chat_id = $1', chat_id)
                affected = result.split()[-1] if hasattr(result, 'split') else "0"
                
                logger.info(f"Cleared {affected} messages from chat {chat_id}")
                return True
        except Exception as e:
            logger.error(f"Error clearing messages for chat {chat_id}: {e}")
            return False

    # Message operations
    async def add_message(
        self,
        chat_id: int,
        sender_id: int,
        content: Optional[str] = None,
        message_type: str = 'text',
        file_id: Optional[str] = None,
        local_file_path: Optional[str] = None,
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_content: Optional[bytes] = None
    ) -> Optional[int]:
        """
        Add a new message to the database with optional media content.
        
        Args:
            chat_id: The chat ID
            sender_id: The user ID of the sender
            content: Optional text content of the message
            message_type: Type of message (text, photo, video, etc.)
            file_id: Optional Telegram file ID
            local_file_path: Optional path to the local file
            file_name: Optional name of the file
            mime_type: Optional MIME type of the file
            file_content: Optional binary content of the file
            
        Returns:
            The message ID if successful, None otherwise
        """
        if not self.pool:
            logger.error(f"Cannot add message: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                # For text messages
                if message_type == 'text':
                    return await conn.fetchval('''
                        INSERT INTO messages (chat_id, sender_id, content, message_type)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id
                    ''', chat_id, sender_id, content, message_type)
                # For media messages
                else:
                    # If file path is specified but file content is not provided, read it
                    if local_file_path and file_content is None and os.path.exists(local_file_path):
                        try:
                            async with aiofiles.open(local_file_path, mode='rb') as f:
                                file_content = await f.read()
                        except Exception as e:
                            logger.error(f"Error reading file {local_file_path}: {e}")
                    
                    return await conn.fetchval('''
                        INSERT INTO messages 
                        (chat_id, sender_id, content, message_type, file_id, local_file_path, file_name, mime_type, file_content)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        RETURNING id
                    ''', chat_id, sender_id, content, message_type, file_id,
                        local_file_path, file_name, mime_type, file_content)
        except Exception as e:
            logger.error(f"Error adding message to chat {chat_id} from user {sender_id}: {e}")
            return None

    async def get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """
        Get full message data including media content.
        
        Args:
            message_id: The ID of the message to retrieve
            
        Returns:
            A dictionary with message data if found, None otherwise
        """
        if not self.pool:
            logger.error(f"Cannot get message {message_id}: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT * FROM messages WHERE id = $1
                ''', message_id)
                
                if row:
                    # Convert to dict for easier access
                    return dict(row)
                return None
        except Exception as e:
            logger.error(f"Error getting message {message_id}: {e}")
            return None

    async def save_media_to_db(
        self,
        message_id: int,
        file_content: bytes,
        file_name: Optional[str] = None,
        mime_type: Optional[str] = None
    ) -> bool:
        """
        Save or update media content for an existing message.
        
        Args:
            message_id: The ID of the message to update
            file_content: The binary content of the file
            file_name: Optional name of the file
            mime_type: Optional MIME type of the file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot save media for message {message_id}: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute('''
                    UPDATE messages
                    SET file_content = $2, file_name = $3, mime_type = $4
                    WHERE id = $1
                ''', message_id, file_content, file_name, mime_type)
                
                affected = result.split()[-1] if hasattr(result, 'split') else "0"
                if affected != "0":
                    logger.info(f"Updated media content for message {message_id}")
                    return True
                else:
                    logger.warning(f"No message with ID {message_id} found to update")
                    return False
        except Exception as e:
            logger.error(f"Error saving media content for message {message_id}: {e}")
            return False

    async def get_media_content(self, message_id: int) -> Optional[Tuple[bytes, str, str]]:
        """
        Get media content, filename, and MIME type for a message.
        
        Args:
            message_id: The ID of the message to retrieve media from
            
        Returns:
            A tuple of (file_content, file_name, mime_type) if found, None otherwise
        """
        if not self.pool:
            logger.error(f"Cannot get media content for message {message_id}: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT file_content, file_name, mime_type 
                    FROM messages 
                    WHERE id = $1 AND file_content IS NOT NULL
                ''', message_id)
                
                if row and row['file_content']:
                    return row['file_content'], row['file_name'], row['mime_type']
                return None
        except Exception as e:
            logger.error(f"Error getting media content for message {message_id}: {e}")
            return None

    # User state operations
    async def set_user_searching(self, user_id: int, is_searching: bool) -> bool:
        """
        Update user's searching status.
        
        Args:
            user_id: The user ID to update
            is_searching: Whether the user is currently searching
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot set searching status for user {user_id}: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # Create or update user_state record
                await conn.execute('''
                    INSERT INTO user_state (user_id, is_searching, last_updated)
                    VALUES ($1, $2, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET 
                        is_searching = EXCLUDED.is_searching,
                        last_updated = CURRENT_TIMESTAMP
                ''', user_id, is_searching)
                
                logger.info(f"Updated searching status for user {user_id} to {is_searching}")
                return True
        except Exception as e:
            logger.error(f"Error setting searching status for user {user_id}: {e}")
            return False

    async def get_searching_users(self) -> List[int]:
        """
        Get a list of users who are currently searching for a chat.
        
        Returns:
            A list of user IDs who have is_searching=True
        """
        if not self.pool:
            logger.error(f"Cannot get searching users: database connection not established")
            return []
            
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT user_id FROM user_state 
                    WHERE is_searching = TRUE
                    ORDER BY last_updated ASC
                ''')
                
                return [row['user_id'] for row in rows]
        except Exception as e:
            logger.error(f"Error getting searching users: {e}")
            return []

    async def update_main_message_id(self, user_id: int, message_id: int) -> bool:
        """
        Update the main message ID for a user.
        
        Args:
            user_id: The user ID to update
            message_id: The new main message ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot update main message ID for user {user_id}: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO user_state (user_id, main_message_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET main_message_id = EXCLUDED.main_message_id
                ''', user_id, message_id)
                
                logger.info(f"Updated main message ID for user {user_id} to {message_id}")
                return True
        except Exception as e:
            logger.error(f"Error updating main message ID for user {user_id}: {e}")
            return False

    async def get_main_message_id(self, user_id: int) -> Optional[int]:
        """
        Get the main message ID for a user.
        
        Args:
            user_id: The user ID to retrieve message ID for
            
        Returns:
            The main message ID if found, None otherwise
        """
        if not self.pool:
            logger.error(f"Cannot get main message ID for user {user_id}: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval('''
                    SELECT main_message_id FROM user_state 
                    WHERE user_id = $1
                ''', user_id)
        except Exception as e:
            logger.error(f"Error getting main message ID for user {user_id}: {e}")
            return None

    async def update_pin_message_id(self, user_id: int, message_id: int) -> bool:
        """
        Update the pin notification message ID for a user.
        
        Args:
            user_id: The user ID to update
            message_id: The new pin message ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot update pin message ID for user {user_id}: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO user_state (user_id, pin_message_id)
                    VALUES ($1, $2)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET pin_message_id = EXCLUDED.pin_message_id
                ''', user_id, message_id)
                
                logger.info(f"Updated pin message ID for user {user_id} to {message_id}")
                return True
        except Exception as e:
            logger.error(f"Error updating pin message ID for user {user_id}: {e}")
            return False

    async def get_pin_message_id(self, user_id: int) -> Optional[int]:
        """
        Get the pin notification message ID for a user.
        
        Args:
            user_id: The user ID to retrieve pin message ID for
            
        Returns:
            The pin message ID if found, None otherwise
        """
        if not self.pool:
            logger.error(f"Cannot get pin message ID for user {user_id}: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                return await conn.fetchval('''
                    SELECT pin_message_id FROM user_state 
                    WHERE user_id = $1
                ''', user_id)
        except Exception as e:
            logger.error(f"Error getting pin message ID for user {user_id}: {e}")
            return None

    async def migrate_tables(self):
        """Migrate database tables to new structure."""
        async with self.pool.acquire() as conn:
            # Проверка существования колонки file_content
            file_content_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'messages' AND column_name = 'file_content'
                )
            ''')
            
            # Если колонки file_content нет, добавляем её и другие колонки для медиа
            if not file_content_exists:
                logger.info("Migrating messages table to support media file storage in database")
                async with conn.transaction():
                    # Добавляем колонку file_content
                    await conn.execute('''
                        ALTER TABLE messages 
                        ADD COLUMN file_content BYTEA
                    ''')
                    
                    # Добавляем колонку file_name
                    await conn.execute('''
                        ALTER TABLE messages 
                        ADD COLUMN file_name TEXT
                    ''')
                    
                    # Добавляем колонку mime_type
                    await conn.execute('''
                        ALTER TABLE messages 
                        ADD COLUMN mime_type TEXT
                    ''')
                    
                    logger.info("Media file storage migration completed successfully")
            
            # Проверка существования остальных нужных колонок
            # Проверка колонки message_type
            message_type_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'messages' AND column_name = 'message_type'
                )
            ''')
            
            # Если колонки message_type нет, добавляем её
            if not message_type_exists:
                logger.info("Migrating messages table to support media messages")
                async with conn.transaction():
                    # Добавляем колонку message_type
                    await conn.execute('''
                        ALTER TABLE messages 
                        ADD COLUMN message_type VARCHAR(20) DEFAULT 'text'
                    ''')
                    
                    # Добавляем колонку file_id
                    await conn.execute('''
                        ALTER TABLE messages 
                        ADD COLUMN file_id TEXT
                    ''')
                    
                    logger.info("Message type migration completed successfully")
            
            # Проверка существования колонки local_file_path
            local_file_path_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'messages' AND column_name = 'local_file_path'
                )
            ''')
            
            # Если колонки local_file_path нет, добавляем её
            if not local_file_path_exists:
                logger.info("Migrating messages table to support local file storage")
                async with conn.transaction():
                    # Добавляем колонку local_file_path
                    await conn.execute('''
                        ALTER TABLE messages 
                        ADD COLUMN local_file_path TEXT
                    ''')
                    
                    logger.info("Local file path migration completed successfully")

            # Проверка существования колонки profile_setup_state в таблице user_state
            profile_setup_state_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'user_state' AND column_name = 'profile_setup_state'
                )
            ''')
            
            # Если колонки profile_setup_state нет, добавляем её и setup_step
            if not profile_setup_state_exists:
                logger.info("Migrating user_state table to add profile setup columns")
                async with conn.transaction():
                    try:
                        # Добавляем колонку profile_setup_state
                        await conn.execute('''
                            ALTER TABLE user_state 
                            ADD COLUMN IF NOT EXISTS profile_setup_state TEXT
                        ''')
                        
                        # Добавляем колонку setup_step
                        await conn.execute('''
                            ALTER TABLE user_state 
                            ADD COLUMN IF NOT EXISTS setup_step INT DEFAULT 0
                        ''')
                        
                        logger.info("Profile setup state columns added successfully")
                    except Exception as e:
                        logger.error(f"Error adding profile setup columns: {e}")
                        # Попытка обойти проблему с созданием колонок
                        try:
                            # Проверка, что такие колонки на самом деле существуют
                            setup_state_type = await conn.fetchval('''
                                SELECT data_type FROM information_schema.columns 
                                WHERE table_name = 'user_state' AND column_name = 'profile_setup_state'
                            ''')
                            
                            setup_step_type = await conn.fetchval('''
                                SELECT data_type FROM information_schema.columns 
                                WHERE table_name = 'user_state' AND column_name = 'setup_step'
                            ''')
                            
                            if setup_state_type and setup_step_type:
                                logger.info("Columns exist but might be in an inconsistent state. Continuing...")
                            else:
                                # Если колонки действительно не существуют, пробуем другой синтаксис
                                await conn.execute('ALTER TABLE user_state ADD COLUMN profile_setup_state TEXT')
                                await conn.execute('ALTER TABLE user_state ADD COLUMN setup_step INT DEFAULT 0')
                                logger.info("Added profile setup columns with alternative syntax")
                        except Exception as inner_e:
                            logger.error(f"Failed alternative attempt to add columns: {inner_e}")

            # Check for user_profile table
            profile_table_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'user_profile'
                )
            ''')
            
            if not profile_table_exists:
                logger.info("Migrating database to add profile tables")
                async with conn.transaction():
                    # Create user_profile table
                    await conn.execute('''
                        CREATE TABLE IF NOT EXISTS user_profile (
                            user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
                            gender TEXT,
                            looking_for TEXT,
                            age INT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')
                    
                    # Create interests table
                    await conn.execute('''
                        CREATE TABLE IF NOT EXISTS interests (
                            id SERIAL PRIMARY KEY,
                            name TEXT UNIQUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')
                    
                    # Create user_interests table
                    await conn.execute('''
                        CREATE TABLE IF NOT EXISTS user_interests (
                            user_id BIGINT REFERENCES users(user_id),
                            interest_id INT REFERENCES interests(id),
                            PRIMARY KEY (user_id, interest_id)
                        )
                    ''')
                    
                    # Add profile setup fields to user_state
                    await conn.execute('''
                        ALTER TABLE user_state 
                        ADD COLUMN IF NOT EXISTS profile_setup_state TEXT DEFAULT NULL
                    ''')
                    
                    await conn.execute('''
                        ALTER TABLE user_state 
                        ADD COLUMN IF NOT EXISTS setup_step INT DEFAULT 0
                    ''')
                    
                    # Insert default interests
                    default_interests = ["🗣️ Общение", "💋 Флирт", "🔥 Темки"]
                    for interest in default_interests:
                        await conn.execute('''
                            INSERT INTO interests (name) 
                            VALUES ($1) 
                            ON CONFLICT (name) DO NOTHING
                        ''', interest)
                        
                    # Get IDs of our new interests
                    общение_new_id = await conn.fetchval("SELECT id FROM interests WHERE name = '🗣️ Общение'")
                    флирт_id = await conn.fetchval("SELECT id FROM interests WHERE name = '💋 Флирт'")
                    темки_id = await conn.fetchval("SELECT id FROM interests WHERE name = '🔥 Темки'")
                    
                    # Check if old interests exist and get their IDs
                    общение_id = await conn.fetchval("SELECT id FROM interests WHERE name = 'общение'")
                    секс_id = await conn.fetchval("SELECT id FROM interests WHERE name = 'секс'")
                    
                    # Update references for old standard interests
                    if общение_id and общение_new_id:
                        # Get all users who have the old interest
                        rows = await conn.fetch('''
                            SELECT user_id FROM user_interests WHERE interest_id = $1
                        ''', общение_id)
                        
                        # For each user, add the new interest if they don't already have it
                        for row in rows:
                            user_id = row['user_id']
                            # Check if user already has this interest
                            has_interest = await conn.fetchval('''
                                SELECT EXISTS(
                                    SELECT 1 FROM user_interests 
                                    WHERE user_id = $1 AND interest_id = $2
                                )
                            ''', user_id, общение_new_id)
                            
                            # If they don't have it yet, add it
                            if not has_interest:
                                await conn.execute('''
                                    INSERT INTO user_interests (user_id, interest_id)
                                    VALUES ($1, $2)
                                ''', user_id, общение_new_id)
                    
                    if секс_id and флирт_id:
                        # Get all users who have the old interest
                        rows = await conn.fetch('''
                            SELECT user_id FROM user_interests WHERE interest_id = $1
                        ''', секс_id)
                        
                        # For each user, add the new interest if they don't already have it
                        for row in rows:
                            user_id = row['user_id']
                            # Check if user already has this interest
                            has_interest = await conn.fetchval('''
                                SELECT EXISTS(
                                    SELECT 1 FROM user_interests 
                                    WHERE user_id = $1 AND interest_id = $2
                                )
                            ''', user_id, флирт_id)
                            
                            # If they don't have it yet, add it
                            if not has_interest:
                                await conn.execute('''
                                    INSERT INTO user_interests (user_id, interest_id)
                                    VALUES ($1, $2)
                                ''', user_id, флирт_id)
                    
                    # Migrate any other interests to "🔥 Темки" if they're not already in our new list
                    if темки_id:
                        # First get all user IDs with interests that need migration
                        rows = await conn.fetch('''
                            SELECT DISTINCT ui.user_id 
                            FROM user_interests ui
                            JOIN interests i ON ui.interest_id = i.id
                            WHERE i.name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки', 'общение', 'секс')
                        ''')
                        
                        # For each user, add the "🔥 Темки" interest if they don't already have it
                        for row in rows:
                            user_id = row['user_id']
                            # Check if user already has this interest
                            has_interest = await conn.fetchval('''
                                SELECT EXISTS(
                                    SELECT 1 FROM user_interests 
                                    WHERE user_id = $1 AND interest_id = $2
                                )
                            ''', user_id, темки_id)
                            
                            # If they don't have it yet, add it
                            if not has_interest:
                                await conn.execute('''
                                    INSERT INTO user_interests (user_id, interest_id)
                                    VALUES ($1, $2)
                                ''', user_id, темки_id)
                        
                        # Delete user_interests entries for interests that are not in our new list
                        await conn.execute('''
                            DELETE FROM user_interests 
                            WHERE interest_id IN (
                                SELECT id FROM interests 
                                WHERE name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки')
                            )
                        ''')
                    
                    # Now delete the old interests that we've migrated
                    if общение_id:
                        await conn.execute("DELETE FROM interests WHERE id = $1", общение_id)
                    if секс_id:
                        await conn.execute("DELETE FROM interests WHERE id = $1", секс_id)
                    
                    # Safely delete interests that are not in our new list and not referenced by any users
                    await conn.execute('''
                        DELETE FROM interests 
                        WHERE name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки')
                        AND id NOT IN (SELECT DISTINCT interest_id FROM user_interests)
                    ''')
                    
                    logger.info("Profile tables migration completed successfully")

            # Check if user_ratings table exists
            ratings_table_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'user_ratings'
                )
            ''')
            
            if not ratings_table_exists:
                logger.info("Creating user_ratings table")
                async with conn.transaction():
                    # Create user_ratings table
                    await conn.execute('''
                        CREATE TABLE IF NOT EXISTS user_ratings (
                            id SERIAL PRIMARY KEY,
                            chat_id INTEGER REFERENCES ended_chats(chat_id),
                            rater_id BIGINT REFERENCES users(user_id),
                            rated_user_id BIGINT REFERENCES users(user_id),
                            rating_type VARCHAR(20), -- kiss, money, clown
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    ''')
                    
                    logger.info("User ratings table created successfully")

    async def get_message_media(self, message_id: int):
        """Get media information for a message."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT message_type, file_id, file_name, mime_type, local_file_path, file_content
                FROM messages 
                WHERE id = $1
            ''', message_id)
            return row
    
    async def get_chat_media(self, chat_id: int):
        """Get all media messages from a chat."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT id, sender_id, message_type, file_id, file_name, mime_type, local_file_path, sent_at 
                FROM messages 
                WHERE chat_id = $1 AND message_type != 'text'
                ORDER BY sent_at DESC
            ''', chat_id)
            return rows

    # Profile related methods
    async def get_profile_setup_state(self, user_id: int) -> Tuple[Optional[str], int]:
        """
        Get user's profile setup state and step.
        
        Args:
            user_id: The user ID
            
        Returns:
            Tuple with state name and step number
        """
        if not self.pool:
            logger.error(f"Cannot get profile setup state: database connection not established")
            return None, 0
            
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT profile_setup_state, setup_step
                    FROM user_state
                    WHERE user_id = $1
                ''', user_id)
                
                if row:
                    return row['profile_setup_state'], row['setup_step']
                return None, 0
        except Exception as e:
            logger.error(f"Error getting profile setup state for user {user_id}: {e}")
            return None, 0
    
    async def update_profile_setup_state(self, user_id: int, state: Optional[str], step: int) -> bool:
        """
        Update user's profile setup state and step.
        
        Args:
            user_id: The user ID
            state: Current setup state (or None if complete)
            step: Current step number
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot update profile setup state: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO user_state (user_id, profile_setup_state, setup_step)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (user_id) 
                    DO UPDATE SET 
                        profile_setup_state = EXCLUDED.profile_setup_state,
                        setup_step = EXCLUDED.setup_step,
                        last_updated = CURRENT_TIMESTAMP
                ''', user_id, state, step)
                
                logger.info(f"Updated profile setup state for user {user_id} to {state}, step {step}")
                return True
        except Exception as e:
            logger.error(f"Error updating profile setup state for user {user_id}: {e}")
            return False
    
    async def save_user_profile(
        self, 
        user_id: int, 
        gender: Optional[str] = None, 
        looking_for: Optional[str] = None, 
        age: Optional[int] = None
    ) -> bool:
        """
        Save or update user profile information.
        
        Args:
            user_id: The user ID
            gender: User's gender
            looking_for: Gender preference
            age: User's age
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot save user profile: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # First check if there's an existing profile
                existing = await conn.fetchval('''
                    SELECT COUNT(*) FROM user_profile WHERE user_id = $1
                ''', user_id)
                
                if existing:
                    # Update only the provided fields
                    update_parts = []
                    params = [user_id]
                    param_index = 2
                    
                    if gender is not None:
                        update_parts.append(f"gender = ${param_index}")
                        params.append(gender)
                        param_index += 1
                        
                    if looking_for is not None:
                        update_parts.append(f"looking_for = ${param_index}")
                        params.append(looking_for)
                        param_index += 1
                        
                    if age is not None:
                        update_parts.append(f"age = ${param_index}")
                        params.append(age)
                        param_index += 1
                    
                    if update_parts:
                        update_parts.append("updated_at = CURRENT_TIMESTAMP")
                        query = f'''
                            UPDATE user_profile 
                            SET {", ".join(update_parts)}
                            WHERE user_id = $1
                        '''
                        await conn.execute(query, *params)
                else:
                    # Insert new profile
                    await conn.execute('''
                        INSERT INTO user_profile (user_id, gender, looking_for, age)
                        VALUES ($1, $2, $3, $4)
                    ''', user_id, gender, looking_for, age)
                
                logger.info(f"Saved profile for user {user_id}")
                return True
        except Exception as e:
            logger.error(f"Error saving profile for user {user_id}: {e}")
            return False
    
    async def get_user_profile(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get user's profile information.
        
        Args:
            user_id: The user ID
            
        Returns:
            Dictionary with profile data or None if not found
        """
        if not self.pool:
            logger.error(f"Cannot get user profile: database connection not established")
            return None
            
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT gender, looking_for, age
                    FROM user_profile
                    WHERE user_id = $1
                ''', user_id)
                
                if row:
                    return dict(row)
                return None
        except Exception as e:
            logger.error(f"Error getting profile for user {user_id}: {e}")
            return None
    
    async def save_user_interest(self, user_id: int, interest_name: str) -> bool:
        """
        Add an interest to user's profile.
        
        Args:
            user_id: The user ID
            interest_name: Name of the interest
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot save user interest: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    # Get or create interest
                    interest_id = await conn.fetchval('''
                        SELECT id FROM interests WHERE name = $1
                    ''', interest_name)
                    
                    if not interest_id:
                        interest_id = await conn.fetchval('''
                            INSERT INTO interests (name) VALUES ($1) RETURNING id
                        ''', interest_name)
                    
                    # Add to user_interests
                    await conn.execute('''
                        INSERT INTO user_interests (user_id, interest_id)
                        VALUES ($1, $2)
                        ON CONFLICT (user_id, interest_id) DO NOTHING
                    ''', user_id, interest_id)
                    
                    logger.info(f"Added interest '{interest_name}' for user {user_id}")
                    return True
        except Exception as e:
            logger.error(f"Error saving interest for user {user_id}: {e}")
            return False
    
    async def remove_user_interest(self, user_id: int, interest_name: str) -> bool:
        """
        Remove an interest from user's profile.
        
        Args:
            user_id: The user ID
            interest_name: Name of the interest
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot remove user interest: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # Get interest ID
                interest_id = await conn.fetchval('''
                    SELECT id FROM interests WHERE name = $1
                ''', interest_name)
                
                if interest_id:
                    # Remove from user_interests
                    await conn.execute('''
                        DELETE FROM user_interests
                        WHERE user_id = $1 AND interest_id = $2
                    ''', user_id, interest_id)
                    
                    logger.info(f"Removed interest '{interest_name}' from user {user_id}")
                
                return True
        except Exception as e:
            logger.error(f"Error removing interest for user {user_id}: {e}")
            return False
    
    async def get_user_interests(self, user_id: int) -> List[str]:
        """
        Get all interests for a user.
        
        Args:
            user_id: The user ID
            
        Returns:
            List of interest names
        """
        if not self.pool:
            logger.error(f"Cannot get user interests: database connection not established")
            return []
            
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT i.name
                    FROM user_interests ui
                    JOIN interests i ON ui.interest_id = i.id
                    WHERE ui.user_id = $1
                    ORDER BY i.name
                ''', user_id)
                
                return [row['name'] for row in rows]
        except Exception as e:
            logger.error(f"Error getting interests for user {user_id}: {e}")
            return []
    
    async def get_all_interests(self) -> List[Dict[str, Any]]:
        """
        Get all available interests.
        
        Returns:
            List of dictionaries with interest data
        """
        if not self.pool:
            logger.error("Cannot get all interests: database connection not established")
            return []
            
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT id, name
                    FROM interests
                    ORDER BY name
                ''')
                
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting all interests: {e}")
            return []
    
    async def clear_user_interests(self, user_id: int) -> bool:
        """
        Remove all interests for a user.
        
        Args:
            user_id: The user ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot clear user interests: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    DELETE FROM user_interests
                    WHERE user_id = $1
                ''', user_id)
                
                logger.info(f"Cleared all interests for user {user_id}")
                return True
        except Exception as e:
            logger.error(f"Error clearing interests for user {user_id}: {e}")
            return False
    
    async def has_completed_profile(self, user_id: int) -> bool:
        """
        Check if user has completed their profile.
        
        Args:
            user_id: The user ID
            
        Returns:
            True if profile is complete, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot check profile completion: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # Check if profile exists with required fields
                profile_exists = await conn.fetchval('''
                    SELECT COUNT(*) FROM user_profile
                    WHERE user_id = $1 AND gender IS NOT NULL
                ''', user_id)
                
                if not profile_exists:
                    return False
                
                # Check if user has at least one interest
                has_interests = await conn.fetchval('''
                    SELECT COUNT(*) FROM user_interests
                    WHERE user_id = $1
                ''', user_id)
                
                return has_interests > 0
        except Exception as e:
            logger.error(f"Error checking profile completion for user {user_id}: {e}")
            return False

    async def is_new_user(self, user_id: int) -> bool:
        """
        Check if a user has never completed profile setup.
        
        Args:
            user_id: The user ID to check
            
        Returns:
            True if the user has never completed profile setup, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot check if user {user_id} is new: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # Check if user has any profile data
                has_profile = await conn.fetchval('''
                    SELECT EXISTS (
                        SELECT 1 FROM user_profile 
                        WHERE user_id = $1
                    )
                ''', user_id)
                
                return not has_profile
        except Exception as e:
            logger.error(f"Error checking if user {user_id} is new: {e}")
            return False

    async def migrate_interests_for_user(self, conn, user_id):
        # Get the IDs of our new interests
        общение_id = await conn.fetchval("SELECT id FROM interests WHERE name = '🗣️ Общение'")
        флирт_id = await conn.fetchval("SELECT id FROM interests WHERE name = '💋 Флирт'")
        темки_id = await conn.fetchval("SELECT id FROM interests WHERE name = '🔥 Темки'")
        
        # Check if user has the old 'общение' interest
        old_общение_id = await conn.fetchval("SELECT id FROM interests WHERE name = 'общение'")
        if old_общение_id:
            has_old_общение = await conn.fetchval('''
                SELECT EXISTS(
                    SELECT 1 FROM user_interests 
                    WHERE user_id = $1 AND interest_id = $2
                )
            ''', user_id, old_общение_id)
            
            # If they have the old interest, add the new one if they don't already have it
            if has_old_общение and общение_id:
                has_new_общение = await conn.fetchval('''
                    SELECT EXISTS(
                        SELECT 1 FROM user_interests 
                        WHERE user_id = $1 AND interest_id = $2
                    )
                ''', user_id, общение_id)
                
                if not has_new_общение:
                    await conn.execute('''
                        INSERT INTO user_interests (user_id, interest_id)
                        VALUES ($1, $2)
                    ''', user_id, общение_id)
        
        # Check if user has the old 'секс' interest
        old_секс_id = await conn.fetchval("SELECT id FROM interests WHERE name = 'секс'")
        if old_секс_id:
            has_old_секс = await conn.fetchval('''
                SELECT EXISTS(
                    SELECT 1 FROM user_interests 
                    WHERE user_id = $1 AND interest_id = $2
                )
            ''', user_id, old_секс_id)
            
            # If they have the old interest, add the new one if they don't already have it
            if has_old_секс and флирт_id:
                has_new_флирт = await conn.fetchval('''
                    SELECT EXISTS(
                        SELECT 1 FROM user_interests 
                        WHERE user_id = $1 AND interest_id = $2
                    )
                ''', user_id, флирт_id)
                
                if not has_new_флирт:
                    await conn.execute('''
                        INSERT INTO user_interests (user_id, interest_id)
                        VALUES ($1, $2)
                    ''', user_id, флирт_id)
        
        # Check if user has any other interests that need to be migrated to "🔥 Темки"
        if темки_id:
            has_other_interests = await conn.fetchval('''
                SELECT EXISTS(
                    SELECT 1 FROM user_interests ui
                    JOIN interests i ON ui.interest_id = i.id
                    WHERE ui.user_id = $1 
                    AND i.name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки', 'общение', 'секс')
                )
            ''', user_id)
            
            # If they have other interests, add "🔥 Темки" if they don't already have it
            if has_other_interests:
                has_темки = await conn.fetchval('''
                    SELECT EXISTS(
                        SELECT 1 FROM user_interests 
                        WHERE user_id = $1 AND interest_id = $2
                    )
                ''', user_id, темки_id)
                
                if not has_темки:
                    await conn.execute('''
                        INSERT INTO user_interests (user_id, interest_id)
                        VALUES ($1, $2)
                    ''', user_id, темки_id)
        
        # Delete all old interests for this user
        await conn.execute('''
            DELETE FROM user_interests 
            WHERE user_id = $1 AND interest_id IN (
                SELECT id FROM interests 
                WHERE name NOT IN ('🗣️ Общение', '💋 Флирт', '🔥 Темки')
            )
        ''', user_id)

    async def save_user_rating(self, chat_id: int, rater_id: int, rated_user_id: int, rating_type: str) -> bool:
        """
        Save a user rating.
        
        Args:
            chat_id: The ID of the chat where the rating occurred
            rater_id: User ID of the rater
            rated_user_id: User ID of the user being rated
            rating_type: Type of rating (kiss, money, clown)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.pool:
            logger.error(f"Cannot save user rating: database connection not established")
            return False
            
        try:
            async with self.pool.acquire() as conn:
                # Check if a rating already exists for this rater/rated combination
                existing_rating = await conn.fetchval('''
                    SELECT id FROM user_ratings 
                    WHERE chat_id = $1 AND rater_id = $2 AND rated_user_id = $3
                ''', chat_id, rater_id, rated_user_id)
                
                if existing_rating:
                    # Update existing rating
                    await conn.execute('''
                        UPDATE user_ratings 
                        SET rating_type = $4, created_at = CURRENT_TIMESTAMP
                        WHERE id = $1
                    ''', existing_rating, rating_type)
                else:
                    # Insert new rating
                    await conn.execute('''
                        INSERT INTO user_ratings (chat_id, rater_id, rated_user_id, rating_type)
                        VALUES ($1, $2, $3, $4)
                    ''', chat_id, rater_id, rated_user_id, rating_type)
                
                logger.info(f"Saved rating from user {rater_id} for user {rated_user_id} in chat {chat_id}")
                return True
        except Exception as e:
            logger.error(f"Error saving rating for user {rated_user_id}: {e}")
            return False
    
    async def get_user_ratings(self, user_id: int) -> Dict[str, int]:
        """
        Get ratings statistics for a user.
        
        Args:
            user_id: The user ID to get ratings for
            
        Returns:
            Dictionary with count of each rating type
        """
        if not self.pool:
            logger.error(f"Cannot get user ratings: database connection not established")
            return {"kiss": 0, "money": 0, "clown": 0}
            
        try:
            async with self.pool.acquire() as conn:
                ratings = {"kiss": 0, "money": 0, "clown": 0}
                
                rows = await conn.fetch('''
                    SELECT rating_type, COUNT(*) as count
                    FROM user_ratings
                    WHERE rated_user_id = $1
                    GROUP BY rating_type
                ''', user_id)
                
                for row in rows:
                    ratings[row['rating_type']] = row['count']
                
                return ratings
        except Exception as e:
            logger.error(f"Error getting ratings for user {user_id}: {e}")
            return {"kiss": 0, "money": 0, "clown": 0}

    async def get_all_active_chats(self) -> List[Tuple[int, int]]:
        """
        Get all active chats from the database.
        
        Returns:
            List of tuples containing (user_id_1, user_id_2) for each active chat
        """
        if not self.pool:
            logger.error("Cannot get active chats: database connection not established")
            return []
            
        try:
            async with self.pool.acquire() as conn:
                # Get all active chats
                rows = await conn.fetch('''
                    SELECT user_id_1, user_id_2 
                    FROM active_chats
                ''')
                
                # Convert rows to list of tuples
                return [(row['user_id_1'], row['user_id_2']) for row in rows]
                
        except Exception as e:
            logger.error(f"Error getting active chats: {e}")
            return []

    async def init_db():
        """Initialize database tables."""
        async with aiosqlite.connect(DB_PATH) as db:
            # Create users table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    gender TEXT,
                    looking_for TEXT,
                    age INTEGER,
                    interests TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create chats table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user1_id INTEGER,
                    user2_id INTEGER,
                    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    end_time TIMESTAMP,
                    status TEXT DEFAULT 'active'
                )
            ''')
            
            # Create messages table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    sender_id INTEGER,
                    message_type TEXT,
                    content TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES chats (chat_id)
                )
            ''')
            
            # Create media table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS media (
                    media_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    sender_id INTEGER,
                    message_id INTEGER,
                    file_id TEXT,
                    file_type TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (chat_id) REFERENCES chats (chat_id)
                )
            ''')
            
            await db.commit()

# Create global database instance
db = Database() 