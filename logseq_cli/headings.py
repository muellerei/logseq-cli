"""Headings on a page: compare them, find one, add one that is missing.

A heading is found by its text, not by a uuid, so two spellings of one heading
have to compare equal, and normalize_heading decides when they do. The
renderer asks the same question when it cuts out a section, which is why this
stands apart from the commands. find_or_create_heading writes, but not by
Strict Insert: it appends, and reads the page back when Logseq's answer names
no uuid.
"""

import re


def strip_title_heading(content: str, page_name: str) -> str:
    """Remove '# PageName' heading from content to prevent duplication."""
    pattern = re.compile(rf"^#\s+{re.escape(page_name)}\s*$", re.IGNORECASE | re.MULTILINE)
    return pattern.sub("", content).strip()


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
