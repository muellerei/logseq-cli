"""rename-page refuses a new name that would merge or do nothing.

Measured (Logseq 0.10.15): renamePage onto a name another page has merges
the two, the source page gone and its blocks under the target; an empty
name does nothing. Logseq answers null to both, and rename-page reported "Renamed".
A change of case only is a rename that works: the page keeps its uuid.

The check is shared by the preview and the run, like check_move for
move-block, so --dry-run refuses the same way.
"""
import json

import pytest

from logseq_cli.api import LogseqAPI
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble


@pytest.fixture
def double(monkeypatch):
    double = LogseqHttpDouble().install(monkeypatch)
    double.add_page("Old Page", ["old text"])
    double.add_page("Taken Page", ["taken text"])
    double.add_page("Linking Page", ["see [[Old Page]]"])
    return double


def _rename(new_name, *extra):
    return split_runner().invoke(cli, ["--token", "t", "rename-page", "--page", "Old Page",
                                       "--new-name", new_name, "--json", *extra])


def _assert_refused(double, new_name, why, *extra):
    before = double.snapshot()
    r = _rename(new_name, *extra)
    assert r.exit_code == 1
    assert r.stdout == ""
    error = json.loads(r.stderr)
    assert error["reason"] == "rename_refused"
    assert (error["old"], error["new"].strip(), error["why"]) == \
        ("Old Page", new_name.strip(), why)
    assert double.sent("renamePage") == []
    assert double.snapshot() == before
    return error


RUN_OR_PREVIEW = pytest.mark.parametrize("extra", [(), ("--dry-run",)], ids=["run", "dry-run"])


@RUN_OR_PREVIEW
@pytest.mark.parametrize("new_name", ["Taken Page", "taken page", "Taken Page ",
                                      "\ufeffTaken Page", "Taken Page\u3000"])
def test_existing_name_is_refused(double, extra, new_name):
    # "Taken Page " is the same page once trimmed, as Logseq trims it. So is
    # the name behind a byte order mark or after an ideographic space, which
    # JavaScript's trim takes off and Python's str.strip() leaves or not.
    _assert_refused(double, new_name, "exists", *extra)
    assert double.tree("Old Page") == [("old text", [])]
    assert double.tree("Taken Page") == [("taken text", [])]


@RUN_OR_PREVIEW
@pytest.mark.parametrize("new_name", ["", "   ", "\ufeff"], ids=["empty", "blank", "bom"])
def test_empty_name_is_refused(double, extra, new_name):
    _assert_refused(double, new_name, "empty", *extra)


@pytest.mark.parametrize("new_name,why", [("Taken Page", "exists"), ("   ", "empty")])
def test_dry_run_and_run_refuse_alike(double, new_name, why):
    run = json.loads(_rename(new_name).stderr)
    preview = json.loads(_rename(new_name, "--dry-run").stderr)
    keys = ("error", "reason", "old", "new", "why")
    assert {k: preview.get(k) for k in keys} == {k: run.get(k) for k in keys}
    assert run["why"] == why


def test_the_name_is_sent_stripped(double):
    r = _rename("New Name ")
    assert r.exit_code == 0, r.stderr
    assert double.sent("renamePage") == [["Old Page", "New Name"]]
    assert LogseqAPI(token="t").get_page("new name")["originalName"] == "New Name"


@pytest.mark.parametrize("new_name,sent", [("\ufeffNew Name\u2028", "New Name"),
                                           ("New Name\x85", "New Name\x85")],
                         ids=["js-trims", "js-keeps"])
def test_the_name_is_trimmed_as_logseq_trims_it(double, new_name, sent):
    # Sent as Logseq will name the page, so the proof reads that name: the
    # characters JavaScript's trim takes, and only those.
    r = _rename(new_name)
    assert r.exit_code == 0, r.stderr
    assert double.sent("renamePage") == [["Old Page", sent]]
    assert json.loads(r.stdout)["new_name"] == sent


@pytest.mark.parametrize("extra", [(), ("--dry-run",)], ids=["run", "dry-run"])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "text"])
def test_the_name_is_reported_as_sent(double, extra, as_json):
    # The page is called what was sent, not what was typed around it.
    args = ["--token", "t", "rename-page", "--page", "Old Page",
            "--new-name", "  New Name ", *extra, *(["--json"] if as_json else [])]
    r = split_runner().invoke(cli, args)
    assert r.exit_code == 0, r.stderr
    if as_json:
        assert json.loads(r.stdout)["new_name"] == "New Name"
    else:
        assert ("to:   New Name\n" if extra else "-> 'New Name'\n") in r.stdout


@pytest.mark.parametrize("extra", [(), ("--dry-run",)], ids=["run", "dry-run"])
def test_a_change_of_case_is_allowed(double, extra):
    # Measured: same uuid, originalName changes. Not a merge.
    uuid = LogseqAPI(token="t").get_page("old page")["uuid"]
    r = _rename("OLD page", *extra)
    assert r.exit_code == 0, r.stderr
    if extra:
        assert double.sent("renamePage") == []
        return
    assert double.sent("renamePage") == [["Old Page", "OLD page"]]
    page = LogseqAPI(token="t").get_page("old page")
    assert (page["uuid"], page["originalName"]) == (uuid, "OLD page")


def test_rename_refusal_answers_none_for_a_rename_that_works(double):
    api = LogseqAPI(token="t")
    assert api.rename_refusal("Old Page", "New Name") is None
    assert api.rename_refusal("Old Page", "OLD page") is None
    for refused in ("Taken Page", "", "   "):
        assert api.rename_refusal("Old Page", refused) is not None, refused
