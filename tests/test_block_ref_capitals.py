"""A ref in capitals is a ref: Logseq reads ((UUID)) as one (measured,
0.10.15: the target got its id:: line as for any ref, #95), and getBlock finds
a block by its uuid in capitals (measured, #70). The reads that resolve and
count refs took only lower case and left such a ref as an opaque hole.
"""
from unittest.mock import MagicMock, patch

from logseq_cli.cli import cli
from logseq_cli.render import count_unresolved_refs
from tests.conftest import split_runner

LIVE = "8f2a3b4c-5d6e-4f70-8a9b-0c1d2e3f4a81"


def test_resolve_refs_reads_a_ref_in_capitals():
    api = MagicMock()
    api.get_page.return_value = {"name": "page"}
    api.get_page_linked_references.return_value = []
    api.get_page_blocks_tree.return_value = [
        {"uuid": "b1", "content": f"see (({LIVE.upper()}))", "children": []}]
    api.get_block.side_effect = lambda uuid, include_children=True: (
        {"uuid": LIVE, "content": "the live one", "page": {"originalName": "Source"}}
        if uuid.lower() == LIVE else None)
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        result = split_runner().invoke(cli, ["get-page", "--page", "Page", "--resolve-refs"])
    assert result.exit_code == 0, result.stderr
    assert "see the live one ↳ Source" in result.stdout


def test_a_ref_in_capitals_is_counted_unresolved():
    assert count_unresolved_refs([{"content": f"(({LIVE.upper()}))", "children": []}]) == 1
