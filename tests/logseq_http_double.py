"""A Logseq that answers over HTTP, in memory, the way 0.10.15 does.

The test suite mostly replaces ``LogseqAPI`` method by method (``FakeGraph``,
``PageGraph``, ``MagicMock``). Whatever ``LogseqAPI`` does inside its methods,
the editor gate and the proof of each write, is invisible there.
This double sits one level lower: it stands in for ``requests.post`` and
answers each JSON-RPC request ``{method, args}`` from a small graph, so the
real ``LogseqAPI`` runs in full.

Every answer form below was measured against Logseq 0.10.15, unless it says
otherwise: a form that was not measured says so, with the source it
follows. A double that answers more kindly than Logseq would
let a proof pass that Logseq fails, which is the one thing it must not do.

Switches, all on the instance:

* ``set_mode(method, "execute" | "noop" | "error", from_call=k)`` per write
  method. ``noop`` answers ``null`` and writes nothing: that is how Logseq
  answers a write that did not happen, so every proof has something to
  catch, ``insertBlock`` and ``createPage`` included. ``error`` answers an
  error object. With ``from_call`` the first k-1 calls still execute.
* ``editing``: the uuid of the block open in the editor, or ``None``.
* ``check_editing_form``: how ``checkEditing`` answers (``raw`` as measured;
  ``json``, ``empty``, ``error``, ``ok1``, ``timeout`` for the gate's
  fail-closed cases).
* ``show_page(name)``: the page is on screen, so an insert opens a block.
* ``batch_opens_after_checks``: after a batch on a visible page, how many
  ``checkEditing`` requests still answer ``false`` before the opened block
  shows. Logseq opens it asynchronously, 16–34 ms after answering (three runs);
  0, the default, opens it at once.
* ``time_tracking``: Logseq's time tracking, off as in the measured graph.

Every request is recorded in ``requests`` as ``(method, args)``.
"""
import copy
import datetime
import json
import re
import unicodedata

import requests

import logseq_cli.api
from tests.conftest import _logseq_block_id

# The write methods, as LogseqAPI calls them; set_mode takes these names.
WRITES = frozenset({
    "createPage", "deletePage", "renamePage", "appendBlockInPage", "insertBlock",
    "updateBlock", "removeBlock", "upsertBlockProperty", "removeBlockProperty",
    "insertBatchBlock", "moveBlock", "setBlocksId",
})
MODES = ("execute", "noop", "error")
CHECK_EDITING_FORMS = ("raw", "json", "empty", "error", "ok1", "timeout")

# These four check the uuid before anything else, in every mode.
_UUID_CHECKED = frozenset({"updateBlock", "removeBlock", "upsertBlockProperty",
                           "removeBlockProperty"})

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PROPERTY_LINE = re.compile(r"[ \t]*([^\s:]+):: ?(.*)")
_PAGE_LINK = re.compile(r"\[\[([^\[\]]+)\]\]")
_TAG = re.compile(r"(?:^|(?<=\s))#([^\s#\[\],]+)")
_BLOCK_REF = re.compile(r"\(\(([0-9a-fA-F-]{36})\)\)")
_MARKERS = ("TODO", "DOING", "DONE", "LATER", "NOW", "WAITING", "CANCELED", "CANCELLED")

# The date format of the measured graph.
DATE_FORMAT = "yyyy-MM-dd, EEEE"
# The journal titles Logseq takes as such whatever the graph's format, besides
# the graph's own (date_time_util.cljs safe-journal-title-formatters,
# 0.10.15, read in the code). Only "MMM do, yyyy" was measured (with "Jan 1st, 2099").
_OTHER_JOURNAL_FORMATS = ("MMM do, yyyy", "yyyy-MM-dd", "yyyy_MM_dd")

# Assumed, not measured (time tracking is off in the measured graph):
# the clock lines as upstream util/clock.cljs clock-in and clock-out
# (0.10.15) write them, at a fixed
# time so a test can spell them out.
CLOCK_IN = "CLOCK: [2026-09-26 Sat 14:00]"
CLOCK_OUT = "CLOCK: [2026-09-26 Sat 14:00]--[2026-09-26 Sat 14:05] =>  00:05:00"

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
_DATE_TOKENS = re.compile(r"yyyy|MMMM|MMM|MM|dd|do|EEEE|EEE")


class Response:
    """What ``requests.post`` hands back, as far as the client reads it.

    Always HTTP 200: Logseq answers a failure with 200 and an error object in
    the body.
    """

    def __init__(self, text):
        self.status_code = 200
        self.text = text

    def json(self):
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as e:
            # What requests raises, and what api.call catches (checkEditing
            # answers raw text).
            raise requests.exceptions.JSONDecodeError(e.msg, e.doc, e.pos) from None

    def raise_for_status(self):
        return None


# --- names, keys, values -----------------------------------------------------

def _key(name) -> str:
    """How Logseq compares page names: lower case, a slash at either end
    dropped, NFC (graph_parser/util.cljs ``page-name-sanity-lc``; the
    slashes measured, 0.10.15: getPage on "/X/" finds X)."""
    return unicodedata.normalize("NFC", _without_boundary_slashes(str(name).lower()))


def _without_boundary_slashes(name: str) -> str:
    """``remove-boundary-slashes``: one "/" at the start, one at the end."""
    name = name[1:] if name.startswith("/") else name
    return name[:-1] if name.endswith("/") else name


# What ``clojure.string/trim`` takes off both ends: JavaScript's
# String.prototype.trim, whose set is WhiteSpace and LineTerminator in the
# ECMAScript spec: U+FEFF and every space separator, not Python's \x1c-\x1f
# or \x85. Read in the upstream source (``create!`` and ``rename!`` in
# handler/page.cljs, 0.10.15, trim their names with it), not measured.
_JS_TRIMMED = ("\t\n\v\f\r \u00a0\u1680" + "".join(map(chr, range(0x2000, 0x200b)))
               + "\u2028\u2029\u202f\u205f\u3000\ufeff")


