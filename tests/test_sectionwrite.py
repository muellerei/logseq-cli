"""The plan for a write into a page section, and what the three commands
that write by it report.

``add-journal-block`` (four paths), ``add-journal-content`` and
``add-note-content`` once worked out their previews path by path, and the
previews drifted from the runs and from each other (measured against the HTTP
double before sectionwrite existed):

- A batch or a tree on a journal page that did not exist yet printed text
  after the JSON of its --dry-run --json (``tests/test_dry_run_json_only.py``),
  and the tree path left ``would_create_page`` out.
- The upsert's preview said neither that the page would be created nor
  whether it would replace a block or create one.
- No journal preview said that the heading would be created.
- ``--upsert-heading`` with two ``--content`` skipped the upsert: exit 0, the
  old block stayed, both values were added under the heading.
- Two blocks under the heading matching ``--upsert-heading``: the first won.

Run through the real ``LogseqAPI`` against the HTTP double.
"""
import json

import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

JOURNAL = "2099-01-05, Monday"      # exists in the graphs below
KEEP = "LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(KEEP, raising=False)
    monkeypatch.delenv("LOGSEQ_JOURNAL_HEADING", raising=False)


def run(*args):
    return split_runner().invoke(cli, ["--token", "t", *args, "--json"])


def payload(r):
    assert r.exit_code == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


def journal_block(date, *args):
    return ["add-journal-block", "--date", date, *args]


# --- A and B: the flag is in the JSON --------------------------------------

@pytest.mark.parametrize("content", [["--content", "a", "--content", "b"],
                                     ["--content", "parent\n\t- child"]],
                         ids=["batch", "tree"])
def test_a_preview_on_a_missing_journal_page_says_it_would_be_created(monkeypatch, content):
    LogseqHttpDouble.installed(monkeypatch, {})
    p = payload(run(*journal_block("2099-01-06", "--top-level", *content), "--dry-run"))
    assert p["would_create_page"] is True


# --- C: the upsert's preview ----------------------------------------------

def test_the_upsert_preview_says_the_page_and_the_block_would_be_created(monkeypatch):
    double = LogseqHttpDouble.installed(monkeypatch, {})
    p = payload(run(*journal_block("2099-01-06", "--under-heading", "## Log",
                                   "--upsert-heading", "### Carol", "--content", "### Carol\nx"),
                    "--dry-run"))
    assert (p["would_create_page"], p["would_create_heading"], p["action"]) == (True, True, "create")
    assert "would_replace" not in p
    assert p["blocks"] == 1
    assert double.writes() == []


def test_the_upsert_preview_names_the_block_it_would_replace(monkeypatch):
    double = LogseqHttpDouble.installed(
        monkeypatch, {JOURNAL: [{"content": "## Log", "children": ["### Carol\nold"]}]})
    p = payload(run(*journal_block("2099-01-05", "--under-heading", "## Log",
                                   "--upsert-heading", "### Carol",
                                   "--content", "### Carol\n\t- kid"), "--dry-run"))
    assert (p["would_create_page"], p["would_create_heading"]) == (False, False)
    assert (p["action"], p["would_replace"]) == ("replace", double.uuid_of("### Carol\nold"))
    assert p["blocks"] == 2


# --- C2: the heading -------------------------------------------------------

HEADING_CASES = {
    "journal-block flat": journal_block("2099-01-05", "--content", "x"),
    "journal-block tree": journal_block("2099-01-05", "--content", "x\n\t- y"),
    "journal-block batch": journal_block("2099-01-05", "--content", "x", "--content", "y"),
    "journal-content": ["add-journal-content", "--date", "2099-01-05", "--content", "- x\n- y"],
    "note-content": ["add-note-content", "--page", JOURNAL, "--content", "x"],
}


@pytest.mark.parametrize("args", HEADING_CASES.values(), ids=HEADING_CASES.keys())
@pytest.mark.parametrize("heading,created", [("## Log", False), ("## Missing", True)])
def test_every_preview_says_whether_the_heading_would_be_created(monkeypatch, args, heading, created):
    LogseqHttpDouble.installed(monkeypatch, {JOURNAL: [{"content": "## Log", "children": ["a"]}]})
    p = payload(run(*args, "--under-heading", heading, "--dry-run"))
    assert p["would_create_heading"] is created
    assert p["would_create_page"] is False


# --- D2 and E8: refused before any write ----------------------------------

