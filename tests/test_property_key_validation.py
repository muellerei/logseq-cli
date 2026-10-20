"""Property keys are checked against what Logseq reads back, before any write.

``upsertBlockProperty`` stores whatever key it is handed, as ``(keyword key)``,
and writes ``key:: value`` into the file. Logseq's own parser is stricter: on the
next read of that file it lower-cases the key, turns ``_`` into ``-`` and drops
anything that is not a valid EDN keyword. Until then the database and the file
disagree about the page. A shell loop that passed ``"type Project"`` as one
argument left a page whose first line was a bullet block, with properties the
read-back reported under a camel-cased name no file contained.

The tables below are the parser's verdicts, measured by writing each key into a
file on a throwaway page and reading the parsed properties back (Logseq 0.10.15).
"""
import json
from unittest.mock import patch

import pytest

from logseq_cli.blockprops import REF_TARGET_ID_NOTE, normalize_property_key, parse_property_pairs
from logseq_cli.blocktext import PROPERTY_LINE_RE
from logseq_cli.cli import cli
from logseq_cli.safety import WriteCommand
from tests.conftest import PageGraph, page_graph_api, split_runner

# Keys the parser drops entirely: the line is no property at all on re-read.
DROPPED = [
    "type Project", "a  b", " pad ", "a\tb",
    "a:b", "k::", "k:",
    "a,b", "a;b", "a@b", "a~b", "a^b", 'a"b', "a|b", "a\\b", "a`b",
    "a[b", "a]b", "a(b", "a)b", "a{b", "a}b",
    "#tag",
    # '/' makes a namespaced keyword: "a/b" survives only as "b", the rest vanish
    "a/b", "a/", "/a", "a/b/c",
]

# Keys the parser turns into the block's id. Measured: "custom-id:: plain-text"
# on re-read made "plain-text" the block's uuid, so a write under this key
# replaces the identity every ((ref)) to the block points at. "id" itself does
# the same (measured in #51: set-block-property --key id wrote "id:: plain-text",
# and on re-read "plain-text" was the block's uuid; the command's --help example
# wrote that key); it was left out here because only the rename had been
# measured.
BECOMES_ID = ["custom-id", "custom_id", "Custom_ID", "id", "ID", "Id"]

# Undecodable argv bytes arrive as lone surrogates (PEP 383). Measured on the
# Python side: the upsert went out, then printing the confirmation raised.
UNDECODABLE = ["Caf\udce9"]

# Keys the parser keeps, but under another name.
RENAMED = [
    ("Mixed", "mixed"),
    ("a_b", "a-b"),
    ("Ab_C", "ab-c"),
    ("ÉB", "éb"),
    ("a-_b", "a--b"),
]

# Keys the parser keeps exactly as written.
KEPT = ["type", "a-b", "a.b", "a.b.c", "crème", "1abc", "a1", "a#b", "a?b",
        "a!b", "a*b", "a+b", "a'b", "a%b", "a&b", "a=b", "a<b", "a$b",
        "-ab", ".ab", "a.", "ab-"]


class TestNormalizePropertyKey:
    @pytest.mark.parametrize("key", DROPPED)
    def test_key_logseq_drops_is_refused(self, key):
        with pytest.raises(ValueError, match="Invalid property key"):
            normalize_property_key(key)

    @pytest.mark.parametrize("key,stored", RENAMED)
    def test_key_logseq_renames_is_normalised_the_same_way(self, key, stored):
        assert normalize_property_key(key) == stored

    @pytest.mark.parametrize("key", KEPT)
    def test_key_logseq_keeps_passes_unchanged(self, key):
        assert normalize_property_key(key) == key

    @pytest.mark.parametrize("key", BECOMES_ID)
    def test_key_logseq_reads_as_the_block_id_is_refused(self, key):
        with pytest.raises(ValueError, match="block's uuid") as refused:
            normalize_property_key(key)
        # The reason holds in every situation; the way depends on the caller.
        assert "((uuid))" not in str(refused.value)
        assert "--keep-ids" not in str(refused.value)

    @pytest.mark.parametrize("key", UNDECODABLE)
    def test_key_with_undecodable_bytes_is_refused(self, key):
        with pytest.raises(ValueError, match="Invalid property key"):
            normalize_property_key(key)

    def test_empty_key_is_refused(self):
        with pytest.raises(ValueError, match="Invalid property key"):
            normalize_property_key("")

    def test_message_names_the_key_and_the_reason(self):
        with pytest.raises(ValueError) as e:
            normalize_property_key("type Project")
        assert "'type Project'" in str(e.value)
        assert "whitespace" in str(e.value)


