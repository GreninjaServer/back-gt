import os
import logging
import re
import json
from datetime import datetime
from typing import Dict, List, Optional, Union, Any
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GROUP_ID = os.getenv("GROUP_ID")  # Will be set up if not provided
PORT = int(os.getenv("PORT", "8443"))
RAILWAY_STATIC_URL = os.getenv("RAILWAY_STATIC_URL")

# Data storage
DATA_FILE = "bot_data.json"

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
AUTHENTICATE = 0

# Data structures
class BotData:
    def __init__(self):
        self.authenticated_users: Dict[int, Dict[str, Any]] = {}
        self.blocked_users: List[int] = []
        self.security_questions: Dict[str, str] = {
            "What's your secret phrase?": "277353"  # Set to the provided secret answer
        }
        
    def save_to_file(self):
        """Save bot data to file"""
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump({
                    'authenticated_users': self.authenticated_users,
                    'blocked_users': self.blocked_users,
                    'security_questions': self.security_questions
                }, f)
            logger.info("Bot data saved successfully")
        except Exception as e:
            logger.error(f"Error saving bot data: {e}")
    
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

# Initialize bot data
bot_data = BotData()
bot_data.load_from_file()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Send a welcome message when the command /start is issued."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # If it's the admin, authenticate immediately
    if user_id == ADMIN_ID:
        bot_data.authenticated_users[user_id] = {
            "name": user_name,
            "timestamp": datetime.now().isoformat(),
            "is_admin": True
        }
        bot_data.save_to_file()
        await update.message.reply_text(
            "Welcome back! You're authenticated as admin. All messages sent to this bot will be stored in your account and backup group."
        )
        return ConversationHandler.END
    
    # Check if already authenticated
    if str(user_id) in bot_data.authenticated_users:
        await update.message.reply_text(
            "You've already been authenticated. Send any message and it will be forwarded to the admin."
        )
        return ConversationHandler.END
    
    # Ask for authentication
    question = list(bot_data.security_questions.keys())[0]
    await update.message.reply_text(
        f"To authenticate, please answer this question:\n\n{question}\n\n"
        f"Your answer will be deleted immediately for security."
    )
    return AUTHENTICATE

