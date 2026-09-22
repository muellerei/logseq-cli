"""Shared test helpers."""
import os

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    """Keep the developer's own environment out of every test.

    A shell with LOGSEQ_CLI_CONFIG or LOGSEQ_JOURNAL_HEADING set would
    otherwise change what commands do here — silently, and differently on
    each machine. Tests that want either one set it themselves.
    """
    for var in ("LOGSEQ_CLI_CONFIG", "LOGSEQ_JOURNAL_HEADING"):
        monkeypatch.delenv(var, raising=False)
    yield


def split_runner():
    """CliRunner that captures stderr separately from stdout.

    Click <8.2 needs ``mix_stderr=False`` for that; in 8.2+ the streams are
    always separate and the argument was removed. Support both so assertions
    about "payload on stdout, errors on stderr" keep working across versions.
    """
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


class FakeGraph:
    """Minimal in-memory stand-in for the block graph.

    ``insertBatchBlock`` answers ``null`` whether it wrote or not, so
    :func:`helpers.insert_block_tree_batched` proves the write by reading the
    parent's children back. A MagicMock returns a MagicMock for that read, which
    reads as "nothing arrived" and would make every success test fail for the
    wrong reason. This models just enough of the real API to tell a genuine
    write apart from a silent failure.

    ``fail_after`` writes only that many blocks and then stops silently, which
    is the partial-write shape the graph itself has to expose.
    """

    def __init__(self, uuids, *, fail_after=None):
        self._uuids = list(uuids)
        self._fail_after = fail_after
        self._written = 0
        self.children = {}          # parent uuid -> list of child dicts
        self.batch_calls = []       # (anchor, tree, options)
        self.insert_calls = []      # (parent, content, options)

    # --- helpers ---------------------------------------------------------
    @property
    def written(self):
        """Blocks that actually reached the graph, across all calls."""
        return self._written

    def set_fail_after(self, n):
        """Write only ``n`` more blocks, then stop silently (no error, no UUID).

        Models the real failure shape: ``insertBatchBlock`` skips a node it
        cannot write and reports nothing, so only a re-read reveals the gap.
        """
        self._fail_after = self._written + n

    def _next_uuid(self):
        if self._fail_after is not None and self._written >= self._fail_after:
            return None
        self._written += 1
        return self._uuids.pop(0) if self._uuids else f"auto-{self._written}"

    def _add_tree(self, parent, tree, *, prepend=False):
        """Insert roots keeping declaration order, then nest their children.

        A prepending loop would put each root ahead of the previous one and
        reverse the order, so the batch is built first and spliced in at the
        head as one contiguous run - which is what the real API does.
        """
        made = []
        roots = []
        for node in tree:
            uuid = self._next_uuid()
            if uuid is None:
                break
            block = {"uuid": uuid, "content": node["content"], "children": []}
            roots.append(block)
            made.append(block)
            kids = node.get("children") or []
            if kids:
                made.extend(self._add_tree(uuid, kids))
        bucket = self.children.setdefault(parent, [])
        if prepend:
            bucket[:0] = roots
        else:
            bucket.extend(roots)
        return made

    # --- API surface -----------------------------------------------------
    def insert_batch_block(self, anchor, batch, options=None):
        self.batch_calls.append((anchor, batch, options))
        opts = options or {}
        if opts.get("sibling"):
            # anchored on a sibling: the batch lands after it, under its parent
            for parent, kids in self.children.items():
                if any(k["uuid"] == anchor for k in kids):
                    self._add_tree(parent, batch)
                    return None
        self._add_tree(anchor, batch, prepend=True)
        return None

    def insert_block(self, parent, content, options=None):
        self.insert_calls.append((parent, content, options))
        uuid = self._next_uuid()
        if uuid is None:
            return None
        block = {"uuid": uuid, "content": content, "children": []}
        opts = options or {}
        bucket = self.children.setdefault(parent, [])
        bucket.insert(0, block) if opts.get("before") else bucket.append(block)
        return {"uuid": uuid}

    def get_block(self, uuid, include_children=True):
        def find(node_uuid):
            if node_uuid in self.children:
                return {"uuid": node_uuid, "children": self.children[node_uuid]}
            return None
        found = find(uuid)
        if found:
            return self._materialize(found)
        return {"uuid": uuid, "children": []}

    def _materialize(self, node):
        out = {"uuid": node["uuid"], "children": []}
        for child in node.get("children", []):
            sub = {"uuid": child["uuid"], "content": child["content"],
                   "children": self.children.get(child["uuid"], [])}
            out["children"].append(self._materialize_child(sub))
        return out

    def _materialize_child(self, child):
        return {
            "uuid": child["uuid"],
            "content": child["content"],
            "children": [
                self._materialize_child({
                    "uuid": g["uuid"], "content": g["content"],
                    "children": self.children.get(g["uuid"], []),
                })
                for g in child.get("children", [])
            ],
        }


def fake_api(uuids, *, fail_after=None):
    """MagicMock whose block-write/read methods are backed by FakeGraph."""
    from unittest.mock import MagicMock
    graph = FakeGraph(uuids, fail_after=fail_after)
    api = MagicMock()
    api.insert_batch_block.side_effect = graph.insert_batch_block
    api.insert_block.side_effect = graph.insert_block
    api.get_block.side_effect = graph.get_block
    api.graph = graph
    return api


def answer_property_pulls(api):
    """Answer ``stored_properties``' datascript pull from the mocked blocks.

    Commands read property keys through a pull (see helpers.stored_properties),
    while most tests describe a block the way the API returns it, with a
    ``properties`` map. This lets such a test keep describing the block once:
    the pull for a uuid answers with the properties of whichever mocked page or
    block carries that uuid. tests/test_stored_property_keys.py, which is about
    the difference between the two, does not use this: it models the API's
    camel-cased map and the stored keys separately.
    """
    import re
    from unittest.mock import DEFAULT

    def candidates():
        for source in (api.get_block, api.get_page):
            value = source.return_value
            if isinstance(value, dict):
                yield value
        tree = api.get_page_blocks_tree.return_value
        if isinstance(tree, list):
            yield from (b for b in tree if isinstance(b, dict))

    def query(q):
        if ":block/properties-text-values" not in q:
            return DEFAULT  # any other query answers as the test configured it
        m = re.search(r'#uuid "([^"]+)"', q)
        for entity in candidates():
            if m and entity.get("uuid") == m.group(1):
                values = entity.get("properties") or {}
                texts = entity.get("propertiesTextValues") or {
                    k: v if isinstance(v, str) else str(v) for k, v in values.items()}
                return [[{"properties": values, "properties-text-values": texts}]]
        return [[None]]

    api.datascript_query.side_effect = query
    return api
