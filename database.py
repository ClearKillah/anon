import asyncpg
import logging
from typing import Optional, List, Tuple
from datetime import datetime
import os

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.pool = None

    async def connect(self, dsn: str):
        """Connect to the database."""
        try:
            self.pool = await asyncpg.create_pool(dsn)
            # await self.drop_tables()  # Drop existing tables
            await self.create_tables()  # Create tables with new schema
            await self.migrate_tables()  # Выполняем миграцию, если необходимо
            logger.info("Successfully connected to the database")
        except Exception as e:
            logger.error(f"Error connecting to the database: {e}")
            raise

    async def disconnect(self):
        """Disconnect from the database."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Successfully disconnected from the database")

    async def create_tables(self):
        """Create database tables if they don't exist."""
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

                # Create messages table with updated foreign key
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS messages (
                        id SERIAL PRIMARY KEY,
                        chat_id INTEGER,
                        sender_id BIGINT REFERENCES users(user_id),
                        content TEXT,
                        message_type VARCHAR(20) DEFAULT 'text', -- тип: text, photo, video, voice, sticker, video_note
                        file_id TEXT, -- для хранения ID файла в Telegram
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
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')

    async def drop_tables(self):
        """Drop all existing tables."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute('''
                    DROP TABLE IF EXISTS messages CASCADE;
                    DROP TABLE IF EXISTS active_chats CASCADE;
                    DROP TABLE IF EXISTS ended_chats CASCADE;
                    DROP TABLE IF EXISTS user_state CASCADE;
                    DROP TABLE IF EXISTS users CASCADE;
                ''')

    # User operations
    async def add_user(self, user_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str]):
        """Add a new user or update existing one."""
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

    # Chat operations
    async def create_chat(self, user_id_1: int, user_id_2: int) -> int:
        """Create a new chat between two users."""
        async with self.pool.acquire() as conn:
            chat_id = await conn.fetchval('''
                INSERT INTO active_chats (user_id_1, user_id_2)
                VALUES ($1, $2)
                RETURNING chat_id
            ''', user_id_1, user_id_2)
            return chat_id

    async def get_active_chat(self, user_id: int) -> Optional[Tuple[int, int]]:
        """Get active chat for user. Returns (chat_id, partner_id) if exists."""
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

    async def end_chat(self, chat_id: int):
        """End a chat by moving it to ended_chats and removing from active_chats."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # First, create ended_chats table if it doesn't exist
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS ended_chats (
                        chat_id INTEGER PRIMARY KEY,
                        user_id_1 BIGINT REFERENCES users(user_id),
                        user_id_2 BIGINT REFERENCES users(user_id),
                        started_at TIMESTAMP,
                        ended_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Move chat to ended_chats
                await conn.execute('''
                    INSERT INTO ended_chats (chat_id, user_id_1, user_id_2, started_at)
                    SELECT chat_id, user_id_1, user_id_2, started_at
                    FROM active_chats
                    WHERE chat_id = $1
                ''', chat_id)
                
                # Then remove from active_chats
                await conn.execute('DELETE FROM active_chats WHERE chat_id = $1', chat_id)

    async def remove_chat(self, chat_id: int):
        """Remove a chat and all its messages."""
        async with self.pool.acquire() as conn:
            # Start transaction
            async with conn.transaction():
                # Delete messages first (due to foreign key constraint)
                await conn.execute('DELETE FROM messages WHERE chat_id = $1', chat_id)
                # Then delete the chat
                await conn.execute('DELETE FROM active_chats WHERE chat_id = $1', chat_id)

    async def clear_chat_messages(self, chat_id: int):
        """Clear all messages from a chat and delete local media files."""
        async with self.pool.acquire() as conn:
            # Получаем пути к локальным файлам перед удалением сообщений
            media_files = await conn.fetch('''
                SELECT local_file_path FROM messages 
                WHERE chat_id = $1 AND local_file_path IS NOT NULL
            ''', chat_id)
            
            # Удаляем локальные файлы
            for row in media_files:
                file_path = row['local_file_path']
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logging.info(f"Deleted local file: {file_path}")
                    except Exception as e:
                        logging.error(f"Error deleting local file {file_path}: {e}")
            
            # Удаляем сообщения из базы данных
            await conn.execute('DELETE FROM messages WHERE chat_id = $1', chat_id)

    # Message operations
    async def add_message(self, chat_id: int, sender_id: int, content: str = None, 
                         message_type: str = 'text', file_id: str = None, local_file_path: str = None):
        """Add a new message to the database."""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO messages (chat_id, sender_id, content, message_type, file_id, local_file_path)
                VALUES ($1, $2, $3, $4, $5, $6)
            ''', chat_id, sender_id, content, message_type, file_id, local_file_path)

    # User state operations
    async def set_user_searching(self, user_id: int, is_searching: bool):
        """Set user's searching status."""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_state (user_id, is_searching)
                VALUES ($1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET is_searching = EXCLUDED.is_searching,
                             last_updated = CURRENT_TIMESTAMP
            ''', user_id, is_searching)

    async def get_searching_users(self) -> List[int]:
        """Get list of users who are currently searching."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT user_id
                FROM user_state
                WHERE is_searching = TRUE
            ''')
            return [row['user_id'] for row in rows]

    async def update_main_message_id(self, user_id: int, message_id: int):
        """Update user's main message ID."""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_state (user_id, main_message_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET main_message_id = EXCLUDED.main_message_id,
                             last_updated = CURRENT_TIMESTAMP
            ''', user_id, message_id)

    async def get_main_message_id(self, user_id: int) -> Optional[int]:
        """Get user's main message ID."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval('''
                SELECT main_message_id
                FROM user_state
                WHERE user_id = $1
            ''', user_id)

    async def update_pin_message_id(self, user_id: int, message_id: int):
        """Update user's pin message ID."""
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO user_state (user_id, pin_message_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET pin_message_id = EXCLUDED.pin_message_id,
                             last_updated = CURRENT_TIMESTAMP
            ''', user_id, message_id)

    async def get_pin_message_id(self, user_id: int) -> Optional[int]:
        """Get user's pin message ID."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval('''
                SELECT pin_message_id
                FROM user_state
                WHERE user_id = $1
            ''', user_id)

    async def migrate_tables(self):
        """Migrate database tables to new structure."""
        async with self.pool.acquire() as conn:
            # Проверка существования колонок в таблице messages
            column_exists = await conn.fetchval('''
                SELECT EXISTS (
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'messages' AND column_name = 'message_type'
                )
            ''')
            
            # Если колонки message_type нет, добавляем новые колонки
            if not column_exists:
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
                    
                    logger.info("Migration completed successfully")
            
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

    async def get_message_media(self, message_id: int):
        """Get media information for a message."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT message_type, file_id, local_file_path 
                FROM messages 
                WHERE id = $1
            ''', message_id)
            return row
    
    async def get_chat_media(self, chat_id: int):
        """Get all media messages from a chat."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT id, sender_id, message_type, file_id, local_file_path, sent_at 
                FROM messages 
                WHERE chat_id = $1 AND message_type != 'text'
                ORDER BY sent_at DESC
            ''', chat_id)
            return rows

# Create global database instance
db = Database() 