async def authenticate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle authentication attempts."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    message_text = update.message.text
    
    # Check if the answer matches
    question = list(bot_data.security_questions.keys())[0]
    correct_answer = bot_data.security_questions[question]
    
    # Delete the authentication message for security
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete authentication message: {e}")
    
    if message_text.lower() == correct_answer.lower():
        # Authenticate the user
        bot_data.authenticated_users[str(user_id)] = {
            "name": user_name,
            "timestamp": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "is_admin": (user_id == ADMIN_ID)
        }
        bot_data.save_to_file()
        
        await update.message.reply_text(
            "Authentication successful! You can now use the bot to send messages to the admin."
        )
        
        # Notify admin about new authentication with terminate buttons
        if user_id != ADMIN_ID:
            keyboard = [
                [
                    InlineKeyboardButton("❌ Terminate Session", callback_data=f"terminate_{user_id}"),
                    InlineKeyboardButton("🚫 Block User", callback_data=f"block_{user_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ Alert: New user authenticated from account:\n"
                     f"• Name: {user_name}\n"
                     f"• ID: `{user_id}`\n"
                     f"• Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Authentication failed. Please try again with /start"
        )
        return ConversationHandler.END

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
            await update.message.reply_text(f"User {target_user_id} has been blocked.")
        else:
            await update.message.reply_text(f"User {target_user_id} is already blocked.")
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
            await update.message.reply_text(f"User {target_user_id} has been unblocked.")
        else:
            await update.message.reply_text(f"User {target_user_id} is not blocked.")
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

async def check_authentication(user_id: int) -> bool:
    """Check if user's authentication is still valid (not expired after 15 minutes)."""
    try:
        # Convert user_id to string for dictionary lookup
        user_id_str = str(user_id)
        
        # Admin is always authenticated
        if user_id == ADMIN_ID:
            return True
            
        # Check if user exists in authenticated users
        if user_id_str not in bot_data.authenticated_users:
            return False
            
        # Get last activity timestamp
        last_activity = bot_data.authenticated_users[user_id_str].get("last_activity", "")
        if not last_activity:
            return False
            
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
        # Just acknowledge receipt - no special handling needed
        keyboard = [
            [KeyboardButton("📢 Broadcast"), KeyboardButton("👥 Users")],
            [KeyboardButton("⚙️ Settings"), KeyboardButton("📊 Stats")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Message received.", reply_markup=reply_markup)
        return
    
    # Check if user is authenticated
    auth_valid = await check_authentication(user_id)
    
    if not auth_valid:
        # Authentication expired or not found
        if str(user_id) in bot_data.authenticated_users:
            del bot_data.authenticated_users[str(user_id)]
            bot_data.save_to_file()
        
        keyboard = [[InlineKeyboardButton("Authenticate", callback_data="auth")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Your session has expired or you're not authenticated. Please use /start to authenticate.",
            reply_markup=reply_markup
        )
        return
    
    # Update last activity timestamp
    if str(user_id) in bot_data.authenticated_users:
        bot_data.authenticated_users[str(user_id)]["last_activity"] = datetime.now().isoformat()
        bot_data.save_to_file()
    
    # Relay the message to admin
    message_content = update.message.text
    forwarded_message = (
        f"📨 *Message from {user_name}*\n"
        f"👤 ID: `{user_id}`\n"
        f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{message_content}"
    )
    
    # Send to admin with reply markup
    keyboard = [
        [InlineKeyboardButton("Reply", callback_data=f"reply_{user_id}")],
        [InlineKeyboardButton("Block User", callback_data=f"block_{user_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=forwarded_message,
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # If group ID is configured, also relay to group
    if GROUP_ID:
        try:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=forwarded_message,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to relay message to group: {e}")
    
    # Simple acknowledgment with keyboard
    keyboard = [
        [KeyboardButton("📨 Help"), KeyboardButton("📝 Status")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("✓ Message sent", reply_markup=reply_markup)

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle media messages (photos, documents, etc.)"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Check if message is in private chat
    if update.effective_chat.type != "private":
        # Don't relay media from groups
        return
    
    # If it's the admin, just acknowledge
    if user_id == ADMIN_ID:
        keyboard = [
            [KeyboardButton("📢 Broadcast"), KeyboardButton("👥 Users")],
            [KeyboardButton("⚙️ Settings"), KeyboardButton("📊 Stats")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("Media received.", reply_markup=reply_markup)
        return
    
    # Check if user is authenticated
    auth_valid = await check_authentication(user_id)
    
    if not auth_valid:
        # Authentication expired or not found
        if str(user_id) in bot_data.authenticated_users:
            del bot_data.authenticated_users[str(user_id)]
            bot_data.save_to_file()
        
        keyboard = [[InlineKeyboardButton("Authenticate", callback_data="auth")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Your session has expired or you're not authenticated. Please use /start to authenticate.",
            reply_markup=reply_markup
        )
        return
    
    # Update last activity timestamp
    if str(user_id) in bot_data.authenticated_users:
        bot_data.authenticated_users[str(user_id)]["last_activity"] = datetime.now().isoformat()
        bot_data.save_to_file()
    
    # Determine which type of media it is and forward appropriately
    media_type = "Unknown"
    file_id = None
    
    if update.message.photo:
        media_type = "Photo"
        file_id = update.message.photo[-1].file_id  # Get the highest resolution
    elif update.message.document:
        media_type = "Document"
        file_id = update.message.document.file_id
    elif update.message.video:
        media_type = "Video"
        file_id = update.message.video.file_id
    elif update.message.voice:
        media_type = "Voice Message"
        file_id = update.message.voice.file_id
    elif update.message.audio:
        media_type = "Audio"
        file_id = update.message.audio.file_id
    elif update.message.sticker:
        media_type = "Sticker"
        file_id = update.message.sticker.file_id
    
    caption = f"📎 *{media_type} from {user_name}*\n👤 ID: `{user_id}`\n⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
    # Add optional caption from user
    if update.message.caption:
        caption += f"\n\n{update.message.caption}"
    
    # Create reply markup for admin
    keyboard = [
        [InlineKeyboardButton("Reply", callback_data=f"reply_{user_id}")],
        [InlineKeyboardButton("Block User", callback_data=f"block_{user_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send to admin
    try:
        if update.message.photo:
            await context.bot.send_photo(
                chat_id=ADMIN_ID, 
                photo=file_id, 
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        elif update.message.document:
            await context.bot.send_document(
                chat_id=ADMIN_ID, 
                document=file_id, 
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        elif update.message.video:
            await context.bot.send_video(
                chat_id=ADMIN_ID, 
                video=file_id, 
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        elif update.message.voice:
            await context.bot.send_voice(
                chat_id=ADMIN_ID, 
                voice=file_id, 
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        elif update.message.audio:
            await context.bot.send_audio(
                chat_id=ADMIN_ID, 
                audio=file_id, 
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        elif update.message.sticker:
            await context.bot.send_sticker(
                chat_id=ADMIN_ID, 
                sticker=file_id
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"Failed to relay media to admin: {e}")
        await update.message.reply_text("Failed to send media. Please try again later.")
        return
    
    # Send to group if configured
    if GROUP_ID:
        try:
            if update.message.photo:
                await context.bot.send_photo(
                    chat_id=GROUP_ID, 
                    photo=file_id, 
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message.document:
                await context.bot.send_document(
                    chat_id=GROUP_ID, 
                    document=file_id, 
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message.video:
                await context.bot.send_video(
                    chat_id=GROUP_ID, 
                    video=file_id, 
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message.voice:
                await context.bot.send_voice(
                    chat_id=GROUP_ID, 
                    voice=file_id, 
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message.audio:
                await context.bot.send_audio(
                    chat_id=GROUP_ID, 
                    audio=file_id, 
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
            elif update.message.sticker:
                await context.bot.send_sticker(
                    chat_id=GROUP_ID, 
                    sticker=file_id
                )
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            logger.error(f"Failed to relay media to group: {e}")
    
    # Simple acknowledgment with keyboard
    keyboard = [
        [KeyboardButton("📨 Help"), KeyboardButton("📝 Status")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("✓ Media sent", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send help message - admin only."""
    user_id = update.effective_user.id
    
    # Only admin can use this command
    if user_id != ADMIN_ID:
        await update.message.reply_text("This command is only available to the admin.")
        return
    
    help_text = (
        "*Admin Commands:*\n"
        "/start - Start the bot\n"
        "/help - Show this help message\n"
        "/status - Check system status\n"
        "/setupgroup - Set current group as backup\n"
        "/broadcast - Send message to all users\n"
        "/block [user_id] - Block a user\n"
        "/unblock [user_id] - Unblock a user\n"
        "/users - List all authenticated users\n"
        "/setquestion [question|answer] - Set security question\n\n"
        "You can reply to any forwarded message to respond directly to that user."
    )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check system status - admin only."""
    user_id = update.effective_user.id
    
    # Only admin can use this command
    if user_id != ADMIN_ID:
        await update.message.reply_text("This command is only available to the admin.")
        return
    
    user_count = len(bot_data.authenticated_users)
    blocked_count = len(bot_data.blocked_users)
    await update.message.reply_text(
        f"*System Status:*\n"
        f"• You are the admin\n"
        f"• Total users: {user_count}\n"
        f"• Blocked users: {blocked_count}\n"
        f"• Group backup: {'Configured' if GROUP_ID else 'Not configured'}\n"
        f"• Bot uptime: Running",
        parse_mode=ParseMode.MARKDOWN
    )

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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Only admin can interact with these buttons
    if user_id != ADMIN_ID and query.data != "auth":
        await query.answer("You're not authorized to use these controls.")
        return
    
    callback_data = query.data
    
    # Handle authentication button pressed by non-admin user
    if callback_data == "auth":
        await query.answer("Please use /start command to authenticate.")
        await query.message.reply_text("Please use /start command to begin authentication.")
        return
    
    # Reply to user button
    if callback_data.startswith("reply_"):
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
    
    # Terminate session button
    elif callback_data.startswith("terminate_"):
        try:
            target_id = int(callback_data.split("_")[1])
            
            if str(target_id) in bot_data.authenticated_users:
                del bot_data.authenticated_users[str(target_id)]
                bot_data.save_to_file()
                
                await query.answer(f"Session for user {target_id} has been terminated.")
                await query.edit_message_reply_markup(reply_markup=None)
                await query.message.reply_text(f"✅ Session for user {target_id} has been terminated.")
            else:
                await query.answer(f"User {target_id} is not authenticated.")
        except Exception as e:
            logger.error(f"Error terminating session: {e}")
            await query.answer(f"Error terminating session: {e}")
    
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
            else:
                await query.answer(f"User {target_id} is already blocked.")
        except Exception as e:
            logger.error(f"Error blocking user: {e}")
            await query.answer(f"Error blocking user: {e}")

def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add conversation handler for authentication
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AUTHENTICATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, authenticate)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    application.add_handler(conv_handler)
    
    # Command handlers
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("setupgroup", setup_group))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("block", block_user))
    application.add_handler(CommandHandler("unblock", unblock_user))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("setquestion", set_security_question))
    
    # Button callback handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Message handlers - using simplified filters
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay_message))
    
    # Media handler for all non-text messages
    application.add_handler(MessageHandler(~filters.TEXT, handle_media))
    
    # Error handler
    application.add_error_handler(error_handler)
    
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