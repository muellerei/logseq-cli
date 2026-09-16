"""Tests for the inline --property option and UUID return on
add-note-content / insert-block (atomic capture)."""

import json
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


class TestAddNoteContentProperties:
    def test_property_under_heading_sets_on_root_and_returns_uuid(self):
        api = _build_api(existing_blocks=[
            {"content": "## Collection", "uuid": "heading-uuid"},
        ])
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--under-heading", "## Collection",
                "--content", "[Name](https://example.com): desc",
                "--property", "added=2026-06-04",
                "--property", "tags=mcp, agents",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        # both properties upserted onto the inserted root block
        calls = {(c.args[1], c.args[2]) for c in api.upsert_block_property.call_args_list}
        assert ("added", "2026-06-04") in calls
        assert ("tags", "mcp, agents") in calls  # comma survives split-on-first-'='
        for c in api.upsert_block_property.call_args_list:
            assert c.args[0] == "inserted-uuid"
        payload = json.loads(result.output)
        assert payload["uuid"] == "inserted-uuid"
        assert payload["uuids"] == ["inserted-uuid"]
        assert payload["properties"] == {"added": "2026-06-04", "tags": "mcp, agents"}

    def test_property_append_path_uses_appended_uuid(self):
        api = _build_api()
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--content", "body text",
                "--property", "type=note",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        api.upsert_block_property.assert_called_once_with("appended-uuid", "type", "note")
        payload = json.loads(result.output)
        assert payload["uuid"] == "appended-uuid"

    def test_invalid_property_fails_before_any_write(self):
        api = _build_api()
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--content", "should-not-write",
                "--property", "brokenpair",
            ])
        assert result.exit_code == 1
        assert "KEY=VALUE" in result.output
        api.append_block_in_page.assert_not_called()
        api.insert_block.assert_not_called()
        api.upsert_block_property.assert_not_called()

    def test_numeric_value_is_coerced(self):
        api = _build_api()
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "add-note-content",
                "--page", "Foo",
                "--content", "body",
                "--property", "count=5",
            ])
        assert result.exit_code == 0, result.output
        api.upsert_block_property.assert_called_once_with("appended-uuid", "count", 5)
        # value is an int, not the string "5"
        assert api.upsert_block_property.call_args.args[2] == 5
        assert isinstance(api.upsert_block_property.call_args.args[2], int)


class TestInsertBlockProperties:
    def test_property_on_appended_block(self):
        api = _build_api()
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--page", "Foo",
                "--content", "x",
                "--property", "k=v",
                "--json",
            ])
        assert result.exit_code == 0, result.output
        api.upsert_block_property.assert_called_once_with("appended-uuid", "k", "v")
        payload = json.loads(result.output)
        assert payload["uuid"] == "appended-uuid"
        assert payload["properties"] == {"k": "v"}

    def test_invalid_property_fails_before_write(self):
        api = _build_api()
        runner = CliRunner()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            result = runner.invoke(cli, [
                "insert-block",
                "--page", "Foo",
                "--content", "x",
                "--property", "nopair",
            ])
        assert result.exit_code == 1
        assert "KEY=VALUE" in result.output
        api.append_block_in_page.assert_not_called()
        api.upsert_block_property.assert_not_called()


# ---------- update-block keeps block properties ----------------------------

class TestUpdateBlockKeepsProperties:
    """Block properties live inside the block content, so replacing the text
    used to drop them silently. `updateBlock` takes them back via its third
    parameter, which is what this guards."""

    def _api(self, properties):
        api = MagicMock()
        api.get_block.return_value = {
            "uuid": "u-1", "content": "old", "properties": properties}
        return api

    def test_properties_are_passed_back(self):
        api = self._api({"ticket": "ISSUE-42", "owner": ["Bob"]})
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--id", "u-1", "--content", "new"])
        assert r.exit_code == 0, r.output
        api.update_block.assert_called_once_with(
            "u-1", "new", properties={"ticket": "ISSUE-42", "owner": ["Bob"]})

    def test_block_without_properties_passes_none(self):
        api = self._api({})
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--id", "u-1", "--content", "new"])
        assert r.exit_code == 0, r.output
        assert api.update_block.call_args.kwargs["properties"] == {}

    def test_dry_run_names_what_it_keeps(self):
        api = self._api({"ticket": "ISSUE-42"})
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "update-block", "--id", "u-1", "--content", "new", "--dry-run"])
        assert r.exit_code == 0, r.output
        assert "ticket::" in r.output
        api.update_block.assert_not_called()

    def test_api_omits_the_option_when_there_is_nothing_to_keep(self):
        """No properties -> plain two-arg call, same as before."""
        from logseq_cli.api import LogseqAPI
        api = LogseqAPI(token="t")
        with patch.object(api, "call") as call:
            api.update_block("u-1", "text")
            assert call.call_args.args[1] == ["u-1", "text"]
            api.update_block("u-1", "text", properties={"a": 1})
            assert call.call_args.args[1] == ["u-1", "text", {"properties": {"a": 1}}]


class TestRemovePropertyById:
    """remove-property was wired to blocks[0], so a property on any other block
    could not be removed at all."""

    def test_id_targets_that_block(self):
        api = MagicMock()
        api.get_block.return_value = {"uuid": "b-9", "content": "x"}
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "remove-property", "--id", "b-9", "--key", "prio"])
        assert r.exit_code == 0, r.output
        api.remove_block_property.assert_called_once_with("b-9", "prio")
        api.get_page_blocks_tree.assert_not_called()

    def test_page_path_still_uses_the_first_block(self):
        api = MagicMock()
        api.get_page_blocks_tree.return_value = [{"uuid": "first"}]
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "remove-property", "--name", "P", "--key", "type"])
        assert r.exit_code == 0, r.output
        api.remove_block_property.assert_called_once_with("first", "type")

    def test_exactly_one_selector(self):
        api = MagicMock()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            both = CliRunner().invoke(cli, [
                "remove-property", "--name", "P", "--id", "b", "--key", "k"])
            neither = CliRunner().invoke(cli, ["remove-property", "--key", "k"])
        for r in (both, neither):
            assert r.exit_code == 1
            assert "exactly one of" in r.output
        api.remove_block_property.assert_not_called()

    def test_missing_block_aborts(self):
        api = MagicMock()
        api.get_block.return_value = None
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "remove-property", "--id", "nope", "--key", "k"])
        assert r.exit_code == 1
        api.remove_block_property.assert_not_called()
