"""Turning blocks into text, and finding and resolving the references inside them.

Most of these render blocks to text or Markdown, or cut the tree down to what a
read asked for (a section, the outline, a size); resolve_refs_in_blocks and
count_unresolved_refs do not — they resolve block references against the graph
and count the dead ones. Finding references in block text is the third job:
BLOCK_REF_RE for block refs, extract_page_links and extract_topics for [[links]]
and #tags, which todos, pages, journal and analysis use to find references, not
to render a block. The name is approximate and kept: moving this out buys a
file plus an edge between the two, which is movement without the gain.

They live apart from the commands because pages, journal and todos all read
them, and a helper three modules import is not private to any of them.
"""
import re

from logseq_cli.blocktext import PROPERTY_LINE_RE
from logseq_cli.headings import normalize_heading
from logseq_cli.outlinetext import bullet_lines


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

def extract_page_links(text: str) -> list:
    """Extract all [[page link]] references from text."""
    return re.findall(r"\[\[(.*?)\]\]", text)

def extract_topics(text: str) -> list:
    """Extract topics from hashtags and page links."""
    links = extract_page_links(text)
    tags = re.findall(r"#(\w+)", text)
    return list(set(links + tags))

def is_properties_block(content: str) -> bool:
    """Check if block content is a Logseq properties block (key:: value lines)."""
    lines = content.strip().split("\n")
    return all(PROPERTY_LINE_RE.match(line) for line in lines if line.strip())

def process_blocks(blocks, indent: int = 0) -> str:
    """Recursively format blocks as indented text."""
    lines = []
    prefix = "  " * indent
    for block in blocks:
        content = block.get("content", "")
        if content:
            lines.extend(bullet_lines(content, prefix))
        children = block.get("children", [])
        if children:
            lines.append(process_blocks(children, indent + 1))
    return "\n".join(lines)

def blocks_to_markdown(blocks, indent=0, page_start=True):
    """Convert block tree to Logseq-compatible markdown.

    The page's own properties, its first block when that holds nothing but
    property lines, are rendered without a bullet, as Logseq writes them. Any
    other properties-only block (an empty numbered-list item, say) keeps its
    bullet, or the output would turn it into page properties. ``page_start``
    says whether ``blocks`` begins where the page does; a read continued with
    ``--from-block`` does not.
    """
    lines = []
    prefix = "\t" * indent
    for position, block in enumerate(blocks):
        content = block.get("content", "")
        if content:
            if page_start and indent == 0 and position == 0 and is_properties_block(content):
                # Properties block: no bullet prefix, matches Logseq file format
                lines.append(content)
            else:
                lines.extend(bullet_lines(content, prefix))
        children = block.get("children", [])
        if children:
            lines.append(blocks_to_markdown(children, indent + 1))
    return "\n".join(lines)

def hanging(text: str, prefix: str, deeper: int = 0) -> str:
    """``text`` after ``prefix``, its further lines under its first (#77).

    At column 0 a reader cannot tell which entry of a listing a line belongs
    to, and a property line there reads like part of the listing. Where
    entries follow one another at the same column, the further lines go
    ``deeper``, or where one entry ends is lost.
    """
    first, *rest = text.split("\n")
    pad = " " * (len(prefix) + deeper)
    return "\n".join([prefix + first, *(pad + line for line in rest)])

def blocks_with_ids(blocks, indent=0, first_line=False):
    """Render block tree as ``<uuid>\\t<indent-tabs>\\t<content>`` lines.

    Allows downstream tools to extract a block UUID without parsing JSON.
    Indentation is encoded as a run of tab characters whose length matches
    the depth (matching ``blocks_to_markdown``). ``first_line`` keeps only the
    first line of each block, which for a heading is the heading; what follows
    is its properties or text continued below it.
    """
    lines = []
    indent_str = "\t" * indent
    for block in blocks:
        uuid = block.get("uuid") or ""
        content = block.get("content", "")
        if first_line:
            content = content.split("\n", 1)[0]
        if content:
            lines.append(f"{uuid}\t{indent_str}\t{content}")
        children = block.get("children", [])
        if children:
            lines.append(blocks_with_ids(children, indent + 1, first_line))
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

