"""
Timezone and time utilities for trading bot.
Provides US Eastern Time (market timezone) regardless of system location.
"""
import logging
from datetime import datetime, timedelta
import pytz
import requests

logger = logging.getLogger(__name__)


def get_us_time():
    """
    Get current time in US Eastern Time (market timezone).
    Uses NTP-like service or falls back to system time with timezone conversion.
    
    Returns:
        datetime: Current US Eastern Time
    """
    try:
        # Try to get current time from world time API (reliable, no auth needed)
        response = requests.get(
            "https://worldtimeapi.org/api/timezone/America/New_York",
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            # Parse the ISO 8601 datetime string
            us_time = datetime.fromisoformat(data['datetime'].replace('Z', '+00:00'))
            logger.debug(f"US time from API: {us_time}")
            return us_time
    except Exception as e:
        logger.debug(f"Could not fetch US time from API: {e}")
    
    # Fallback: Use system time with timezone conversion
    try:
        eastern = pytz.timezone('America/New_York')
        us_time = datetime.now(eastern)
        logger.debug(f"US time from system (converted): {us_time}")
        return us_time
    except Exception as e:
        logger.error(f"Error getting US time: {e}")
        # Final fallback: use system time (not ideal but better than crashing)
        return datetime.now()


def get_us_date():
    """
    Get current date in US Eastern Time.
    
    Returns:
        datetime.date: Current US Eastern date
    """
    return get_us_time().date()


def get_us_datetime():
    """
    Get current datetime in US Eastern Time as naive datetime (for compatibility).
    
    Returns:
        datetime: Current US Eastern datetime (naive, no timezone info)
    """
    us_time = get_us_time()
    # Return as naive datetime for compatibility with yfinance and Alpaca
    if us_time.tzinfo is not None:
        eastern = pytz.timezone('America/New_York')
        us_time = us_time.astimezone(eastern)
        return us_time.replace(tzinfo=None)
    return us_time


def is_market_hours(dt=None):
    """
    Check if given time is during US market hours (9:30 AM - 4:00 PM ET, Mon-Fri).
    
    Args:
        dt (datetime, optional): Time to check. If None, uses current US time.
        
    Returns:
        bool: True if market is open, False otherwise
    """
    if dt is None:
        dt = get_us_datetime()
    
    # Market closed on weekends (5=Saturday, 6=Sunday)
    if dt.weekday() >= 5:
        return False
    
    # Market hours: 9:30 AM to 4:00 PM ET
    market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = dt.replace(hour=16, minute=0, second=0, microsecond=0)
    
    return market_open <= dt <= market_close


def is_premarket_hours(dt=None):
    """
    Check if given time is during US premarket hours (4:00 AM - 9:30 AM ET).
    
    Args:
        dt (datetime, optional): Time to check. If None, uses current US time.
        
    Returns:
        bool: True if in premarket hours, False otherwise
    """
    if dt is None:
        dt = get_us_datetime()
    
    # Premarket closed on weekends
    if dt.weekday() >= 5:
        return False
    
    # Premarket hours: 4:00 AM to 9:30 AM ET
    premarket_start = dt.replace(hour=4, minute=0, second=0, microsecond=0)
    market_open = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    
    return premarket_start <= dt < market_open
