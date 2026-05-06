"""Tests for add-note-content --under-heading flag."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


def _build_api(existing_blocks=None, page_exists=True):
    api = MagicMock()
    api.get_page.return_value = {"name": "Foo"} if page_exists else None
    api.get_page_blocks_tree.return_value = existing_blocks or []
    api.append_block_in_page.return_value = {"uuid": "appended-uuid"}
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
        api = _build_api(existing_blocks=[
            {"content": "## Log", "uuid": "log-uuid"},
        ])
        # Need multiple uuids for nested tree
        api.insert_block.side_effect = [
            {"uuid": "parent-uuid"},
            {"uuid": "child-uuid"},
        ]
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--under-heading", "## Log",
                "--content", "- top\n\t- sub",
            ])
        assert result.exit_code == 0, result.output
        # at least 2 inserts: parent + child
        assert api.insert_block.call_count >= 2
        # first call goes under heading
        first_call = api.insert_block.call_args_list[0]
        assert first_call.args[0] == "log-uuid"
