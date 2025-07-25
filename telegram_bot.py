# telegram_bot.py

import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from dotenv import load_dotenv
from scheduler import process_candidate
from calendar_utils import find_candidate_events, delete_event
import logging
import re
from datetime import datetime
import pytz

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
logging.basicConfig(level=logging.INFO)

# Telegram Intake States  
NAME, EMAIL, START, END, WEEKDAY_SLOTS, WEEKEND_SLOTS, TIMEZONE, CANCEL = range(8)

# Delete States
DELETE_NAME, DELETE_START, DELETE_END, DELETE_CONFIRM = range(8, 12)

def is_valid_email(email):
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def is_valid_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def is_valid_slots(slots_str):
    # e.g., 09:00-12:00, 16:00-19:00
    slot_pattern = r"^([01]?\d|2[0-3]):[0-5]\d-([01]?\d|2[0-3]):[0-5]\d(,\s*([01]?\d|2[0-3]):[0-5]\d-([01]?\d|2[0-3]):[0-5]\d)*$"
    return re.match(slot_pattern, slots_str.replace(' ', ''))

def is_valid_timezone(timezone_str):
    # Only EST, CST, PST are supported
    valid_timezones = ['EST', 'CST', 'PST']
    return timezone_str.strip().upper() in valid_timezones

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Candidate intake cancelled.")
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("TechpathX Scheduler: Enter candidate's full name (or /cancel to exit):")
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name cannot be empty. Please enter candidate's full name:")
        return NAME
    context.user_data['name'] = name
    await update.message.reply_text("Enter candidate's email address:")
    return EMAIL

async def get_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    if not is_valid_email(email):
        await update.message.reply_text("Invalid email format. Please enter a valid email address:")
        return EMAIL
    context.user_data['email'] = email
    await update.message.reply_text("Enter available start date (YYYY-MM-DD):")
    return START

async def get_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_date = update.message.text.strip()
    if not is_valid_date(start_date):
        await update.message.reply_text("Invalid date format. Please enter start date as YYYY-MM-DD:")
        return START
    context.user_data['start_date'] = start_date
    await update.message.reply_text("Enter available end date (YYYY-MM-DD):")
    return END

async def get_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    end_date = update.message.text.strip()
    if not is_valid_date(end_date):
        await update.message.reply_text("Invalid date format. Please enter end date as YYYY-MM-DD:")
        return END
    # Check end date after start date
    start_date = context.user_data['start_date']
    if datetime.strptime(end_date, "%Y-%m-%d") < datetime.strptime(start_date, "%Y-%m-%d"):
        await update.message.reply_text("End date must be after start date. Please enter a valid end date:")
        return END
    context.user_data['end_date'] = end_date
    await update.message.reply_text("Enter available time slots for WEEKDAYS (Monday-Friday) (comma-separated, 24h format, e.g., 09:00-12:00, 16:00-19:00) in PST:")
    return WEEKDAY_SLOTS

async def get_weekday_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    weekday_slots = update.message.text.strip()
    if not is_valid_slots(weekday_slots):
        await update.message.reply_text("Invalid slot format. Please enter weekday slots as e.g., 09:00-12:00, 16:00-19:00:")
        return WEEKDAY_SLOTS
    context.user_data['weekday_slots'] = weekday_slots
    await update.message.reply_text("Enter available time slots for WEEKENDS (Saturday-Sunday) (comma-separated, 24h format, e.g., 10:00-14:00, 18:00-22:00) in PST:")
    return WEEKEND_SLOTS

async def get_weekend_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    weekend_slots = update.message.text.strip()
    if not is_valid_slots(weekend_slots):
        await update.message.reply_text("Invalid slot format. Please enter weekend slots as e.g., 10:00-14:00, 18:00-22:00:")
        return WEEKEND_SLOTS
    context.user_data['weekend_slots'] = weekend_slots
    await update.message.reply_text("Enter candidate's timezone. Supported timezones: EST, CST, PST:")
    return TIMEZONE

