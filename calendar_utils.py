# calendar_utils.py

import os
from dotenv import load_dotenv
import pytz
import datetime
import re
import asyncio
import time
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from config import ALL_BACKUP_INTERVIEWERS, MAIN_INTERVIEWERS, BACKUP_INTERVIEWERS, COURSES, CALENDAR_API_DELAY_SECONDS, CALENDAR_API_MAX_RETRIES
import uuid

load_dotenv()

# Rate limiting for Calendar API
_last_api_call_time = 0
_rate_limit_stats = {
    'total_calls': 0,
    'delayed_calls': 0,
    'retry_attempts': 0,
    'quota_errors': 0,
    'rate_limit_errors': 0,
    'successful_calls': 0
}

def get_rate_limit_stats():
    """Get current rate limiting statistics."""
    return _rate_limit_stats.copy()

def reset_rate_limit_stats():
    """Reset rate limiting statistics."""
    global _rate_limit_stats
    _rate_limit_stats = {
        'total_calls': 0,
        'delayed_calls': 0,
        'retry_attempts': 0,
        'quota_errors': 0,
        'rate_limit_errors': 0,
        'successful_calls': 0
    }

async def rate_limited_sleep():
    """Ensure minimum delay between Calendar API calls to avoid operational limits."""
    global _last_api_call_time, _rate_limit_stats
    current_time = time.time()
    time_since_last_call = current_time - _last_api_call_time
    
    if time_since_last_call < CALENDAR_API_DELAY_SECONDS:
        sleep_time = CALENDAR_API_DELAY_SECONDS - time_since_last_call
        print(f"[RATE LIMIT] Waiting {sleep_time:.1f}s before next Calendar API call...")
        _rate_limit_stats['delayed_calls'] += 1
        await asyncio.sleep(sleep_time)
    
    _last_api_call_time = time.time()
    _rate_limit_stats['total_calls'] += 1

