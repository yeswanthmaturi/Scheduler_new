# scheduler.py

import asyncio
import pytz
from datetime import datetime, timedelta, time
from config import (
    MAIN_INTERVIEWERS,
    BACKUP_INTERVIEWERS,
    ALL_BACKUP_INTERVIEWERS,
    INTERVIEWER_HOURS,
    COURSES,
    MOCK_SCHEDULE,
    MIN_GAP_HOURS,
    MAX_GAP_DAYS,
    MIN_COURSE_GAP_HOURS,
    MAX_MOCKS_PER_INTERVIEWER_PER_DAY
)
from utils import slots_from_list, is_within_any_slot, get_day_type, hours_between, get_candidate_slots_for_day
from calendar_utils import fetch_events, parse_event, create_event_with_retry, get_rate_limit_stats, reset_rate_limit_stats
import os
from dotenv import load_dotenv
import inspect
import itertools

load_dotenv()

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")
PST = pytz.timezone("America/Los_Angeles")

def calculate_calendar_days(start_date, end_date):
    """Calculate total calendar days between start and end dates (inclusive)."""
    return (end_date.date() - start_date.date()).days + 1

async def safe_log(logger, msg):
    if inspect.iscoroutinefunction(logger):
        await logger(msg)
    else:
        logger(msg)

async def process_candidate(candidate, logger=None):
    if logger is None:
        logger = print
    name = candidate['name']
    email = candidate['email']
    start_date = PST.localize(datetime.strptime(candidate['start_date'], "%Y-%m-%d"))
    end_date = PST.localize(datetime.strptime(candidate['end_date'], "%Y-%m-%d"))

    # Reset rate limiting statistics for this candidate
    reset_rate_limit_stats()
    await safe_log(logger, f"[Scheduler] 🔄 Reset rate limiting stats for {name}")

    await safe_log(logger, f"[Scheduler] Fetching events from Google Calendar for {name} between {start_date} and {end_date}...")
    events_result = fetch_events(
        CALENDAR_ID,
        start_date,
        end_date + timedelta(days=1)
    )
    if isinstance(events_result, dict):
        events = events_result.get('events', [])
    else:
        events = events_result
    await safe_log(logger, f"[Scheduler] {len(events)} total events fetched.")
    for ev in events:
        title = ev.get('summary', '')
        start = ev['start'].get('dateTime') or ev['start'].get('date')
        end = ev['end'].get('dateTime') or ev['end'].get('date')
        await safe_log(logger, f"[Scheduler] Raw Event: '{title}' from {start} to {end}")
    
    await safe_log(logger, f"[Scheduler] Parsing and filtering events (only mock interview format will be processed)...")
    parsed_events = [parse_event(ev) for ev in events]
    
    # Count how many events were filtered
    mock_events = [e for e in parsed_events if e.get('is_mock_event', False)]
    filtered_count = len(parsed_events) - len(mock_events)
    
    await safe_log(logger, f"[Scheduler] ✅ {len(mock_events)} mock interview events processed, {filtered_count} other events ignored.")

    interviewer_avail = build_interviewer_availability(parsed_events, start_date, end_date)
    candidate_avail = build_candidate_availability(parsed_events, email, start_date, end_date)

    await safe_log(logger, f"[Scheduler] Starting SOP scheduling for {name}...")
    success = await sop_schedule(candidate, candidate_avail, interviewer_avail, parsed_events, start_date, end_date, logger)
    
    # Log rate limiting statistics
    stats = get_rate_limit_stats()
    await safe_log(logger, f"[Scheduler] 📊 Calendar API Stats: {stats['successful_calls']} successful calls, {stats['delayed_calls']} delayed, {stats['retry_attempts']} retries, {stats['quota_errors']} quota errors")
    
    if not success:
        await safe_log(logger, f"Unable to fully schedule all mocks for {candidate['name']} as per SOP. Manual admin action needed.")
    return success

def build_interviewer_availability(parsed_events, start_date, end_date):
    # For each interviewer, build a map {date: [busy_slots]}
    availability = {}
    
    # Create email to name mapping for all interviewers
    email_to_name = {}
    for course in COURSES:
        for main_or_backup in [MAIN_INTERVIEWERS[course]] + BACKUP_INTERVIEWERS.get(course, []):
            name = main_or_backup['name']
            email = main_or_backup['email']
            email_to_name[email] = name
            if name not in availability:
                availability[name] = {}
    
    # Go over events - ONLY MOCK INTERVIEW EVENTS
    for event in parsed_events:
        # Skip non-mock events (they are invisible to scheduler)
        if not event.get('is_mock_event', False):
            continue
            
        # For each guest who is after the colon and required
        after_colon = event['after_colon']
        required = event['required']
        start = datetime.fromisoformat(event['start'])
        end = datetime.fromisoformat(event['end'])
        # If colon pattern, find interviewer after colon
        if after_colon:
            # E.g., "Kumar FS2 Mock1"
            parts = after_colon.split()
            if parts:
                interviewer_name = parts[0]
                # Check if any required guest email matches this interviewer
                for guest_email in required:
                    if guest_email in email_to_name and email_to_name[guest_email] == interviewer_name:
                        day = start.date()
                        if interviewer_name not in availability:
                            availability[interviewer_name] = {}
                        if day not in availability[interviewer_name]:
                            availability[interviewer_name][day] = []
                        availability[interviewer_name][day].append((start, end))
                        break
    return availability

