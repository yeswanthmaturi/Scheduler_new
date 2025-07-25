# TechpathX Mock Interview Scheduler

SOP-driven, fully automated mock interview scheduler using Python, Google Calendar, Telegram, and Supabase.

## Features

- Admin-only Telegram bot to onboard candidate details
- Automatic scheduling of all SOP-compliant mock interviews with interviewer mapping, backup fallback, working hours, and double-booking prevention
- **Smart Calendar API rate limiting** with automatic retry and exponential backoff
- Google Calendar integration (event fetch, event creation)
- Supabase backend for candidate queue, audit logs, and escalation tracking
- Escalation and error notification via Telegram and Supabase logs

## Calendar API Rate Limiting

The scheduler includes intelligent rate limiting to prevent Google Calendar quota issues:

### Features:
- **2-second delays** between Calendar API calls (configurable)
- **Automatic retry** with exponential backoff for failed requests
- **Detailed statistics** tracking (successful calls, retries, errors)
- **Quota-aware error handling** with specific user feedback

### Configuration:
In `config.py`, you can adjust:
```python
CALENDAR_API_DELAY_SECONDS = 2.0  # Delay between API calls
CALENDAR_API_MAX_RETRIES = 3      # Maximum retry attempts
```

### What This Solves:
- **403 Forbidden quotaExceeded errors**
- **429 Too Many Requests errors** 
- **Google Calendar operational limits**
- **Rapid event creation issues**

The system will automatically handle rate limiting and provide clear feedback to users about any calendar-related issues.

## Folder Structure

techpath_mock_scheduler
├── main.py # Entrypoint
├── telegram_bot.py # Telegram intake and admin chat
├── calendar_utils.py # Google Calendar API logic
├── scheduler.py # SOP scheduling engine
├── supabase_db.py # Supabase DB interface
├── config.py # Interviewer, course, hours mapping
├── utils.py # Helpers (time, parsing)
├── requirements.txt
└── .env # All API keys and credentials (DO NOT commit!)


## Quick Start

### 1. Clone repo and install dependencies

```bash
pip install -r requirements.txt
2. Set up .env
Copy the template below and fill with your own keys:

ini
Copy
Edit
TELEGRAM_BOT_TOKEN=...
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_REFRESH_TOKEN=...
GOOGLE_CALENDAR_ID=...
To get your GOOGLE_REFRESH_TOKEN:

Go to Google API Console, create OAuth credentials for Calendar API.

Run this helper script:


from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file('credentials.json', scopes=['https://www.googleapis.com/auth/calendar'])
creds = flow.run_local_server(port=0)
print('Refresh token:', creds.refresh_token)
Paste the result into .env.

3. Create Supabase Tables
Paste each block from the "Supabase Table Schema Definitions" above in the SQL editor.

4. Start the Bot

python main.py
Bot will listen for /start from admin(s) in Telegram

Enter candidate details step by step (name, email, available date window, time slots in 09:00-12:00, 16:00-19:00 format)

The candidate is queued, scheduling engine fetches events, parses all constraints, and creates Google Calendar events as per SOP

Progress/errors/escalations are logged to Supabase and (if needed) reported to Telegram admin

How It Works
One candidate at a time is scheduled; further candidates are queued.

SOP rules enforced: working hours, sequence, min/max gaps, interviewer mapping, backup logic, double-booking checks.

All actions are audit-logged to Supabase (scheduling_audit).

Escalations or manual review triggers are logged to escalations and optionally messaged in Telegram.

Troubleshooting
If you see no calendar events: check your Google credentials, refresh token, and that your bot has access to the Google Calendar.

All scheduling exceptions/escalations are logged to Supabase.

For time zone errors, confirm that your times and slots are all in PST.

Support
For questions, DM @your_admin on Telegram or open an issue in your code repository.



---

**If you want, I can generate a helper script for Google OAuth refresh token as well—just ask!**  
Let me know if you want more detailed docs, code for a test candidate, or a Telegram admin escalation message template!