async def create_event_with_retry(calendar_id, title, start_dt, end_dt, required_emails, optional_emails, max_retries=None):
    """Create a Google Calendar event with rate limiting and retry logic."""
    global _rate_limit_stats
    
    if max_retries is None:
        max_retries = CALENDAR_API_MAX_RETRIES
    
    for attempt in range(max_retries + 1):
        try:
            # Rate limiting: ensure minimum delay between calls
            await rate_limited_sleep()
            
            if attempt > 0:
                _rate_limit_stats['retry_attempts'] += 1
                print(f"[CALENDAR] Creating event '{title}' (retry {attempt}/{max_retries})")
            else:
                print(f"[CALENDAR] Creating event '{title}'")
            
            service = get_google_service()
            if not service:
                return {'status': 'error', 'error': 'Google service unavailable', 'event_id': None}
            
            # Generate a unique requestId for the Meet link
            request_id = str(uuid.uuid4())
            event = {
                'summary': title,
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'America/Los_Angeles'},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'America/Los_Angeles'},
                'attendees': [{'email': e, 'optional': False} for e in required_emails]
                              + [{'email': e, 'optional': True} for e in optional_emails],
                'reminders': {'useDefault': True},
                'conferenceData': {
                    'createRequest': {
                        'requestId': request_id,
                        'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                    }
                }
            }
            
            event = service.events().insert(
                calendarId=calendar_id,
                body=event,
                sendUpdates="all",
                conferenceDataVersion=1
            ).execute()
            
            _rate_limit_stats['successful_calls'] += 1
            print(f"[CALENDAR] ✅ Event created successfully: {event.get('id')}")
            return {'status': 'ok', 'event_id': event.get('id')}
            
        except HttpError as e:
            error_details = str(e)
            print(f"[CALENDAR] ❌ HttpError on attempt {attempt + 1}: {error_details}")
            
            # Check for quota/rate limit errors
            if e.resp.status == 403:
                if 'quotaExceeded' in error_details or 'usageLimits' in error_details:
                    _rate_limit_stats['quota_errors'] += 1
                    if attempt < max_retries:
                        # Exponential backoff for quota errors
                        retry_delay = (2 ** attempt) * 5  # 5, 10, 20 seconds
                        print(f"[CALENDAR] 🔄 Quota exceeded, retrying in {retry_delay}s...")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        print(f"[CALENDAR] 💥 Max retries reached for quota error")
                        return {'status': 'error', 'error': 'calendar_quota_exceeded', 'event_id': None}
            
            elif e.resp.status == 429:  # Too Many Requests
                _rate_limit_stats['rate_limit_errors'] += 1
                if attempt < max_retries:
                    retry_delay = (2 ** attempt) * 3  # 3, 6, 12 seconds
                    print(f"[CALENDAR] 🔄 Rate limited, retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    print(f"[CALENDAR] 💥 Max retries reached for rate limit")
                    return {'status': 'error', 'error': 'calendar_rate_limited', 'event_id': None}
            
            # For other HTTP errors, don't retry
            print(f"[CALENDAR] 💥 Non-retryable error: {error_details}")
            return {'status': 'error', 'error': str(e), 'event_id': None}
            
        except Exception as e:
            print(f"[CALENDAR] ❌ Unexpected error on attempt {attempt + 1}: {str(e)}")
            if attempt < max_retries:
                retry_delay = 2 * (attempt + 1)  # 2, 4, 6 seconds
                print(f"[CALENDAR] 🔄 Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                continue
            else:
                print(f"[CALENDAR] 💥 Max retries reached for unexpected error")
                return {'status': 'error', 'error': str(e), 'event_id': None}
    
    return {'status': 'error', 'error': 'Max retries exceeded', 'event_id': None}

def create_event(calendar_id, title, start_dt, end_dt, required_emails, optional_emails):
    """
    Backwards compatibility wrapper for create_event_with_retry.
    NOTE: This is synchronous and doesn't include the full rate limiting benefits.
    Use create_event_with_retry directly for better performance.
    """
    import asyncio
    
    # Run the async function in a new event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(
        create_event_with_retry(calendar_id, title, start_dt, end_dt, required_emails, optional_emails)
    )

def is_mock_interview_event(title):
    """
    Check if event title matches the exact mock interview format:
    'Candidate name : Interviewer name Course Mock#'
    
    Returns True only for valid mock interview events, False for all others.
    """
    if not title or ':' not in title:
        return False
    
    # Split by colon
    parts = title.split(':', 1)
    if len(parts) != 2:
        return False
    
    candidate_part = parts[0].strip()
    after_colon = parts[1].strip()
    
    if not candidate_part or not after_colon:
        return False
    
    # Parse after colon: should be "Interviewer Course Mock#"
    after_parts = after_colon.split()
    if len(after_parts) != 3:
        return False
    
    interviewer_name, course, mock_part = after_parts
    
    # Validate course is in our list
    if course not in COURSES:
        return False
    
    # Validate mock format (Mock1, Mock2, Mock3)
    mock_pattern = r'^Mock[123]$'
    if not re.match(mock_pattern, mock_part):
        return False
    
    # Validate interviewer name exists in our config
    all_interviewer_names = set()
    
    # Add main interviewers
    for course_config in MAIN_INTERVIEWERS.values():
        all_interviewer_names.add(course_config['name'])
    
    # Add backup interviewers
    for backup_list in BACKUP_INTERVIEWERS.values():
        for backup in backup_list:
            all_interviewer_names.add(backup['name'])
    
    if interviewer_name not in all_interviewer_names:
        return False
    
    return True

def get_google_service():
    """Create and return a Google Calendar API service client."""
    try:
        creds = Credentials(
            token=None,
            refresh_token=os.getenv("GOOGLE_REFRESH_TOKEN"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET")
        )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f"get_google_service error: {str(e)}")
        return None

def fetch_events(calendar_id, time_min, time_max):
    """Fetch events from Google Calendar between time_min and time_max."""
    try:
        service = get_google_service()
        if not service:
            return {'status': 'error', 'error': 'Google service unavailable', 'events': []}
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        return {'status': 'ok', 'events': events_result.get('items', [])}
    except Exception as e:
        print(f"fetch_events error: {str(e)}")
        return {'status': 'error', 'error': str(e), 'events': []}

def parse_event(event):
    """Parse a Google Calendar event into a dict with title, start, end, guests, and colon info."""
    try:
        title = event.get('summary', '')
        
        # FILTER: Only process mock interview events
        if not is_mock_interview_event(title):
            print(f"[EVENT FILTER] Ignoring non-mock event: '{title}'")
            return {
                "title": title,
                "candidate": None,
                "after_colon": None,
                "start": None,
                "end": None,
                "required": [],
                "optional": [],
                "is_mock_event": False
            }
        
        print(f"[EVENT FILTER] Processing mock interview event: '{title}'")
        guests = event.get('attendees', [])
        required = [g['email'] for g in guests if not g.get('optional', False)]
        optional = [g['email'] for g in guests if g.get('optional', False)]
        # Handle both dateTime and date (all-day events)
        start = event['start'].get('dateTime') or event['start'].get('date')
        end = event['end'].get('dateTime') or event['end'].get('date')
        # Colon split
        colon = title.split(":", 1)
        candidate = colon[0].strip() if len(colon) == 2 else None
        after_colon = colon[1].strip() if len(colon) == 2 else None
        return {
            "title": title,
            "candidate": candidate,
            "after_colon": after_colon,
            "start": start,
            "end": end,
            "required": required,
            "optional": optional,
            "is_mock_event": True
        }
    except Exception as e:
        print(f"parse_event error: {str(e)} | event: {event}")
        return {
            "title": None,
            "candidate": None,
            "after_colon": None,
            "start": None,
            "end": None,
            "required": [],
            "optional": [],
            "is_mock_event": False
        }

def test_event_filtering():
    """Test function to demonstrate event filtering behavior."""
    test_titles = [
        "Manish1 : Kumar FS3 Mock1",      # ✅ Valid mock event
        "Manish : Kumar intro call",      # ❌ Not mock format
        "Manish2 : Ram FS2 Mock2",        # ✅ Valid mock event  
        "Manish : Kumar FS4 Mock4",       # ❌ Invalid mock number
        "Manish3 : Shaya Own Mock1",      # ✅ Valid mock event
        "Manish : Kumar meeting",         # ❌ Not mock format
        "Meeting with team",              # ❌ No colon
        "Manish4 : Nikhil FS1 Mock3",     # ✅ Valid mock event
        "Manish : UnknownInterviewer FS1 Mock1",  # ❌ Unknown interviewer
    ]
    
    print("\n🧪 [EVENT FILTERING TEST]")
    print("=" * 50)
    
    for title in test_titles:
        result = is_mock_interview_event(title)
        status = "✅ ACCEPTED" if result else "❌ IGNORED"
        print(f"{status}: '{title}'")
    
    print("=" * 50)
    print("📋 Only ACCEPTED events will be processed by scheduler/deletion logic")
    return

def delete_event(calendar_id, event_id):
    """Delete a Google Calendar event by its ID."""
    try:
        service = get_google_service()
        if not service:
            return {'status': 'error', 'error': 'Google service unavailable'}
        
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates="all"
        ).execute()
        
        return {'status': 'ok'}
    except Exception as e:
        print(f"delete_event error: {str(e)} | event_id: {event_id}")
        return {'status': 'error', 'error': str(e)}

def find_candidate_events(calendar_id, candidate_name, time_min, time_max):
    """Find all MOCK INTERVIEW events for a specific candidate between given dates using colon strategy."""
    try:
        # Fetch all events in the date range
        events_result = fetch_events(calendar_id, time_min, time_max)
        if events_result['status'] != 'ok':
            return {'status': 'error', 'error': events_result['error'], 'events': []}
        
        candidate_events = []
        for event in events_result['events']:
            parsed_event = parse_event(event)
            
            # FILTER: Only consider mock interview events for deletion
            if not parsed_event.get('is_mock_event', False):
                continue
            
            # Check if candidate name matches (case-insensitive)
            if (parsed_event['candidate'] and 
                parsed_event['candidate'].lower().strip() == candidate_name.lower().strip()):
                candidate_events.append({
                    'event_id': event.get('id'),
                    'title': parsed_event['title'],
                    'start': parsed_event['start'],
                    'end': parsed_event['end'],
                    'candidate': parsed_event['candidate'],
                    'after_colon': parsed_event['after_colon']
                })
        
        return {'status': 'ok', 'events': candidate_events}
    except Exception as e:
        print(f"find_candidate_events error: {str(e)}")
        return {'status': 'error', 'error': str(e), 'events': []}
