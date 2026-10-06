import datetime
import re

import click

from logseq_cli.blocktext import pointer_target
from logseq_cli.dates import (
    journal_day_to_date,
    next_occurrence,
    parse_date_keyword,
    timestamps,
)
from logseq_cli.group import cli
from logseq_cli.lookup import find_blocks_by_content
from logseq_cli.notes import print_note
from logseq_cli.output import fail, follow_page, handle_connection_error, output
from logseq_cli.safety import WriteCommand
from logseq_cli.tasks import (FRONTEND_MARKERS, ORDER, STATES, marker_before_newline, marker_of,
                              markers_in, marker_clause,
                              task_text, with_marker)


def follow_ref_chain(api, block: dict, as_json: bool):
    """The block at the end of the refs ``block`` stands for, and the uuids
    passed on the way there: ``(target, followed)``.

    A block that holds only ``((uuid))`` or ``{{embed ((uuid))}}``, apart
    from its property lines, stands for its target, and so may that target:
    a task pulled forward twice. Followed one step only, the link in the
    middle became ``DONE ((T))``, a second task, while T stayed open (#106).
    ``followed`` runs from the first target to the last, without ``block``.

    Refuses without a write when the chain comes back to a block it passed
    (``ref_cycle``) or leads to one that is gone (``dead_ref``), which
    includes the placeholder Logseq keeps for it (api.block_or_none). Either
    way no block on it is the task the caller meant.
    """
    start = block["uuid"].lower()
    followed, target = [], block
    while (uuid := pointer_target(target.get("content"))):
        if uuid == start or uuid in followed:
            fail(f"The refs from block {start} come back to block {uuid}. "
                 "Nothing was written.",
                 as_json, reason="ref_cycle", id=uuid, followed=followed)
        read = api.get_block(uuid, include_children=False)
        if not read:
            fail(f"The refs from block {start} lead to block {uuid}, which is gone "
                 "(a dead ref). Nothing was written.",
                 as_json, reason="dead_ref", id=uuid, followed=followed)
        followed.append(uuid)
        target = read
    return target, followed


def _fetch_todo_references(api, markers) -> dict:
    """Map each referenced todo's uuid to the pages its references sit on.

    In Logseq a block reference is not a copy, it is the same block appearing in
    a second place: checking off a reference checks off the original. Carrying an
    open task forward by ``((uuid))`` is therefore the ordinary way to keep it
    alive, and the later journals hold references rather than blocks of their
    own. ``:block/refs`` is a real relation, so this needs no string matching on
    the ``((uuid))`` form.

    Answers ``{uuid: [(journal_day_or_None, page_name), ...]}``, unordered and
    with duplicates intact — the caller decides what a date range keeps and how
    the rest is counted, which it cannot do once entries are dropped here.
    """
    query = (
        '[:find (pull ?src [:block/uuid]) '
        '(pull ?refp [:block/original-name :block/name :block/journal-day]) '
        ':where [?src :block/marker ?m] '
        + marker_clause("?m", sorted(markers)) + ' '
        '[?ref :block/refs ?src] '
        '[?ref :block/page ?refp]]'
    )
    occurrences = {}
    for row in api.datascript_query(query) or []:
        if not (isinstance(row, (list, tuple)) and len(row) >= 2):
            continue
        src, refp = row[0], row[1]
        # A pull answers None, not {}, for an entity carrying none of the
        # requested attributes — seen on a live graph, and it is the reference
        # pages without a name that hit this.
        if not isinstance(src, dict) or not isinstance(refp, dict):
            continue
        uuid = src.get("uuid")
        name = refp.get("original-name") or refp.get("name", "")
        if not uuid or not name:
            continue
        jd = refp.get("journal-day") or refp.get("journalDay")
        occurrences.setdefault(uuid, []).append((jd, name))
    return occurrences

