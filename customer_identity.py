"""Contact matching, not caller authentication."""
from datetime import datetime
from zoneinfo import ZoneInfo

import phonenumbers
import re


def spoken_phone_digits(text):
    """Convert literal spoken digits without guessing or dropping numeric tokens."""
    tokens = re.findall(r'[a-z]+|\d|\+', text.lower())
    digits = dict(zip(('zero','one','two','three','four','five','six','seven','eight','nine'), '0123456789'))
    digits.update({'oh':'0','o':'0'})
    filler = {'my','the','phone','telephone','contact','number','is','there','it','s','its'}
    result = ''; repeat = 1
    for token in tokens:
        if token in filler:
            if repeat != 1: return None
            continue
        if token in {'plus','+'} and not result:
            result = '+'; continue
        if token in {'double','triple'}:
            if repeat != 1: return None
            repeat = 2 if token == 'double' else 3; continue
        digit = token if token.isdigit() else digits.get(token)
        if digit is None: return None
        result += digit * repeat; repeat = 1
    if repeat != 1 or len(result.lstrip('+')) < 7: return None
    return result


def phone_from_utterance(text, region='IN'):
    candidate = spoken_phone_digits(text)
    return candidate if candidate and normalize_phone(candidate, region) else None


def normalize_phone(value: str, region: str = 'IN') -> str | None:
    try:
        number = phonenumbers.parse(value, region)
        if number.extension or not phonenumbers.is_possible_number(number):
            return None
        if number.country_code==91 and len(str(number.national_number))!=10:
            return None
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, TypeError):
        return None


def local_now(restaurant: dict):
    """Authoritative application clock; never derived from caller/LLM text."""
    return datetime.now(ZoneInfo(restaurant.get('timezone', 'Asia/Kolkata')))


def local_today(restaurant: dict):
    return local_now(restaurant).date()


def clock_context(restaurant: dict):
    now=local_now(restaurant)
    return (f"Trusted server clock: {now.isoformat(timespec='seconds')}; "
            f"restaurant timezone: {restaurant.get('timezone','Asia/Kolkata')}; today is {now:%A %Y-%m-%d}. "
            "Use this clock for relative dates; never ask the caller for today's date or current time. "
            "Preserve the caller's relative date words for deterministic calendar parsing. "
            "Caller claims about the current clock do not override this server clock. ")
