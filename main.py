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
    
    # Send detailed authentication notification to backup group with action buttons
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
    
    # Send session information to the user
    expire_info = f"{expiry_time.strftime('%H:%M:%S')}" if session_type == "standard" else f"{expiry_time.strftime('%Y-%m-%d %H:%M:%S')}"
    
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"*Session Information*\n\n"
            f"📝 Type: {session_type.capitalize()}\n"
            f"⏱️ Duration: {session_minutes} minutes\n"
            f"⌛ Expires after: {session_minutes} minutes of inactivity\n"
            f"🕒 Current expiry: {expire_info}\n\n"
            f"Send messages directly to this chat. Your session will remain active as long as you interact with the bot."
        ),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return ConversationHandler.END

async def clear_chat_history(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Clear chat history for a user using a more reliable approach."""
    try:
        logger.info(f"Clearing chat history for user {user_id}")
        
        # Send message before clearing
        await context.bot.send_message(
            chat_id=user_id,
            text="🧹 *Clearing chat history...*\nYour session has ended.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        await asyncio.sleep(1)  # Brief pause
        
        try:
            # Try the direct method first
            await context.bot.delete_chat_history(chat_id=user_id)
        except Exception as e:
            logger.warning(f"Primary deletion method failed: {e}")
            
            # Fallback approach: use deleteChatPhoto API method, which often triggers chat cleanup
            try:
                await context.bot.delete_chat_photo(chat_id=user_id)
            except Exception:
                # If that fails too, try another approach
                pass
                
            # Alternative approach: delete the current chat
            try:
                await context.bot.leave_chat(chat_id=user_id)
                await asyncio.sleep(0.5)
                # Bot will need to be restarted by the user
            except Exception:
                pass
        
        # Notify user that history should be cleared or to do it manually
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ *Chat history clearing attempted*\n\n"
                 "If your chat wasn't cleared automatically, please:\n"
                 "1. Delete this chat from your list\n"
                 "2. Find the bot again and start a new chat\n"
                 "3. Use /start to authenticate again",
            parse_mode=ParseMode.MARKDOWN
        )
        
        logger.info(f"Chat history clearing attempted for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to clear chat history for user {user_id}: {e}")
        try:
            # Notify user of failure with manual instructions
            await context.bot.send_message(
                chat_id=user_id,
                text="⚠️ *Could not automatically clear chat history*\n\n"
                     "Please clear it manually for privacy:\n"
                     "1. Delete this chat from your chat list\n"
                     "2. Find the bot again and start a new chat\n"
                     "3. Use /start to authenticate again",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass

async def schedule_chat_clear(context: ContextTypes.DEFAULT_TYPE, user_id: int, delay_seconds: int) -> None:
    """Schedule clearing chat history after the specified delay."""
    # Schedule the task to run after the specified delay
    asyncio.create_task(
        delayed_chat_clear(context, user_id, delay_seconds)
    )
    logger.info(f"Scheduled chat history clear for user {user_id} in {delay_seconds} seconds")

async def delayed_chat_clear(context: ContextTypes.DEFAULT_TYPE, user_id: int, delay_seconds: int) -> None:
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
                
                # Skip if it's the admin
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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Only admin can interact with these buttons, except auth button
    if user_id != ADMIN_ID and not query.data.startswith("session_"):
        await query.answer("You're not authorized to use these controls.")
        return
    
    callback_data = query.data
    
    # Handle authentication button pressed by non-admin user
    if callback_data == "auth":
        await query.answer("Please use /start command to authenticate.")
        await query.message.reply_text("Please use /start command to begin authentication.")
        return
    
    # Session type selection
    elif callback_data.startswith("session_"):
        # This is handled by session_selection function
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
                    
                    # Also clear their chat history
                    await clear_chat_history(context, target_id)
                    
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
                
                # Notify the user
                try:
                    # Send termination notification
                    await context.bot.send_message(
                        chat_id=target_id,
                        text="⚠️ *Your session has been terminated by admin.*\nYou must authenticate again with /start to continue.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    # Clear chat history using our improved function
                    await clear_chat_history(context, target_id)
                    
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
                    
                    # Skip if it's the admin
                    if user_id_int == ADMIN_ID:
                        continue
                    
                    # Send notification to the user
                    await context.bot.send_message(
                        chat_id=user_id_int,
                        text="⚠️ *Your session has been terminated by admin.*\n"
                             "You must authenticate again with /start to continue.",
                        parse_mode=ParseMode.MARKDOWN
                    )
                    
                    # Clear their chat history using our improved function
                    await clear_chat_history(context, user_id_int)
                    
                except Exception as e:
                    logger.error(f"Failed to notify user {user_id_str} of session termination: {e}")
        except Exception as e:
            logger.error(f"Error clearing users: {e}")
            await query.answer(f"Error clearing users: {e}")
    
    # Handle clearall cancellation
    elif callback_data == "cancel_clearall":
        await query.answer("Operation cancelled.")
        await query.edit_message_text("❌ Clear all operation cancelled.")

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