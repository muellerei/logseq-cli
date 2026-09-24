"""Dates as the CLI reads and writes them: keywords, journal days, repeaters.

Parses what a caller types for a date (``today``, an ISO date, a range), turns
a journal day into a date and a date into the title Logseq gives its Journal
Page, and works out when a repeating task falls due. Nothing here talks to
Logseq, so all of it is tested without a mock; the layering tests keep it that
way (ADR 0003).
"""

import re
import calendar
import datetime

import click


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


def is_journal_date(name: str) -> bool:
    """Check if a page name looks like a journal date.

    Supports multiple Logseq date formats:
    - 'mar 14th, 2025' (MMM do, yyyy)
    - '2025-03-14, friday' (yyyy-MM-dd, EEEE)
    - '2025-03-14' (yyyy-MM-dd)
    - '14.03.2025' (dd.MM.yyyy)
    """
    name = name.strip().lower()
    patterns = [
        r"^[a-z]{3}\s+\d{1,2}(?:st|nd|rd|th),\s+\d{4}$",       # MMM do, yyyy
        r"^\d{4}-\d{2}-\d{2},\s+[a-z]+$",                        # yyyy-MM-dd, EEEE
        r"^\d{4}-\d{2}-\d{2}$",                                   # yyyy-MM-dd
        r"^\d{2}\.\d{2}\.\d{4}$",                                 # dd.MM.yyyy
    ]
    return any(re.match(p, name) for p in patterns)


def journal_day_to_date(jd: int) -> datetime.date:
    """Convert YYYYMMDD integer to a date object."""
    s = str(jd)
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


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
_REPEATER_RE = re.compile(
    r"(?:SCHEDULED|DEADLINE):\s*<[^>]*?(\+\+|\.\+|\+)(\d+)([hdwmy])[^>]*>"
)


def parse_repeater(content: str):
    """Extract ``(kind, num, unit)`` from a SCHEDULED/DEADLINE line, or None.

    ``kind`` is ``"+"``, ``"++"`` or ``".+"`` exactly as Logseq spells it. The
    order in the pattern matters: ``++`` and ``.+`` must be tried before the
    bare ``+``, or every repeater would read as ``+``.
    """
    if not content:
        return None
    match = _REPEATER_RE.search(content)
    if not match:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def _add_interval(start: datetime.date, num: int, unit: str):
    """Add ``num`` units to ``start``. Returns None for an unknown unit.

    Months and years are handled by arithmetic on the calendar fields rather
    than by a fixed day count, clamping the day to the target month's length
    (31 January plus one month is 28 or 29 February, as a calendar reads it).
    """
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
    return None


def next_occurrence(start: datetime.date, repeater, today: datetime.date = None):
    """Next due date of a repeating task, or None if it cannot be derived.

    ``repeater`` is what :func:`parse_repeater` returns. ``today`` is injectable
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
    # So the single step is kept where it lands in the future, and otherwise the
    # ".+" loop runs for every form. The interval is still Logseq's, and so is
    # the weekday rule; only the starting point differs, because the question
    # does.
    if kind in ("+", "++"):
        stepped = start if (kind == "++" and start > today) else _add_interval(start, num, unit)
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
