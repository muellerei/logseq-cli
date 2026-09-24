"""The id:: contract holds for every command that writes an outline, not only insert-block --tree.

#1 gave ``insert-block --tree`` its contract: an ``id::`` in the input is kept
with ``--keep-ids`` and announced without it, never dropped in silence. The
other writers were left out. ``add-note-content``, ``add-journal-block``,
``add-journal-content`` and ``insert-block --content`` wrote such a block under
a fresh uuid, and every ``((ref))`` to it died. In real use it happened while
rebuilding a page: the file even kept the old ``id::`` line under a block the
database knew by a new uuid.

Two assumptions went with the old code and both were measured wrong against
Logseq 0.10.15:

- "appendBlockInPage takes no options". It passes them to insertBlock, and a
  ``customUUID`` keeps the id for a top-level block, on an empty page and on
  one with blocks alike. So top-level blocks can keep their ids too.
- keeping ids is safe as long as they are well-formed. An id that already
  belongs to a block is the copy case: insertBlock throws "Custom block UUID
  already exists" midway, after earlier blocks were written, and
  insertBatchBlock does not even check an id given as an ``id::`` line. Measured,
  it wrote the copy over the original in the database and left the page's block
  list empty. It is refused before the first write instead.

A third case turned up while measuring the rebuild: a ``((ref))`` to an id with
no block makes Logseq keep a placeholder entity under that uuid, without a page.
It is not a block, so refusing it as "already exists" would be wrong; but
insertBlock refuses to give a new block its uuid all the same. It was refused
up front until #31, which sends every --keep-ids write through the one call
that takes a placeholder over; tests/test_keep_ids_placeholders.py has that.
Since then the writes here go out as insertBatchBlock with keepUUID, proven by
reading the page back, so they run against ``PageGraph`` rather than a mock
that answers every read.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner

ID = "6d0f1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
ANCHOR = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"

TOP = f"root with id\nid:: {ID}"          # one top-level block carrying the id
NESTED = f"- parent\n\t- child\n\t  id:: {ID}"


def _api(*, existing_ids=()):
    api = MagicMock()
    api.get_page.return_value = {"name": "page a", "originalName": "Page A"}
    api.get_page_blocks_tree.return_value = [{"uuid": ANCHOR, "content": "## Log", "children": []}]
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    api.append_block_in_page.side_effect = lambda page, content, options=None: {
        "uuid": (options or {}).get("customUUID") or "00000000-0000-4000-8000-000000000001"}
    api.insert_block.side_effect = lambda parent, content, options=None: {
        "uuid": (options or {}).get("customUUID") or "00000000-0000-4000-8000-000000000002"}
    api.get_block.return_value = {"uuid": ANCHOR, "content": "anchor", "children": []}

    def query(q):
        if "contains?" in q:  # the existence check for ids about to be kept
            return [[i] for i in existing_ids if i in q]
        return []
    api.datascript_query.side_effect = query
    return api


def _run(args, api):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args)


def _ids_asked_for(api):
    """Every uuid a write asked Logseq to keep, whichever call carried it:
    ``customUUID`` on insertBlock/appendBlockInPage, or ``keepUUID`` on a
    batch, which keeps the ``id::`` lines of the nodes it carries."""
    from logseq_cli.ids import collect_block_ids
    asked = []
    for call in api.append_block_in_page.call_args_list + api.insert_block.call_args_list:
        opts = call.kwargs.get("options") or (call.args[2] if len(call.args) > 2 else None) or {}
        if opts.get("customUUID"):
            asked.append(opts["customUUID"])
    for call in api.insert_batch_block.call_args_list:
        opts = call.args[2] if len(call.args) > 2 else call.kwargs.get("options") or {}
        if (opts or {}).get("keepUUID"):
            asked.extend(collect_block_ids(call.args[1]))
    return asked


def _fake():
    """A graph that answers as Logseq does, for writes proven by reading back:
    Page A holds the anchor (a ``## Log`` heading with one entry)."""
    from tests.conftest import PageGraph, page_graph_api
    return page_graph_api(PageGraph({"Page A": [
        {"uuid": ANCHOR, "content": "## Log", "children": [{"content": "entry"}]}]}))


def _kept(api):
    """Whether a block on a page now carries ID."""
    return api.graph.locate(ID) is not None


# One top-level block with an id, through every command that writes content.
# Top-level is the case the old code declared impossible.
TOP_LEVEL_WRITES = {
    "add-note-content": ["add-note-content", "--page", "Page A", "--content", TOP],
    "add-journal-block": ["add-journal-block", "--top-level", "--date", "2026-01-05",
                          "--content", TOP],
    "add-journal-block-batch": ["add-journal-block", "--top-level", "--date", "2026-01-05",
                                "--content", TOP, "--content", "a second entry"],
    "add-journal-block-file": ["add-journal-block", "--top-level", "--date", "2026-01-05",
                               "--content-file", "-"],
    "add-journal-content": ["add-journal-content", "--top-level", "--date", "2026-01-05",
                            "--content", TOP],
    "insert-block-content": ["insert-block", "--page", "Page A", "--content", TOP],
    "insert-block-top-level": ["insert-block", "--page", "Page A", "--top-level",
                               "--tree", json.dumps([{"content": TOP}])],
}


def _invoke(name, extra, api):
    args = TOP_LEVEL_WRITES[name] + extra
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args, input=f"- {TOP}\n" if "-file" in name else None)


