"""move-block: structural move, and copy-block --remove no longer risks the source.

Two distinct problems:

1. ``copy-block --remove`` wrote the copy without checking the result. Logseq
   answers a failed write with HTTP 200 + null, so a copy that never landed was
   reported as "Moved 1 block(s)" with exit 0 - and the source was deleted
   anyway. That destroys the block.
2. Even when it works, copy+remove writes a NEW block: the UUID changes and
   every ``((block-ref))`` pointing at the original goes dead. ``moveBlock``
   moves the block itself, so refs survive.

``moveBlock`` answers null for success, for a missing target AND for a refused
move (Logseq declines to move a block into its own subtree by doing nothing), so
every move is verified by re-reading.
"""
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


SRC = "s-uuid"
TGT = "t-uuid"


def _api(*, children_after=None, target_parent=1, src_parent_after=None,
         sibling_order=None):
    """API stand-in for one move.

    ``children_after`` is what the target reports as its children afterwards;
    ``sibling_order`` what the target's parent reports, for the --before case.
    """
    api = MagicMock()
    blocks = {
        SRC: {"uuid": SRC, "content": "QUELLE", "children": [],
              "parent": {"id": src_parent_after if src_parent_after is not None else 9}},
        TGT: {"uuid": TGT, "content": "TARGET", "parent": {"id": target_parent},
              "children": children_after if children_after is not None else []},
        target_parent: {"uuid": "p-uuid", "children": [
            {"uuid": u} for u in (sibling_order or [])]},
    }

    def _get_block(uuid, include_children=True):
        return blocks.get(uuid)

    api.get_block.side_effect = _get_block
    return api


class TestMoveBlock:
    def test_under_moves_and_reports(self):
        api = _api(children_after=[{"uuid": SRC}])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--under", TGT])
        assert r.exit_code == 0, r.output
        assert "Moved 1 block(s) under" in r.output
        api.move_block.assert_called_once_with(SRC, TGT, {"children": True})

    def test_before_moves_as_sibling(self):
        api = _api(sibling_order=[SRC, TGT])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--before", TGT])
        assert r.exit_code == 0, r.output
        assert "Moved 1 block(s) before" in r.output
        api.move_block.assert_called_once_with(SRC, TGT, {"before": True})

    def test_before_requires_source_directly_in_front(self):
        """Same parent is not enough: a move that did nothing must not pass."""
        api = _api(sibling_order=[TGT, SRC])  # source lands AFTER the target
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--before", TGT])
        assert r.exit_code == 1
        assert "did not take effect" in r.output

    def test_silent_refusal_is_reported(self):
        """Moving into the block's own subtree: Logseq just does nothing."""
        api = _api(children_after=[])  # source never shows up under the target
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--under", TGT])
        assert r.exit_code == 1
        assert "did not take effect" in r.output
        assert "own subtree" in r.output

    def test_missing_target_aborts_before_moving(self):
        api = _api()
        api.get_block.side_effect = lambda uuid, include_children=True: (
            {"uuid": SRC, "content": "x", "children": []} if uuid == SRC else None)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--under", "nope"])
        assert r.exit_code == 1
        assert "not found" in r.output
        api.move_block.assert_not_called()

    def test_same_block_is_rejected(self):
        api = _api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--before", SRC])
        assert r.exit_code == 1
        assert "same block" in r.output
        api.move_block.assert_not_called()

    def test_exactly_one_position_flag(self):
        api = _api()
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            both = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--under", TGT, "--before", TGT])
            neither = CliRunner().invoke(cli, ["move-block", "--id", SRC])
        for r in (both, neither):
            assert r.exit_code == 1
            assert "exactly one of" in r.output
        api.move_block.assert_not_called()

    def test_dry_run_writes_nothing(self):
        api = _api(children_after=[{"uuid": SRC}])
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--under", TGT, "--dry-run"])
        assert r.exit_code == 0, r.output
        assert "[DRY RUN]" in r.output
        api.move_block.assert_not_called()


class TestCopyBlockRemoveIsGuarded:
    """Regression: a failed copy must never take the source with it."""

    def test_failed_copy_does_not_remove_source(self):
        api = MagicMock()
        api.get_block.return_value = {"uuid": SRC, "content": "WICHTIG", "children": []}
        api.append_block_in_page.return_value = None  # silent write failure
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "copy-block", "--id", SRC, "--to-page", "Target", "--remove"])
        assert r.exit_code == 1
        assert "Moved" not in r.output
        api.remove_block.assert_not_called()

    def test_failed_child_copy_does_not_remove_source(self):
        """The root lands, a child does not: still no removal."""
        api = MagicMock()
        api.get_block.return_value = {
            "uuid": SRC, "content": "Head",
            "children": [{"content": "Child", "children": []}]}
        api.append_block_in_page.return_value = {"uuid": "new-root"}
        api.insert_block.return_value = None
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "copy-block", "--id", SRC, "--to-page", "Target", "--remove"])
        assert r.exit_code == 1
        api.remove_block.assert_not_called()

    def test_successful_copy_still_removes(self):
        api = MagicMock()
        api.get_block.return_value = {"uuid": SRC, "content": "Head", "children": []}
        api.append_block_in_page.return_value = {"uuid": "new-root"}
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "copy-block", "--id", SRC, "--to-page", "Target", "--remove"])
        assert r.exit_code == 0, r.output
        api.remove_block.assert_called_once_with(SRC)
