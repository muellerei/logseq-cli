import re
import sys
import calendar
import json
import datetime
import uuid as uuid_module
from pathlib import Path

import click

from logseq_cli.blocktext import (
    PROPERTY_KEY_STOP,
    PROPERTY_LINE_RE,
    block_id_property,
    code_block_lines,
    id_lines,
    is_fence,
    refuse_split_property,
    without_block_ids,
)
from logseq_cli.datalog import edn_string, page_name_literal

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


def strip_title_heading(content: str, page_name: str) -> str:
    """Remove '# PageName' heading from content to prevent duplication."""
    pattern = re.compile(rf"^#\s+{re.escape(page_name)}\s*$", re.IGNORECASE | re.MULTILINE)
    return pattern.sub("", content).strip()


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


def count_blocks(tree: list) -> int:
    """Recursively count total blocks in a hierarchical tree."""
    total = 0
    for node in tree:
        total += 1
        if node.get("children"):
            total += count_blocks(node["children"])
    return total


def escape_regex(s: str) -> str:
    """Escape special regex characters in a string."""
    return re.escape(s)


def journal_day_to_date(jd: int) -> datetime.date:
    """Convert YYYYMMDD integer to a date object."""
    s = str(jd)
    return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# Repeating tasks
# ---------------
# Logseq stores a repeater's date as written, never the next occurrence, and
# :block/scheduled follows that. The next one is therefore derived — using the
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


def process_blocks(blocks, indent: int = 0) -> str:
    """Recursively format blocks as indented text."""
    lines = []
    prefix = "  " * indent
    for block in blocks:
        content = block.get("content", "")
        if content:
            lines.append(f"{prefix}- {content}")
        children = block.get("children", [])
        if children:
            lines.append(process_blocks(children, indent + 1))
    return "\n".join(lines)


def get_page_content(api, page_name: str) -> str:
    """Fetch page blocks and return formatted text."""
    blocks = api.get_page_blocks_tree(page_name)
    if not blocks:
        return ""
    return process_blocks(blocks)


def find_backlinks(api, page_name: str) -> list:
    """Find all pages that link to page_name by scanning all page contents."""
    escaped = escape_regex(page_name)
    pattern = re.compile(rf"\[\[\s*{escaped}\s*\]\]", re.IGNORECASE)
    pages = api.get_all_pages()
    backlink_pages = []
    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        if name.lower() == page_name.lower():
            continue
        try:
            content = get_page_content(api, name)
            if not content:
                continue
            if pattern.search(content):
                backlink_pages.append(name)
        except Exception:
            continue
    return sorted(backlink_pages)


def contains_hierarchical_content(content: str) -> bool:
    """Check if content contains indented sub-bullets (hierarchy markers).

    Detects newline followed by indentation (tab or spaces) and a bullet marker.
    Used by add-journal-block to auto-delegate to hierarchical insertion.
    """
    return bool(re.search(r'\n[\t ]+- ', content))


def has_mixed_indentation(content: str) -> bool:
    """True if any indented line mixes tabs and spaces in its leading whitespace.

    Logseq's block model requires tab-only indentation; a ``\\t  \\t`` style
    prefix breaks the outline. This catches a content string that would write
    such a line, so callers can reject or normalize it instead of silently
    persisting a broken block.
    """
    for line in content.split("\n"):
        lead = line[:len(line) - len(line.lstrip(" \t"))]
        if "\t" in lead and " " in lead:
            return True
    return False


def normalize_indentation(content: str) -> str:
    """Rewrite each line's leading whitespace to tabs only (2 spaces = 1 tab).

    Mirrors the tab/space counting in :func:`parse_hierarchical_content` so a
    string with mixed or space-based indentation is coerced to the tab-only
    form Logseq expects, without changing the (non-leading) line text.
    """
    out = []
    for line in content.split("\n"):
        raw = line
        level = 0
        i = 0
        while i < len(raw):
            if raw[i] == '\t':
                level += 1
                i += 1
            elif raw[i] == ' ':
                spaces = 0
                while i < len(raw) and raw[i] == ' ':
                    spaces += 1
                    i += 1
                level += spaces // 2
            else:
                break
        out.append('\t' * level + raw[i:])
    return "\n".join(out)


def _outline_fence_close(raw_lines: list, opener: int):
    """Index of the line that closes the code block opened at ``opener``: the
    next one without a bullet that starts with a fence. ``None`` if none does."""
    for index in range(opener + 1, len(raw_lines)):
        if is_fence(raw_lines[index]):
            return index
    return None


def _dedent_code(raw_lines: list, opener: int, close: int, bulleted: bool) -> str:
    """The lines after ``opener`` up to ``close``, as ``"\n"``-prefixed text,
    each moved left by the opener's indentation (plus the "- " of a bullet),
    and by no more than the whitespace a line has."""
    line = raw_lines[opener]
    width = len(line) - len(line.lstrip(" \t")) + (2 if bulleted else 0)
    out = []
    for text in raw_lines[opener + 1:close + 1]:
        lead = len(text) - len(text.lstrip(" \t"))
        out.append(text[min(width, lead):])
    return "".join("\n" + text for text in out)


def parse_hierarchical_content(content: str) -> list:
    """Parse indented content into a block tree.

    Each line becomes a block, except a property line or a code block, which
    go on the block they belong to (see below). Indentation (tab or 2 spaces)
    creates children. Leading '- ' is stripped from each line. Mixed tab/space indentation is
    normalized to tab-only first, so a node's leading whitespace can never
    carry the ``\\t  \\t`` form that would break Logseq's outline.
    """
    raw_lines = content.split("\n")
    lines = normalize_indentation(content).split("\n")
    root = []
    stack = [(root, -1)]  # (children_list, indent_level)
    last_node = None
    last_indent = -1
    skip_to = -1

    for index, line in enumerate(lines):
        if index <= skip_to:
            continue
        if not line.strip():
            continue
        # Normalize indentation: count tabs (each tab = 1 level) or spaces (2 spaces = 1 level)
        raw = line
        tab_count = 0
        i = 0
        while i < len(raw):
            if raw[i] == '\t':
                tab_count += 1
                i += 1
            elif raw[i] == ' ':
                # Count consecutive spaces, 2 spaces = 1 tab level
                space_count = 0
                while i < len(raw) and raw[i] == ' ':
                    space_count += 1
                    i += 1
                tab_count += space_count // 2
            else:
                break
        indent = tab_count

        stripped = raw.strip()
        # strip leading '- ' bullet marker
        bulleted = stripped.startswith("- ")
        if bulleted:
            stripped = stripped[2:]

        # A code block stays one block, as in Logseq's files: from the opening
        # fence to the closing one every line is code, "- " and "# " lines
        # included, and is taken from the text as written, so the indentation
        # inside survives normalize_indentation. The closer is the next line
        # without a bullet that starts with a fence. Cut into a block per
        # line, the "```" block would run on into the blocks after it when
        # Logseq reads the page file again, up to the next code block (#47).
        close = _outline_fence_close(raw_lines, index) if is_fence(stripped) else None
        if close is not None:
            code = _dedent_code(raw_lines, index, close, bulleted)
            skip_to = close
            if not bulleted and last_node is not None:
                # Without a bullet it goes on the block above, as a property
                # line does.
                last_node["content"] += "\n" + stripped + code
                continue
            stripped += code

        # A property line without a bullet continues the block above it, as in
        # Logseq's files, so pasted outlines carrying collapsed:: true / id::
        # ... keep their structure instead of gaining a bogus content block.
        # A bulleted one merges only when it sits deeper than that block: the
        # shape agents write for a property of the block above
        # ("- ## Plan" / "\t- collapsed:: true", a 22-block plan insert on
        # 2026-08-10). At the same level or above, Logseq reads "- k:: v" as a
        # block of its own (measured, 0.10.15), and merging it would move it
        # into whatever block came last, e.g. "- Priorität:: hoch" after a
        # nested detail (#39).
        if (PROPERTY_LINE_RE.match(stripped) and last_node is not None
                and (not bulleted or indent > last_indent)):
            last_node["content"] += "\n" + stripped
            continue

        node = {"content": stripped, "children": []}

        # find correct parent
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()

        stack[-1][0].append(node)
        stack.append((node["children"], indent))
        last_node = node
        last_indent = indent

    return root


def outline_text(tree: list) -> str:
    """``tree`` as the text :func:`parse_hierarchical_content` reads back as it.

    For showing what a command writes after it has changed the parsed tree
    (dropped id:: lines): derived from that tree, the preview cannot disagree
    with the write. A block's further lines sit under its bullet, indented.
    """
    def walk(blocks, depth):
        for block in blocks:
            first, *rest = (block.get("content") or "").split("\n")
            yield "\t" * depth + "- " + first
            yield from ("\t" * depth + "  " + line for line in rest)
            yield from walk(block.get("children") or [], depth + 1)
    return "\n".join(walk(tree or [], 0))


