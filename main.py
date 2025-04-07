import os
import logging
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union, Any
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeChat
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler
)
import asyncio
from telegram.constants import ParseMode
import time

# Try to import psutil for better uptime info, but it's optional
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GROUP_ID = os.getenv("GROUP_ID")  # Will be set up if not provided
PORT = int(os.getenv("PORT", "8443"))
RAILWAY_STATIC_URL = os.getenv("RAILWAY_STATIC_URL")

# Session management settings
SESSION_TIMEOUT = timedelta(minutes=int(os.getenv("SESSION_TIMEOUT", "15")))  # Normal session timeout (15 min default)
EXTENDED_SESSION_TIMEOUT = timedelta(minutes=int(os.getenv("EXTENDED_SESSION_TIMEOUT", "1440")))  # Extended session timeout (24 hours default)

# Data storage
DATA_FILE = "bot_data.json"
BACKUP_DIR = "backups"
os.makedirs(BACKUP_DIR, exist_ok=True)

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
AUTHENTICATE = 0
AWAITING_AUTH_REPLY = 1
SESSION_SELECTION = 2

# Data structures
class BotData:
    def __init__(self):
        self.authenticated_users: Dict[str, Dict[str, Any]] = {}
        self.blocked_users: List[int] = []
        self.security_questions: Dict[str, str] = {
            "What's your secret phrase?": "277353"  # Set to the provided secret answer
        }
        self.last_backup = datetime.now()
        self.start_time = datetime.now()  # Initialize start time for uptime calculation
        
    def save_to_file(self):
        """Save bot data to file"""
        try:
            # Create a backup if needed (every 24 hours)
            now = datetime.now()
            if (now - self.last_backup).total_seconds() > 86400:  # 24 hours
                self.create_backup()
                self.last_backup = now
                
            with open(DATA_FILE, 'w') as f:
                json.dump({
                    'authenticated_users': self.authenticated_users,
                    'blocked_users': self.blocked_users,
                    'security_questions': self.security_questions
                }, f, indent=2)
            logger.info("Bot data saved successfully")
        except Exception as e:
            logger.error(f"Error saving bot data: {e}")
    
    def create_backup(self):
        """Create a backup of the data file"""
        try:
            if not os.path.exists(DATA_FILE):
                return
                
            # Create backup file with timestamp
            backup_file = f"{BACKUP_DIR}/bot_data_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
            # Copy the current data to backup
            with open(DATA_FILE, 'r') as src, open(backup_file, 'w') as dst:
                dst.write(src.read())
                
            # Keep only last 5 backups
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("bot_data_backup_")])
            if len(backups) > 5:
                for old_backup in backups[:-5]:
                    os.remove(os.path.join(BACKUP_DIR, old_backup))
                    
            logger.info(f"Created backup: {backup_file}")
        except Exception as e:
            logger.error(f"Error creating backup: {e}")
    
    def load_from_file(self):
        """Load bot data from file"""
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
                    self.authenticated_users = data.get('authenticated_users', {})
                    self.blocked_users = data.get('blocked_users', [])
                    self.security_questions = data.get('security_questions', 
                                                      {"What's your secret phrase?": "277353"})
                logger.info("Bot data loaded successfully")
            else:
                logger.info("No existing data file found, using defaults")
        except Exception as e:
            logger.error(f"Error loading bot data: {e}")
            
    def is_session_valid(self, user_id: int) -> bool:
        """Check if a user's session is valid and not expired"""
        user_id_str = str(user_id)
        
        # Admin always has a valid session
        if user_id == ADMIN_ID:
            return True
            
        if user_id_str not in self.authenticated_users:
            return False
            
        user_data = self.authenticated_users[user_id_str]
        last_activity = user_data.get("last_activity", "")
        session_type = user_data.get("session_type", "standard")
        
        if not last_activity:
            return False
            
        try:
            # Calculate expiration based on session type
            last_active = datetime.fromisoformat(last_activity)
            current_time = datetime.now()
            
            # Get the appropriate timeout based on session type
            if session_type == "extended":
                timeout_minutes = EXTENDED_SESSION_TIMEOUT.total_seconds() / 60
            else:
                timeout_minutes = SESSION_TIMEOUT.total_seconds() / 60
                
            # Check if expired
            time_diff = (current_time - last_active).total_seconds() / 60
            if time_diff > timeout_minutes:
                logger.info(f"Session expired for user {user_id} after {time_diff:.1f} minutes (timeout: {timeout_minutes})")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Error checking session expiration: {e}")
            return False

    def update_activity(self, user_id: int):
        """Update the last activity timestamp for a user"""
        user_id_str = str(user_id)
        if user_id_str in self.authenticated_users:
            self.authenticated_users[user_id_str]["last_activity"] = datetime.now().isoformat()
            self.save_to_file()

# Initialize bot data
bot_data = BotData()
bot_data.load_from_file()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Send a message when the command /start is issued."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # If it's admin, don't need to authenticate
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            f"Hello Admin! You are already authenticated.\n"
            f"Your status is always active."
        )
        return ConversationHandler.END
    
    # Check if user is already authenticated with a valid session
    if bot_data.is_session_valid(user_id):
        session_info = bot_data.authenticated_users[str(user_id)]
        session_type = session_info.get("session_type", "standard")
        timeout_seconds = session_info.get("session_timeout", SESSION_TIMEOUT.total_seconds())
        minutes_remaining = int(timeout_seconds / 60)
        
        await update.message.reply_text(
            f"Hello {user_name}!\n\n"
            f"You are already authenticated with a {session_type} session.\n"
            f"Session timeout: {minutes_remaining} minutes of inactivity."
        )
        return ConversationHandler.END
    
    # User needs to authenticate
    # Choose a random security question
    questions = list(bot_data.security_questions.keys())
    question = questions[0]  # Since we only have one question for simplicity
    
    # Send the question and store its message_id for validation in authenticate handler
    auth_message = await update.message.reply_text(
        f"Please answer the following security question to authenticate:\n\n"
        f"{question}"
    )
    
    # Store the message ID for the authentication handler to verify replies
    context.user_data['auth_message_id'] = auth_message.message_id
    
    return AWAITING_AUTH_REPLY

