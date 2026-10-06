"""Dates as the CLI reads and writes them: keywords, journal days, timestamps, repeaters.

Parses what a caller types for a date (``today``, an ISO date, a range), turns
a journal day into a date and a date into the title Logseq gives its Journal
Page, reads the SCHEDULED/DEADLINE timestamps of a block, and works out when a repeating task falls due. Nothing here talks to
Logseq, so all of it is tested without a mock; the layering tests keep it that
way (ADR 0003).
"""

import re
import calendar
import datetime
from typing import NamedTuple, Optional

import click

from logseq_cli.blocktext import PLANNING_LINE_RE, code_block_lines


# Locale-independent English day/month names (Logseq always uses English)
_WEEKDAYS_FULL = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAYS_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_MONTHS_FULL = ["", "january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december"]
_MONTHS_ABBR = ["", "jan", "feb", "mar", "apr", "may", "jun",
                "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_date_keyword(date_str: str) -> datetime.date:
    """Parse a date string that may be a keyword like 'today', 'yesterday', or an ISO date."""
    if not date_str or date_str.lower() == "today":
        return datetime.date.today()
    if date_str.lower() == "yesterday":
        return datetime.date.today() - datetime.timedelta(days=1)
    if date_str.lower() == "tomorrow":
        return datetime.date.today() + datetime.timedelta(days=1)
    try:
        return datetime.date.fromisoformat(date_str)
    except ValueError:
        raise click.BadParameter(
            f"Invalid date: '{date_str}'. Use 'today', 'yesterday', 'tomorrow', or YYYY-MM-DD."
        )


# The journal titles Logseq takes as such whatever the graph's date format,
# besides the graph's own: "MMM do, yyyy", "yyyy-MM-dd" and "yyyy_MM_dd"
# (date_time_util.cljs safe-journal-title-formatters, 0.10.15; read in the
# code). Logseq creates a page of such a name as the journal under the
# graph's name, and answers createPage with null (measured for
# "Jan 1st, 2099"). Matched against the name stripped and in lower case, as
# Logseq capitalises it before parsing.
_JOURNAL_NAMES = [
    re.compile(r"^(?P<mon>[a-z]{3})\s+(?P<d>\d{1,2})(?:st|nd|rd|th),\s+(?P<y>\d{4})$"),  # MMM do, yyyy
    re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})$"),                            # yyyy-MM-dd
    re.compile(r"^(?P<y>\d{4})_(?P<m>\d{2})_(?P<d>\d{2})$"),                            # yyyy_MM_dd
]


def parse_journal_name(name: str) -> datetime.date | None:
    """The day ``name`` names as a journal title in one of the formats
    above, or ``None``: for any other name, and for a day no calendar has
    (``2099-02-30``, a month ``foo``).

    A name in the graph's own format needs no such reading: Logseq creates
    and answers it under that name (measured). For
    ``pagenames.page_name_to_create``, which sends such a title under the
    graph's name.
    """
    name = name.strip().lower()
    for pattern in _JOURNAL_NAMES:
        m = pattern.match(name)
        if not m:
            continue
        parts = m.groupdict()
        if "mon" in parts:
            if parts["mon"] not in _MONTHS_ABBR[1:]:
                return None
            month = _MONTHS_ABBR.index(parts["mon"])
        else:
            month = int(parts["m"])
        try:
            return datetime.date(int(parts["y"]), month, int(parts["d"]))
        except ValueError:
            return None
    return None


def journal_day_to_date(jd: int) -> datetime.date:
    """Convert YYYYMMDD integer to a date object."""
    s = str(jd)
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


class Timestamp(NamedTuple):
    """One SCHEDULED or DEADLINE of a block. ``kind`` is that word as written
    (None where only the text between the angle brackets was read); the
    weekday is neither read nor kept, so a text that names the wrong one reads
    the same. ``repeater`` is ``(kind, num, unit)``: ``kind`` is ``+``, ``++`` or ``.+`` as
    Logseq spells it, ``unit`` one of h d w m y."""
    kind: Optional[str]
    date: datetime.date
    time: Optional[datetime.time]
    repeater: Optional[tuple]


