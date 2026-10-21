"""A property write's hash vouches only for text the command read.

set-block-property and remove-property --id prove the property they wrote and
return the block's hash for the next write's --expect-hash. When someone edits
the rest of the block's text between the command's read and the proof read,
that hash would vouch for an edit nobody read, and a chained write on it would
overwrite the edit unseen. The write still lands and is proven, so the command
exits 0; it leaves ``hash`` out, and the caller reads the block again.

"The rest of the text" is the text without the lines of the written key, under
the hash's own normalisation: what the hash cannot see, a following
--expect-hash cannot see either.
"""
import json

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.blocktext import block_hash, text_beside_property
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


# --- the text beside the written property -------------------------------------

def test_the_lines_of_the_key_are_left_out_in_any_spelling():
    assert text_beside_property("text\nstatus:: done", "status") == "text"
    assert text_beside_property("text\nSome_Key:: 1", "some-key") == "text"
    assert text_beside_property("text\nprio-level:: 2", "Prio_Level") == "text"


def test_other_properties_stay():
    assert text_beside_property("text\nowner:: alice\nstatus:: done", "status") == \
        text_beside_property("text\nowner:: alice", "status")
    assert text_beside_property("text\nowner:: alice", "status") != \
        text_beside_property("text", "status")


def test_where_the_line_sits_does_not_count():
    # Logseq puts the line before text that stands under the properties
    # (measured, 0.10.15).
    assert text_beside_property("a:: 1\nstatus:: done\n\nmore", "status") == \
        text_beside_property("a:: 1\n\nmore\nstatus:: done", "status")


def test_what_the_hash_does_not_see_does_not_count():
    plain = text_beside_property("text\nstatus:: done", "status")
    assert text_beside_property("text\ncollapsed:: true\nstatus:: done", "status") == plain
    assert text_beside_property(
        "text\nid:: 6650a1b2-0000-4000-8000-000000000001\nstatus:: done", "status") == plain
    assert text_beside_property("text  \nstatus:: done", "status") == plain


def test_a_block_of_only_the_key_leaves_nothing():
    assert text_beside_property("status:: done", "status") == ""
    assert text_beside_property("", "status") == ""


def test_an_edit_to_the_text_counts():
    assert text_beside_property("text\nstatus:: done", "status") != \
        text_beside_property("edited\nstatus:: done", "status")


# --- set-block-property ----------------------------------------------------------

BLOCK = "00000000-0000-4000-8000-0000000000b1"
TEXT = "ship the parser"
SET = ["set-block-property", "--id", BLOCK, "--key", "prio", "--value", "high"]


def invoke(*args):
    return split_runner().invoke(cli, ["--token", "t", *args])


def run(*args):
    """The command with --json: exit 0, the parsed output."""
    result = invoke(*args, "--json")
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def graph(monkeypatch, content=TEXT):
    return LogseqHttpDouble.installed(monkeypatch, {"Probe Page": [{"content": content,
                                                                    "uuid": BLOCK}]})


def expecting(content=TEXT):
    return ["--expect-hash", block_hash(content)]


def edit_text(double):
    """Someone edits the block's text in Logseq, leaving its properties be."""
    return lambda: double.set_content(
        BLOCK, double.content_of(BLOCK).replace(TEXT, "edited meanwhile"))


def hash_now():
    return run("get-block", "--id", BLOCK, "--no-children")["hash"]


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("expect", [False, True])
def test_set_block_property_leaves_out_the_hash_when_the_text_changed(monkeypatch, phase, expect):
    double = graph(monkeypatch)
    double.on_next_write(edit_text(double), phase=phase)
    out = run(*SET, *(expecting() if expect else []))
    assert out == {"block": BLOCK, "property": "prio", "value": "high", "status": "updated"}
    # The write landed, beside the edit.
    assert double.content_of(BLOCK) == "edited meanwhile\nprio:: high"


def test_set_block_property_keeps_the_hash_when_only_its_own_line_changed(monkeypatch):
    # The key's spelling, at the same value: the only edit that is separable.
    # Before the write, the write overwrites it; another value fails the proof.
    double = graph(monkeypatch)
    double.on_next_write(lambda: double.set_content(BLOCK, f"{TEXT}\nPrio:: high"),
                         phase="after")
    out = run(*SET)
    assert out["hash"] == block_hash(f"{TEXT}\nPrio:: high")
    assert out["hash"] == hash_now()


