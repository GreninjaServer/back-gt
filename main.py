import os
import logging
import re
import json
from datetime import datetime
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
AWAITING_AUTH_REPLY = 1

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
        bot_data.authenticated_users[str(user_id)] = {
            "name": user_name,
            "timestamp": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "is_admin": True
        }
        bot_data.save_to_file()
        await update.message.reply_text(
            "Welcome back! You're authenticated as admin."
        )
        return ConversationHandler.END
    
    # Check if already authenticated
    if str(user_id) in bot_data.authenticated_users:
        # Check if authentication has expired (15 minute timeout)
        user_data = bot_data.authenticated_users.get(str(user_id), {})
        last_activity = user_data.get("last_activity", "")
        
        if last_activity and await check_authentication(user_id, last_activity):
            # Update the last activity timestamp
            bot_data.authenticated_users[str(user_id)]["last_activity"] = datetime.now().isoformat()
            bot_data.save_to_file()
            
            await update.message.reply_text(
                "You're already authenticated. Session refreshed."
            )
            return ConversationHandler.END
        else:
            # Authentication expired, remove from authenticated users
            if str(user_id) in bot_data.authenticated_users:
                del bot_data.authenticated_users[str(user_id)]
                bot_data.save_to_file()
    
    # Ask for authentication
    question = list(bot_data.security_questions.keys())[0]
    # Store the message_id in context to verify the reply
    context.user_data['auth_message_id'] = (await update.message.reply_text(
        f"To authenticate, please answer this question:\n\n{question}\n\n"
        f"Your answer will be deleted immediately for security."
    )).message_id
    
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
        # Authenticate the user - IMPORTANT: Store as string key
        bot_data.authenticated_users[str(user_id)] = {
            "name": user_name,
            "timestamp": datetime.now().isoformat(),
            "last_activity": datetime.now().isoformat(),
            "is_admin": False
        }
        bot_data.save_to_file()
        
        success_msg = await update.message.reply_text(
            "Authentication successful! You can now use the bot to send messages to the admin."
        )
        
        # Schedule deletion of success message
        asyncio.create_task(delete_message_after_delay(success_msg, 10))
        
        # Notify admin about new authentication with terminate button
        keyboard = [
            [InlineKeyboardButton("Terminate Session", callback_data=f"terminate_{user_id}")],
            [InlineKeyboardButton("Block User", callback_data=f"block_{user_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ *Alert: New user authenticated*\n"
                 f"• Name: {user_name}\n"
                 f"• ID: `{user_id}`\n"
                 f"• Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        
        return ConversationHandler.END
    else:
        error_msg = await update.message.reply_text(
            "Authentication failed. Please try again with /start"
        )
        
        # Schedule deletion of error message
        asyncio.create_task(delete_message_after_delay(error_msg, 5))
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
    
    # If it's the admin, handle admin commands or acknowledge
    if user_id == ADMIN_ID:
        # Check if replying to a forwarded message
        if update.message.reply_to_message:
            # Try to extract the original sender's ID from the forwarded message
            try:
                original_message = update.message.reply_to_message.text
                match = re.search(r"ID: (\d+)", original_message)
                if match:
                    target_id = int(match.group(1))
                    reply_text = update.message.text
                    
                    # Send the admin's reply to the original sender
                    try:
                        await context.bot.send_message(
                            chat_id=target_id,
                            text=f"*Reply from Admin:*\n\n{reply_text}",
                            parse_mode=ParseMode.MARKDOWN
                        )
                        await update.message.reply_text("✅ Reply sent to user!")
                    except Exception as e:
                        logger.error(f"Failed to send reply: {e}")
                        await update.message.reply_text(f"❌ Failed to send reply: {e}")
                else:
                    await update.message.reply_text("Could not determine the original sender.")
            except Exception as e:
                await update.message.reply_text(f"Error processing reply: {e}")
        else:
            # Regular message from admin, just acknowledge
            await update.message.reply_text("Message received.")
        return
    
    # Check if user is authenticated - IMPORTANT FIX: Convert user_id to str for dictionary lookup
    if str(user_id) not in bot_data.authenticated_users:
        await update.message.reply_text("You need to authenticate first. Please use /start command.")
        return
    
    # Check if authentication has expired (15 minute timeout)
    user_data = bot_data.authenticated_users.get(str(user_id), {})
    last_activity = user_data.get("last_activity", "")
    
    if not last_activity or not await check_authentication(user_id, last_activity):
        # Authentication expired
        if str(user_id) in bot_data.authenticated_users:
            del bot_data.authenticated_users[str(user_id)]
            bot_data.save_to_file()
        
        await update.message.reply_text("Your session has expired. Please authenticate again with /start")
        return
    
    # Update last activity timestamp
    bot_data.authenticated_users[str(user_id)]["last_activity"] = datetime.now().isoformat()
    bot_data.save_to_file()
    
    # Relay the message content directly to admin
    message_content = update.message.text
    
    # Send plain message to admin WITHOUT metadata header
    admin_msg = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=message_content
    )
    
    # Log the message metadata in the group if configured
    if GROUP_ID:
        try:
            # Send detailed message to group
            group_message = (
                f"📨 *Message from {user_name}*\n"
                f"👤 ID: `{user_id}`\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"{message_content}"
            )
            group_msg = await context.bot.send_message(
                chat_id=GROUP_ID,
                text=group_message,
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Store message mapping for /showme command
            if not hasattr(context.bot_data, 'message_map'):
                context.bot_data['message_map'] = {}
            
            # Map the admin message ID to the group message ID
            context.bot_data['message_map'][str(admin_msg.message_id)] = {
                'chat_id': GROUP_ID,
                'message_id': group_msg.message_id,
                'sender_id': user_id,
                'sender_name': user_name
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
    confirm_msg = await update.message.reply_text("Message sent")
    
    # Delete confirmation after a short delay
    asyncio.create_task(delete_message_after_delay(confirm_msg, 3))
    
    # Delete the original message from the user for privacy
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete user message: {e}")

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
        
        # Remove the -100 prefix for the URL
        if str(group_id).startswith('-100'):
            clean_group_id = str(group_id)[4:]
        else:
            clean_group_id = str(group_id)
        
        # Create the message link
        message_link = f"https://t.me/c/{clean_group_id}/{group_msg_id}"
        
        # Create clickable button to view the message
        keyboard = [[InlineKeyboardButton("View Message Details", url=message_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Get sender info if available
        sender_name = group_info.get('sender_name', 'Unknown')
        sender_id = group_info.get('sender_id', 'Unknown')
        
        await update.message.reply_text(
            f"*Message Info:*\n"
            f"• From: {sender_name}\n"
            f"• ID: `{sender_id}`\n\n"
            f"Click the button below to view full details in the backup group:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text(
            "Could not find the detailed message in the backup group."
        )

async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display available commands"""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        commands_text = (
            "*Available Commands:*\n\n"
            "*Basic Commands:*\n"
            "/start - Start the bot and authenticate\n"
            "/help - Show help message\n"
            "/status - Check system status\n"
            "/cmd - Show this command list\n\n"
            
            "*Admin Commands:*\n"
            "/setupgroup - Set current group as backup group\n"
            "/broadcast [message] - Send message to all users\n"
            "/block [user_id] - Block a user\n"
            "/unblock [user_id] - Unblock a user\n"
            "/users - List all authenticated users\n"
            "/setquestion [question|answer] - Set security question\n"
            "/showme - Reply to a message to see its backup version\n\n"
            
            "*Message Controls:*\n"
            "• Reply to any message to send a response to that user\n"
            "• Use inline buttons for quick actions\n"
            "• Session validity: 15 minutes"
        )
    else:
        commands_text = (
            "*Available Commands:*\n\n"
            "/start - Start the bot and authenticate\n"
            "/status - Check your authentication status\n\n"
            
            "Just send a message to relay it to the admin."
        )
    
    await update.message.reply_text(commands_text, parse_mode=ParseMode.MARKDOWN)

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
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
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
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button callbacks."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Only admin can interact with these buttons, except auth button
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
                    await context.bot.send_message(
                        chat_id=target_id,
                        text="Your session has been terminated by the admin. Please authenticate again if needed."
                    )
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
    
    # If it's the admin, handle admin commands or acknowledge
    if user_id == ADMIN_ID:
        # Just acknowledge
        await update.message.reply_text("Media received.")
        return
    
    # Check if user is authenticated
    if str(user_id) not in bot_data.authenticated_users:
        await update.message.reply_text("You need to authenticate first. Please use /start command.")
        return
    
    # Check if authentication has expired (15 minute timeout)
    user_data = bot_data.authenticated_users.get(str(user_id), {})
    last_activity = user_data.get("last_activity", "")
    
    if not last_activity or not await check_authentication(user_id, last_activity):
        # Authentication expired
        if str(user_id) in bot_data.authenticated_users:
            del bot_data.authenticated_users[str(user_id)]
            bot_data.save_to_file()
        
        await update.message.reply_text("Your session has expired. Please authenticate again with /start")
        return
    
    # Update last activity timestamp
    bot_data.authenticated_users[str(user_id)]["last_activity"] = datetime.now().isoformat()
    bot_data.save_to_file()
    
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
            admin_msg = await update.message.forward(chat_id=ADMIN_ID)
        
        # Send detailed info to group if configured
        if GROUP_ID and admin_msg:
            group_info = (
                f"📨 *{media_type} from {user_name}*\n"
                f"👤 ID: `{user_id}`\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            if caption:
                group_info += f"\n\n*Caption:* {caption}"
            
            group_msg = await context.bot.send_message(
                chat_id=GROUP_ID,
                text=group_info,
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Store message mapping for /showme command
            if not hasattr(context.bot_data, 'message_map'):
                context.bot_data['message_map'] = {}
            
            # Map the admin message ID to the group message ID
            context.bot_data['message_map'][str(admin_msg.message_id)] = {
                'chat_id': GROUP_ID,
                'message_id': group_msg.message_id,
                'sender_id': user_id,
                'sender_name': user_name
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
        await update.message.reply_text("Failed to send media. Please try again later.")
        return
    
    # Send confirmation that will be deleted
    confirm_msg = await update.message.reply_text(f"{media_type} sent")
    
    # Delete confirmation after a short delay
    asyncio.create_task(delete_message_after_delay(confirm_msg, 3))
    
    # Delete the original message from the user for privacy
    try:
        await update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete user media: {e}")

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
        ("status", "Check system status"),
        ("cmd", "Show command list"),
        ("setupgroup", "Set current group as backup group"),
        ("broadcast", "Send message to all users"),
        ("block", "Block a user"),
        ("unblock", "Unblock a user"),
        ("users", "List all authenticated users"),
        ("setquestion", "Set security question"),
        ("showme", "Show message details in backup group")
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
    """Check system status or user authentication status."""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        # Admin gets system status
        num_authenticated = len(bot_data.authenticated_users)
        num_blocked = len(bot_data.blocked_users)
        
        # Format uptime - with Windows compatibility
        try:
            # Try Linux-style uptime
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                uptime_str = str(datetime.timedelta(seconds=uptime_seconds)).split('.')[0]
        except FileNotFoundError:
            # Fall back to a different method for Windows
            try:
                if HAS_PSUTIL:
                    uptime_seconds = time.time() - psutil.boot_time()
                    uptime_str = str(datetime.timedelta(seconds=int(uptime_seconds))).split('.')[0]
                else:
                    uptime_str = "Unknown (psutil not installed)"
            except:
                uptime_str = "Unknown"
        except:
            uptime_str = "Unknown"
            
        # Get bot version info
        version_info = "1.0.0"  # You can update this manually or use a version file
        
        status_text = (
            "*System Status:*\n"
            f"• Authenticated users: {num_authenticated}\n"
            f"• Blocked users: {num_blocked}\n"
            f"• System uptime: {uptime_str}\n"
            f"• Bot version: {version_info}\n"
            f"• Bot status: Running ✅"
        )
        
        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)
    else:
        # Regular users get authentication status
        if str(user_id) in bot_data.authenticated_users:
            user_data = bot_data.authenticated_users[str(user_id)]
            auth_time = datetime.fromisoformat(user_data.get("timestamp", ""))
            last_activity = datetime.fromisoformat(user_data.get("last_activity", ""))
            
            # Update last activity time
            bot_data.authenticated_users[str(user_id)]["last_activity"] = datetime.now().isoformat()
            bot_data.save_to_file()
            
            # Calculate remaining time
            now = datetime.now()
            time_diff = now - last_activity
            remaining_mins = max(0, 15 - int(time_diff.total_seconds() / 60))
            
            status_text = (
                "*Your Authentication Status:*\n"
                f"• Status: Authenticated ✅\n"
                f"• Authenticated since: {auth_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"• Session expires in: {remaining_mins} minutes\n"
            )
            
            await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(
                "*Your Authentication Status:*\n"
                "• Status: Not authenticated ❌\n\n"
                "Please use /start to authenticate.",
                parse_mode=ParseMode.MARKDOWN
            )

async def delete_message_after_delay(message, delay_seconds):
    """Delete a message after a specified delay."""
    await asyncio.sleep(delay_seconds)
    try:
        await message.delete()
    except Exception as e:
        # Don't log common errors like "message to delete not found"
        if "message to delete not found" not in str(e).lower():
            logger.warning(f"Could not delete message: {e}")

def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add conversation handler for authentication with proper states
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AWAITING_AUTH_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, authenticate)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
    application.add_handler(conv_handler)
    
    # Command handlers
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
    
    # Button callback handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Message handlers - using simplified filters
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay_message))
    
    # Media handler for all non-text messages
    application.add_handler(MessageHandler(~filters.TEXT, handle_media))
    
    # Error handler
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