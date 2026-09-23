"""#24: deleting blocks checks for ((block-refs)) from outside the deleted set.

A ``((uuid))`` into a deleted block dies silently, and the damage lands on
another page than the one being changed. ``delete-page``, ``remove-block`` and
``copy-block --remove`` count incoming refs first and refuse unless
``--ignore-refs`` is given. ``--force`` does not override it: a caller that
deletes pages routinely passes ``--force`` every time.

Measured against Logseq 0.10.15: ``:block/refs`` holds plain ``((uuid))``,
``{{embed ((uuid))}}``, ``[label](((uuid)))`` and a ``rel:: ((uuid))``
property value alike, so one relation answers for all of them.
"""
import json
import re
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli
from logseq_cli.helpers import incoming_block_refs


ROOT = "00000000-0000-4000-8000-000000000001"
CHILD = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"   # same page, not deleted
REF_OUT = "00000000-0000-4000-8000-0000000000f1"  # on another page
REF_IN = "00000000-0000-4000-8000-0000000000f2"   # inside the deleted subtree


class _RefIndex:
    """Answers the ref query from a table of blocks, like ``:block/refs`` does.

    ``blocks`` maps uuid -> (page name, [uuids it references]). The query is
    read for what it asks (a set of target uuids, or a page), so a caller that
    forgets the descendants gets no answer for them, as from the real graph.
    """

    def __init__(self, blocks):
        self.blocks = blocks
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        if "contains?" in query:
            targets = set(re.findall(r'#uuid "([0-9a-f-]+)"', query))
        else:
            page = re.search(r':block/name "([^"]+)"', query).group(1)
            targets = {u for u, (p, _) in self.blocks.items() if p.lower() == page}
        rows = []
        for src, (page, refs) in self.blocks.items():
            for target in refs:
                if target in targets:
                    rows.append([target, {"uuid": src, "page": {
                        "original-name": page, "name": page.lower()}}])
        return rows


def _graph(refs_from_outside=True, refs_from_inside=True):
    blocks = {
        ROOT: ("Target", []),
        CHILD: ("Target", []),
        OTHER: ("Target", [CHILD] if refs_from_inside else []),
        REF_IN: ("Target", [CHILD] if refs_from_inside else []),
    }
    if refs_from_outside:
        blocks[REF_OUT] = ("Journal Day", [CHILD])
    return blocks


def _api(blocks):
    api = MagicMock()
    api.datascript_query.side_effect = _RefIndex(blocks)
    subtree = {"uuid": ROOT, "content": "root", "children": [
        {"uuid": CHILD, "content": "child", "children": [
            {"uuid": REF_IN, "content": "grandchild", "children": []}]}]}
    api.get_block.side_effect = lambda u, include_children=True: (
        subtree if u == ROOT else None)
    api.get_page.return_value = {"name": "target", "originalName": "Target"}
    api.get_page_blocks_tree.return_value = [subtree]
    api.append_block_in_page.return_value = {"uuid": "00000000-0000-4000-8000-0000000000c1"}
    api.insert_block.return_value = {"uuid": "00000000-0000-4000-8000-0000000000c2"}
    return api


def _run(api, *args):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return CliRunner().invoke(cli, list(args))


class TestIncomingBlockRefs:
    def test_counts_refs_into_descendants(self):
        api = _api(_graph(refs_from_inside=False))
        refs = incoming_block_refs(api, uuids=[ROOT, CHILD, REF_IN])
        assert [(r["target"], r["block"], r["page"]) for r in refs] == [
            (CHILD, REF_OUT, "Journal Day")]

    def test_refs_from_inside_the_deleted_set_do_not_count(self):
        api = _api(_graph(refs_from_outside=False))
        assert incoming_block_refs(api, uuids=[ROOT, CHILD, REF_IN]) == [
            {"target": CHILD, "block": OTHER, "page": "Target"}]

    def test_by_page_ignores_refs_from_the_same_page(self):
        api = _api(_graph())
        refs = incoming_block_refs(api, page="target")
        assert [(r["target"], r["block"]) for r in refs] == [(CHILD, REF_OUT)]

    def test_same_page_is_judged_by_the_stored_name(self):
        """original-name keeps the spelling it was created with (here NFD);
        :block/name is what the query matched, so it decides "same page"."""
        api = MagicMock()
        api.datascript_query.return_value = [[CHILD, {"uuid": OTHER, "page": {
            "original-name": "Mu\u0308ller", "name": "m\u00fcller"}}]]
        assert incoming_block_refs(api, page="m\u00fcller") == []

    def test_count_inside_keeps_refs_from_within_the_set(self):
        api = _api(_graph(refs_from_outside=False))
        refs = incoming_block_refs(api, uuids=[ROOT, CHILD, REF_IN], count_inside=True)
        assert {r["block"] for r in refs} == {OTHER, REF_IN}

    def test_a_page_entity_as_source_is_its_own_page(self):
        """Not seen live; if a page carries refs itself, it must still count."""
        api = MagicMock()
        api.datascript_query.return_value = [
            [CHILD, {"uuid": REF_OUT, "name": "notes", "original-name": "Notes"}],
            [CHILD, {"uuid": OTHER, "name": "target", "original-name": "Target"}],
        ]
        assert incoming_block_refs(api, uuids=[CHILD]) == [
            {"target": CHILD, "block": REF_OUT, "page": "Notes"},
            {"target": CHILD, "block": OTHER, "page": "Target"}]
        assert incoming_block_refs(api, page="target") == [
            {"target": CHILD, "block": REF_OUT, "page": "Notes"}]


