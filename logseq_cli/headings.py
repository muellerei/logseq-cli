"""Headings on a page: compare them, find one, add one that is missing.

A heading is found by its text, not by a uuid, so two spellings of one heading
have to compare equal, and normalize_heading decides when they do. The
renderer asks the same question when it cuts out a section, which is why this
stands apart from the commands. find_or_create_heading writes: it appends, or
with ``keep_last`` goes before the empty blocks that end the page, and the API
proves the write. That is why this module imports strictinsert, and render and output,
which import this one, reach it too (no cycle: tests/test_package_layering.py).
"""

import re
import textwrap

from logseq_cli.strictinsert import before_first_empty, first_empty_at_end


class TitleHeadingOnly(Exception):
    """Content that was nothing but the page's title heading. Not a
    ValueError, like blocktext.SplitBlockError: it has to reach
    handle_connection_error, which reports it through fail() with
    ``reason: "empty_content"`` and the ``page``."""

    def __init__(self, page_name: str):
        super().__init__(
            f"The content is only the page's title heading '# {page_name}', "
            "which is dropped since the page shows its name: nothing would be "
            "written.")
        self.page = page_name


def strip_title_heading(content: str, page_name: str) -> str:
    """Remove a leading '# PageName' heading from content to prevent
    duplication, and the indentation the lines below it share.

    Only the first line with text is the title. A '# PageName' line further
    down, or in a code block, is the caller's text and stays.

    Refuses content that was nothing but that heading (TitleHeadingOnly). The
    commands check for empty content before they get here, so text the
    removal empties had passed that check, and was written as an empty block,
    or as "Added 0 block(s)" with exit 0. Checked here, where the text is
    emptied, so every writer that removes the heading refuses it the same way.
    """
    lines = content.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    # From the line's start, as before: " # Title" stays text (see
    # test_the_title_is_stripped_once).
    title = re.compile(rf"#\s+{re.escape(page_name)}\s*", re.IGNORECASE)
    if first is not None and title.fullmatch(lines[first]):
        lines = lines[first + 1:]
    # The lines below go out together: stripped alone, the first lost its
    # indentation and became the parent of the lines that shared it.
    stripped = textwrap.dedent("\n".join(lines)).strip()
    if content.strip() and not stripped:
        raise TitleHeadingOnly(page_name)
    return stripped


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


def find_or_create_heading(api, page_name: str, heading: str, *, keep_last: bool = False) -> str:
    """Find heading block UUID on page, create if missing.

    Matches existing headings tolerantly via :func:`normalize_heading` so that
    renderer macros and whitespace variations do not cause spurious duplicates.

    Returns the UUID of the heading block. A heading Logseq does not create
    raises WriteNotVerified from the API; the callers once wrote to the top of
    the page instead, with a warning (removed once every write was proven).

    A heading that is missing is a write at the end of the page like any
    other, so with ``keep_last`` (``[graph] keep_empty_blocks_last``) it goes
    directly before the empty blocks that end the page (#110).
    """
    found = find_heading(api, page_name, heading)
    if found:
        return found
    if keep_last:
        uuids, _ = before_first_empty(api, [{"content": heading, "children": []}],
                                      page_name=page_name)
        if uuids:
            return uuids[0]
    return api.append_block_in_page(page_name, heading)["uuid"]


def first_empty_under(api, nodes: list, page_name: str, heading: str | None):
    """The empty block a write of ``nodes`` under ``heading`` (or at the end of
    the page, with no heading) would go before, for a preview: it reads, and
    never creates the heading. With the heading missing there is none to name:
    the heading would go before the empty blocks at the end of the page, and a
    write reports where its content went, not that (#110)."""
    if not heading:
        return first_empty_at_end(api, nodes, page_name=page_name)
    found = find_heading(api, page_name, heading)
    return first_empty_at_end(api, nodes, parent_uuid=found) if found else None