def is_heading_block(block) -> bool:
    """Whether Logseq reads the block as a heading.

    Taken from ``properties.heading``, which Logseq sets for ``## X``,
    ``heading:: true`` and ``heading:: 2`` alike, and not for a ``#tag`` or a
    ``##`` inside a code fence (measured on 0.10.15). A pattern over the
    content would have to re-derive all of that and could disagree with it.
    """
    return bool((block.get("properties") or {}).get("heading"))

def outline_blocks(blocks) -> list:
    """The heading blocks of a tree, nested by heading ancestry.

    A heading under a plain block takes the plain block's place; everything
    that is not a heading is dropped. The blocks are the ones passed in, with
    only ``children`` replaced, so the outline cannot say anything about a
    heading that the full read does not.
    """
    outline = []
    for block in blocks:
        below = outline_blocks(block.get("children") or [])
        if is_heading_block(block):
            outline.append({**block, "children": below})
        else:
            outline.extend(below)
    return outline

def _preorder(blocks, ancestors=()):
    """Yield ``(block, ancestors)`` in reading order."""
    for block in blocks:
        yield block, ancestors
        yield from _preorder(block.get("children") or [], ancestors + (block,))

def _keep(blocks, kept):
    """Copy of ``blocks`` holding only the blocks whose id() is in ``kept``.

    ``kept`` must hold every ancestor of a block it holds, or that block is
    lost with its parent. Both callers guarantee it: a reading-order prefix
    keeps its ancestors by construction, and :func:`start_at` adds them.
    """
    return [{**block, "children": _keep(block.get("children") or [], kept)}
            for block in blocks if id(block) in kept]

def start_at(results, uuid):
    """``results`` from the block ``uuid`` on, in reading order.

    Results before the one holding it are dropped, and within it every block
    before it, except its ancestors: they stay as context, so the block keeps
    its place in the tree. Every block before it that has anything after it
    is one of those ancestors, which is why nothing else needs keeping.

    Returns ``(results, context)``, ``context`` being the number of ancestors
    kept, or ``None`` when no result holds the block.
    """
    for index, result in enumerate(results):
        order = list(_preorder(result.get("blocks") or []))
        for position, (block, ancestors) in enumerate(order):
            if block.get("uuid") == uuid:
                kept = {id(a) for a in ancestors} | {id(b) for b, _ in order[position:]}
                resumed = {**result, "blocks": _keep(result["blocks"], kept)}
                return [resumed] + results[index + 1:], len(ancestors)
    return None

def cap_results(results, render, max_chars, context=0):
    """Cut ``results`` so that ``render(results)`` fits in ``max_chars``.

    ``results`` is the list a read prints, each a dict with ``blocks``. What is
    printed is a prefix of it in reading order, cut between blocks, never
    inside one. The result the cut falls in carries what was withheld, so every
    format has it:

    * ``withheld``: its blocks that were not printed;
    * ``cut``: the first block withheld (``before``), the section it lies in
      (``section``, ``section_uuid``: the block itself if it is a heading, else
      its nearest heading ancestor, else ``None``), and how many results come
      after it (``later``). Those are not printed at all: a placeholder for
      each outgrew the cap on short journal days. ``before`` is what
      ``--from-block`` continues from.

    The size is measured with the caller's own renderer, because the cap is on
    what is printed: a block in JSON is several times its text. The size does
    not grow strictly with the blocks kept (``cut`` names a section, and names
    differ in length), so the search holds on to "fits" rather than assuming
    it: the output fits, and is at worst a few blocks shorter than the
    longest that would. Only the blocks can be cut. When even none of them
    leaves the output over the cap (a page header, backlinks), the summary
    says by how much.

    ``context`` is the number of leading blocks :func:`start_at` kept as
    ancestors; they are never cut. When not one block past them fits, ``cut``
    carries ``needs``, the size the next block takes, and the note offers no
    ``--from-block``: it would name the same block again, and a reader
    following the notes would never get past it.

    Returns ``(results, None)`` when everything fits, else the cut results and
    a summary for :func:`capped_note`.
    """
    size = len(render(results))
    if size <= max_chars:
        return results, None
    order = [(index, block, ancestors)
             for index, result in enumerate(results)
             for block, ancestors in _preorder(result.get("blocks") or [])]
    if not order:
        return results, {"max_chars": max_chars, "size": size, "withheld": 0}

    def take(count):
        index, block, ancestors = order[count]
        kept = {id(b) for _, b, _ in order[:count]}
        cut = [dict(result) for result in results[:index]]
        cut.append({**results[index],
                    "blocks": _keep(results[index].get("blocks") or [], kept)})
        section = next((b for b in (block,) + tuple(reversed(ancestors))
                        if is_heading_block(b)), None)
        cut[-1]["withheld"] = sum(1 for i, _, _ in order[count:] if i == index)
        cut[-1]["cut"] = {
            "before": block.get("uuid"),
            "section": normalize_heading(section.get("content", "")) if section else None,
            "section_uuid": section.get("uuid") if section else None,
            "later": len(results) - index - 1,
        }
        return cut

    # Invariant: take(fits) fits, or fits is context; take(too_many) does not fit,
    # or is one past the last block (the full output, measured above).
    fits, too_many = context, len(order)
    while too_many - fits > 1:
        middle = (fits + too_many) // 2
        if len(render(take(middle))) <= max_chars:
            fits = middle
        else:
            too_many = middle
    cut = take(fits)
    if fits == context:
        # Nothing new printed. What the next block needs is measured the same
        # way as everything else: the output with it, cut right after it.
        cut[-1]["cut"]["needs"] = len(render(take(fits + 1) if fits + 1 < len(order) else results))
    index = order[fits][0]
    return cut, {
        "max_chars": max_chars,
        "size": len(render(cut)),
        "withheld": cut[-1]["withheld"],
        "page": results[index].get("page"),
        "later_names": [r.get("page") for r in results[index + 1:]],
        **cut[-1]["cut"],
    }

