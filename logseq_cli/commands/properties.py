import re

import click

from logseq_cli.group import cli
from logseq_cli.datalog import edn_keyword, edn_string
from logseq_cli.helpers import (
    coerce_property_value,
    normalize_property_key,
    note_renamed_property_key,
)
from logseq_cli.output import fail, handle_connection_error, output


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

    The API returns camelCase keys (excludeFromGraphView), datalog and habit
    spell them kebab-cased; a plain .lower() matches neither. Compare with
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
    page_data = api.get_page(page)

    if not page_data:
        fail(f"Page '{page}' not found.", as_json=as_json, page=page)

    properties = page_data.get("properties") or {}
    text_values = page_data.get("propertiesTextValues") or {}
    page_name = page_data.get("originalName") or page_data.get("name", page)

    # Logseq does not always expose page properties on the page object itself:
    # for pages written via set-property they live on the first block instead
    # (the property block). Without this fallback the command reported
    # "No properties" for pages whose properties were perfectly intact on disk,
    # which is what made set-property look like it had silently failed.
    if not properties:
        try:
            blocks = api.get_page_blocks_tree(page) or []
        except Exception:
            blocks = []
        if blocks:
            first = blocks[0] or {}
            block_props = first.get("properties") or {}
            if block_props:
                properties = block_props
                text_values = first.get("propertiesTextValues") or text_values

    if prop_name:
        stored_key = _find_stored_property_key(properties, prop_name)
        if stored_key is None:
            fail(f"Property '{prop_name}' not found on '{page_name}'.",
                 as_json=as_json, page=page_name, property=prop_name)
        value = properties.get(stored_key)
        text_key = _find_stored_property_key(text_values, prop_name)
        text_value = text_values.get(text_key) if text_key else None

        if as_json:
            output({"page": page_name, "property": stored_key, "value": value, "text": text_value}, True)
        else:
            click.echo(text_value or value)
    else:
        if as_json:
            output({"page": page_name, "properties": properties, "text_values": text_values}, True)
        else:
            if not properties:
                click.echo(f"No properties on '{page_name}'.")
            else:
                click.echo(f"Properties of '{page_name}':\n")
                for key in sorted(properties.keys()):
                    display = text_values.get(key, properties[key])
                    click.echo(f"  {key}:: {display}")