async def get_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    timezone = update.message.text.strip().upper()
    if not is_valid_timezone(timezone):
        await update.message.reply_text("Please respond with the correct timezone. Supported timezones: EST, CST, PST:")
        return TIMEZONE
    context.user_data['timezone'] = timezone
    # Show summary for confirmation
    summary = (
        f"Please confirm the following details:\n"
        f"Name: {context.user_data['name']}\n"
        f"Email: {context.user_data['email']}\n"
        f"Start Date: {context.user_data['start_date']}\n"
        f"End Date: {context.user_data['end_date']}\n"
        f"Weekday Slots (PST): {context.user_data['weekday_slots']}\n"
        f"Weekend Slots (PST): {context.user_data['weekend_slots']}\n"
        f"Timezone: {context.user_data['timezone']}\n"
        "Reply 'yes' to confirm and submit, or 'no' to cancel."
    )
    await update.message.reply_text(summary)
    return CANCEL

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Your chat ID is: {chat_id}")
    print(f"[Admin Chat ID] The chat ID for this chat is: {chat_id}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 TechpathX Scheduler Bot Commands:

📅 /start - Schedule new candidate mocks
🗑️ /delete - Delete candidate events by name and date range
🆔 /get_chat_id - Get your chat ID for admin notifications
❌ /cancel - Cancel current operation
❓ /help - Show this help message

For scheduling: Follow the prompts to enter candidate details, weekday availability, and weekend availability.
For deletion: Enter candidate name and date range to find and delete their events.
"""
    await update.message.reply_text(help_text)

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_chat_id_env = os.getenv("ADMIN_CHAT_ID")
    try:
        ADMIN_CHAT_ID = int(admin_chat_id_env)
    except (TypeError, ValueError):
        ADMIN_CHAT_ID = None

    async def telegram_logger(msg):
        print(msg)
        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
            except Exception as e:
                print(f"[Logger Error] Failed to send message to ADMIN_CHAT_ID {ADMIN_CHAT_ID}: {e}")
        else:
            print("[Logger Warning] ADMIN_CHAT_ID is not set or invalid. Set it in your .env file after using /get_chat_id.")

    response = update.message.text.strip().lower()
    if response == 'yes':
        candidate = {
            "name": context.user_data['name'],
            "email": context.user_data['email'],
            "start_date": context.user_data['start_date'],
            "end_date": context.user_data['end_date'],
            "weekday_slots": context.user_data['weekday_slots'],
            "weekend_slots": context.user_data['weekend_slots'],
            "timezone": context.user_data['timezone']
        }
        # Call scheduling directly, passing the logger
        result = await process_candidate(candidate, logger=telegram_logger)
        if result == 'calendar_quota_exceeded':
            await update.message.reply_text("⚠️ Scheduling failed due to Google Calendar quota limits. The system will automatically retry with delays. Please try again in a few minutes.")
        elif result == 'calendar_rate_limited':
            await update.message.reply_text("⚠️ Scheduling was rate limited by Google Calendar. Please try again in a few minutes.")
        elif result == 'calendar_event_error':
            await update.message.reply_text("❌ Calendar event creation failed. Please contact admin for assistance.")
        elif result:
            await update.message.reply_text("✅ Candidate submitted and mocks scheduled successfully!")
        else:
            await update.message.reply_text("❌ Scheduling failed as per SOP constraints. Please contact admin to review availability.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Submission cancelled.")
        return ConversationHandler.END

# Delete functionality handlers
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🗑️ Delete Candidate Events\n\nEnter candidate's name (or /cancel to exit):")
    return DELETE_NAME

async def delete_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    candidate_name = update.message.text.strip()
    if not candidate_name:
        await update.message.reply_text("Name cannot be empty. Please enter candidate's name:")
        return DELETE_NAME
    context.user_data['delete_candidate_name'] = candidate_name
    await update.message.reply_text(f"Enter start date to search from (YYYY-MM-DD):")
    return DELETE_START

async def delete_get_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_date = update.message.text.strip()
    if not is_valid_date(start_date):
        await update.message.reply_text("Invalid date format. Please enter start date as YYYY-MM-DD:")
        return DELETE_START
    context.user_data['delete_start_date'] = start_date
    await update.message.reply_text("Enter end date to search until (YYYY-MM-DD):")
    return DELETE_END

async def delete_get_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    end_date = update.message.text.strip()
    if not is_valid_date(end_date):
        await update.message.reply_text("Invalid date format. Please enter end date as YYYY-MM-DD:")
        return DELETE_END
    
    # Check end date after start date
    start_date = context.user_data['delete_start_date']
    if datetime.strptime(end_date, "%Y-%m-%d") < datetime.strptime(start_date, "%Y-%m-%d"):
        await update.message.reply_text("End date must be after start date. Please enter a valid end date:")
        return DELETE_END
    
    context.user_data['delete_end_date'] = end_date
    
    # Search for candidate events
    await update.message.reply_text("🔍 Searching for candidate events...")
    
    candidate_name = context.user_data['delete_candidate_name']
    calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
    
    # Convert dates to PST timezone
    pst = pytz.timezone("America/Los_Angeles")
    start_dt = pst.localize(datetime.strptime(start_date, "%Y-%m-%d"))
    end_dt = pst.localize(datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59))
    
    # Find candidate events
    result = find_candidate_events(calendar_id, candidate_name, start_dt, end_dt)
    
    if result['status'] != 'ok':
        await update.message.reply_text(f"❌ Error searching for events: {result['error']}")
        return ConversationHandler.END
    
    events = result['events']
    context.user_data['delete_events'] = events
    
    if not events:
        await update.message.reply_text(f"✅ No events found for candidate '{candidate_name}' between {start_date} and {end_date}.")
        return ConversationHandler.END
    
    # Show found events for confirmation
    event_list = f"📅 Found {len(events)} event(s) for '{candidate_name}':\n\n"
    for i, event in enumerate(events, 1):
        start_time = event['start']
        if 'T' in start_time:  # DateTime format
            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            formatted_time = dt.strftime('%Y-%m-%d %H:%M')
        else:  # Date only format
            formatted_time = start_time
        
        event_list += f"{i}. {event['title']}\n   📅 {formatted_time}\n   👤 Interviewer: {event['after_colon']}\n\n"
    
    event_list += "⚠️ Are you sure you want to DELETE all these events?\n\nReply 'yes' to confirm deletion, or 'no' to cancel."
    
    await update.message.reply_text(event_list)
    return DELETE_CONFIRM

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_chat_id_env = os.getenv("ADMIN_CHAT_ID")
    try:
        ADMIN_CHAT_ID = int(admin_chat_id_env)
    except (TypeError, ValueError):
        ADMIN_CHAT_ID = None

    async def telegram_logger(msg):
        print(msg)
        if ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
            except Exception as e:
                print(f"[Logger Error] Failed to send message to ADMIN_CHAT_ID {ADMIN_CHAT_ID}: {e}")

    response = update.message.text.strip().lower()
    if response == 'yes':
        events = context.user_data.get('delete_events', [])
        candidate_name = context.user_data['delete_candidate_name']
        calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
        
        await update.message.reply_text("🗑️ Deleting events...")
        await telegram_logger(f"[DELETE] Starting deletion of {len(events)} events for candidate '{candidate_name}'")
        
        deleted_count = 0
        failed_count = 0
        
        for event in events:
            result = delete_event(calendar_id, event['event_id'])
            if result['status'] == 'ok':
                deleted_count += 1
                await telegram_logger(f"[DELETE] ✅ Deleted: {event['title']}")
            else:
                failed_count += 1
                await telegram_logger(f"[DELETE] ❌ Failed to delete: {event['title']} - Error: {result['error']}")
        
        # Summary message
        summary = f"🗑️ Deletion Summary for '{candidate_name}':\n✅ Successfully deleted: {deleted_count} events\n"
        if failed_count > 0:
            summary += f"❌ Failed to delete: {failed_count} events"
        else:
            summary += "🎉 All events deleted successfully!"
        
        await update.message.reply_text(summary)
        await telegram_logger(f"[DELETE] {summary}")
        
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Deletion cancelled.")
        return ConversationHandler.END

async def delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Delete operation cancelled.")
    return ConversationHandler.END

def build_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Scheduling conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)],
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_email)],
            START: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_start)],
            END: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_end)],
            WEEKDAY_SLOTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_weekday_slots)],
            WEEKEND_SLOTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_weekend_slots)],
            TIMEZONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_timezone)],
            CANCEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Delete conversation handler
    delete_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('delete', delete_start)],
        states={
            DELETE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_get_name)],
            DELETE_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_get_start)],
            DELETE_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_get_end)],
            DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm)],
        },
        fallbacks=[CommandHandler('cancel', delete_cancel)],
    )
    
    app.add_handler(conv_handler)
    app.add_handler(delete_conv_handler)
    app.add_handler(CommandHandler('get_chat_id', get_chat_id))
    app.add_handler(CommandHandler('help', help_command))
    return app
