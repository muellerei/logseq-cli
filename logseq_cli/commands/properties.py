import re

import click

from logseq_cli.blocktext import refuse_split_property, with_property_line
from logseq_cli.group import cli
from logseq_cli.datalog import edn_keyword, edn_string
from logseq_cli.blockprops import (
    coerce_property_value,
    normalize_property_key,
    note_renamed_property_key,
    stored_properties,
)
from logseq_cli.strictinsert import require_insert
from logseq_cli.output import fail, follow_page, handle_connection_error, output
from logseq_cli.render import is_properties_block


# Logseq's hidden-built-in-properties (graph-parser property.cljs, 0.10.15),
# with the card keys srs.cljs registers into it, keyed as stored: kept for
# a block itself, never shown as a property.
_HIDDEN_BUILT_IN = frozenset({
    "id", "custom-id", "background-color", "heading", "collapsed",
    "created-at", "updated-at", "last-modified-at",
    "query-table", "query-properties", "query-sort-by", "query-sort-desc",
    "ls-type", "hl-type", "hl-page", "hl-stamp", "hl-color",
    "logseq.macro-name", "logseq.macro-arguments", "logseq.order-list-type",
    "logseq.tldraw.page", "logseq.tldraw.shape",
    "todo", "doing", "now", "later", "done",
    "card-last-interval", "card-repeats", "card-last-reviewed",
    "card-next-schedule", "card-ease-factor", "card-last-score",
})


def _property_key_spellings(key: str):
    """Return the datalog spellings to try for a property key.

    Logseq stores property keys kebab-cased in datalog but shows them
    camelCased. A camelCase key gets its kebab form added so either spelling
    the user types finds the page; the camelCase form is kept too, in case a
    foreign graph stored it that way. Order preserved, duplicates dropped.
    """
    kebab = re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), key)
    forms = [key]
    if kebab != key:
        forms.append(kebab)
    return forms

def _find_stored_property_key(props: dict, key: str):
    """Find the stored spelling of a user-typed property key.

    Keys are read as stored (kebab-cased, see stored_properties), but users
    type them the way the API and Logseq's UI show them (excludeFromGraphView);
    a plain .lower() matches neither form against the other. Compare with
    dashes stripped and case folded so every spelling finds the stored key.
    """
    want = key.replace("-", "").lower()
    for stored in props:
        if stored.replace("-", "").lower() == want:
            return stored
    return None