@cli.command("set-property", epilog="""\b
Examples:
  logseq-cli --token TOKEN set-property --name "Alice" --key "team" --value "[[Platform]]"
  logseq-cli --token TOKEN set-property --name "X" --key "type" --value "Person"
Note:
  Properties land at page-top (above first block). NEVER use update-block for
  properties — that creates a text-block, not a real property.
  Verify with: get-properties --name X
  Keys are stored the way Logseq reads them back: lower-case, '_' as '-'
  ("Status" becomes "status", said on stderr). A key Logseq would drop is
  refused before anything is read: whitespace, a leading '#', or any of
  : , ; / \\ [ ] ( ) { } | ^ " @ ~ `
  "custom-id" is refused as well: Logseq reads it as the block's uuid.
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--key", required=True, help="Property key (e.g. 'type', 'team', 'role')")
@click.option("--value", required=True, help="Property value")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the property change, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_property(ctx, page, key, value, dry_run, as_json):
    """Set or update a property on a page's first block."""
    api = ctx.obj["api"]

    # Before the first read: a key Logseq cannot read back must cost nothing.
    try:
        stored = normalize_property_key(key)
    except ValueError as e:
        fail(str(e), as_json=as_json, page=page, property=key)
    note_renamed_property_key(key, stored)
    key = stored

    # Get page blocks to find the first block (properties block)
    blocks = api.get_page_blocks_tree(page)
    if not blocks:
        fail(f"Page '{page}' not found or has no blocks", as_json=as_json, page=page)

    first_block = blocks[0]
    block_uuid = first_block.get("uuid")
    if not block_uuid:
        fail("Could not find block UUID", as_json=as_json, page=page)

    # Auto-detect value type (shared with set-block-property / --property)
    value = coerce_property_value(value)

    if dry_run:
        # Whether this creates or overwrites is the fact worth previewing: the
        # command is called "set" either way, and an unnoticed overwrite loses
        # the old value with no trace. It is read off the block already fetched.
        existing = first_block.get("properties") or {}
        had = key in existing
        old_value = existing.get(key)
        if as_json:
            output({"page": page, "property": key, "old_value": old_value,
                    "value": value, "existed": had, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would set '{key}::' on page '{page}'")
            if had:
                click.echo(f"  was: {old_value}")
            else:
                click.echo(f"  was: (not set)")
            click.echo(f"  now: {value}")
        return

    api.upsert_block_property(str(block_uuid), key, value)

    result = {"page": page, "property": key, "value": value, "status": "updated"}
    if as_json:
        output(result, True)
    else:
        click.echo(f"Set '{key}:: {value}' on page '{page}'")

@cli.command("remove-property", epilog="""\b
Examples:
  logseq-cli --token TOKEN remove-property --name "X" --key "deprecated_key"
  logseq-cli --token TOKEN remove-property --id UUID --key "prio"
Note:
  --name removes a PAGE property (stored on the page's first block).
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
        stored = normalize_property_key(key)
    except ValueError:
        stored = key
    note_renamed_property_key(key, stored)
    key = stored

    if block_id:
        # A page property is just a property on the page's first block, so the
        # API call is the same; only the way the block is found differs.
        block_uuid = block_id.strip().replace("((", "").replace("))", "")
        block = api.get_block(block_uuid, include_children=False)
        if not block:
            fail(f"Block not found: {block_uuid}", as_json=as_json, id=block_uuid)
        existing = (block.get("properties") if isinstance(block, dict) else None) or {}
        target = f"block '{block_uuid}'"
        result = {"id": block_uuid, "property": key, "status": "removed"}
    else:
        blocks = api.get_page_blocks_tree(page)
        if not blocks:
            fail(f"Page '{page}' not found or has no blocks", as_json=as_json, page=page)
        block_uuid = blocks[0].get("uuid")
        if not block_uuid:
            fail("Could not find block UUID", as_json=as_json, page=page)
        existing = blocks[0].get("properties") or {}
        target = f"page '{page}'"
        result = {"page": page, "property": key, "status": "removed"}

    if dry_run:
        # "Property not there" is the outcome worth knowing before the write:
        # the real call succeeds silently either way, so a caller who misspelled
        # the key would otherwise see "Removed" and believe it.
        present = key in existing
        if as_json:
            output({**result, "status": "would_remove" if present else "not_present",
                    "value": existing.get(key), "present": present,
                    "dry_run": True}, True)
        elif present:
            click.echo(f"[DRY RUN] Would remove '{key}' from {target}")
            click.echo(f"  value: {existing[key]}")
        else:
            click.echo(f"[DRY RUN] '{key}' is not set on {target}; nothing would be removed")
        return

    api.remove_block_property(str(block_uuid), key)

    if as_json:
        output(result, True)
    else:
        click.echo(f"Removed '{key}' from {target}")

@cli.command("set-block-property", epilog="""\b
Example:
  logseq-cli --token TOKEN set-block-property --id UUID --key "id" --value "abc-123"
Note:
  Keys follow the same rule as set-property: stored lower-case with '_' as
  '-', and refused if Logseq would not read them back as a property.
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

    # Auto-detect value type (shared coercion with the inline --property option)
    value = coerce_property_value(value)

    if dry_run:
        # The write path sets the property blind — upsert needs no prior read.
        # The preview does need one: without it there is no old value to show,
        # and it also turns a mistyped UUID into an error instead of a silent
        # no-op. One extra read, only on this path.
        block = api.get_block(block_id, include_children=False)
        if not block:
            fail(f"Block not found: {block_id}", as_json=as_json, id=block_id)
        existing = (block.get("properties") if isinstance(block, dict) else None) or {}
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