_TIMESTAMP_RE = re.compile(r"(SCHEDULED|DEADLINE):\s*<([^>]*)>")
_TIMESTAMP_BODY_RE = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})(?:\s+[A-Za-z]+)?(?:\s+(\d{1,2}):(\d{2}))?(.*)$", re.S)
# ++ and .+ before +: the bare + would take them all.
_INTERVAL_RE = re.compile(r"(\+\+|\.\+|\+)(\d+)([hdwmy])")


# No date lies this many days, weeks, months or years away (the year stops at
# 9999), so every number past it means the same: the repeater cannot be placed.
_TOO_FAR = 10 ** 12


def _interval_number(digits: str) -> int:
    """The number of a repeater. Digits past what any date can hold are read as
    _TOO_FAR, because int() refuses a string of more than 4300 digits and the
    text is whatever a person typed or pasted."""
    return int(digits) if len(digits) <= 12 else _TOO_FAR


def parse_timestamp(text: str):
    """The text between ``<`` and ``>`` of a timestamp, as a Timestamp without
    ``kind``, or None. Forgiving on purpose: a wrong or missing weekday, and
    what is not a repeater (a warning time such as ``-2d``), are let go; the
    date is not (a day that does not exist is None)."""
    match = _TIMESTAMP_BODY_RE.match(text or "")
    if not match:
        return None
    year, month, day, hour, minute, rest = match.groups()
    try:
        date = datetime.date(int(year), int(month), int(day))
        time = datetime.time(int(hour), int(minute)) if hour is not None else None
    except ValueError:
        return None
    interval = _INTERVAL_RE.search(rest)
    repeater = (interval.group(1), _interval_number(interval.group(2)), interval.group(3)) if interval else None
    return Timestamp(None, date, time, repeater)


def timestamps(content: str) -> list:
    """Every SCHEDULED/DEADLINE of a block's text, in the order of the text,
    each with the repeater of its own.

    Only a line that starts with the word counts (blocktext.PLANNING_LINE_RE),
    and not one inside a code block: Logseq's parser reads a date as a line of
    the block, nothing else. A line may hold both. One that does not read as a
    date is left out, never guessed. next_occurrence works in days, for "what
    is due"; this tells what the text says, and the two answer different
    questions.
    """
    found = []
    if "SCHEDULED" not in (content or "") and "DEADLINE" not in (content or ""):
        return found
    lines = (content or "").split("\n")
    inside, _ = code_block_lines(lines)
    for line, in_code in zip(lines, inside):
        if in_code or not PLANNING_LINE_RE.match(line):
            continue
        for match in _TIMESTAMP_RE.finditer(line):
            stamp = parse_timestamp(match.group(2))
            if stamp:
                found.append(stamp._replace(kind=match.group(1)))
    return found


# Repeating tasks
# ---------------
# Logseq stores a repeater's date as written in the text, and :block/scheduled
# follows that. The text holds the next occurrence only once Logseq's checkbox
# has rewritten it on ticking the task off; otherwise it is an earlier date. The next one is therefore derived — using the
# source's own formula rather than a second answer invented here.
#
# From frontend/handler/repeated.cljs (0.10.12), next-timestamp-text. All three
# forms compute from the written date, the current time and the interval; the
# completion time is not an input, which is what makes this derivable:
#
#   .+   add delta until the result is in the future; week intervals keep their
#        weekday (repeat-until-future-timestamp)
#   ++   add delta once, but only if the written date is already past
#   +    add delta once, unconditionally


