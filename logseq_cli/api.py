import json
import os
import time
from dataclasses import dataclass

import requests

# Every write of block text is checked here, whichever command sent it: text
# written as one block must come back from the page file as that block (#47),
# and an id:: line reaches Logseq only where a command decided to keep it
# (#56). The commands check first, for their own way out in the message; this
# is the net no write path can go around.
from logseq_cli.blocktext import (
    block_ref_uuids,
    refuse_id_lines,
    refuse_id_lines_tree,
    refuse_split_block,
    refuse_split_property,
    refuse_split_tree,
    tree_texts,
)


@dataclass(frozen=True)
class Read:
    """Reads data. ``cache``: the answer may be kept for the cache TTL."""
    cache: bool


@dataclass(frozen=True)
class Write:
    """Changes the graph, so the whole cache is cleared after it.

    ``editor`` and ``proof`` name the ``LogseqAPI._gate_<editor>`` and
    ``_prove_<proof>`` methods the central write dispatches on (spec 030).
    Names rather than descriptions: a label nothing acts on can disagree
    with the code. ``None`` until those methods exist.
    """
    editor: str | None
    proof: str | None


@dataclass(frozen=True)
class UI:
    """Asks or changes Logseq's editor, not the graph: no cache either way."""


# Every method call() may send, and what it is. call() refuses anything not
# here: a write nobody registered would otherwise run past the cache, and
# later past the editor gate and the proof, without a trace.
#
# Deliberately absent: logseq.Editor.getPageProperties. It is declared in
# Logseq's plugin API (LSPlugin.ts), which is presumably where the old read
# list was first copied from, but the HTTP server does not expose it — it
# answers `MethodNotExist: get_page_properties` (checked against 0.10.15). It
# was listed from the initial commit and never called even once, so the entry
# claimed a read the tool does not make. Page properties are read through
# get_page plus the first block instead; see the 0.6.0 changelog entry.
_METHODS = {
    "logseq.Editor.getPage": Read(cache=True),
    "logseq.Editor.getBlock": Read(cache=True),
    "logseq.Editor.getPageBlocksTree": Read(cache=True),
    "logseq.Editor.getPageLinkedReferences": Read(cache=True),
    "logseq.Editor.getAllPages": Read(cache=True),
    "logseq.App.getUserConfigs": Read(cache=True),
    "logseq.DB.datascriptQuery": Read(cache=True),
    "logseq.Editor.createPage": Write(editor=None, proof=None),
    "logseq.Editor.deletePage": Write(editor=None, proof=None),
    "logseq.Editor.renamePage": Write(editor=None, proof=None),
    "logseq.Editor.appendBlockInPage": Write(editor=None, proof=None),
    "logseq.Editor.insertBlock": Write(editor=None, proof=None),
    "logseq.Editor.updateBlock": Write(editor=None, proof=None),
    "logseq.Editor.removeBlock": Write(editor=None, proof=None),
    "logseq.Editor.upsertBlockProperty": Write(editor=None, proof=None),
    "logseq.Editor.removeBlockProperty": Write(editor=None, proof=None),
    "logseq.Editor.insertBatchBlock": Write(editor=None, proof=None),
    "logseq.Editor.moveBlock": Write(editor=None, proof=None),
    "logseq.Editor.setBlocksId": Write(editor=None, proof=None),
    "logseq.Editor.checkEditing": UI(),
    "logseq.Editor.exitEditingMode": UI(),
}

# Derived, never listed by hand: the names predate the registry and tests
# import them.
_MUTATING_METHODS = frozenset(m for m, k in _METHODS.items() if isinstance(k, Write))
_CACHEABLE_METHODS = frozenset(
    m for m, k in _METHODS.items() if isinstance(k, Read) and k.cache
)


class UnknownMethod(Exception):
    """call() was asked for a method the registry does not list.

    A programming error, not something a user can cause or fix: the method
    has to be added to ``_METHODS`` with its kind. Deliberately not a
    LookupError: commands catch those as "page not found".
    """

    def __init__(self, method: str):
        super().__init__(
            f"{method} is not in logseq_cli.api._METHODS; register it as "
            "Read, Write or UI before calling it."
        )
        self.method = method


class BadResponseError(RuntimeError):
    """Logseq answered with something that is not JSON.

    A RuntimeError as before, so nothing that caught one changes; its own type
    lets the error handler report it like a transport failure rather than as a
    traceback, now that read errors reach the caller instead of being counted
    as an empty page (#93).
    """


