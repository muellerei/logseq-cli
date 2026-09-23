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

    def test_silent_no_op_is_reported(self):
        """Logseq answers null whether or not it moved; the re-read decides."""
        api = _api(children_after=[])  # source never shows up under the target
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "move-block", "--id", SRC, "--under", TGT])
        assert r.exit_code == 1
        assert "did not take effect" in r.output

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


class _Graph:
    """Pages and blocks that answer the way Logseq 0.10.15 was measured to.

    - ``getBlock`` knows blocks only: a page's numeric id answers ``null``,
      although a top-level block reports that very id as its ``parent``.
    - ``getPage`` accepts the numeric id; ``getPageBlocksTree`` wants the name.
    - ``moveBlock`` answers ``null`` always, and does nothing when the target
      lies in the source's own subtree.

    ``_api`` answers ``getBlock`` for any parent id, page or block,
    which is how the top-level failure of #23 got past the suite.
    """

    def __init__(self, pages):
        # pages: {name: [(uuid, [children...]), ...]}, children nest the same way
        self.page_ids = {}
        self.pages = {}
        self.nodes = {}  # uuid -> {"id", "uuid", "page", "children": [uuids]}
        self.next_id = 100
        for name, roots in pages.items():
            pid = self._id()
            self.page_ids[name] = pid
            self.pages[pid] = {"name": name, "children": []}
            for root in roots:
                self._add(root, pid, pid)
        self.moves = []

    def _id(self):
        self.next_id += 1
        return self.next_id

    def _add(self, spec, parent_id, page_id):
        uuid, kids = spec
        node = {"id": self._id(), "uuid": uuid, "page": page_id,
                "parent": parent_id, "children": []}
        self.nodes[uuid] = node
        self._kids(parent_id).append(uuid)
        for kid in kids:
            self._add(kid, node["id"], page_id)

    def _kids(self, parent_id):
        if parent_id in self.pages:
            return self.pages[parent_id]["children"]
        return next(n for n in self.nodes.values() if n["id"] == parent_id)["children"]

    def _subtree(self, uuid):
        out = {uuid}
        for kid in self.nodes[uuid]["children"]:
            out |= self._subtree(kid)
        return out

    def _render(self, uuid, include_children):
        n = self.nodes[uuid]
        block = {"id": n["id"], "uuid": uuid, "content": uuid,
                 "parent": {"id": n["parent"]}, "page": {"id": n["page"]}}
        if include_children:
            block["children"] = [self._render(k, True) for k in n["children"]]
        else:
            block["children"] = [["uuid", k] for k in n["children"]]
        return block

    def get_block(self, ref, include_children=True):
        if isinstance(ref, int):
            uuid = next((u for u, n in self.nodes.items() if n["id"] == ref), None)
        else:
            uuid = ref if ref in self.nodes else None
        return self._render(uuid, include_children) if uuid else None

    def get_page(self, ref):
        pid = ref if isinstance(ref, int) else self.page_ids.get(str(ref).lower())
        page = self.pages.get(pid)
        return {"id": pid, "name": page["name"], "originalName": page["name"]} if page else None

    def get_page_blocks_tree(self, name):
        if not isinstance(name, str):
            return {"error": "Expected string, got: number"}
        pid = self.page_ids.get(name.lower())
        return [self._render(u, True) for u in self.pages[pid]["children"]] if pid else None

    def move_block(self, src, target, options=None):
        self.moves.append((src, target, options))
        if target in self._subtree(src):
            return None  # refused, silently
        node, tgt = self.nodes[src], self.nodes[target]
        self._kids(node["parent"]).remove(src)
        if (options or {}).get("before"):
            siblings = self._kids(tgt["parent"])
            siblings.insert(siblings.index(target), src)
            new_parent = tgt["parent"]
        else:
            tgt["children"].insert(0, src)
            new_parent = tgt["id"]
        node["parent"] = new_parent
        for u in self._subtree(src):
            self.nodes[u]["page"] = tgt["page"]
        return None

    def order(self, name):
        return list(self.pages[self.page_ids[name]]["children"])


A = "00000000-0000-4000-8000-00000000000a"
B = "00000000-0000-4000-8000-00000000000b"
C = "00000000-0000-4000-8000-00000000000c"
A1 = "00000000-0000-4000-8000-0000000000a1"
X = "00000000-0000-4000-8000-00000000000f"


def _run(graph, *args):
    with patch("logseq_cli.group.LogseqAPI", return_value=graph):
        return CliRunner().invoke(cli, ["move-block", *args])


class TestMoveBeforeTopLevel:
    """#23: a top-level target's parent is its page, which getBlock cannot read."""

    def test_top_level_before_top_level(self):
        g = _Graph({"page a": [(A, []), (B, []), (C, [])]})
        r = _run(g, "--id", C, "--before", A)
        assert r.exit_code == 0, r.output
        assert g.order("page a") == [C, A, B]

    def test_nested_block_before_top_level(self):
        g = _Graph({"page a": [(A, [(A1, [])]), (B, [])]})
        r = _run(g, "--id", A1, "--before", B)
        assert r.exit_code == 0, r.output
        assert g.order("page a") == [A, A1, B]

    def test_across_pages_before_top_level(self):
        g = _Graph({"page a": [(A, []), (B, [])], "page b": [(X, [])]})
        r = _run(g, "--id", X, "--before", B)
        assert r.exit_code == 0, r.output
        assert g.order("page a") == [A, X, B]
        assert g.order("page b") == []

    def test_top_level_move_that_did_nothing_still_fails(self):
        """The fix must read the real order, not wave every top-level move through."""
        g = _Graph({"page a": [(A, []), (B, [])]})
        g.move_block = lambda *a, **k: None  # Logseq did nothing
        r = _run(g, "--id", B, "--before", A)
        assert r.exit_code == 1
        assert "did not take effect" in r.output


class TestSubtreeTargetIsRefusedUpFront:
    """Logseq refuses a move into the source's own subtree by doing nothing.

    That is checked before the move, so the message can name the real cause,
    and a failure after the move no longer guesses at it.
    """

    def test_before_own_descendant(self):
        g = _Graph({"page a": [(A, [(B, [])])]})
        r = _run(g, "--id", A, "--before", B)
        assert r.exit_code == 1
        assert "own subtree" in r.output
        assert g.moves == []

    def test_under_own_grandchild(self):
        g = _Graph({"page a": [(A, [(A1, [(B, [])])])]})
        r = _run(g, "--id", A, "--under", B)
        assert r.exit_code == 1
        assert "own subtree" in r.output
        assert g.moves == []

    def test_failure_after_the_move_does_not_name_the_subtree(self):
        g = _Graph({"page a": [(A, []), (B, [])]})
        g.move_block = lambda *a, **k: None
        r = _run(g, "--id", A, "--under", B)
        assert r.exit_code == 1
        assert "did not take effect" in r.output
        assert "subtree" not in r.output