def _js_trim(text: str) -> str:
    return text.strip(_JS_TRIMMED)


def _created_title(name: str) -> str:
    """The title ``create!`` makes of a name (handler/page.cljs, 0.10.15):
    trimmed, ``[[...]]`` unwrapped, leading ``#`` dropped, a slash at either
    end dropped. Measured, 0.10.15, for each of the four; the trim with
    spaces, its full set read in the source (``_js_trim``)."""
    title = _js_trim(name)
    m = re.fullmatch(r"\[\[(.*)\]\]", title)
    title = re.sub(r"^#+", "", m.group(1) if m else title)
    return _without_boundary_slashes(title)


def _is_uuid(value) -> bool:
    return isinstance(value, str) and bool(_UUID_RE.fullmatch(value))


def _stored_key(key: str) -> str:
    """A property key as the database stores it: lower case, "_" read as "-"
    (#21, tests/test_stored_property_keys.py)."""
    return key.lower().replace("_", "-")


def _camel(key: str) -> str:
    """The plugin API camel-cases keys on the way out (created-at →
    createdAt, due-date → dueDate)."""
    head, *rest = key.split("-")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _value_text(value) -> str:
    """A list is written as a,b, a number as itself."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def _parse_value(key: str, text: str):
    """The value Logseq's parser makes of a property's text.

    A whole number becomes a number, "01234" and "[[Link]]" stay text.
    ``tags`` and ``alias`` hold page names (tests/test_stored_property_keys.py).
    """
    if key in ("tags", "alias"):
        names = [p.strip() for p in text.split(",")]
        return [n[2:-2] if n.startswith("[[") and n.endswith("]]") else n.lstrip("#")
                for n in names if n]
    if re.fullmatch(r"-?(0|[1-9][0-9]*)", text):
        return int(text)
    if text in ("true", "false"):
        return text == "true"
    return text


def _property_texts(content: str) -> dict:
    """Each ``key:: value`` line's text, trimmed, under its stored key; of
    two the last (blockprops.py: Logseq's parser trims values)."""
    texts = {}
    for line in content.split("\n"):
        m = _PROPERTY_LINE.fullmatch(line)
        if m:
            texts[_stored_key(m.group(1))] = m.group(2).strip()
    return texts


def _only_properties(content: str):
    """The texts when every line is a property line, else ``None``."""
    if not content.strip():
        return None
    if not all(_PROPERTY_LINE.fullmatch(line) for line in content.split("\n")):
        return None
    return _property_texts(content)


def _marker(content: str):
    first = content.split(" ", 1)[0].split("\n", 1)[0]
    return first if first in _MARKERS else None


def _page_names(content: str, texts: dict) -> list:
    """Page names ``content`` refers to: [[Name]], #Name, tags::/alias::
    values (all three count alike)."""
    names = _PAGE_LINK.findall(content) + _TAG.findall(content)
    for key in ("tags", "alias"):
        if key in texts:
            names += _parse_value(key, texts[key])
    return list(dict.fromkeys(names))


def _edn_strings(query: str) -> list:
    """The string literals of a datalog query, unescaped (datalog.edn_string
    in reverse)."""
    def one(m):
        escape = m.group(1)
        if len(escape) == 5:
            return chr(int(escape[1:], 16))
        return {"n": "\n", "r": "\r", "t": "\t"}.get(escape, escape)

    def unescape(body):
        return re.sub(r'\\(u[0-9A-Fa-f]{4}|.)', one, body)
    return [unescape(s) for s in re.findall(r'"((?:[^"\\]|\\.)*)"', query)]


def _literal_after(query: str, marker: str):
    """The string literal right after ``marker`` in ``query``, unescaped."""
    m = re.search(re.escape(marker) + r'\s*("(?:[^"\\]|\\.)*")', query)
    return _edn_strings(m.group(1))[0] if m else None


def _over_the_wire(body):
    """The request as Logseq receives it: an unserialisable argument fails
    here as it would in requests, and nothing the caller keeps is shared."""
    return json.loads(json.dumps(body))


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# --- journal names -----------------------------------------------------------

def _format_date(day: datetime.date, fmt: str) -> str:
    def token(m):
        t = m.group(0)
        if t == "yyyy":
            return f"{day.year:04d}"
        if t == "MMMM":
            return _MONTHS[day.month - 1]
        if t == "MMM":
            return _MONTHS[day.month - 1][:3]
        if t == "MM":
            return f"{day.month:02d}"
        if t == "dd":
            return f"{day.day:02d}"
        if t == "do":
            suffix = "th" if 10 <= day.day % 100 <= 20 else \
                {1: "st", 2: "nd", 3: "rd"}.get(day.day % 10, "th")
            return f"{day.day}{suffix}"
        if t == "EEEE":
            return _WEEKDAYS[day.weekday()]
        return _WEEKDAYS[day.weekday()][:3]
    return _DATE_TOKENS.sub(token, fmt)


def _parse_date(name: str, fmt: str):
    """The date ``name`` spells in ``fmt``, ignoring case, or ``None``."""
    parts, groups = [], {"yyyy": r"(?P<y>\d{4})", "MMMM": r"(?P<mon>[a-z]+)",
                         "MMM": r"(?P<mon>[a-z]{3})", "MM": r"(?P<m>\d{2})",
                         "dd": r"(?P<d>\d{2})", "do": r"(?P<d>\d{1,2})(?:st|nd|rd|th)",
                         "EEEE": r"[a-z]+", "EEE": r"[a-z]{3}"}
    pos = 0
    for m in _DATE_TOKENS.finditer(fmt):
        parts.append(re.escape(fmt[pos:m.start()]))
        parts.append(groups[m.group(0)])
        pos = m.end()
    parts.append(re.escape(fmt[pos:]))
    m = re.fullmatch("".join(parts), _key(name).strip())
    if not m:
        return None
    try:
        month = int(m.group("m")) if "m" in m.groupdict() else \
            [x[:len(m.group("mon"))].lower() for x in _MONTHS].index(m.group("mon")) + 1
        day = datetime.date(int(m.group("y")), month, int(m.group("d")))
    except (ValueError, IndexError):
        return None
    return day if _format_date(day, fmt).lower() == _key(name).strip() else None


# --- the double --------------------------------------------------------------

class LogseqHttpDouble:
    """Pages and blocks in memory, answering JSON-RPC as Logseq 0.10.15 does."""

    def __init__(self, *, date_format=DATE_FORMAT):
        self.date_format = date_format
        self.pages = []             # {"id", "uuid", "name", "blocks", "props", "journal_day"}
        self.placeholders = {}      # uuid -> db id: a ((ref)) whose block does not exist
        self.requests = []          # (method, args), in order
        self.unanswered = []        # queries the double has no answer for
        self.editing = None
        self.check_editing_form = "raw"
        self.time_tracking = False
        self._visible = set()       # page ids on screen
        self.batch_opens_after_checks = 0
        self._opening = None        # [uuid, checks left] of a batch's block
        self._modes = {}            # method -> (mode, from_call)
        self._calls = {}            # method -> write calls counted so far
        self._next_id = 100
        self._next_uuid = 0
        self._handlers = {
            "logseq.Editor.getPage": self._get_page,
            "logseq.Editor.getBlock": self._get_block,
            "logseq.Editor.getPageBlocksTree": self._get_page_blocks_tree,
            "logseq.Editor.getPageLinkedReferences": self._get_page_linked_references,
            "logseq.Editor.getAllPages": self._get_all_pages,
            "logseq.App.getUserConfigs": self._get_user_configs,
            "logseq.DB.datascriptQuery": self._datascript_query,
            "logseq.Editor.exitEditingMode": self._exit_editing_mode,
            "logseq.Editor.createPage": self._create_page,
            "logseq.Editor.deletePage": self._delete_page,
            "logseq.Editor.renamePage": self._rename_page,
            "logseq.Editor.appendBlockInPage": self._append_block_in_page,
            "logseq.Editor.insertBlock": self._insert_block,
            "logseq.Editor.insertBatchBlock": self._insert_batch_block,
            "logseq.Editor.updateBlock": self._update_block,
            "logseq.Editor.removeBlock": self._remove_block,
            "logseq.Editor.upsertBlockProperty": self._upsert_block_property,
            "logseq.Editor.removeBlockProperty": self._remove_block_property,
            "logseq.Editor.moveBlock": self._move_block,
            "logseq.Editor.setBlocksId": self._set_blocks_id,
        }

    # --- switches and inspection -------------------------------------------
    @classmethod
    def installed(cls, monkeypatch, pages=None, *, modes=None, from_call=1):
        """A double with ``pages`` (``{name: blocks}``, as ``add_page`` takes
        them) and ``modes`` (``{method: mode}``, each from ``from_call`` on),
        standing in for ``requests.post``: the set-up most tests repeat."""
        double = cls()
        for name, blocks in (pages or {}).items():
            double.add_page(name, blocks)
        for method, mode in (modes or {}).items():
            double.set_mode(method, mode, from_call=from_call)
        return double.install(monkeypatch)

    def install(self, monkeypatch):
        """Stand in for ``requests.post`` in logseq_cli.api for this test.

        Replaces whatever is there, the conftest fixture that refuses real
        hosts included; monkeypatch puts it back afterwards.
        """
        monkeypatch.setattr(logseq_cli.api.requests, "post", self.post)
        return self

    def set_mode(self, method: str, mode: str, *, from_call: int = 1):
        if method not in WRITES:
            raise ValueError(f"{method!r} is not a write method")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        self._modes[method] = (mode, from_call)

    def show_page(self, name):
        """Put the page on screen: Logseq then opens an inserted block."""
        self._visible.add(self._require_page(name)["id"])

    def sent(self, method: str) -> list:
        """The args of every request to ``method`` (short or full name)."""
        return [a for m, a in self.requests if m == method or m.endswith("." + method)]

    def writes(self) -> list:
        return [(m, a) for m, a in self.requests if m.rsplit(".", 1)[-1] in WRITES]

    def snapshot(self):
        """The graph's state, for "nothing was written"."""
        return copy.deepcopy((self.pages, self.placeholders))

    def uuid_of(self, content: str) -> str:
        """The uuid of the one block whose content is ``content``."""
        found = [n["uuid"] for p in self.pages for n in self._walk(p["blocks"])
                 if n["content"] == content]
        if len(found) != 1:
            raise LookupError(f"{len(found)} blocks hold {content!r}")
        return found[0]

    def tree(self, name):
        """The page as first lines, nested: for assertions."""
        def walk(nodes):
            return [(n["content"].split("\n")[0], walk(n["children"])) for n in nodes]
        return walk(self._require_page(name)["blocks"])

    # --- building ----------------------------------------------------------
    def add_page(self, name, blocks=()):
        """A page as a file read in makes it: a first block holding only
        property lines is its property block. ``blocks`` are texts or
        ``{"content", "children", "uuid"}``. Returns the page."""
        page = self._new_page(name, [self._build(b) for b in blocks])
        self._settle(page)
        for node in self._walk(page["blocks"]):
            self._index(node)
        return page

    def _fresh_uuid(self):
        self._next_uuid += 1
        return f"00000000-0000-4000-8000-{self._next_uuid:012d}"

    def _fresh_id(self):
        self._next_id += 1
        return self._next_id

    def _build(self, spec, *, keep=False, strip_ids=False, prefix=""):
        spec = {"content": spec} if isinstance(spec, str) else spec
        wanted = _logseq_block_id(spec["content"]) if keep else ""
        uuid = (spec.get("uuid") or wanted or self._fresh_uuid()).lower()
        self.placeholders.pop(uuid, None)
        content = spec["content"]
        if strip_ids:
            content = "\n".join(ln for ln in content.split("\n")
                                if not re.match(r"(?i)[\s﻿]*id:: ", ln))
        return {"id": self._fresh_id(), "uuid": uuid, "content": prefix + content,
                "pre": False, "typed": {},
                "children": [self._build(c, keep=keep, strip_ids=strip_ids, prefix=prefix)
                             for c in spec.get("children") or []]}

    def _new_page(self, name, blocks):
        day = _parse_date(name, self.date_format)
        page = {"id": self._fresh_id(), "uuid": self._fresh_uuid(),
                # A journal's title is Logseq's own spelling of the date.
                "name": _format_date(day, self.date_format) if day else name,
                "blocks": blocks, "props": {},
                "journal_day": int(day.strftime("%Y%m%d")) if day else None}
        self.pages.append(page)
        return page

    def _settle(self, page):
        """A first block of property lines only is the page's property block."""
        if page["blocks"]:
            first = page["blocks"][0]
            texts = _only_properties(first["content"])
            if texts is not None:
                first["pre"], page["props"] = True, texts

    def _index(self, node):
        """What Logseq makes of the refs in a written block: a page for each
        name it links (without blocks), a placeholder for each ((ref)) whose
        block does not exist (#70)."""
        for name in _page_names(node["content"], _property_texts(node["content"])):
            if self._find_page(name) is None:
                self._new_page(name, [])
        for uuid in _BLOCK_REF.findall(node["content"]):
            uuid = uuid.lower()
            if not self._locate(uuid) and uuid not in self.placeholders \
                    and not any(p["uuid"] == uuid for p in self.pages):
                self.placeholders[uuid] = self._fresh_id()

    # --- lookup ------------------------------------------------------------
    @staticmethod
    def _walk(nodes):
        for n in nodes:
            yield n
            yield from LogseqHttpDouble._walk(n["children"])

    def _find_page(self, name):
        if isinstance(name, int) and not isinstance(name, bool):
            return next((p for p in self.pages if p["id"] == name), None)
        if _is_uuid(name):
            found = next((p for p in self.pages if p["uuid"] == name.lower()), None)
            if found:
                return found
        return next((p for p in self.pages if _key(p["name"]) == _key(name)), None)

    def _require_page(self, name):
        page = self._find_page(name)
        if page is None:
            raise LookupError(f"no page {name!r}")
        return page

    def _locate(self, key):
        """``(page, siblings, index, parent)`` of the block with this uuid
        (any case) or db id, or ``None``."""
        def match(n):
            return n["id"] == key if isinstance(key, int) else n["uuid"] == str(key).lower()

        def walk(page, nodes, parent):
            for i, n in enumerate(nodes):
                if match(n):
                    return page, nodes, i, parent
                found = walk(page, n["children"], n)
                if found:
                    return found
            return None
        for page in self.pages:
            found = walk(page, page["blocks"], None)
            if found:
                return found
        return None

    # --- answer forms ------------------------------------------------------
    def _values(self, node) -> dict:
        """Stored key -> value. A value upserted as a list or number keeps its
        type while its text is unchanged."""
        values = {}
        for key, text in _property_texts(node["content"]).items():
            typed = node["typed"].get(key)
            values[key] = typed[1] if typed and typed[0] == text else _parse_value(key, text)
        return values

    def _refs(self, node) -> list:
        """``[{"id": <db id>}]`` per page linked, then per block ref."""
        ids = []
        for name in _page_names(node["content"], _property_texts(node["content"])):
            page = self._find_page(name)
            if page:
                ids.append(page["id"])
        for uuid in _BLOCK_REF.findall(node["content"]):
            found = self._locate(uuid.lower())
            ids.append(found[1][found[2]]["id"] if found else self.placeholders.get(uuid.lower()))
        return [{"id": i} for i in dict.fromkeys(i for i in ids if i is not None)]

    def _block_out(self, page, node, parent, children):
        """getBlock's form: ``page`` and ``parent`` as ``{"id": <int>}``
        (a db id, no uuid), property keys camel-cased, property lines kept in
        ``content``; ``preBlock?``; ``refs`` with or without
        children. Children only when asked for."""
        texts = _property_texts(node["content"])
        out = {"id": node["id"], "uuid": node["uuid"], "content": node["content"],
               "format": "markdown",
               "page": {"id": page["id"]},
               "parent": {"id": parent["id"] if parent else page["id"]},
               "properties": {_camel(k): v for k, v in self._values(node).items()},
               "propertiesTextValues": {_camel(k): v for k, v in texts.items()},
               "preBlock?": bool(node["pre"]),
               "refs": self._refs(node)}
        marker = _marker(node["content"])
        if marker:
            out["marker"] = marker
        if children:
            out["children"] = [self._block_out(page, c, node, True) for c in node["children"]]
        return out

    def _page_out(self, page):
        """getPage's form: uuid, name (lower), originalName, id, journal?;
        properties when it has any; ``file`` once a block holds
        text (a page from a link or an empty createPage has none)."""
        out = {"id": page["id"], "uuid": page["uuid"], "name": _key(page["name"]),
               "originalName": page["name"], "format": "markdown",
               "journal?": page["journal_day"] is not None}
        if page["journal_day"] is not None:
            out["journalDay"] = page["journal_day"]
        if page["props"]:
            out["properties"] = {_camel(k): _parse_value(k, v) for k, v in page["props"].items()}
        if any(n["content"] for n in self._walk(page["blocks"])):
            out["file"] = {"id": page["id"] + 50000}
        return out

    # --- transport ---------------------------------------------------------
    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        body = _over_the_wire(json)
        method, args = body.get("method"), body.get("args") or []
        self.requests.append((method, args))
        short = str(method).rsplit(".", 1)[-1]
        if method == "logseq.Editor.checkEditing":
            return self._check_editing()
        handler = self._handlers.get(method)
        if handler is None:
            # HTTP 200, the error in the body, the name in snake case.
            return self._answer({"error": f"MethodNotExist: {_snake(short)}"})
        if short in WRITES:
            if short in _UUID_CHECKED and not _is_uuid(args[0] if args else None):
                # In every mode.
                return self._answer({"error": f"{args[0] if args else None} is not a valid UUID string."})
            self._calls[short] = self._calls.get(short, 0) + 1
            mode, from_call = self._modes.get(short, ("execute", 1))
            if mode != "execute" and self._calls[short] >= from_call:
                if mode == "noop":
                    # Logseq's answer to a write that did not happen: null.
                    return self._answer(None)
                return self._answer({"error": f"{short} failed"})
        return self._answer(handler(args))

    @staticmethod
    def _answer(value):
        return Response(json.dumps(value))

    def _check_editing(self):
        """The open block's uuid as raw text (not JSON), else ``false``.
        The other forms are there for the gate's fail-closed tests."""
        form = self.check_editing_form
        if form == "timeout":
            raise requests.exceptions.Timeout("checkEditing timed out (double)")
        if form == "empty":
            return Response("")
        if form == "error":
            return self._answer({"error": "checkEditing failed"})
        if form == "ok1":
            return self._answer({"ok": 1})
        if self._opening is not None:
            uuid, left = self._opening
            if left:
                self._opening[1] -= 1
            else:
                self._opening = None
                self.editing = uuid
        if self.editing is None:
            return Response("false")
        return Response(json.dumps(self.editing) if form == "json" else self.editing)

    def _exit_editing_mode(self, args):
        # Answers null.
        self.editing = None
        return None

    # --- reads -------------------------------------------------------------
    def _get_page(self, args):
        # Unknown: null. An alias is a page of its own to the API, its stub
        # without blocks (pagenames.py docstring, measured).
        page = self._find_page(args[0])
        return self._page_out(page) if page else None

    def _get_block(self, args):
        key, opts = args[0], (args[1] if len(args) > 1 else {}) or {}
        if isinstance(key, str) and not _is_uuid(key):
            return {"error": f"{key} is not a valid UUID string."}
        found = self._locate(key if isinstance(key, int) else key.lower())
        if found:
            page, siblings, i, parent = found
            return self._block_out(page, siblings[i], parent, bool(opts.get("includeChildren")))
        if isinstance(key, str) and key.lower() in self.placeholders:
            # The placeholder of a ((ref)) without a block: no page (#70).
            return {"id": self.placeholders[key.lower()], "uuid": key.lower(),
                    "content": f"id:: {key.lower()}", "properties": {}}
        # Unknown, and a page's uuid or db id (LogseqAPI._sibling_uuids_in_order).
        return None

    def _get_page_blocks_tree(self, args):
        if not isinstance(args[0], str):
            # LogseqAPI._sibling_uuids_in_order, measured.
            return {"error": "Expected string, got: number"}
        page = self._find_page(args[0])
        if page is None:
            return None
        return [self._block_out(page, n, None, True) for n in page["blocks"]]

    def _get_page_linked_references(self, args):
        """``[[page, [blocks]]]`` of the blocks on other pages that link it."""
        target = self._find_page(args[0])
        if target is None:
            return None
        out = []
        for page in self.pages:
            if page is target:
                continue
            blocks = []
            for node in self._walk(page["blocks"]):
                if {"id": target["id"]} in self._refs(node):
                    loc = self._locate(node["uuid"])
                    blocks.append(self._block_out(page, node, loc[3], True))
            if blocks:
                out.append([self._page_out(page), blocks])
        return out

    def _get_all_pages(self, args):
        return [self._page_out(p) for p in self.pages]

    def _get_user_configs(self, args):
        # The date format of the measured graph. The rest invented.
        return {"preferredDateFormat": self.date_format, "preferredFormat": "markdown",
                "preferredWorkflow": "todo", "preferredLanguage": "en",
                "currentGraph": "logseq_local_/invented/probe-graph"}

    # --- datascriptQuery ---------------------------------------------------
    def _datascript_query(self, args):
        """The queries the writing commands send (grep datascript_query
        logseq_cli/). Any other raises, so a missing form shows at once."""
        query = args[0]
        if ":block/properties-text-values" in query:
            return self._pull_properties(query)
        if ":block/alias" in query:
            return self._alias_sources(query)
        if ":block/namespace" in query:
            return self._namespace_query(query)
        if ":block/refs ?t" in query:
            return self._incoming_refs(query)
        if "(or [?b :block/page _] [?b :block/name _])" in query:
            return self._uuids_in_use(query)
        if ":block/content" in query and "(pull ?b [:block/content :block/uuid" in query:
            return self._content_search(query)
        self.unanswered.append(query)
        raise NotImplementedError(f"datascriptQuery not modelled by the double: {query}")

    def _pull_properties(self, query):
        """blockprops.stored_properties: stored keys, parsed values and texts
        (5 in properties, "5" as text; ["a","b"] and "a,b"). A page's are
        those of its property block. Unknown entity: no row; one without
        properties: a nil pull."""
        uuid = re.search(r'#uuid "([^"]+)"', query).group(1).lower()
        page = next((p for p in self.pages if p["uuid"] == uuid), None)
        if page is not None:
            texts = dict(page["props"])
            values = {k: _parse_value(k, v) for k, v in texts.items()}
        else:
            found = self._locate(uuid)
            if not found:
                return []
            node = found[1][found[2]]
            texts, values = _property_texts(node["content"]), self._values(node)
        if not texts:
            return [[None]]
        return [[{"properties": values, "properties-text-values": texts}]]

    def _alias_sources(self, query):
        """pagenames.alias_sources: ``[source original name, alias values]``
        for each page whose own alias:: names the alias page."""
        alias = self._find_page(_literal_after(query, ":block/name"))
        if alias is None:
            return []
        rows = []
        for page in self.pages:
            names = _parse_value("alias", page["props"].get("alias", ""))
            if any(_key(n) == _key(alias["name"]) for n in names):
                rows.append([page["name"], names])
        return rows

    def _namespace_query(self, query):
        """LogseqAPI._is_namespace: a row per page whose namespace is the
        page of the uuid asked for."""
        uuid = re.search(r'#uuid "([^"]+)"', query).group(1).lower()
        page = next((p for p in self.pages if p["uuid"] == uuid), None)
        return [[p["id"]] for p in self._namespace_children(page)] if page else []

    def _uuids_in_use(self, query):
        """ids.uuids_in_use: blocks (:block/page) and pages (:block/name), not
        a placeholder, which has neither."""
        asked = [u.lower() for u in re.findall(r'#uuid "([^"]+)"', query)]
        have = {n["uuid"] for p in self.pages for n in self._walk(p["blocks"])} | \
               {p["uuid"] for p in self.pages}
        return [[u] for u in asked if u in have]

    def _page_pull(self, page):
        return {"original-name": page["name"], "name": _key(page["name"])}

    def _incoming_refs(self, query):
        """lookup.incoming_block_refs: ``[target uuid, pull of the source]``
        per block ref into the targets, a page's blocks or a set of uuids."""
        name = _literal_after(query, ":block/name")
        if name is not None:
            page = self._find_page(name)
            targets = {n["uuid"] for n in self._walk(page["blocks"])} if page else set()
        else:
            targets = {u.lower() for u in re.findall(r'#uuid "([^"]+)"', query)}
        rows = []
        for page in self.pages:
            for node in self._walk(page["blocks"]):
                for ref in dict.fromkeys(u.lower() for u in _BLOCK_REF.findall(node["content"])):
                    if ref in targets:
                        rows.append([ref, {"uuid": node["uuid"], "page": self._page_pull(page)}])
        return rows

    def _content_search(self, query):
        """lookup.find_blocks_by_content, substring or all blocks (regex is
        applied by the caller), on one page or everywhere."""
        name = _literal_after(query, ":block/name")
        needle = _literal_after(query, "?c")
        pages = [self._find_page(name)] if name is not None else self.pages
        rows = []
        for page in (p for p in pages if p):
            for node in self._walk(page["blocks"]):
                if needle is None or needle in node["content"]:
                    rows.append([{"content": node["content"], "uuid": node["uuid"],
                                  "page": self._page_pull(page)}])
        return rows

    # --- writes --------------------------------------------------------------
    def _create_page(self, args):
        """The page, with the properties as sent; an existing page comes
        back as it is, the passed properties silently dropped.

        A name in the graph's date format is a journal, property or not.
        Without ``createFirstBlock: false`` the page gets an empty first
        block; passed properties become its property block (the file
        starts with ``journal?:: true``). A journal title in another
        format is created under the graph's name and answered with null.

        The name is looked up as sent, then created as ``create!`` makes it
        (``_created_title``) and answered under that (api.cljs
        ``create_page``; measured, 0.10.15): ``[[X]]`` answers the page X, a page X that
        exists included, its properties sent dropped.
        """
        name = args[0]
        properties = (args[1] if len(args) > 1 else None) or {}
        options = (args[2] if len(args) > 2 else None) or {}
        existing = self._find_page(name)
        if existing is not None:
            return self._page_out(existing)
        name = _created_title(name)
        other_day = next((d for f in _OTHER_JOURNAL_FORMATS if (d := _parse_date(name, f))), None)
        if other_day and not _parse_date(name, self.date_format):
            journal = _format_date(other_day, self.date_format)
            if self._find_page(journal) is None:
                self._create_page([journal, properties, options])
            return None
        existing = self._find_page(name)
        if existing is not None:
            return self._page_out(existing)
        if properties:
            text = "\n".join(f"{k}:: {_value_text(v)}" for k, v in properties.items())
            blocks = [self._build(text)]
        elif options.get("createFirstBlock", True):
            blocks = [self._build("")]
        else:
            blocks = []
        page = self._new_page(name, blocks)
        self._settle(page)
        for node in self._walk(page["blocks"]):
            self._index(node)
        return {**self._page_out(page), "properties": properties}

    def _delete_page(self, args):
        """Answers null, a missing page too. A page other pages name as their
        namespace keeps its entity without blocks (page.cljs ``delete!``;
        measured, 0.10.15: getPage answers it, getPageBlocksTree [])."""
        page = self._find_page(args[0])
        if page is None:
            return None
        if self._namespace_children(page):
            page["blocks"], page["props"] = [], {}
        else:
            self.pages.remove(page)
            self._visible.discard(page["id"])
        return None

    def _namespace_children(self, page) -> list:
        """The pages whose namespace is ``page``: named "<page>/<one level>"."""
        prefix = _key(page["name"]) + "/"
        return [p for p in self.pages if _key(p["name"]).startswith(prefix)
                and "/" not in _key(p["name"])[len(prefix):]]

    def _rename_page(self, args):
        """Answers null; the uuid stays. Case only: originalName changes, name
        not. Empty name: nothing. An existing name: the pages merge, the
        source is gone and its blocks follow the target's. A missing source:
        Logseq's own TypeError.

        Both names are trimmed first, as ``rename!`` trims them (handler/page.cljs,
        0.10.15, read in the source, ``_js_trim``): U+FEFF in front of X is
        X, and merges into a page X."""
        old, new = _js_trim(args[0]), _js_trim(args[1])
        source = self._find_page(old)
        if source is None:
            return {"error": "Cannot read properties of null (reading 'replace')"}
        if not new or new == old:
            # Measured for "" only; blank names are assumed to behave alike.
            # The same name once trimmed: rename! does nothing (name-changed?).
            return None
        target = self._find_page(new)
        if target is None or target is source:
            source["name"] = new
            self._rewrite_links(old, new)
            return None
        target["blocks"].extend(source["blocks"])
        self.pages.remove(source)
        self._rewrite_links(old, target["name"])
        return None

    def _rewrite_links(self, old, new):
        """Logseq rewrites [[Old]] and #Old across the graph (upstream
        page.cljs rename; the form is assumed, not measured)."""
        link = "[[" + new + "]]"
        tag = "#" + new if not re.search(r"[\s#\[\],]", new) else "#" + link
        for page in self.pages:
            for node in self._walk(page["blocks"]):
                text = re.sub(r"\[\[" + re.escape(old) + r"\]\]", lambda m: link,
                              node["content"], flags=re.IGNORECASE)
                node["content"] = re.sub(r"(?:^|(?<=\s))#" + re.escape(old) + r"(?![^\s#\[\],])",
                                         lambda m: tag, text, flags=re.IGNORECASE)

    def _taken(self, uuid):
        return bool(uuid) and (self._locate(uuid.lower()) or uuid.lower() in self.placeholders)

    def _opened(self, page, node, options):
        """An insert opens its block on a visible page unless
        ``focus: false`` (editor.cljs ``api-insert-new-block!``, 0.10.15)."""
        if options.get("focus", True) and page["id"] in self._visible:
            self.editing = node["uuid"]

    def _append_block_in_page(self, args):
        """The new block. A missing page is made first, with its empty first
        block, and the block goes after it (strictinsert.py, measured). It is
        made through createPage's ``create!`` and then looked up under the
        name as sent (api.cljs ``append_block_in_page``, 0.10.15, read in
        the code): a journal title in
        another format makes the journal and answers null."""
        name, content = args[0], args[1]
        options = (args[2] if len(args) > 2 else None) or {}
        wanted = options.get("customUUID")
        if self._taken(wanted):
            # PageGraph, measured: a uuid a block or placeholder holds.
            return {"error": "Custom block UUID already exists"}
        if self._find_page(name) is None:
            self._create_page([name, {}, {}])
        page = self._find_page(name)
        if page is None:
            return None
        node = self._build({"content": content, "uuid": wanted})
        page["blocks"].append(node)
        self._index(node)
        self._opened(page, node, options)
        return self._block_out(page, node, None, True)

    def _insert_block(self, args):
        """The new block. Not ``sibling``: last child (first with ``before``);
        ``sibling``: right after the target (before it with ``before``).
        Unknown target: null."""
        target, content = args[0], args[1]
        options = (args[2] if len(args) > 2 else None) or {}
        wanted = options.get("customUUID")
        if self._taken(wanted):
            return {"error": "Custom block UUID already exists"}
        found = self._locate(str(target).lower()) if _is_uuid(target) else None
        if not found:
            return None
        page, siblings, i, parent = found
        node = self._build({"content": content, "uuid": wanted})
        if not options.get("sibling"):
            kids = siblings[i]["children"]
            kids.insert(0, node) if options.get("before") else kids.append(node)
            parent = siblings[i]
        else:
            siblings.insert(i if options.get("before") else i + 1, node)
        self._index(node)
        self._opened(page, node, options)
        return self._block_out(page, node, parent, True)

    def _insert_batch_block(self, args):
        """null. Positions and ids as PageGraph models them (#31, measured):
        ``sibling: false`` at the head of the anchor's children, ``sibling``
        after the anchor (before it with ``before``); a page uuid anchors at
        the head of the page. With ``keepUUID`` a node takes the uuid of its
        id:: line, without it every id:: line goes. Anchored before a page's
        first block or on a page with none, every node gets "* " in front.

        On a visible page Logseq then opens the last inserted block
        (editor.cljs ``edit-last-block-after-inserted!``, 0.10.15; measured).
        """
        anchor, batch = args[0], args[1]
        options = (args[2] if len(args) > 2 else None) or {}
        keep = bool(options.get("keepUUID"))
        page = next((p for p in self.pages if p["uuid"] == str(anchor).lower()), None)
        if page is not None:
            if options.get("sibling"):
                return None
            target, at, headless = page["blocks"], 0, not page["blocks"]
        else:
            found = self._locate(str(anchor).lower()) if _is_uuid(anchor) else None
            if not found:
                return None
            page, siblings, i, parent = found
            if not options.get("sibling"):
                target, at, headless = siblings[i]["children"], 0, False
            elif options.get("before"):
                target, at, headless = siblings, i, parent is None and i == 0
            else:
                target, at, headless = siblings, i + 1, False
        nodes = [self._build(n, keep=keep, strip_ids=not keep, prefix="* " if headless else "")
                 for n in batch]
        target[at:at] = nodes
        made = list(self._walk(nodes))
        for node in made:
            self._index(node)
        if made and page["id"] in self._visible:
            if self.batch_opens_after_checks:
                self._opening = [made[-1]["uuid"], self.batch_opens_after_checks]
            else:
                self.editing = made[-1]["uuid"]
        return None

    def _track_time(self, old, new):
        """With time tracking on, a marker change to DOING/NOW clocks in, one
        to DONE/LATER/TODO closes the open CLOCK line (upstream
        util/clock.cljs ``clock-in`` and ``clock-out``, 0.10.15; format
        assumed: time tracking was off in the measured graph)."""
        before, after = _marker(old), _marker(new)
        if not self.time_tracking or before == after:
            return new
        lines = new.split("\n")
        if after in ("DOING", "NOW"):
            if ":LOGBOOK:" in lines and ":END:" in lines[lines.index(":LOGBOOK:"):]:
                end = lines.index(":END:", lines.index(":LOGBOOK:"))
                lines.insert(end, CLOCK_IN)
                return "\n".join(lines)
            return "\n".join([*lines, ":LOGBOOK:", CLOCK_IN, ":END:"])
        if after in ("DONE", "LATER", "TODO"):
            open_clocks = [i for i, ln in enumerate(lines)
                           if ln.startswith("CLOCK: [") and "--" not in ln]
            if open_clocks:
                lines[open_clocks[-1]] = CLOCK_OUT
        return "\n".join(lines)

    def _update_block(self, args):
        """Answers null on success, for the same text and for an unknown uuid.
        The text is trimmed on both sides ("new  " → "new"; at the start
        measured for spaces, a tab and blank lines, 0.10.15), and a ref to
        the block itself is dropped ("see ((own)) here" → "see  here",
        measured; editor.cljs ``wrap-parse-block`` replaces the lower-case
        form). ``{"properties":
        {k: v}}`` writes ``k:: v`` lines below the text, a passed key winning
        over the text's own line (#30, #66). A stored id:: line stays when
        the text sent has none (#95). An id:: line becomes the
        block's uuid once the file is read again (#56); modelled at once."""
        uuid, content = args[0], args[1]
        options = (args[2] if len(args) > 2 else None) or {}
        found = self._locate(uuid.lower())
        if not found:
            return None
        page, siblings, i, parent = found
        node = siblings[i]
        properties = options.get("properties") or {}
        lines = content.replace(f"(({node['uuid']}))", "").strip().split("\n")
        for key, value in properties.items():
            lines = [ln for ln in lines if not re.match(rf"[ \t]*{re.escape(key)}:: ", ln)]
            lines.append(f"{key}:: {_value_text(value)}")
        # #95, measured: a stored id stays through an update whose text
        # does not carry it, read back after the text sent.
        kept_id = _property_texts(node["content"]).get("id")
        if kept_id and "id" not in _property_texts("\n".join(lines)):
            lines.append(f"id:: {kept_id}")
        new = "\n".join(lines)
        if new == node["content"]:
            return None
        node["content"] = self._track_time(node["content"], new)
        wanted = _logseq_block_id(node["content"])
        if wanted:
            node["uuid"] = wanted.lower()
        self._saved(page, parent, i, node)
        self._index(node)
        return None

    def _saved(self, page, parent, i, node):
        """#80: saving a page's first block makes it the property block when
        it holds only property lines, and unmakes it otherwise."""
        texts = _only_properties(node["content"])
        if parent is None and i == 0 and texts is not None:
            node["pre"], page["props"] = True, texts
        elif node["pre"]:
            node["pre"], page["props"] = False, {}

    def _remove_block(self, args):
        # Answers null; the block goes with its subtree. Unknown uuid: null.
        found = self._locate(args[0].lower())
        if found:
            page, siblings, i, _ = found
            if siblings[i]["pre"]:
                page["props"] = {}
            del siblings[i]
        return None

    def _upsert_block_property(self, args):
        """Answers null, for an unknown uuid too. Writes ``key:: value`` into the
        text (a list as a,b), in place of the key's line or at the end. The
        page's properties do not follow a property block written this way
        (#80)."""
        uuid, key, value = args[0], args[1], args[2]
        found = self._locate(uuid.lower())
        if not found:
            return None
        node = found[1][found[2]]
        text = _value_text(value)
        line = f"{key}:: {text}"
        lines = node["content"].split("\n") if node["content"] else []
        at = [j for j, ln in enumerate(lines)
              if (m := _PROPERTY_LINE.fullmatch(ln)) and _stored_key(m.group(1)) == _stored_key(key)]
        if at:
            lines[at[0]] = line
            lines = [ln for j, ln in enumerate(lines) if j not in at[1:]]
        else:
            lines.append(line)
        node["content"] = "\n".join(lines)
        if not isinstance(value, str):
            node["typed"][_stored_key(key)] = (text.strip(), value)
        self._index(node)
        return None

    def _remove_block_property(self, args):
        # Answers null, for an unknown uuid and a missing key too.
        uuid, key = args[0], args[1]
        found = self._locate(uuid.lower())
        if found:
            node = found[1][found[2]]
            node["content"] = "\n".join(
                ln for ln in node["content"].split("\n")
                if not ((m := _PROPERTY_LINE.fullmatch(ln))
                        and _stored_key(m.group(1)) == _stored_key(key)))
            node["typed"].pop(_stored_key(key), None)
        return None

    def _move_block(self, args):
        """null. ``children``: last child of the target; else after it
        (before it with ``before``). Into its own subtree: nothing."""
        src, target = str(args[0]).lower(), str(args[1]).lower()
        options = (args[2] if len(args) > 2 else None) or {}
        moving = self._locate(src)
        if not moving or not self._locate(target):
            return None
        if target in {n["uuid"] for n in self._walk([moving[1][moving[2]]])}:
            return None
        _, siblings, i, _ = moving
        node = siblings.pop(i)
        _, t_siblings, t_i, _ = self._locate(target)
        if options.get("children"):
            t_siblings[t_i]["children"].append(node)
        else:
            t_siblings.insert(t_i if options.get("before") else t_i + 1, node)
        return None

    def _set_blocks_id(self, args):
        """null. Stores each asked block's uuid as its id property; skips a
        uuid without a block and a page's property block, leaves a stored id
        as it is (api.set_blocks_id, measured)."""
        for uuid in args[0] or []:
            found = self._locate(str(uuid).lower())
            if not found:
                continue
            node = found[1][found[2]]
            if node["pre"] or "id" in _property_texts(node["content"]):
                continue
            node["content"] = "\n".join(
                x for x in (node["content"], f"id:: {node['uuid']}") if x)
        return None
