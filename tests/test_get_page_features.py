"""Tests for get-page command flags: --resolve-refs, --with-ids."""

from unittest.mock import MagicMock, patch
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


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


class TestGetPageHeading:
    """Verify --heading uses tolerant matching (renderer macros, whitespace)."""

    def test_heading_matches_despite_renderer_suffix(self):
        # Real-world: journal heading is stored as "## Tasks {{renderer :todomaster}}"
        # but user queries with the bare "## Tasks".
        blocks = [
            {"content": "## Tasks {{renderer :todomaster}}",
             "uuid": "h-uuid",
             "children": [
                 {"content": "TODO Sub-Task", "uuid": "t-uuid", "children": []}
             ]},
            {"content": "## Log", "uuid": "log-uuid", "children": [
                {"content": "log-entry", "uuid": "l-uuid", "children": []}
            ]},
        ]
        api = _api_with_blocks(blocks)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-page", "--name", "Journal", "--heading", "## Tasks"]
            )
        assert result.exit_code == 0, result.output
        assert "TODO Sub-Task" in result.output
        # The Log section must NOT leak into the output
        assert "log-entry" not in result.output

    def test_heading_matches_with_extra_whitespace(self):
        blocks = [
            {"content": "##   Meeting   ", "uuid": "h", "children": [
                {"content": "Notes", "uuid": "n", "children": []}
            ]},
        ]
        api = _api_with_blocks(blocks)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-page", "--name", "X", "--heading", "## Meeting"]
            )
        assert result.exit_code == 0, result.output
        assert "Notes" in result.output

    def test_heading_not_found_emits_warning(self):
        blocks = [{"content": "## Other", "uuid": "h", "children": []}]
        api = _api_with_blocks(blocks)
        runner = CliRunner()
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = runner.invoke(
                cli, ["get-page", "--name", "X", "--heading", "## Tasks"]
            )
        assert result.exit_code == 0, result.output
        # Either stderr (mix_stderr default) or stdout carries a not-found warning.
        combined = result.output.lower()
        assert "not found" in combined or "warning" in combined


class TestGetPageUnresolvedRefWarning:
    """A ((uuid)) left in the output is dead weight for the caller.

    get-journal-range has warned about this since the flag existed; get-page
    stayed silent, so the same page read through two commands gave two
    different answers about whether the output was complete.
    """

    UUID = "11111111-2222-3333-4444-555555555555"

    def test_warns_on_stderr_when_flag_is_missing(self):
        blocks = [{"content": f"see (({self.UUID})) here", "uuid": "b1", "children": []}]
        api = _api_with_blocks(blocks)
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks"])
        assert result.exit_code == 0, result.output
        assert "1 unresolved block-ref" in result.stderr
        assert "--resolve-refs" in result.stderr

    def test_counts_refs_in_children_too(self):
        blocks = [{
            "content": f"parent (({self.UUID}))", "uuid": "b1",
            "children": [{"content": f"child (({self.UUID}))", "uuid": "b2", "children": []}],
        }]
        api = _api_with_blocks(blocks)
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks"])
        assert result.exit_code == 0, result.output
        assert "2 unresolved block-ref" in result.stderr

    def test_silent_when_flag_resolves_them(self):
        blocks = [{"content": f"see (({self.UUID})) here", "uuid": "b1", "children": []}]
        ref = {"content": "the target", "page": {"originalName": "Src"}}
        api = _api_with_blocks(blocks, ref_block=ref)
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks", "--resolve-refs"])
        assert result.exit_code == 0, result.output
        assert "unresolved" not in result.stderr

    def test_silent_when_page_has_no_refs(self):
        blocks = [{"content": "plain text", "uuid": "b1", "children": []}]
        api = _api_with_blocks(blocks)
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks"])
        assert result.exit_code == 0, result.output
        assert "unresolved" not in result.stderr

    def test_warning_does_not_pollute_json_payload(self):
        """stdout must stay parseable; the warning belongs on stderr."""
        import json
        blocks = [{"content": f"see (({self.UUID})) here", "uuid": "b1", "children": []}]
        api = _api_with_blocks(blocks)
        with patch("logseq_cli.cli.LogseqAPI", return_value=api):
            result = split_runner().invoke(
                cli, ["get-page", "--name", "Foo", "--no-backlinks", "--json"])
        assert result.exit_code == 0, result.output
        json.loads(result.stdout)
        assert "unresolved" in result.stderr
