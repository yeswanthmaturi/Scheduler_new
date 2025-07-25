# utils.py

import re
import pytz
from datetime import datetime, timedelta, time
from config import PST

def normalize_slot_str(slot_str):
    """Normalize slot string to HH:MM-HH:MM format, zero-padding if needed."""
    slot_str = slot_str.strip()
    match = re.match(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", slot_str)
    if not match:
        return None
    h1, m1, h2, m2 = [int(x) for x in match.groups()]
    return f"{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}"

def parse_time_slot(slot_str):
    """Parse a slot like '09:00-12:00' or '9:00-12:00' to (start_time, end_time) as naive time objects (no tzinfo)."""
    slot_str = slot_str.strip()
    match = re.match(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", slot_str)
    if not match:
        print(f"Bad slot string: {slot_str}")
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    t1 = time(h1, m1)  # naive time
    t2 = time(h2, m2)  # naive time
    if t2 <= t1:
        print(f"End time must be after start time in slot: {slot_str}")
        return None
    return (t1, t2)

def slots_from_list(slots_str):
    """Convert a comma-separated slot string to a list of (start_time, end_time) tuples."""
    slots = []
    for s in slots_str.split(","):
        slot = parse_time_slot(s)
        if slot:
            slots.append(slot)
    return slots

def is_within_any_slot(dt_start, dt_end, slots):
    """Checks if dt_start/dt_end fall inside any provided time slots."""
    for slot_start, slot_end in slots:
        if slot_start <= dt_start.time() and dt_end.time() <= slot_end:
            return True
    return False

def is_weekend(dt):
    """Return True if dt is Saturday or Sunday."""
    return dt.weekday() in [5,6]

def next_weekday(dt, weekday):
    """Return the next datetime that is the given weekday (0=Monday)."""
    days_ahead = weekday - dt.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return dt + timedelta(days=days_ahead)

def get_day_type(dt):
    """Return 'Weekday', 'Saturday', or 'Sunday' for a datetime."""
    wd = dt.weekday()
    if wd == 5: return "Saturday"
    if wd == 6: return "Sunday"
    return "Weekday"

def hours_between(dt1, dt2):
    """Return the absolute number of hours between two datetimes."""
    return abs((dt2 - dt1).total_seconds()) / 3600

def get_candidate_slots_for_day(candidate, current_datetime):
    """Get appropriate candidate slots (weekday or weekend) based on the day type."""
    day_type = get_day_type(current_datetime)
    
    # For backward compatibility, check if candidate has old 'slots' format
    if 'slots' in candidate:
        return slots_from_list(candidate['slots'])
    
    # Use new weekday/weekend format
    if day_type in ["Saturday", "Sunday"]:
        return slots_from_list(candidate.get('weekend_slots', ''))
    else:  # Weekday
        return slots_from_list(candidate.get('weekday_slots', ''))