class DatalogQueryError(RuntimeError):
    """A datalog query was rejected by Logseq instead of being executed.

    Logseq answers a broken query with HTTP 200 and ``{"error": ...}`` in the
    body, so without this exception a query that never ran is indistinguishable
    from a query with zero hits. Message and query are attributes, not just
    text: the JSON error output needs structured fields.
    """

    def __init__(self, api_message: str, query: str):
        super().__init__(f"Datalog query failed: {api_message}")
        self.api_message = api_message
        self.query = query


class InvalidPortError(ValueError):
    """The configured port is not a port.

    Raised before the first request, because the alternative is worse: an
    unchecked value travels into the URL and comes back one step later as
    "no listener" — the same message a correct port gets when Logseq is not
    running. Two causes, one message, and the wrong one is the one people act
    on.
    """

    def __init__(self, value: str, source: str = "LOGSEQ_PORT"):
        super().__init__(
            f"Invalid port {value!r}: {source} must be a number "
            f"between 1 and 65535."
        )
        self.value = value
        self.source = source


def _validated_port(value: str, source: str = "LOGSEQ_PORT") -> str:
    """Return ``value`` if it names a usable TCP port, else raise.

    ``source`` names where the value came from, because that is what the
    reader has to go and change: pointing at LOGSEQ_PORT when the value came
    from ``--port`` sends them to a setting that is not the one in effect.

    Surrounding whitespace is stripped rather than rejected: a trailing
    newline is what a shell pipeline leaves behind, and the value is usable
    once it is gone. Returned as a string because that is what the URL needs;
    the int is only the check.
    """
    stripped = value.strip()
    try:
        port = int(stripped)
    except ValueError:
        raise InvalidPortError(value, source) from None
    if not 1 <= port <= 65535:
        raise InvalidPortError(value, source)
    return stripped


def _without_focus(options: dict | None) -> dict:
    """Insert options with ``focus: false``, whatever the caller passed.

    Unset, Logseq takes focus as true and opens the new block in its editor
    (api.cljs:603, editor.cljs:647); on the visible page the cursor of
    whoever is typing jumps into it and the rest of their typing lands there
    (M10, spec 030). No caller wants that, so a caller's own focus is
    overridden.
    """
    return {**(options or {}), "focus": False}


