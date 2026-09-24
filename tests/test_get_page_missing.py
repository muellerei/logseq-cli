"""get-page must distinguish "page does not exist" from "page is empty".

Logseq's getPage returns null for a missing page but a real object for an
existing-but-empty one, so the two cases are separable at the API level. Before
this, both printed "(empty page)" and exited 0 — a caller could not tell a typo
in the page name from a page with nothing written yet.

Convention: a missing resource is an error (non-zero), an empty result is not.
Cf. POSIX grep — 1 = no lines selected (not an error), >1 = actual error.
"""
import json
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import answer_property_pulls, split_runner


@pytest.fixture
def api(monkeypatch):
    mock = answer_property_pulls(MagicMock())
    monkeypatch.setattr("logseq_cli.group.LogseqAPI", lambda **kwargs: mock)
    mock.get_page_linked_references.return_value = []
    return mock


class TestGetPageMissing:
    def test_missing_page_exits_1(self, api):
        api.get_page.return_value = None
        api.get_page_blocks_tree.return_value = None
        result = CliRunner().invoke(cli, ["get-page", "--name", "Nope"])
        assert result.exit_code == 1
        assert "(page does not exist)" in result.output

    def test_existing_but_empty_page_exits_0(self, api):
        """An empty page is a legitimate result, not an error."""
        api.get_page.return_value = {"name": "leer", "originalName": "Leer"}
        api.get_page_blocks_tree.return_value = []
        result = CliRunner().invoke(cli, ["get-page", "--name", "Leer"])
        assert result.exit_code == 0
        assert "(empty page)" in result.output
        assert "(page does not exist)" not in result.output

    def test_populated_page_exits_0(self, api):
        api.get_page.return_value = {"name": "x", "originalName": "X"}
        api.get_page_blocks_tree.return_value = [{"uuid": "00000000-0000-4000-8000-0000000000b1", "content": "hello"}]
        result = CliRunner().invoke(cli, ["get-page", "--name", "X"])
        assert result.exit_code == 0
        assert "hello" in result.output

    def test_json_marks_missing_page(self, api):
        """stdout stays parseable JSON; the error object goes to stderr."""
        api.get_page.return_value = None
        api.get_page_blocks_tree.return_value = None
        result = split_runner().invoke(cli, ["get-page", "--name", "Nope", "--json"])
        assert result.exit_code == 1

        payload = json.loads(result.stdout)
        assert payload["exists"] is False

        err = json.loads(result.stderr)
        assert err["missing"] == ["Nope"]

    def test_json_omits_exists_flag_for_present_page(self, api):
        """Existing pages keep their previous JSON shape (no noise added)."""
        api.get_page.return_value = {"name": "x", "originalName": "X"}
        api.get_page_blocks_tree.return_value = [{"uuid": "00000000-0000-4000-8000-0000000000b1", "content": "hi"}]
        result = CliRunner().invoke(cli, ["get-page", "--name", "X", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "exists" not in payload

    def test_batch_still_prints_present_pages_then_exits_1(self, api):
        """One bad name must not cost the whole batch result."""
        def get_page(name):
            return None if name == "Missing" else {"name": name, "originalName": name}

        def blocks(name):
            return None if name == "Missing" else [{"uuid": "00000000-0000-4000-8000-0000000000b1", "content": "content of " + name}]

        api.get_page.side_effect = get_page
        api.get_page_blocks_tree.side_effect = blocks

        result = CliRunner().invoke(cli, ["get-page", "--name", "Present", "--name", "Missing"])
        assert result.exit_code == 1
        assert "content of Present" in result.output       # present page still rendered
        assert "(page does not exist)" in result.output


class TestDeleteBlockAlias:
    def test_delete_block_alias_is_registered(self, api):
        """`delete-block` is the most common wrong guess; it must work."""
        api.get_block.return_value = {"uuid": "00000000-0000-4000-8000-0000000000a1", "content": "x"}
        result = CliRunner().invoke(cli, ["delete-block", "--id", "00000000-0000-4000-8000-0000000000a1", "--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output

    def test_alias_and_canonical_share_behaviour(self, api):
        api.get_block.return_value = {"uuid": "00000000-0000-4000-8000-0000000000a1", "content": "x"}
        runner = CliRunner()
        via_alias = runner.invoke(cli, ["delete-block", "--id", "00000000-0000-4000-8000-0000000000a1", "--dry-run", "--json"])
        api.reset_mock()
        api.get_block.return_value = {"uuid": "00000000-0000-4000-8000-0000000000a1", "content": "x"}
        via_canonical = runner.invoke(cli, ["remove-block", "--id", "00000000-0000-4000-8000-0000000000a1", "--dry-run", "--json"])
        assert json.loads(via_alias.stdout) == json.loads(via_canonical.stdout)


PAGE_UUID = "00000000-0000-4000-8000-0000000000f0"
FIRST_UUID = "00000000-0000-4000-8000-0000000000f1"
BODY_UUID = "00000000-0000-4000-8000-0000000000f2"


class TestGetPropertiesFallback:
    """Page properties may sit in the first block, not on the page object.

    Before #80, set-property wrote into the first block without Logseq taking
    the lines as the page's, and page_data["properties"] stayed empty.
    Reading only the page object made get-properties report "No properties"
    for them, which made set-property look like it had silently failed.
    """

    def test_falls_back_to_first_block(self, api):
        api.get_page.return_value = {"uuid": PAGE_UUID, "name": "x", "originalName": "X", "properties": {}}
        api.get_page_blocks_tree.return_value = [
            {"uuid": FIRST_UUID, "content": "type:: Person\nstatus:: Active",
             "properties": {"type": "Person", "status": "Active"}},
            {"uuid": BODY_UUID, "content": "Body", "properties": {}},
        ]
        result = CliRunner().invoke(cli, ["get-properties", "--name", "X", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["properties"] == {"type": "Person", "status": "Active"}

    def test_single_property_via_fallback(self, api):
        api.get_page.return_value = {"uuid": PAGE_UUID, "name": "x", "originalName": "X", "properties": {}}
        api.get_page_blocks_tree.return_value = [
            {"uuid": FIRST_UUID, "content": "type:: Person", "properties": {"type": "Person"}}]
        result = CliRunner().invoke(cli, ["get-properties", "--name", "X",
                                          "--property", "type"])
        assert result.exit_code == 0
        assert "Person" in result.output

    def test_page_level_properties_still_win(self, api):
        """When the page object carries them, no extra block fetch is needed."""
        api.get_page.return_value = {"uuid": PAGE_UUID, "name": "x", "originalName": "X",
                                     "properties": {"type": "Project"}}
        result = CliRunner().invoke(cli, ["get-properties", "--name", "X", "--json"])
        payload = json.loads(result.stdout)
        assert payload["properties"] == {"type": "Project"}
        api.get_page_blocks_tree.assert_not_called()

    def test_the_first_blocks_own_built_in_keys_are_not_the_pages(self, api):
        """A first block that is a heading with an ``id::`` carries ``heading``
        and ``id`` of its own, which Logseq hides from view. Reported as the
        page's they were wrong on 754 of 918 pages of a real graph (#82)."""
        api.get_page.return_value = {"uuid": PAGE_UUID, "name": "x", "originalName": "X", "properties": {}}
        api.get_page_blocks_tree.return_value = [
            {"uuid": FIRST_UUID,
             "content": f"## Heading #card\nid:: {FIRST_UUID}\ncollapsed:: true\ncard-repeats:: 1",
             "properties": {"heading": 2, "id": FIRST_UUID, "collapsed": True, "card-repeats": 1}}]
        result = CliRunner().invoke(cli, ["get-properties", "--name", "X", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["properties"] == {} and payload["text_values"] == {}
        text = CliRunner().invoke(cli, ["get-properties", "--name", "X"])
        assert "No properties" in text.output

    def test_a_user_key_next_to_them_is_still_shown(self, api):
        api.get_page.return_value = {"uuid": PAGE_UUID, "name": "x", "originalName": "X", "properties": {}}
        api.get_page_blocks_tree.return_value = [
            {"uuid": FIRST_UUID, "content": f"type:: Person\nid:: {FIRST_UUID}",
             "properties": {"type": "Person", "id": FIRST_UUID}}]
        result = CliRunner().invoke(cli, ["get-properties", "--name", "X", "--json"])
        assert json.loads(result.stdout)["properties"] == {"type": "Person"}

    def test_genuinely_empty_page_reports_none(self, api):
        api.get_page.return_value = {"uuid": PAGE_UUID, "name": "x", "originalName": "X", "properties": {}}
        api.get_page_blocks_tree.return_value = [
            {"uuid": BODY_UUID, "content": "nur Text", "properties": {}}]
        result = CliRunner().invoke(cli, ["get-properties", "--name", "X"])
        assert result.exit_code == 0
        assert "No properties" in result.output