def build_candidate_availability(parsed_events, candidate_email, start_date, end_date):
    # Build map of busy slots for candidate - ONLY MOCK INTERVIEW EVENTS
    availability = {}
    for single_date in (start_date + timedelta(n) for n in range((end_date - start_date).days + 1)):
        availability[single_date.date()] = []
    for event in parsed_events:
        # Skip non-mock events (they are invisible to scheduler)
        if not event.get('is_mock_event', False):
            continue
            
        if candidate_email in event['required']:
            start = datetime.fromisoformat(event['start'])
            end = datetime.fromisoformat(event['end'])
            day = start.date()
            availability.setdefault(day, []).append((start, end))
    # Mark time slots as per availability (slots is list of (start, end) time)
    # (We filter later when matching mock slots)
    return availability

async def schedule_mock_set(candidate, candidate_avail, interviewer_avail, parsed_events, window_start, end_date,
                           course, mock_durations, scheduled_events, logger=print, dry_run=False):
    """
    Schedule a complete course set (2 or 3 mocks) sequentially with proper 12+ hour gaps.
    Prioritizes main interviewers first across ALL time slots, then falls back to backups.
    """
    
    # Get all possible interviewers for this course (main first, then backups)
    main_interviewer = MAIN_INTERVIEWERS[course]
    backup_interviewers = BACKUP_INTERVIEWERS.get(course, [])
    
    # Cross-course main interviewers: dynamically find other courses with same main interviewer
    cross_course_mains = []
    current_main_name = main_interviewer['name']
    for other_course in COURSES[:-1]:  # Exclude "Own" course
        if other_course != course and MAIN_INTERVIEWERS[other_course]['name'] == current_main_name:
            cross_course_mains.append(MAIN_INTERVIEWERS[other_course])
    
    # Separate main and backup interviewers
    main_interviewers_list = [main_interviewer] + cross_course_mains
    backup_interviewers_list = backup_interviewers
    
    await safe_log(logger, f"[Scheduler] 📅 Scheduling {course} set: {len(mock_durations)} mocks starting from {window_start.strftime('%Y-%m-%d %H:%M')}")
    
    # Schedule each mock in the course set sequentially
    last_mock_end = window_start
    course_scheduled_events = []
    
    for i, duration_min in enumerate(mock_durations):
        # Enforce 12+ hour gap from previous mock
        if i > 0:
            earliest_start = last_mock_end + timedelta(hours=MIN_GAP_HOURS)
        else:
            earliest_start = window_start
        
        await safe_log(logger, f"[Scheduler] 🔍 Looking for {course} Mock {i+1} starting from {earliest_start.strftime('%Y-%m-%d %H:%M')}")
        
        # DAY-BY-DAY Round 1/Round 2: Check each day for main interviewers first, then backup interviewers
        mock_scheduled = False
        best_slot = None
        best_interviewer = None
        best_interviewer_type = None
        
        # Get timezone preference from candidate
        timezone = candidate.get('timezone', 'PST').upper()
        time_preference = 'latest' if timezone == 'PST' else 'earliest'
        
        await safe_log(logger, f"[Scheduler] 🔍 Day-by-day Round 1/2 strategy for {course} Mock {i+1} (timezone: {timezone}, preference: {time_preference})")
        
        # Iterate day by day from earliest_start to end_date
        current_day = earliest_start.date()
        end_day = end_date.date()
        
        while current_day <= end_day and not best_slot:
            await safe_log(logger, f"[Scheduler] 📅 Checking {current_day.strftime('%Y-%m-%d')} ({current_day.strftime('%A')})")
            
            # ROUND 1 for this day: Check main interviewers
            await safe_log(logger, f"[Scheduler] 🥇 Round 1: Checking main interviewers for {current_day.strftime('%Y-%m-%d')}")
            
            # Start from beginning of day or earliest_start if it's the first day
            if current_day == earliest_start.date():
                day_start_time = earliest_start
            else:
                day_start_time = PST.localize(datetime.combine(current_day, time(0, 0)))
            
            day_end_time = PST.localize(datetime.combine(current_day, time(23, 59)))
            current_time = day_start_time
            
            # Collect all valid main interviewer slots for this day
            main_interviewer_slots_today = []
            
            # Try main interviewers for all slots in this day
            while current_time.date() == current_day and current_time <= day_end_time:
                # Check if within candidate's available slots (dynamic based on day type)
                candidate_slots = get_candidate_slots_for_day(candidate, current_time)
                time_in_slot = any(
                    current_time.time() >= slot_start and current_time.time() <= slot_end
                    for slot_start, slot_end in candidate_slots
                )
                
                if time_in_slot and current_day in candidate_avail:
                    # Check candidate availability
                    candidate_free = True
                    mock_end = current_time + timedelta(minutes=duration_min)
                    
                    for busy_start, busy_end in candidate_avail[current_day]:
                        if not (mock_end <= busy_start or current_time >= busy_end):
                            candidate_free = False
                            break
                    
                    if candidate_free:
                        # Try main interviewers for this time slot
                        for interviewer in main_interviewers_list:
                            day_type = get_day_type(current_time)
                            
                            # Build updated availability including current session and course events
                            updated_interviewer_avail = {}
                            for int_name, day_slots in interviewer_avail.items():
                                updated_interviewer_avail[int_name] = {}
                                for date, slots_list in day_slots.items():
                                    updated_interviewer_avail[int_name][date] = slots_list.copy()
                            
                            # Add current session events + this course's events
                            for event in scheduled_events + course_scheduled_events:
                                int_name = event['interviewer']
                                event_date = event['start'].date()
                                if int_name not in updated_interviewer_avail:
                                    updated_interviewer_avail[int_name] = {}
                                if event_date not in updated_interviewer_avail[int_name]:
                                    updated_interviewer_avail[int_name][event_date] = []
                                updated_interviewer_avail[int_name][event_date].append((event['start'], event['end']))
                            
                            # Check interviewer availability
                            if check_interviewer_avail(interviewer, updated_interviewer_avail, current_time, mock_end, day_type, parsed_events):
                                main_interviewer_slots_today.append((current_time, interviewer))
                                await safe_log(logger, f"[Scheduler] 🎯 Found main interviewer {interviewer['name']} at {current_time.strftime('%Y-%m-%d %H:%M')}")
                                break  # Found valid interviewer for this time slot, move to next time slot
                
                # Move to next 30-minute slot
                current_time += timedelta(minutes=30)
            
            # Select best main interviewer slot based on timezone preference
            if main_interviewer_slots_today:
                if time_preference == 'latest':
                    best_slot, best_interviewer = max(main_interviewer_slots_today, key=lambda x: x[0])
                else:
                    best_slot, best_interviewer = min(main_interviewer_slots_today, key=lambda x: x[0])
                best_interviewer_type = "main"
                await safe_log(logger, f"[Scheduler] 🎯 Selected {time_preference} main interviewer slot: {best_interviewer['name']} at {best_slot.strftime('%Y-%m-%d %H:%M')}")
            
            # ROUND 2 for this day: If no main interviewer found, check backup interviewers
            if not best_slot:
                await safe_log(logger, f"[Scheduler] 🥈 Round 2: Checking backup interviewers for {current_day.strftime('%Y-%m-%d')}")
                
                # Reset to start of day
                if current_day == earliest_start.date():
                    day_start_time = earliest_start
                else:
                    day_start_time = PST.localize(datetime.combine(current_day, time(0, 0)))
                
                current_time = day_start_time
                
                # Collect all valid backup interviewer slots for this day
                backup_interviewer_slots_today = []
                
                # Try backup interviewers for all slots in this day
                while current_time.date() == current_day and current_time <= day_end_time:
                    # Check if within candidate's available slots (dynamic based on day type)
                    candidate_slots = get_candidate_slots_for_day(candidate, current_time)
                    time_in_slot = any(
                        current_time.time() >= slot_start and current_time.time() <= slot_end
                        for slot_start, slot_end in candidate_slots
                    )
                    
                    if time_in_slot and current_day in candidate_avail:
                        # Check candidate availability
                        candidate_free = True
                        mock_end = current_time + timedelta(minutes=duration_min)
                        
                        for busy_start, busy_end in candidate_avail[current_day]:
                            if not (mock_end <= busy_start or current_time >= busy_end):
                                candidate_free = False
                                break
                        
                        if candidate_free:
                            # Try backup interviewers for this time slot
                            for interviewer in backup_interviewers_list:
                                day_type = get_day_type(current_time)
                                
                                # Build updated availability including current session and course events
                                updated_interviewer_avail = {}
                                for int_name, day_slots in interviewer_avail.items():
                                    updated_interviewer_avail[int_name] = {}
                                    for date, slots_list in day_slots.items():
                                        updated_interviewer_avail[int_name][date] = slots_list.copy()
                                
                                # Add current session events + this course's events
                                for event in scheduled_events + course_scheduled_events:
                                    int_name = event['interviewer']
                                    event_date = event['start'].date()
                                    if int_name not in updated_interviewer_avail:
                                        updated_interviewer_avail[int_name] = {}
                                    if event_date not in updated_interviewer_avail[int_name]:
                                        updated_interviewer_avail[int_name][event_date] = []
                                    updated_interviewer_avail[int_name][event_date].append((event['start'], event['end']))
                                
                                # Check interviewer availability
                                if check_interviewer_avail(interviewer, updated_interviewer_avail, current_time, mock_end, day_type, parsed_events):
                                    backup_interviewer_slots_today.append((current_time, interviewer))
                                    await safe_log(logger, f"[Scheduler] 🎯 Found backup interviewer {interviewer['name']} at {current_time.strftime('%Y-%m-%d %H:%M')}")
                                    break  # Found valid interviewer for this time slot, move to next time slot
                    
                    # Move to next 30-minute slot
                    current_time += timedelta(minutes=30)
                
                # Select best backup interviewer slot based on timezone preference
                if backup_interviewer_slots_today:
                    if time_preference == 'latest':
                        best_slot, best_interviewer = max(backup_interviewer_slots_today, key=lambda x: x[0])
                    else:
                        best_slot, best_interviewer = min(backup_interviewer_slots_today, key=lambda x: x[0])
                    best_interviewer_type = "backup"
                    await safe_log(logger, f"[Scheduler] 🎯 Selected {time_preference} backup interviewer slot: {best_interviewer['name']} at {best_slot.strftime('%Y-%m-%d %H:%M')}")
            
            # Move to next day
            current_day += timedelta(days=1)
        
        # Schedule the mock if slot found
        if best_slot and best_interviewer:
            mock_end = best_slot + timedelta(minutes=duration_min)
            
            if not dry_run:
                # Create the actual calendar event
                title = f"{candidate['name']} : {best_interviewer['name']} {course} Mock{i+1}"
                required_emails = [candidate['email'], best_interviewer['email']]
                optional_emails = [b['email'] for b in ALL_BACKUP_INTERVIEWERS if b['email'] != best_interviewer['email']]
                
                await safe_log(logger, f"[Scheduler] 🕒 Creating calendar event: '{title}'")
                event_result = await create_event_with_retry(
                    os.getenv("GOOGLE_CALENDAR_ID"),
                    title, best_slot, mock_end, required_emails, optional_emails
                )
                
                if isinstance(event_result, dict) and event_result.get('status') == 'error':
                    error_type = event_result.get('error', '')
                    if 'calendar_quota_exceeded' in str(error_type):
                        await safe_log(logger, f"[Scheduler] ❌ Calendar quota exceeded for event: {title}")
                        return 'calendar_quota_exceeded'
                    elif 'calendar_rate_limited' in str(error_type):
                        await safe_log(logger, f"[Scheduler] ❌ Calendar rate limited for event: {title}")
                        return 'calendar_rate_limited'
                    else:
                        await safe_log(logger, f"[Scheduler] ❌ Calendar event creation failed: {error_type}")
                        return 'calendar_event_error'
                
                # Add to scheduled events
                event_info = {
                    "event_id": event_result.get('event_id') if isinstance(event_result, dict) else None,
                    "title": title,
                    "start": best_slot,
                    "end": mock_end,
                    "interviewer": best_interviewer['name']
                }
                scheduled_events.append(event_info)
                course_scheduled_events.append(event_info)
                
                # Update global availability map
                interviewer_name = best_interviewer['name']
                mock_date = best_slot.date()
                if interviewer_name not in interviewer_avail:
                    interviewer_avail[interviewer_name] = {}
                if mock_date not in interviewer_avail[interviewer_name]:
                    interviewer_avail[interviewer_name][mock_date] = []
                interviewer_avail[interviewer_name][mock_date].append((best_slot, mock_end))
            
            await safe_log(logger, f"[Scheduler] ✅ Scheduled {course} Mock {i+1} with {best_interviewer_type} {best_interviewer['name']} at {best_slot.strftime('%Y-%m-%d %H:%M')}")
            
            last_mock_end = mock_end
            mock_scheduled = True
        
        if not mock_scheduled:
            await safe_log(logger, f"[Scheduler] ❌ Unable to schedule {course} Mock {i+1} within time window")
            return False
    
    await safe_log(logger, f"[Scheduler] 🎉 Successfully scheduled complete {course} set ({len(mock_durations)} mocks)")
    return True

