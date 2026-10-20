"""What a write into a page section would do, decided once.

``add-journal-block``, ``add-journal-content`` and ``add-note-content`` write
under a heading of a page, or at its end. Each path of them once worked out
its own preview and its own run, and the previews drifted: one printed text
after its JSON, one left out that the journal page would be created, none of
the journal ones said that the heading would be. So the facts come from one
place now: :func:`plan_section_write` reads, and never writes;
:func:`plan_fields` and :func:`plan_lines` report the plan for ``--dry-run``;
:func:`apply_section_plan` carries it out. What a preview says is read from
the object the run writes by.

Which helper writes is left as each path had it: the batch path writes its
values one by one, the tree path one tree, the flat path one block, and the
helpers differ in how they send children (one ``insertBatchBlock`` against
``insertBlock`` per block). Making them the same would change what is written,
for no defect.

Each command still builds the rest of its output itself; the JSON keys of the
three stay what they were, and this module knows no command.
"""
from dataclasses import dataclass

from logseq_cli.blockprops import kept_properties
from logseq_cli.headings import find_heading, find_or_create_heading, find_upsert_target
from logseq_cli.notes import print_note
from logseq_cli.outlinetext import count_blocks
from logseq_cli.output import would_go_before_fields
from logseq_cli.preconditions import Expect, check_precondition, read_fresh
from logseq_cli.safety import Owed, require_precondition
from logseq_cli.strictinsert import (
    append_in_page,
    before_empty_or_append,
    before_first_empty,
    first_empty_at_end,
    insert_block_at,
    insert_block_tree_as_siblings,
    insert_block_tree_at_page_top,
    insert_block_tree_with_uuids,
    insert_tree_at_page_end,
)
from logseq_cli.writerefused import AmbiguousTarget


@dataclass(frozen=True)
class Upsert:
    """``--upsert-heading``: the block under the heading it replaces, and the
    ``--expect-hash`` the call gave for it."""
    heading: str
    expect: Expect = Expect()


@dataclass(frozen=True)
class UpsertPlan:
    """The block an upsert replaces (``action == "replace"``), or that there is
    none and the block is created (``"create"``). ``held`` is the text the
    precondition was checked on, for ``update_block``."""
    action: str
    target: str | None
    held: dict


@dataclass(frozen=True)
class SectionPlan:
    page: str
    heading: str | None
    # (kind, payload) per value as given: ("tree", nodes) or ("flat", text).
    values: tuple
    roots: tuple            # the roots of all values, in order
    path: str               # "flat" | "tree" | "batch" | "upsert": picks the helper
    would_create_page: bool
    would_create_heading: bool
    heading_uuid: str | None
    blocks: int
    anchor: str | None      # the empty block the write would go before
    keep_last: bool
    upsert: UpsertPlan | None = None


@dataclass(frozen=True)
class SectionWritten:
    uuids: list
    anchor: str | None      # the empty block the write went before
    heading_uuid: str | None
    result: dict | None = None   # the flat path's block, as the insert answered it
    status: str | None = None    # the upsert's: "updated" | "created"
    hash: str | None = None      # the replaced block's hash, as its proof read it


def _roots(values) -> list:
    return [node for kind, payload in values
            for node in (payload if kind == "tree" else [{"content": payload, "children": []}])]


def _path(values, upsert) -> str:
    if upsert is not None:
        return "upsert"
    if len(values) > 1:
        return "batch"
    return values[0][0]


def _choose_upsert_target(api, page, heading, upsert):
    """The heading and the one block under it ``upsert`` replaces, read past
    the cache: the answer decides which block is overwritten. Several matches
    are refused rather than the first taken, since the caller could not tell
    which one was replaced."""
    heading_uuid, matches = find_upsert_target(api, page, heading, upsert.heading)
    if len(matches) > 1:
        raise AmbiguousTarget(
            f"{len(matches)} blocks under '{heading}' match '{upsert.heading}'; refusing to "
            "guess which one to replace. Remove or rename all but one, or update the one "
            "meant with update-block --id.", matches=matches)
    return heading_uuid, (matches[0] if matches else None)


