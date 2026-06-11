import re
import json
import datetime
from collections import Counter

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


def parse_hierarchical_content(content: str) -> list:
    """Parse indented content into a block tree.

    Each line becomes a block. Indentation (tab or 2 spaces) creates children.
    Leading '- ' is stripped from each line. Mixed tab/space indentation is
    normalized to tab-only first, so a node's leading whitespace can never
    carry the ``\\t  \\t`` form that would break Logseq's outline.
    """
    content = normalize_indentation(content)
    lines = content.split("\n")
    root = []
    stack = [(root, -1)]  # (children_list, indent_level)

    for line in lines:
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
        if stripped.startswith("- "):
            stripped = stripped[2:]

        node = {"content": stripped, "children": []}

        # find correct parent
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()

        stack[-1][0].append(node)
        stack.append((node["children"], indent))

    return root


def insert_formatted_content(api, page_name: str, content: str) -> int:
    """Insert hierarchical content into a page as nested blocks. Returns block count."""
    # parse_hierarchical_content handles bullet stripping per-line
    tree = parse_hierarchical_content(content)

    def insert_tree(blocks, parent_uuid=None):
        for block in blocks:
            if parent_uuid:
                result = api.insert_block(
                    parent_uuid, block["content"], {"sibling": False}
                )
            else:
                result = api.append_block_in_page(page_name, block["content"])

            if result and block["children"]:
                child_uuid = None
                if isinstance(result, dict):
                    child_uuid = result.get("uuid")
                elif isinstance(result, str):
                    child_uuid = result
                if child_uuid:
                    insert_tree(block["children"], child_uuid)

    insert_tree(tree)
    return count_blocks(tree)


_HEADING_SUFFIX_RE = re.compile(r'(\s*\{\{[^}]*\}\})+\s*$')


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


def find_or_create_heading(api, page_name: str, heading: str) -> str | None:
    """Find heading block UUID on page, create if missing.

    Matches existing headings tolerantly via :func:`normalize_heading` so that
    renderer macros and whitespace variations do not cause spurious duplicates.

    Returns the UUID of the heading block, or None if creation failed.
    """
    target = normalize_heading(heading)
    blocks = api.get_page_blocks_tree(page_name) or []
    for block in blocks:
        if normalize_heading(block.get("content", "")) == target:
            return block.get("uuid")

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


def require_insert(result, what: str) -> str:
    """Return the UUID of a just-inserted block, or abort loudly.

    The Logseq API answers a failed insert/append with HTTP 200 + ``null``
    instead of an error status, so a missing UUID is the only failure signal.
    Callers that must not continue on a silent write failure use this to turn
    that ``null`` into a non-zero exit with a clear message, rather than
    reporting a phantom success.
    """
    uuid = block_uuid_from_result(result)
    if not uuid:
        raise click.ClickException(
            f"Logseq did not create {what} (API returned no block UUID). "
            "Likely cause: the target/anchor UUID does not exist, or the page "
            "is not loaded. Nothing was written."
        )
    return uuid


def insert_block_tree_with_uuids(api, tree: list, parent_uuid: str, *, strict: bool = False) -> list:
    """Recursively insert a parsed tree under ``parent_uuid``.

    Returns the UUIDs of inserted blocks in DFS pre-order (parent before
    children, siblings in declaration order). With ``strict=True`` a silent
    write failure (``null`` result) aborts via :func:`require_insert` instead
    of pushing a ``None`` UUID and skipping that block's children.
    """
    uuids = []
    for block in tree:
        result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
        if strict:
            new_uuid = require_insert(result, "a block")
        else:
            new_uuid = block_uuid_from_result(result)
        uuids.append(new_uuid)
        children = block.get("children") or []
        if new_uuid and children:
            uuids.extend(insert_block_tree_with_uuids(api, children, new_uuid, strict=strict))
    return uuids