def build_daily_interviewer_slot_table(interviewer_avail, start_date, end_date):
    """
    Build a daily slot usage table for each interviewer.
    Returns: {date: {interviewer_name: {'used_slots': count, 'courses': [course_list]}}}
    """
    daily_table = {}
    
    for single_date in (start_date + timedelta(n) for n in range((end_date - start_date).days + 1)):
        date_key = single_date.date()
        daily_table[date_key] = {}
        
        # Initialize for all interviewers
        for course in COURSES:
            main_interviewer = MAIN_INTERVIEWERS[course]['name']
            if main_interviewer not in daily_table[date_key]:
                daily_table[date_key][main_interviewer] = {'used_slots': 0, 'courses': []}
            if f"{course}(main)" not in daily_table[date_key][main_interviewer]['courses']:
                daily_table[date_key][main_interviewer]['courses'].append(f"{course}(main)")
            
            # Add backup interviewers
            for backup in BACKUP_INTERVIEWERS.get(course, []):
                backup_name = backup['name']
                if backup_name not in daily_table[date_key]:
                    daily_table[date_key][backup_name] = {'used_slots': 0, 'courses': []}
                if f"{course}(backup)" not in daily_table[date_key][backup_name]['courses']:
                    daily_table[date_key][backup_name]['courses'].append(f"{course}(backup)")
        
        # Count existing scheduled slots
        for interviewer_name, day_slots in interviewer_avail.items():
            if date_key in day_slots:
                used_count = len(day_slots[date_key])
                if interviewer_name in daily_table[date_key]:
                    daily_table[date_key][interviewer_name]['used_slots'] = used_count
    
    return daily_table

