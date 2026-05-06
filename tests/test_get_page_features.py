"""Tests for get-page command flags: --resolve-refs, --with-ids."""

from unittest.mock import MagicMock, patch
from click.testing import CliRunner

from logseq_cli.cli import cli


def _api_with_blocks(blocks, ref_block=None):
    """Build a mocked LogseqAPI returning fixed blocks and (optionally) a ref block."""
    api = MagicMock()
    api.get_page_blocks_tree.return_value = blocks
    api.get_page_linked_references.return_value = []
    if ref_block is not None:
        api.get_block.return_value = ref_block
    return api


class TestGetPageResolveRefs:
    """Verify --resolve-refs inlines ((uuid)) into block content."""

    def test_without_flag_keeps_uuid_ref(self):
        blocks = [
            {"content": "see ((11111111-2222-3333-4444-555555555555)) here",
             "uuid": "block-1", "children": []}
        ]
        api = _api_with_blocks(blocks)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-page", "--name", "Foo", "--no-backlinks"])
        assert result.exit_code == 0, result.output
        assert "((11111111-2222-3333-4444-555555555555))" in result.output

    def test_with_flag_inlines_referenced_content(self):
        target_uuid = "11111111-2222-3333-4444-555555555555"
        blocks = [
            {"content": f"see (({target_uuid})) here",
             "uuid": "block-1", "children": []}
        ]
        ref_block = {
            "content": "RESOLVED CONTENT",
            "page": {"originalName": "OtherPage"},
        }
        api = _api_with_blocks(blocks, ref_block=ref_block)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks", "--resolve-refs"]
            )
        assert result.exit_code == 0, result.output
        assert "RESOLVED CONTENT" in result.output
        assert "OtherPage" in result.output
        assert f"(({target_uuid}))" not in result.output

    def test_resolve_refs_in_nested_children(self):
        target_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        blocks = [
            {"content": "parent", "uuid": "p1", "children": [
                {"content": f"child sees (({target_uuid}))",
                 "uuid": "c1", "children": []}
            ]}
        ]
        ref_block = {
            "content": "DEEP",
            "page": {"originalName": "Z"},
        }
        api = _api_with_blocks(blocks, ref_block=ref_block)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks", "--resolve-refs"]
            )
        assert result.exit_code == 0, result.output
        assert "DEEP" in result.output
        assert f"(({target_uuid}))" not in result.output


class TestGetPageWithIds:
    """Verify --with-ids prefixes each block line with its UUID."""

    def test_without_flag_no_uuid_prefix(self):
        blocks = [
            {"content": "first", "uuid": "uuid-aaa", "children": [
                {"content": "child", "uuid": "uuid-bbb", "children": []}
            ]}
        ]
        api = _api_with_blocks(blocks)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(cli, ["get-page", "--name", "Foo", "--no-backlinks"])
        assert result.exit_code == 0, result.output
        assert "uuid-aaa" not in result.output
        assert "uuid-bbb" not in result.output
        assert "first" in result.output

    def test_with_flag_prefixes_uuid(self):
        blocks = [
            {"content": "first", "uuid": "uuid-aaa", "children": [
                {"content": "child", "uuid": "uuid-bbb", "children": []}
            ]}
        ]
        api = _api_with_blocks(blocks)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks", "--with-ids"]
            )
        assert result.exit_code == 0, result.output
        assert "uuid-aaa" in result.output
        assert "uuid-bbb" in result.output
        # Format expected: <uuid>\t<indent>\t<content>
        assert "uuid-aaa\t" in result.output
        assert "first" in result.output

    def test_with_ids_combines_with_resolve_refs(self):
        target_uuid = "11111111-2222-3333-4444-555555555555"
        blocks = [
            {"content": f"see (({target_uuid}))",
             "uuid": "outer-uuid", "children": []}
        ]
        ref_block = {
            "content": "INNER",
            "page": {"originalName": "OtherPage"},
        }
        api = _api_with_blocks(blocks, ref_block=ref_block)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks",
                      "--with-ids", "--resolve-refs"]
            )
        assert result.exit_code == 0, result.output
        assert "outer-uuid" in result.output
        assert "INNER" in result.output
        assert f"(({target_uuid}))" not in result.output