def test_upsert_with_two_contents_is_a_usage_error(monkeypatch):
    double = LogseqHttpDouble.installed(
        monkeypatch, {JOURNAL: [{"content": "## Log", "children": ["### Carol\nold"]}]})
    r = run(*journal_block("2099-01-05", "--under-heading", "## Log", "--upsert-heading",
                           "### Carol", "--content", "### Carol\nnew", "--content", "other"))
    assert r.exit_code == 2
    assert "--upsert-heading replaces one block" in r.stderr
    assert double.writes() == []
    assert double.uuid_of("### Carol\nold")


@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]], ids=["run", "dry-run"])
def test_two_matching_blocks_are_refused(monkeypatch, dry_run):
    double = LogseqHttpDouble.installed(
        monkeypatch, {JOURNAL: [{"content": "## Log", "children": ["### Carol\none", "### Carol\ntwo"]}]})
    r = run(*journal_block("2099-01-05", "--under-heading", "## Log", "--upsert-heading",
                           "### Carol", "--content", "### Carol\nnew"), *dry_run)
    assert r.exit_code == 1
    error = json.loads(r.stderr)
    assert error["reason"] == "ambiguous"
    assert error["matches"] == [double.uuid_of("### Carol\none"), double.uuid_of("### Carol\ntwo")]
    assert double.writes() == []


# --- The preview and the run agree ----------------------------------------

def _heading_children(double, page, heading):
    for content, children in double.tree(page):
        if content == heading:
            return children
    return None


PATHS = {
    "journal-block flat": (["add-journal-block", "--content", "x"], "journal"),
    "journal-block tree": (["add-journal-block", "--content", "x\n\t- y"], "journal"),
    "journal-block batch": (["add-journal-block", "--content", "x", "--content", "y\n\t- z"], "journal"),
    "journal-block upsert": (["add-journal-block", "--upsert-heading", "### Carol",
                              "--content", "### Carol\nnew\n\t- kid"], "journal"),
    "journal-content": (["add-journal-content", "--content", "- x\n\t- y"], "journal"),
    "note-content": (["add-note-content", "--content", "- x\n\t- y"], "note"),
}

# The section ends in an empty block, so with keep_empty_blocks_last a write
# goes before it; "### Carol\nold" is the block the upsert replaces.
SECTION = [{"content": "## Log", "children": ["### Carol\nold", ""]}]


def _where(kind, date, page):
    return (["--date", date] if kind == "journal" else ["--page", page])


@pytest.mark.parametrize("args,kind", PATHS.values(), ids=PATHS.keys())
@pytest.mark.parametrize("graph,heading", [
    ("missing page", "## Log"), ("missing heading", "## Other"),
    ("present", "## Log"), ("present, top level", None)])
@pytest.mark.parametrize("keep", [False, True], ids=["keep-off", "keep-on"])
def test_the_preview_says_what_the_run_does(monkeypatch, args, kind, graph, heading, keep):
    if heading is None and "--upsert-heading" in args:
        pytest.skip("--upsert-heading requires --under-heading")
    if keep:
        monkeypatch.setenv(KEEP, "true")
    pages = {} if graph == "missing page" else {JOURNAL: SECTION, "Note Page": SECTION}
    page = JOURNAL if kind == "journal" else "Note Page"
    double = LogseqHttpDouble.installed(monkeypatch, pages)
    place = ["--under-heading", heading] if heading else (["--top-level"] if kind == "journal" else [])
    call = [*args, *_where(kind, "2099-01-05", page), *place]

    preview = payload(run(*call, "--dry-run"))
    assert double.writes() == []
    done = payload(run(*call))

    written = done.get("blocks_added", done.get("blocks", 1 if "result" in done else None))
    assert preview.get("blocks", preview.get("blocks_added")) == written
    created = [m.rsplit(".", 1)[-1] for m, _ in double.writes()]
    assert preview["would_create_page"] is ("createPage" in created)
    if heading:
        assert preview["would_create_heading"] is (graph != "present")
        assert _heading_children(double, page, heading) is not None
    assert preview.get("would_go_before_empty_block") == done.get("before_empty_block")


def test_the_run_reads_the_section_once(monkeypatch):
    # The empty block a write goes before is read by the write's own helper;
    # the plan read it as well once, a request more than before the plan
    # existed.
    monkeypatch.setenv(KEEP, "true")
    double = LogseqHttpDouble.installed(monkeypatch, {JOURNAL: SECTION})
    payload(run(*journal_block("2099-01-05", "--content", "x", "--under-heading", "## Log")))
    sent = [m.rsplit(".", 1)[-1] for m, _ in double.requests]
    reads = sent[:sent.index(next(m for m in sent if m in ("insertBlock", "insertBatchBlock")))]
    assert [m for m in reads if m != "getUserConfigs"] == ["getPage", "getPageBlocksTree", "getBlock"]