class TestParsePropertyPairs:
    def test_key_is_normalised(self):
        assert parse_property_pairs(["Status=open"]) == [("status", "open")]

    def test_key_logseq_drops_is_refused(self):
        with pytest.raises(ValueError, match="Invalid property key"):
            parse_property_pairs(["type Project="])

    def test_space_around_the_equals_sign_is_still_syntax(self):
        # "k = v" is KEY=VALUE formatting, not a key containing whitespace
        assert parse_property_pairs(["  k =v"]) == [("k", "v")]


def _api(first=""):
    """Page A, its first block ``first``, and a block ``blk`` after it."""
    return page_graph_api(PageGraph({"Page A": [first, {"content": "text", "uuid": "blk"}]}))


def _written(api):
    """The ``(key, value)`` pairs that reached Logseq: through
    upsertBlockProperty, or as a line of the property block set-property
    saves (#80)."""
    pairs = [c.args[1:] for c in api.upsert_block_property.call_args_list]
    for call in api.update_block.call_args_list:
        pairs += [(m.group(1), line[m.end():].strip()) for line in call.args[1].split("\n")
                  if (m := PROPERTY_LINE_RE.match(line))]
    return pairs


def _written_keys(api):
    return [key for key, _value in _written(api)]


def _run(args, api):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args)


# Every way a property key reaches upsertBlockProperty.
def _set_property(key, value="x"):
    return ["set-property", "--name", "Page A", "--key", key, "--value", value]


def _set_block_property(key, value="x"):
    return ["set-block-property", "--id", "blk", "--key", key, "--value", value]


def _add_note_content(key, value="x"):
    return ["add-note-content", "--page", "Page A", "--content", "body",
            "--property", f"{key}={value}"]


def _insert_block(key, value="x"):
    return ["insert-block", "--child-of", "blk", "--content", "body",
            "--property", f"{key}={value}"]


PATHS = [_set_property, _set_block_property, _add_note_content, _insert_block]


class TestEveryWritePath:
    @pytest.mark.parametrize("path", PATHS, ids=lambda p: p.__name__.strip("_"))
    @pytest.mark.parametrize("key", ["type Project", "a/b", "k::"])
    def test_refused_before_any_read_or_write(self, path, key):
        api = _api()
        r = _run(path(key), api)
        assert r.exit_code == 1, r.output
        assert "Invalid property key" in r.stderr
        assert api.method_calls == [], "a refused key must cost no API call"

    @pytest.mark.parametrize("path", PATHS, ids=lambda p: p.__name__.strip("_"))
    def test_renamed_key_is_written_as_logseq_stores_it_and_said_so(self, path):
        api = _api()
        r = _run(path("Mixed"), api)
        assert r.exit_code == 0, r.stderr
        assert _written_keys(api) == ["mixed"]
        assert "'Mixed'" in r.stderr and "'mixed'" in r.stderr

    @pytest.mark.parametrize("path", PATHS, ids=lambda p: p.__name__.strip("_"))
    def test_kept_key_passes_without_a_note(self, path):
        api = _api()
        r = _run(path("a-b"), api)
        assert r.exit_code == 0, r.stderr
        assert _written_keys(api) == ["a-b"]
        assert "stored as" not in r.stderr

    @pytest.mark.parametrize("path", PATHS, ids=lambda p: p.__name__.strip("_"))
    def test_json_refusal_is_parseable_on_stderr(self, path):
        # stdout is payload only (AGENTS.md); an error object there would be
        # parsed as data by a caller that checks the exit code afterwards
        r = _run(path("type Project") + ["--json"], _api())
        assert r.exit_code == 1
        assert r.stdout == ""
        assert "Invalid property key" in json.loads(r.stderr)["error"]

    @pytest.mark.parametrize("path", [_add_note_content, _insert_block],
                             ids=lambda p: p.__name__.strip("_"))
    def test_malformed_pair_is_refused_on_stderr_too(self, path):
        args = [a if not a.endswith("=x") else "nopair" for a in path("k")] + ["--json"]
        r = _run(args, _api())
        assert r.exit_code == 1
        assert r.stdout == ""
        assert "expected KEY=VALUE" in json.loads(r.stderr)["error"]

    def test_confirmation_names_the_stored_key(self):
        r = _run(_set_property("Mixed"), _api())
        assert "Set 'mixed:: x'" in r.stdout