@pytest.mark.parametrize("name", TOP_LEVEL_WRITES)
class TestEveryWriter:
    def test_without_keep_ids_the_drop_is_announced(self, name):
        api = _api()
        r = _invoke(name, [], api)
        assert r.exit_code == 0, r.stderr
        assert "id:: propert" in r.stderr and "--keep-ids" in r.stderr
        assert _ids_asked_for(api) == []

    def test_with_keep_ids_the_id_is_kept_even_at_top_level(self, name):
        api = _fake()
        r = _invoke(name, ["--keep-ids"], api)
        assert r.exit_code == 0, r.stderr
        assert _ids_asked_for(api) == [ID]
        page, _, _, parent = api.graph.locate(ID)
        assert parent is None
        assert "cannot preserve" not in r.stderr

    def test_an_id_that_already_exists_is_refused_before_any_write(self, name):
        api = _api(existing_ids=[ID])
        api.get_page.return_value = None  # a page that would have to be created
        r = _invoke(name, ["--keep-ids"], api)
        assert r.exit_code == 1
        assert ID in r.stderr and "already" in r.stderr
        api.create_page.assert_not_called()
        api.append_block_in_page.assert_not_called()
        api.insert_block.assert_not_called()
        api.insert_batch_block.assert_not_called()

    def test_a_malformed_id_is_refused_before_any_write(self, name):
        api = _api()
        args = [a.replace(ID, "NOT-A-UUID") for a in TOP_LEVEL_WRITES[name]] + ["--keep-ids"]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = split_runner().invoke(cli, args, input="- root\n  id:: NOT-A-UUID\n"
                                      if "-file" in name else None)
        assert r.exit_code == 1
        assert "NOT-A-UUID" in r.stderr
        api.append_block_in_page.assert_not_called()
        api.insert_block.assert_not_called()


class TestNestedBlocks:
    """A nested block goes through insertBlock (one block) or insertBatchBlock."""

    def test_add_note_content_under_heading_keeps_a_nested_id(self):
        api = _fake()
        r = _run(["add-note-content", "--page", "Page A", "--under-heading", "## Log",
                  "--content", NESTED, "--keep-ids"], api)
        assert r.exit_code == 0, r.stderr
        _, _, _, parent = api.graph.locate(ID)
        assert parent["content"] == "parent"

    def test_upsert_heading_refuses_keep_ids(self):
        # --upsert-heading rewrites an existing block, whose uuid cannot change;
        # the flag would half work, so the combination is refused.
        r = _run(["add-journal-block", "--under-heading", "## Log", "--upsert-heading",
                  "Status", "--content", TOP, "--keep-ids"], _api())
        assert r.exit_code != 0
        assert "--upsert-heading" in r.output + r.stderr


