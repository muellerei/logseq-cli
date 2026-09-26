"""insert-block --dry-run checks its anchor, as the run does.

The preview ended before anything was read, so ``--after``, ``--before`` or
``--child-of`` with a uuid no block has previewed with exit 0 and the run
then failed. Like move-block, the preview now refuses what the run would.
"""
import pytest

from logseq_cli.cli import cli
from tests.conftest import mock_api, split_runner

KNOWN = "abcdef12-3456-7890-abcd-ef1234567890"
UNKNOWN = "00000000-0000-4000-8000-00000000dead"

ANCHORS = ["--after", "--before", "--child-of"]
SOURCES = {
    "content": ["--content", "a note"],
    "outline": ["--content", "- parent\n  - child"],
    "tree": ["--tree", '[{"content": "a note"}]'],
}


def _api(monkeypatch):
    api = mock_api()
    api.get_block.side_effect = lambda uuid, **kw: (
        {"uuid": KNOWN, "content": "anchor", "page": {"id": 7}}
        if uuid.lower() == KNOWN else None)
    monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kw: api)
    return api


def _preview(anchor, uuid, source, *extra):
    return split_runner().invoke(cli, ["--token", "t", "insert-block", anchor, uuid,
                                       *SOURCES[source], "--dry-run", *extra])


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("anchor", ANCHORS)
def test_unknown_anchor_is_refused(monkeypatch, anchor, source):
    api = _api(monkeypatch)
    result = _preview(anchor, UNKNOWN, source)
    assert result.exit_code == 1, result.stdout
    assert "not found" in result.stderr
    api.insert_block.assert_not_called()
    api.insert_batch_block.assert_not_called()


def test_unknown_anchor_under_json_names_it(monkeypatch):
    _api(monkeypatch)
    result = _preview("--after", f"(({UNKNOWN}))", "content", "--json")
    assert result.exit_code == 1
    assert '"reason": "block_not_found"' in result.stderr
    assert f'"id": "{UNKNOWN}"' in result.stderr


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("anchor", ANCHORS)
def test_known_anchor_previews(monkeypatch, anchor, source):
    _api(monkeypatch)
    result = _preview(anchor, KNOWN.upper(), source)
    assert result.exit_code == 0, result.stderr
    assert "[DRY RUN]" in result.stdout