_QUOTE_LINE_RE = re.compile(r'^[ \t]*>')
_EMPTY_QUOTE_LINE_RE = re.compile(r'^[ \t]*>[ \t\r]*$')
_ORG_BLOCK_RE = re.compile(r'^[ \t]*#\+(BEGIN|END)_(\S+)', re.IGNORECASE)


def _org_block_lines(lines: list, code: list) -> list:
    """Which of ``lines`` belong to an org ``#+BEGIN_X`` ... ``#+END_X`` block.

    Like a fence (#43), an opener counts only when a closer follows: the
    next ``#+END_`` of the same name, outside code. An opener nothing closes,
    or a closer nothing opened, is text. A block inside another is part of it.
    """
    inside, i = [False] * len(lines), 0
    while i < len(lines):
        mark = None if code[i] or is_fence(lines[i]) else _ORG_BLOCK_RE.match(lines[i])
        if mark and mark.group(1).upper() == "BEGIN":
            name = mark.group(2).upper()
            for j in range(i + 1, len(lines)):
                end = None if code[j] or is_fence(lines[j]) else _ORG_BLOCK_RE.match(lines[j])
                if end and end.group(1).upper() == "END" and end.group(2).upper() == name:
                    inside[i:j + 1] = [True] * (j + 1 - i)
                    i = j
                    break
        i += 1
    return inside


def quote_break_lines(content: str) -> list:
    """Line numbers (1-based) where a quote in ``content`` has stopped.

    mldoc takes every line after a ``>`` into the quote, until a blank line
    (#45, from its source; measured with mldoc 1.5.7, the version Logseq
    0.10.15 pins, in #50). The paragraph after the blank line is plain text,
    which text written as one quote rarely means. Named is the first line of
    that paragraph. Not named is what mldoc still reads as intended: a line
    that opens a quote again, and a property line, which is no paragraph; the
    paragraph after the property is named. A ``>`` with nothing after it
    opens no quote after a blank line, so it is named too.

    Blank means spaces, tabs, a form feed or a carriage return: mldoc reads a
    no-break space as text and goes on with the quote. Inside a code block or
    a closed org ``#+BEGIN_`` ... ``#+END_`` block nothing is a quote. List, heading and unclosed fence
    lines after a blank line are refused by #47 before this runs.
    """
    lines = content.split("\n")
    inside, _ = code_block_lines(lines)
    org = _org_block_lines(lines, inside)
    breaks, quoted, blank = [], False, False
    for number, (line, code, in_org) in enumerate(zip(lines, inside, org), start=1):
        if code or is_fence(line) or in_org:
            quoted = blank = False
        elif not line.strip(" \t\r\f"):
            blank = quoted
        elif blank and (_EMPTY_QUOTE_LINE_RE.match(line)
                        or not (_QUOTE_LINE_RE.match(line) or PROPERTY_LINE_RE.match(line))):
            breaks.append(number)
            quoted = blank = False
        elif _QUOTE_LINE_RE.match(line):
            quoted, blank = True, False
    return breaks


def quote_break_note(tree: list) -> str | None:
    """A ``Note:`` for every block in ``tree`` whose quote stops at a blank line.

    A note, not a refusal: the text is written as sent and stays one block,
    and a quote followed by a paragraph may be meant. Callers pass the nodes
    they are about to write and print the note on stderr, like the one for
    dropped id:: lines. Line numbers count within a block, so each is named
    with the block's first line.
    """
    found = []

    def walk(nodes):
        for node in nodes:
            content = node.get("content") or ""
            numbers = quote_break_lines(content)
            if numbers:
                first = content.split("\n", 1)[0].strip()
                first = first if len(first) <= 40 else first[:39] + "…"
                lines = ", ".join(f"line {n}" for n in numbers)
                found.append(f'"{first}" {lines}')
            walk(node.get("children") or [])

    walk(tree)
    if not found:
        return None
    return ("Note: a quote ends at a blank line, and Logseq shows the line after "
            f"it as plain text, not quoted: block {'; block '.join(found)}. To "
            "keep a line in the quote, start the blank line before it with \">\".")


def note_quote_breaks(tree: list) -> None:
    """Print :func:`quote_break_note` for ``tree`` on stderr, if there is one."""
    note = quote_break_note(tree)
    if note:
        click.echo(note, err=True)


_HEADING_SUFFIX_RE = re.compile(r'(\s*\{\{[^}]*\}\})+\s*$')

# Logseq stores block ids as RFC 4122 UUIDs. A value that is not one cannot
# become a block id, so a tree carrying one has to be refused before the write
# rather than after: the batch call answers null either way and the per-block
# call would simply ignore the option.
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def tree_without_block_ids(tree: list) -> list:
    """``tree`` with :func:`without_block_ids` applied to every node."""
    return [
        {**block,
         "content": without_block_ids(block.get("content", "")),
         "children": tree_without_block_ids(block.get("children") or [])}
        for block in tree or [] if isinstance(block, dict)
    ]


def collect_block_ids(tree: list) -> list:
    """Every ``id::`` value in ``tree``, DFS pre-order.

    Used both to warn that ids would be dropped and to validate them before a
    write that promises to keep them.
    """
    found = []
    for block in tree or []:
        if not isinstance(block, dict):
            continue
        value = block_id_property(block.get("content", ""))
        if value:
            found.append(value)
        found.extend(collect_block_ids(block.get("children") or []))
    return found


def blocks_with_several_ids(tree: list) -> list:
    """The ``id::`` values of every block in ``tree`` that carries more than one."""
    found = []
    for block in tree or []:
        if not isinstance(block, dict):
            continue
        values = [value for _, value in id_lines(block.get("content", "")) if value]
        if len(values) > 1:
            found.extend(values)
        found.extend(blocks_with_several_ids(block.get("children") or []))
    return found


def invalid_block_ids(tree: list) -> list:
    """The ``id::`` values in ``tree`` that are not RFC 4122 UUIDs."""
    return [v for v in collect_block_ids(tree) if not _UUID_RE.match(v)]


class BlockIdError(ValueError):
    """The id:: contract refuses the content: ``--keep-ids`` cannot be
    honoured, or without it nothing but ids was sent. ``field`` names the
    offending ``ids`` in a JSON error (``ambiguous_ids``, ``repeated_ids``,
    ``invalid_ids``, ``existing_ids`` or ``dropped_ids``)."""

    def __init__(self, message: str, field: str, ids: list):
        super().__init__(message)
        self.field = field
        self.ids = ids


# The one wording for text that dropping its id:: lines left empty (#56, #67).
BESIDES_IDS = "holds nothing but id:: lines, and those are dropped"
NOTHING_BESIDES_IDS = f"The content {BESIDES_IDS}: nothing is left to write. Nothing was written."


def uuids_in_use(api, ids: list) -> list:
    """Those of ``ids`` (well-formed uuids) that a block or a page has.

    Named positively, by what the entity carries (measured, 0.10.15): a block
    has ``:block/page``, a page ``:block/name``. The placeholder Logseq keeps
    for a ``((ref))`` whose target does not exist has neither, and a
    --keep-ids write takes it over (#31). Asking only for ``:block/page`` let
    a page's uuid through as if it were a placeholder.
    """
    if not ids:
        return []
    literals = " ".join(f'#uuid "{i.lower()}"' for i in ids)
    rows = api.datascript_query(
        f"[:find ?u :where [?b :block/uuid ?u] "
        f"(or [?b :block/page _] [?b :block/name _]) "
        f"[(contains? #{{{literals}}} ?u)]]") or []
    found = {str(row[0]).lower() for row in rows if row}
    return [i for i in ids if i.lower() in found]


def dropped_ids_note(count: int, way: str = None) -> str:
    """The note for ``count`` id:: lines a write drops, with ``way`` to keep
    them; the default is the --keep-ids of the commands that have it."""
    return (f"Note: {count} id:: propert(ies) in the content will be dropped; "
            "Logseq mints new UUIDs and any ((uuid)) pointing at the old ones "
            "will dangle. " + (way or "Pass --keep-ids to preserve them (for "
                               "moving or restoring an outline; ids that still "
                               "exist are refused)."))


def without_block_ids_noted(contents: list) -> tuple:
    """``contents``, the texts of blocks a writer without --keep-ids sends,
    with their id:: lines removed, and the note saying so (``None`` if there
    were none). An id:: line would become the block's uuid (#56)."""
    dropped = collect_block_ids([{"content": c} for c in contents])
    if not dropped:
        return contents, None
    return [without_block_ids(c) for c in contents], dropped_ids_note(
        len(dropped), way="To write a block under a uuid of your choosing, use "
                          "insert-block --keep-ids.")


def without_foreign_block_ids(content: str, own: str) -> tuple:
    """``content`` without the id:: lines that name another uuid than
    ``own``, and the note saying so (``None`` if there were none).

    For an update of the block ``own``: its own line is what getBlock hands
    out, and writing it back keeps the uuid (measured, #56). Another one would
    become the block's uuid once Logseq reads the file again, and leave every
    ((ref)) to it dangling, so it goes, as it would in any other write.
    """
    kept, dropped = [], 0
    for line, value in id_lines(content):
        if value and value.lower() != own.lower():
            dropped += 1
        else:
            kept.append(line)
    if not dropped:
        return content, None
    return "\n".join(kept), (
        f"Note: {dropped} id:: line(s) naming another block's uuid will be "
        "dropped: a block keeps its uuid when its text changes, and taking "
        "that one would leave every ((ref)) to this block dangling. move-block "
        "moves a block with its uuid.")