# ---------------------------------------------------------------------------
# Every write path, not only the top-level one. Each case sends the id down a
# different branch of the command; removing keep_ids from that branch alone
# must turn its case red.
# ---------------------------------------------------------------------------
UNDER = ["--under-heading", "## Log"]
DATE = ["--date", "2026-01-05"]
ONE_CHILD = f"root\n\t- child\n\t  id:: {ID}"   # the id sits on the only child

PATH_CASES = {
    "note-content, nested, no heading": ["add-note-content", "--page", "Page A", "--content", NESTED],
    "journal-block, flat under heading": ["add-journal-block", *UNDER, *DATE, "--content", TOP],
    "journal-block, batch under heading": ["add-journal-block", *UNDER, *DATE,
                                           "--content", TOP, "--content", "second"],
    "journal-block, batch tree at top": ["add-journal-block", "--top-level", *DATE,
                                         "--content", ONE_CHILD, "--content", "second"],
    "journal-content, under heading": ["add-journal-content", *UNDER, *DATE, "--content", TOP],
    "insert-block, content after": ["insert-block", "--after", ANCHOR, "--content", TOP],
    "insert-block, content before": ["insert-block", "--before", ANCHOR, "--content", TOP],
    "insert-block, content child-of": ["insert-block", "--child-of", ANCHOR, "--content", TOP],
    "insert-block, content first child": ["insert-block", "--child-of", ANCHOR, "--first",
                                          "--content", TOP],
    "insert-block, nested content after": ["insert-block", "--after", ANCHOR, "--content", ONE_CHILD],
    "insert-block, tree top-level child": ["insert-block", "--page", "Page A", "--top-level",
                                           "--tree", ONE_CHILD],
}

HEADING_MISSING_CASES = {
    "journal-block, flat, heading not found": ["add-journal-block", *UNDER, *DATE, "--content", TOP],
    "journal-block, nested, heading not found": ["add-journal-block", *UNDER, *DATE,
                                                 "--content", ONE_CHILD],
    "journal-content, heading not found": ["add-journal-content", *UNDER, *DATE, "--content", TOP],
}


TWO_ROOTS = f"- first\n- second\n  id:: {ID}"   # the id sits on a later root

PATH_CASES.update({
    "insert-block, content on page": ["insert-block", "--page", "Page A", "--content", NESTED],
    "insert-block, nested content before": ["insert-block", "--before", ANCHOR, "--content", ONE_CHILD],
    "insert-block, nested content child-of": ["insert-block", "--child-of", ANCHOR,
                                              "--content", ONE_CHILD],
    "insert-block, nested content first child": ["insert-block", "--child-of", ANCHOR, "--first",
                                                 "--content", ONE_CHILD],
    "insert-block, tree first child, root": ["insert-block", "--child-of", ANCHOR, "--first",
                                             "--tree", TOP],
    "insert-block, tree first child, later root": ["insert-block", "--child-of", ANCHOR,
                                                   "--first", "--tree", TWO_ROOTS],
    "insert-block, tree after": ["insert-block", "--after", ANCHOR, "--tree", ONE_CHILD],
    "insert-block, tree before": ["insert-block", "--before", ANCHOR, "--tree", ONE_CHILD],
    "journal-block, nested under heading": ["add-journal-block", *UNDER, *DATE, "--content", ONE_CHILD],
    "journal-block, batch tree under heading": ["add-journal-block", *UNDER, *DATE,
                                                "--content", ONE_CHILD, "--content", "second"],
})


@pytest.mark.parametrize("args", PATH_CASES.values(), ids=PATH_CASES.keys())
def test_every_path_passes_the_id_on(args):
    api = _fake()
    r = _run(args + ["--keep-ids"], api)
    assert r.exit_code == 0, r.stderr
    assert _ids_asked_for(api) == [ID]
    assert _kept(api)


