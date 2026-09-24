"""Block properties: the key Logseq keeps, the type a value reads back as.

Parses ``KEY=VALUE`` pairs, spells a key the way Logseq will read it back, and
says so on stderr when that differs from what was given. stored_properties and
kept_properties read what the database holds, so that a write replacing a
block's text keeps the properties it did not mean to touch;
apply_block_properties writes them.
"""

import re
import uuid as uuid_module

import click

from logseq_cli.blocktext import (
    PROPERTY_KEY_STOP,
    PROPERTY_LINE_RE,
    refuse_split_property,
    stored_property_key,
)


# Why a property value is sent as a number only in one narrow case
# ----------------------------------------------------------------
# ``upsertBlockProperty`` stores what it is sent and writes it into the file:
# a string verbatim, a number the way JavaScript prints it. So every value sent
# as a number is rewritten in the file unless printing it gives back the typed
# text. Python's ``int``/``float`` accept far more than that (``01234``,
# ``1.50``, ``1e3``, ``1_0``, non-ASCII digits, ``nan``), and an integer above
# 2^53-1 loses digits as a JavaScript number.
#
# When Logseq reads a file it makes a number only from ASCII digits up to
# 2^53-1, ignoring surrounding whitespace, and keeps the text beside it;
# ``1.50``, ``-7`` and ``1e3`` stay text (measured, 0.10.15). A number is
# therefore sent only where the parser would make one AND it prints back as
# typed. A leading zero is sent as text: the
# file keeps it, and the database holds the text until the file is next read,
# the lesser of the two disagreements. See #35.
_MAX_EXACT_INTEGER = 2**53 - 1


def coerce_property_value(value: str):
    """``value`` as an int where Logseq reads one that prints back as typed.

    Anything else comes back unchanged, ``01234`` included: Logseq reads that
    as 1234, but sent as a number it would lose its zero in the file.

    Single source of truth for property-value typing (shared by set-property,
    set-block-property and the inline --property option). See the note above
    for why the rule is this narrow.
    """
    if not isinstance(value, str):
        return value
    digits = value.strip()  # the parser trims the value before reading it
    # The length bound keeps int() away from Python's digit limit (4300), which
    # raises instead of converting; 2^53-1 has 16 digits.
    if (digits.isascii() and digits.isdigit() and len(digits) <= 16
            and (digits == "0" or not digits.startswith("0"))
            and int(digits) <= _MAX_EXACT_INTEGER):
        return int(digits)
    return value


# Why property keys are checked here and not left to Logseq
# ----------------------------------------------------------
# ``upsertBlockProperty`` stores any key it is handed as ``(keyword key)`` and
# writes ``key:: value`` into the file. The parser that reads the file back is
# stricter (``extract-properties`` in graph-parser/block.cljs): it lower-cases
# the key, reads ``_`` as ``-``, and drops the line unless the result is a valid
# EDN keyword. So a key the parser would change or drop leaves the database and
# the file disagreeing until the next re-index, and the write still reports
# success. Measured per key against Logseq 0.10.15; the verdicts are pinned in
# tests/test_property_key_validation.py.
#
# The two renames are applied here, so the database gets the key the file will
# be read back as. Everything the parser drops is refused. '/' is refused too:
# it makes a namespaced keyword, and "a/b" survives only as "b". So is "id",
# and the parser's third rename, "custom-id" to "id": measured, both make the
# value the block's uuid on re-read, even when the value is no uuid at all.
# Only the rename had been measured at first, so "id" itself went through
# until #51, with the set-block-property --help example writing it.
_PROPERTY_KEY_FORBIDDEN = re.compile(rf'[/{PROPERTY_KEY_STOP}]')
_PROPERTY_KEYS_READ_AS_ID = {"id", "custom-id"}


def normalize_property_key(key: str) -> str:
    """The key Logseq will read back, or ValueError if it reads back none.

    Lower-cases and turns ``_`` into ``-``, as Logseq's parser does. Refuses
    what the parser drops (whitespace, a leading ``#``, the characters in
    :data:`_PROPERTY_KEY_FORBIDDEN`), what it reads as the block's id, and
    bytes that were not valid UTF-8.
    """
    def refuse(reason, consequence="Logseq would not read it back as a property"):
        return ValueError(f"Invalid property key {key!r}: {reason}. {consequence}.")

    if not key:
        raise refuse("empty")
    if any(c.isspace() for c in key):
        raise refuse("contains whitespace")
    if key.startswith("#"):
        raise refuse("starts with '#'")
    bad = _PROPERTY_KEY_FORBIDDEN.search(key)
    if bad:
        raise refuse(f"contains {bad.group()!r}")
    # Bytes that were not valid UTF-8 on the command line, carried as lone
    # surrogates (PEP 383). They cannot be written to the file as given.
    if any("\ud800" <= c <= "\udfff" for c in key):
        raise refuse("contains bytes that are not valid UTF-8")
    canonical = stored_property_key(key)
    if canonical in _PROPERTY_KEYS_READ_AS_ID:
        raise refuse("Logseq reads it as the block's id",
                     "Writing it would replace the uuid that ((refs)) to the block point at")
    return canonical


