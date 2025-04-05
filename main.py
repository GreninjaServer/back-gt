import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
GROUP_ID = os.getenv("GROUP_ID")  # Will be set up if not provided

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Dictionary to track authenticated users
authenticated_users = {}
security_questions = {
    "What's your secret phrase?": "your_secret_answer_here"  # Replace with your secret answer
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            "Welcome back, Admin! You're already authenticated."
        )
        authenticated_users[user_id] = True
    else:
        # Ask security question
        question = list(security_questions.keys())[0]
        await update.message.reply_text(
            f"Welcome to the Relay Bot. Please authenticate yourself.\n\n{question}"
        )

async def authenticate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle authentication attempts."""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Admin is always authenticated
    if user_id == ADMIN_ID:
        authenticated_users[user_id] = True
        await update.message.reply_text("You're the admin, always authenticated!")
        return
    
    # Check if the answer matches
    question = list(security_questions.keys())[0]
    correct_answer = security_questions[question]
    
    if message_text.lower() == correct_answer.lower():
        authenticated_users[user_id] = True
        await update.message.reply_text("Authentication successful! You can now use the bot.")
    else:
        await update.message.reply_text("Authentication failed. Please try again.")

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
    
    await update.message.reply_text(f"Backup group has been set up with ID: {GROUP_ID}")
    await update.message.reply_text("This group will now receive all messages sent to the bot.")

async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Relay messages to admin and group."""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Check if message is in private chat
    if update.effective_chat.type != "private":
        # Don't relay messages from groups unless it's a setup command
        return
    
    # If it's the admin, just acknowledge receipt
    if user_id == ADMIN_ID:
        await update.message.reply_text("Message received!")
        return
    
    # Check if user is authenticated
    if user_id not in authenticated_users or not authenticated_users[user_id]:
        await update.message.reply_text(
            "You're not authenticated. Please use /start to authenticate."
        )
        return
    
    # Relay the message to admin
    message_content = update.message.text
    forwarded_message = f"Message from {user_name} (ID: {user_id}):\n\n{message_content}"
    
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=forwarded_message
    )
    
    # If group ID is configured, also relay to group
    if GROUP_ID:
        try:
            await context.bot.send_message(
                chat_id=GROUP_ID,
                text=forwarded_message
            )
        except Exception as e:
            logger.error(f"Failed to send message to group: {e}")
            # Notify admin of the failure
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"Failed to send message to group: {e}"
            )
    
    # Acknowledge receipt
    await update.message.reply_text("Message relayed to the admin!")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle media messages (photos, documents, etc.)"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Check if message is in private chat
    if update.effective_chat.type != "private":
        # Don't relay media from groups
        return
    
    # Check if user is authenticated
    if user_id != ADMIN_ID and (user_id not in authenticated_users or not authenticated_users[user_id]):
        await update.message.reply_text(
            "You're not authenticated. Please use /start to authenticate."
        )
        return
    
    # Determine which type of media it is and forward appropriately
    caption = f"Media from {user_name} (ID: {user_id})"
    if update.message.photo:
        photo = update.message.photo[-1]  # Get the highest resolution
        await context.bot.send_photo(ADMIN_ID, photo.file_id, caption=caption)
        if GROUP_ID:
            await context.bot.send_photo(GROUP_ID, photo.file_id, caption=caption)
    elif update.message.document:
        document = update.message.document
        await context.bot.send_document(ADMIN_ID, document.file_id, caption=caption)
        if GROUP_ID:
            await context.bot.send_document(GROUP_ID, document.file_id, caption=caption)
    elif update.message.video:
        video = update.message.video
        await context.bot.send_video(ADMIN_ID, video.file_id, caption=caption)
        if GROUP_ID:
            await context.bot.send_video(GROUP_ID, video.file_id, caption=caption)
    elif update.message.voice:
        voice = update.message.voice
        await context.bot.send_voice(ADMIN_ID, voice.file_id, caption=caption)
        if GROUP_ID:
            await context.bot.send_voice(GROUP_ID, voice.file_id, caption=caption)
    
    await update.message.reply_text("Media relayed to the admin!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /help is issued."""
    help_text = (
        "Available commands:\n"
        "/start - Start the bot and authenticate\n"
        "/help - Show this help message\n"
        "/status - Check your authentication status\n"
        "/setupgroup - Set current group as backup (admin only)\n\n"
        "Just send any message and I'll relay it to the admin!"
    )
    await update.message.reply_text(help_text)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check authentication status."""
    user_id = update.effective_user.id
    
    if user_id == ADMIN_ID:
        await update.message.reply_text("You are the admin. Always authenticated.")
    elif user_id in authenticated_users and authenticated_users[user_id]:
        await update.message.reply_text("You are authenticated. You can use the bot.")
    else:
        await update.message.reply_text("You are not authenticated. Use /start to authenticate.")

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("setupgroup", setup_group))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, relay_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.DOCUMENT | filters.VIDEO | filters.VOICE, handle_media))

    # Start the Bot
    application.run_polling()

if __name__ == "__main__":
    main()