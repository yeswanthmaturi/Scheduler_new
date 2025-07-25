# config.py

import pytz
from datetime import time

# PST Timezone
PST = pytz.timezone("America/Los_Angeles")

# Main Interviewers & Courses
MAIN_INTERVIEWERS = {
    "FS1": {"name": "Chandu", "email": "chandu.techpathai@gmail.com"},
    "FS2": {"name": "Ram", "email": "ram.m500062@gmail.com"},
    "FS3": {"name": "Kumar", "email": "kumar.techpath@gmail.com"},
    "FS4": {"name": "Shaya", "email": "shaya.techpath@gmail.com"},
    "FS5": {"name": "Shaya", "email": "shaya.techpath@gmail.com"},
    "FS6": {"name": "Chandu", "email": "chandu.techpathai@gmail.com"},
    "Own": {"name": "Vani", "email": "vani.techpath@gmail.com"},
}

# Backup/Core Team (per course)
BACKUP_INTERVIEWERS = {
    "FS1": [
        {"name": "Harshith", "email": "dwserviceoffice@gmail.com"},
        {"name": "Nikhil", "email": "techpath.mocks@gmail.com"},
    ],
    "FS2": [
        {"name": "Gowtham", "email": "techpathaimocks@gmail.com"},
        {"name": "Harshith", "email": "dwserviceoffice@gmail.com"},
    ],
    "FS3": [
        {"name": "Chandu", "email": "chandu.techpathai@gmail.com"},
        {"name": "Harshith", "email": "dwserviceoffice@gmail.com"},
    ],
    "FS4": [
        {"name": "Chandu", "email": "chandu.techpathai@gmail.com"},
        {"name": "Harshith", "email": "dwserviceoffice@gmail.com"},
    ],
    "FS5": [
        {"name": "Chandu", "email": "chandu.techpathai@gmail.com"},
        {"name": "Harshith", "email": "dwserviceoffice@gmail.com"},
    ],
    "FS6": [
        {"name": "Harshith", "email": "dwserviceoffice@gmail.com"},
    ],
    "Own": [
        {"name": "Gowtham", "email": "techpathaimocks@gmail.com"},
    ]
}

# All core team for optional invite
ALL_BACKUP_INTERVIEWERS = [
    {"name": "Harshitha", "email": "harshithatechpath@gmail.com"},
    {"name": "Harshith", "email": "dwserviceoffice@gmail.com"},
    {"name": "Gowtham", "email": "techpathaimocks@gmail.com"},
    {"name": "Nikhil", "email": "techpath.mocks@gmail.com"},
]

# Working hours (PST, 24h format)
# Format: {'Weekday': [(start1, end1), (start2, end2)], 'Saturday': [...], 'Sunday': [...]}
INTERVIEWER_HOURS = {
    "Kumar": {
        "Weekday": [(18, 21)],    # Add friday 5-8pmm- config later
        "Saturday": [(8, 12), (18, 21)],
        "Sunday": [(8, 12), (18, 21)],
    },
    "Ram": {
        "Weekday": [(18, 21)],
        "Saturday": [(11, 14), (18, 21)],
        "Sunday": [(11, 14), (18, 21)],
    },
    "Shaya": {
        "Weekday": [(19, 22)],
        "Saturday": [(11, 14), (18, 21)],
        "Sunday": [(11, 14), (18, 21)],
    },
    "Vani": {
        "Weekday": [(18, 21)],
        "Saturday": [(11, 14), (18, 21)],
        "Sunday": [(11, 14), (18, 21)],
    },
    "Chandu": {
        "Weekday": [(18, 23)],
        "Saturday": [(10, 14)],
        "Sunday": [(11, 14), (19, 23)],
    },
    "Harshith": {
        "Weekday": [(18, 23)],
        "Saturday": [(10, 13)],
        "Sunday": [(10, 13), (20, 23)],
    },
    "Gowtham": {
        "Weekday": [(17, 21.5)],
        "Saturday": [(17, 21.5)],
        "Sunday": [(17, 21.5)],
    },
    "Nikhil": {
        "Weekday": [(17, 19)],
        "Saturday": [(9, 12)],
        "Sunday": [(21, 23)],  # Highest priority: 9pm-11pm
    },
}

# Course sequence
COURSES = ["FS1", "FS2", "FS3", "FS4", "FS5", "FS6", "Own"]

# Number/duration of mocks (minutes)
MOCK_SCHEDULE = {
    # Long schedule mode (≥20 days) - Original configurations
    "FSX_FIRST": [90, 90],     # First Full Stack set (3 mocks)
    "FSX_OTHER": [90, 90],         # All other Full Stack sets (2 mocks)
    "FS5": [105],                  # FS5 set (1 mock, 105 minutes)
    "FS6": [105],                  # FS6 set (1 mock, 105 minutes)
    "OWN": [90, 90, 60],           # Ownership set (3 mocks)
    
    # Short schedule mode (<20 days) - New configurations
    "FSX_FIRST_SHORT": [90, 90],   # First Full Stack set - Short mode (2 mocks)
    "OWN_SHORT": [90, 90],         # Ownership set - Short mode (2 mocks)
}

# Min/max gap rules (in hours)
MIN_GAP_HOURS = 12
MAX_GAP_DAYS = 4
MIN_COURSE_GAP_HOURS = 24

# Max per interviewer/day
MAX_MOCKS_PER_INTERVIEWER_PER_DAY = 2

# Calendar API Rate Limiting Settings
CALENDAR_API_DELAY_SECONDS = 15.0  # Minimum delay between Calendar API calls
CALENDAR_API_MAX_RETRIES = 3      # Maximum retry attempts for failed calls

# Timezone helper
def get_pst_now():
    import datetime
    return datetime.datetime.now(PST)