def insert_block_tree_as_siblings(api, tree: list, anchor_uuid: str, *, before: bool = False, strict: bool = True) -> list:
    """Insert a parsed tree as sibling(s) after (or before) ``anchor_uuid``.

    The first top-level node is inserted as a sibling of the anchor; its
    children are nested beneath it; each further top-level node is inserted as
    a sibling after the previous top-level node, preserving declaration order.
    This is the ``--after``/``--before`` counterpart to
    :func:`insert_block_tree_with_uuids` (which only nests under a parent).

    Returns inserted UUIDs in DFS pre-order. ``strict`` (default True) aborts
    on a silent write failure rather than orphaning the remaining nodes.
    """
    uuids = []
    cursor = anchor_uuid
    for block in tree:
        result = api.insert_block(cursor, block["content"], {"sibling": True, "before": before})
        if strict:
            new_uuid = require_insert(result, "a block")
        else:
            new_uuid = block_uuid_from_result(result)
        uuids.append(new_uuid)
        if not new_uuid:
            # non-strict and the insert failed: stop walking this chain
            break
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(api, children, new_uuid, strict=strict))
        # When inserting "before", keep each new top node before the anchor in
        # order by advancing the cursor to the node just placed; when "after",
        # the next sibling must follow the one we just inserted.
        cursor = new_uuid
    return uuids


def insert_block_tree_at_page_top(api, tree: list, page_name: str) -> list:
    """Insert tree starting at the top of ``page_name``.

    Top-level nodes use ``append_block_in_page`` (which currently appends; the
    Logseq API has no first-block primitive). Children use insert_block.
    Returns DFS pre-order UUIDs.
    """
    uuids = []
    for block in tree:
        result = api.append_block_in_page(page_name, block["content"])
        new_uuid = None
        if isinstance(result, dict):
            new_uuid = result.get("uuid")
        elif isinstance(result, str):
            new_uuid = result
        uuids.append(new_uuid)
        children = block.get("children") or []
        if new_uuid and children:
            uuids.extend(insert_block_tree_with_uuids(api, children, new_uuid))
    return uuids


def insert_block_tree(api, tree: list, parent_uuid: str) -> int:
    """Recursively insert parsed block tree under a parent block.

    Returns the number of blocks inserted.
    """
    n = 0
    for block in tree:
        result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
        n += 1
        if block["children"]:
            child_uuid = None
            if isinstance(result, dict):
                child_uuid = result.get("uuid")
            elif isinstance(result, str):
                child_uuid = result
            if child_uuid:
                n += insert_block_tree(api, block["children"], child_uuid)
    return n


def insert_formatted_content_with_uuids(api, page_name: str, content: str) -> list:
    """Like ``insert_formatted_content`` but returns the inserted block UUIDs.

    Top-level nodes are appended to the page; children use insert_block.
    Returns UUIDs in DFS pre-order (parent before children).
    """
    tree = parse_hierarchical_content(content)
    uuids = []

    def insert_tree(blocks, parent_uuid=None):
        for block in blocks:
            if parent_uuid:
                result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
            else:
                result = api.append_block_in_page(page_name, block["content"])
            new_uuid = None
            if isinstance(result, dict):
                new_uuid = result.get("uuid")
            elif isinstance(result, str):
                new_uuid = result
            uuids.append(new_uuid)
            if new_uuid and block["children"]:
                insert_tree(block["children"], new_uuid)

    insert_tree(tree)
    return uuids


def coerce_property_value(value: str):
    """Coerce a property value string to int/float when possible, else leave as str.

    Single source of truth for property-value typing (shared by set-block-property
    and the inline --property option).
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return float(value)
        except (ValueError, TypeError):
            return value


def parse_property_pairs(pairs) -> list:
    """Parse ('key=value', ...) strings into [(key, coerced_value), ...].

    Splits on the FIRST '=' only, so values may contain '=', commas and spaces
    (e.g. ``tags=mcp, agents``). Raises ValueError on a missing '=' or empty key.
    """
    out = []
    for raw in pairs:
        if "=" not in raw:
            raise ValueError(f"Invalid --property '{raw}', expected KEY=VALUE")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --property '{raw}', empty key")
        out.append((key, coerce_property_value(value)))
    return out


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
