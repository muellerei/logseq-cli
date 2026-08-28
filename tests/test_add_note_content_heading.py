"""Tests for add-note-content --under-heading flag."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from tests.conftest import fake_api
from logseq_cli.cli import cli


def _build_api(existing_blocks=None, page_exists=True, uuids=None):
    """API stand-in for the heading paths.

    ``uuids`` switches to a :class:`tests.conftest.FakeGraph` backing, needed
    whenever the content is a tree: those go out as one ``insertBatchBlock``
    call whose result is ``null``, so the write is proven by re-reading the
    parent's children - which a static return value cannot answer.
    """
    api = fake_api(uuids) if uuids else MagicMock()
    api.get_page.return_value = {"name": "Foo"} if page_exists else None
    api.get_page_blocks_tree.return_value = existing_blocks or []
    api.append_block_in_page.return_value = {"uuid": "appended-uuid"}
    if not uuids:
        api.insert_block.return_value = {"uuid": "inserted-uuid"}
    return api


class TestAddNoteContentUnderHeading:
    def test_existing_heading_appends_under_it(self):
        api = _build_api(existing_blocks=[
            {"content": "## Notes", "uuid": "heading-uuid"},
        ])
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--under-heading", "## Notes",
                "--content", "first note",
            ])
        assert result.exit_code == 0, result.output
        # insert_block must have been called with the heading UUID as parent
        any_under_heading = any(
            call.args[0] == "heading-uuid"
            for call in api.insert_block.call_args_list
        )
        assert any_under_heading, api.insert_block.call_args_list

    def test_missing_heading_creates_heading_then_appends(self):
        # First call returns no headings, then heading appears (after creation)
        api = MagicMock()
        api.get_page.return_value = {"name": "Foo"}
        api.get_page_blocks_tree.return_value = []  # no headings on first lookup
        api.append_block_in_page.return_value = {"uuid": "new-heading"}
        api.insert_block.return_value = {"uuid": "child"}
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--under-heading", "## Brand-new",
                "--content", "first content",
            ])
        assert result.exit_code == 0, result.output
        # heading creation: append_block_in_page called with heading text
        heading_calls = [
            call for call in api.append_block_in_page.call_args_list
            if call.args[1] == "## Brand-new"
        ]
        assert heading_calls, "heading was not created via append_block_in_page"
        # child insert under new heading
        any_under_heading = any(
            call.args[0] == "new-heading"
            for call in api.insert_block.call_args_list
        )
        assert any_under_heading

    def test_hierarchical_content_under_heading(self):
        api = _build_api(
            existing_blocks=[{"content": "## Log", "uuid": "log-uuid"}],
            uuids=["parent-uuid", "child-uuid"],
        )
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--under-heading", "## Log",
                "--content", "- top\n\t- sub",
            ])
        assert result.exit_code == 0, result.output
        # parent under the heading, child under the parent. Asserted on the
        # resulting shape rather than on the number of insert calls: the tree
        # goes out as one batch call, so a call count would measure transport.
        top = api.graph.children["log-uuid"]
        assert [b["content"] for b in top] == ["top"]
        assert [b["content"] for b in api.graph.children[top[0]["uuid"]]] == ["sub"]