def _tree_texts(tree: list):
    """The content of every node in ``tree``, DFS pre-order."""
    for block in tree:
        yield block.get("content", "")
        yield from _tree_texts(block.get("children") or [])


def check_block_ids(api, tree: list, keep_ids: bool):
    """Apply the ``id::`` contract to ``tree`` before any of it is written.

    insert-block (--tree and --content), add-note-content, add-journal-block
    and add-journal-content go through this, so none of them can drop an id
    in silence again (#1 fixed insert-block --tree alone, and the others kept
    the defect). The writers without --keep-ids decide their own contract, and
    LogseqAPI refuses a line none of them decided on (#56). Returns a note for
    stderr when ids would be dropped, or ``None``. Without ``keep_ids`` raises
    :class:`BlockIdError` when dropping them leaves no text at all. With
    ``keep_ids`` raises it for
    an id that cannot become a block id, and for one a block already has:
    that is the copy case, and insertBatchBlock would give the uuid to a second
    block without a word (measured), leaving two blocks one uuid.
    """
    ids = collect_block_ids(tree)
    if not ids:
        return None
    if not keep_ids:
        # Text that was nothing but its ids leaves nothing to write, and an
        # empty block, or with --upsert-heading an emptied one, is no success
        # (#67). One empty block among others stays: a copied block that held
        # only its id was empty.
        if not any(without_block_ids(t).strip() for t in _tree_texts(tree)):
            raise BlockIdError(NOTHING_BESIDES_IDS, "dropped_ids", ids)
        return dropped_ids_note(len(ids))
    several = blocks_with_several_ids(tree)
    if several:
        # Only one can be the block's id, and which one a batch keeps is not
        # for the CLI to guess: the other would reach the graph unchecked.
        raise BlockIdError(
            f"A block carries more than one id:: line: {', '.join(several[:3])}"
            f"{' ...' if len(several) > 3 else ''}. A block has one uuid; keep the "
            "line it should have. Nothing was written.", "ambiguous_ids", several)
    folded = [i.lower() for i in ids]  # Logseq's uuids are lower-case
    repeated = sorted({i for i in ids if folded.count(i.lower()) > 1})
    if repeated:
        # insertBatchBlock does not check (measured): both blocks would get
        # the uuid, and the graph would hold two blocks under one id.
        raise BlockIdError(
            f"{len(repeated)} id:: value(s) appear more than once in the content: "
            f"{', '.join(repeated[:3])}{' ...' if len(repeated) > 3 else ''}. "
            "One uuid can belong to one block only. Nothing was written.",
            "repeated_ids", repeated)
    bad = invalid_block_ids(tree)
    if bad:
        raise BlockIdError(
            f"{len(bad)} id:: value(s) are not valid UUIDs and cannot become "
            f"block ids: {', '.join(bad[:3])}{' ...' if len(bad) > 3 else ''}. "
            "Nothing was written.", "invalid_ids", bad)
    taken = uuids_in_use(api, ids)
    if taken:
        raise BlockIdError(
            f"{len(taken)} id:: value(s) already belong to a block or page in the graph: "
            f"{', '.join(taken[:3])}{' ...' if len(taken) > 3 else ''}. Keeping "
            "them would give two blocks one uuid; drop --keep-ids to copy with "
            "new ids, or move the original with move-block. Nothing was written.",
            "existing_ids", taken)
    # An id that only a ((ref)) still holds is not refused: Logseq keeps a
    # placeholder under it, which insert_tree_keeping_ids takes over (#31).
    return None


def _write_one_keeping_id(api, content, where, target, written_before):
    """One block through :func:`insert_tree_keeping_ids`, answered as the
    block, the way insertBlock and appendBlockInPage answer."""
    uuid, = insert_tree_keeping_ids(api, [{"content": content, "children": []}],
                                    where, target, written_before=written_before)
    return api.get_block(uuid, include_children=False) or {"uuid": uuid}


def append_in_page(api, page_name: str, content: str, keep_ids: bool, *,
                   written_before: int = 0):
    """``appendBlockInPage``, or with ``keep_ids`` the same position through
    :func:`insert_tree_keeping_ids`. Answers the block, as the API does."""
    if not keep_ids:
        return api.append_block_in_page(page_name, content)
    return _write_one_keeping_id(api, content, "page_end", page_name, written_before)


def insert_block_at(api, target: str, content: str, *, sibling: bool,
                    before: bool = False, keep_ids: bool = False,
                    written_before: int = 0):
    """``insertBlock`` for one block, or with ``keep_ids`` the same position
    through :func:`insert_tree_keeping_ids`. Answers the block, as the API does.

    insertBlock's positions: not ``sibling`` is the last child (the first with
    ``before``), ``sibling`` right after the target (before it with ``before``).
    """
    if not keep_ids:
        # The options as they have always been sent: a plain child append
        # never carried "before".
        opts = {"sibling": sibling, "before": before} if sibling or before else {"sibling": False}
        return api.insert_block(target, content, opts)
    where = (("before" if before else "after") if sibling
             else ("first_child" if before else "last_child"))
    return _write_one_keeping_id(api, content, where, target, written_before)




def normalize_heading(text: str) -> str:
    """Normalize a heading string for comparison.

    Uses only the first line: a heading block can carry trailing Logseq
    block-properties (``id::``, ``collapsed::``, ...) on the lines *after* the
    heading text, so the heading itself is line one. Then strips trailing renderer
    macros (e.g. ``{{renderer :todomaster}}``) and collapses whitespace, so
    equivalent headings compare equal regardless of decoration. Enables matching
    ``## Tasks`` against ``## Tasks {{renderer :todomaster}}`` or against
    ``## Focus Topics W24\nid:: fedcba98-...\ncollapsed:: true``.
    """
    if not text:
        return ""
    first_line = text.strip().split('\n', 1)[0]
    stripped = _HEADING_SUFFIX_RE.sub('', first_line)
    return ' '.join(stripped.split())


def find_heading(api, page_name: str, heading: str) -> str | None:
    """Find an existing heading block's UUID on a page; never create one.

    Split out of :func:`find_or_create_heading` for the --dry-run paths: a
    preview that creates the heading it only meant to report has already written
    to the graph, which is the one thing --dry-run promises not to do.

    Returns the UUID, or None if no block on the page matches the heading.
    """
    target = normalize_heading(heading)
    for block in api.get_page_blocks_tree(page_name) or []:
        if normalize_heading(block.get("content", "")) == target:
            return block.get("uuid")
    return None


def find_or_create_heading(api, page_name: str, heading: str) -> str | None:
    """Find heading block UUID on page, create if missing.

    Matches existing headings tolerantly via :func:`normalize_heading` so that
    renderer macros and whitespace variations do not cause spurious duplicates.

    Returns the UUID of the heading block, or None if creation failed.
    """
    target = normalize_heading(heading)
    found = find_heading(api, page_name, heading)
    if found:
        return found

    # Heading doesn't exist — create it
    heading_result = api.append_block_in_page(page_name, heading)
    if isinstance(heading_result, dict):
        uuid = heading_result.get("uuid")
        if uuid:
            return uuid
    elif isinstance(heading_result, list) and heading_result:
        uuid = heading_result[0].get("uuid")
        if uuid:
            return uuid

    # Fallback: re-fetch blocks to find the just-created heading
    blocks = api.get_page_blocks_tree(page_name) or []
    for block in blocks:
        if normalize_heading(block.get("content", "")) == target:
            return block.get("uuid")

    return None


def _normalize_json_tree(nodes) -> list:
    """Coerce a JSON-loaded tree into the shape ``parse_hierarchical_content`` produces.

    Each node must have a ``content`` string; ``children`` defaults to an empty list.
    """
    if not isinstance(nodes, list):
        raise click.BadParameter("Tree JSON must be an array of node objects.")
    out = []
    for node in nodes:
        if not isinstance(node, dict) or "content" not in node:
            raise click.BadParameter(
                "Tree JSON node must be an object with a 'content' field."
            )
        children = node.get("children") or []
        out.append({
            "content": str(node["content"]),
            "children": _normalize_json_tree(children),
        })
    return out


def parse_tree_input(raw: str) -> list:
    """Parse tree input from either JSON or tab-indented text.

    Auto-detection: first non-whitespace character ``[`` or ``{`` is treated as
    JSON, otherwise the input is fed through :func:`parse_hierarchical_content`.
    Returns a list of ``{"content": str, "children": [...]}`` nodes.
    """
    if raw is None:
        return []
    stripped = raw.lstrip()
    if not stripped:
        return []
    if stripped[0] in "[{":
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Invalid JSON tree: {e}")
        if isinstance(data, dict):
            data = [data]
        return _normalize_json_tree(data)
    return parse_hierarchical_content(raw)


