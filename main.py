import os
import hmac
import hashlib
from urllib.parse import unquote, parse_qs
from datetime import datetime, timezone
from threading import Thread

import firebase-admin
from firebase_admin import credentials, db
from flask import Flask, request, jsonify
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

load_dotenv()

# --- Configurations ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# Firebase Service Account Key (JSON content) কে Railway Environment Variable হিসেবে রাখুন
FIREBASE_SERVICE_ACCOUNT_KEY = os.getenv("FIREBASE_SERVICE_ACCOUNT_KEY_JSON") 
WEB_APP_URL = os.getenv("WEB_APP_URL") # আপনার GitHub Pages URL

# --- Firebase Initialization ---
cred = credentials.Certificate(eval(FIREBASE_SERVICE_ACCOUNT_KEY)) # eval to parse string to dict
firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://telegam-5ea9f-default-rtdb.asia-southeast1.firebasedatabase.app/' # আপনার Firebase DB URL
})

# --- Rewards & Limits ---
WELCOME_BONUS = 1000
REFERRAL_BONUS = 500
AD_REWARD = 100
DAILY_AD_LIMIT = 400

# --- Flask App for API ---
app = Flask(__name__)

def get_today_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')

def validate_init_data(init_data_str: str) -> dict | None:
    """Validates initData from Telegram WebApp."""
    try:
        # Create a secret key from the bot token
        secret_key = hmac.new("WebAppData".encode(), TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
        
        # Parse the initData string
        parsed_data = parse_qs(init_data_str)
        received_hash = parsed_data.pop('hash')[0]
        
        # Create the data-check-string
        data_check_string = "\n".join(sorted([f"{k}={v[0]}" for k, v in parsed_data.items()]))
        
        # Calculate the hash
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        # Compare hashes
        if calculated_hash == received_hash:
            return {k: v[0] for k, v in parsed_data.items()}
        return None
    except Exception:
        return None

@app.route('/reward_ad', methods=['POST'])
def reward_ad():
    data = request.json
    init_data = data.get('initData')

    validated_data = validate_init_data(init_data)
    if not validated_data:
        return jsonify({"error": "Invalid data. Authentication failed."}), 403

    user_data = eval(validated_data.get('user'))
    user_id = str(user_data['id'])
    
    user_ref = db.reference(f'users/{user_id}')
    user = user_ref.get()

    if not user:
        return jsonify({"error": "User not found."}), 404

    today_str = get_today_str()
    last_watch_date = user.get('last_ad_watch_date', '')
    ads_today = user.get('ads_watched_today', 0)

    # Reset daily count if it's a new day
    if last_watch_date != today_str:
        ads_today = 0
        user_ref.update({'last_ad_watch_date': today_str})

    if ads_today >= DAILY_AD_LIMIT:
        return jsonify({"error": "Daily ad limit reached."}), 429
    
    # Update user's data
    new_balance = user.get('balance', 0) + AD_REWARD
    new_ads_today = ads_today + 1
    
    user_ref.update({
        'balance': new_balance,
        'ads_watched_today': new_ads_today
    })
    
    return jsonify({"message": f"You have been rewarded {AD_REWARD} coins!", "new_balance": new_balance}), 200

# --- Telegram Bot Logic ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = str(user.id)
    user_ref = db.reference(f'users/{user_id}')
    
    is_new_user = not user_ref.get()
    
    if is_new_user:
        new_user_data = {
            'username': user.username,
            'first_name': user.first_name,
            'balance': WELCOME_BONUS,
            'referred_by': None,
            'ads_watched_today': 0,
            'last_ad_watch_date': '2000-01-01'
        }
        
        # Handle referral
        if context.args:
            referrer_id = context.args[0]
            if referrer_id != user_id:
                referrer_ref = db.reference(f'users/{referrer_id}')
                if referrer_ref.get():
                    new_user_data['referred_by'] = referrer_id
                    
                    # Reward referrer
                    referrer_ref.child('balance').transaction(lambda current_balance: (current_balance or 0) + REFERRAL_BONUS)
                    
                    # Add bonus to new user for being referred
                    new_user_data['balance'] += REFERRAL_BONUS
                    
                    # Notify referrer
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎉 Congratulations! {user.first_name} joined using your link. You've received {REFERRAL_BONUS} coins!"
                    )

        user_ref.set(new_user_data)
        welcome_text = (
            f"Welcome, {user.first_name}! 🎁\n\n"
            f"You've received a welcome bonus of {new_user_data['balance']} coins.\n\n"
            "Click the button below to start earning!"
        )
    else:
        welcome_text = f"Welcome back, {user.first_name}! 👋\n\nClick the button below to continue earning."

    keyboard = [[InlineKeyboardButton("🚀 Open Earning App", web_app=WebAppInfo(url=WEB_APP_URL))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, reply_markup=reply_markup)


def run_flask():
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 8080)))

def main() -> None:
    """Start the bot and the Flask server."""
    # Run Flask server in a separate thread
    flask_thread = Thread(target=run_flask)
    flask_thread.start()

    # Create the Telegram Application and pass it your bot's token.
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start))

    # Run the bot until the user presses Ctrl-C
    print("Bot is running...")
    application.run_polling()

if __name__ == "__main__":
    main()
