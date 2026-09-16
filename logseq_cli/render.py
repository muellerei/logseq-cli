"""Turning blocks into text, and resolving the references inside them.

Six of these render blocks to text or Markdown; resolve_refs_in_blocks and
count_unresolved_refs do not — they resolve block references against the graph
and count the dead ones. BLOCK_REF_RE is here although todos.py uses it to
recognise a reference rather than to display one. The name is approximate and
kept: splitting along that line yields two files of about sixty lines plus an
edge between them, which is movement without the gain.

They live apart from the commands because pages, journal and todos all read
them, and a helper three modules import is not private to any of them.
"""
import re

from logseq_cli.helpers import normalize_heading


BLOCK_REF_RE = re.compile(r'\(\(([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)\)')

def _resolve_single_ref(api, uuid: str, dead: list = None) -> str:
    """Resolve one block UUID to its content text. Returns UUID unchanged on failure.

    A failed lookup means the target is gone — Logseq answers ``null`` for a
    deleted block. The fallback then renders the ref exactly as an unresolved
    one, so two different things end up spelled the same way in the output.
    ``dead`` collects those uuids so the caller can say which is which.
    """
    try:
        block = api.get_block(uuid, include_children=False)
        if block:
            ref_content = (block.get("content") or "").strip()
            page_info = block.get("page") or {}
            page_name = ""
            if isinstance(page_info, dict):
                page_name = page_info.get("originalName") or page_info.get("name") or ""
            source = f" ↳ {page_name}" if page_name else ""
            return f"{ref_content}{source}"
    except Exception:
        pass
    if dead is not None and uuid not in dead:
        dead.append(uuid)
    return f"(({uuid}))"

def resolve_refs_in_blocks(api, blocks: list, dead: list = None) -> None:
    """Recursively resolve ((uuid)) references in block content, in-place.

    ``dead`` collects the uuids whose target could not be read, in first-seen
    order, so a caller can report them without walking the output again.
    """
    for block in blocks:
        content = block.get("content", "")
        if content and "((" in content:
            block["content"] = BLOCK_REF_RE.sub(
                lambda m: _resolve_single_ref(api, m.group(1), dead), content
            )
        children = block.get("children", [])
        if children:
            resolve_refs_in_blocks(api, children, dead)

def extract_backlink_names(refs) -> list:
    """Extract sorted page names from getPageLinkedReferences response.

    The native API returns a list of [page_dict, [block, ...]] pairs.
    We extract the page name from each pair and return a sorted list.
    """
    if not refs or not isinstance(refs, list):
        return []
    names = []
    for entry in refs:
        if isinstance(entry, (list, tuple)) and len(entry) >= 1:
            page_info = entry[0]
            if isinstance(page_info, dict):
                name = page_info.get("originalName") or page_info.get("name", "")
                if name:
                    names.append(name)
    return sorted(names)

def is_properties_block(content: str) -> bool:
    """Check if block content is a Logseq properties block (key:: value lines)."""
    lines = content.strip().split("\n")
    return all(re.match(r"^[\w-]+::", line) for line in lines if line.strip())

def blocks_to_markdown(blocks, indent=0):
    """Convert block tree to Logseq-compatible markdown.

    Properties blocks (top-level, all lines match 'key:: value') are rendered
    without bullet prefix to match Logseq's on-disk format.
    """
    lines = []
    prefix = "\t" * indent
    for block in blocks:
        content = block.get("content", "")
        if content:
            if indent == 0 and is_properties_block(content):
                # Properties block: no bullet prefix, matches Logseq file format
                lines.append(content)
            else:
                lines.append(f"{prefix}- {content}")
        children = block.get("children", [])
        if children:
            lines.append(blocks_to_markdown(children, indent + 1))
    return "\n".join(lines)

def blocks_with_ids(blocks, indent=0):
    """Render block tree as ``<uuid>\\t<indent-tabs>\\t<content>`` lines.

    Allows downstream tools to extract a block UUID without parsing JSON.
    Indentation is encoded as a run of tab characters whose length matches
    the depth (matching ``blocks_to_markdown``).
    """
    lines = []
    indent_str = "\t" * indent
    for block in blocks:
        uuid = block.get("uuid") or ""
        content = block.get("content", "")
        if content:
            lines.append(f"{uuid}\t{indent_str}\t{content}")
        children = block.get("children", [])
        if children:
            lines.append(blocks_with_ids(children, indent + 1))
    return "\n".join(lines)

def extract_section(blocks, heading_text):
    """Return the block matching heading_text (with its children), searched recursively.

    Uses :func:`normalize_heading` so renderer macros (e.g. ``{{renderer :todomaster}}``)
    and whitespace variations on the stored block do not prevent a match.
    """
    target = normalize_heading(heading_text)
    for block in blocks:
        content = block.get("content") or ""
        if normalize_heading(content) == target:
            return [block]
        children = block.get("children", [])
        if children:
            result = extract_section(children, heading_text)
            if result:
                return result
    return []

def count_unresolved_refs(blocks) -> int:
    """Recursively count ((uuid)) patterns in block content."""
    count = 0
    for block in blocks:
        content = block.get("content", "")
        if content and "((" in content:
            count += len(BLOCK_REF_RE.findall(content))
        children = block.get("children", [])
        if children:
            count += count_unresolved_refs(children)
    return count