def _add_interval(start: datetime.date, num: int, unit: str):
    """Add ``num`` units to ``start``. Returns None for an unknown unit, and
    for a result no date can hold (a repeater typed as +99999999999d).

    Months and years are handled by arithmetic on the calendar fields rather
    than by a fixed day count, clamping the day to the target month's length
    (31 January plus one month is 28 or 29 February, as a calendar reads it).
    """
    try:
        if unit == "h":
            # Hour repeats exist in the grammar; at date granularity the smallest
            # step that can move the result is a day.
            return start + datetime.timedelta(days=1)
        if unit == "d":
            return start + datetime.timedelta(days=num)
        if unit == "w":
            return start + datetime.timedelta(weeks=num)
        if unit == "m":
            month_index = start.month - 1 + num
            year = start.year + month_index // 12
            month = month_index % 12 + 1
            day = min(start.day, calendar.monthrange(year, month)[1])
            return datetime.date(year, month, day)
        if unit == "y":
            year = start.year + num
            day = min(start.day, calendar.monthrange(year, start.month)[1])
            return datetime.date(year, start.month, day)
    except (OverflowError, ValueError):
        return None
    return None


def next_occurrence(start: datetime.date, repeater, today: datetime.date = None):
    """Next due date of a repeating task, or None if it cannot be derived.

    ``repeater`` is the one of a :class:`Timestamp` from :func:`timestamps`. ``today`` is injectable
    so the rule can be tested against fixed dates instead of the clock.
    """
    if not repeater or start is None:
        return None
    kind, num, unit = repeater
    if today is None:
        today = datetime.date.today()

    if _add_interval(start, num, unit) is None:
        return None

    # Logseq's own formula answers a different question than this one.
    # next-timestamp-text runs at the moment a task is ticked off
    # (update-timestamps-content! in handler/editor.cljs), where the stored date
    # is near today and one step is enough. Applied to a task that was never
    # ticked off, "+" and "++" return a date that is still in the past — useless
    # for "what is due", which has to look forward from today whatever the form.
    #
    # So a stored date that is not past is the occurrence itself: a task ticked
    # off before has its next date written into the text, and one more step would
    # skip it. Past that, the single step is kept where it lands in the future,
    # and otherwise the ".+" loop runs for every form. The interval is still
    # Logseq's, and so is the weekday rule; only the starting point differs,
    # because the question does.
    if start >= today:
        return start
    if kind in ("+", "++"):
        stepped = _add_interval(start, num, unit)
        if stepped is None or stepped > today:
            return stepped

    current = start
    for _ in range(50000):
        current = _add_interval(current, num, unit)
        if current is None:
            return None
        if current > today:
            break
    else:
        return None

    if unit == "w" and current.weekday() != start.weekday():
        delta = current.weekday() - start.weekday()
        current += datetime.timedelta(days=(7 - delta) if delta > 0 else -delta)
    return current


def get_day_suffix(day: int) -> str:
    """Return st, nd, rd, or th for a given day number."""
    if 11 <= day <= 13:
        return "th"
    last = day % 10
    if last == 1:
        return "st"
    if last == 2:
        return "nd"
    if last == 3:
        return "rd"
    return "th"