class TestRemoveBlock:
    def test_refuses_with_incoming_refs(self):
        api = _api(_graph())
        r = _run(api, "remove-block", "--id", ROOT)
        assert r.exit_code == 1
        assert "Journal Day" in r.output
        assert "--ignore-refs" in r.output
        api.remove_block.assert_not_called()

    def test_ignore_refs_removes_and_says_what_broke(self):
        api = _api(_graph())
        r = _run(api, "remove-block", "--id", ROOT, "--ignore-refs")
        assert r.exit_code == 0, r.output
        api.remove_block.assert_called_once_with(ROOT)
        assert "2 incoming block ref(s)" in r.output

    def test_no_refs_removes_as_before(self):
        api = _api(_graph(refs_from_outside=False, refs_from_inside=False))
        r = _run(api, "remove-block", "--id", ROOT)
        assert r.exit_code == 0, r.output
        api.remove_block.assert_called_once_with(ROOT)

    def test_dry_run_predicts_the_refusal(self):
        api = _api(_graph())
        r = _run(api, "remove-block", "--id", ROOT, "--dry-run")
        assert r.exit_code == 1
        assert "Journal Day" in r.output
        api.remove_block.assert_not_called()

    def test_json_refusal_is_an_object_with_the_refs(self):
        api = _api(_graph())
        r = _run(api, "remove-block", "--id", ROOT, "--json")
        assert r.exit_code == 1
        payload = json.loads(r.stderr)
        assert {x["block"] for x in payload["refs"]} == {OTHER, REF_OUT}


class TestDeletePage:
    def test_force_does_not_override_the_ref_check(self):
        api = _api(_graph())
        r = _run(api, "delete-page", "--name", "Target", "--force")
        assert r.exit_code == 1
        assert "Journal Day" in r.output
        api.delete_page.assert_not_called()

    def test_ignore_refs_deletes(self):
        api = _api(_graph())
        r = _run(api, "delete-page", "--name", "Target", "--force", "--ignore-refs")
        assert r.exit_code == 0, r.output
        api.delete_page.assert_called_once()

    def test_refs_from_the_same_page_do_not_block(self):
        api = _api(_graph(refs_from_outside=False))
        r = _run(api, "delete-page", "--name", "Target", "--force")
        assert r.exit_code == 0, r.output
        api.delete_page.assert_called_once()

    def test_dry_run_predicts_the_refusal(self):
        api = _api(_graph())
        r = _run(api, "delete-page", "--name", "Target", "--dry-run")
        assert r.exit_code == 1
        assert "Journal Day" in r.output

    def test_asks_by_the_name_logseq_resolved(self):
        """getPage normalises further than lower-casing (a trailing slash, the
        Unicode form); asking by the typed name found no page and no refs."""
        api = _api(_graph())
        r = _run(api, "delete-page", "--name", "Target/", "--force")
        assert r.exit_code == 1
        assert "Journal Day" in r.output
        api.delete_page.assert_not_called()


class TestCopyBlockRemove:
    def test_refuses_before_copying_and_points_to_move_block(self):
        api = _api(_graph())
        r = _run(api, "copy-block", "--id", ROOT, "--to-page", "Elsewhere", "--remove")
        assert r.exit_code == 1
        assert "move-block" in r.output
        api.append_block_in_page.assert_not_called()
        api.remove_block.assert_not_called()

    def test_refs_from_within_the_source_count(self):
        """The copy carries them under new uuids, and the original goes."""
        api = _api(_graph(refs_from_outside=False))
        del api.datascript_query.side_effect.blocks[OTHER]
        r = _run(api, "copy-block", "--id", ROOT, "--to-page", "Elsewhere", "--remove")
        assert r.exit_code == 1
        assert REF_IN in r.output
        api.append_block_in_page.assert_not_called()

    def test_plain_copy_is_not_checked(self):
        api = _api(_graph())
        r = _run(api, "copy-block", "--id", ROOT, "--to-page", "Elsewhere")
        assert r.exit_code == 0, r.output
        api.datascript_query.assert_not_called()

    def test_ignore_refs_moves(self):
        api = _api(_graph())
        r = _run(api, "copy-block", "--id", ROOT, "--to-page", "Elsewhere",
                 "--remove", "--ignore-refs", "--json")
        assert r.exit_code == 0, r.output
        api.remove_block.assert_called_once_with(ROOT)
        assert json.loads(r.output)["refs_broken"] == 3