async def send_daily_availability_analysis(daily_slot_table, start_date, candidate_name, logger):
    """Send daily availability analysis via telegram for first 7 days"""
    await safe_log(logger, f"📊 [DAILY AVAILABILITY ANALYSIS] for {candidate_name}")
    await safe_log(logger, f"📅 Analyzing interviewer availability from {start_date.strftime('%Y-%m-%d')}...")
    
    # Show first 7 days in detail
    for i in range(7):
        date_key = start_date.date() + timedelta(days=i)
        day_name = date_key.strftime('%A')
        
        available_interviewers = []
        busy_interviewers = []
        
        for interviewer_name, data in daily_slot_table[date_key].items():
            max_slots = MAX_MOCKS_PER_INTERVIEWER_PER_DAY
            available_slots = max_slots - data['used_slots']
            
            if available_slots > 0 and data['courses']:
                courses_str = ', '.join(data['courses'])
                available_interviewers.append(f"{interviewer_name}({available_slots} slots)")
            elif data['used_slots'] > 0:
                busy_interviewers.append(f"{interviewer_name}({data['used_slots']} busy)")
        
        await safe_log(logger, f"📅 {date_key} ({day_name}):")
        if available_interviewers:
            await safe_log(logger, f"  ✅ Available: {', '.join(available_interviewers)}")
        if busy_interviewers:
            await safe_log(logger, f"  ❌ Busy: {', '.join(busy_interviewers)}")
        if not available_interviewers and not busy_interviewers:
            await safe_log(logger, f"  ⚪ No working interviewers")