def capped_note(summary, unit) -> str:
    """The stderr note for a cut from :func:`cap_results`.

    ``unit`` names what a result is ("page", "day"). The way on is
    ``--from-block``, not ``--heading``: a heading name can repeat on a page,
    ``--resolve-refs`` rewrites it, and a section larger than the cap would
    stop at the same block again.
    """
    over = summary["size"] - summary["max_chars"]
    frame = (f"{unit} headers and backlinks (--no-backlinks drops those)"
             if unit == "page" else f"{unit} headers")
    if not summary["withheld"]:
        return (f"Note: output is {summary['size']} chars, {over} over "
                f"--max-chars {summary['max_chars']}, and holds no block to "
                f"withhold: the rest is {frame}.")
    if summary.get("needs"):
        return (f"Note: block (({summary['before']})) on '{summary['page']}' "
                f"does not fit: printing it needs --max-chars {summary['needs']} "
                f"or more (the cap is {summary['max_chars']}). Nothing from it "
                f"on was printed." + _still_over(over, frame))
    note = (f"Note: output cut at --max-chars {summary['max_chars']}; "
            f"{summary['withheld']} block(s) withheld on '{summary['page']}', cut ")
    if summary["section_uuid"]:
        note += f"in section '{summary['section']}'"
    else:
        note += "outside any section"
    later = summary["later_names"]
    if later:
        names = f"'{later[0]}'" if len(later) == 1 else f"'{later[0]}' to '{later[-1]}'"
        note += f"; {len(later)} later {unit}(s) not printed ({names})"
    note += (f". Continue with the same command plus "
             f"--from-block {summary['before']}, or raise --max-chars.")
    return note + _still_over(over, frame)

def _still_over(over, frame) -> str:
    if over <= 0:
        return ""
    return (f" Still {over} chars over --max-chars: {frame} alone exceed it, "
            f"and only blocks are cut.")

def first_uuids(results) -> dict:
    """The uuid of each result's first block, by page name.

    Markdown writes a page's own first block without a bullet when it holds
    only properties, and ``--from-block`` can start mid-page; this is how the
    renderer tells the page's first block from the first one printed.
    """
    return {r["page"]: (r.get("blocks") or [{}])[0].get("uuid") for r in results}

def bound(results, render, max_chars, from_block, unit):
    """Apply ``--from-block``, then ``--max-chars``, as both reads do.

    Returns ``(results, note)``, ``note`` being the stderr note for a cut or
    ``None``. Raises ``LookupError`` when no result holds ``from_block``.
    """
    context = 0
    if from_block:
        resumed = start_at(results, from_block)
        if resumed is None:
            raise LookupError(
                f"--from-block {from_block}: no such block in this read. It "
                f"must come from a note of the same command with the same flags.")
        results, context = resumed
    if max_chars is None:
        return results, None
    results, summary = cap_results(results, render, max_chars, context)
    return results, capped_note(summary, unit) if summary else None

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