class LogseqAPI:
    def __init__(self, host=None, port=None, token=None):
        self.host = host or os.getenv("LOGSEQ_HOST", "127.0.0.1")
        port_source = "--port" if port else "LOGSEQ_PORT"
        self.port = port or os.getenv("LOGSEQ_PORT", "12315")
        self.token = token or os.getenv("LOGSEQ_TOKEN", "")
        # Only checked when the port is actually used. LOGSEQ_API_URL replaces
        # the assembled URL, and a LOGSEQ_PORT left over in a shell profile
        # must not fail a run that never reads it.
        explicit_url = os.getenv("LOGSEQ_API_URL")
        if explicit_url:
            self.base_url = explicit_url
        else:
            self.port = _validated_port(self.port, port_source)
            self.base_url = f"http://{self.host}:{self.port}/api"
        try:
            ttl = int(os.getenv("LOGSEQ_CLI_CACHE_TTL", "60"))
        except ValueError:
            ttl = 60
        self._cache_ttl = max(0, ttl)
        self.cache_enabled = self._cache_ttl > 0
        self._cache = {}

    def _cache_key(self, method, args):
        try:
            return (method, json.dumps(args, sort_keys=True, default=str))
        except (TypeError, ValueError):
            return (method, repr(args))

    def _cache_get(self, key):
        entry = self._cache.get(key)
        if not entry:
            return None
        value, expires = entry
        if expires < time.monotonic():
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key, value):
        self._cache[key] = (value, time.monotonic() + self._cache_ttl)

    def clear_cache(self):
        self._cache.clear()

    def _post(self, method: str, args: list):
        """Send one request and hand back the raw response.

        The one place a request leaves the process, and therefore the one
        place the registry is enforced: nothing unregistered goes out, however
        it is sent. The answer is not parsed here, because checkEditing
        answers with raw text instead of JSON. ``requests.post`` is looked up
        on the module at call time, with the arguments it always had, so
        tests that patch it keep working.
        """
        if method not in _METHODS:
            raise UnknownMethod(method)
        resp = requests.post(
            self.base_url,
            json={"method": method, "args": args},
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp

    def call(self, method: str, args: list = None):
        args = args or []
        # None for an unregistered method, which _post refuses before sending.
        kind = _METHODS.get(method)
        cacheable = self.cache_enabled and isinstance(kind, Read) and kind.cache
        if cacheable:
            key = self._cache_key(method, args)
            cached = self._cache_get(key)
            if cached is not None:
                return cached
        else:
            key = None

        resp = self._post(method, args)
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError:
            raise BadResponseError(f"Logseq API returned non-JSON response: {resp.text[:200]}")

        if cacheable and key is not None:
            # Error payloads must not outlive their cause: a cached
            # {"error": ...} would keep answering as a failure for up to TTL
            # seconds after the caller fixed the input.
            if not (isinstance(data, dict) and "error" in data):
                self._cache_set(key, data)
        elif isinstance(kind, Write):
            self.clear_cache()

        return data

    def get_all_pages(self):
        return self.call("logseq.Editor.getAllPages")

    def get_page_blocks_tree(self, page_name: str):
        return self.call("logseq.Editor.getPageBlocksTree", [page_name])

    def get_page(self, page_name: str):
        return self.call("logseq.Editor.getPage", [page_name])

    def get_block(self, block_id: str, include_children: bool = True):
        """The block, or ``None`` when Logseq has none under ``block_id``.

        An unknown uuid comes back as ``null``, a malformed one as HTTP 200 with
        ``{"error": "... is not a valid UUID string."}`` (measured, 0.10.15).
        Both mean "no such block"; passing the error object on made it look
        like one to every ``if not block`` guard.
        """
        result = self.call(
            "logseq.Editor.getBlock", [block_id, {"includeChildren": include_children}]
        )
        if isinstance(result, dict) and "error" in result and "uuid" not in result:
            return None
        return result

    def create_page(self, page_name: str, properties: dict = None, *, first_block: bool = True):
        """Create a page. A name in the graph's date format is a journal.

        Logseq tells a journal by its name alone (M18, spec 030); a
        ``journal?`` property is not needed and lands as a line
        ``journal?:: true`` at the top of the file. ``first_block=False`` is
        for a caller that writes right after: otherwise the page starts with
        an empty block. A page created without a first block and without
        text gets no file (measured), so a caller that writes nothing keeps it.
        """
        # Without redirect: false, Logseq turns its view to the new page (M13,
        # spec 030) -- every page and journal the CLI created moved the view.
        options = {"redirect": False}
        if not first_block:
            options["createFirstBlock"] = False
        return self.call("logseq.Editor.createPage", [page_name, properties or {}, options])

    def append_block_in_page(self, page_name: str, content: str, options: dict = None):
        # Options reach insertBlock unchanged (append_block_in_page in api.cljs),
        # so customUUID works here as it does there, and is refused the same way
        # for a placeholder's uuid. --keep-ids writes go through insertBatchBlock
        # instead (#31).
        refuse_split_block(content, command="logseq-cli", where="The text")
        refuse_id_lines(content)
        self._store_ref_target_ids([content])
        return self.call(
            "logseq.Editor.appendBlockInPage",
            [page_name, content, _without_focus(options)],
        )

    def insert_block(self, block_uuid: str, content: str, options: dict = None):
        refuse_split_block(content, command="logseq-cli", where="The text")
        refuse_id_lines(content)
        self._store_ref_target_ids([content])
        return self.call(
            "logseq.Editor.insertBlock", [block_uuid, content, _without_focus(options)]
        )

    def insert_batch_block(self, block_uuid: str, batch: list, options: dict = None):
        """Insert a whole tree in ONE call. Returns null even on success.

        ``insertBatchBlock`` writes an arbitrarily deep ``[{content, children}]``
        tree against a single anchor, where :meth:`insert_block` needs one call
        per node. It answers ``null`` both when it wrote and when it did not, so
        the return value carries no success signal at all: callers must verify by
        reading the anchor's children back (see
        :func:`strictinsert.insert_block_tree_batched`).

        Positioning also differs from :meth:`insert_block`: with
        ``sibling: false`` the batch lands at the HEAD of the child list and
        ``before: false`` does not change that, so appending means anchoring on
        the last existing child with ``sibling: true``.
        """
        refuse_split_tree(batch, command="logseq-cli", single_label="The text")
        # With keepUUID the id:: lines are the point of the call, vetted by
        # check_block_ids. Without it Logseq drops them (measured), so none
        # should arrive: one that does was decided on by no command.
        if not (options or {}).get("keepUUID"):
            refuse_id_lines_tree(batch)
        self._store_ref_target_ids(tree_texts(batch))
        return self.call(
            "logseq.Editor.insertBatchBlock", [block_uuid, batch, options or {}]
        )

    def move_block(self, src_uuid: str, target_uuid: str, options: dict = None):
        """Move a block (with its children) next to / under ``target_uuid``.

        Structural move, unlike copy+remove: the block keeps its UUID, so
        ``((block-ref))`` backlinks survive.
        """
        return self.call(
            "logseq.Editor.moveBlock", [src_uuid, target_uuid, options or {}]
        )

    def update_block(self, block_uuid: str, content: str, properties: dict = None, *,
                     replacing: str = None):
        """Replace a block's content, optionally carrying its properties along.

        Block properties live *inside* the content (``prio:: 1`` as a line of
        the same block), so a plain content replacement drops every one of
        them. Passing them through the documented third parameter
        (``opts.properties``) makes Logseq re-emit them below the new text.

        Logseq writes each entry out as ``key:: value`` text, so what goes in
        decides what lands in the file. Pass the original text under the keys
        the database stores (``blockprops.stored_properties``), not the block's
        own ``properties`` map: that one is camel-cased by the API and parsed,
        and writing it back turned ``due-date::`` into ``duedate::`` and
        ``zip:: 01234`` into ``zip:: 1234`` (measured, Logseq 0.10.15). ``id::``
        is part of the map and is written back unchanged, so block references
        stay intact. A passed key wins over a line of ``content`` with the same
        key (#66): a caller replacing a block's text takes the set from
        ``blockprops.kept_properties``, which leaves out what the text sets.

        ``replacing`` is the text this replaces, for a caller that changes a
        block rather than writing one: a line it already had passes the check
        every write here goes through (#47).
        """
        refuse_split_block(content, command="logseq-cli", where="The text", replacing=replacing)
        refuse_id_lines(content, own=block_uuid, replacing=replacing)
        # Properties carried along are written again, a ref among them too.
        self._store_ref_target_ids([content, *map(str, (properties or {}).values())],
                                   own=block_uuid)
        args = [block_uuid, content]
        if properties:
            args.append({"properties": properties})
        return self.call("logseq.Editor.updateBlock", args)

    def _store_ref_target_ids(self, contents: list, own: str = None) -> None:
        """Store the id of every Block Ref target in ``contents`` that has
        none yet, before the text holding the refs is written (#95).

        Logseq's editor does this when a ref is copied (``set-blocks-id!``,
        exported as ``setBlocksId``). Without it Logseq adds the Id Line to the
        target itself, in column 0 and outside the database, and the next
        property write on the target drops it from the file (measured,
        0.10.15). A page uuid, a dead ref and a target that has its id are
        left alone, as is ``own``: the block being written replaces its text.

        Before the write, not after: then Logseq adds no column-0 line at all
        (measured), and updateBlock and insertBatchBlock answer null either
        way, so "after it landed" cannot be told. A write that then fails
        leaves the target with its id, as a copied ref in the editor does.
        """
        wanted = []
        for uuid in dict.fromkeys(u for c in contents for u in block_ref_uuids(c)):
            if uuid == (own or "").lower():
                continue
            block = self.get_block(uuid, include_children=False)
            if block and block.get("page") and not (block.get("properties") or {}).get("id"):
                wanted.append(block["uuid"])
        if wanted:
            self.set_blocks_id(wanted)

    def set_blocks_id(self, block_uuids: list):
        """Store each block's uuid as its ``id`` property, as Logseq's editor
        does for a copied ref. Not in the plugin API's declarations, but
        exported (``logseq.api/set_blocks_id``, 0.10.15); it skips a uuid no
        block has and a page's property block, and leaves a stored id as it
        is, file untouched (measured)."""
        return self.call("logseq.Editor.setBlocksId", [block_uuids])

    def remove_block(self, block_uuid: str):
        return self.call("logseq.Editor.removeBlock", [block_uuid])

    def get_page_linked_references(self, page_name: str):
        """Get backlinks using native Logseq API (faster than brute-force search)."""
        return self.call("logseq.Editor.getPageLinkedReferences", [page_name])

    def upsert_block_property(self, block_uuid: str, key: str, value):
        """Set or update a property on a block."""
        refuse_split_property(key, value)
        self._store_ref_target_ids([str(value)], own=block_uuid)
        return self.call("logseq.Editor.upsertBlockProperty", [block_uuid, key, value])

    def remove_block_property(self, block_uuid: str, key: str):
        """Remove a property from a block."""
        return self.call("logseq.Editor.removeBlockProperty", [block_uuid, key])

    def rename_page(self, old_name: str, new_name: str):
        """Rename a page."""
        return self.call("logseq.Editor.renamePage", [old_name, new_name])

    def delete_page(self, page_name: str):
        """Delete a page."""
        return self.call("logseq.Editor.deletePage", [page_name])

    def get_user_configs(self):
        """Get user configuration including preferredDateFormat."""
        return self.call("logseq.App.getUserConfigs")

    def datascript_query(self, query: str):
        result = self.call("logseq.DB.datascriptQuery", [query])
        # Logseq answers a rejected query with HTTP 200 + {"error": ...} in the
        # body; success is always a list. The check lives here and not in
        # call(): only for datalog is a dict unambiguously a failure, other
        # methods may carry an "error" field legitimately.
        if isinstance(result, dict) and "error" in result:
            raise DatalogQueryError(str(result["error"]), query)
        return result