def plan_section_write(api, page: str, heading: str | None, values, *, page_exists: bool,
                       upsert: Upsert | None = None, keep_last: bool = False,
                       preview: bool = False) -> SectionPlan:
    """The plan for writing ``values`` under ``heading`` on ``page`` (at the end
    of the page with no heading). ``page_exists`` is the answer the caller
    already has. Reads, and never writes.

    The empty block a write would go before is read for a ``preview`` only:
    the run's helpers read the section themselves right before they write,
    and a read here as well would only add a request (measured: one per run
    with keep_empty_blocks_last on)."""
    values = tuple(values)
    roots = _roots(values)
    path = _path(values, upsert)
    up = None
    anchor = None
    if path == "upsert":
        heading_uuid, target = _choose_upsert_target(api, page, heading, upsert)
        held = {}
        if target:
            if upsert.expect:
                matched = read_fresh(api, target)
                check_precondition(matched, upsert.expect)
                # Held to the text the hash was checked on, as update-block does.
                held = {"expect": matched.get("content") or ""}
            else:
                require_precondition(Owed(("--expect-hash",)), False)
        elif upsert.expect:
            print_note("Note: --expect-hash has no object: no block matches "
                       f"'{upsert.heading}' under '{heading}', so one is created.")
        up = UpsertPlan("replace" if target else "create", target, held)
        # A block to update is written in place; only one that is not there
        # yet goes at the end of the heading, before the empty blocks there.
        if preview and keep_last and heading_uuid and not target:
            anchor = first_empty_at_end(api, roots, parent_uuid=heading_uuid)
    else:
        heading_uuid = find_heading(api, page, heading) if heading and page_exists else None
        if preview and keep_last and page_exists:
            # With the heading missing there is no empty block to name: the
            # heading would go before the empty blocks at the end of the
            # page, and a write reports where its content went (#110).
            if not heading:
                anchor = first_empty_at_end(api, roots, page_name=page)
            elif heading_uuid:
                anchor = first_empty_at_end(api, roots, parent_uuid=heading_uuid)
    return SectionPlan(
        page=page, heading=heading, values=values, roots=tuple(roots), path=path,
        would_create_page=not page_exists,
        would_create_heading=bool(heading) and not heading_uuid,
        heading_uuid=heading_uuid, blocks=count_blocks(roots), anchor=anchor, keep_last=keep_last,
        upsert=up)


def plan_fields(plan: SectionPlan) -> dict:
    """The --dry-run JSON keys the three commands share."""
    fields = {"would_create_page": plan.would_create_page,
              "would_create_heading": plan.would_create_heading,
              **would_go_before_fields(plan.anchor)}
    if plan.upsert is not None:
        fields["action"] = plan.upsert.action
        if plan.upsert.action == "replace":
            fields["would_replace"] = plan.upsert.target
    return fields


def plan_lines(plan: SectionPlan) -> list:
    """The --dry-run text lines for :func:`plan_fields`."""
    lines = []
    if plan.would_create_page:
        lines.append(f"  page: {plan.page} (would be created)")
    if plan.would_create_heading:
        lines.append(f"  heading: {plan.heading} (would be created)")
    if plan.upsert is not None:
        lines.append(f"  would replace the block {plan.upsert.target}"
                     if plan.upsert.action == "replace" else "  would create the block")
    if plan.anchor:
        lines.append(f"  would go before the empty block {plan.anchor[:8]}...")
    return lines


