"""Outline text: indented bullets read into a block tree, and written back.

parse_hierarchical_content reads the text a caller passes as an outline;
outline_text is its inverse, so what a write echoes reads back as the same
tree. The rules that decide where a block ends live here with them: tabs
against spaces, code blocks a bullet must not split, and quotes a blank line
stops. Nothing here talks to Logseq, so all of it is tested without a mock;
the layering tests keep it that way (ADR 0003).
"""

import re

import click

from logseq_cli.blocktext import PROPERTY_LINE_RE, code_block_lines, is_fence


def count_blocks(tree: list) -> int:
    """Recursively count total blocks in a hierarchical tree."""
    total = 0
    for node in tree:
        total += 1
        if node.get("children"):
            total += count_blocks(node["children"])
    return total


def bullet_lines(content: str, prefix: str) -> list:
    """One block's text as lines of an outline, its bullet after ``prefix``.

    The further lines sit under the bullet, two spaces in, blank ones too:
    the layout Logseq writes to the page file (measured, #75). Logseq reads
    them back at column 0 as well, but a reader cannot tell which block such
    a line belongs to, and a property line there looks like the page's own.
    """
    first, *rest = content.split("\n")
    return [f"{prefix}- {first}", *(f"{prefix}  {line}" for line in rest)]


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
    with the write.
    """
    def walk(blocks, depth):
        for block in blocks:
            yield from bullet_lines(block.get("content") or "", "\t" * depth)
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