def require_content(content: str, option: str = "--content") -> str:
    """Reject content that is empty or only whitespace, before any write.

    The mirror of the guard in :func:`read_content_file`, for text arriving on
    the command line. ``--content "$(cat missing.md)"`` collapses to an empty
    string when the substitution fails, and the shell reports that on stderr
    while still exiting 0 -- so without this check the CLI writes an empty
    block and reports success. A block with no content is never the intent,
    which is why this is an error rather than a warning.

    Returns the content unchanged, so callers can wrap the value in place.
    """
    if not content.strip():
        raise click.BadParameter(f"{option} is empty")
    return content


def require_text_besides_ids(content: str) -> str:
    """Refuse text that dropping its id:: lines left empty (#56): there is
    nothing the caller meant left to write, and writing an empty block, or
    emptying the one updated, reports a success that is none."""
    if not content.strip():
        raise click.BadParameter(f"--content {BESIDES_IDS}: nothing is left to write")
    return content


def read_content_file(path: str, option: str = "--content-file") -> str:
    """Read block content from a file, for ``--content-file``.

    The file is read as UTF-8 and returned as written (minus a trailing
    newline), so tab-indented hierarchies and flush top-level bullets survive
    unchanged. Unlike ``--content``, no shell quoting sits between the text and
    the CLI, which is why this is the safe path for content with apostrophes,
    quotes or umlauts. Two things are not text and go: a BOM, and the ``\\r``
    of a CRLF or CR line ending.

    ``-`` reads stdin instead, the convention every Unix tool shares: content
    that is already in a pipe would otherwise need a temporary file, which is
    the one detour this option exists to remove. A file literally named ``-``
    is then unreachable — the convention wins, and ``./-`` still names the file.
    stdin is read as bytes and decoded here, like the file: ``sys.stdin``
    decodes by the locale and keeps ``\\r``, so under ``LC_ALL=C`` invalid
    bytes went on as lone surrogates, and a pipe wrote what the same file
    did not.

    Raises :class:`click.BadParameter` for a missing, unreadable, non-UTF-8 or
    effectively empty file, so the caller fails before any write. ``option``
    is the flag the message names: ``insert-block --tree-file`` reads through
    here too.
    """
    if path == "-":
        where = "stdin"
        data = sys.stdin.buffer.read()
    else:
        where = path
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            raise click.BadParameter(f"{option} not found: {path}")
        except IsADirectoryError:
            raise click.BadParameter(f"{option} is a directory: {path}")
        except OSError as e:
            raise click.BadParameter(f"{option} cannot be read: {path} ({e})")
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise click.BadParameter(f"{option} is not valid UTF-8: {where} ({e})")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        raise click.BadParameter(f"{option} is empty: {where}")
    return raw.rstrip("\n")


def content_or_file(content, content_file, *, required=True):
    """The text of ``--content`` or of ``--content-file``, whichever was given.

    One place for the either/or, so the commands that use it answer both,
    neither and a bad file the same way. ``insert-block`` passes
    ``required=False``: ``--tree`` is its other source, and it words "neither"
    itself. The file's text is
    returned as if it had been passed as ``--content``: what the command does
    with it is unchanged. ``add-journal-block`` does not use this, since its
    ``--content-file`` means a tree, not ``--content``.
    """
    if content_file is None:
        if content is None and required:
            raise click.UsageError("Missing option '--content' (or '--content-file').")
        return content
    if content is not None:
        raise click.UsageError("Specify either --content or --content-file, not both.")
    return read_content_file(content_file)


def block_uuid_from_result(result):
    """Extract a block UUID from a Logseq insert/append API result.

    The API returns a block map ``{"uuid": "...", ...}`` on success, a bare
    UUID string in some paths, or ``None`` when the operation silently failed
    (e.g. an unknown anchor UUID — the API answers HTTP 200 with ``null``).
    Returns the UUID string or ``None``.
    """
    if isinstance(result, dict):
        return result.get("uuid")
    if isinstance(result, str):
        return result
    return None


# Why some writes are verified by reading them back
# ---------------------------------------------------
# Three API methods answer ``null`` for a successful call as well as a failed
# one, so their return value carries no success signal at all (each verified
# against a live graph, 2026-08-22):
#
#   insertBatchBlock  null on success, on partial write, and on failure
#   moveBlock         null on success, on a missing target, and on a refusal
#                     (Logseq declines to move a block into its own subtree)
#   updateBlock       null on success and on a non-existent block UUID
#
# For these, :func:`require_insert` cannot help: there is no UUID to miss. The
# callers therefore re-read the affected blocks and compare against what they
# intended to write. That costs one extra API call per operation and is a
# workaround, not a design choice.
#
# If a future Logseq version returns the written block (or any error signal) for
# these methods, drop the verifying read and route them through
# ``require_insert`` like every other write, keeping the read only where a count
# has to be compared. The affected call sites are
# :func:`insert_block_tree_batched`, :func:`move_block_verified` and the
# ``replace-text`` command; they are the ones to revisit.
#
# ``insertBlock`` and ``appendBlockInPage`` do return the new block, which is
# why ``require_insert`` works for them and is the cheaper check to prefer.


def require_insert(result, what: str, *, written_so_far: int = 0) -> str:
    """Return the UUID of a just-inserted block, or abort loudly.

    The Logseq API answers a failed insert/append with HTTP 200 + ``null``
    instead of an error status, so a missing UUID is the only failure signal.
    Callers that must not continue on a silent write failure use this to turn
    that ``null`` into a non-zero exit with a clear message, rather than
    reporting a phantom success.

    ``written_so_far`` is the number of blocks already persisted in this
    operation. There is no rollback (the API offers none), so on a multi-block
    insert those blocks stay. Saying "Nothing was written" there would be a
    lie that invites a retry and thus duplicates, so the message names the
    partial state instead.
    """
    uuid = block_uuid_from_result(result)
    if not uuid:
        if written_so_far:
            tail = (
                f"{written_so_far} block(s) were already written and remain "
                "(no rollback available) — check the page before retrying, or "
                "the retry will duplicate them."
            )
        else:
            tail = "Nothing was written."
        raise click.ClickException(
            f"Logseq did not create {what} (API returned no block UUID). "
            "Likely cause: the target/anchor UUID does not exist, or the page "
            f"is not loaded. {tail}"
        )
    return uuid


# The lines insertBatchBlock takes out of a block's content when it is not
# asked to keep uuids (measured, 0.10.15): "id:: " with any value or none, in
# any case, inside a code block too, behind any whitespace JavaScript's \s
# knows (space, tab, form feed, carriage return, no-break space and vertical
# tab measured; \ufeff is in \s there and not in Python's). Not "id::"
# alone, not "custom-id::". insertBlock writes the same text as given.
_BATCH_DROPS_RE = re.compile(r'^[\s\ufeff]*id:: ', re.IGNORECASE)


def _quotes_an_id_line(tree: list) -> bool:
    """Whether a node of ``tree`` has a line the batch would drop that is not
    the block's id: one inside a code block, say. Such a tree goes block by
    block, so the text is written as given."""
    for block in tree or []:
        if not isinstance(block, dict):
            continue
        for line, value in id_lines(block.get("content", "")):
            if value is None and _BATCH_DROPS_RE.match(line):
                return True
        if _quotes_an_id_line(block.get("children") or []):
            return True
    return False


