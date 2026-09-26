"""Shared test helpers."""
import re

import pytest
from click.testing import CliRunner

from logseq_cli.api import _not_verified


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


class _TextResponse:
    """What ``requests.post`` hands back, reduced to what the client reads."""

    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


@pytest.fixture(autouse=True)
def no_batch_editor_wait(monkeypatch):
    """Ask checkEditing once after a batch, without waiting.

    LogseqAPI waits up to 100 ms after each insertBatchBlock for the block
    Logseq opens in its editor (M16). The doubles open it at once, so the
    wait would only slow the suite. Tests of the wait set it themselves.
    """
    from logseq_cli.api import LogseqAPI
    monkeypatch.setattr(LogseqAPI, "batch_editor_wait_s", 0)


@pytest.fixture
def real_host_calls():
    """Requests ``block_real_hosts`` refused in this test.

    The fixture fails the test in teardown while this holds anything. A test
    that reaches for a real host on purpose checks the list and clears it.
    """
    return []


@pytest.fixture(autouse=True)
def block_real_hosts(monkeypatch, real_host_calls):
    """Keep every test away from the Logseq running on the developer's machine.

    ``requests.post`` is replaced for the whole run. ``checkEditing`` answers
    with the raw text ``false``, as Logseq does when nobody edits, so a test
    that mocks ``call`` still passes the editor gate. Any other request is
    recorded and refused. The refusal alone is not enough: broad ``except``
    blocks in the commands can swallow it, so teardown fails on the record.
    A test that patches ``requests.post`` itself, or installs the HTTP
    double, overrides this for its own duration.

    doctor opens a socket to the API port before it asks anything, and with
    Logseq running it would then go on to query it; that probe answers "no
    listener" here. tests/test_doctor_probes.py imports the function under
    its own name at load time and keeps testing it against a local socket.
    """
    import logseq_cli.api
    import logseq_cli.commands.meta

    def post(url, json=None, **kwargs):
        method = (json or {}).get("method")
        if method == "logseq.Editor.checkEditing":
            return _TextResponse("false")
        real_host_calls.append({"url": url, "method": method, "json": json})
        raise RuntimeError("test reached a real host")

    monkeypatch.setattr(logseq_cli.api.requests, "post", post)
    monkeypatch.setattr(logseq_cli.commands.meta, "_port_has_listener",
                        lambda *args, **kwargs: False)
    yield
    if real_host_calls:
        pytest.fail(f"test reached a real host: {real_host_calls}", pytrace=False)