def _format_property_value(value) -> str:
    """A property value as the page writes it, not as Python prints it.

    A collection rendered with ``str()`` comes out as ``['Core']`` - Python
    syntax for something the page spells ``Core``, or ``Core, Edge``
    when it carries several values.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)

def _read_property_value(props: dict, key: str):
    """Read a property value trying every spelling of the key.

    The datalog pull returns kebab-cased keys, so a user who typed the
    camelCase form would otherwise read an empty value off a page the query
    did find. Try each spelling, first hit wins.
    """
    for form in _property_key_spellings(key):
        if form in props:
            return props[form]
    return ""

@cli.command("get-properties", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-properties --name "Alice"
  logseq-cli --token TOKEN get-properties --name "Alice" --property "team"
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--property", "prop_name", default=None, help="Get a specific property by name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_properties(ctx, page, prop_name, as_json):
    """Get properties of a page."""
    api = ctx.obj["api"]
    ref = follow_page(api, page, as_json)
    page = ref.page
    page_data = api.get_page(page)

    if not page_data:
        fail(f"Page '{page}' not found.", as_json=as_json, page=page)

    # Keys as stored, not the camel-cased ones the page object carries: those
    # name keys no file contains (due-date:: listed as dueDate).
    properties, text_values = (stored_properties(api, page_data["uuid"])
                               if page_data.get("uuid") else ({}, {}))
    page_name = page_data.get("originalName") or page_data.get("name", page)
    # This command has always named the page as stored; an alias keeps the
    # rule of the others, the name asked for and alias_of (#63).
    names = ref.fields() if ref.redirected else {"page": page_name}

    # A page whose first block holds text has no page properties to Logseq,
    # but set-property wrote into that block before #80, and a page created
    # empty got its lines in a block Logseq did not take them from. Without
    # this fallback the command reported "No properties" for them.
    if not properties:
        try:
            blocks = api.get_page_blocks_tree(page) or []
        except Exception:
            blocks = []
        first = (blocks[0] or {}) if blocks else {}
        if first.get("uuid"):
            # Not the keys Logseq keeps for the block itself: a heading's
            # ``heading``, its ``id``. They were reported as the page's on
            # 754 of 918 pages of a real graph (#82).
            values, texts = stored_properties(api, first["uuid"])
            properties = {k: v for k, v in values.items() if k not in _HIDDEN_BUILT_IN}
            text_values = {k: v for k, v in texts.items() if k not in _HIDDEN_BUILT_IN}

    if prop_name:
        stored_key = _find_stored_property_key(properties, prop_name)
        if stored_key is None:
            fail(f"Property '{prop_name}' not found on '{page_name}'.",
                 as_json=as_json, page=page_name, property=prop_name)
        value = properties.get(stored_key)
        text_key = _find_stored_property_key(text_values, prop_name)
        text_value = text_values.get(text_key) if text_key else None

        if as_json:
            output({**names, "property": stored_key, "value": value, "text": text_value}, True)
        else:
            click.echo(text_value or value)
    else:
        if as_json:
            output({**names, "properties": properties, "text_values": text_values}, True)
        else:
            if not properties:
                click.echo(f"No properties on '{page_name}'.")
            else:
                click.echo(f"Properties of '{page_name}':\n")
                for key in sorted(properties.keys()):
                    display = text_values.get(key, properties[key])
                    click.echo(f"  {key}:: {display}")

# Keys a page's property block cannot carry as the page's own (#80).
_NOT_PAGE_PROPERTIES = {
    "title": "'title' is refused: saved into the page's property block it renames "
             "the page, past every check rename-page makes. Use rename-page.",
    "collapsed": "'collapsed' is refused: Logseq reads it as the block's folded "
                 "state and takes it out of the page's properties.",
}


def _current_text(api, block):
    """``block``'s text read again just before a write. The whole text is
    written back, so a key another call set since the page was read would
    otherwise be undone; upsertBlockProperty changed one key and had no such
    window."""
    fresh = api.get_block(block["uuid"], include_children=False)
    return fresh.get("content") or "" if isinstance(fresh, dict) else block.get("content") or ""


def _property_block(blocks):
    """The page's property block, or ``None`` when one has to be made.

    Logseq takes a page's properties from its property block, and only when
    that block is saved (``save-block-inner!``; ``upsertBlockProperty`` skips
    that step, so the page kept its old properties, #80). The page's first
    block becomes the property block when it is saved holding nothing but
    property lines, so an empty one, or one of property lines only (what
    set-property used to leave on a page created empty), serves as well.
    A first block with text does not: its lines stay the block's own.
    """
    first = blocks[0] if blocks else None
    if first and (first.get("preBlock?") or is_properties_block(first.get("content") or "")):
        return first
    return None


def _refuse_front_matter(block, page, as_json):
    """A property block written as front matter has no ``key::`` lines to
    set; rewriting it would turn it into something else."""
    if block and (block.get("content") or "").lstrip().startswith("---"):
        fail(f"Page '{page}' keeps its properties as front matter (---); "
             f"edit that in the file.", as_json=as_json, page=page)


def _write_property_block(api, blocks, block, content):
    """Save ``content`` as the page's property block, made before the first
    block when there is none, and removed when nothing is left in it.

    Saved with ``updateBlock``, the one call that makes Logseq take the page's
    properties from the block. A block inserted with property text is not
    made the property block; one inserted empty and then saved is (measured,
    0.10.15). Logseq saves only a change, the text compared trimmed, so a
    block whose text is already right but which the page does not show (the
    old way's leftovers) is saved empty first, then with its text: the same
    two steps as a new one.
    """
    if block is None:
        result = api.insert_block(blocks[0]["uuid"], "", {"before": True, "sibling": True})
        uuid, old = require_insert(result, "the page's property block"), ""
    else:
        uuid, old = block["uuid"], block.get("content") or ""
    if not content and block is not None and not block.get("children") and len(blocks) > 1:
        api.remove_block(uuid)
        return
    if content == old and old:
        api.update_block(uuid, "", replacing=old)
        old = ""
    api.update_block(uuid, content, replacing=old)


def _page_uuid(api, page):
    page_data = api.get_page(page)
    return page_data.get("uuid") if isinstance(page_data, dict) else None


def _page_shows(api, page_uuid, key, value):
    """Whether the page, as Logseq shows it, holds ``key`` as ``value``, or
    lacks it for ``None``. Compared as text, the way the line was written."""
    got = stored_properties(api, page_uuid)[1].get(key) if page_uuid else None
    return got is None if value is None else str(got).strip() == str(value).strip()


def _check_page_took(api, page, page_uuid, key, value, as_json):
    """Fail unless the page now shows ``key`` as written: a write Logseq
    drops, or one another write overtook, must not report success."""
    if _page_shows(api, page_uuid, key, value):
        return
    what = f"'{key}' removed" if value is None else f"'{key}:: {value}'"
    fail(f"Logseq did not show {what} on page '{page}' after the write; "
         f"check the page before retrying.", as_json=as_json, page=page, property=key)


@cli.command("set-property", epilog="""\b
Examples:
  logseq-cli --token TOKEN set-property --name "Alice" --key "team" --value "[[Platform]]"
  logseq-cli --token TOKEN set-property --name "X" --key "type" --value "Person"
Note:
  Properties go into the page's property block, the lines above its first
  block; a page without one gets one, before its first block. Logseq reads the
  page's properties from there, so query-pages-by-property finds them at
  once. The page is read back, and a write it does not show fails.
  "title" is refused: saved there, it renames the page, past every check
  rename-page makes. Use rename-page. "collapsed" is refused too: Logseq
  reads it as the block's folded state, never as the page's.
  Keys are stored the way Logseq reads them back: lower-case, '_' as '-'
  ("Status" becomes "status", said on stderr). A key Logseq would drop is
  refused before anything is read: whitespace, a leading '#', or any of
  : , ; / \\ [ ] ( ) { } | ^ " @ ~ `
  "id" and "custom-id" are refused as well: Logseq reads them as the block's
  uuid.
  A value is one line: a line break in it is refused, since Logseq would read
  each line after it as a line of the block (a block, a property, its id).
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--key", required=True, help="Property key (e.g. 'type', 'team', 'role')")
@click.option("--value", required=True, help="Property value")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the property change, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_property(ctx, page, key, value, dry_run, as_json):
    """Set or update a property in the page's property block."""
    api = ctx.obj["api"]

    # Before the first read: a key Logseq cannot read back must cost nothing.
    try:
        stored = normalize_property_key(key)
    except ValueError as e:
        fail(str(e), as_json=as_json, page=page, property=key)
    note_renamed_property_key(key, stored)
    key = stored
    refuse_split_property(key, value)
    if key in _NOT_PAGE_PROPERTIES:
        fail(_NOT_PAGE_PROPERTIES[key], as_json=as_json, page=page, property=key)

    ref = follow_page(api, page, as_json)
    page = ref.page

    blocks = api.get_page_blocks_tree(page)
    if not blocks:
        fail(f"Page '{page}' not found or has no blocks", as_json=as_json, page=page)
    block = _property_block(blocks)
    _refuse_front_matter(block, page, as_json)

    # Sent as typed unless it is a number that prints back the same (#35)
    value = coerce_property_value(value)
    old = (block or {}).get("content") or ""
    new = with_property_line(old, key, value)
    target = "property block" if block else "new property block"

    if dry_run:
        # Whether this creates or overwrites is the fact worth previewing: the
        # command is called "set" either way, and an unnoticed overwrite loses
        # the old value with no trace. Read from the block the write changes,
        # under the keys as stored (see stored_properties).
        existing = stored_properties(api, block["uuid"])[0] if block else {}
        had = key in existing
        old_value = existing.get(key)
        if as_json:
            output({**ref.fields(), "property": key, "old_value": old_value,
                    "value": value, "existed": had, "target": target,
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would set '{key}::' on page '{page}'"
                       + ("" if block else ", in a new property block before its first block"))
            if had:
                click.echo(f"  was: {old_value}")
            else:
                click.echo("  was: (not set)")
            click.echo(f"  now: {value}")
        return

    page_uuid = _page_uuid(api, page)
    in_step = block is not None and new == old and _page_shows(api, page_uuid, key, value)
    if not in_step:
        if block is not None:
            block = {**block, "content": _current_text(api, block)}
            new = with_property_line(block["content"], key, value)
        _write_property_block(api, blocks, block, new)
        _check_page_took(api, page, page_uuid, key, value, as_json)

    result = {**ref.fields(), "property": key, "value": value,
              "status": "unchanged" if in_step else "updated"}
    if as_json:
        output(result, True)
    elif in_step:
        click.echo(f"'{key}:: {value}' is already set on page '{page}'")
    else:
        click.echo(f"Set '{key}:: {value}' on page '{page}'")

@cli.command("remove-property", epilog="""\b
Examples:
  logseq-cli --token TOKEN remove-property --name "X" --key "deprecated_key"
  logseq-cli --token TOKEN remove-property --id UUID --key "prio"
Note:
  --name removes a PAGE property, from the page's property block (the block
  goes with its last one).
  --id removes the property from that one block, wherever it sits.
  The key is addressed as set-property stores it ("Status" as "status");
  a key set-property would refuse is passed through as given.
""")
@click.option("--page", "--name", default=None, help="Page name (removes a page property)")
@click.option("--id", "block_id", default=None, help="Block UUID (removes the property from that block)")
@click.option("--key", required=True, help="Property key to remove")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show which property would be removed, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def remove_property(ctx, page, block_id, key, dry_run, as_json):
    """Remove a property from a page or from a single block."""
    api = ctx.obj["api"]
    if bool(page) == bool(block_id):
        fail("Specify exactly one of: --name, --id.", as_json=as_json)

    # Address the key under the name set-property stores it as, or "set
    # --key Status" followed by "remove --key Status" removes nothing and still
    # reports success. A key set-property would refuse is passed through as
    # given instead: versions before the check stored such keys verbatim, and
    # until the next re-index the database may still hold one.
    try:
        stored, verbatim = normalize_property_key(key), False
    except ValueError:
        stored, verbatim = key, True
    note_renamed_property_key(key, stored)
    key = stored

    if block_id:
        block_uuid = block_id.strip().replace("((", "").replace("))", "")
        block = api.get_block(block_uuid, include_children=False)
        if not block:
            fail(f"Block not found: {block_uuid}", as_json=as_json, id=block_uuid)
        target = f"block '{block_uuid}'"
        result = {"id": block_uuid, "property": key, "status": "removed"}
    else:
        ref = follow_page(api, page, as_json)
        page = ref.page
        blocks = api.get_page_blocks_tree(page)
        if not blocks:
            fail(f"Page '{page}' not found or has no blocks", as_json=as_json, page=page)
        if not verbatim:
            _remove_page_property(api, ref, blocks, key, dry_run, as_json)
            return
        # Such a key is in the database only: the file line holding it is no
        # property line to Logseq, so there is no line to take out, and Logseq
        # removes it by name.
        block_uuid = blocks[0]["uuid"]
        target = f"page '{page}'"
        result = {**ref.fields(), "property": key, "status": "removed"}

    if dry_run:
        existing, _texts = stored_properties(api, block_uuid)
        _preview_removal(result, target, key, existing.get(key), key in existing, as_json)
        return

    api.remove_block_property(str(block_uuid), key)

    if as_json:
        output(result, True)
    else:
        click.echo(f"Removed '{key}' from {target}")

def _preview_removal(result, target, key, value, present, as_json):
    """remove-property --dry-run. "Property not there" is the outcome worth
    knowing before the write: a caller who misspelled the key would
    otherwise see "Removed" and believe it."""
    if as_json:
        output({**result, "status": "would_remove" if present else "not_present",
                "value": value, "present": present, "dry_run": True}, True)
    elif present:
        click.echo(f"[DRY RUN] Would remove '{key}' from {target}")
        click.echo(f"  value: {value}")
    else:
        click.echo(f"[DRY RUN] '{key}' is not set on {target}; nothing would be removed")


def _remove_page_property(api, ref, blocks, key, dry_run, as_json):
    """remove-property --name: the key's lines go from the page's first block
    and the block is saved, so the page lets go of the key at once (#80).

    That block is the property block, or, on a page that starts with text,
    the block set-property used to write into, whose property it was all
    along. The property block goes when its last line does.
    """
    page, first = ref.page, blocks[0]
    block = _property_block(blocks)
    _refuse_front_matter(block, page, as_json)
    page_uuid = _page_uuid(api, page)
    old = first.get("content") or ""
    # The page may still show a key whose line is gone (the old way's
    # leftovers): that is present too, until the page lets go of it.
    present = with_property_line(old, key, None) != old or (
        first is block and not _page_shows(api, page_uuid, key, None))
    result = {**ref.fields(), "property": key,
              "status": "removed" if present else "not_present"}
    if dry_run:
        value = stored_properties(api, first["uuid"])[0].get(key)
        _preview_removal(result, f"page '{page}'", key, value, present, as_json)
        return
    if present:
        first = {**first, "content": _current_text(api, first)}
        _write_property_block(api, blocks, first, with_property_line(first["content"], key, None))
        _check_page_took(api, page, page_uuid, key, None, as_json)
    if as_json:
        output(result, True)
    elif present:
        click.echo(f"Removed '{key}' from page '{page}'")
    else:
        click.echo(f"'{key}' is not set on page '{page}'; nothing was removed")


@cli.command("set-block-property", epilog="""\b
Example:
  logseq-cli --token TOKEN set-block-property --id UUID --key "status" --value "done"
Note:
  Keys follow the same rule as set-property: stored lower-case with '_' as
  '-', and refused if Logseq would not read them back as a property.
  A value is one line: a line break in it is refused, since Logseq would read
  each line after it as a line of the block (a block, a property, its id).
""")
@click.option("--id", "block_id", required=True, help="Block UUID")
@click.option("--key", required=True, help="Property key")
@click.option("--value", required=True, help="Property value")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the property change, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_block_property(ctx, block_id, key, value, dry_run, as_json):
    """Set or update a property on a specific block."""
    api = ctx.obj["api"]

    try:
        stored = normalize_property_key(key)
    except ValueError as e:
        fail(str(e), as_json=as_json, block=block_id, property=key)
    note_renamed_property_key(key, stored)
    key = stored
    refuse_split_property(key, value)

    # Sent as typed unless it is a number that prints back the same (#35)
    value = coerce_property_value(value)

    if dry_run:
        # The write path sets the property blind — upsert needs no prior read.
        # The preview does need one: without it there is no old value to show,
        # and it also turns a mistyped UUID into an error instead of a silent
        # no-op. Two extra reads, only on this path: the block, then its
        # properties under their stored keys (see stored_properties).
        block = api.get_block(block_id, include_children=False)
        if not block:
            fail(f"Block not found: {block_id}", as_json=as_json, id=block_id)
        existing, _texts = stored_properties(api, block.get("uuid") or block_id)
        had = key in existing
        old_value = existing.get(key)
        if as_json:
            output({"block": block_id, "property": key, "old_value": old_value,
                    "value": value, "existed": had, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would set '{key}::' on block '{block_id}'")
            click.echo(f"  was: {old_value if had else '(not set)'}")
            click.echo(f"  now: {value}")
        return

    api.upsert_block_property(block_id, key, value)

    result = {"block": block_id, "property": key, "value": value, "status": "updated"}
    if as_json:
        output(result, True)
    else:
        click.echo(f"Set '{key}:: {value}' on block '{block_id}'")

@cli.command("query-pages-by-property", epilog="""\b
Examples:
  logseq-cli --token TOKEN query-pages-by-property --key "type" --value "Person"
  logseq-cli --token TOKEN query-pages-by-property --key "team"
Note:
  Without --value: lists all pages that have the key (with their values).
  With --value: matches the whole value, and also a page whose value is a
  collection containing it — Logseq stores `team:: Core` as "Core" on one
  page and ["Core"] on another, and the page does not show which.
""")
@click.option("--key", required=True, help="Property key to filter by (e.g. 'type', 'team', 'role')")
@click.option("--value", default=None, help="Property value to match (omit to find all pages with this key)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def query_pages_by_property(ctx, key, value, as_json):
    """Find pages by property key/value (e.g. --key type --value Person)."""
    api = ctx.obj["api"]

    # Property keys have two spellings for the same data: Logseq displays
    # camelCase (excludeFromGraphView), datalog stores kebab-case
    # (exclude-from-graph-view). Querying the user's spelling as-is finds
    # nothing when they typed the displayed form. Try both, so either works;
    # a foreign graph might store either. Both are whitelisted before use.
    key_forms = _property_key_spellings(key)
    key_get = " ".join(
        f"[(get ?props :{edn_keyword(k)}) ?v]" for k in key_forms
    )
    key_clause = key_get if len(key_forms) == 1 else f"(or {key_get})"
    if value:
        # Logseq stores a property value either as a scalar or as a collection,
        # and which one is not visible from the page: on one real graph `team`
        # was "Core" on two pages and ["Core"] on ten others. Equality alone
        # matched the two and silently dropped the rest. `contains?` covers the
        # collection form; neither `coll?` nor `set` is available as a predicate
        # here, so the two shapes are tried side by side rather than normalised.
        literal = edn_string(value)
        value_clause = f"(or [(= ?v {literal})] [(contains? ?v {literal})])"
        query = f'''[:find (pull ?p [:block/name :block/original-name :block/properties])
                     :where
                     [?p :block/name]
                     [?p :block/properties ?props]
                     {key_clause}
                     {value_clause}]'''
    else:
        # Query pages that have this property key (any value)
        query = f'''[:find (pull ?p [:block/name :block/original-name :block/properties])
                     :where
                     [?p :block/name]
                     [?p :block/properties ?props]
                     {key_clause}]'''

    # The former full-scan fallback is gone: it existed for a malformed key,
    # which edn_keyword now rejects before any query is built, and a silent
    # scan over ~1900 pages is no good answer even on success. A rejected key
    # is a usage error with a clear message, not a reason to fall back.
    results = api.datascript_query(query)

    # Extract page names from results
    pages_found = []
    for item in results:
        if isinstance(item, list) and len(item) > 0:
            page = item[0]
            if isinstance(page, dict):
                name = page.get("original-name") or page.get("name", "?")
                prop_value = _read_property_value(page.get("properties", {}), key)
                pages_found.append({"name": name, "value": _format_property_value(prop_value)})
        elif isinstance(item, dict):
            name = item.get("original-name") or item.get("name", "?")
            prop_value = _read_property_value(item.get("properties", {}), key)
            pages_found.append({"name": name, "value": _format_property_value(prop_value)})

    pages_found.sort(key=lambda x: x["name"].lower())

    result_data = {
        "key": key,
        "value": value,
        "count": len(pages_found),
        "pages": pages_found,
    }

    if as_json:
        output(result_data, True)
    else:
        filter_desc = f"{key}:: {value}" if value else f"{key}:: *"
        click.echo(f"Pages with {filter_desc} ({len(pages_found)}):\n")
        for p in pages_found:
            if value:
                click.echo(f"  {p['name']}")
            else:
                click.echo(f"  {p['name']} ({key}:: {p['value']})")