class TestTheIdKeyRefusalNamesTheWay:
    """Each caller of the key check knows its situation, the check does not.
    The refusal said "would replace the uuid", which is false when the value
    is the block's own uuid, and named no way at all."""

    def test_set_block_property_names_the_ref(self):
        api = _api()
        r = _run(_set_block_property("id", "blk"), api)
        assert r.exit_code == 1, r.output
        assert "((uuid))" in r.stderr and "replace" not in r.stderr
        # Refused before the block is read, so the hint names the one block
        # that gets no id:: (setBlocksId skips a page's property block).
        assert "property block" in r.stderr
        assert api.method_calls == []

    @pytest.mark.parametrize("path", [_add_note_content, _insert_block],
                             ids=lambda p: p.__name__.strip("_"))
    def test_a_new_block_names_keep_ids(self, path):
        api = _api()
        r = _run(path("id"), api)
        assert r.exit_code == 1, r.output
        assert "--keep-ids" in r.stderr and "replace" not in r.stderr
        assert api.method_calls == []

    def test_set_property_names_no_way(self):
        # A page's property block gets no id:: line: setBlocksId skips it.
        api = _api()
        r = _run(_set_property("id"), api)
        assert r.exit_code == 1, r.output
        assert "block's uuid" in r.stderr
        assert "((uuid))" not in r.stderr and "--keep-ids" not in r.stderr


class TestRemoveProperty:
    """remove-property has to address the key set-property stored. Without the
    same rename, "set --key Status" stored "status" and "remove --key Status"
    reported success while removing nothing."""

    def test_removes_the_key_set_property_stored_from_the_page(self):
        api = _api(first="due-date:: 2026-10-01\nkind:: a")
        r = _run(["remove-property", "--name", "Page A", "--key", "Due_Date"], api)
        assert r.exit_code == 0, r.stderr
        assert api.graph.tree("Page A")[0] == ("kind:: a", [])
        assert "'Due_Date'" in r.stderr and "'due-date'" in r.stderr

    def test_removes_the_key_set_property_stored_from_a_block(self):
        api = _api()
        r = _run(["remove-property", "--id", "blk", "--key", "Due_Date"], api)
        assert r.exit_code == 0, r.stderr
        assert [c.args[1] for c in api.remove_block_property.call_args_list] == ["due-date"]
        assert "'Due_Date'" in r.stderr and "'due-date'" in r.stderr

    @pytest.mark.parametrize("key", ["type Project", "Custom_ID"])
    def test_a_key_set_property_refuses_is_removed_as_given(self, key):
        # Versions before the check stored such keys verbatim; until a
        # re-index the database may hold one, and it must stay removable.
        api = _api()
        r = _run(["remove-property", "--name", "Page A", "--key", key], api)
        assert r.exit_code == 0, r.stderr
        assert [c.args[1] for c in api.remove_block_property.call_args_list] == [key]
        assert "stored as" not in r.stderr

    @pytest.mark.parametrize("dry_run", [[], ["--dry-run"]], ids=["write", "dry-run"])
    @pytest.mark.parametrize("target", [["--id", "blk"], ["--name", "Page A"]],
                             ids=["block", "page"])
    @pytest.mark.parametrize("key", ["id", "ID", "Id", " id"])
    def test_the_id_key_is_refused_before_any_read(self, key, target, dry_run):
        # Measured (Logseq 0.10.15): removeBlockProperty(uuid, "id") empties the
        # database property but leaves the id:: line in column 0, and the next
        # property write drops it from the file. Both calls exit 0.
        api = _api()
        r = _run(["remove-property", *target, "--key", key, *dry_run], api)
        assert r.exit_code == 1, r.output
        assert "id:: line" in r.stderr and "next property write" in r.stderr
        assert api.method_calls == [], "a refused key must cost no API call"

    def test_the_id_key_refusal_is_json_on_stderr(self):
        r = _run(["remove-property", "--id", "blk", "--key", "id", "--json"], _api())
        assert r.exit_code == 1
        assert r.stdout == ""
        error = json.loads(r.stderr)
        assert "id:: line" in error["error"]
        assert error["property"] == "id"

    def test_help_names_the_id_key_as_refused(self):
        r = split_runner().invoke(cli, ["remove-property", "--help"])
        assert "except id" in r.stdout


