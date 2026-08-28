"""A failed write must never be reported as success.

Logseq answers a failed insert/append with HTTP 200 + ``null`` rather than an
error status, so a missing UUID is the only failure signal there is. Before
this, ``insert_block_tree_with_uuids`` defaulted to ``strict=False``: it pushed
a ``None`` UUID, skipped that block's children, and the caller printed
"Added N block(s)" with exit 0 — for content that was never written. On a
journal entry that means the text is gone and nothing says so.

Regression guard for that whole class of failure, not just one command.
"""
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from tests.conftest import fake_api
from logseq_cli.cli import cli
from logseq_cli.helpers import (
    insert_block_tree_with_uuids,
    insert_block_tree_at_page_top,
)


TREE = [{"content": "Kopf", "children": [{"content": "Detail"}]}]


class TestHelperDefaults:
    def test_strict_is_the_default(self):
        """The unsafe mode must be opt-in, never the default."""
        api = fake_api([], fail_after=0)  # writes nothing, reports nothing
        with pytest.raises(Exception) as exc:
            insert_block_tree_with_uuids(api, TREE, "parent")
        assert "wrote 0 of 2 block(s)" in str(exc.value)

    def test_single_block_failure_uses_the_per_block_path(self):
        """One block needs no batch call, so the UUID check still applies."""
        api = MagicMock()
        api.insert_block.return_value = None
        with pytest.raises(Exception) as exc:
            insert_block_tree_with_uuids(api, [{"content": "solo"}], "parent")
        assert "did not create" in str(exc.value)

    def test_batch_partial_write_is_detected_and_named(self):
        """A batch can write part of its nodes; the count check must catch it."""
        api = fake_api(["u1", "u2"], fail_after=1)
        with pytest.raises(Exception) as exc:
            insert_block_tree_with_uuids(api, TREE, "parent")
        msg = str(exc.value)
        assert "wrote 1 of 2 block(s)" in msg
        assert "duplicate" in msg

    def test_strict_false_still_tolerates_partial_writes(self):
        """Opt-out stays available for callers that deliberately want it."""
        api = MagicMock()
        api.insert_block.return_value = None
        uuids = insert_block_tree_with_uuids(api, TREE, "parent", strict=False)
        assert uuids == [None]

    def test_successful_write_returns_uuids_in_dfs_order(self):
        api = fake_api(["u-kopf", "u-detail"])
        assert insert_block_tree_with_uuids(api, TREE, "parent") == ["u-kopf", "u-detail"]

    def test_page_top_insert_aborts_on_failed_append(self):
        api = MagicMock()
        api.append_block_in_page.return_value = None
        with pytest.raises(Exception) as exc:
            insert_block_tree_at_page_top(api, TREE, "Seite")
        assert "did not create" in str(exc.value)

    def test_page_top_insert_succeeds_normally(self):
        api = MagicMock()
        api.append_block_in_page.return_value = {"uuid": "u-top"}
        api.insert_block.return_value = {"uuid": "u-child"}
        assert insert_block_tree_at_page_top(api, TREE, "Seite") == ["u-top", "u-child"]


@pytest.fixture
def api(monkeypatch):
    """Journal page with an empty '## Log' heading, backed by FakeGraph.

    The batch write path proves its write by re-reading the parent's children,
    which a bare MagicMock cannot answer meaningfully.
    """
    mock = fake_api([f"u{i}" for i in range(1, 40)])
    monkeypatch.setattr("logseq_cli.cli.LogseqAPI", lambda **kwargs: mock)
    mock.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    mock.get_page.return_value = {"name": "journal"}
    mock.get_page_blocks_tree.return_value = [
        {"uuid": "head", "content": "## Log", "children": []}]
    return mock


class TestCommandsSurfaceTheFailure:
    def test_add_journal_block_fails_loudly(self, api):
        api.graph.set_fail_after(0)
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content", "**09:00** Kopf\n\t- Detail"])
        assert result.exit_code == 1
        assert "wrote 0 of 2 block(s)" in result.output
        assert "Added" not in result.output

    def test_add_journal_block_succeeds_when_writes_land(self, api):
        result = CliRunner().invoke(cli, [
            "add-journal-block", "--under-heading", "## Log",
            "--content", "**09:00** Kopf\n\t- Detail"])
        assert result.exit_code == 0
        assert "Added 2 block(s)" in result.output

    def test_add_note_content_fails_loudly(self, api):
        """Uses the heading the fixture already provides, so the failure comes
        from the block insert itself rather than from heading creation."""
        api.get_page.return_value = {"name": "Seite"}
        api.graph.set_fail_after(0)
        result = CliRunner().invoke(cli, [
            "add-note-content", "--page", "Seite",
            "--under-heading", "## Log", "--content", "Kopf\n\t- Detail"])
        assert result.exit_code == 1
        assert "wrote 0 of 2 block(s)" in result.output
        assert "Added" not in result.output