def insert_block_tree_with_uuids(api, tree: list, parent_uuid: str, *, strict: bool = True, batch: bool = True, keep_ids: bool = False, _written: int = 0) -> list:
    """Recursively insert a parsed tree under ``parent_uuid``.

    Returns the UUIDs of inserted blocks in DFS pre-order (parent before
    children, siblings in declaration order).

    ``strict`` (the default) turns a silent write failure into a hard abort via
    :func:`require_insert`. Logseq answers a failed insert with HTTP 200 +
    ``null``, so without this the function pushes a ``None`` UUID, skips that
    block's children, and the caller reports success for content that was never
    written — the worst outcome for a journal entry, since the text is gone and
    nothing says so. ``strict=False`` is only for callers that deliberately
    tolerate partial writes; it must never be the default.

    ``batch`` (the default) sends a multi-block tree as a single
    ``insertBatchBlock`` call via :func:`insert_block_tree_batched`, which
    verifies the write by re-reading. It requires ``strict``, because the
    non-strict contract is to return a ``None`` per unwritten block, and the
    batch path cannot say which nodes those were: the API reports neither an
    error nor UUIDs. Set ``batch=False`` to force the per-block path when the
    caller needs a UUID for every node as it is written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "last_child", parent_uuid,
                                       written_before=_written)
    if strict and batch and count_blocks(tree) > 1 and not _quotes_an_id_line(tree):
        # One round-trip instead of N. NOT atomic: a batch can still write only
        # part of its nodes (verified against a live graph - a malformed node is
        # skipped while its siblings land), and it answers null either way. That
        # is why the batch path verifies by re-reading instead of trusting the
        # response. Single blocks keep the per-block path, which returns the
        # UUID directly and needs no verifying read.
        return insert_block_tree_batched(api, tree, parent_uuid)

    uuids = []
    for block in tree:
        result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
        if strict:
            new_uuid = require_insert(result, "a block", written_so_far=_written + len(uuids))
        else:
            new_uuid = block_uuid_from_result(result)
        uuids.append(new_uuid)
        children = block.get("children") or []
        if new_uuid and children:
            uuids.extend(insert_block_tree_with_uuids(
                api, children, new_uuid, strict=strict,
                _written=_written + len(uuids)))
    return uuids


def _collect_child_uuids(node) -> list:
    """UUIDs of a getBlock(includeChildren=True) subtree, DFS pre-order."""
    out = []
    for child in (node.get("children") or []):
        if not isinstance(child, dict):
            continue  # a children list of bare UUID refs carries no content
        if child.get("uuid"):
            out.append(child["uuid"])
        out.extend(_collect_child_uuids(child))
    return out


def insert_block_tree_batched(api, tree: list, parent_uuid: str) -> list:
    """Insert a tree under ``parent_uuid`` in ONE API call, then verify.

    ``insertBatchBlock`` replaces N ``insertBlock`` round-trips with one. It is
    NOT atomic: verified against a live graph, a batch containing a malformed
    node writes its siblings and skips that node, so a partial write is still
    possible - one call is fewer chances to fail, not none.

    Worse, the API answers ``null`` whether it wrote everything, part of it, or
    nothing, so the return value carries no success signal at all. That is
    exactly the silent-write-failure shape this codebase refuses to accept, so
    the write is proven instead: the parent's children are read back and the new
    UUIDs counted. The read also recovers the UUIDs the batch call withholds.
    See the note above :func:`require_insert` for when this read can be dropped.

    Positioning: with ``sibling: false`` the batch lands at the HEAD of the child
    list (``before: false`` does not change it), so to append we anchor on the
    last existing child with ``sibling: true``. With no children yet, the parent
    itself is the anchor.

    Returns the new UUIDs in DFS pre-order. Raises ``ClickException`` if the
    graph does not show the expected number of new blocks afterwards.
    """
    if not tree:
        return []

    before = api.get_block(parent_uuid, include_children=True)
    if not before:
        raise click.ClickException(
            f"Cannot insert: block {parent_uuid[:8]}... not found "
            "(the target UUID does not exist, or the page is not loaded). "
            "Nothing was written."
        )
    existing = [c for c in (before.get("children") or []) if isinstance(c, dict)]
    before_uuids = set(_collect_child_uuids(before))

    if existing and existing[-1].get("uuid"):
        anchor, opts = existing[-1]["uuid"], {"sibling": True}
    else:
        anchor, opts = parent_uuid, {"sibling": False}

    api.insert_batch_block(anchor, tree, opts)

    after = api.get_block(parent_uuid, include_children=True)
    after_uuids = _collect_child_uuids(after) if after else []
    new = [u for u in after_uuids if u not in before_uuids]

    expected = count_blocks(tree)
    if len(new) != expected:
        raise click.ClickException(
            f"Batch insert wrote {len(new)} of {expected} block(s) under "
            f"{parent_uuid[:8]}... . The API reports no error for this, so the "
            "graph was re-read to check. Verify the page before retrying, or the "
            "retry will duplicate what did land."
        )
    return new


# Where a --keep-ids write lands, and how insertBatchBlock is asked for it
# ------------------------------------------------------------------------
# Every write that keeps ids goes through insertBatchBlock with keepUUID and
# the id as an ``id::`` line: it is the one call that takes over the
# placeholder Logseq keeps for a ((ref)) whose block is gone, where insertBlock
# and appendBlockInPage answer "Custom block UUID already exists" (#31). One
# call for every position, rather than a second path for placeholders only, so
# a restore cannot behave differently from a move depending on which ids the
# graph happens to hold.
#
# The positions, measured on 0.10.15:
#
#   last_child   after the parent's last child (sibling), or under a parent
#                with none (not sibling)
#   first_child  under the parent (not sibling): the batch goes to the head
#   after        after the anchor (sibling)
#   before       before the anchor (sibling + before), except before the
#                page's first block: there Logseq writes every node with "* "
#                in front of its content. The batch goes after that block and
#                its roots are moved before it, which moveBlock does cleanly.
#   page_end     after the page's last top-level block (sibling). On a page
#                with no blocks, the page uuid as anchor has the same "* "
#                defect, so a stand-in block is appended, written after, and
#                removed.
#
# insertBatchBlock answers null whatever it did, so the page is read back:
# the new blocks must be there in the number sent, carry the ids asked for,
# and sit where they were sent.


def _page_blocks_by_uuid(tree: list) -> dict:
    """``uuid -> (siblings, index, parent block or None)`` over a page tree."""
    found = {}

    def walk(blocks, parent):
        for i, block in enumerate(blocks):
            found[block["uuid"]] = (blocks, i, parent)
            walk(block.get("children") or [], block)
    walk(tree, None)
    return found


def _preorder_uuids(tree: list) -> list:
    out = []
    for block in tree:
        out.append(block["uuid"])
        out.extend(_preorder_uuids(block.get("children") or []))
    return out


def _landed_where_sent(where, target, siblings, first, count, parent) -> bool:
    """Whether ``count`` roots starting at ``siblings[first]`` sit at ``where``
    relative to ``target``, in the page tree read back."""
    end = first + count
    if where == "last_child":
        return (parent or {}).get("uuid") == target and end == len(siblings)
    if where == "first_child":
        return (parent or {}).get("uuid") == target and first == 0
    if where == "after":
        return first > 0 and siblings[first - 1]["uuid"] == target
    if where == "before":
        return end < len(siblings) and siblings[end]["uuid"] == target
    return parent is None and end == len(siblings)  # page_end


def insert_tree_keeping_ids(api, tree: list, where: str, target: str, *,
                            written_before: int = 0) -> list:
    """Write ``tree`` at ``where`` relative to ``target``, keeping its ids.

    ``where`` is one of the positions in the note above; ``target`` is a block
    uuid, or for ``page_end`` a page name. The ids have been checked by
    :func:`check_block_ids` before: insertBatchBlock would give a real
    block's uuid to a second block without a word (measured).
    ``written_before`` counts blocks this command already wrote, for the
    message when this write fails.

    Returns the new uuids in DFS pre-order. Raises ``ClickException`` when the
    target is missing or the read-back disagrees with what was sent.
    """
    if not tree:
        return []
    earlier = (f" {written_before} block(s) written earlier by this command "
               "remain." if written_before else "")
    if where == "page_end":
        page_name = target
        if api.get_page(page_name) is None:
            # appendBlockInPage creates a missing page (with its empty block)
            # and writes after it (measured); this position does the same.
            api.create_page(page_name)
    else:
        # Logseq's uuids are lower-case; a target typed in capitals is the
        # same block.
        target = target.lower()
        block = api.get_block(target, include_children=False)
        page = api.get_page((block or {}).get("page", {}).get("id")) if block else None
        if not page:
            raise click.ClickException(
                f"Cannot insert: block {target[:8]}... not found (the uuid does not "
                f"exist, or its page is not loaded). Nothing more was written.{earlier}")
        page_name = page.get("originalName") or page.get("name")

    before_tree = api.get_page_blocks_tree(page_name) or []
    index = _page_blocks_by_uuid(before_tree)
    if where != "page_end" and target not in index:
        raise click.ClickException(
            f"Cannot insert: block {target[:8]}... is not on '{page_name}' as read. "
            f"Nothing more was written.{earlier}")

    opts = {"keepUUID": True}
    stand_in = None
    lead_first = False
    if where == "last_child":
        kids = index[target][0][index[target][1]].get("children") or []
        anchor = kids[-1]["uuid"] if kids else target
        opts["sibling"] = bool(kids)
    elif where == "first_child":
        anchor, opts["sibling"] = target, False
    elif where == "after":
        anchor, opts["sibling"] = target, True
    elif where == "before":
        siblings, i, parent = index[target]
        lead_first = parent is None and i == 0
        anchor, opts["sibling"] = target, True
        if not lead_first:
            opts["before"] = True
    elif where == "page_end":
        if before_tree:
            anchor, opts["sibling"] = before_tree[-1]["uuid"], True
        else:
            stand_in = require_insert(api.append_block_in_page(page_name, ""),
                                      f"a block on '{page_name}'")
            anchor, opts["sibling"] = stand_in, True
    else:
        raise ValueError(f"unknown position {where!r}")

    try:
        api.insert_batch_block(anchor, tree, opts)
    finally:
        if stand_in:
            api.remove_block(stand_in)

    expected = count_blocks(tree)
    old = set(_preorder_uuids(before_tree))
    after_tree = api.get_page_blocks_tree(page_name) or []
    new = [u for u in _preorder_uuids(after_tree) if u not in old and u != stand_in]
    if len(new) != expected:
        raise click.ClickException(
            f"Batch insert wrote {len(new)} of {expected} block(s) on '{page_name}'. "
            "The API reports no error for this, so the page was re-read to check. "
            "Verify the page before retrying, or the retry will duplicate what "
            f"did land.{earlier}")
    missing = [i for i in collect_block_ids(tree) if i.lower() not in new]
    if missing:
        raise click.ClickException(
            f"{expected} block(s) were written on '{page_name}', but not under "
            f"the ids asked for: {', '.join(missing[:3])}"
            f"{' ...' if len(missing) > 3 else ''}. Logseq minted new ones; the "
            f"((refs)) to these ids still point nowhere. Check the page.{earlier}")

    index = _page_blocks_by_uuid(after_tree)
    roots = [u for u in new if index[u][2] is None or index[u][2]["uuid"] not in new]
    if lead_first:
        # Written after the first block; the roots go before it, in order.
        # ``new`` keeps its order: only that block moved relative to them.
        for root in roots:
            api.move_block(root, target, {"before": True})
        after_tree = api.get_page_blocks_tree(page_name) or []
        index = _page_blocks_by_uuid(after_tree)

    siblings, first, parent = index[roots[0]]
    run = [b["uuid"] for b in siblings[first:first + len(roots)]]
    if run != roots or not _landed_where_sent(where, target, siblings, first,
                                              len(roots), parent):
        raise click.ClickException(
            f"{expected} block(s) were written on '{page_name}' with their ids, but "
            f"not where they were sent ({where.replace('_', ' ')} "
            f"{target if where == 'page_end' else target[:8] + '...'}). "
            f"Check the page; move-block puts them in place and keeps their ids.{earlier}")
    return new


def _sibling_uuids_in_order(api, block: dict) -> list:
    """UUIDs of ``block`` and its siblings, in order.

    ``getBlock`` reports a parent as ``{"id": <int>}`` with no UUID, but it also
    accepts that id as its argument, so a nested block's sibling list is
    reachable in one read without walking the page tree.

    That does not hold at the top level. There the parent is the page, and
    ``getBlock`` answers ``null`` for a page id (measured, 0.10.15), so the
    order comes from the page tree. ``getPageBlocksTree`` in turn refuses a
    numeric id ("Expected string, got: number") and needs the page name first.
    """
    parent_id = (block.get("parent") or {}).get("id")
    if parent_id is None:
        return []
    if parent_id == (block.get("page") or {}).get("id"):
        page = api.get_page(parent_id) or {}
        children = api.get_page_blocks_tree(page["name"]) if page.get("name") else None
    else:
        children = (api.get_block(parent_id, include_children=True) or {}).get("children")
    if not isinstance(children, list):
        return []
    return [c.get("uuid") for c in children if isinstance(c, dict) and c.get("uuid")]


def find_blocks_by_content(api, content: str, page: str = None, use_regex: bool = False) -> list:
    """Blocks whose content matches ``content``, optionally scoped to a page.

    Single source for the content lookup shared by ``find-block`` and the
    ``--where-content`` selectors, so a query fix cannot land in one and miss
    the other. Substring matching happens in datalog; ``use_regex`` pulls the
    candidates and filters them here, because datalog has no regex predicate.
    """
    if use_regex:
        if page:
            query = (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                f' :where [?p :block/name {page_name_literal(page)}]'
                ' [?b :block/page ?p]'
                ' [?b :block/content _]]'
            )
        else:
            query = (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/content _]]'
            )
        raw = api.datascript_query(query) or []
        pattern = re.compile(content)
        return [r[0] for r in raw if r and r[0] and pattern.search(r[0].get("content", ""))]

    content_literal = edn_string(content)
    if page:
        query = (
            '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
            f' :where [?p :block/name {page_name_literal(page)}]'
            ' [?b :block/page ?p]'
            ' [?b :block/content ?c]'
            f' [(clojure.string/includes? ?c {content_literal})]]'
        )
    else:
        query = (
            '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
            ' :where [?b :block/content ?c]'
            f' [(clojure.string/includes? ?c {content_literal})]]'
        )
    raw = api.datascript_query(query) or []
    return [r[0] for r in raw if r and r[0]]


def resolve_single_block(api, content: str, page: str = None, use_regex: bool = False) -> str:
    """UUID of the ONE block matching ``content``, or abort.

    Selecting a write target by text is only safe when the text identifies
    exactly one block. Zero matches and several matches both raise instead of
    picking one: these commands overwrite or delete, so guessing on an ambiguous
    match would destroy the wrong content, and the caller cannot tell afterwards.
    Several matches are listed so the caller can narrow the search or pass --id.
    """
    matches = find_blocks_by_content(api, content, page=page, use_regex=use_regex)
    where = f" on page '{page}'" if page else ""
    if not matches:
        raise click.ClickException(
            f"No block matches {content!r}{where}. Nothing was changed.")
    if len(matches) > 1:
        listing = "\n".join(
            f"  {m.get('uuid')}  {(m.get('content') or '')[:70]}"
            for m in matches[:10]
        )
        more = f"\n  ... and {len(matches) - 10} more" if len(matches) > 10 else ""
        raise click.ClickException(
            f"{len(matches)} blocks match {content!r}{where}; refusing to guess "
            f"which one to write to. Narrow the search (--page, a longer text) or "
            f"pass --id:\n{listing}{more}"
        )
    uuid = matches[0].get("uuid")
    if not uuid:
        raise click.ClickException(
            f"Match for {content!r} carries no UUID. Nothing was changed.")
    return uuid


def check_move(api, src_uuid: str, target_uuid: str) -> None:
    """Refuse a move Logseq would not carry out, before anything is written.

    Shared by the move and its dry run, so a preview cannot promise a move the
    real run refuses. The subtree case is the one refusal Logseq is known for,
    and it gives it by doing nothing, so this is the only place it can be named.
    """
    src_uuid = src_uuid.strip().replace("((", "").replace("))", "")
    target_uuid = target_uuid.strip().replace("((", "").replace("))", "")
    if src_uuid == target_uuid:
        raise click.ClickException("Source and target are the same block.")

    source = api.get_block(src_uuid, include_children=True)
    if not source:
        raise click.ClickException(
            f"Source block {src_uuid[:8]}... not found. Nothing was moved.")
    if not api.get_block(target_uuid, include_children=False):
        raise click.ClickException(
            f"Target block {target_uuid[:8]}... not found. Nothing was moved.")
    if target_uuid in _collect_child_uuids(source):
        raise click.ClickException(
            f"Target {target_uuid[:8]}... lies inside the subtree of "
            f"{src_uuid[:8]}...; a block cannot be moved into its own subtree. "
            "Nothing was moved.")


def move_block_verified(api, src_uuid: str, target_uuid: str, *, before: bool = False) -> None:
    """Move ``src_uuid`` to ``target_uuid``, then prove it landed.

    Unlike copy+remove, the block keeps its UUID, so every ``((block-ref))``
    pointing at it survives the move.

    Position follows what ``moveBlock`` actually does, which is narrower than
    its option names suggest (probed against a live graph): ``before: true``
    places the block as the sibling *before* the target; everything else,
    including the ``sibling: true`` the option list implies, nests it as the
    target's first child. There is no "sibling after" - anchor on the following
    block with ``before`` instead.

    ``moveBlock`` answers ``null`` for a successful move, a non-existent target
    AND a refused one (Logseq declines to move a block into its own subtree, and
    says so only by doing nothing), so the response proves nothing. The subtree
    case is therefore refused here before the call, where it can be named, and
    the move itself is verified by re-reading: for a child move the block must
    appear among the target's children, for ``before`` directly in front of the
    target. See the note above :func:`require_insert` for when this read can be
    dropped.
    """
    src_uuid = src_uuid.strip().replace("((", "").replace("))", "")
    target_uuid = target_uuid.strip().replace("((", "").replace("))", "")
    check_move(api, src_uuid, target_uuid)
    api.move_block(src_uuid, target_uuid, {"before": True} if before else {"children": True})

    landed = api.get_block(target_uuid, include_children=True) or {}
    if before:
        # The block must sit directly in front of the target under the same
        # parent. Checking only "same parent" would pass a move that did
        # nothing, since source and target often already share one.
        siblings = _sibling_uuids_in_order(api, landed)
        try:
            ok = siblings.index(src_uuid) + 1 == siblings.index(target_uuid)
        except ValueError:
            ok = False
    else:
        ok = any(
            isinstance(c, dict) and c.get("uuid") == src_uuid
            for c in (landed.get("children") or [])
        )
    if not ok:
        raise click.ClickException(
            f"Move of {src_uuid[:8]}... did not take effect. Logseq reports no error "
            "for this, so the graph was re-read to check; the block is not where it "
            "was sent. Nothing was removed."
        )


def insert_block_tree_as_first_children(api, tree: list, parent_uuid: str, *, keep_ids: bool = False, _written: int = 0) -> list:
    """Insert a parsed tree at the HEAD of ``parent_uuid``'s child list.

    ``insertBlock`` can address the first child position (``sibling: false`` +
    ``before: true``) but has no "nth child" option. Looping over the roots with
    ``before=True`` would therefore push each one ahead of the previous and
    reverse the declaration order. So the first root claims the head position
    and the remaining roots chain as siblings behind it, which preserves the
    order the caller wrote.

    Returns the UUIDs in DFS pre-order, like the sibling/child variants.
    """
    if not tree:
        return []
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "first_child", parent_uuid,
                                       written_before=_written)
    head = api.insert_block(parent_uuid, tree[0]["content"], {"sibling": False, "before": True})
    head_uuid = require_insert(head, "the first child", written_so_far=_written)
    uuids = [head_uuid]
    children = tree[0].get("children") or []
    if children:
        uuids.extend(insert_block_tree_with_uuids(
            api, children, head_uuid, strict=True, _written=_written + len(uuids)))
    if len(tree) > 1:
        uuids.extend(insert_block_tree_as_siblings(
            api, tree[1:], head_uuid, _written=_written + len(uuids)))
    return uuids


def insert_block_tree_as_siblings(api, tree: list, anchor_uuid: str, *, before: bool = False, strict: bool = True, keep_ids: bool = False, _written: int = 0) -> list:
    """Insert a parsed tree as sibling(s) after (or before) ``anchor_uuid``.

    The first top-level node is inserted as a sibling of the anchor; its
    children are nested beneath it; each further top-level node is inserted as
    a sibling after the previous top-level node, preserving declaration order.
    This is the ``--after``/``--before`` counterpart to
    :func:`insert_block_tree_with_uuids` (which only nests under a parent).

    Returns inserted UUIDs in DFS pre-order. ``strict`` (default True) aborts
    on a silent write failure rather than orphaning the remaining nodes.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "before" if before else "after",
                                       anchor_uuid, written_before=_written)
    uuids = []
    cursor = anchor_uuid
    for position, block in enumerate(tree):
        # Only the first root goes before the anchor; each further one after
        # the root just written. Sending every root "before" the one written
        # ahead of it put a, b, c down as c, b, a (measured, 0.10.15).
        ahead = before and position == 0
        result = api.insert_block(cursor, block["content"], {"sibling": True, "before": ahead})
        if strict:
            new_uuid = require_insert(result, "a block", written_so_far=_written + len(uuids))
        else:
            new_uuid = block_uuid_from_result(result)
        uuids.append(new_uuid)
        if not new_uuid:
            # non-strict and the insert failed: stop walking this chain
            break
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(
                api, children, new_uuid, strict=strict,
                _written=_written + len(uuids)))
        cursor = new_uuid
    return uuids