class TestEmptyValue:
    """An empty value is kept: measured, Logseq writes ``type::`` and reads it
    back as ``""``, in the database and in the file alike. It is not what broke
    the page above; the key was. ``remove-property`` stays the way to remove."""

    @pytest.mark.parametrize("path", [_set_property, _set_block_property],
                             ids=lambda p: p.__name__.strip("_"))
    def test_empty_value_is_written(self, path):
        api = _api()
        r = _run(path("type", ""), api)
        assert r.exit_code == 0, r.stderr
        assert _written(api) == [("type", "")]


class TestAKeyKeptAsGivenIsProvenGone:
    """The removal of such a key is proven under the key as sent. Looked up
    only as Logseq's parser would store it ("type project", "custom-id"), a
    key the database holds as given was never found, and a removal Logseq
    did not do passed as done."""

    @pytest.mark.parametrize("key", ["type Project", "Custom_ID"])
    @pytest.mark.parametrize("mode,exit_code", [("noop", 1), ("execute", 0)])
    def test_removal_is_read_back_under_the_key_sent(self, monkeypatch, key, mode, exit_code):
        from tests.logseq_http_double import LogseqHttpDouble
        double = LogseqHttpDouble.installed(monkeypatch, {"Page A": ["block one"]},
                                            modes={"removeBlockProperty": mode})
        uuid = double.uuid_of("block one")
        # What the database holds: the key as an earlier version stored it,
        # until Logseq did its removal.
        held = {key: "a"}
        real = double._handlers["logseq.Editor.removeBlockProperty"]

        def remove(args):
            if mode == "execute" and args[1] == key:
                held.clear()
            return real(args)
        monkeypatch.setitem(double._handlers, "logseq.Editor.removeBlockProperty", remove)
        monkeypatch.setattr("logseq_cli.api.stored_properties",
                            lambda api, u: (dict(held), dict(held)))
        r = split_runner().invoke(cli, ["--token", "t", "remove-property", "--id", uuid,
                                        "--key", key, "--json"])
        assert r.exit_code == exit_code, r.stderr
        if exit_code:
            error = json.loads(r.stderr)
            assert (error["reason"], error["method"]) == \
                ("write_not_verified", "removeBlockProperty")


# Write commands whose write carries no text a ((uuid)) could be in. Every other
# WriteCommand can write a ref, and its --help says what happens to the target.
# remove-property is one of them: --name writes back what is left of the
# property block, and a ((uuid)) in a remaining value goes through the same
# step that gives the target its id:: line.
NO_REF_TEXT = {
    "delete-block": "removes a block, writes no text",
    "delete-page": "removes a page, writes no text",
    "move-block": "moves a block as it is",
    "remove-block": "removes a block, writes no text",
    "rename-page": "writes a page name, not block text",
}


class TestEveryRefWriterSaysWhatHappensToTheTarget:
    """A write holding a ((uuid)) gives the block it points at an id:: line
    first (#95). No --help said so, and an agent that wanted to pin a block's
    id before referencing it took two detours."""

    def test_the_help_of_each_ref_writer_carries_the_note(self):
        writers = {name for name, cmd in cli.commands.items() if isinstance(cmd, WriteCommand)}
        assert set(NO_REF_TEXT) <= writers, "an excluded command is no write command"
        note = " ".join(REF_TARGET_ID_NOTE.split())
        missing = [name for name in sorted(writers - set(NO_REF_TEXT))
                   if note not in " ".join(split_runner().invoke(cli, [name, "--help"]).stdout.split())]
        assert not missing, f"--help without REF_TARGET_ID_NOTE: {missing}"

    def test_the_note_says_nothing_unmeasured(self):
        # Whether a re-index keeps the id:: line was not measured (#95).
        assert "re-index" not in REF_TARGET_ID_NOTE.lower()
