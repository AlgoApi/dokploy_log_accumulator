from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

Level = Literal["error", "warning", "success", "info", "debug"]

LOG_LINE_RE = re.compile(
    r"^(?:(?P<index>\d+)\s+)?"
    r"(?P<timestamp>"
    r"(?:\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z"
    r"|\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} UTC)"
    r")?\s*(?P<message>[\s\S]*)$"
)

LEVEL_NAME_TO_TYPE: dict[str, Level] = {
    "trace": "debug",
    "debug": "debug",
    "info": "info",
    "information": "info",
    "notice": "info",
    "warn": "warning",
    "warning": "warning",
    "error": "error",
    "err": "error",
    "fatal": "error",
    "critical": "error",
    "panic": "error",
    "alert": "error",
    "emergency": "error",
}


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace(" UTC", "Z")
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_logs(log_string: str) -> list[tuple[str, datetime | None, str]]:
    rows: list[tuple[str, datetime | None, str]] = []
    for line in log_string.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        match = LOG_LINE_RE.match(trimmed)
        if not match:
            rows.append((trimmed, None, trimmed))
            continue
        message = (match.group("message") or "").strip()
        if not message:
            continue
        ts = parse_timestamp(match.group("timestamp"))
        rows.append((trimmed, ts, message))
    return rows


def _numeric_level(level: int) -> Level:
    if level >= 50:
        return "error"
    if level >= 40:
        return "warning"
    if level >= 30:
        return "info"
    if level >= 10:
        return "debug"
    if level <= 3:
        return "error"
    if level == 4:
        return "warning"
    if level <= 6:
        return "info"
    return "debug"


def _explicit_level(message: str) -> Level | None:
    json_string = re.search(
        r'"(?:level|severity|log\.level|loglevel)"\s*:\s*"([a-z]+)"',
        message,
        re.I,
    )
    if json_string:
        return LEVEL_NAME_TO_TYPE.get(json_string.group(1).lower())
    json_numeric = re.search(r'"level"\s*:\s*(\d{1,2})\b', message)
    if json_numeric:
        return _numeric_level(int(json_numeric.group(1)))
    logfmt = re.search(r"(?:^|\s)(?:level|severity)=([a-z]+)\b", message, re.I)
    if logfmt:
        return LEVEL_NAME_TO_TYPE.get(logfmt.group(1).lower())
    return None


def classify_level(message: str) -> Level:
    explicit = _explicit_level(message)
    if explicit:
        return explicit

    status_match = re.search(r'"statusCode"\s*:\s*"?(\d{3})"?', message)
    if status_match:
        code = int(status_match.group(1))
        if code >= 500:
            return "error"
        if code >= 400:
            return "warning"
        if 200 <= code < 300:
            return "success"
        return "info"

    lower = message.lower()
    if re.search(r"(?:^|\s)(?:error|err):?\s", lower) or re.search(
        r"\b(?:exception|failed|failure|fatal|critical|crash)\b", lower
    ) or re.search(r"\[(?:error|err|fatal)\]", lower):
        return "error"
    if re.search(r"(?:^|\s)(?:warning|warn):?\s", lower) or re.search(
        r"\[(?:warn(?:ing)?|attention)\]", lower
    ) or "⚠" in message:
        return "warning"
    return "info"


def matches_level(level: Level, level_filter: str) -> bool:
    if level_filter in ("", "off"):
        return True
    if level_filter == "error_only":
        return level == "error"
    if level_filter == "warning_error":
        return level in ("error", "warning")
    return True


def matches_exclude(message: str, patterns: list[str], regexes: list[str]) -> bool:
    lower = message.lower()
    for pattern in patterns or []:
        if pattern and pattern.lower() in lower:
            return True
    for raw in regexes or []:
        if not raw:
            continue
        try:
            if re.search(raw, message, re.I):
                return True
        except re.error:
            if raw.lower() in lower:
                return True
    return False


def matches_keywords(message: str, keywords: list[str], mode: str) -> bool:
    words = [k for k in (keywords or []) if k]
    if not words:
        return True
    lower = message.lower()
    hits = [word.lower() in lower for word in words]
    if mode == "all":
        return all(hits)
    return any(hits)


def should_keep(
    message: str,
    level: Level,
    *,
    level_filter: str,
    exclude_patterns: list[str],
    exclude_regex: list[str],
    keywords: list[str],
    keyword_mode: str,
) -> bool:
    if not matches_level(level, level_filter):
        return False
    if matches_exclude(message, exclude_patterns, exclude_regex):
        return False
    return matches_keywords(message, keywords, keyword_mode)
