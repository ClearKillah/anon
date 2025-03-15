import os
from log import logger
from db import db
from state import state

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