@pytest.mark.parametrize("args", HEADING_MISSING_CASES.values(), ids=HEADING_MISSING_CASES.keys())
def test_the_heading_fallback_passes_the_id_on(args):
    api = _fake()
    with patch("logseq_cli.commands.journal.find_or_create_heading", return_value=None):
        r = _run(args + ["--keep-ids"], api)
    assert r.exit_code == 0, r.stderr
    assert _ids_asked_for(api) == [ID]
    assert _kept(api)


class TestRefusedCombinations:
    def test_an_id_repeated_in_the_content_is_refused(self):
        api = _api()
        r = _run(["add-note-content", "--page", "Page A", "--keep-ids",
                  "--content", f"- a\n  id:: {ID}\n- b\n  id:: {ID}"], api)
        assert r.exit_code == 1
        assert "more than once" in r.stderr
        api.append_block_in_page.assert_not_called()

    @pytest.mark.parametrize("contents", [[TOP], [TOP, "second"]], ids=["single", "batch"])
    def test_no_preserve_refuses_keep_ids(self, contents):
        # joining the lines would turn id:: into text and keep nothing
        args = ["add-journal-block", "--top-level", *DATE, "--no-preserve", "--keep-ids"]
        for c in contents:
            args += ["--content", c]
        r = _run(args, _api())
        assert r.exit_code != 0
        assert "--no-preserve" in r.output + r.stderr


class TestFoundInReview:
    def test_an_indented_id_in_flat_content_is_kept(self):
        # "- Meeting\n  id:: X" is how a block reads when copied out of a page
        # file; the check saw the id, the flat write path did not.
        api = _fake()
        r = _run(["insert-block", "--page", "Page A", "--keep-ids",
                  "--content", f"- Meeting\n  id:: {ID}"], api)
        assert r.exit_code == 0, r.stderr
        assert _ids_asked_for(api) == [ID]
        assert _kept(api)

    @pytest.mark.parametrize("args", [
        ["insert-block", "--after", ANCHOR, "--content", TOP],
        ["add-note-content", "--page", "Page A", "--content", TOP],
        ["add-journal-block", "--top-level", *DATE, "--content", TOP],
        ["add-journal-content", "--top-level", *DATE, "--content", TOP],
        ["insert-block", "--after", ANCHOR, "--tree", ONE_CHILD],
    ], ids=["insert-block content", "add-note-content", "add-journal-block",
            "add-journal-content", "insert-block tree"])
    def test_without_keep_ids_the_id_line_is_removed_not_left_behind(self, args):
        # Left in the content, the old id:: line would sit under a block the
        # database knows by another uuid, and a copy would carry the original's
        # id into the file.
        api = _fake()
        r = _run(args, api)
        assert r.exit_code == 0, r.stderr
        written = [c.args[1] for c in api.insert_block.call_args_list + api.append_block_in_page.call_args_list]
        written += [n["content"] for c in api.insert_batch_block.call_args_list for n in _walk(c.args[1])]
        assert written and not any(ID in w for w in written), written

    @pytest.mark.parametrize("key", ["custom-id", "custom_id", "ID", "Custom_ID"])
    def test_every_spelling_logseq_reads_as_id_is_checked(self, key):
        api = _api(existing_ids=[ID])
        r = _run(["insert-block", "--child-of", ANCHOR, "--keep-ids",
                  "--tree", f"A\n  {key}:: {ID}\nB"], api)
        assert r.exit_code == 1
        assert "already belong to a block" in r.stderr
        api.insert_batch_block.assert_not_called()

    def test_a_repeated_id_is_caught_whatever_its_case(self):
        api = _api()
        r = _run(["add-note-content", "--page", "Page A", "--keep-ids",
                  "--content", f"- a\n  id:: {ID}\n- b\n  id:: {ID.upper()}"], api)
        assert r.exit_code == 1
        assert "more than once" in r.stderr


def _walk(nodes):
    for n in nodes or []:
        yield n
        yield from _walk(n.get("children"))