def note_renamed_property_key(key: str, stored: str) -> None:
    """Say on stderr when the key written differs from the key given."""
    if key != stored:
        click.echo(
            f"Note: property key {key!r} is stored as {stored!r} "
            "(Logseq lower-cases keys and reads '_' as '-').",
            err=True,
        )


def _split_property_pair(raw: str):
    if "=" not in raw:
        raise ValueError(f"Invalid --property '{raw}', expected KEY=VALUE")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"Invalid --property '{raw}', empty key")
    return key, value


def parse_property_pairs(pairs) -> list:
    """Parse ('key=value', ...) strings into [(key, coerced_value), ...].

    Splits on the FIRST '=' only, so values may contain '=', commas and spaces
    (e.g. ``tags=mcp, agents``). Keys come back as :func:`normalize_property_key`
    returns them. Raises ValueError on a missing '=', an empty key, or a key
    Logseq would not read back.
    """
    out = []
    for raw in pairs:
        key, value = _split_property_pair(raw)
        out.append((normalize_property_key(key), coerce_property_value(value)))
    return out


def check_property_pairs(pairs) -> list:
    """:func:`parse_property_pairs` for a command's up-front validation.

    Same result, and additionally names every renamed key on stderr and refuses
    a value Logseq would split into blocks (#47). Called once per command,
    before anything is read or written, so each note appears once however often
    the pairs are parsed later.
    """
    parsed = parse_property_pairs(pairs)
    for raw, (stored, value) in zip(pairs, parsed):
        note_renamed_property_key(_split_property_pair(raw)[0], stored)
        refuse_split_property(stored, value)
    return parsed


def stored_properties(api, uuid: str) -> tuple:
    """Properties of the block or page ``uuid`` as the database holds them.

    Returns ``(values, texts)``: the parsed values and the original text of each
    property, both keyed as stored (``due-date``), or two empty dicts when the
    entity has none.

    Read with a datascript pull, not from ``getBlock``/``getPage``: the plugin
    API camel-cases property keys on the way out (``normalize-keyword-for-json``
    in sdk/utils.cljs), so ``due-date`` and ``created_at`` arrive as ``dueDate``
    and ``createdAt``, and the stored spelling cannot be recovered from that.
    Anything that compares or writes back a key needs this form. Measured
    against Logseq 0.10.15; the cases are pinned in
    tests/test_stored_property_keys.py.
    """
    # Validated before it is spliced into the query text: a uuid is the only
    # thing this function puts there, so nothing else can reach it.
    uuid_literal = str(uuid_module.UUID(str(uuid)))
    rows = api.datascript_query(
        "[:find (pull ?b [:block/properties :block/properties-text-values]) "
        f':where [?b :block/uuid #uuid "{uuid_literal}"]]'
    ) or []
    entity = rows[0][0] if rows and rows[0] else None
    if not isinstance(entity, dict):
        return {}, {}
    return entity.get("properties") or {}, entity.get("properties-text-values") or {}


def kept_properties(api, uuid: str, content: str) -> tuple:
    """The properties a replacement of block ``uuid``'s text with ``content``
    passes back, as ``(values, texts)`` like :func:`stored_properties`.

    Properties are lines of the text, so replacing it drops them unless their
    text goes back through ``updateBlock``'s ``opts.properties`` (#30). Only
    what has a text goes back: a markdown heading ("## Title") shows up as
    heading 2 among the values but comes from the "##", not from a line.

    A key ``content`` sets itself is left out: Logseq lets the passed value
    win and drops the caller's line (measured, 0.10.15, #66); with ``_`` for
    ``-`` both lines stay and the passed one, written last, wins. Keys compare
    as Logseq stores them. Every line counts, one in a code block too:
    ``updateBlock`` takes such a line out of the code block as a property
    (measured, #68), unlike a file read, so property_line_mask is not the rule.

    ``id`` and ``custom-id`` always go back. The block's uuid is not the
    text's to set (#56), and a foreign ``id::`` line in a code block, which
    the id rules read as code, would otherwise leave the block and become its
    uuid (#68).
    """
    values, texts = stored_properties(api, uuid)
    own = {stored_property_key(m.group(1))
           for m in map(PROPERTY_LINE_RE.match, content.split("\n")) if m}
    own -= _PROPERTY_KEYS_READ_AS_ID
    texts = {k: v for k, v in texts.items() if k not in own}
    return {k: v for k, v in values.items() if k in texts}, texts


def apply_block_properties(api, block_uuid: str, pairs) -> dict:
    """Upsert parsed KEY=VALUE pairs onto a block. Returns the applied {key: value}."""
    applied = {}
    for key, value in parse_property_pairs(pairs):
        api.upsert_block_property(block_uuid, key, value)
        applied[key] = value
    return applied