def insert_block_tree_at_page_top(api, tree: list, page_name: str, *, keep_ids: bool = False, _written: int = 0) -> list:
    """Insert tree starting at the top of ``page_name``.

    Top-level nodes use ``append_block_in_page`` (which currently appends; the
    Logseq API has no first-block primitive). Children use insert_block.
    Returns DFS pre-order UUIDs.

    A failed append answers HTTP 200 + ``null``; :func:`require_insert` turns
    that into a hard abort so the caller cannot report success for text that
    was never written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "page_end", page_name,
                                       written_before=_written)
    uuids = []
    for block in tree:
        result = api.append_block_in_page(page_name, block["content"])
        new_uuid = require_insert(
            result, f"a block on '{page_name}'", written_so_far=_written + len(uuids))
        uuids.append(new_uuid)
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(
                api, children, new_uuid, _written=_written + len(uuids)))
    return uuids


def insert_tree_at_page_end(api, page_name: str, tree: list, *, strict: bool = True, keep_ids: bool = False) -> list:
    """Append a parsed tree to a page, returning the inserted block UUIDs.

    It takes the tree, not the text: the caller checked and cleaned that tree
    for ``id::`` lines, and parsing the text again here would write something
    the check never saw.

    Top-level nodes are appended to the page; children use insert_block.
    Returns UUIDs in DFS pre-order (parent before children).

    ``strict`` (the default) aborts on a silent write failure, the same
    contract the other tree inserters follow. Without it this was the one
    remaining path where a page that Logseq has not loaded answers every
    append with HTTP 200 + ``null``, the ``None`` UUIDs still get counted, and
    the caller reports "Added N block(s)" with exit 0 for a journal entry that
    was never written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "page_end", page_name)
    uuids = []

    def insert_tree(blocks, parent_uuid=None):
        for block in blocks:
            if parent_uuid:
                result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
                what = "a block"
            else:
                result = api.append_block_in_page(page_name, block["content"])
                what = f"a block on '{page_name}'"
            if strict:
                new_uuid = require_insert(result, what, written_so_far=len(uuids))
            else:
                new_uuid = block_uuid_from_result(result)
            uuids.append(new_uuid)
            if new_uuid and block["children"]:
                insert_tree(block["children"], new_uuid)

    insert_tree(tree)
    return uuids