def apply_section_plan(api, plan: SectionPlan, *, create_page, keep_ids: bool = False) -> SectionWritten:
    """Carry ``plan`` out. ``create_page`` creates the missing page, the way the
    command does: a journal page and a note page are created differently."""
    if plan.would_create_page:
        create_page()
    if plan.path == "upsert":
        return _apply_upsert(api, plan)
    heading_uuid = (find_or_create_heading(api, plan.page, plan.heading, keep_last=plan.keep_last)
                    if plan.heading else None)
    end = {"parent_uuid": heading_uuid} if heading_uuid else {"page_name": plan.page}
    roots = list(plan.roots)
    if plan.path == "batch":
        # One command is one write: all the roots go before the empty blocks
        # together, in order (#110).
        uuids, anchor = (before_first_empty(api, roots, keep_ids=keep_ids, **end)
                         if plan.keep_last else (None, None))
        if uuids is None:
            uuids = []
            for kind, payload in plan.values:
                if kind == "tree":
                    uuids.extend(insert_block_tree_with_uuids(api, payload, heading_uuid, keep_ids=keep_ids)
                                 if heading_uuid else
                                 insert_block_tree_at_page_top(api, payload, plan.page, keep_ids=keep_ids))
                else:
                    r = (insert_block_at(api, heading_uuid, payload, sibling=False, keep_ids=keep_ids)
                         if heading_uuid else append_in_page(api, plan.page, payload, keep_ids))
                    uuids.append(r["uuid"])
        return SectionWritten(uuids, anchor, heading_uuid)
    if plan.path == "tree":
        legacy = ((lambda: insert_block_tree_with_uuids(api, roots, heading_uuid, keep_ids=keep_ids))
                  if heading_uuid else
                  (lambda: insert_tree_at_page_end(api, plan.page, roots, keep_ids=keep_ids)))
        uuids, anchor = before_empty_or_append(api, roots, legacy, keep_last=plan.keep_last,
                                               keep_ids=keep_ids, **end)
        return SectionWritten(uuids, anchor, heading_uuid)
    # The flat path. The write is proven like every other: the API raises when
    # Logseq's answer names no block. Unchecked, a single flat entry (the
    # common case for a timestamped log line) was once reported as added with
    # exit 0 while nothing had been written.
    content = plan.values[0][1]
    written, anchor = (before_first_empty(api, roots, keep_ids=keep_ids, **end)
                       if plan.keep_last else (None, None))
    if written is not None:
        result = api.get_block(written[0], include_children=False) or {"uuid": written[0]}
    elif heading_uuid:
        result = insert_block_at(api, heading_uuid, content, sibling=False, keep_ids=keep_ids)
    else:
        result = append_in_page(api, plan.page, content, keep_ids)
    return SectionWritten([result["uuid"]], anchor, heading_uuid, result=result)


def _apply_upsert(api, plan: SectionPlan) -> SectionWritten:
    heading_uuid = plan.heading_uuid or find_or_create_heading(
        api, plan.page, plan.heading, keep_last=plan.keep_last)
    roots = list(plan.roots)
    target = plan.upsert.target
    if target:
        # The first root replaces the matched block; its children nest under
        # it. Any further roots are siblings after it: dropping them would
        # lose content while still reporting them as written.
        text = roots[0]["content"]
        # The matched block keeps its properties, as with update-block (#67).
        _, kept = kept_properties(api, target, text)
        # The replaced block's hash, as its proof read it: the inserts below
        # go through proofs of their own and leave it as it is.
        written = api.update_block(target, text, properties=kept or None, **plan.upsert.held)
        # The API proves each insert and raises on one that did not land, so
        # the uuids are what the graph took, not the size asked for.
        uuids = [target]
        kids = roots[0].get("children") or []
        if kids:
            uuids += insert_block_tree_with_uuids(api, kids, target)
        if len(roots) > 1:
            uuids += insert_block_tree_as_siblings(api, roots[1:], target)
        return SectionWritten(uuids, None, heading_uuid, status="updated",
                              hash=written if isinstance(written, str) else None)
    # A new block as the last child of the heading: the write
    # [graph] keep_empty_blocks_last is about. A found block is replaced where
    # it stands, which is no end position.
    created, anchor = before_empty_or_append(
        api, roots, lambda: insert_block_tree_with_uuids(api, roots, heading_uuid),
        keep_last=plan.keep_last, parent_uuid=heading_uuid)
    return SectionWritten(created, anchor, heading_uuid, status="created")