def java_date_format_to_python(java_fmt: str, d: datetime.date) -> str:
    """Convert a Java SimpleDateFormat pattern to a formatted date string.

    Supports the tokens Logseq uses in :journal/page-title-format:
    yyyy=4-digit year, yy=2-digit year, MMMM=full month, MMM=abbrev month,
    MM=zero-padded month, M=month, dd=zero-padded day, d=day, do=day+ordinal,
    EEEE=full weekday, EEE=abbrev weekday, EE/E=abbrev weekday.
    """
    result = []
    i = 0
    fmt = java_fmt
    while i < len(fmt):
        # Year
        if fmt[i:i+4] == "yyyy":
            result.append(str(d.year))
            i += 4
        elif fmt[i:i+2] == "yy":
            result.append(d.strftime("%y"))
            i += 2
        # Month (MMMM before MMM before MM before M) — locale-independent English
        elif fmt[i:i+4] == "MMMM":
            result.append(_MONTHS_FULL[d.month])
            i += 4
        elif fmt[i:i+3] == "MMM":
            result.append(_MONTHS_ABBR[d.month])
            i += 3
        elif fmt[i:i+2] == "MM":
            result.append(d.strftime("%m"))
            i += 2
        elif fmt[i] == "M" and (i + 1 >= len(fmt) or fmt[i+1] != "M"):
            result.append(str(d.month))
            i += 1
        # Day with ordinal suffix (do)
        elif fmt[i:i+2] == "do":
            result.append(f"{d.day}{get_day_suffix(d.day)}")
            i += 2
        # Day (dd before d)
        elif fmt[i:i+2] == "dd":
            result.append(d.strftime("%d"))
            i += 2
        elif fmt[i] == "d" and (i + 1 >= len(fmt) or fmt[i+1] not in "do"):
            result.append(str(d.day))
            i += 1
        # Weekday (EEEE before EEE before EE before E) — locale-independent English
        elif fmt[i:i+4] == "EEEE":
            result.append(_WEEKDAYS_FULL[d.weekday()])
            i += 4
        elif fmt[i:i+3] == "EEE":
            result.append(_WEEKDAYS_ABBR[d.weekday()])
            i += 3
        elif fmt[i:i+2] == "EE":
            result.append(_WEEKDAYS_ABBR[d.weekday()])
            i += 2
        elif fmt[i] == "E" and (i + 1 >= len(fmt) or fmt[i+1] != "E"):
            result.append(_WEEKDAYS_ABBR[d.weekday()])
            i += 1
        # Literal characters
        else:
            result.append(fmt[i])
            i += 1
    return "".join(result)


def format_journal_date(d: datetime.date, date_format: str = None) -> str:
    """Format date according to Logseq's configured journal page title format.

    If date_format is None, falls back to Logseq default 'MMM do, yyyy' (e.g. 'mar 14th, 2025').
    """
    if date_format is None:
        date_format = "MMM do, yyyy"
    return java_date_format_to_python(date_format, d)


def parse_date_range(range_str: str) -> tuple:
    """Parse a date range string into (start, end) datetime pair.

    Supports: 'today', 'this week', 'last 30 days', 'last N days',
    'this month', 'this year', 'year to date'.
    """
    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    r = range_str.strip().lower()

    if r == "today":
        return today_start, today_end

    if r == "yesterday":
        yesterday = today_start - datetime.timedelta(days=1)
        yesterday_end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        return yesterday, yesterday_end

    if r == "this week":
        weekday = now.weekday()  # Monday=0
        start = today_start - datetime.timedelta(days=weekday)
        return start, today_end

    if r == "this month":
        start = today_start.replace(day=1)
        return start, today_end

    if r in ("this year", "year to date"):
        start = today_start.replace(month=1, day=1)
        return start, today_end

    # "last N days"
    m = re.match(r"last\s+(\d+)\s+days?", r)
    if m:
        days = int(m.group(1))
        start = today_start - datetime.timedelta(days=days)
        return start, today_end

    # "last week"
    if r == "last week":
        weekday = now.weekday()
        this_monday = today_start - datetime.timedelta(days=weekday)
        last_monday = this_monday - datetime.timedelta(days=7)
        last_sunday = this_monday - datetime.timedelta(seconds=1)
        return last_monday, last_sunday

    # "last month"
    if r == "last month":
        first_this_month = today_start.replace(day=1)
        end = first_this_month - datetime.timedelta(seconds=1)
        start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, end

    # "last year"
    if r == "last year":
        start = today_start.replace(year=now.year - 1, month=1, day=1)
        end = today_start.replace(month=1, day=1) - datetime.timedelta(seconds=1)
        return start, end

    raise click.BadParameter(
        f"Unknown date range: '{range_str}'. "
        "Try: 'today', 'yesterday', 'this week', 'last week', 'last 30 days', "
        "'this month', 'last month', 'this year', 'last year'."
    )
