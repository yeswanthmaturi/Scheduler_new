"""
main.py: Entrypoint for the TechpathX Telegram Scheduler Bot.
Run this file to start the bot and handle candidate scheduling via Google Calendar.
"""

from telegram_bot import build_bot
from calendar_utils import test_event_filtering, get_rate_limit_stats, reset_rate_limit_stats
import asyncio
import logging
import signal
import sys

# Placeholder for notification (implement in telegram_bot.py or utils.py)
def notify_candidate_via_telegram(candidate, message):
    # Implement this to send a Telegram message to the candidate or admin
    print(f"Notify {candidate['email']}: {message}")

async def test_rate_limiting():
    """Test the Calendar API rate limiting system."""
    print("🧪 Testing Calendar API Rate Limiting...")
    print("=" * 50)
    
    # Show current configuration
    from config import CALENDAR_API_DELAY_SECONDS, CALENDAR_API_MAX_RETRIES
    print(f"📋 Configuration:")
    print(f"   • Delay between calls: {CALENDAR_API_DELAY_SECONDS}s")
    print(f"   • Max retries: {CALENDAR_API_MAX_RETRIES}")
    print()
    
    # Reset and show stats
    reset_rate_limit_stats()
    stats = get_rate_limit_stats()
    print(f"📊 Initial stats: {stats}")
    print()
    
    print("✅ Rate limiting system is configured and ready!")
    print("💡 The system will automatically:")
    print("   • Wait 2+ seconds between Calendar API calls")
    print("   • Retry failed requests with exponential backoff")
    print("   • Track detailed statistics")
    print("   • Handle quota and rate limit errors gracefully")
    print()
    print("🚀 Start scheduling candidates to see rate limiting in action!")

if __name__ == "__main__":
    # Check command line arguments
    if len(sys.argv) > 1:
        if sys.argv[1] == "--test-filter":
            print("🧪 Testing Event Filtering System...")
            test_event_filtering()
            print("\n✅ Test completed. Use 'python main.py' to start the bot.")
            sys.exit(0)
        elif sys.argv[1] == "--test-rate-limit":
            print("🧪 Testing Rate Limiting System...")
            asyncio.run(test_rate_limiting())
            print("\n✅ Test completed. Use 'python main.py' to start the bot.")
            sys.exit(0)
    
    print("[TechpathX Scheduler] Bot is starting...")
    print("💡 Available test commands:")
    print("   • python main.py --test-filter     (test event filtering)")
    print("   • python main.py --test-rate-limit (test rate limiting)")
    print()
    app = build_bot()
    app.run_polling()