# Why a property value is sent as a number only in one narrow case
# ----------------------------------------------------------------
# ``upsertBlockProperty`` stores what it is sent and writes it into the file:
# a string verbatim, a number the way JavaScript prints it. So every value sent
# as a number is rewritten in the file unless printing it gives back the typed
# text. Python's ``int``/``float`` accept far more than that (``01234``,
# ``1.50``, ``1e3``, ``1_0``, non-ASCII digits, ``nan``), and an integer above
# 2^53-1 loses digits as a JavaScript number.
#
# When Logseq reads a file it makes a number only from ASCII digits up to
# 2^53-1, ignoring surrounding whitespace, and keeps the text beside it;
# ``1.50``, ``-7`` and ``1e3`` stay text (measured, 0.10.15). A number is
# therefore sent only where the parser would make one AND it prints back as
# typed. A leading zero is sent as text: the
# file keeps it, and the database holds the text until the file is next read,
# the lesser of the two disagreements. See #35.
_MAX_EXACT_INTEGER = 2**53 - 1


def coerce_property_value(value: str):
    """``value`` as an int where Logseq reads one that prints back as typed.

    Anything else comes back unchanged, ``01234`` included: Logseq reads that
    as 1234, but sent as a number it would lose its zero in the file.

    Single source of truth for property-value typing (shared by set-property,
    set-block-property and the inline --property option). See the note above
    for why the rule is this narrow.
    """
    if not isinstance(value, str):
        return value
    digits = value.strip()  # the parser trims the value before reading it
    # The length bound keeps int() away from Python's digit limit (4300), which
    # raises instead of converting; 2^53-1 has 16 digits.
    if (digits.isascii() and digits.isdigit() and len(digits) <= 16
            and (digits == "0" or not digits.startswith("0"))
            and int(digits) <= _MAX_EXACT_INTEGER):
        return int(digits)
    return value


# Why property keys are checked here and not left to Logseq
# ----------------------------------------------------------
# ``upsertBlockProperty`` stores any key it is handed as ``(keyword key)`` and
# writes ``key:: value`` into the file. The parser that reads the file back is
# stricter (``extract-properties`` in graph-parser/block.cljs): it lower-cases
# the key, reads ``_`` as ``-``, and drops the line unless the result is a valid
# EDN keyword. So a key the parser would change or drop leaves the database and
# the file disagreeing until the next re-index, and the write still reports
# success. Measured per key against Logseq 0.10.15; the verdicts are pinned in
# tests/test_property_key_validation.py.
#
# The two renames are applied here, so the database gets the key the file will
# be read back as. Everything the parser drops is refused. '/' is refused too:
# it makes a namespaced keyword, and "a/b" survives only as "b". So is "id",
# and the parser's third rename, "custom-id" to "id": measured, both make the
# value the block's uuid on re-read, even when the value is no uuid at all.
# Only the rename had been measured at first, so "id" itself went through
# until #51, with the set-block-property --help example writing it.
_PROPERTY_KEY_FORBIDDEN = re.compile(rf'[/{PROPERTY_KEY_STOP}]')
_PROPERTY_KEYS_READ_AS_ID = {"id", "custom-id"}


def normalize_property_key(key: str) -> str:
    """The key Logseq will read back, or ValueError if it reads back none.

    Lower-cases and turns ``_`` into ``-``, as Logseq's parser does. Refuses
    what the parser drops (whitespace, a leading ``#``, the characters in
    :data:`_PROPERTY_KEY_FORBIDDEN`), what it reads as the block's id, and
    bytes that were not valid UTF-8.
    """
    def refuse(reason, consequence="Logseq would not read it back as a property"):
        return ValueError(f"Invalid property key {key!r}: {reason}. {consequence}.")

    if not key:
        raise refuse("empty")
    if any(c.isspace() for c in key):
        raise refuse("contains whitespace")
    if key.startswith("#"):
        raise refuse("starts with '#'")
    bad = _PROPERTY_KEY_FORBIDDEN.search(key)
    if bad:
        raise refuse(f"contains {bad.group()!r}")
    # Bytes that were not valid UTF-8 on the command line, carried as lone
    # surrogates (PEP 383). They cannot be written to the file as given.
    if any("\ud800" <= c <= "\udfff" for c in key):
        raise refuse("contains bytes that are not valid UTF-8")
    canonical = stored_property_key(key)
    if canonical in _PROPERTY_KEYS_READ_AS_ID:
        raise refuse("Logseq reads it as the block's id",
                     "Writing it would replace the uuid that ((refs)) to the block point at")
    return canonical