async def sop_schedule(candidate, candidate_avail, interviewer_avail, parsed_events, start_date, end_date, logger=print):
    name = candidate['name']
    email = candidate['email']
    scheduled_events = []
    fs_courses = COURSES[:-1]  # FS1, FS2, FS3, FS4, FS5, FS6
    own_course = COURSES[-1]   # Own

    # Calculate schedule length and determine mode
    total_days = calculate_calendar_days(start_date, end_date)
    is_long_schedule = total_days >= 16
    
    await safe_log(logger, f"[Scheduler] 📅 Schedule length: {total_days} days ({'Long' if is_long_schedule else 'Short'} mode)")

    # Build daily slot usage table
    daily_slot_table = build_daily_interviewer_slot_table(interviewer_avail, start_date, end_date)
    
    # Send daily availability analysis via telegram
    await send_daily_availability_analysis(daily_slot_table, start_date, name, logger)

    await safe_log(logger, f"[Scheduler] 🚀 Starting SOP-compliant sequential course scheduling for {name}...")
    
    # Keep track of scheduled and remaining FS courses
    scheduled_fs_courses = []
    remaining_fs_courses = fs_courses.copy()
    last_course_end = None
    
    # STEP 1: Schedule FS courses one by one using round-robin selection
    while remaining_fs_courses:
        # Determine start window for this course set
        if last_course_end is None:
            window_start = start_date
        else:
            if is_long_schedule:
                # SOP: At least 1 calendar day gap between course sets (≥20 days mode)
                window_start = (last_course_end + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
                window_start = PST.localize(window_start.replace(tzinfo=None))
            else:
                # Short schedule: Only 12+ hour gap between course sets (<20 days mode)
                window_start = last_course_end + timedelta(hours=MIN_GAP_HOURS)
        
        await safe_log(logger, f"[Scheduler] 🔄 Round-robin selection for next FS course from {window_start.strftime('%Y-%m-%d %H:%M')}...")
        await safe_log(logger, f"[Scheduler] 📋 Remaining FS courses: {remaining_fs_courses}")
        
        # Apply round-robin selection among remaining FS courses
        earliest_fs_course = None
        earliest_fs_start = None
        
        # ROUND 1: Check main interviewers only across remaining courses
        await safe_log(logger, f"[Scheduler] 🔄 Round 1: Checking main interviewers across remaining FS courses...")
        
        for course in remaining_fs_courses:
            main_interviewer = MAIN_INTERVIEWERS[course]
            
            # Cross-course main interviewers: dynamically find other courses with same main interviewer
            cross_course_mains = []
            current_main_name = main_interviewer['name']
            for other_course in COURSES[:-1]:  # Exclude "Own" course
                if other_course != course and MAIN_INTERVIEWERS[other_course]['name'] == current_main_name:
                    cross_course_mains.append(MAIN_INTERVIEWERS[other_course])
            
            # Only main interviewers for round 1
            main_interviewers_only = [main_interviewer] + cross_course_mains
            
            # Find earliest possible start for this course with main interviewers only
            course_earliest = await find_earliest_mock_slot(
                candidate, candidate_avail, interviewer_avail, parsed_events,
                window_start, end_date, main_interviewers_only, 90, scheduled_events, course
            )
            
            if course_earliest and (earliest_fs_start is None or course_earliest < earliest_fs_start):
                earliest_fs_course = course
                earliest_fs_start = course_earliest
                await safe_log(logger, f"[Scheduler] 🎯 Found main interviewer slot: {course} at {course_earliest.strftime('%Y-%m-%d %H:%M')}")
        
        # ROUND 2: If no main interviewer available, check backup interviewers
        if not earliest_fs_course:
            await safe_log(logger, f"[Scheduler] 🔄 Round 2: No main interviewers available, checking backup interviewers...")
            
            for course in remaining_fs_courses:
                backup_interviewers = BACKUP_INTERVIEWERS.get(course, [])
                
                if backup_interviewers:
                    # Find earliest possible start for this course with backup interviewers only
                    course_earliest = await find_earliest_mock_slot(
                        candidate, candidate_avail, interviewer_avail, parsed_events,
                        window_start, end_date, backup_interviewers, 90, scheduled_events, course
                    )
                    
                    if course_earliest and (earliest_fs_start is None or course_earliest < earliest_fs_start):
                        earliest_fs_course = course
                        earliest_fs_start = course_earliest
                        await safe_log(logger, f"[Scheduler] 🎯 Found backup interviewer slot: {course} at {course_earliest.strftime('%Y-%m-%d %H:%M')}")
        
        if not earliest_fs_course:
            await safe_log(logger, f"[Scheduler] ❌ No remaining FS course can be scheduled from {window_start}")
            return False
        
        await safe_log(logger, f"[Scheduler] 🎯 Selected {earliest_fs_course} as next course (earliest start: {earliest_fs_start.strftime('%Y-%m-%d %H:%M')})")
        
        # Determine mock durations: FS5 and FS6 get 1 mock (105 min), first FS course gets 3/2 mocks, others get 2 mocks
        if earliest_fs_course == 'FS5':
            mock_durations = MOCK_SCHEDULE['FS5']
        elif earliest_fs_course == 'FS6':
            mock_durations = MOCK_SCHEDULE['FS6']
        elif not scheduled_fs_courses:  # First FS course
            if is_long_schedule:
                mock_durations = MOCK_SCHEDULE['FSX_FIRST']  # 3 mocks for ≥20 days
            else:
                mock_durations = MOCK_SCHEDULE['FSX_FIRST_SHORT']  # 2 mocks for <20 days
        else:  # Subsequent FS courses
            mock_durations = MOCK_SCHEDULE['FSX_OTHER']
        
        # Schedule this complete course set
        success = await schedule_mock_set(
            candidate, candidate_avail, interviewer_avail, parsed_events,
            window_start, end_date, earliest_fs_course, mock_durations,
            scheduled_events, logger, dry_run=False
        )
        
        if not success:
            await safe_log(logger, f"[Scheduler] ❌ Failed to schedule {earliest_fs_course} set. Stopping.")
            return False
        
        # Update tracking lists
        scheduled_fs_courses.append(earliest_fs_course)
        remaining_fs_courses.remove(earliest_fs_course)
        
        # Update last course end time for gap calculation
        course_events = [e for e in scheduled_events if earliest_fs_course in e['title']]
        if course_events:
            last_course_end = max(e['end'] for e in course_events)
            await safe_log(logger, f"[Scheduler] ✅ {earliest_fs_course} set completed. Last mock ends at {last_course_end.strftime('%Y-%m-%d %H:%M')}")
    
    # STEP 2: Schedule Own course (always last)
    own_mock_count = "3" if is_long_schedule else "2"
    await safe_log(logger, f"[Scheduler] 📋 Scheduling {own_course} set ({own_mock_count} mocks)...")
    
    # Determine start window for Own course
    if is_long_schedule:
        window_start = (last_course_end + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = PST.localize(window_start.replace(tzinfo=None))
        own_mock_durations = MOCK_SCHEDULE['OWN']  # 3 mocks for ≥20 days
    else:
        window_start = last_course_end + timedelta(hours=MIN_GAP_HOURS)
        own_mock_durations = MOCK_SCHEDULE['OWN_SHORT']  # 2 mocks for <20 days
    
    # Schedule Own course set
    success = await schedule_mock_set(
        candidate, candidate_avail, interviewer_avail, parsed_events,
        window_start, end_date, own_course, own_mock_durations,
        scheduled_events, logger, dry_run=False
    )
    
    if not success:
        await safe_log(logger, f"[Scheduler] ❌ Failed to schedule {own_course} set. Stopping.")
        return False
    
    await safe_log(logger, f"[Scheduler] 🎉 ALL {len(scheduled_events)} MOCKS SUCCESSFULLY SCHEDULED for {name}!")
    await safe_log(logger, f"[Scheduler] ✅ SOP-compliant sequential scheduling complete.")
    return True

async def find_earliest_mock_slot(candidate, candidate_avail, interviewer_avail, parsed_events, 
                                 start_date, end_date, interviewers, duration_min, scheduled_events, course=None):
    """Find the best slot for a single mock with given interviewers based on candidate timezone preference"""
    
    # Get timezone preference from candidate
    timezone = candidate.get('timezone', 'PST').upper()
    time_preference = 'latest' if timezone == 'PST' else 'earliest'
    
    # Separate main and backup interviewers
    from config import MAIN_INTERVIEWERS, BACKUP_INTERVIEWERS
    main_interviewer_names = set()
    if course:
        # Add main interviewer for this specific course
        main_interviewer_names.add(MAIN_INTERVIEWERS[course]['name'])
        # Add cross-course main interviewers
        for other_course, main_int in MAIN_INTERVIEWERS.items():
            if other_course != course and main_int['name'] == MAIN_INTERVIEWERS[course]['name']:
                main_interviewer_names.add(main_int['name'])
    
    main_interviewer_slots = []
    backup_interviewer_slots = []
    
    # Scan from start_date to end_date in 30-minute increments
    current_time = start_date
    
    while current_time <= end_date:
        # Check if current time falls within candidate's available slots (dynamic based on day type)
        current_date = current_time.date()
        candidate_slots = get_candidate_slots_for_day(candidate, current_time)
        time_in_any_slot = any(
            current_time.time() >= slot_start and current_time.time() <= slot_end
            for slot_start, slot_end in candidate_slots
        )
        
        if time_in_any_slot and current_date in candidate_avail:
            # Check candidate availability at this specific time
            candidate_free = True
            for busy_start, busy_end in candidate_avail[current_date]:
                if not (current_time + timedelta(minutes=duration_min) <= busy_start or current_time >= busy_end):
                    candidate_free = False
                    break
            
            if candidate_free:
                mock_end = current_time + timedelta(minutes=duration_min)
                
                # Try each interviewer
                for interviewer in interviewers:
                    day_type = get_day_type(current_time)
                    
                    # Build updated availability including current session
                    updated_interviewer_avail = {}
                    for int_name, day_slots in interviewer_avail.items():
                        updated_interviewer_avail[int_name] = {}
                        for date, slots_list in day_slots.items():
                            updated_interviewer_avail[int_name][date] = slots_list.copy()
                    
                    # Add current session events
                    for event in scheduled_events:
                        int_name = event['interviewer']
                        event_date = event['start'].date()
                        if int_name not in updated_interviewer_avail:
                            updated_interviewer_avail[int_name] = {}
                        if event_date not in updated_interviewer_avail[int_name]:
                            updated_interviewer_avail[int_name][event_date] = []
                        updated_interviewer_avail[int_name][event_date].append((event['start'], event['end']))
                    
                    # Check availability
                    if check_interviewer_avail(interviewer, updated_interviewer_avail, current_time, mock_end, day_type, parsed_events):
                        # Determine if this is a main or backup interviewer
                        if interviewer['name'] in main_interviewer_names:
                            main_interviewer_slots.append((current_time, interviewer))
                        else:
                            backup_interviewer_slots.append((current_time, interviewer))
        
        # Move to next 30-minute slot
        current_time += timedelta(minutes=30)
    
    # Select best slot based on timezone preference and interviewer priority
    # Priority 1: Main interviewers
    if main_interviewer_slots:
        if time_preference == 'latest':
            return max(main_interviewer_slots, key=lambda x: x[0])[0]
        else:
            return min(main_interviewer_slots, key=lambda x: x[0])[0]
    
    # Priority 2: Backup interviewers (only if no main interviewer slots)
    if backup_interviewer_slots:
        if time_preference == 'latest':
            return max(backup_interviewer_slots, key=lambda x: x[0])[0]
        else:
            return min(backup_interviewer_slots, key=lambda x: x[0])[0]
    
    return None

# Helper to generate possible start times within a slot

def generate_possible_start_times(slot_start, slot_end, duration_min, day_dt, increment_min=30):
    """Yield all possible start datetimes within slot on a given day, at given increment. All datetimes are LA-localized and DST-aware."""
    # slot_start and slot_end are naive time objects
    # day_dt is a localized datetime at midnight (PST/PDT)
    naive_start = datetime.combine(day_dt.date(), slot_start)
    naive_end = datetime.combine(day_dt.date(), slot_end)
    start_dt = PST.localize(naive_start)
    end_dt = PST.localize(naive_end)
    while start_dt + timedelta(minutes=duration_min) <= end_dt:
        yield start_dt
        start_dt += timedelta(minutes=increment_min)

def is_interviewer_busy(interviewer, events, mock_start, mock_end):
    interviewer_name = interviewer['name']
    for event in events:
        # Skip non-mock events (they are invisible to scheduler)
        if not event.get('is_mock_event', False):
            continue
            
        event_start = datetime.fromisoformat(event['start'])
        event_end = datetime.fromisoformat(event['end'])
        if not (mock_end <= event_start or mock_start >= event_end):
            after_colon = event.get('after_colon', '')
            # Busy only if their name appears after the colon in the event title
            if after_colon and interviewer_name in after_colon:
                return True
    return False

def check_interviewer_avail_with_scheduled(interviewer, interviewer_avail, mock_start, mock_end, day_type, parsed_events=None, scheduled_events=None):
    """
    Check interviewer availability considering both original calendar events and current session's scheduled events
    """
    name = interviewer['name']
    
    # Check basic availability using original function
    if not check_interviewer_avail(interviewer, interviewer_avail, mock_start, mock_end, day_type, parsed_events):
        return False
    
    # Additionally check conflicts with current session's scheduled events
    if scheduled_events:
        for event in scheduled_events:
            # Check if this interviewer is already scheduled during this time
            if (event['interviewer'] == name and 
                not (mock_end <= event['start'] or mock_start >= event['end'])):
                return False
            
        # Check daily limit: count how many events this interviewer has on this date
        daily_count = sum(1 for e in scheduled_events 
                        if e['interviewer'] == name and e['start'].date() == mock_start.date())
        if daily_count >= MAX_MOCKS_PER_INTERVIEWER_PER_DAY:
            return False
    
    return True

def check_interviewer_avail(interviewer, interviewer_avail, mock_start, mock_end, day_type, parsed_events=None):
    name = interviewer['name']
    
    # Check if mock crosses midnight - reject if it does
    if mock_end.date() != mock_start.date():
        return False
    
    # In working hours?
    wh = INTERVIEWER_HOURS[name].get(day_type, [])
    in_working = any(
        mock_start.time() >= time(int(start), int((start%1)*60)) and
        mock_end.time() <= time(int(end), int((end%1)*60))
        for start, end in wh
    )
    if not in_working:
        return False
    # Not already busy (overlap, SOP colon rules)
    busy_slots = interviewer_avail.get(name, {}).get(mock_start.date(), [])
    for b_start, b_end in busy_slots:
        if not (mock_end <= b_start or mock_start >= b_end):
            return False
    # --- SOP: Max 2 mocks per interviewer per day ---
    scheduled_today = len(busy_slots)
    if scheduled_today >= MAX_MOCKS_PER_INTERVIEWER_PER_DAY:
        return False
    # --- SOP: Triple-check rule for backup interviewer assignment ---
    if parsed_events is not None:
        if is_interviewer_busy(interviewer, parsed_events, mock_start, mock_end):
            return False
    # --- Nikhil's Sunday 9PM-11:59PM priority ---
    if name == "Nikhil" and mock_start.weekday() == 6:  # Sunday
        if not (mock_start.time() >= time(21, 0) and mock_end.time() <= time(23, 59)):
            return False
    return True

def check_candidate_avail(candidate, candidate_avail, mock_start, mock_end):
    busy_slots = candidate_avail.get(mock_start.date(), [])
    for b_start, b_end in busy_slots:
        if not (mock_end <= b_start or mock_start >= b_end):
            return False
    return True