"""Shared test helpers."""
import os
import re

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


def _logseq_block_id(content):
    """The uuid Logseq takes from ``content``'s id:: line, or ``""``.

    Logseq's reading as measured (0.10.15), kept apart from the CLI's rule on
    purpose: a stand-in that asked the code under test which line is the id
    would agree with it whatever it got wrong. A code block runs from a line
    starting with ``` or ~~~ (after spaces, tabs, form feeds) to the next
    such line; an opener nothing closes hides nothing. Of two id:: lines the
    last wins.
    """
    lines = content.split("\n")
    code, opener = set(), None
    for i, line in enumerate(lines):
        if line.lstrip(" \t\f").startswith(("```", "~~~")):
            if opener is None:
                opener = i
            else:
                code.update(range(opener + 1, i))
                opener = None
    found = ""
    for i, line in enumerate(lines):
        m = re.fullmatch(r"(?i)[ \t\f\r]*(?:id|custom[-_]id):: +(\S+)[ \t]*", line)
        if m and i not in code:
            found = m.group(1)
    return found


class PageGraph:
    """Pages and their block trees, answering the way Logseq 0.10.15 does.

    Built for #31, where the writes went through ``insertBatchBlock`` and are
    proven by reading the page back. A MagicMock answers every read with
    something and hid a defect that way before (#23), so each behaviour here
    is one that was measured:

    * ``insertBatchBlock`` answers ``null``. ``sibling: false`` puts the batch
      at the head of the anchor's children, ``sibling: true`` right after the
      anchor, ``+ before: true`` right before it. A page uuid as anchor with
      ``sibling: false`` puts it at the head of the page.
    * With ``keepUUID`` a node keeps the uuid of its ``id::`` line, a
      placeholder's included, and also one a real block already has: the
      batch does not check (the CLI must). Only a line Logseq reads as a
      property counts: not one inside a code block, and of two the last.
    * Without ``keepUUID`` every ``id::`` line leaves the content, one inside a
      code block too.
    * Anchored ``before`` the page's first block, or on a page with no blocks
      at all, Logseq writes every node of the batch with ``* `` in front of its
      content. Anchored on the page uuid of a page that holds a block, it does
      not.
    * ``insertBlock`` and ``appendBlockInPage`` refuse a ``customUUID`` that a
      block or a placeholder holds.
    * ``updateBlock`` writes the content as given, then each of ``properties``
      as a ``key:: value`` line the text does not already carry. Once the
      file is read again, an ``id::`` line in the text is the block's uuid,
      a foreign one too (#56); modelled at once, so a write that lets one
      through shows as the block losing its uuid.
    * ``createPage`` makes a page with one empty block. A page known only from
      a ``[[link]]`` has none.
    * ``getBlock`` answers ``null`` for an unknown uuid and for a page, and the
      placeholder for a ``((ref))`` without a block as ``id:: <uuid>`` with no
      page.
    """

    def __init__(self, pages=None, *, placeholders=(), blockless=()):
        self._ids = 0
        self.pages = []                 # {"id", "uuid", "name", "blocks"}
        self.placeholders = set(placeholders)
        for name, nodes in (pages or {}).items():
            self._page(name, [self._node(n) for n in nodes])
        for name in blockless:
            self._page(name, [])

    # --- building ----------------------------------------------------------
    def _fresh(self):
        self._ids += 1
        return f"00000000-0000-4000-8000-{self._ids:012d}"

    def _page(self, name, blocks):
        page = {"id": 1000 + len(self.pages), "uuid": self._fresh(), "name": name,
                "blocks": blocks}
        self.pages.append(page)
        return page

    def _node(self, spec, *, keep=False, strip_ids=False, prefix=""):
        spec = {"content": spec} if isinstance(spec, str) else spec
        wanted = _logseq_block_id(spec["content"]) if keep else ""
        uuid = spec.get("uuid") or (wanted.lower() if wanted else self._fresh())
        self.placeholders.discard(uuid)
        content = spec["content"]
        if strip_ids:
            content = "\n".join(l for l in content.split("\n")
                                if not re.match(r"(?i)[\s\ufeff]*id:: ", l))
        return {"uuid": uuid, "content": prefix + content,
                "children": [self._node(c, keep=keep, strip_ids=strip_ids, prefix=prefix)
                             for c in spec.get("children") or []]}

    # --- lookup ------------------------------------------------------------
    def page_named(self, name):
        if isinstance(name, int):
            return next((p for p in self.pages if p["id"] == name), None)
        return next((p for p in self.pages if p["name"].lower() == str(name).lower()), None)

    def locate(self, uuid):
        """``(page, siblings, index, parent)`` of the block, or ``None``."""
        def walk(page, blocks, parent):
            for i, b in enumerate(blocks):
                if b["uuid"] == uuid:
                    return page, blocks, i, parent
                found = walk(page, b["children"], b)
                if found:
                    return found
        for page in self.pages:
            found = walk(page, page["blocks"], None)
            if found:
                return found
        return None

    def every_uuid(self):
        def walk(blocks):
            for b in blocks:
                yield b["uuid"]
                yield from walk(b["children"])
        return [u for p in self.pages for u in walk(p["blocks"])]

    def tree(self, name):
        """The page as first lines, nested: for assertions."""
        def walk(blocks):
            return [(b["content"].split("\n")[0], walk(b["children"])) for b in blocks]
        return walk(self.page_named(name)["blocks"])

    def _out(self, block, page, parent, children=True):
        return {"uuid": block["uuid"], "content": block["content"],
                "page": {"id": page["id"]},
                "parent": {"id": parent["uuid"] if parent else page["id"]},
                "children": [self._out(c, page, block) for c in block["children"]]
                            if children else []}

    # --- API surface -------------------------------------------------------
    def get_page(self, name):
        page = self.page_named(name)
        if page is None:
            return None
        return {"id": page["id"], "uuid": page["uuid"], "name": page["name"].lower(),
                "originalName": page["name"]}

    def get_page_blocks_tree(self, name):
        page = self.page_named(name)
        if page is None:
            return None
        return [self._out(b, page, None) for b in page["blocks"]]

    def get_block(self, uuid, include_children=True):
        found = self.locate(uuid)
        if found:
            page, siblings, i, parent = found
            return self._out(siblings[i], page, parent, children=include_children)
        if uuid in self.placeholders:
            return {"uuid": uuid, "content": f"id:: {uuid}", "children": []}
        return None

    def create_page(self, name, properties=None, options=None):
        page = self._page(name, [{"uuid": self._fresh(), "content": "", "children": []}])
        return self.get_page(page["name"])

    def insert_batch_block(self, anchor, batch, options=None):
        opts = options or {}
        keep = bool(opts.get("keepUUID"))
        page = next((p for p in self.pages if p["uuid"] == anchor), None)
        if page is not None:
            if opts.get("sibling"):
                return None
            # Measured: clean on a page holding a block (even an empty one),
            # prefixed on a page with none.
            target, at = page["blocks"], 0
            headless = not page["blocks"]
        else:
            found = self.locate(anchor)
            if not found:
                return None
            page, siblings, i, parent = found
            if not opts.get("sibling"):
                target, at, headless = siblings[i]["children"], 0, False
            elif opts.get("before"):
                target, at = siblings, i
                headless = parent is None and i == 0
            else:
                target, at, headless = siblings, i + 1, False
        prefix = "* " if headless else ""
        target[at:at] = [self._node(n, keep=keep, strip_ids=not keep, prefix=prefix)
                         for n in batch]
        return None

    def update_block(self, uuid, content, properties=None, *, replacing=None):
        found = self.locate(uuid)
        if not found:
            return None
        _, siblings, i, _ = found
        lines = content.split("\n")
        for key, value in (properties or {}).items():
            if not any(re.match(rf"(?i)[ \t]*{re.escape(key)}:: ", l) for l in lines):
                lines.append(f"{key}:: {value}")
        siblings[i]["content"] = "\n".join(lines)
        wanted = _logseq_block_id(siblings[i]["content"])
        if wanted:
            siblings[i]["uuid"] = wanted.lower()
        return None

    def insert_block(self, target, content, options=None):
        opts = options or {}
        wanted = opts.get("customUUID")
        if wanted and (self.locate(wanted) or wanted in self.placeholders):
            return {"error": "Custom block UUID already exists"}
        found = self.locate(target)
        if not found:
            return None
        page, siblings, i, parent = found
        block = {"uuid": wanted or self._fresh(), "content": content, "children": []}
        if not opts.get("sibling"):
            kids = siblings[i]["children"]
            kids.insert(0, block) if opts.get("before") else kids.append(block)
        else:
            siblings.insert(i if opts.get("before") else i + 1, block)
        return self._out(block, page, parent)

    def append_block_in_page(self, name, content, options=None):
        page = self.page_named(name)
        if page is None:
            return None
        wanted = (options or {}).get("customUUID")
        if wanted and (self.locate(wanted) or wanted in self.placeholders):
            return {"error": "Custom block UUID already exists"}
        block = {"uuid": wanted or self._fresh(), "content": content, "children": []}
        page["blocks"].append(block)
        return self._out(block, page, None)

    def remove_block(self, uuid):
        found = self.locate(uuid)
        if found:
            _, siblings, i, _ = found
            del siblings[i]
        return None

    def move_block(self, src, target, options=None):
        opts = options or {}
        moving = self.locate(src)
        if not moving or not self.locate(target):
            return None
        _, siblings, i, _ = moving
        block = siblings.pop(i)
        _, t_siblings, t_i, _ = self.locate(target)
        if opts.get("children"):
            t_siblings[t_i]["children"].append(block)
        else:
            t_siblings.insert(t_i if opts.get("before") else t_i + 1, block)
        return None

    def datascript_query(self, query):
        """The id existence check: which of the literal uuids an entity has.

        Measured attributes: a block has ``:block/page``, a page has
        ``:block/name``, a placeholder has neither. A query that names either
        attribute matches only the entities carrying it.
        """
        import re
        if "contains?" not in query:
            return []
        asked = re.findall(r'#uuid "([^"]+)"', query)
        blocks, pages = set(self.every_uuid()), {p["uuid"] for p in self.pages}
        if ":block/page" in query or ":block/name" in query:
            have = (blocks if ":block/page" in query else set()) | \
                   (pages if ":block/name" in query else set())
        else:
            have = blocks | pages | self.placeholders
        return [[u] for u in asked if u in have]


def page_graph_api(graph):
    """MagicMock whose page and block calls are answered by ``graph``."""
    from unittest.mock import MagicMock
    api = MagicMock()
    for name in ("get_page", "get_page_blocks_tree", "get_block", "create_page",
                 "insert_batch_block", "insert_block", "append_block_in_page",
                 "remove_block", "move_block", "datascript_query", "update_block"):
        getattr(api, name).side_effect = getattr(graph, name)
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    api.get_page_linked_references.return_value = []
    api.graph = graph
    return api
