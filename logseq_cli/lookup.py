"""Finding things in the graph: blocks by content, backlinks, incoming refs.

What several commands have to locate before they act is answered once, here:
the one block a ``--where-content`` means, the Block Refs a delete would leave
dangling, the pages that link to a page, a page's text. So are the Refusals
when the answer stops a command: no block or several for a
``--where-content``, refs a delete would leave pointing at nothing.
"""

import re

import click

from logseq_cli.datalog import edn_string, page_name_literal
from logseq_cli.render import process_blocks


def escape_regex(s: str) -> str:
    """Escape special regex characters in a string."""
    return re.escape(s)


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
        # Not caught: a page that could not be read was skipped, so the list
        # came back short without a word (#93). Reading every page of a real
        # graph raised nothing (measured 2026-09-25), so an error
        # here means the connection, and the caller hears of it.
        content = get_page_content(api, name)
        if content and pattern.search(content):
            backlink_pages.append(name)
    return sorted(backlink_pages)


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
