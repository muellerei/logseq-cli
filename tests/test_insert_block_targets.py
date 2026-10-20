"""``insert-block`` checks its target options once, the same for ``--content``
and ``--tree``, before any request.

The flat mode counted the targets; the tree mode went down an if/elif chain
that stopped at the first one it knew (reproduced against the HTTP double):
``--tree ... --after A --before B`` and ``--after A --first`` wrote after A
with exit 0, ``--page`` beside an anchor was dropped, ``--top-level`` beside
an anchor was dropped in both modes, and ``--tree --page P`` without
``--top-level`` was refused after a ``getPage`` where ``--content`` wrote to
the end of P.

Run through the real ``LogseqAPI`` against the HTTP double.
"""
import pytest

from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

PAGE = "Probe Page"
ONE = "Specify one of: --page, --after, --before, --child-of"
ONLY_ONE = "Specify only one of: --page, --after, --before, --child-of"
FIRST = "--first only applies to --child-of (it selects the first child position)."
TOP = "--top-level only applies to --page (it is where --page writes anyway)."

MODES = {"content": ["--content", "new one"], "tree": ["--tree", "new one"]}

# Options with "@<text>" for that block's uuid.
REFUSED = {
    "no target": ([], ONE),
    "top-level alone": (["--top-level"], ONE),
    "after and before": (["--after", "@alpha", "--before", "@beta"], ONLY_ONE),
    "page and after": (["--page", PAGE, "--after", "@alpha"], ONLY_ONE),
    "page and child-of": (["--page", PAGE, "--child-of", "@parent"], ONLY_ONE),
    "page, child-of, first": (["--page", PAGE, "--child-of", "@parent", "--first"], ONLY_ONE),
    "after and child-of": (["--after", "@alpha", "--child-of", "@parent"], ONLY_ONE),
    "after and first": (["--after", "@alpha", "--first"], FIRST),
    "page and first": (["--page", PAGE, "--first"], FIRST),
    "child-of and top-level": (["--child-of", "@parent", "--top-level"], TOP),
    "after and top-level": (["--after", "@alpha", "--top-level"], TOP),
}

ACCEPTED = {
    "page": ["--page", PAGE],
    "page and top-level": ["--page", PAGE, "--top-level"],
    "after": ["--after", "@alpha"],
    "before": ["--before", "@beta"],
    "child-of": ["--child-of", "@parent"],
    "child-of first": ["--child-of", "@parent", "--first"],
}


def _graph(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {
        PAGE: ["alpha", "beta", {"content": "parent", "children": ["kid"]}]})


def _run(double, *args):
    args = [double.uuid_of(a[1:]) if a.startswith("@") else a for a in args]
    return split_runner().invoke(cli, ["--token", "t", "insert-block", *args])


def _where(double, content="new one"):
    """(parent's first line or None, index among its siblings) of the block."""
    def walk(nodes, parent):
        for i, (text, kids) in enumerate(nodes):
            if text == content:
                return parent, i
            found = walk(kids, text)
            if found:
                return found
        return None
    return walk(double.tree(PAGE), None)


@pytest.mark.parametrize("dry_run", [[], ["--dry-run"]], ids=["run", "dry-run"])
@pytest.mark.parametrize("mode", MODES.values(), ids=MODES.keys())
@pytest.mark.parametrize("options,message", REFUSED.values(), ids=REFUSED.keys())
def test_a_target_conflict_is_refused_before_any_request(monkeypatch, options, message, mode, dry_run):
    double = _graph(monkeypatch)
    r = _run(double, *mode, *options, *dry_run)
    assert r.exit_code != 0
    assert message in r.stderr
    assert double.requests == []


@pytest.mark.parametrize("options,message", REFUSED.values(), ids=REFUSED.keys())
def test_a_tree_file_is_refused_the_same(monkeypatch, tmp_path, options, message):
    tree = tmp_path / "tree.txt"
    tree.write_text("new one\n", encoding="utf-8")
    double = _graph(monkeypatch)
    r = _run(double, "--tree-file", str(tree), *options)
    assert r.exit_code != 0
    assert message in r.stderr
    assert double.requests == []


@pytest.mark.parametrize("options", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_both_modes_write_to_the_same_place(monkeypatch, options):
    landed = []
    for mode in MODES.values():
        double = _graph(monkeypatch)
        r = _run(double, *mode, *options)
        assert r.exit_code == 0, r.stderr
        landed.append(_where(double))
    assert landed[0] == landed[1]


def test_a_tree_with_page_alone_goes_to_the_end_of_the_page(monkeypatch):
    double = _graph(monkeypatch)
    r = _run(double, "--tree", "new one\n\tits kid", "--page", PAGE, "--json")
    assert r.exit_code == 0, r.stderr
    assert '"position": "top-level of \'Probe Page\'"' in r.stdout
    assert double.tree(PAGE)[-1] == ("new one", [("its kid", [])])


def test_a_tree_with_page_alone_creates_a_missing_page(monkeypatch):
    double = _graph(monkeypatch)
    r = split_runner().invoke(cli, ["--token", "t", "insert-block", "--tree", "new one",
                                    "--page", "Fresh Page"])
    assert r.exit_code == 0, r.stderr
    assert double.tree("Fresh Page") == [("new one", [])]