def stored_property_key(key: str) -> str:
    """``key`` as Logseq stores it: lower-cased, ``_`` read as ``-`` (#21)."""
    return key.lower().replace("_", "-")


def note_renamed_property_key(key: str, stored: str) -> None:
    """Say on stderr when the key written differs from the key given."""
    if key != stored:
        click.echo(
            f"Note: property key {key!r} is stored as {stored!r} "
            "(Logseq lower-cases keys and reads '_' as '-').",
            err=True,
        )


def _split_property_pair(raw: str):
    if "=" not in raw:
        raise ValueError(f"Invalid --property '{raw}', expected KEY=VALUE")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"Invalid --property '{raw}', empty key")
    return key, value


def parse_property_pairs(pairs) -> list:
    """Parse ('key=value', ...) strings into [(key, coerced_value), ...].

    Splits on the FIRST '=' only, so values may contain '=', commas and spaces
    (e.g. ``tags=mcp, agents``). Keys come back as :func:`normalize_property_key`
    returns them. Raises ValueError on a missing '=', an empty key, or a key
    Logseq would not read back.
    """
    out = []
    for raw in pairs:
        key, value = _split_property_pair(raw)
        out.append((normalize_property_key(key), coerce_property_value(value)))
    return out


def check_property_pairs(pairs) -> list:
    """:func:`parse_property_pairs` for a command's up-front validation.

    Same result, and additionally names every renamed key on stderr and refuses
    a value Logseq would split into blocks (#47). Called once per command,
    before anything is read or written, so each note appears once however often
    the pairs are parsed later.
    """
    parsed = parse_property_pairs(pairs)
    for raw, (stored, value) in zip(pairs, parsed):
        note_renamed_property_key(_split_property_pair(raw)[0], stored)
        refuse_split_property(stored, value)
    return parsed


def stored_properties(api, uuid: str) -> tuple:
    """Properties of the block or page ``uuid`` as the database holds them.

    Returns ``(values, texts)``: the parsed values and the original text of each
    property, both keyed as stored (``due-date``), or two empty dicts when the
    entity has none.

    Read with a datascript pull, not from ``getBlock``/``getPage``: the plugin
    API camel-cases property keys on the way out (``normalize-keyword-for-json``
    in sdk/utils.cljs), so ``due-date`` and ``created_at`` arrive as ``dueDate``
    and ``createdAt``, and the stored spelling cannot be recovered from that.
    Anything that compares or writes back a key needs this form. Measured
    against Logseq 0.10.15; the cases are pinned in
    tests/test_stored_property_keys.py.
    """
    # Validated before it is spliced into the query text: a uuid is the only
    # thing this function puts there, so nothing else can reach it.
    uuid_literal = str(uuid_module.UUID(str(uuid)))
    rows = api.datascript_query(
        "[:find (pull ?b [:block/properties :block/properties-text-values]) "
        f':where [?b :block/uuid #uuid "{uuid_literal}"]]'
    ) or []
    entity = rows[0][0] if rows and rows[0] else None
    if not isinstance(entity, dict):
        return {}, {}
    return entity.get("properties") or {}, entity.get("properties-text-values") or {}


def kept_properties(api, uuid: str, content: str) -> tuple:
    """The properties a replacement of block ``uuid``'s text with ``content``
    passes back, as ``(values, texts)`` like :func:`stored_properties`.

    Properties are lines of the text, so replacing it drops them unless their
    text goes back through ``updateBlock``'s ``opts.properties`` (#30). Only
    what has a text goes back: a markdown heading ("## Title") shows up as
    heading 2 among the values but comes from the "##", not from a line.

    A key ``content`` sets itself is left out: Logseq lets the passed value
    win and drops the caller's line (measured, 0.10.15, #66); with ``_`` for
    ``-`` both lines stay and the passed one, written last, wins. Keys compare
    as Logseq stores them. Every line counts, one in a code block too:
    ``updateBlock`` takes such a line out of the code block as a property
    (measured, #68), unlike a file read, so property_line_mask is not the rule.

    ``id`` and ``custom-id`` always go back. The block's uuid is not the
    text's to set (#56), and a foreign ``id::`` line in a code block, which
    the id rules read as code, would otherwise leave the block and become its
    uuid (#68).
    """
    values, texts = stored_properties(api, uuid)
    own = {stored_property_key(m.group(1))
           for m in map(PROPERTY_LINE_RE.match, content.split("\n")) if m}
    own -= _PROPERTY_KEYS_READ_AS_ID
    texts = {k: v for k, v in texts.items() if k not in own}
    return {k: v for k, v in values.items() if k in texts}, texts


def apply_block_properties(api, block_uuid: str, pairs) -> dict:
    """Upsert parsed KEY=VALUE pairs onto a block. Returns the applied {key: value}."""
    applied = {}
    for key, value in parse_property_pairs(pairs):
        api.upsert_block_property(block_uuid, key, value)
        applied[key] = value
    return applied


def uuid_fields(uuids: list) -> dict:
    """Standard {uuid, uuids} pair for command JSON output.

    uuid = root/first created block (or None); uuids = all created in DFS pre-order.
    Single source of truth for the creation-command output shape so add-note-content,
    insert-block, add-journal-block and add-journal-content stay consistent.
    """
    return {"uuid": uuids[0] if uuids else None, "uuids": list(uuids)}


def extract_page_links(text: str) -> list:
    """Extract all [[page link]] references from text."""
    return re.findall(r"\[\[(.*?)\]\]", text)


def extract_topics(text: str) -> list:
    """Extract topics from hashtags and page links."""
    links = extract_page_links(text)
    tags = re.findall(r"#(\w+)", text)
    return list(set(links + tags))


def subtree_uuids(block: dict) -> list:
    """The block's own UUID and those of all its descendants."""
    return [block["uuid"], *_collect_child_uuids(block)] if block.get("uuid") else []


def incoming_block_refs(api, *, uuids=None, page=None, count_inside=False) -> list:
    """``((block-refs))`` from outside a deleted set into it.

    The set is either ``uuids`` (a block and its descendants) or every block
    of ``page``. A ref from inside the set does not count: it goes together
    with its target. ``count_inside`` counts it anyway, for a move by copy,
    where the source lives on in the copy under new uuids and its internal
    refs dangle there. Returns ``{"target", "block", "page"}`` per ref, sorted.

    ``page`` must be the ``name`` Logseq itself reports for the page, not the
    caller's spelling: ``getPage`` normalises a name (Unicode form, a slash at
    either end) further than lower-casing, so a name typed differently finds
    the page there and nothing here, which would read as "no refs".

    ``:block/refs`` holds ``((uuid))``, ``{{embed ((uuid))}}``,
    ``[label](((uuid)))`` and ``key:: ((uuid))`` alike (measured, 0.10.15), so
    one relation answers for all of them. The source is pulled rather than
    matched, because a clause on its page name would silently drop a ref whose
    source lacks the attribute, and an undercount here reads as "safe".
    """
    if page is not None:
        target_clause = (f"[?p :block/name {page_name_literal(page)}]"
                         " [?t :block/page ?p] [?t :block/uuid ?tu]")
        def inside(src, owner):
            return owner.get("name") == page.lower()
    else:
        wanted = {u.lower() for u in uuids or []}
        if not wanted:
            return []
        literal = " ".join(f'#uuid "{u}"' for u in sorted(wanted))
        target_clause = f"[?t :block/uuid ?tu] [(contains? #{{{literal}}} ?tu)]"
        def inside(src, owner):
            return src.lower() in wanted
    query = (
        "[:find ?tu (pull ?b [:block/uuid :block/original-name :block/name"
        " {:block/page [:block/original-name :block/name]}])"
        f" :where {target_clause} [?b :block/refs ?t]]"
    )
    refs = []
    for row in api.datascript_query(query) or []:
        if not row or len(row) < 2 or not isinstance(row[1], dict):
            continue
        target, src = str(row[0]), row[1]
        # A page entity can carry refs itself; it is then its own page.
        owner = src.get("page") or src
        src_page = owner.get("original-name") or owner.get("name")
        src_uuid = str(src.get("uuid") or "")
        if not count_inside and inside(src_uuid, owner):
            continue
        refs.append({"target": target, "block": src_uuid, "page": src_page})
    return sorted(refs, key=lambda r: (r["page"] or "", r["block"], r["target"]))


def refs_refusal(refs: list, what: str, instead: str = "", limit: int = 10) -> str:
    """The refusal for a delete that would leave ``refs`` dangling.

    Lists where each ref comes from, bounded like other listings, and names
    the override. ``--force`` is not it: a caller that deletes routinely passes
    ``--force`` every time, so a check it overrides would never stop anything.
    """
    lines = [f"  {r['page']}  {r['block']}  -> (({r['target']}))" for r in refs[:limit]]
    if len(refs) > limit:
        lines.append(f"  ... and {len(refs) - limit} more")
    listing = "\n".join(lines)
    return (f"{len(refs)} block ref(s) point into {what}, and would "
            f"dangle:\n{listing}\n{instead}Re-run with --ignore-refs to delete "
            "anyway. Nothing was changed.")