def test_set_block_property_keeps_the_hash_without_an_edit(monkeypatch):
    graph(monkeypatch)
    out = run(*SET, *expecting())
    assert out["hash"] == block_hash(f"{TEXT}\nprio:: high")
    assert out["hash"] == hash_now()


def test_a_chain_reads_again_when_the_hash_is_missing(monkeypatch):
    # The chain from AGENTS.md: `.hash // empty` is empty, so the script
    # reads the block, and that hash holds the edit.
    double = graph(monkeypatch)
    double.on_next_write(edit_text(double), phase="after")
    out = run(*SET)
    chained = out.get("hash") or ""
    assert chained == ""
    reread = hash_now()
    assert reread == block_hash("edited meanwhile\nprio:: high")


@pytest.mark.parametrize("switch, content", [
    ("strip_collapsed_on_upsert", f"{TEXT}\ncollapsed:: true"),
    ("trim_line_ends_on_upsert", f"{TEXT}  "),
])
def test_logseqs_own_normalisation_keeps_the_hash(monkeypatch, switch, content):
    double = graph(monkeypatch, content)
    setattr(double, switch, True)
    out = run(*SET, *expecting(content))
    assert out["hash"] == hash_now()


def test_a_block_of_only_properties_keeps_the_hash(monkeypatch):
    graph(monkeypatch, "owner:: alice")
    out = run(*SET)
    assert out["hash"] == hash_now()


def test_upsert_block_property_takes_expect_with_no_default(monkeypatch):
    double = graph(monkeypatch)
    api = LogseqAPI(token="t")
    with pytest.raises(TypeError):
        api.upsert_block_property(BLOCK, "prio", "high")
    assert api.upsert_block_property(BLOCK, "prio", "high", expect=None) is None
    assert double.content_of(BLOCK) == f"{TEXT}\nprio:: high"
    assert api.upsert_block_property(BLOCK, "prio", "low", expect=f"{TEXT}\nprio:: high") == \
        block_hash(f"{TEXT}\nprio:: low")


def test_expect_none_binds_to_nothing_not_to_the_empty_text(monkeypatch):
    # The text beside the key is "" on both sides here; None must still
    # mean "read nothing", not "read an empty text".
    graph(monkeypatch, "prio:: low")
    api = LogseqAPI(token="t")
    assert api.upsert_block_property(BLOCK, "prio", "high", expect=None) is None
    assert api.upsert_block_property(BLOCK, "prio", "low", expect="prio:: high") == \
        block_hash("prio:: low")


def test_with_expect_hash_the_binding_is_the_read_the_check_passed(monkeypatch):
    # Someone edits the block between the command's first read and the read
    # its --expect-hash is checked on; the caller had read the edit. The
    # write is bound to the checked read, so the hash stays.
    double = graph(monkeypatch)
    read = double._handlers["logseq.Editor.getBlock"]
    reads = []

    def edited_after_the_first_read(args):
        block = read(args)
        reads.append(1)
        if len(reads) == 1:
            double.set_content(BLOCK, "edited before the check")
        return block

    double._handlers["logseq.Editor.getBlock"] = edited_after_the_first_read
    out = run(*SET, *expecting("edited before the check"))
    assert out["hash"] == block_hash("edited before the check\nprio:: high")


# --- no command loses the binding -------------------------------------------------

# Each command that prints a property write's hash, the API method behind it,
# and the arguments of one run on ``graph``. A command that passes expect=None
# where its read has text would print no hash ever and pass every test above
# that expects none; this pins that each binds to what it read.
BOUND = {
    "set-block-property": ("upsert_block_property", SET),
}


@pytest.mark.parametrize("name", sorted(BOUND))
@pytest.mark.parametrize("expect_hash", [False, True])
def test_the_command_binds_the_write_to_its_read(monkeypatch, name, expect_hash):
    method, args = BOUND[name]
    graph(monkeypatch, f"{TEXT}\nprio:: low")
    seen = []
    original = getattr(LogseqAPI, method)

    def spy(self, *a, expect, **kw):
        seen.append(expect)
        return original(self, *a, expect=expect, **kw)

    monkeypatch.setattr(LogseqAPI, method, spy)
    run(*args, *(expecting(f"{TEXT}\nprio:: low") if expect_hash else []))
    assert seen == [f"{TEXT}\nprio:: low"]
