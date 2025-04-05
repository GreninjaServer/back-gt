# Telegram Relay Bot

A powerful Telegram bot that relays messages and media between users and an admin, with authentication, group backup, and other advanced features.

## Features

- **Authentication System**: Users must authenticate before using the bot
- **Admin Controls**: Block/unblock users, broadcast messages, view user stats
- **Media Support**: Handles photos, documents, videos, voice messages, and more
- **Group Backup**: All messages can be relayed to a designated backup group
- **Inline Buttons**: Easy-to-use admin interface for replying to users
- **Security**: Customizable security questions for authentication
- **Railway.app Ready**: Optimized for deployment on Railway.app

## Setup Instructions

### Prerequisites
- Python 3.8+
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)
- Your Telegram User ID (you can get this from [@userinfobot](https://t.me/userinfobot))

### Environment Variables Explained
- `BOT_TOKEN`: Your Telegram bot token from BotFather (required)
- `ADMIN_ID`: Your Telegram user ID - the bot admin's ID (required)
- `GROUP_ID`: ID of a group where all messages will be backed up (optional, can be set later using /setupgroup)
- `PORT`: Port for webhook mode when hosted on Railway.app (default: 8443, usually set automatically by Railway)
- `RAILWAY_STATIC_URL`: The URL of your Railway deployment (set automatically by Railway)

### Local Development

1. Clone this repository:
```
git clone https://github.com/yourusername/telegram-relay-bot.git
cd telegram-relay-bot
```

2. Create a virtual environment and install dependencies:
```
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Create a `.env` file with your configuration:
```
BOT_TOKEN=your_bot_token_from_botfather
ADMIN_ID=your_telegram_user_id
GROUP_ID=  # Leave empty, will be set with /setupgroup command
```

4. Run the bot:
```
python main.py
```

### Deployment on Railway.app

1. Fork or clone this repository to your GitHub account.

2. Create a new project on [Railway.app](https://railway.app/).

3. Connect your GitHub repository to Railway.

4. Add the following environment variables in Railway:
   - `BOT_TOKEN`: Your Telegram bot token
   - `ADMIN_ID`: Your Telegram user ID
   - `PORT`: 8443 (or Railway will set this automatically)

5. Deploy the application.

6. Once deployed, Railway will provide you with a domain URL. The bot will automatically use this URL for the webhook.

## Bot Commands

| Command | Description |
|---------|-------------|
| /start | Start the bot and authenticate |
| /help | Show help message |
| /status | Check authentication status |
| /setupgroup | Set current group as backup (admin only) |
| /broadcast | Send message to all users (admin only) |
| /block | Block a user (admin only) |
| /unblock | Unblock a user (admin only) |
| /users | List all authenticated users (admin only) |
| /setquestion | Set security question (admin only) |

## Security Configuration

By default, the bot uses a simple security question. To enhance security:

1. Use the `/setquestion` command as admin to set a new question and answer:
```
/setquestion What is the password?|your_secure_password
```

2. The format is: `/setquestion QUESTION|ANSWER`

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.