def mock_api(**overrides):
    """MagicMock standing in for LogseqAPI, with safe answers for the gate.

    On a bare MagicMock every call answers truthy: ``check_editing()`` would
    read as "a block is open", ``rename_refusal()`` as a refusal, and
    ``writes_landed`` would be a Mock that neither counts nor serialises.
    ``overrides`` set further attributes on the mock.
    """
    from unittest.mock import MagicMock
    api = MagicMock()
    api.check_editing.return_value = None
    api.rename_refusal.return_value = None
    api.writes_landed = 0
    for name, value in overrides.items():
        setattr(api, name, value)
    return api


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

    Stands in for LogseqAPI's block writes and reads, and answers the way the
    methods do after their proofs (spec 030): ``insert_batch_block`` the new
    uuids in DFS pre-order, counted in the owning mock's ``writes_landed``. A
    MagicMock answers every read with a MagicMock, which reads as "nothing
    arrived" and would make every success test fail for the wrong reason.

    ``fail_after`` writes only that many blocks and then stops silently, which
    is the partial-write shape the graph itself has to expose; ``insert_block``
    then raises WriteNotVerified, and ``insert_batch_block`` too once the
    blocks that did land are counted, as the real methods do.
    """

    def __init__(self, uuids, *, fail_after=None):
        self._uuids = list(uuids)
        self._fail_after = fail_after
        self._written = 0
        self.api = None             # the mock whose writes_landed is counted
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
    def check_editing(self):
        # Nobody types in a fake: its batches go through.
        return None

    def insert_batch_block(self, anchor, batch, options=None):
        self.batch_calls.append((anchor, batch, options))
        opts = options or {}
        made = None
        if opts.get("sibling"):
            # anchored on a sibling: the batch lands after it, under its parent
            for parent, kids in self.children.items():
                if any(k["uuid"] == anchor for k in kids):
                    made = self._add_tree(parent, batch)
                    break
        if made is None:
            made = self._add_tree(anchor, batch, prepend=True)
        return _batch_proven(self.api, anchor, batch, [b["uuid"] for b in made])

    def insert_block(self, parent, content, options=None):
        self.insert_calls.append((parent, content, options))
        uuid = self._next_uuid()
        if uuid is None:
            # As LogseqAPI.insert_block does on Logseq's null (_prove_uuid).
            raise _not_verified("insertBlock", parent, "a new block",
                                "no block uuid in the answer")
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


def _batch_proven(api, anchor, batch, new):
    """What LogseqAPI.insert_batch_block does with the ``new`` uuids a fake
    wrote: counts them in ``api.writes_landed`` and answers them, or raises
    WriteNotVerified when fewer than ``batch`` holds landed."""
    from logseq_cli.outlinetext import count_blocks
    if api is not None:
        api.writes_landed += len(new)
    expected = count_blocks(batch)
    if len(new) != expected:
        raise _not_verified("insertBatchBlock", anchor, f"{expected} blocks", str(len(new)))
    return new


def fake_api(uuids, *, fail_after=None):
    """MagicMock whose block-write/read methods are backed by FakeGraph."""
    graph = FakeGraph(uuids, fail_after=fail_after)
    api = mock_api()
    graph.api = api
    api.insert_batch_block.side_effect = graph.insert_batch_block
    api.insert_block.side_effect = graph.insert_block
    api.get_block.side_effect = graph.get_block
    api.graph = graph
    return api


def answer_property_pulls(api):
    """Answer ``stored_properties``' datascript pull from the mocked blocks.

    Commands read property keys through a pull (see blockprops.stored_properties),
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
    last wins. The value is trimmed of whitespace but a trailing ``\r``, and
    a value with spaces in it is taken whole (``id:: a b c``, #56).
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
        m = re.fullmatch(r"(?i)[ \t\f\r]*(?:id|custom[-_]id):: +[^\S\r\n]*(\S(?:[^\r\n]*\S)?)[^\S\r\n]*", line)
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
      ``sibling: false`` puts it at the head of the page. As a method here it
      answers as LogseqAPI's does after its proof: the new uuids in DFS
      pre-order, or WriteNotVerified for a batch that wrote nothing.
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
      as a ``key:: value`` line. A line of the text with the same key goes:
      the passed value wins (#66). Once the file is read again, an ``id::``
      line in the text is the block's uuid, a foreign one too (#56); modelled
      at once, so a write that lets one through shows as the block losing its
      uuid.
    * ``createPage`` makes a page with one empty block. A page known only from
      a ``[[link]]`` has none.
    * ``getBlock`` answers ``null`` for an unknown uuid and for a page, and the
      placeholder for a ``((ref))`` without a block as ``id:: <uuid>`` with no
      page.
    * An alias (``aliases={"al": ["Ziel"]}``: pages whose own ``alias::``
      names it) is a page of its own to the API: ``getPage`` answers its stub,
      ``getPageBlocksTree`` its blocks, none unless given, and
      ``appendBlockInPage`` writes onto it. Only the query on ``:block/alias``
      and the source's ``alias::`` property leads to the source (#63).
    * ``getPage`` names a ``file`` for a page built with text in a block; an
      alias, a page known only from a link and one ``createPage`` made empty
      have none.
    * A page's properties are those of its property block (``preBlock?``),
      taken when that block is saved (#80). ``updateBlock`` saves: the page's
      first block becomes the property block when it then holds only property
      lines, and stops being one otherwise; the same text again saves nothing.
      ``upsertBlockProperty`` writes the line without that step, so the page
      keeps its old properties. A block inserted with property text is none,
      and removing the property block clears the page's properties. A page
      read in from a file with one is built by ``with_property_block``.
    """

    def __init__(self, pages=None, *, placeholders=(), blockless=(), aliases=None):
        self._ids = 0
        self.api = None                 # the mock whose writes_landed is counted
        self.pages = []                 # {"id", "uuid", "name", "blocks"}
        self.placeholders = set(placeholders)
        self.alias_sources = {a.lower(): list(s) for a, s in (aliases or {}).items()}
        for name, nodes in (pages or {}).items():
            self._page(name, [self._node(n) for n in nodes])["file"] = True
        for name in list(blockless) + [a for a in (aliases or {})
                                       if a not in (pages or {})]:
            self._page(name, [])

    # --- building ----------------------------------------------------------
    def _fresh(self):
        self._ids += 1
        return f"00000000-0000-4000-8000-{self._ids:012d}"

    def _page(self, name, blocks):
        page = {"id": 1000 + len(self.pages), "uuid": self._fresh(), "name": name,
                "blocks": blocks, "props": {}}
        self.pages.append(page)
        return page

    def _node(self, spec, *, keep=False, strip_ids=False, prefix=""):
        spec = {"content": spec} if isinstance(spec, str) else spec
        wanted = _logseq_block_id(spec["content"]) if keep else ""
        uuid = spec.get("uuid") or (wanted.lower() if wanted else self._fresh())
        self.placeholders.discard(uuid)
        content = spec["content"]
        if strip_ids:
            content = "\n".join(ln for ln in content.split("\n")
                                if not re.match(r"(?i)[\s\ufeff]*id:: ", ln))
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
                "preBlock?": bool(block.get("pre")),
                "page": {"id": page["id"]},
                "parent": {"id": parent["uuid"] if parent else page["id"]},
                "children": [self._out(c, page, block) for c in block["children"]]
                            if children else []}

    # --- API surface -------------------------------------------------------
    def get_page(self, name):
        page = self.page_named(name)
        if page is None:
            return None
        found = {"id": page["id"], "uuid": page["uuid"], "name": page["name"].lower(),
                 "originalName": page["name"]}
        if page.get("file") and any(b["content"] for b in page["blocks"]):
            found["file"] = {"id": page["id"] + 5000}
        return found

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

    def create_page(self, name, properties=None, *, first_block=True):
        blocks = [{"uuid": self._fresh(), "content": "", "children": []}] if first_block else []
        page = self._page(name, blocks)
        return self.get_page(page["name"])

    def with_property_block(self, name, content, *rest):
        """A page whose first block is its property block, as a file read in
        makes it; ``rest`` are further top-level blocks."""
        page = self._page(name, [{"uuid": self._fresh(), "content": content,
                                  "children": [], "pre": True},
                                 *(self._node(r) for r in rest)])
        page["file"], page["props"] = True, self._property_texts(content)
        return page

    @staticmethod
    def _property_texts(content):
        """Each ``key:: value`` line's text under its stored key, or ``None``
        when a line is anything else."""
        texts = {}
        for line in content.split("\n"):
            m = re.fullmatch(r"[ \t]*([^\s:]+):: (.*)", line)
            if not m:
                return None
            texts[m.group(1).lower().replace("_", "-")] = m.group(2)
        return texts

    def _saved(self, page, parent, i, block):
        """What Logseq does to the page when ``block`` is saved."""
        texts = self._property_texts(block["content"]) if block["content"].strip() else None
        if parent is None and i == 0 and texts is not None:
            block["pre"], page["props"] = True, texts
        elif block.get("pre"):
            block["pre"], page["props"] = False, {}

    def upsert_block_property(self, uuid, key, value):
        found = self.locate(uuid)
        if found:
            _, siblings, i, _ = found
            lines = [ln for ln in siblings[i]["content"].split("\n")
                     if not re.match(rf"[ \t]*{re.escape(key)}:: ", ln)]
            siblings[i]["content"] = "\n".join([*filter(None, lines), f"{key}:: {value}"])
        return None

    def check_editing(self):
        # Nobody types in a fake: its batches go through.
        return None

    def insert_batch_block(self, anchor, batch, options=None):
        opts = options or {}
        keep = bool(opts.get("keepUUID"))
        page = next((p for p in self.pages if p["uuid"] == anchor), None)
        if page is not None:
            if opts.get("sibling"):
                return _batch_proven(self.api, anchor, batch, [])
            # Measured: clean on a page holding a block (even an empty one),
            # prefixed on a page with none.
            target, at = page["blocks"], 0
            headless = not page["blocks"]
        else:
            found = self.locate(anchor)
            if not found:
                return _batch_proven(self.api, anchor, batch, [])
            page, siblings, i, parent = found
            if not opts.get("sibling"):
                target, at, headless = siblings[i]["children"], 0, False
            elif opts.get("before"):
                target, at = siblings, i
                headless = parent is None and i == 0
            else:
                target, at, headless = siblings, i + 1, False
        prefix = "* " if headless else ""
        nodes = [self._node(n, keep=keep, strip_ids=not keep, prefix=prefix) for n in batch]
        target[at:at] = nodes

        def walk(blocks):
            for b in blocks:
                yield b["uuid"]
                yield from walk(b["children"])
        return _batch_proven(self.api, anchor, batch, list(walk(nodes)))

    def update_block(self, uuid, content, properties=None, *, replacing=None):
        found = self.locate(uuid)
        if not found:
            return None
        page, siblings, i, parent = found
        if content == siblings[i]["content"] and not properties:
            return None
        lines = content.split("\n")
        for key, value in (properties or {}).items():
            lines = [ln for ln in lines if not re.match(rf"[ \t]*{re.escape(key)}:: ", ln)]
            lines.append(f"{key}:: {value}")
        siblings[i]["content"] = "\n".join(lines)
        wanted = _logseq_block_id(siblings[i]["content"])
        if wanted:
            siblings[i]["uuid"] = wanted.lower()
        self._saved(page, parent, i, siblings[i])
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
            page, siblings, i, _ = found
            if siblings[i].get("pre"):
                page["props"] = {}
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
        if ":block/alias" in query:
            # The source query of pagenames.alias_sources: each page whose own
            # alias:: names the alias, with that property value as a list.
            alias = re.search(r'\[\?a :block/name "([^"]*)"\]', query).group(1)
            return [[source, [alias]] for source in self.alias_sources.get(alias, [])]
        if ":block/properties-text-values" in query:
            # stored_properties' pull: each property line's text, under its
            # key as stored (lower-cased, "_" read as "-", #21), of two the
            # last. Enough for the writers here.
            uuid = re.search(r'#uuid "([^"]+)"', query).group(1)
            page = next((p for p in self.pages if p["uuid"] == uuid), None)
            if page is not None:
                texts = dict(page["props"])
                return [[{"properties": texts, "properties-text-values": texts}]] if texts else []
            found = self.locate(uuid)
            if not found:
                return []
            _, siblings, i, _ = found
            texts = {}
            for line in siblings[i]["content"].split("\n"):
                m = re.fullmatch(r"[ \t]*([^\s:]+):: (.*)", line)
                if m:
                    texts[m.group(1).lower().replace("_", "-")] = m.group(2)
            return [[{"properties": dict(texts), "properties-text-values": texts}]]
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
    api = mock_api()
    for name in ("get_page", "get_page_blocks_tree", "get_block", "create_page",
                 "insert_batch_block", "insert_block", "append_block_in_page",
                 "remove_block", "move_block", "datascript_query", "update_block",
                 "upsert_block_property"):
        getattr(api, name).side_effect = getattr(graph, name)
    api.get_user_configs.return_value = {"preferredDateFormat": "yyyy-MM-dd"}
    api.get_page_linked_references.return_value = []
    api.graph = graph
    graph.api = api
    return api
