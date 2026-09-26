import json
import os
import re
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
from logseq_cli.outlinetext import subtree_uuids
# Raised by the writes below; imported here too so that callers can take them
# from the API they call (spec 030). They live apart to keep imports acyclic.
from logseq_cli.writerefused import (  # noqa: F401  re-exported
    EditorOpen,
    EditorStateUnknown,
    LogseqWriteError,
    PageExists,
    RenameRefused,
    WriteNotVerified,
    WriteRefused,
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
    with the code. ``proof`` is ``None`` for a write whose proof still sits
    with its callers (030-C*).

    ``proof`` says how the write is shown to have landed:

    * ``uuid``: the answer carries the new block's uuid. insertBlock and
      appendBlockInPage answer the block they wrote, and ``null`` for one
      they did not (an unknown anchor, a page not loaded).

    ``editor`` says when a block open in Logseq's editor refuses the write,
    given the open block and the write's target:

    * ``target``: the target is the open block;
    * ``subtree``: the open block is the target or below it (a removed or
      moved block takes its children along; the anchor of a move does not
      count, a block beside the open one changes nothing in it, M10);
    * ``requested``: the open block is one of the blocks asked for;
    * ``page``: the open block is on the target page;
    * ``page_or_link``: ... or refers to it (Logseq rewrites the link on a
      rename, and the open editor would save the old text back);
    * ``any``: any block is open. insertBatchBlock opens its last block in
      the editor once the page is on screen, with no option against it
      (editor.cljs:1998, M16): the cursor would leave the block being typed
      in (E2);
    * ``never``: an insert. Logseq saves the open block before it inserts
      (M10), so it neither asks nor refuses.
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
    "logseq.Editor.createPage": Write(editor="never", proof=None),
    "logseq.Editor.deletePage": Write(editor="page", proof=None),
    "logseq.Editor.renamePage": Write(editor="page_or_link", proof=None),
    "logseq.Editor.appendBlockInPage": Write(editor="never", proof="uuid"),
    "logseq.Editor.insertBlock": Write(editor="never", proof="uuid"),
    "logseq.Editor.updateBlock": Write(editor="target", proof=None),
    "logseq.Editor.removeBlock": Write(editor="subtree", proof=None),
    "logseq.Editor.upsertBlockProperty": Write(editor="target", proof=None),
    "logseq.Editor.removeBlockProperty": Write(editor="target", proof=None),
    "logseq.Editor.insertBatchBlock": Write(editor="any", proof=None),
    "logseq.Editor.moveBlock": Write(editor="subtree", proof=None),
    "logseq.Editor.setBlocksId": Write(editor="requested", proof=None),
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


def rename_refused(old_name: str, new_name: str, why: str) -> RenameRefused:
    """The refusal for ``why`` from LogseqAPI.rename_refusal.

    One wording for the run and the preview. ``new`` is the name as given,
    so the caller finds their own input in it; it is compared stripped.
    """
    if why == "empty":
        message = (f"Refused to rename '{old_name}': the new name is empty. "
                   "Logseq would do nothing and answer as for a rename.")
    else:
        message = (f"Refused to rename '{old_name}' to '{new_name.strip()}': a page of "
                   "that name exists, and Logseq would merge the two, "
                   f"'{old_name}' gone and its blocks moved there. Choose a name "
                   "no page has.")
    return RenameRefused(message, old=old_name, new=new_name, why=why)


# Default of _write's ``editing``: "not asked yet", since None is an answer
# (no block open).
_UNSET = object()

# checkEditing's answer when a block is open: its uuid (M8).
_UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _block_uuid_from_result(result):
    """The uuid in an answer of insertBlock or appendBlockInPage, or ``None``.

    Logseq answers the block map ``{"uuid": ...}``, or ``null`` when it wrote
    nothing (an unknown anchor, a page not loaded: HTTP 200 either way). A
    bare uuid string is taken too; not seen from 0.10.15, but the check has
    always allowed it, and dropping it would fail a write that landed.
    """
    if isinstance(result, dict):
        return result.get("uuid")
    if isinstance(result, str):
        return result
    return None


def _target_text(target) -> str:
    """A write's target as a message names it: a block by the start of its
    uuid, a page by its name in quotes."""
    if isinstance(target, str) and _UUID_TEXT.fullmatch(target):
        return f"block {target[:8]}..."
    return f"'{target}'"


def _not_verified(method: str, target, expected: str, got: str) -> WriteNotVerified:
    """The WriteNotVerified for a write that did not show in Logseq.

    ``method`` is the full name or the short one; the error names the short.
    Whether earlier writes of the call landed, the error handler adds.
    """
    short = method.rsplit(".", 1)[-1]
    return WriteNotVerified(
        f"{short} on {_target_text(target)} did not show in Logseq: "
        f"expected {expected}, read {got}.",
        method=short, target=target, expected=expected, got=got,
    )


# The reasons EditorOpen gives. A write into the open block loses what is
# typed there; a batch beside it only moves the cursor, since Logseq saves
# the open block before it inserts (M10).
DISCARDS_TYPING = "writing now would discard what is being typed there"
MOVES_CURSOR = "a batch insert would move the cursor out of the block being edited"

# How often LogseqAPI asks after a batch whether Logseq opened a block.
_BATCH_EDITOR_POLL_S = 0.01


class LogseqAPI:
    # After a batch, how long to watch for the block Logseq opens in its
    # editor: it opened 16–34 ms after the answer (M16), so 100 ms, counted
    # as ten naps of 10 ms; the checkEditing requests between them come on
    # top. A page not on screen opens nothing, and the window runs full for
    # each multi-block write. Attributes, so that tests need not wait:
    # conftest sets the window to 0, a test of the window replaces _sleep.
    batch_editor_wait_s = 0.1
    _sleep = staticmethod(time.sleep)

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
        # Writes of this CLI call that landed, for the partial state every
        # refusal reports (spec 030): one instance per call (group.py), so the
        # count is the call's, and no command has to keep its own.
        self.writes_landed = 0

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
            # Logseq answers a write it threw on with HTTP 200 and
            # {"error": ...} (M1, M2, M6, M9), which went on as the result and
            # read as success. The test is get_block's: a block map carries a
            # uuid, an error object does not. Writes only; a read's error
            # object stays with the method that knows what it means.
            if isinstance(data, dict) and "error" in data and "uuid" not in data:
                short = method.rsplit(".", 1)[-1]
                message = str(data["error"])
                raise LogseqWriteError(
                    f"Logseq refused {short}: {message.rstrip('.')}.",
                    method=short,
                    logseq_message=message,
                )

        return data

    def _write(self, method: str, args: list, *, target, texts=(), own=None,
               editing=_UNSET, count: int = 1):
        """Send one write the way its ``_METHODS`` entry says, and count it.

        Every public write goes through here, so the steps below hold for all
        of them and no command can go around one (spec 030):

        1. ask checkEditing once, if the entry can refuse or ``texts`` hold
           Block Refs (``editing``, when given, is that answer already, so
           set_blocks_id does not ask again);
        2. find the Block Ref targets in ``texts`` that need an id, ``own``
           excepted (reads only);
        3. refuse if the open block is one the entry's ``editor`` rule
           protects;
        4. store the targets' ids with the answer from step 1, which refuses
           if one of them is the open block (setBlocksId writes into it);
        5. the write;
        6. prove it, through ``_prove_<proof>``, which answers the result the
           method returns;
        7. ``count`` more writes landed (a batch counts its blocks): after
           the proof, so a write that fails it does not count. A write with
           no proof yet counts on Logseq's answer.

        Asked before the ids are stored, not after: setBlocksId would
        otherwise write an id:: into a block for a write the gate then
        refuses. Between the question and the write lie only reads (the ref
        targets, and the open block's place when one is open); the
        milliseconds in which someone can enter a block there stay (spec 030,
        named).

        Step 6 is skipped for an entry whose ``proof`` is None, because the
        entry says so (030-C*), not because a method is missing: a name with
        no ``_prove_`` method behind it fails at the lookup instead of passing
        as a check nobody made (and test_api_endpoint_binding names it).
        """
        kind = _METHODS[method]
        can_refuse = kind.editor not in (None, "never")
        refs = [u for t in texts for u in block_ref_uuids(t)]
        if editing is _UNSET:
            editing = self.check_editing() if can_refuse or refs else None
        wanted = self._ref_targets_needing_id(refs, own=own)
        if editing is not None and can_refuse:
            getattr(self, f"_gate_{kind.editor}")(editing, target)
        # The ref targets are gated by setBlocksId's own rule, with the same
        # answer: it is the write into them, and the first write of all.
        if wanted:
            self.set_blocks_id(wanted, editing=editing)
        result = self.call(method, args)
        if kind.proof is not None:
            result = getattr(self, f"_prove_{kind.proof}")(method, target, result)
        self.writes_landed += count
        return result

    # The proofs of _METHODS, one per ``Write.proof`` name. Each gets the
    # method, the write's target and Logseq's answer, raises WriteNotVerified
    # when the write does not show, and answers what the method returns.
    def _prove_uuid(self, method, target, result):
        """The block, from an answer that names it; a bare uuid becomes
        ``{"uuid": ...}``, so every caller reads ``result["uuid"]``."""
        uuid = _block_uuid_from_result(result)
        if not uuid:
            raise _not_verified(method, target, "a new block", "no block uuid in the answer")
        return result if isinstance(result, dict) else {"uuid": uuid}

    def check_editing(self) -> str | None:
        """The uuid of the block open in Logseq's editor, lower case, or
        ``None`` when none is.

        Logseq answers with raw text, not JSON (M8): the uuid, or ``false``;
        the same values as JSON are taken too. Anything else fails closed
        with EditorStateUnknown: a write on an answer nobody understood could
        be the one that discards what is being typed. A timeout, a refused
        connection and an HTTP error come from _post and go the known way.
        """
        text = self._post("logseq.Editor.checkEditing", []).text
        try:
            value = json.loads(text)
        except ValueError:
            value = text.strip()
        if value is False:
            return None
        if isinstance(value, str) and _UUID_TEXT.fullmatch(value):
            return value.lower()
        answer = text[:80]
        raise EditorStateUnknown(
            f"Logseq's answer to checkEditing was not understood ({answer!r}); "
            "the write was not sent.",
            answer=answer,
        )

    def exit_editing_mode(self):
        """Close Logseq's editor, saving the open block (M10). Answers null."""
        return self.call("logseq.Editor.exitEditingMode")

    # The editor rules of _METHODS, one per ``Write.editor`` name. Each gets
    # the open block's uuid (lower case) and the write's target, and refuses
    # through refuse_open. Only called when a block is open, so the reads
    # that place it cost nothing while nobody types.
    def _gate_never(self, editing, target):
        """Inserts; _write does not call it (``never`` asks nothing)."""

    def _gate_target(self, editing, uuid):
        # Lower case on both sides: update-block passes --id through as
        # typed, checkEditing answers in lower case, and compared as typed
        # the write went past the gate (M8).
        if editing == uuid.lower():
            self.refuse_open(editing)

    def _gate_subtree(self, editing, uuid):
        if editing == uuid.lower():
            self.refuse_open(editing)
        block = self.get_block(uuid, include_children=True)
        if block and editing in {u.lower() for u in subtree_uuids(block)}:
            self.refuse_open(editing)

    def _gate_requested(self, editing, uuids):
        if editing in {u.lower() for u in uuids}:
            self.refuse_open(editing)

    def _gate_any(self, editing, target):
        self.refuse_open(editing, why=MOVES_CURSOR)

    def _gate_page(self, editing, page_name):
        self._gate_page_or_link(editing, page_name, links=False)

    def _gate_page_or_link(self, editing, page_name, *, links=True):
        # A link is found through the open block's refs, which list every
        # page it refers to, as [[Page]], #Page or a property value alike
        # (M15): no parsing of the text here.
        open_block = self.get_block(editing, include_children=False)
        page = self.get_page(page_name)
        if not (open_block and isinstance(page, dict) and page.get("id") is not None):
            return
        ids = {(open_block.get("page") or {}).get("id")}
        if links:
            ids |= {r.get("id") for r in open_block.get("refs") or [] if isinstance(r, dict)}
        if page["id"] in ids:
            self.refuse_open(editing, open_block)

    def refuse_open(self, editing, open_block=None, *, why=DISCARDS_TYPING):
        """Raise EditorOpen for the open block, naming its page.

        Public for strictinsert, which refuses a --keep-ids write before its
        first write (E2) with its own ``why``."""
        if open_block is None:
            open_block = self.get_block(editing, include_children=False)
        page_id = ((open_block or {}).get("page") or {}).get("id")
        page = self.get_page(page_id) if page_id is not None else None
        page_name = page.get("originalName") or page.get("name") if isinstance(page, dict) else None
        on = f" on '{page_name}'" if page_name else ""
        raise EditorOpen(
            f"Block {editing}{on} is open in Logseq's editor; {why}. "
            "Leave the block, then retry.",
            block=editing, page=page_name,
        )

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
        return self._write("logseq.Editor.createPage",
                           [page_name, properties or {}, options], target=page_name)

    def append_block_in_page(self, page_name: str, content: str, options: dict = None):
        # Options reach insertBlock unchanged (append_block_in_page in api.cljs),
        # so customUUID works here as it does there, and is refused the same way
        # for a placeholder's uuid. --keep-ids writes go through insertBatchBlock
        # instead (#31).
        refuse_split_block(content, command="logseq-cli", where="The text")
        refuse_id_lines(content)
        return self._write(
            "logseq.Editor.appendBlockInPage",
            [page_name, content, _without_focus(options)],
            target=page_name, texts=[content],
        )

    def insert_block(self, block_uuid: str, content: str, options: dict = None):
        """Insert one block next to or under ``block_uuid``; answers the new
        block as a dict with its ``uuid``, or raises WriteNotVerified when
        Logseq's answer names none (``_prove_uuid``). The same holds for
        :meth:`append_block_in_page`."""
        refuse_split_block(content, command="logseq-cli", where="The text")
        refuse_id_lines(content)
        return self._write(
            "logseq.Editor.insertBlock", [block_uuid, content, _without_focus(options)],
            target=block_uuid, texts=[content],
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
        texts = list(tree_texts(batch))
        result = self._write(
            "logseq.Editor.insertBatchBlock", [block_uuid, batch, options or {}],
            target=block_uuid, texts=texts, count=len(texts),
        )
        self._close_editor_the_batch_opened()
        return result

    def _close_editor_the_batch_opened(self):
        """Leave the block the batch just opened in Logseq's editor, if it did.

        The gate (``any``) let the batch through only with no block open, so a
        block open now is the batch's (E2). Left open, it would refuse the
        agent's next write to it with open_in_editor. Here rather than in
        strictinsert, so it holds for every batch, --keep-ids included, and
        needs none of the new uuids. Logseq opens it asynchronously (M16), so
        it is asked again every 10 ms within ``batch_editor_wait_s``.
        Someone entering a block in that window has it closed; Logseq saves on
        leaving (M10), nothing is lost.
        """
        polls = round(self.batch_editor_wait_s / _BATCH_EDITOR_POLL_S)
        for poll in range(polls + 1):
            if self.check_editing() is not None:
                self.exit_editing_mode()
                return
            if poll < polls:
                self._sleep(_BATCH_EDITOR_POLL_S)

    def move_block(self, src_uuid: str, target_uuid: str, options: dict = None):
        """Move a block (with its children) next to / under ``target_uuid``.

        Structural move, unlike copy+remove: the block keeps its UUID, so
        ``((block-ref))`` backlinks survive.
        """
        return self._write(
            "logseq.Editor.moveBlock", [src_uuid, target_uuid, options or {}],
            target=src_uuid,
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
        texts = [content, *map(str, (properties or {}).values())]
        args = [block_uuid, content]
        if properties:
            args.append({"properties": properties})
        return self._write("logseq.Editor.updateBlock", args,
                           target=block_uuid, texts=texts, own=block_uuid)

    def _ref_targets_needing_id(self, refs: list, own: str = None) -> list:
        """The Block Ref targets among ``refs`` (lower case, from
        ``block_ref_uuids``) that have no id yet; _write stores theirs before
        the text holding the refs is written (#95). Only reads: the editor
        gate comes between this and the storing.

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
        for uuid in dict.fromkeys(refs):
            if uuid == (own or "").lower():
                continue
            block = self.get_block(uuid, include_children=False)
            if block and block.get("page") and not (block.get("properties") or {}).get("id"):
                wanted.append(block["uuid"])
        return wanted

    def set_blocks_id(self, block_uuids: list, *, editing=_UNSET):
        """Store each block's uuid as its ``id`` property, as Logseq's editor
        does for a copied ref. Not in the plugin API's declarations, but
        exported (``logseq.api/set_blocks_id``, 0.10.15); it skips a uuid no
        block has and a page's property block, and leaves a stored id as it
        is, file untouched (measured).

        ``editing`` is checkEditing's answer when the caller has it already
        (_write, before the write whose refs these are), so it is not asked
        twice."""
        return self._write("logseq.Editor.setBlocksId", [block_uuids], target=block_uuids,
                           editing=editing)

    def remove_block(self, block_uuid: str):
        return self._write("logseq.Editor.removeBlock", [block_uuid], target=block_uuid)

    def get_page_linked_references(self, page_name: str):
        """Get backlinks using native Logseq API (faster than brute-force search)."""
        return self.call("logseq.Editor.getPageLinkedReferences", [page_name])

    def upsert_block_property(self, block_uuid: str, key: str, value):
        """Set or update a property on a block."""
        refuse_split_property(key, value)
        return self._write("logseq.Editor.upsertBlockProperty", [block_uuid, key, value],
                           target=block_uuid, texts=[str(value)], own=block_uuid)

    def remove_block_property(self, block_uuid: str, key: str):
        """Remove a property from a block."""
        return self._write("logseq.Editor.removeBlockProperty", [block_uuid, key],
                           target=block_uuid)

    def rename_refusal(self, old_name: str, new_name: str) -> str | None:
        """Why renaming ``old_name`` to ``new_name`` is refused, or ``None``.

        ``"empty"``: nothing is left of the name once stripped; Logseq does
        nothing and answers null, as for a rename (M4). ``"exists"``: getPage
        finds another page under the name, and Logseq would merge the two into
        it, the old page gone and its blocks under the other (M4). The same
        page, by uuid, is a change of case only, a rename that works.

        Not named for an endpoint: it wraps none. Shared by rename_page and
        rename-page --dry-run, so the preview refuses what the run refuses.
        """
        name = new_name.strip()
        if not name:
            return "empty"
        taken = self.get_page(name)
        if not taken:
            return None
        own = self.get_page(old_name)
        if own and own.get("uuid") == taken.get("uuid"):
            return None
        return "exists"

    def rename_page(self, old_name: str, new_name: str):
        """Rename a page; the new name is sent stripped.

        Raises RenameRefused before anything is sent, see rename_refusal."""
        why = self.rename_refusal(old_name, new_name)
        if why:
            raise rename_refused(old_name, new_name, why)
        return self._write("logseq.Editor.renamePage", [old_name, new_name.strip()],
                           target=old_name)

    def delete_page(self, page_name: str):
        """Delete a page."""
        return self._write("logseq.Editor.deletePage", [page_name], target=page_name)

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
