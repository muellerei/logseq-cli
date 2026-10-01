"""``[graph] keep_empty_blocks_last``: a write goes before the empty blocks that end its section (#110).

Run through the real ``LogseqAPI`` against the HTTP double, so the editor gate
and the proof of each write take part. The setting is switched on through its
environment variable, as a user's config would.

Without the setting a write appends behind an empty last block and leaves it
where it was. With it, the write goes directly before the empty blocks at the
end, which stay last as the place the user clicks to type.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

ENV = "LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST"
PAGE = "Project Alpha"
JOURNAL = "2099-01-05, Monday"


def leaf(text):
    return (text, [])


def invoke(double, *args):
    return split_runner().invoke(cli, ["--token", "t", *args, "--json"])


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(ENV, "1")


@pytest.fixture
def off(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


def graph(monkeypatch, blocks, name=PAGE):
    return LogseqHttpDouble.installed(monkeypatch, {name: blocks})


def tasks(*children):
    return [{"content": "## Tasks", "children": list(children)}]


def under_tasks(double, name=PAGE):
    (heading, kids), = double.tree(name)
    assert heading == "## Tasks"
    return kids


def kids_of_tasks(double, name=PAGE):
    return double._require_page(name)["blocks"][0]["children"]


class TestAtTheEndOfASection:
    def test_insert_block_goes_before_the_empty_last_child(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b"), leaf("")]

    def test_add_journal_block_under_a_heading(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("logged", ""), name=JOURNAL)
        r = invoke(double, "add-journal-block", "--date", "2099-01-05",
                   "--under-heading", "## Tasks", "--content", "next")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double, JOURNAL) == [leaf("logged"), leaf("next"), leaf("")]

    def test_add_note_content_under_a_heading(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        r = invoke(double, "add-note-content", "--page", PAGE,
                   "--under-heading", "## Tasks", "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b"), leaf("")]

    def test_every_empty_block_at_the_end_stays_last(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", "", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b"), leaf(""), leaf("")]

    def test_without_the_setting_it_appends_behind_the_empty_block(self, monkeypatch, off):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf(""), leaf("TODO b")]

    def test_the_config_file_turns_it_on_too(self, monkeypatch, tmp_path, off):
        config = tmp_path / "config.toml"
        config.write_text("[graph]\nkeep_empty_blocks_last = true\n")
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(config))
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b"), leaf("")]

    def test_the_environment_turns_it_off_over_the_file(self, monkeypatch, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text("[graph]\nkeep_empty_blocks_last = true\n")
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(config))
        monkeypatch.setenv(ENV, "0")
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf(""), leaf("TODO b")]

    def test_a_value_in_the_file_that_is_no_bool_is_refused_before_a_write(
            self, monkeypatch, tmp_path, off):
        config = tmp_path / "config.toml"
        config.write_text('[graph]\nkeep_empty_blocks_last = "yes"\n')
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(config))
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code != 0
        assert "keep_empty_blocks_last" in r.stderr
        assert double.writes() == []


class TestWhatCountsAsAnEmptyBlockAtTheEnd:
    def test_an_empty_block_with_an_empty_child_is_not_empty(self, monkeypatch, on):
        nested = {"content": "", "children": [""]}
        double = graph(monkeypatch, tasks("TODO a", nested))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), ("", [leaf("")]), leaf("TODO b")]

    def test_an_empty_block_in_the_middle_is_left_alone(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", "", "TODO c"))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO d")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf(""), leaf("TODO c"), leaf("TODO d")]

    def test_nothing_to_keep_last_appends_as_before(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a"))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b")]
        assert "before_empty_block" not in json.loads(r.stdout)


class TestAtTheEndOfAPage:
    def test_insert_block_page(self, monkeypatch, on):
        double = graph(monkeypatch, ["TODO a", ""])
        r = invoke(double, "insert-block", "--page", PAGE, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(PAGE) == [leaf("TODO a"), leaf("TODO b"), leaf("")]

    def test_add_note_content_without_a_heading_on_a_page_that_is_only_empty(
            self, monkeypatch, on):
        double = graph(monkeypatch, [""])
        r = invoke(double, "add-note-content", "--page", PAGE, "--content", "first")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(PAGE) == [leaf("first"), leaf("")]

    def test_add_journal_block_at_the_top_level(self, monkeypatch, on):
        double = graph(monkeypatch, ["logged", ""], name=JOURNAL)
        r = invoke(double, "add-journal-block", "--date", "2099-01-05",
                   "--top-level", "--content", "next")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(JOURNAL) == [leaf("logged"), leaf("next"), leaf("")]


class TestOneCommandIsOneWrite:
    def test_a_tree_of_two_roots_goes_before_the_empty_blocks_in_order(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", "", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--tree", "- b\n\t- kid\n- c")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [
            leaf("TODO a"), ("b", [leaf("kid")]), leaf("c"), leaf(""), leaf("")]

    def test_two_content_values_keep_the_order_and_the_empty_blocks_last(
            self, monkeypatch, on):
        double = graph(monkeypatch, tasks("X", "", ""), name=JOURNAL)
        r = invoke(double, "add-journal-block", "--date", "2099-01-05",
                   "--under-heading", "## Tasks", "--content", "A", "--content", "B")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double, JOURNAL) == [
            leaf("X"), leaf("A"), leaf("B"), leaf(""), leaf("")]

    def test_the_properties_land_on_the_new_block(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b",
                   "--property", "prio=1")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b"), leaf("")]
        assert "prio:: 1" in kids_of_tasks(double)[1]["content"]


class TestWhatItReports:
    def test_the_json_names_the_empty_block_it_went_before(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        empty = kids_of_tasks(double)[1]["uuid"]
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert json.loads(r.stdout)["before_empty_block"] == empty

    def test_the_text_says_so(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = split_runner().invoke(cli, ["--token", "t", "insert-block", "--child-of", heading,
                                        "--content", "TODO b"])
        assert "(before the empty block at the end)" in r.stdout


class TestWhenItMakesNoDifference:
    def test_a_block_open_in_the_editor_is_no_reason_to_hold_back(self, monkeypatch, on):
        # An insert does not touch the block open in the editor, so the cursor
        # in the empty block the user clicked changes nothing: the write goes
        # before it and the user keeps typing there.
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        double.editing = kids_of_tasks(double)[1]["uuid"]
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b"), leaf("")]

    def test_an_empty_block_is_never_written_to(self, monkeypatch, on):
        # Nothing is overwritten: the empty block may have been typed into
        # since it was read, by the user or by another writer.
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b")
        assert [m for m, _ in double.writes() if m.endswith("updateBlock")] == []

    def test_keep_ids_with_an_id_goes_before_it_too(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--keep-ids", "--content",
                   "TODO b\nid:: 6abe4200-0000-4000-8000-000000000a01")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("TODO b"), leaf("")]
        assert kids_of_tasks(double)[1]["uuid"] == "6abe4200-0000-4000-8000-000000000a01"

    def test_keep_ids_with_an_id_in_a_later_root_keeps_the_order(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        tree = json.dumps([{"content": "b", "children": []},
                           {"content": "c\nid:: 6abe4200-0000-4000-8000-000000000a01",
                            "children": []}])
        r = invoke(double, "insert-block", "--child-of", heading, "--keep-ids", "--tree", tree)
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf("b"), leaf("c"), leaf("")]

    def test_keep_ids_on_a_page_without_blocks_still_anchors_and_cleans_up(
            self, monkeypatch, on):
        double = graph(monkeypatch, [])
        r = invoke(double, "insert-block", "--page", PAGE, "--keep-ids", "--content",
                   "first\nid:: 6abe4200-0000-4000-8000-000000000a01")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(PAGE) == [leaf("first")]

    def test_a_missing_heading_goes_before_the_empty_block_at_the_page_end(
            self, monkeypatch, on):
        double = graph(monkeypatch, [""])
        r = invoke(double, "add-note-content", "--page", PAGE,
                   "--under-heading", "## New", "--content", "first")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(PAGE) == [("## New", [leaf("first")]), leaf("")]

    def test_a_ref_to_the_empty_block_itself_is_a_block_like_any_other(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        empty = kids_of_tasks(double)[1]["uuid"]
        r = invoke(double, "add-block-ref", "--source-id", empty, "--page", PAGE,
                   "--under-heading", "## Tasks")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double) == [leaf("TODO a"), leaf(f"(({empty}))"), leaf(f"id:: {empty}")]


class TestAFirstBlockThatWouldBecomeThePageProperties:
    """Logseq reads a first block of ``key::`` lines as the page's properties:
    ``title::`` renames the page, ``alias::`` and ``tags::`` act on all of it."""

    def test_it_is_not_put_before_the_first_block_of_a_page(self, monkeypatch, on):
        double = graph(monkeypatch, [""])
        r = invoke(double, "add-note-content", "--page", PAGE, "--content", "alias:: Sneaky")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(PAGE) == [leaf(""), leaf("alias:: Sneaky")]

    def test_a_properties_block_after_other_blocks_is_no_page_property(self, monkeypatch, on):
        double = graph(monkeypatch, ["TODO a", ""])
        r = invoke(double, "add-note-content", "--page", PAGE, "--content", "alias:: Sneaky")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(PAGE) == [leaf("TODO a"), leaf("alias:: Sneaky"), leaf("")]


class TestThePreview:
    def test_it_says_which_empty_block_it_would_go_before_and_writes_nothing(
            self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        empty = kids_of_tasks(double)[1]["uuid"]
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b",
                   "--dry-run")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert json.loads(r.stdout)["would_go_before_empty_block"] == empty
        assert double.writes() == []

    def test_without_the_setting_the_preview_is_what_it_was(self, monkeypatch, off):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "TODO b",
                   "--dry-run")
        assert "would_go_before_empty_block" not in json.loads(r.stdout)

    def test_an_upsert_preview_names_the_block_when_the_block_is_new(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""), name=JOURNAL)
        empty = kids_of_tasks(double, JOURNAL)[1]["uuid"]
        r = invoke(double, "add-journal-block", "--date", "2099-01-05",
                   "--under-heading", "## Tasks", "--upsert-heading", "new",
                   "--content", "new entry", "--dry-run")
        assert json.loads(r.stdout)["would_go_before_empty_block"] == empty
        assert double.writes() == []

    def test_an_upsert_preview_names_nothing_when_the_block_is_found(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("new", ""), name=JOURNAL)
        r = invoke(double, "add-journal-block", "--date", "2099-01-05",
                   "--under-heading", "## Tasks", "--upsert-heading", "new",
                   "--content", "new again", "--dry-run")
        assert "would_go_before_empty_block" not in json.loads(r.stdout)

    def test_a_missing_heading_is_not_promised_in_the_preview(self, monkeypatch, on):
        double = graph(monkeypatch, [""])
        r = invoke(double, "add-note-content", "--page", PAGE,
                   "--under-heading", "## New", "--content", "first", "--dry-run")
        assert "would_go_before_empty_block" not in json.loads(r.stdout)


class TestOtherWritersAtTheEnd:
    def test_copy_block_puts_the_copy_before_the_empty_block(self, monkeypatch, on):
        double = LogseqHttpDouble.installed(monkeypatch, {
            PAGE: ["TODO a", ""],
            "Source": [{"content": "parent", "children": ["kid"]}]})
        source = double.uuid_of("parent")
        r = invoke(double, "copy-block", "--id", source, "--to-page", PAGE)
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(PAGE) == [leaf("TODO a"), ("parent", [leaf("kid")]), leaf("")]

    def test_an_upsert_that_adds_a_new_block_goes_before_the_empty_block(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""), name=JOURNAL)
        r = invoke(double, "add-journal-block", "--date", "2099-01-05",
                   "--under-heading", "## Tasks", "--upsert-heading", "new",
                   "--content", "new entry")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert under_tasks(double, JOURNAL) == [leaf("TODO a"), leaf("new entry"), leaf("")]

    def test_the_deprecated_add_journal_entry_is_left_as_it_is(self, monkeypatch, on):
        double = graph(monkeypatch, ["logged", ""], name=JOURNAL)
        r = invoke(double, "add-journal-entry", "--date", "2099-01-05", "--content", "next")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree(JOURNAL) == [leaf("logged"), leaf(""), leaf("next")]


class TestWhenAReadFindsNothing:
    def test_a_page_logseq_does_not_have_is_written_as_before(self, monkeypatch, on):
        double = LogseqHttpDouble.installed(monkeypatch, {})
        r = invoke(double, "add-note-content", "--page", "Fresh Page", "--content", "first")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert double.tree("Fresh Page") == [leaf("first")]

    @pytest.mark.parametrize("failure", [RuntimeError("bad response"), OSError("timeout")])
    def test_a_read_that_fails_is_no_reason_to_stop(self, failure):
        from logseq_cli.strictinsert import first_empty_at_end

        class Api:
            def get_page_blocks_tree(self, name, cached=True):
                raise failure

        assert first_empty_at_end(
            Api(), [{"content": "x", "children": []}], page_name="P") is None


class TestAWriteThatStopsHalfway:
    def test_the_first_block_landed_and_the_error_says_how_many(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", "", ""))
        double.set_mode("insertBlock", "error", from_call=2)
        heading = double.uuid_of("## Tasks")
        r = invoke(double, "insert-block", "--child-of", heading, "--tree", "- b\n- c")
        assert r.exit_code != 0
        error = json.loads(r.stderr)
        assert error["writes_landed"] == 1, error
        assert "check before retrying" in error["error"], error
        assert under_tasks(double)[1] == leaf("b")


# One row per way a write ends a section: the command, where the empty block is
# (a page's end or the last child of "## Tasks"), and what the write adds.
SECTION = "section"
PAGE_END = "page-end"
FORMS = [
    ("insert-block-child-flat", SECTION, PAGE, ["insert-block", "--child-of", "@heading", "--content", "new"]),
    ("insert-block-child-tree", SECTION, PAGE, ["insert-block", "--child-of", "@heading", "--tree", "- new"]),
    ("insert-block-child-multiline", SECTION, PAGE, ["insert-block", "--child-of", "@heading", "--content", "new\n\t- kid"]),
    ("insert-block-page-flat", PAGE_END, PAGE, ["insert-block", "--page", PAGE, "--content", "new"]),
    ("insert-block-page-multiline", PAGE_END, PAGE, ["insert-block", "--page", PAGE, "--content", "new\n\t- kid"]),
    ("insert-block-page-top-level-tree", PAGE_END, PAGE, ["insert-block", "--page", PAGE, "--top-level", "--tree", "- new"]),
    ("add-note-content-heading", SECTION, PAGE, ["add-note-content", "--page", PAGE, "--under-heading", "## Tasks", "--content", "new"]),
    ("add-note-content-page", PAGE_END, PAGE, ["add-note-content", "--page", PAGE, "--content", "new"]),
    ("add-journal-block-heading", SECTION, JOURNAL, ["add-journal-block", "--date", "2099-01-05", "--under-heading", "## Tasks", "--content", "new"]),
    ("add-journal-block-tree", SECTION, JOURNAL, ["add-journal-block", "--date", "2099-01-05", "--under-heading", "## Tasks", "--content", "new\n\t- kid"]),
    ("add-journal-block-batch", SECTION, JOURNAL, ["add-journal-block", "--date", "2099-01-05", "--under-heading", "## Tasks", "--content", "new", "--content", "more"]),
    ("add-journal-block-top-level", PAGE_END, JOURNAL, ["add-journal-block", "--date", "2099-01-05", "--top-level", "--content", "new"]),
    ("add-journal-block-upsert", SECTION, JOURNAL, ["add-journal-block", "--date", "2099-01-05", "--under-heading", "## Tasks", "--upsert-heading", "new", "--content", "new"]),
    ("add-journal-content-heading", SECTION, JOURNAL, ["add-journal-content", "--date", "2099-01-05", "--under-heading", "## Tasks", "--content", "- new"]),
    ("add-journal-content-top-level", PAGE_END, JOURNAL, ["add-journal-content", "--date", "2099-01-05", "--top-level", "--content", "- new"]),
    ("add-block-ref-heading", SECTION, PAGE, ["add-block-ref", "--source-id", "@source", "--page", PAGE, "--under-heading", "## Tasks"]),
    ("add-block-ref-page", PAGE_END, PAGE, ["add-block-ref", "--source-id", "@source", "--page", PAGE]),
    ("copy-block", PAGE_END, PAGE, ["copy-block", "--id", "@source", "--to-page", PAGE]),
]


def count(tree):
    return sum(1 + count(kids) for _, kids in tree)


def form_graph(monkeypatch, where, page):
    blocks = tasks("TODO a", "") if where == SECTION else ["TODO a", ""]
    pages = {page: blocks, "Source": ["source block"]}
    return LogseqHttpDouble.installed(monkeypatch, pages)


def form_args(double, args):
    refs = {"@heading": lambda: double.uuid_of("## Tasks"),
            "@source": lambda: double.uuid_of("source block")}
    return [refs[a]() if a in refs else a for a in args]


def at_the_end(double, where, page):
    return under_tasks(double, page) if where == SECTION else double.tree(page)


@pytest.mark.parametrize("form", FORMS, ids=[f[0] for f in FORMS])
class TestEveryFormOfAWriteAtTheEnd:
    def test_it_goes_before_the_empty_block(self, monkeypatch, on, form):
        _, where, page, args = form
        double = form_graph(monkeypatch, where, page)
        r = invoke(double, *form_args(double, args))
        assert r.exit_code == 0, r.stdout + r.stderr
        out = at_the_end(double, where, page)
        assert out[0] == leaf("TODO a")
        assert out[1] != leaf(""), out
        assert out[-1] == leaf(""), out
        assert json.loads(r.stdout).get("before_empty_block"), r.stdout

    def test_it_writes_each_block_once_and_loses_none(self, monkeypatch, form):
        _, where, page, args = form

        def blocks_after(setting):
            monkeypatch.setenv(ENV, setting)
            double = form_graph(monkeypatch, where, page)
            r = invoke(double, *form_args(double, args))
            assert r.exit_code == 0, r.stdout + r.stderr
            return count(double.tree(page))

        assert blocks_after("1") == blocks_after("0")

    def test_the_text_says_so(self, monkeypatch, on, form):
        _, where, page, args = form
        double = form_graph(monkeypatch, where, page)
        r = split_runner().invoke(cli, ["--token", "t", *form_args(double, args)])
        assert r.exit_code == 0, r.stdout + r.stderr
        assert "(before the empty block at the end)" in r.stdout

    def test_the_preview_says_which_block(self, monkeypatch, on, form):
        _, where, page, args = form
        double = form_graph(monkeypatch, where, page)
        r = invoke(double, *form_args(double, args), "--dry-run")
        assert r.exit_code == 0, r.stdout + r.stderr
        assert json.loads(r.stdout).get("would_go_before_empty_block"), r.stdout
        assert double.writes() == []
        text = split_runner().invoke(cli, ["--token", "t", *form_args(double, args), "--dry-run"])
        assert "would go before the empty block" in text.stdout

    def test_without_the_setting_the_empty_block_stays_in_front(self, monkeypatch, off, form):
        _, where, page, args = form
        double = form_graph(monkeypatch, where, page)
        r = invoke(double, *form_args(double, args))
        assert r.exit_code == 0, r.stdout + r.stderr
        out = at_the_end(double, where, page)
        assert out[:2] == [leaf("TODO a"), leaf("")], out
        assert "before_empty_block" not in json.loads(r.stdout)


class TestWhatTheSecondGateFound:
    """Cases the second spec check showed to be unguarded (#110)."""

    ID = "6abe4200-0000-4000-8000-000000000a01"

    def test_a_target_block_that_does_not_exist_fails_as_it_does_without_the_setting(
            self, monkeypatch, tmp_path):
        reasons = {}
        for setting in ("1", "0"):
            monkeypatch.setenv(ENV, setting)
            double = graph(monkeypatch, tasks("TODO a", ""))
            r = invoke(double, "insert-block", "--child-of",
                       "00000000-0000-4000-8000-0000000fffff", "--content", "x")
            assert r.exit_code != 0
            reasons[setting] = json.loads(r.stderr)["reason"]
        assert reasons["1"] == reasons["0"]

    def test_the_children_are_read_fresh_not_from_the_cache(self):
        from logseq_cli.strictinsert import first_empty_at_end

        seen = []

        class Api:
            def get_block(self, uuid, include_children=True, cached=True):
                seen.append(("get_block", cached))
                return {"children": [{"uuid": "e", "content": "", "children": []}]}

            def get_page_blocks_tree(self, name, cached=True):
                seen.append(("get_page_blocks_tree", cached))
                return [{"uuid": "a", "content": "x", "children": []},
                        {"uuid": "e", "content": "", "children": []}]

        node = [{"content": "n", "children": []}]
        assert first_empty_at_end(Api(), node, parent_uuid="p") == "e"
        assert first_empty_at_end(Api(), node, page_name="P") == "e"
        assert seen == [("get_block", False), ("get_page_blocks_tree", False)]

    def test_keep_ids_keeps_the_id_of_a_later_root(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        tree = json.dumps([{"content": "b", "children": []},
                           {"content": f"c\nid:: {self.ID}", "children": []}])
        invoke(double, "insert-block", "--child-of", heading, "--keep-ids", "--tree", tree)
        assert kids_of_tasks(double)[2]["uuid"] == self.ID

    def test_keep_ids_keeps_the_id_of_a_child(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        heading = double.uuid_of("## Tasks")
        tree = json.dumps([{"content": "b", "children": [
            {"content": f"kid\nid:: {self.ID}", "children": []}]}])
        invoke(double, "insert-block", "--child-of", heading, "--keep-ids", "--tree", tree)
        assert kids_of_tasks(double)[1]["children"][0]["uuid"] == self.ID

    def test_keep_ids_on_a_page_without_blocks_keeps_the_id(self, monkeypatch, on):
        double = graph(monkeypatch, [])
        invoke(double, "insert-block", "--page", PAGE, "--keep-ids", "--content",
               f"first\nid:: {self.ID}")
        assert double._require_page(PAGE)["blocks"][0]["uuid"] == self.ID

    def test_a_non_bool_in_the_file_is_a_config_error_with_its_reason(
            self, monkeypatch, tmp_path, off):
        config = tmp_path / "config.toml"
        config.write_text('[graph]\nkeep_empty_blocks_last = "yes"\n')
        monkeypatch.setenv("LOGSEQ_CLI_CONFIG", str(config))
        double = graph(monkeypatch, tasks("TODO a", ""))
        r = invoke(double, "insert-block", "--child-of", double.uuid_of("## Tasks"),
                   "--content", "x")
        assert json.loads(r.stderr)["reason"] == "config_error"

    def test_add_note_content_that_stops_halfway_says_how_many_landed(self, monkeypatch, on):
        double = graph(monkeypatch, tasks("TODO a", ""))
        double.set_mode("insertBlock", "error", from_call=2)
        r = invoke(double, "add-note-content", "--page", PAGE, "--under-heading", "## Tasks",
                   "--content", "- b\n- c")
        assert r.exit_code != 0
        assert json.loads(r.stderr)["writes_landed"] == 1
        assert under_tasks(double)[:2] == [leaf("TODO a"), leaf("b")]

    def test_copy_block_that_stops_halfway_says_how_many_landed(self, monkeypatch, on):
        double = LogseqHttpDouble.installed(monkeypatch, {
            PAGE: ["TODO a", ""],
            "Source": [{"content": "parent", "children": ["kid"]}]})
        double.set_mode("insertBlock", "error", from_call=2)
        r = invoke(double, "copy-block", "--id", double.uuid_of("parent"), "--to-page", PAGE)
        assert r.exit_code != 0
        assert json.loads(r.stderr)["writes_landed"] == 1


class TestTheAnchorChangedOnTheWay:
    """The empty block is read, and a moment later the write goes before it. If
    it moved in between, the write says so instead of landing somewhere else."""

    def test_the_roots_are_proven_to_stand_directly_before_the_anchor(self):
        from logseq_cli.strictinsert import _directly_before

        kids = [{"uuid": "a"}, {"uuid": "n1"}, {"uuid": "n2"}, {"uuid": "e"}]
        assert _directly_before(kids, ["n1", "kid", "n2"], "e")
        assert not _directly_before(kids, ["n2", "n1"], "e")          # the other order
        assert not _directly_before([{"uuid": "a"}, {"uuid": "n1"}, {"uuid": "x"},
                                     {"uuid": "e"}], ["n1"], "e")      # a block in between
        assert not _directly_before([{"uuid": "a"}, {"uuid": "e"}], ["n1"], "e")  # not there
        assert not _directly_before([{"uuid": "n1"}], ["n1"], "e")     # anchor gone

    def test_an_anchor_that_became_a_child_is_reported_not_written_around(
            self, monkeypatch, on):
        double = graph(monkeypatch, tasks({"content": "TODO a", "children": ["kid"]}, ""))
        heading = double.uuid_of("## Tasks")
        # What a Tab in the empty block does between the read and the write:
        # the anchor the write goes before is now below another block.
        kid = double.uuid_of("kid")
        monkeypatch.setattr("logseq_cli.strictinsert.first_empty_at_end",
                            lambda *args, **kwargs: kid)
        r = invoke(double, "insert-block", "--child-of", heading, "--content", "new")
        assert r.exit_code != 0
        error = json.loads(r.stderr)
        assert error["reason"] == "write_not_verified", error
        assert error["writes_landed"] == 1, error