async def authenticate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle authentication attempts."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    message_text = update.message.text
    
    # Check if this is a reply to the authentication question
    if not update.message.reply_to_message or update.message.reply_to_message.message_id != context.user_data.get('auth_message_id'):
        # Not a reply to the authentication question
        try:
            await update.message.delete()
        except Exception as e:
            logger.warning(f"Could not delete non-reply authentication message: {e}")
        
        error_msg = await update.message.reply_text(
            "Please reply directly to the authentication question."
        )
        
        # Schedule deletion of error message
        asyncio.create_task(delete_message_after_delay(error_msg, 5))
        return AWAITING_AUTH_REPLY
    
    # Check if the answer matches
    question = list(bot_data.security_questions.keys())[0]
    correct_answer = bot_data.security_questions[question]
    
    # Delete the authentication message for security
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete authentication message: {e}")
    
    # Try to delete the question message too
    try:
        await update.message.reply_to_message.delete()
    except Exception as e:
        logger.warning(f"Could not delete authentication question: {e}")
    
    if message_text.lower() == correct_answer.lower():
        # Authentication successful - ask for session type
        keyboard = [
            [
                InlineKeyboardButton("Standard Session (15 min)", callback_data="session_standard"),
                InlineKeyboardButton("Extended Session (24h)", callback_data="session_extended")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "Authentication successful! Please choose your session type:",
            reply_markup=reply_markup
        )
        
        return SESSION_SELECTION
    else:
        error_msg = await update.message.reply_text(
            "Authentication failed. Please try again with /start"
        )
        
        # Schedule deletion of error message
        asyncio.create_task(delete_message_after_delay(error_msg, 5))
        return ConversationHandler.END

async def session_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle session type selection."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    query = update.callback_query
    await query.answer()
    
    # Check which session type was selected
    session_type = "standard"
    session_timeout = SESSION_TIMEOUT
    if "extended" in query.data:
        session_type = "extended"
        session_timeout = EXTENDED_SESSION_TIMEOUT
    
    # Calculate expiration time
    current_time = datetime.now()
    expiry_time = current_time + session_timeout
    session_minutes = int(session_timeout.total_seconds() / 60)
    
    # Authenticate the user with the appropriate session timeout
    bot_data.authenticated_users[str(user_id)] = {
        "name": user_name,
        "authenticated_at": current_time.isoformat(),
        "last_activity": current_time.isoformat(),
        "session_type": session_type,
        "session_timeout": session_timeout.total_seconds(),
        "expiry_time": expiry_time.isoformat()
    }
    bot_data.save_to_file()
    
    # Send detailed authentication notification ONLY to backup group with action buttons
    if GROUP_ID:
        try:
            auth_message = (
                f"🔐 *New Authentication*\n"
                f"👤 User: {user_name}\n"
                f"🆔 ID: `{user_id}`\n"
                f"⏰ Time: {current_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"📝 Session: {session_type.capitalize()} ({session_minutes} min)"
            )
            
            # Create action buttons for the admin to manage this user
            keyboard = [
                [InlineKeyboardButton("Terminate Session", callback_data=f"terminate_{user_id}")],
                [InlineKeyboardButton("Block User", callback_data=f"block_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=auth_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send authentication notification to group: {e}")
    
    # Remove the keyboard and update the message
    await query.edit_message_text(
        text=f"Authentication successful! You have a {session_type} session."
    )
    
    # Create a countdown timer with buttons
    try:
        # Calculate time format for display
        remaining_seconds = session_timeout.total_seconds()
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            time_str = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
        else:
            time_str = f"{int(minutes):02d}:{int(seconds):02d}"
        
        # Create keyboard with timer button
        timer_keyboard = [
            [InlineKeyboardButton(f"⏱️ {time_str}", callback_data="timer_info")],
            [InlineKeyboardButton("Session Info", callback_data="session_info")]
        ]
        timer_markup = InlineKeyboardMarkup(timer_keyboard)
        
        # Create message with countdown
        timer_msg = await context.bot.send_message(
            chat_id=user_id,
            text=(
                f"*Session Active: {session_type.capitalize()}*\n\n"
                f"⏱️ Expires at: {expiry_time.strftime('%H:%M:%S')}\n"
                f"⚠️ Chat history will be cleared when session ends."
            ),
            reply_markup=timer_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Pin the timer message
        try:
            await context.bot.pin_chat_message(
                chat_id=user_id,
                message_id=timer_msg.message_id,
                disable_notification=True
            )
        except Exception as e:
            logger.warning(f"Could not pin timer message: {e}")
            
        # Schedule regular updates of the timer message and button
        # We'll use job queue for this
        try:
            # Store the timer message ID in context for later updates
            if not hasattr(context.user_data, 'timer_data'):
                context.user_data['timer_data'] = {}
                
            context.user_data['timer_data'][user_id] = {
                'message_id': timer_msg.message_id,
                'expiry_time': expiry_time.isoformat(),
                'session_type': session_type
            }
            
            # Start a job to update the timer regularly
            context.job_queue.run_repeating(
                update_timer_job,
                interval=30,  # Update every 30 seconds
                first=30,  # First update after 30 seconds
                data={
                    'user_id': user_id,
                    'message_id': timer_msg.message_id,
                    'expiry_time': expiry_time
                },
                name=f"timer_{user_id}"
            )
            
            logger.info(f"Scheduled timer updates for user {user_id}")
            
            # Schedule chat clearing for extended sessions
            if session_type == "extended":
                # Schedule a job to clear chat history after session expires
                context.job_queue.run_once(
                    clear_chat_job,
                    when=session_timeout.total_seconds() + 5,  # Add small buffer
                    data={'user_id': user_id},
                    name=f"clear_chat_{user_id}"
                )
                logger.info(f"Scheduled chat clear for user {user_id} in {session_timeout.total_seconds()} seconds")
        
        except Exception as e:
            logger.error(f"Failed to schedule timer updates: {e}")
        
    except Exception as e:
        logger.error(f"Failed to create session timer: {e}")
        
        # Send a simple fallback message if timer creation fails
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ You're now authenticated for a {session_type} session.\n"
                 f"Session will expire after {session_minutes} minutes of inactivity.",
            parse_mode=ParseMode.MARKDOWN
        )
    
    return ConversationHandler.END

async def update_timer_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update timer button in regular intervals."""
    job = context.job
    data = job.data
    
    user_id = data['user_id']
    message_id = data['message_id']
    expiry_time = data['expiry_time']
    
    try:
        # Check if user is still authenticated
        if not bot_data.is_session_valid(user_id):
            # Cancel the job if user is no longer authenticated
            job.schedule_removal()
            return
        
        # Get session type from user data
        user_data = bot_data.authenticated_users[str(user_id)]
        session_type = user_data.get('session_type', 'standard')
        
        # Calculate remaining time
        now = datetime.now()
        remaining = expiry_time - now
        
        if remaining.total_seconds() <= 0:
            # Session expired, update message
            await context.bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text="⏱️ *Session Expired*\n\nYour session has ended. Please authenticate again.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Cancel the job
            job.schedule_removal()
            return
        
        # Format remaining time for display
        hours, remainder = divmod(remaining.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            time_str = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
        else:
            time_str = f"{int(minutes):02d}:{int(seconds):02d}"
        
        # Update the timer button
        timer_keyboard = [
            [InlineKeyboardButton(f"⏱️ {time_str}", callback_data="timer_info")],
            [InlineKeyboardButton("Session Info", callback_data="session_info")]
        ]
        timer_markup = InlineKeyboardMarkup(timer_keyboard)
        
        # Update message with new time
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=message_id,
            text=(
                f"*Session Active: {session_type.capitalize()}*\n\n"
                f"⏱️ Expires at: {expiry_time.strftime('%H:%M:%S')}\n"
                f"⚠️ Chat history will be cleared when session ends."
            ),
            reply_markup=timer_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Adjust update frequency based on remaining time
        if remaining.total_seconds() <= 60:  # Last minute
            job.interval = 10  # Update every 10 seconds
        elif remaining.total_seconds() <= 300:  # Last 5 minutes
            job.interval = 30  # Update every 30 seconds
        else:
            job.interval = 60  # Update every minute
    
    except Exception as e:
        logger.error(f"Error updating timer: {e}")
        # Don't cancel the job on error, try again next time

async def clear_chat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear chat history when called by job queue."""
    job = context.job
    user_id = job.data['user_id']
    
    try:
        # Send message before clearing
        await context.bot.send_message(
            chat_id=user_id,
            text="🧹 *Clearing chat history...*\nYour session has ended.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        await asyncio.sleep(2)  # Give time for message to be seen
        
        # Delete all messages in the chat
        await context.bot.delete_chat_history(chat_id=user_id)
        
        # Notify user that history was cleared
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Chat history has been cleared for privacy.\nStart a new session with /start if needed.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        logger.info(f"Successfully cleared chat history for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to clear chat history for user {user_id}: {e}")
        try:
            # Notify user of failure
            await context.bot.send_message(
                chat_id=user_id,
                text="⚠️ Could not automatically clear chat history. Please clear it manually for privacy.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

async def setup_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command to set up the backup group."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can set up the backup group.")
        return
    
    # Check if this is a group chat
    chat_type = update.effective_chat.type
    if chat_type not in ["group", "supergroup"]:
        await update.message.reply_text("This command should only be used in a group chat.")
        return
    
    global GROUP_ID
    GROUP_ID = str(update.effective_chat.id)
    
    # Save to .env file
    try:
        with open(".env", "r") as file:
            env_lines = file.readlines()
        
        # Check if GROUP_ID already exists in the file
        group_id_exists = False
        for i, line in enumerate(env_lines):
            if line.startswith("GROUP_ID="):
                env_lines[i] = f"GROUP_ID={GROUP_ID}\n"
                group_id_exists = True
                break
        
        # If GROUP_ID doesn't exist, add it
        if not group_id_exists:
            env_lines.append(f"GROUP_ID={GROUP_ID}\n")
        
        # Write back to the file
        with open(".env", "w") as file:
            file.writelines(env_lines)
        
        await update.message.reply_text(f"✅ Backup group has been set up with ID: {GROUP_ID}")
        await update.message.reply_text("This group will now receive all messages sent to the bot.")
    except Exception as e:
        logger.error(f"Error updating .env file: {e}")
        await update.message.reply_text(f"⚠️ Error saving group ID to .env file: {e}")
        await update.message.reply_text(f"Group ID set temporarily for this session: {GROUP_ID}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcast a message to all authenticated users."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can broadcast messages.")
        return
    
    # Check if there's a message to broadcast
    if not context.args:
        await update.message.reply_text(
            "Please provide a message to broadcast.\n"
            "Usage: /broadcast Your message here"
        )
        return
    
    broadcast_message = " ".join(context.args)
    sent_count = 0
    failed_count = 0
    
    # Send message to all authenticated users
    for user_id_str, user_data in bot_data.authenticated_users.items():
        user_id = int(user_id_str)
        if user_id != ADMIN_ID:  # Don't send to yourself
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📢 *Broadcast Message*\n\n{broadcast_message}",
                    parse_mode=ParseMode.MARKDOWN
                )
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to user {user_id}: {e}")
                failed_count += 1
    
    await update.message.reply_text(
        f"✅ Broadcast sent to {sent_count} users\n"
        f"❌ Failed to send to {failed_count} users"
    )

async def block_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block a user from using the bot."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can block users.")
        return
    
    # Check if there's a user ID provided
    if not context.args:
        await update.message.reply_text(
            "Please provide a user ID to block.\n"
            "Usage: /block 123456789"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Don't allow blocking the admin
        if target_user_id == ADMIN_ID:
            await update.message.reply_text("You cannot block yourself as the admin.")
            return
        
        # Add user to blocked list if not already blocked
        if target_user_id not in bot_data.blocked_users:
            bot_data.blocked_users.append(target_user_id)
            
            # Remove from authenticated users if present
            if str(target_user_id) in bot_data.authenticated_users:
                del bot_data.authenticated_users[str(target_user_id)]
            
            bot_data.save_to_file()
            await update.message.reply_text(f"User {target_id} has been blocked.")
        else:
            await update.message.reply_text(f"User {target_id} is already blocked.")
    except ValueError:
        await update.message.reply_text("Please provide a valid user ID (numbers only).")

async def unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unblock a user from using the bot."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can unblock users.")
        return
    
    # Check if there's a user ID provided
    if not context.args:
        await update.message.reply_text(
            "Please provide a user ID to unblock.\n"
            "Usage: /unblock 123456789"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Remove user from blocked list
        if target_user_id in bot_data.blocked_users:
            bot_data.blocked_users.remove(target_user_id)
            bot_data.save_to_file()
            await update.message.reply_text(f"User {target_id} has been unblocked.")
        else:
            await update.message.reply_text(f"User {target_id} is not blocked.")
    except ValueError:
        await update.message.reply_text("Please provide a valid user ID (numbers only).")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all authenticated users."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can view the user list.")
        return
    
    if not bot_data.authenticated_users:
        await update.message.reply_text("No authenticated users found.")
        return
    
    user_list = "*Authenticated Users:*\n\n"
    for user_id_str, user_data in bot_data.authenticated_users.items():
        user_id = user_id_str
        name = user_data.get("name", "Unknown")
        timestamp = user_data.get("timestamp", "Unknown")
        is_admin = "Admin" if user_data.get("is_admin", False) else "User"
        
        user_list += f"• ID: `{user_id}`\n"
        user_list += f"  Name: {name}\n"
        user_list += f"  Role: {is_admin}\n"
        user_list += f"  Joined: {timestamp[:10]}\n\n"
    
    user_list += f"Total users: {len(bot_data.authenticated_users)}"
    
    try:
        await update.message.reply_text(user_list, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        # If the message is too long, split it
        await update.message.reply_text("User list is too long, sending in parts...")
        chunks = [user_list[i:i+4000] for i in range(0, len(user_list), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)

async def set_security_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a new security question and answer."""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("Only the admin can change security questions.")
        return
    
    # Check if there's text provided
    if not context.args:
        await update.message.reply_text(
            "Please provide a question and answer separated by a pipe (|).\n"
            "Usage: /setquestion What is the secret code?|secret123"
        )
        return
    
    input_text = " ".join(context.args)
    if "|" not in input_text:
        await update.message.reply_text(
            "Question and answer must be separated by a pipe (|).\n"
            "Example: /setquestion What is the secret code?|secret123"
        )
        return
    
    question, answer = input_text.split("|", 1)
    question = question.strip()
    answer = answer.strip()
    
    if not question or not answer:
        await update.message.reply_text("Both question and answer must be provided.")
        return
    
    # Clear existing questions and set the new one
    bot_data.security_questions = {question: answer}
    bot_data.save_to_file()
    
    await update.message.reply_text(
        f"✅ Security question updated successfully.\n"
        f"Question: {question}\n"
        f"Answer: {answer}"
    )

async def check_authentication(user_id: int, last_activity: str) -> bool:
    """Check if user's authentication is still valid (not expired after 15 minutes)."""
    try:
        # Parse the last activity timestamp
        last_active = datetime.fromisoformat(last_activity)
        current_time = datetime.now()
        
        # Calculate time difference in minutes
        time_diff = (current_time - last_active).total_seconds() / 60
        
        # If more than 15 minutes have passed, authentication has expired
        if time_diff > 15:
            logger.info(f"Authentication expired for user {user_id} after {time_diff:.1f} minutes")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error checking authentication expiration: {e}")
        return False  # If there's an error, require re-authentication

async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Relay text messages to admin and group."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Check if message is in private chat
    if update.effective_chat.type != "private":
        # Don't relay messages from groups
        return
    
    # If it's the admin, just acknowledge
    if user_id == ADMIN_ID:
        # Just acknowledge, no reply functionality needed
        admin_msg = await update.message.reply_text("Message received.")
        # Auto-delete the confirmation after 5 seconds
        asyncio.create_task(delete_message_after_delay(admin_msg, 5))
        return
    
    # Check if user is authenticated and session is valid
    if not bot_data.is_session_valid(user_id):
        # Authentication expired or user not authenticated
        if str(user_id) in bot_data.authenticated_users:
            # Session expired, remove from authenticated users
            del bot_data.authenticated_users[str(user_id)]
            bot_data.save_to_file()
            auth_error = await update.message.reply_text(
                "Your session has expired. Please authenticate again with /start"
            )
            # Auto-delete after 10 seconds
            asyncio.create_task(delete_message_after_delay(auth_error, 10))
            
            # Clear chat history after session expiry
            await clear_chat_history(context, user_id)
        else:
            auth_required = await update.message.reply_text(
                "You need to authenticate first. Please use /start command."
            )
            # Auto-delete after 10 seconds
            asyncio.create_task(delete_message_after_delay(auth_required, 10))
        return
    
    # Update last activity timestamp for valid sessions
    bot_data.update_activity(user_id)
    
    # Get the message content
    message_content = update.message.text
    message_id = update.message.message_id
    
    # IMPORTANT: Delete the original message first, before sending any confirmation
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete user message: {e}")
    
    # Send plain message to admin WITHOUT metadata header
    admin_msg = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=message_content
    )
    
    # Log the message metadata in the group if configured
    if GROUP_ID:
        try:
            # Send detailed message to group with action buttons
            group_message = (
                f"📨 *Message from {user_name}*\n"
                f"👤 ID: `{user_id}`\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"{message_content}"
            )
            
            # Create action buttons for admin
            keyboard = [
                [InlineKeyboardButton("Reply to User", callback_data=f"reply_{user_id}")],
                [InlineKeyboardButton("Block User", callback_data=f"block_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            group_msg = await context.bot.send_message(
                chat_id=GROUP_ID,
                text=group_message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
            # Store message mapping for /showme command
            if not hasattr(context.bot_data, 'message_map'):
                context.bot_data['message_map'] = {}
            
            # Map the admin message ID to BOTH group messages 
            # (use the media message ID as primary for /showme)
            context.bot_data['message_map'][str(admin_msg.message_id)] = {
                'chat_id': GROUP_ID,
                'message_id': group_msg.message_id,  # Use the media message ID
                'info_message_id': group_msg.message_id,   # Store the info message ID too
                'sender_id': user_id,
                'sender_name': user_name,
                'media_type': "Message"
            }
            
            # Send a single small note about /showme once per conversation
            if not context.user_data.get('showme_info_sent'):
                info_msg = await admin_msg.reply_text(
                    "Reply with /showme to see full message details in backup group.",
                    parse_mode=ParseMode.MARKDOWN
                )
                context.user_data['showme_info_sent'] = True
                # Delete this info after 10 seconds
                asyncio.create_task(delete_message_after_delay(info_msg, 10))
                
        except Exception as e:
            logger.error(f"Failed to relay message to group: {e}")
    
    # Send confirmation that will be deleted
    confirm_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Message sent"
    )
    
    # Delete confirmation after a short delay
    asyncio.create_task(delete_message_after_delay(confirm_msg, 3))

async def showme_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Link to the original message in the backup group"""
    user_id = update.effective_user.id
    
    # Only admin can use this command
    if user_id != ADMIN_ID:
        await update.message.reply_text("This command is only available to the admin.")
        return
    
    # Check if this is a reply to a message
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Please reply to a message to see its backup group version."
        )
        return
    
    # Get the original message ID
    msg_id = update.message.reply_to_message.message_id
    
    # Check if we have this message mapped
    message_map = context.bot_data.get('message_map', {})
    if str(msg_id) in message_map:
        group_info = message_map[str(msg_id)]
        group_id = group_info['chat_id']
        group_msg_id = group_info['message_id']
        sender_id = group_info.get('sender_id', 'Unknown')
        sender_name = group_info.get('sender_name', 'Unknown')
        media_type = group_info.get('media_type', 'Message')
        
        # Remove the -100 prefix for the URL
        if str(group_id).startswith('-100'):
            clean_group_id = str(group_id)[4:]
        else:
            clean_group_id = str(group_id)
        
        # Create the message link
        message_link = f"https://t.me/c/{clean_group_id}/{group_msg_id}"
        
        # Create buttons for various actions
        keyboard = [
            [InlineKeyboardButton("View Message Details", url=message_link)],
            [InlineKeyboardButton("Reply to User", callback_data=f"reply_{sender_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"*Message Info:*\n"
            f"• From: {sender_name}\n"
            f"• ID: `{sender_id}`\n"
            f"• Type: {media_type}\n"
            f"• Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Click the buttons below for actions:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "Could not find the detailed message in the backup group."
        )

async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available commands."""
    user_id = update.effective_user.id
    
    # Check if user is authenticated
    if not bot_data.is_session_valid(user_id) and user_id != ADMIN_ID:
        not_auth_msg = await update.message.reply_text(
            "❌ You are not authenticated. Please use /start to authenticate."
        )
        # Auto-delete the message after 15 seconds
        asyncio.create_task(delete_message_after_delay(not_auth_msg, 15))
        return
    
    # Update last activity for authenticated users
    if str(user_id) in bot_data.authenticated_users:
        bot_data.update_activity(user_id)
    
    # Build command list
    if user_id == ADMIN_ID:
        commands = (
            "*Available Commands*\n\n"
            "General commands:\n"
            "/start - Start the bot and authenticate\n"
            "/status - Show bot status\n"
            "/help - Show help message\n"
            "/cmd - Show this command list\n\n"
            
            "Admin commands:\n"
            "/broadcast - Send message to all authenticated users\n"
            "/clearall - Clear all authenticated users\n"
            "/showme - Show message details in backup group"
        )
    else:
        commands = (
            "*Available Commands*\n\n"
            "/start - Start the bot and authenticate\n"
            "/status - Check your authentication status\n"
            "/help - Show help message\n"
            "/cmd - Show this command list"
        )
            
    await update.message.reply_text(
        commands,
        parse_mode=ParseMode.MARKDOWN
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        help_text = (
            "*🤖 GT-UP Bot - Admin Help*\n\n"
            "This bot allows users to send you messages after authentication.\n\n"
            "*Key Features:*\n"
            "• Secure authentication with custom security question\n"
            "• Backup group for message logging\n"
            "• Session management (15-minute timeout)\n"
            "• User blocking capabilities\n\n"
            "Use /cmd to see all available commands."
        )
        help_msg = await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
        # Schedule deletion of help message after 13 seconds
        asyncio.create_task(delete_message_after_delay(help_msg, 13))
    else:
        help_text = (
            "*🤖 GT-UP Bot - Help*\n\n"
            "This bot allows you to send messages to the admin.\n\n"
            "*How to use:*\n"
            "1. Use /start to authenticate\n"
            "2. Answer the security question correctly\n"
            "3. Send your message once authenticated\n\n"
            "*Notes:*\n"
            "• Your session expires after 15 minutes of inactivity\n"
            "• Use /status to check your authentication status"
        )
        help_msg = await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
        # Schedule deletion of help message after 13 seconds
        asyncio.create_task(delete_message_after_delay(help_msg, 13))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Only admin can interact with these buttons, except auth and timer buttons
    if user_id != ADMIN_ID and not query.data.startswith("session_") and not query.data.startswith("timer_") and not query.data == "session_info":
        await query.answer("You're not authorized to use these controls.")
        return
    
    callback_data = query.data
    
    # Timer info button
    if callback_data == "timer_info":
        # Check if user is authenticated
        if not bot_data.is_session_valid(user_id):
            await query.answer("Your session has expired.")
            return
            
        # Get session info
        user_data = bot_data.authenticated_users[str(user_id)]
        expiry_time = datetime.fromisoformat(user_data.get("expiry_time", datetime.now().isoformat()))
        
        # Calculate remaining time
        remaining = expiry_time - datetime.now()
        if remaining.total_seconds() <= 0:
            await query.answer("Your session has expired.")
            return
        
        # Format time
        hours, remainder = divmod(remaining.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            time_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
        else:
            time_str = f"{int(minutes)}m {int(seconds)}s"
        
        # Show time remaining
        await query.answer(f"Session expires in: {time_str}", show_alert=True)
        return
        
    # Session info button
    elif callback_data == "session_info":
        # Check if user is authenticated
        if not bot_data.is_session_valid(user_id):
            await query.answer("You are not currently authenticated.")
            return
        
        # Get session info
        user_data = bot_data.authenticated_users[str(user_id)]
        session_type = user_data.get("session_type", "standard")
        authenticated_at = datetime.fromisoformat(user_data.get("authenticated_at", datetime.now().isoformat()))
        last_activity = datetime.fromisoformat(user_data.get("last_activity", datetime.now().isoformat()))
        
        # Calculate time since authentication
        time_since_auth = datetime.now() - authenticated_at
        auth_minutes = int(time_since_auth.total_seconds() / 60)
        
        # Calculate time since last activity
        time_since_activity = datetime.now() - last_activity
        activity_minutes = int(time_since_activity.total_seconds() / 60)
        
        # Answer with session info
        await query.answer(
            f"Session: {session_type.capitalize()}\n"
            f"Authenticated {auth_minutes} min ago\n"
            f"Last activity: {activity_minutes} min ago",
            show_alert=True
        )
        return
    
    # Handle authentication button pressed by non-admin user
    elif callback_data == "auth":
        await query.answer("Please use /start command to authenticate.")
        await query.message.reply_text("Please use /start command to begin authentication.")
        return
    
    # Session type selection
    elif callback_data.startswith("session_"):
        # This is now handled by session_selection function
        await query.answer("Please wait...")
        return
    
    # Reply to user button
    elif callback_data.startswith("reply_"):
        try:
            target_id = int(callback_data.split("_")[1])
            await query.answer("Please type your reply to this message.")
            await query.edit_message_reply_markup(reply_markup=None)
            
            # Add a note to the message indicating reply mode
            await query.message.reply_text(
                f"✏️ *Reply Mode Activated*\n"
                f"Reply to this message to send a response to user ID: `{target_id}`",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Error setting up reply: {e}")
            await query.answer(f"Error setting up reply: {e}")
    
    # Block user button
    elif callback_data.startswith("block_"):
        try:
            target_id = int(callback_data.split("_")[1])
            
            if target_id not in bot_data.blocked_users:
                bot_data.blocked_users.append(target_id)
                
                # Remove from authenticated users if present
                if str(target_id) in bot_data.authenticated_users:
                    del bot_data.authenticated_users[str(target_id)]
                
                bot_data.save_to_file()
                
                await query.answer(f"User {target_id} has been blocked.")
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"✅ User {target_id} has been blocked.")
                
                # Notify the user
                try:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text="You have been blocked from using this bot."
                    )
                except Exception as e:
                    logger.error(f"Could not notify user about being blocked: {e}")
            else:
                await query.answer(f"User {target_id} is already blocked.")
        except Exception as e:
            logger.error(f"Error blocking user: {e}")
            await query.answer(f"Error blocking user: {e}")
    
    # Terminate session button
    elif callback_data.startswith("terminate_"):
        try:
            target_id = int(callback_data.split("_")[1])
            
            # Remove from authenticated users if present
            if str(target_id) in bot_data.authenticated_users:
                del bot_data.authenticated_users[str(target_id)]
                bot_data.save_to_file()
                
                await query.answer(f"Session for user {target_id} has been terminated.")
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"✅ Session for user {target_id} has been terminated.")
                
                # Notify the user and clear chat history
                try:
                    # Send termination notification
                    term_msg = await context.bot.send_message(
                        chat_id=target_id,
                        text="Your session has been terminated by the admin."
                    )
                    
                    # Clear chat history
                    try:
                        await context.bot.send_message(
                            chat_id=target_id,
                            text="🧹 Clearing chat history for privacy...",
                            parse_mode=ParseMode.MARKDOWN
                        )
                        await asyncio.sleep(1)
                        await context.bot.delete_chat_history(chat_id=target_id)
                        await context.bot.send_message(
                            chat_id=target_id,
                            text="✅ Chat history cleared.",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception as e:
                        logger.error(f"Failed to clear chat for terminated session: {e}")
                    
                except Exception as e:
                    logger.error(f"Could not notify user about session termination: {e}")
            else:
                await query.answer(f"User {target_id} is not currently authenticated.")
        except Exception as e:
            logger.error(f"Error terminating session: {e}")
            await query.answer(f"Error terminating session: {e}")
    
    # Show message details
    elif callback_data.startswith("showme_"):
        try:
            msg_id = int(callback_data.split("_")[1])
            
            # Check if we have this message mapped
            message_map = context.bot_data.get('message_map', {})
            if str(msg_id) in message_map:
                group_info = message_map[str(msg_id)]
                group_id = group_info['chat_id']
                group_msg_id = group_info['message_id']
                
                # Remove the -100 prefix for the URL
                if str(group_id).startswith('-100'):
                    clean_group_id = str(group_id)[4:]
                else:
                    clean_group_id = str(group_id)
                
                # Create the message link
                message_link = f"https://t.me/c/{clean_group_id}/{group_msg_id}"
                
                keyboard = [[InlineKeyboardButton("View Details", url=message_link)]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.answer("Link to message details generated")
                await query.edit_message_reply_markup(reply_markup=reply_markup)
            else:
                await query.answer("Could not find message details")
        except Exception as e:
            logger.error(f"Error generating message link: {e}")
            await query.answer(f"Error generating message link")
    
    # Handle clearall confirmation
    elif callback_data == "confirm_clearall":
        try:
            # Get list of users before clearing
            users_to_clear = list(bot_data.authenticated_users.keys())
            user_count = len(users_to_clear)
            
            # Reset authenticated users
            bot_data.authenticated_users = {}
            bot_data.save_to_file()
            
            # Update the callback message
            await query.edit_message_text(
                f"✅ Successfully cleared {user_count} authenticated users.\n"
                f"All sessions have been terminated."
            )
            
            # Notify users that their sessions were terminated
            for user_id_str in users_to_clear:
                try:
                    user_id_int = int(user_id_str)
                    
                    # Skip if it's the admin (though we've already excluded admin from the list)
                    if user_id_int == ADMIN_ID:
                        continue
                    
                    # Send notification to the user
                    await context.bot.send_message(
                        chat_id=user_id_int,
                        text="⚠️ *Your session has been terminated by admin.*\n"
                             "You must authenticate again with /start to continue.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    # Clear their chat history
                    try:
                        await context.bot.send_message(
                            chat_id=user_id_int,
                            text="🧹 Clearing chat history for privacy...",
                            parse_mode=ParseMode.MARKDOWN
                        )
                        await asyncio.sleep(1)
                        await context.bot.delete_chat_history(chat_id=user_id_int)
                        await context.bot.send_message(
                            chat_id=user_id_int,
                            text="✅ Chat history cleared.",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception as e:
                        logger.error(f"Failed to clear chat history for user {user_id_int}: {e}")
                    
                except Exception as e:
                    logger.error(f"Failed to notify user {user_id_str} of session termination: {e}")
        
        except Exception as e:
            logger.error(f"Error clearing users: {e}")
            await query.answer(f"Error clearing users: {e}")
    
    # Handle clearall cancellation
    elif callback_data == "cancel_clearall":
        await query.answer("Operation cancelled.")
        await query.edit_message_text("❌ Clear all operation cancelled.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates."""
    logger.error(f"Update {update} caused error: {context.error}")
    
    # Notify admin of errors
    if ADMIN_ID:
        error_message = f"⚠️ *ERROR ALERT*\n\n"
        
        if update:
            if update.effective_user:
                error_message += f"User: {update.effective_user.first_name} (ID: {update.effective_user.id})\n"
            if update.effective_chat:
                error_message += f"Chat: {update.effective_chat.title} (ID: {update.effective_chat.id})\n"
            if update.effective_message:
                error_message += f"Message: {update.effective_message.text if update.effective_message.text else '[No text]'}\n"
        
        error_message += f"\nError: {context.error}"
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=error_message,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle non-text media messages."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Check if message is in private chat
    if update.effective_chat.type != "private":
        # Don't relay messages from groups
        return
    
    # If it's the admin, just acknowledge
    if user_id == ADMIN_ID:
        # Just acknowledge
        admin_msg = await update.message.reply_text("Media received.")
        # Auto-delete confirmation after 5 seconds
        asyncio.create_task(delete_message_after_delay(admin_msg, 5))
        return
    
    # Check if user is authenticated and session is valid
    if not bot_data.is_session_valid(user_id):
        # Authentication expired or user not authenticated
        if str(user_id) in bot_data.authenticated_users:
            # Session expired, remove from authenticated users
            del bot_data.authenticated_users[str(user_id)]
            bot_data.save_to_file()
            auth_error = await update.message.reply_text(
                "Your session has expired. Please authenticate again with /start"
            )
            # Auto-delete after 10 seconds
            asyncio.create_task(delete_message_after_delay(auth_error, 10))
            
            # Clear chat history after session expiry
            await clear_chat_history(context, user_id)
        else:
            auth_required = await update.message.reply_text(
                "You need to authenticate first. Please use /start command."
            )
            # Auto-delete after 10 seconds
            asyncio.create_task(delete_message_after_delay(auth_required, 10))
        return
    
    # Update last activity timestamp for valid sessions
    bot_data.update_activity(user_id)
    
    # Determine media type and relay to admin
    media_type = "Unknown"
    file_id = None
    caption = update.message.caption or ""
    
    if update.message.photo:
        media_type = "Photo"
        file_id = update.message.photo[-1].file_id  # Get the largest photo
    elif update.message.video:
        media_type = "Video"
        file_id = update.message.video.file_id
    elif update.message.audio:
        media_type = "Audio"
        file_id = update.message.audio.file_id
    elif update.message.voice:
        media_type = "Voice"
        file_id = update.message.voice.file_id
    elif update.message.document:
        media_type = "Document"
        file_id = update.message.document.file_id
    elif update.message.sticker:
        media_type = "Sticker"
        file_id = update.message.sticker.file_id
    elif update.message.animation:
        media_type = "Animation"
        file_id = update.message.animation.file_id
    elif update.message.video_note:
        media_type = "Video Note"
        file_id = update.message.video_note.file_id
    
    # IMPORTANT: Save the original message before deleting it
    # This needs to happen before we delete the message
    original_message = update.message
    
    # Try to delete the original message first
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete user media: {e}")
    
    # Forward media to admin
    try:
        # Send media to admin (just the media, no header text)
        admin_msg = None
        if media_type == "Photo":
            admin_msg = await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=caption)
        elif media_type == "Video":
            admin_msg = await context.bot.send_video(chat_id=ADMIN_ID, video=file_id, caption=caption)
        elif media_type == "Audio":
            admin_msg = await context.bot.send_audio(chat_id=ADMIN_ID, audio=file_id, caption=caption)
        elif media_type == "Voice":
            admin_msg = await context.bot.send_voice(chat_id=ADMIN_ID, voice=file_id, caption=caption)
        elif media_type == "Document":
            admin_msg = await context.bot.send_document(chat_id=ADMIN_ID, document=file_id, caption=caption)
        elif media_type == "Sticker":
            admin_msg = await context.bot.send_sticker(chat_id=ADMIN_ID, sticker=file_id)
        elif media_type == "Animation":
            admin_msg = await context.bot.send_animation(chat_id=ADMIN_ID, animation=file_id, caption=caption)
        elif media_type == "Video Note":
            admin_msg = await context.bot.send_video_note(chat_id=ADMIN_ID, video_note=file_id)
        else:
            # Forward unknown media types directly
            admin_msg = await original_message.forward(chat_id=ADMIN_ID)
        
        # Send detailed info AND the media to the backup group
        if GROUP_ID and admin_msg:
            # First send the media file to the group
            group_media_msg = None
            if media_type == "Photo":
                group_media_msg = await context.bot.send_photo(chat_id=GROUP_ID, photo=file_id, caption=caption)
            elif media_type == "Video":
                group_media_msg = await context.bot.send_video(chat_id=GROUP_ID, video=file_id, caption=caption)
            elif media_type == "Audio":
                group_media_msg = await context.bot.send_audio(chat_id=GROUP_ID, audio=file_id, caption=caption)
            elif media_type == "Voice":
                group_media_msg = await context.bot.send_voice(chat_id=GROUP_ID, voice=file_id, caption=caption)
            elif media_type == "Document":
                group_media_msg = await context.bot.send_document(chat_id=GROUP_ID, document=file_id, caption=caption)
            elif media_type == "Sticker":
                group_media_msg = await context.bot.send_sticker(chat_id=GROUP_ID, sticker=file_id)
            elif media_type == "Animation":
                group_media_msg = await context.bot.send_animation(chat_id=GROUP_ID, animation=file_id, caption=caption)
            elif media_type == "Video Note":
                group_media_msg = await context.bot.send_video_note(chat_id=GROUP_ID, video_note=file_id)
            else:
                # Forward unknown media types directly
                group_media_msg = await original_message.forward(chat_id=GROUP_ID)
            
            # Then send the info message with action buttons
            group_info = (
                f"📨 *{media_type} from {user_name}*\n"
                f"👤 ID: `{user_id}`\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            if caption:
                group_info += f"\n\n*Caption:* {caption}"
            
            # Create action buttons for admin
            keyboard = [
                [InlineKeyboardButton("Reply to User", callback_data=f"reply_{user_id}")],
                [InlineKeyboardButton("Block User", callback_data=f"block_{user_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            group_msg = await context.bot.send_message(
                chat_id=GROUP_ID,
                text=group_info,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
            
            # Store message mapping for /showme command
            if not hasattr(context.bot_data, 'message_map'):
                context.bot_data['message_map'] = {}
            
            # Map the admin message ID to BOTH group messages 
            # (use the media message ID as primary for /showme)
            context.bot_data['message_map'][str(admin_msg.message_id)] = {
                'chat_id': GROUP_ID,
                'message_id': group_media_msg.message_id,  # Use the media message ID
                'info_message_id': group_msg.message_id,   # Store the info message ID too
                'sender_id': user_id,
                'sender_name': user_name,
                'media_type': media_type
            }
            
            # Send a single small note about /showme once per conversation
            if not context.user_data.get('showme_info_sent'):
                info_msg = await admin_msg.reply_text(
                    "Reply with /showme to see full message details in backup group.",
                    parse_mode=ParseMode.MARKDOWN
                )
                context.user_data['showme_info_sent'] = True
                # Delete this info after 10 seconds
                asyncio.create_task(delete_message_after_delay(info_msg, 10))
    
    except Exception as e:
        logger.error(f"Failed to relay media: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Failed to send media. Please try again later."
        )
        return
    
    # Send confirmation that will be deleted
    confirm_msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{media_type} sent"
    )
    
    # Delete confirmation after a short delay
    asyncio.create_task(delete_message_after_delay(confirm_msg, 3))

async def register_bot_commands(application: Application) -> None:
    """Register bot commands with Telegram to show in the command menu."""
    # Define commands for regular users
    user_commands = [
        ("start", "Start the bot and authenticate"),
        ("status", "Check your authentication status"),
        ("help", "Show help information")
    ]
    
    # Define commands for admin
    admin_commands = [
        ("start", "Start the bot and authenticate"),
        ("help", "Show help information"),
        ("status", "Check system status and active sessions"),
        ("cmd", "Show this command list"),
        ("setupgroup", "Set current group as backup group"),
        ("broadcast", "Send message to all users"),
        ("block", "Block a user from using the bot"),
        ("unblock", "Unblock a previously blocked user"),
        ("users", "List all authenticated users"),
        ("setquestion", "Set security question and answer"),
        ("showme", "See full message details when replying"),
        ("clearall", "Clear all authenticated users")
    ]
    
    # Set commands for regular users (visible in private chats)
    await application.bot.set_my_commands(
        [BotCommand(command, description) for command, description in user_commands],
        scope=BotCommandScopeAllPrivateChats()
    )
    
    # Set commands for admin (visible only to admin)
    if ADMIN_ID:
        await application.bot.set_my_commands(
            [BotCommand(command, description) for command, description in admin_commands],
            scope=BotCommandScopeChat(chat_id=ADMIN_ID)
        )
    
    logger.info("Bot commands registered")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot status information."""
    user_id = update.effective_user.id
    
    # Calculate uptime
    uptime = datetime.now() - bot_data.start_time
    days, remainder = divmod(uptime.total_seconds(), 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    uptime_str = f"{int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s"
    
    # Count active sessions
    active_sessions = 0
    for user_id_str, user_data in bot_data.authenticated_users.items():
        if bot_data.is_session_valid(int(user_id_str)):
            active_sessions += 1
    
    # Count backups
    backup_count = 0
    if os.path.exists(BACKUP_DIR):
        backup_count = len([f for f in os.listdir(BACKUP_DIR) if f.startswith("bot_data_") and f.endswith(".json")])
    
    # If user is admin, show detailed status
    if user_id == ADMIN_ID:
        status_message = (
            f"*Bot Status*\n\n"
            f"🕒 Uptime: {uptime_str}\n"
            f"👥 Authenticated users: {len(bot_data.authenticated_users)}\n"
            f"🔄 Active sessions: {active_sessions}\n"
            f"💾 Backups: {backup_count}\n\n"
            f"*Session Settings*\n"
            f"⏱️ Standard session: {int(SESSION_TIMEOUT.total_seconds()/60)} minutes\n"
            f"⏱️ Extended session: {int(EXTENDED_SESSION_TIMEOUT.total_seconds()/60)} minutes"
        )
        
        await update.message.reply_text(
            status_message,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        # Check if user is authenticated
        if bot_data.is_session_valid(user_id):
            user_data = bot_data.authenticated_users[str(user_id)]
            session_type = user_data.get("session_type", "standard")
            authenticated_at = datetime.fromisoformat(user_data.get("authenticated_at", datetime.now().isoformat()))
            last_activity = datetime.fromisoformat(user_data.get("last_activity", datetime.now().isoformat()))
            
            time_since_auth = datetime.now() - authenticated_at
            time_since_activity = datetime.now() - last_activity
            
            status_message = (
                f"*Your Status*\n\n"
                f"✅ Authenticated: Yes\n"
                f"🔑 Session type: {session_type.capitalize()}\n"
                f"⏰ Authenticated {int(time_since_auth.total_seconds()/60)} minutes ago\n"
                f"⌛ Last activity: {int(time_since_activity.total_seconds()/60)} minutes ago\n"
                f"🤖 Bot uptime: {uptime_str}"
            )
            
            await update.message.reply_text(
                status_message,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # User is not authenticated
            not_auth_msg = await update.message.reply_text(
                "❌ You are not authenticated. Please use /start to authenticate."
            )
            
            # Auto-delete the "Not authenticated" message after 15 seconds
            asyncio.create_task(delete_message_after_delay(not_auth_msg, 15))

async def delete_message_after_delay(message, delay_seconds):
    """Delete a message after a specified delay."""
    await asyncio.sleep(delay_seconds)
    try:
        await message.delete()
    except Exception as e:
        # Don't log common errors like "message to delete not found"
        if "message to delete not found" not in str(e).lower():
            logger.warning(f"Could not delete message: {e}")

async def clear_chat_history(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear chat history for a user."""
    job = context.job
    user_id = job.data["user_id"]
    
    try:
        logger.info(f"Executing chat history clear for user {user_id}")
        
        # Send message before clearing
        await context.bot.send_message(
            chat_id=user_id,
            text="🧹 *Clearing chat history...*\nYour session has ended.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        await asyncio.sleep(2)  # Give time for message to be seen
        
        # Delete all messages in the chat
        await context.bot.delete_chat_history(chat_id=user_id)
        
        # Notify user that history was cleared
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Chat history has been cleared for privacy.\nStart a new session with /start if needed.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        logger.info(f"Successfully cleared chat history for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to clear chat history for user {user_id}: {e}")
        try:
            # Notify user of failure
            await context.bot.send_message(
                chat_id=user_id,
                text="⚠️ Could not automatically clear chat history. Please clear it manually for privacy.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

async def schedule_chat_clear(context: ContextTypes.DEFAULT_TYPE, user_id: int, delay_seconds: int) -> None:
    """Schedule clearing chat history after session ends."""
    try:
        logger.info(f"Scheduled chat clear for user {user_id} in {delay_seconds} seconds")
        # Add job to job queue
        context.job_queue.run_once(
            clear_chat_history,
            delay_seconds,
            data={"user_id": user_id}
        )
    except Exception as e:
        logger.error(f"Error scheduling chat clear: {e}")

async def wait_and_clear_chat(context: ContextTypes.DEFAULT_TYPE, user_id: int, delay_seconds: int) -> None:
    """Wait for the specified delay and then clear chat history."""
    await asyncio.sleep(delay_seconds)
    
    # Check if the user is still authenticated before clearing
    if not bot_data.is_session_valid(user_id):
        await clear_chat_history(context, user_id)
    # Otherwise, the session is still valid, so don't clear yet

async def clearall_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all authenticated users except the admin."""
    user_id = update.effective_user.id
    
    # Only the admin can use this command
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is only available to the admin.")
        return
    
    # Count authenticated users
    auth_count = len(bot_data.authenticated_users)
    
    if auth_count == 0:
        await update.message.reply_text("ℹ️ There are no authenticated users to clear.")
        return
    
    # Ask for confirmation with inline keyboard
    keyboard = [
        [InlineKeyboardButton("Yes, clear all", callback_data="confirm_clearall")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_clearall")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⚠️ *WARNING*\nThis will remove *{auth_count}* authenticated users and terminate all active sessions.\n\n"
        f"Are you sure you want to continue?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def clearall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle clearall confirmation button."""
    query = update.callback_query
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await query.answer("You are not authorized to perform this action.", show_alert=True)
        return
    
    action = query.data
    
    if action == "confirm_clearall":
        # Get list of users before clearing
        users_to_clear = list(bot_data.authenticated_users.keys())
        user_count = len(users_to_clear)
        
        # Reset authenticated users
        bot_data.authenticated_users = {}
        bot_data.save_to_file()
        
        # Update the callback message
        await query.edit_message_text(
            f"✅ Successfully cleared {user_count} authenticated users.\n"
            f"All sessions have been terminated."
        )
        
        # Notify users that their sessions were terminated
        for user_id_str in users_to_clear:
            try:
                user_id_int = int(user_id_str)
                
                # Skip if it's the admin (though we've already excluded admin from the list)
                if user_id_int == ADMIN_ID:
                    continue
                
                # Send notification to the user
                await context.bot.send_message(
                    chat_id=user_id_int,
                    text="⚠️ *Your session has been terminated by admin.*\n"
                         "You must authenticate again with /start to continue.",
                    parse_mode=ParseMode.MARKDOWN
                )
                
                # Clear their chat history
                try:
                    await context.bot.send_message(
                        chat_id=user_id_int,
                        text="🧹 Clearing chat history for privacy...",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    await asyncio.sleep(1)
                    await context.bot.delete_chat_history(chat_id=user_id_int)
                    await context.bot.send_message(
                        chat_id=user_id_int,
                        text="✅ Chat history cleared.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.error(f"Failed to clear chat history for user {user_id_int}: {e}")
                
            except Exception as e:
                logger.error(f"Failed to notify user {user_id_str} of session termination: {e}")
    
    elif action == "cancel_clearall":
        await query.edit_message_text("❌ Clear all operation canceled.")
    
    # Answer the callback query
    await query.answer()

def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add conversation handler for authentication with proper states
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AWAITING_AUTH_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, authenticate)],
            SESSION_SELECTION: [CallbackQueryHandler(session_selection, pattern="^session_")],
        },
        fallbacks=[CommandHandler("start", start)],
        # Remove per_message=True as it was causing issues
    )
    application.add_handler(conv_handler)
    
    # Add button callback handler for all button actions
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Add command handlers
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cmd", cmd_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("setupgroup", setup_group))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("block", block_user))
    application.add_handler(CommandHandler("unblock", unblock_user))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("setquestion", set_security_question))
    application.add_handler(CommandHandler("showme", showme_command))
    application.add_handler(CommandHandler("clearall", clearall_command))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay_message))
    
    # Media handler for all non-text messages
    application.add_handler(MessageHandler(~filters.TEXT, handle_media))
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Register bot commands on startup
    application.post_init = register_bot_commands
    
    # Determine if running on Railway
    if RAILWAY_STATIC_URL:
        # Set webhook for Railway deployment
        webhook_url = f"{RAILWAY_STATIC_URL}/{BOT_TOKEN}"
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=webhook_url
        )
        logger.info(f"Bot running in webhook mode on Railway.app at port {PORT}")
    else:
        # Run the bot locally in polling mode
        application.run_polling()
        logger.info("Bot running in polling mode")

if __name__ == "__main__":
    main()