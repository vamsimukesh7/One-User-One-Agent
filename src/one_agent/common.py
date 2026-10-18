import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def ts(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def iso(value):
    return value.astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def day(value):
    return value.astimezone(IST).date()


def stable_id(prefix, *parts):
    return prefix + '-' + hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:20]


def jsonl(path):
    with Path(path).open(encoding='utf8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def csv_rows(path):
    with Path(path).open(encoding='utf8', newline='') as stream:
        return list(csv.DictReader(stream))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf8')


def write_jsonl(path, values):
    with Path(path).open('w', encoding='utf8') as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False) + '\n')


def event_time(event):
    """Clock-offset-resistant estimate, NOT verified wall-clock truth.

    sent-occured measures buffering on one device clock. Anchor that duration to
    server receipt; network delay remains unknown and is recorded as an assumption.
    """
    received = ts(event['received_at'])
    sent, occurred = ts(event.get('sent_at')), ts(event.get('occurred_at'))
    if sent is None or occurred is None or sent < occurred:
        return received, False
    return received - (sent - occurred), True


def fresh_trigger(event, now, max_age_s=600):
    effective, valid = event_time(event)
    return valid and 0 <= (now - effective).total_seconds() <= max_age_s