def _place_references(occurrences, date_start, date_end, limit: int):
    """Pick the occurrences to report and count the ones left out.

    Answers ``(names, withheld)``. ``names`` is sorted newest first, because
    Datalog guarantees no result order and because the most recent occurrence is
    the one a caller reaches for first — the origin is already in ``page``.

    Two things fall out rather than being listed. An occurrence outside a given
    range is not an answer to the question asked; and an occurrence on a page
    with no ``journal-day`` cannot be shown to fall inside a range at all, the
    same rule the origin page already follows. Both are counted in ``withheld``
    instead of vanishing: that a task has been carried for months is worth
    knowing even when the dates themselves are not asked for.
    """
    dated, undated = [], []
    for jd, name in occurrences:
        if jd is None:
            undated.append(name)
            continue
        try:
            dated.append((journal_day_to_date(jd), name))
        except (ValueError, TypeError):
            # An unparseable journal-day places an occurrence no better than a
            # missing one does.
            undated.append(name)

    if date_start or date_end:
        in_range, out_of_range = [], len(undated)
        for d, name in dated:
            dt = datetime.datetime.combine(d, datetime.time())
            if (date_start and dt < date_start) or (date_end and dt > date_end):
                out_of_range += 1
            else:
                in_range.append((d, name))
        kept = [name for _, name in sorted(in_range, key=lambda e: e[0], reverse=True)]
        withheld = out_of_range
    else:
        kept = [name for _, name in sorted(dated, key=lambda e: e[0], reverse=True)]
        kept += sorted(undated)
        withheld = 0

    # Two references written on the same day are one occurrence of that day:
    # the field names where a task stood, not how often it was typed.
    deduped = list(dict.fromkeys(kept))
    withheld += len(kept) - len(deduped)

    if limit and len(deduped) > limit:
        withheld += len(deduped) - limit
        deduped = deduped[:limit]
    return deduped, withheld

def _given(ctx, name):
    """Whether the option was typed on the command line: its source, not its
    value, since --state open equals the default and is still given."""
    return ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE


@cli.command("get-todos", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-todos --status TODO --status DOING
  logseq-cli --token TOKEN get-todos --page "Projects" --tag urgent
  logseq-cli --token TOKEN get-todos --from 2026-05-01 --to 2026-05-31 --state open --state done
  logseq-cli --token TOKEN get-todos --due-from today --due-to 2026-05-08
  logseq-cli --token TOKEN get-todos --page-type journal --no-follow-refs --to 2025-10-06
Notes:
""" f"""  --state {'|'.join(STATES)} (repeatable, default open: {', '.join(markers_in(['open']))}) selects by state;
  --status picks single markers and overrides --state.
""" """  --due-from today --due-to <today plus 7 days> lists what is due in the next seven days.
  A task carried forward by a block-ref ((uuid)) is found on the day it stands,
  and reported once: "page" and "uuid" stay the original block, "references"
  names the other pages it appears on. Following refs costs one extra query for
  the whole command, not one per task. --no-follow-refs restores the old reading.
  Plain-text output: "MARKER [Page] preview" — page name inline, no grouping needed.
  --json: {"todos": [...], "count": N}, plus "repeating_excluded": N when a
  due range left out repeaters it could not place. Each task has marker,
  content (the task text without its marker, properties, SCHEDULED/DEADLINE
  and LOGBOOK), page and uuid; journal_day when the page its block lives on is
  a journal; scheduled, deadline, next_due (dates as YYYY-MM-DD), repeating,
  references, references_withheld where they apply. A key that does not apply
  is absent, not null.
  --match is a Python regex over that content as stored: ((refs)) are not
  resolved, and ^/$ anchor the whole text unless the pattern starts with (?m).
""")
@click.option("--state", multiple=True, default=("open",),
              type=click.Choice(STATES, case_sensitive=False),
              help=f"Select by state (repeatable): {', '.join(STATES)}. Default: open.")
@click.option("--status", multiple=True, type=click.Choice(ORDER, case_sensitive=False),
              help=f"Select single markers instead of states (repeatable, case does not matter): "
                   f"{', '.join(ORDER)}. Given together with --state, --status applies.")
@click.option("--page", "--name", default=None, help="Filter by page name (substring, case-insensitive)")
@click.option("--page-type", "page_type", default=None,
              type=click.Choice(["journal", "page"], case_sensitive=False),
              help="Only tasks whose block stands on a journal page ('journal') or on any "
                   "other page ('page'). The block counts, not the pages it was carried to "
                   "by ((block-ref)): with --from/--to a task still counts as in the range "
                   "through such a page. To measure the range by the block's own day, add "
                   "--no-follow-refs.")
@click.option("--tag", default=None, help="Filter by hashtag (e.g. 'urgent', without #)")
@click.option("--match", "match", default=None, help="Filter by what the task says: a regular expression, case-insensitive, searched in the task text (not its properties)")
@click.option("--from", "from_date", default=None, help="Only TODOs on or after this date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow'). Dates come from the journal pages a task stands on — the one its block lives on and the ones it was carried into by ((block-ref)) — so tasks found only on ordinary pages are excluded whenever a range is given.")
@click.option("--to", "to_date", default=None, help="Only TODOs on or before this date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow'). Same page rule as --from.")
@click.option("--due-from", "due_from", default=None, help="Only tasks due on or after this date, by SCHEDULED/DEADLINE rather than by the journal page they sit on. A repeating task is placed by its next occurrence, derived from the date in its text; one whose interval cannot be read is left out and named on stderr")
@click.option("--due-to", "due_to", default=None, help="Only tasks due on or before this date. Same rule as --due-from")
@click.option("--include-done", is_flag=True, hidden=True, help="Also include DONE tasks")
@click.option("--refs-limit", "refs_limit", type=int, default=10, show_default=True,
              help="Occurrences kept per task in 'references'; 0 lifts the cap. references_withheld counts everything left out, which with --from/--to also includes occurrences outside the range and on pages with no journal-day — so 0 does not make it zero")
@click.option("--no-follow-refs", "no_follow_refs", is_flag=True,
              help="Do not resolve block-refs: report only where task blocks live, not where they appear. Saves one read. With --from/--to the range is measured by the day of the page the block lives on")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_todos(ctx, state, status, page, page_type, tag, match, from_date, to_date, due_from, due_to,
              include_done, refs_limit, no_follow_refs, as_json):
    """List all TODOs/tasks in the graph."""
    api = ctx.obj["api"]

    if _given(ctx, "status"):
        markers = set(status)
        if _given(ctx, "state"):
            print_note("Note: --state was not applied, because --status is given.")
    else:
        markers = set(markers_in(state))
    if include_done:
        markers.add("DONE")

    if refs_limit < 0:
        fail("--refs-limit must be 0 or greater (0 lifts the cap).", as_json)
    try:
        match_re = re.compile(match, re.IGNORECASE) if match else None
    except re.error as e:
        fail(f"--match is not a valid regular expression: {e}.", as_json)

    query = (
        '[:find (pull ?b [:block/content :block/marker :block/uuid '':block/scheduled :block/deadline :block/repeated?]) '
        '(pull ?p [:block/original-name :block/name :block/journal-day]) '
        ':where [?b :block/marker ?m] '
        + marker_clause("?m", sorted(markers)) + ' '
        '[?b :block/page ?p]]'
    )
    results = api.datascript_query(query)

    # One extra read for the whole command, not one per task: the relation is
    # queried in bulk and joined below. --no-follow-refs skips it entirely
    # rather than fetching what it will not use.
    occurrences = {} if no_follow_refs else _fetch_todo_references(api, markers)

    todos = []
    for block_data, page_data in results:
        content = block_data.get("content", "")
        marker = block_data.get("marker", "")
        uuid = block_data.get("uuid", "")
        page_name = page_data.get("original-name") or page_data.get("name", "")
        journal_day = page_data.get("journal-day") or page_data.get("journalDay")

        clean_content = task_text(content, marker)

        record = {
            "marker": marker,
            "content": clean_content,
            "page": page_name,
            "uuid": uuid,
            "_journal_day": journal_day,
        }
        # The day the task was noted on, for its age. Already read for
        # --from/--to; absent on an ordinary page, like the due fields below.
        if journal_day:
            try:
                record["journal_day"] = str(journal_day_to_date(journal_day))
            except (ValueError, TypeError):
                pass
        # scheduled/deadline are YYYYMMDD integers, the same shape as
        # journal-day (verified against a live graph), so the existing
        # conversion applies. Absent keys stay absent: a graph that does not
        # use these fields must see the payload it saw before.
        for field in ("scheduled", "deadline"):
            raw = block_data.get(field)
            if raw:
                try:
                    record[field] = str(journal_day_to_date(raw))
                except (ValueError, TypeError):
                    pass
        if block_data.get("repeated?"):
            record["repeating"] = True
            # Logseq stores the date as written, which is the next occurrence
            # only if the task was ticked off by its checkbox, so the next one
            # is derived with the source's own formula (see next_occurrence). A repeater whose interval cannot be read is
            # left without next_due and reported rather than guessed at. The
            # repeater is the one of the timestamp whose date is reported.
            reported = "DEADLINE" if record.get("deadline") else "SCHEDULED"
            stored = record.get("deadline") or record.get("scheduled")
            planned = next((ts for ts in timestamps(content) if ts.kind == reported), None)
            if planned and stored:
                if planned.repeater is None:
                    # Logseq moves only a timestamp with a repeater.
                    record["next_due"] = stored
                else:
                    try:
                        nxt = next_occurrence(datetime.date.fromisoformat(stored),
                                              planned.repeater)
                    except (ValueError, TypeError):
                        nxt = None
                    if nxt:
                        record["next_due"] = str(nxt)
        todos.append(record)

    # Filter by page if requested
    if page:
        page_lower = page.lower()
        todos = [t for t in todos if page_lower in t["page"].lower()]

    # journal_day is set only for a block on a journal page, so it tells the
    # kind of page its block stands on, whatever refs carried it elsewhere.
    if page_type:
        on_journal = page_type.lower() == "journal"
        todos = [t for t in todos if ("journal_day" in t) == on_journal]

    # Filter by tag if requested
    if tag:
        tag_pattern = re.compile(rf"#\b{re.escape(tag)}\b", re.IGNORECASE)
        todos = [t for t in todos if tag_pattern.search(t["content"])]

    # On the cleaned text, so a property value or a LOGBOOK timestamp cannot
    # match a task that does not say it.
    if match_re:
        todos = [t for t in todos if match_re.search(t["content"])]

    # Resolve block references. A task carried forward by ((uuid)) stands on the
    # later day as much as on the day it was written, so its occurrences are
    # attached here — before the date filter, which reads them.
    date_start = (
        datetime.datetime.combine(parse_date_keyword(from_date), datetime.time())
        if from_date else None
    )
    date_end = (
        datetime.datetime.combine(parse_date_keyword(to_date), datetime.time())
        if to_date else None
    )
    for t in todos:
        refs = occurrences.get(t["uuid"])
        if not refs:
            continue
        names, withheld = _place_references(refs, date_start, date_end, refs_limit)
        # A task with no occurrence left to report carries no field: a caller
        # reading tasks that are not carried forward sees the payload it saw
        # before this command learned to follow references.
        if names:
            t["references"] = names
        if withheld:
            t["references_withheld"] = withheld

    # Filter by date range. A task counts as inside the range if the journal
    # page it sits on is, or if it appears inside it through a reference —
    # checking off a reference checks off the original, so both are the same
    # task standing on that day.
    #
    # A page carrying no journal-day cannot be shown to fall inside the range,
    # so it falls out of it, and the same rule governs reference pages: 44 of
    # 248 reference occurrences measured on a live graph sit on ordinary pages.
    # Letting either pass made the filter apply to the journal subset only and
    # stay silent about the rest: a range predating the graph still returned
    # every task on an ordinary page, and no caller could tell which part had
    # been filtered.
    if from_date or to_date:
        filtered = []
        for t in todos:
            if t.get("references"):
                filtered.append(t)
                continue
            jd = t.get("_journal_day")
            if jd is None:
                continue
            try:
                d = journal_day_to_date(jd)
                dt = datetime.datetime.combine(d, datetime.time())
                if date_start and dt < date_start:
                    continue
                if date_end and dt > date_end:
                    continue
                filtered.append(t)
            except (ValueError, TypeError):
                # An unparseable journal-day is no more inside the range than a
                # missing one; keeping it here would reintroduce the same
                # silent pass-through for a rarer input.
                continue
        todos = filtered

    # Filter by due date. Separate from --from/--to on purpose: those date a
    # task by the journal page it sits on, which is when it was written down.
    #
    # A repeating task is placed by its next occurrence (next_due, derived
    # above), not by the date in its text. Logseq moves that date on only when
    # the task is ticked off by its checkbox; one never ticked still reads the
    # date it was written with (measured: a weekly task from 2020 reads
    # 20200106).
    # A repeater whose interval cannot be read has no next occurrence, so it
    # is left out and reported rather than placed on a date it does not have.
    repeating_excluded = []
    if due_from or due_to:
        due_start = parse_date_keyword(due_from) if due_from else None
        due_end = parse_date_keyword(due_to) if due_to else None
        kept = []
        for t in todos:
            # A deadline is the commitment; a schedule is when work starts. A
            # task carrying both is placed by its deadline. For a repeater the
            # derived next occurrence replaces the date in its text, which may
            # be long past — filtering on that would place a live weekly task
            # in the year it was created.
            due = t.get("next_due") or t.get("deadline") or t.get("scheduled")
            if not due:
                continue
            if t.get("repeating") and not t.get("next_due"):
                repeating_excluded.append(t)
                continue
            try:
                d = datetime.date.fromisoformat(due)
            except (ValueError, TypeError):
                continue
            if due_start and d < due_start:
                continue
            if due_end and d > due_end:
                continue
            kept.append(t)
        todos = kept

    # Strip internal _journal_day before output
    for t in todos:
        t.pop("_journal_day", None)

    # Sort: DOING/NOW first, then by page
    rank = {m: i for i, m in enumerate(ORDER)}
    todos.sort(key=lambda t: (rank.get(t["marker"], len(ORDER)), t["page"].lower()))

    if repeating_excluded:
        # Named, not just counted: a bare number would leave the caller unable
        # to tell which commitments were left out of the answer.
        print_note(
            f"⚠️  {len(repeating_excluded)} repeating task(s) excluded from the "
            f"due range — their repeat interval could not be read, so the next "
            f"occurrence cannot be derived:")
        for t in repeating_excluded:
            print_note(f"     {t['content'][:70]} ({t['page']})")

    if as_json:
        payload = {"todos": todos, "count": len(todos)}
        if repeating_excluded:
            payload["repeating_excluded"] = len(repeating_excluded)
        output(payload, True)
    else:
        if not todos:
            click.echo("No tasks found.")
        else:
            click.echo(f"Tasks ({len(todos)}):\n")
            for t in todos:
                preview = t["content"][:100] + ("..." if len(t["content"]) > 100 else "")
                click.echo(f"  {t['marker']} [{t['page']}] {preview}")
                # Named here too, not only in JSON: the gap this closes was
                # just as invisible in plain text, and "[Mar 4th]" alone still
                # reads as though the task had not been touched since.
                refs = t.get("references")
                if refs:
                    withheld = t.get("references_withheld")
                    more = f" (+{withheld} more)" if withheld else ""
                    # Semicolons, not commas: a journal page is named
                    # "2026-09-16, Wednesday", so a comma-separated list of
                    # them reads as twice as many entries as it holds.
                    click.echo(f"      also on: {'; '.join(refs)}{more}")
                elif t.get("references_withheld"):
                    click.echo(f"      also on {t['references_withheld']} other page(s)")

def _refuse_no_task(block: dict, content: str, as_json: bool):
    """Refuse a block that is no task to this command (never returns). Which
    sentence fits is checked in this order: a block that only points to
    another, a marker Logseq stored that this command cannot reach, a line
    break right behind a marker word, anything else. The marker rules
    themselves are tasks'."""
    uuid = block.get("uuid")
    target = pointer_target(content)
    if target:
        fail(f"Block {uuid} is not a task: it only points to block {target}. "
             "Pass --follow-refs to change the task it points to.",
             as_json, reason="not_a_task", id=uuid, points_to=target)
    if block.get("marker"):
        fail(f"Block {uuid} is a task, but its marker is not where this command can change "
             'it: it must start the text, after an optional "#" prefix and a space. Change '
             "the marker in Logseq, or rewrite the text so the marker starts it.",
             as_json, reason="not_a_task", id=uuid)
    word = marker_before_newline(content)
    if word:
        fail(f"Block {uuid} is not a task: Logseq reads no marker in it, because a line "
             f'break follows "{word}" directly. Put text or a space after "{word}" on the '
             "first line.", as_json, reason="not_a_task", id=uuid)
    fail(f"Block {uuid} is not a task: Logseq reads no marker in it. A task starts with a "
         'marker, e.g. "TODO …".', as_json, reason="not_a_task", id=uuid)


@cli.command("set-todo-status", cls=WriteCommand, epilog="""\b
Examples:
  logseq-cli --token TOKEN set-todo-status --id UUID --status DONE
  logseq-cli --token TOKEN set-todo-status --content "ship the parser" \\
                                            --page "Project Alpha" --status DONE
  logseq-cli --token TOKEN set-todo-status --id JOURNAL-UUID --status DONE --follow-refs
Notes:
""" f"""  Status values: {', '.join(FRONTEND_MARKERS)}.
""" """  --follow-refs: when the block holds only a ((uuid)) ref or {{embed ((uuid))}},
  follows it, and on through every such block, and updates the one at the end.
  A chain that loops or reaches a missing block is refused, nothing written.
  Prefer this over replace-text for marker changes — 1 call, deterministic.
  A block Logseq reads no marker in is not a task: it is refused and nothing is
  written, also with --dry-run. A block that holds only a ((uuid)) ref is refused
  too, and the message names --follow-refs.
  --content that matches blocks but none with a marker is refused too (without
  --follow-refs).
""")
@click.option("--id", "block_id", default=None, help="Block UUID (find by UUID)")
@click.option("--content", default=None, help="Content substring to find the block (used with --page)")
@click.option("--page", "--name", default=None, help="Page to search in (used with --content)")
@click.option("--status", required=True,
              type=click.Choice(FRONTEND_MARKERS, case_sensitive=False),
              help=f"Marker to set: {', '.join(FRONTEND_MARKERS)} (case does not matter). It takes "
                   "a marker, not a state, because only the marker DOING separates it from TODO.")
@click.option("--follow-refs", is_flag=True,
              help="If the block holds only a ((uuid)) ref or an embed of one, follow it, "
                   "through every such block, and update the block at the end instead.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the marker change, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_todo_status(ctx, block_id, content, page, status, follow_refs, dry_run, as_json):
    """Update the status of a TODO block (e.g. TODO → DONE).

    Identify the block either by UUID (--id) or by content substring + page (--content + --page).
    Use --follow-refs when the block is a ((uuid)) reference in a journal and the original block
    lives on a project page. A block holding only {{embed ((uuid))}} counts as a reference too,
    and a chain of such references is followed to its end.

    Examples:
      logseq-cli set-todo-status --id UUID --status DONE
      logseq-cli set-todo-status --content "ship the parser" --page "Project Alpha" --status DONE
      logseq-cli set-todo-status --id JOURNAL-REF-UUID --status DONE --follow-refs
    """
    api = ctx.obj["api"]

    if not block_id and not (content and page):
        fail("Specify either --id or both --content and --page.", as_json,
             reason="missing_selector")

    # Resolve UUID via content search if needed
    if not block_id:
        page = follow_page(api, page, as_json).page
        matches = find_blocks_by_content(api, content, page=page, with_task_fields=True)
        # Prefer blocks that carry a marker: a status change is only meaningful
        # there, and it disambiguates a text that also appears in prose.
        task_matches = [m for m in matches if m.get("marker")]
        candidates = task_matches or matches
        if matches and not task_matches and not follow_refs:
            fail(f"{len(matches)} blocks match '{content}' on page '{page}', none is a task "
                 f'(no marker). Pass --id of a task, or write one as "TODO …".',
                 as_json, reason="no_task_matches", content=content, page=page,
                 match_count=len(matches))
        if not candidates:
            fail(f"No block found matching '{content}' on page '{page}'.", as_json,
                 reason="block_not_found", content=content, page=page)
        if len(candidates) > 1:
            # Taking the first match would silently rewrite one of several
            # equally valid blocks, and the caller could not tell which. This
            # command overwrites content, so an ambiguous selector must stop.
            listing = "\n".join(
                f"  {m.get('uuid')}  {(m.get('content') or '')[:70]}"
                for m in candidates[:10]
            )
            more = f"\n  ... and {len(candidates) - 10} more" if len(candidates) > 10 else ""
            fail(f"{len(candidates)} blocks match '{content}' on page '{page}'; refusing "
                 f"to guess which one to update. Narrow --content or pass --id:\n"
                 f"{listing}{more}", as_json, reason="ambiguous", content=content, page=page,
                 matches=[m.get("uuid") for m in candidates])
        block = candidates[0]
    else:
        block_id = block_id.strip("()")
        block = api.get_block(block_id, include_children=False)
        if not block:
            fail(f"Block {block_id} not found.", as_json, reason="block_not_found", id=block_id)

    # With --follow-refs every JSON result says which blocks it went through.
    chain = {}
    if follow_refs:
        block, chain["followed"] = follow_ref_chain(api, block, as_json)
    block_id = block.get("uuid")
    old_content = block.get("content", "")

    # A task is a block Logseq stored a marker for. The text must show it where
    # with_marker can change it, too: it writes on what marker_of reads.
    old_marker = marker_of(old_content)
    if not block.get("marker") or old_marker is None:
        _refuse_no_task(block, old_content, as_json)

    new_content = with_marker(old_content, status)
    if new_content == old_content:
        if as_json:
            output({"uuid": block_id, "status": "unchanged", "content": old_content, **chain},
                   True)
        else:
            click.echo("No change (block already has this status).")
        return

    # The marker swap is the whole change, so the preview shows both markers and
    # the line they sit on: enough to tell the right block from a near-identical
    # one before committing. Resolution and the ambiguity guard above already ran.
    if dry_run:
        if as_json:
            output({"uuid": block_id, "old_marker": old_marker, "new_marker": status,
                    "old": old_content, "new": new_content, "status": status,
                    "dry_run": True, **chain}, True)
        else:
            click.echo(f"[DRY RUN] Would set status on block {block_id}")
            if chain.get("followed"):
                click.echo(f"  followed: {' -> '.join(chain['followed'])}")
            click.echo(f"  marker: {old_marker} -> {status}")
            preview = old_content[:60] + ("..." if len(old_content) > 60 else "")
            click.echo(f"  was: {preview}")
            preview = new_content[:60] + ("..." if len(new_content) > 60 else "")
            click.echo(f"  now: {preview}")
        return

    # Only the marker changes; lines the block already had are not ours to refuse.
    api.update_block(block_id, new_content, replacing=old_content)

    if as_json:
        output({"uuid": block_id, "old": old_content, "new": new_content, "status": status,
                **chain}, True)
    else:
        click.echo(f"Updated: {old_content[:60]}{'...' if len(old_content) > 60 else ''}")
        click.echo(f"      → {new_content[:60]}{'...' if len(new_content) > 60 else ''}")
