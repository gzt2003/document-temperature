from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo


TIME_KEYS = (
    "monitorTime",
    "recordTime",
    "collectTime",
    "createTime",
    "timestamp",
    "datetime",
    "time",
)
VALUE_KEYS = ("temperature", "temp", "currentValue", "probeValue", "value")
UNIT_KEYS = ("unitCode", "unitName", "unit", "unitSymbol")
PROBE_KEYS = ("probeName", "combineProbeName", "attributeName", "paramName", "name")
TEMPERATURE_UNITS = {"℃", "°C", "Celsius", "摄氏度", "温度"}


def normalize_time(value, timezone="Asia/Shanghai"):
    if value is None or isinstance(value, bool):
        return None

    tz = ZoneInfo(timezone)
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, tz).strftime("%Y-%m-%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"\(([+-]\d{2}:\d{2})\)$", r"\1", text)
    text = text.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        else:
            parsed = parsed.astimezone(tz)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def normalize_temperature(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).strip())
        if not match:
            return None
        number = float(match.group(0))
    if not -100 <= number <= 200:
        return None
    return round(number, 3)


def _temperature_context(obj, allowed_probe_names):
    for key in UNIT_KEYS:
        value = obj.get(key)
        if value is not None and str(value).strip() in TEMPERATURE_UNITS:
            return True

    for key in PROBE_KEYS:
        value = obj.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text in allowed_probe_names or "温度" in text or "temperature" in text.lower():
            return True
    return False


def _first_present(obj, keys):
    for key in keys:
        if key in obj and obj[key] is not None:
            return obj[key]
    lowered = {str(key).lower(): value for key, value in obj.items()}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] is not None:
            return lowered[key.lower()]
    return None


def extract_temperature_records(payload, allowed_probe_names=None, timezone="Asia/Shanghai"):
    """Extract only temperature readings from a mixed-probe Elitech payload.

    A generic ``value`` field is accepted only while traversing a branch that is
    explicitly marked as Celsius/temperature or as an allowed temperature probe.
    This prevents GSP-8G Lux readings from being misclassified as temperatures.
    """

    allowed = {str(name).strip() for name in (allowed_probe_names or ["探头2"])}
    records = {}

    def walk(obj, inherited_temperature=False):
        if isinstance(obj, dict):
            is_temperature = inherited_temperature or _temperature_context(obj, allowed)
            if is_temperature:
                time_value = _first_present(obj, TIME_KEYS)
                temperature_value = _first_present(obj, VALUE_KEYS)
                normalized_time = normalize_time(time_value, timezone)
                normalized_temperature = normalize_temperature(temperature_value)
                if normalized_time is not None and normalized_temperature is not None:
                    records[normalized_time] = normalized_temperature

            for value in obj.values():
                if isinstance(value, (dict, list)):
                    walk(value, is_temperature)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, inherited_temperature)

    walk(payload)
    return [
        {"time": recorded_at, "temperature": records[recorded_at]}
        for recorded_at in sorted(records)
    ]

