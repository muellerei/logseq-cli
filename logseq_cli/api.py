import json
import os
import re
import time
from dataclasses import dataclass

import requests

# The reader of stored property keys and texts, for the property proofs.
from logseq_cli.blockprops import stored_properties
# Every write of block text is checked here, whichever command sent it: text
# written as one block must come back from the page file as that block (#47),
# and an id:: line reaches Logseq only where a command decided to keep it
# (#56). The commands check first, for their own way out in the message; this
# is the net no write path can go around.
from logseq_cli.blocktext import (
    PROPERTY_LINE_RE,
    block_ref_uuids,
    block_text_matches,
    refuse_id_lines,
    refuse_id_lines_tree,
    refuse_split_block,
    refuse_split_property,
    refuse_split_tree,
    stored_property_key,
    tree_texts,
)
from logseq_cli.outlinetext import preorder_blocks, subtree_uuids
from logseq_cli.pagenames import js_trim, page_name_to_create, title_as_created
# Raised by the writes below; imported here too so that callers can take them
# from the API they call. They live in a leaf module, so that
# strictinsert and output need not import the HTTP client for them.
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
    ``_prove_<proof>`` methods the central write dispatches on.
    Names rather than descriptions: a label nothing acts on can disagree
    with the code.

    ``proof`` says how the write is shown to have landed:

    * ``uuid``: the answer carries the new block's uuid. insertBlock and
      appendBlockInPage answer the block they wrote, and ``null`` for one
      they did not (an unknown anchor, a page not loaded).
    * ``batch``: the place the batch lands, read before and after the write,
      holds as many new blocks as were sent, with the texts sent, in order.
    * ``move``: the moved block is read back where it was sent.
    * ``text``: the block's text is read back, less the lines of the
      properties sent along, which are read back as values.
    * ``property``: the block holds the key with the value sent.
    * ``no_property``: the block no longer holds the key.
    * ``no_block``: no block is found under the uuid.
    * ``no_page``: no page is found under the name, or one without blocks
      that is the namespace of others.
    * ``renamed``: the new name finds the page's uuid, spelt as sent.
    * ``page``: the answer names a page, and the name sent finds that page.
    * ``ids``: each block asked for holds ``id`` among its properties.

    Why each is proven the way it is: the note above ``_METHODS``.

    ``editor`` says when a block open in Logseq's editor refuses the write,
    given the open block and the write's target:

    * ``target``: the target is the open block;
    * ``subtree``: the open block is the target or below it (a removed or
      moved block takes its children along; the anchor of a move does not
      count, a block beside the open one changes nothing in it, measured);
    * ``requested``: the open block is one of the blocks asked for;
    * ``page``: the open block is on the target page;
    * ``page_or_link``: ... or refers to it (Logseq rewrites the link on a
      rename, and the open editor would save the old text back);
    * ``any``: any block is open. insertBatchBlock opens its last block in
      the editor once the page is on screen, with no option against it
      (editor.cljs ``edit-last-block-after-inserted!``, 0.10.15; measured):
      the cursor would leave the block being
      typed in;
    * ``never``: an insert. Logseq saves the open block before it inserts
      (measured), so it neither asks nor refuses.
    """
    editor: str
    proof: str


@dataclass(frozen=True)
class UI:
    """Asks or changes Logseq's editor, not the graph: no cache either way."""


# Every method call() may send, and what it is. call() refuses anything not
# here: a write nobody registered would otherwise run past the cache, and
# later past the editor gate and the proof, without a trace.
#
# How a write is proven, and why (measured on 0.10.15, the batch
# and the move against a live graph on 2026-08-22)
# ------------------------------------------------------------------------
# Logseq answers most writes with null, whether it wrote or not, and a write
# it threw on with HTTP 200 and {"error": ...}. call() turns the
# error object into LogseqWriteError for every write; the null has to be
# proven away, each method by what it can show:
#
#   insertBlock, appendBlockInPage  answer the new block (null when nothing
#       was written), so the answer is the proof and nothing is read (uuid).
#   insertBatchBlock  null on success, on a partial write (a malformed node
#       is skipped while its siblings land) and on failure, and it withholds
#       the new uuids. The place it lands is read before and after (batch);
#       the read also yields the uuids.
#   moveBlock  null on success, on a missing target and on a refusal (a move
#       into the block's own subtree does nothing). The block is read back
#       where it was sent (move).
#   updateBlock  null on success, for the same text and for a uuid no block
#       has. The block is read back (text): the text through
#       block_text_matches, since Logseq trims it and, with time tracking
#       on, appends or rewrites a :LOGBOOK: drawer (editor.cljs
#       with-marker-time, read in the code, not measured: time tracking was
#       off); the properties
#       sent along through stored_properties. Their key:: lines are taken
#       out of the text by key, not by place: Logseq writes them below the
#       text, where in a text of several lines was not measured.
#   upsertBlockProperty, removeBlockProperty  null either way, an unknown
#       uuid too. The block's stored properties are read (property,
#       no_property): stored_properties, never getBlock's map, which
#       camel-cases the keys. The block is read first: for a uuid no block
#       has the reader finds no key, which would pass as "removed".
#   removeBlock  null on success and for a uuid no block has. getBlock
#       then finds none (no_block). A uuid that never had a block passes
#       too: the commands read the block first, and strictinsert's stand-in
#       was written a moment before.
#   deletePage  null, for a missing page too. getPage then finds none
#       (no_page), or a page without blocks that others name as their
#       namespace, which Logseq keeps (measured). A page a block of another
#       page links to is retracted whole (page.cljs delete!; measured), and
#       so is a page named in another's alias:: (measured).
#   renamePage  null. The new name then finds the page's uuid, spelt
#       as sent (renamed): the uuid alone would pass a change of case that
#       did nothing, since getPage finds a page by its name in lower case.
#   createPage  answers the page, and null for a journal title in
#       another format, which it creates under the graph's name:
#       create_page sends the name as asked, which Logseq cleans once into
#       the name page_name_to_create predicts, and the journal's under the
#       graph's format. A page that exists comes back as
#       it is, the properties sent dropped, so it is refused before the
#       write (PageExists). The answer's uuid must be the page getPage finds
#       under the name predicted (page): no names are compared here, since Logseq
#       normalises them further than lower() does (NFC, pagenames.py). The
#       properties in the answer are not read; the proof needs none.
#   setBlocksId  null; it skips a page's property block, which its
#       caller leaves out. Each block asked for then shows id among its
#       properties (ids).
#
# A read proves Logseq's database, not the file, which follows 1.8 s later;
# it reaches Logseq, not the cache, since every write clears the cache.
# A write elsewhere between a write and its read shows as a failed proof,
# with what was expected and what was read. Should a Logseq version answer
# one of these writes with what it wrote, or with an error when it wrote
# nothing, prove it by the answer as insertBlock is, and drop the read;
# keep it where the answer cannot show a count or a place.
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
    "logseq.Editor.createPage": Write(editor="never", proof="page"),
    "logseq.Editor.deletePage": Write(editor="page", proof="no_page"),
    "logseq.Editor.renamePage": Write(editor="page_or_link", proof="renamed"),
    "logseq.Editor.appendBlockInPage": Write(editor="never", proof="uuid"),
    "logseq.Editor.insertBlock": Write(editor="never", proof="uuid"),
    "logseq.Editor.updateBlock": Write(editor="target", proof="text"),
    "logseq.Editor.removeBlock": Write(editor="subtree", proof="no_block"),
    "logseq.Editor.upsertBlockProperty": Write(editor="target", proof="property"),
    "logseq.Editor.removeBlockProperty": Write(editor="target", proof="no_property"),
    "logseq.Editor.insertBatchBlock": Write(editor="any", proof="batch"),
    "logseq.Editor.moveBlock": Write(editor="subtree", proof="move"),
    "logseq.Editor.setBlocksId": Write(editor="requested", proof="ids"),
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
    (api.cljs insert_block, editor.cljs api-insert-new-block!, 0.10.15); on
    the visible page the cursor of
    whoever is typing jumps into it and the rest of their typing lands there
    (measured, 0.10.15). No caller wants that, so a caller's own focus is
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

# checkEditing's answer when a block is open: its uuid (measured).
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


def _property_text(value) -> str:
    """A property value as Logseq writes it into the text (measured): a list as
    ``a,b``, a number as its digits, a string as it is (``01234`` and
    ``[[Link]]`` stay text)."""
    if isinstance(value, (list, tuple)):
        return ",".join(map(str, value))
    return str(value)


def _target_text(target) -> str:
    """A write's target as a message names it: a block by the start of its
    uuid, a page by its name in quotes."""
    if isinstance(target, str) and _UUID_TEXT.fullmatch(target):
        return f"block {target[:8]}..."
    return f"'{target}'"


def _block_count_text(n: int) -> str:
    """``n`` as "1 block" or "n blocks", for what a proof expected."""
    return f"{n} block" if n == 1 else f"{n} blocks"


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
# the open block before it inserts (measured).
DISCARDS_TYPING = "writing now would discard what is being typed there"
MOVES_CURSOR = "a batch insert would move the cursor out of the block being edited"

# How often LogseqAPI asks after a batch whether Logseq opened a block.
_BATCH_EDITOR_POLL_S = 0.01


def _editor_unknown_after_batch(answer: str) -> EditorStateUnknown:
    """checkEditing after a batch that was sent: the batch is written, and
    a block it opened may be open still."""
    return EditorStateUnknown(
        f"The batch was written, but checkEditing after it gave no usable "
        f"answer ({answer!r}); a block the batch opened may still be open in "
        "Logseq's editor.",
        answer=answer,
    )


class LogseqAPI:
    # After a batch, how long to watch for the block Logseq opens in its
    # editor: it opened 16–34 ms after the answer (measured, three runs), so
    # 100 ms, counted as ten naps of 10 ms; the checkEditing requests between
    # them come on top, eleven of 1.2 ms each (measured), about 115 ms in all.
    # A page not on screen opens nothing, and the window runs full for each
    # multi-block write. Attributes, so that tests need not
    # wait:
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
        # refusal reports: one instance per call (group.py), so the
        # count is the call's, and no command has to keep its own.
        self.writes_landed = 0
        # The write sent and not yet proven, by its short name: a connection
        # that fails before the proof holds leaves it unknown, and the error
        # handler names it beside writes_landed. None between writes, unless
        # one was left in doubt and the call went on writing (_write).
        self.write_unproven = None
        # Why the call's first write is refused while any block is open
        # (refuse_open_before_first_write); None once it is sent, or when
        # the call asked for nothing of the kind.
        self._first_write_refuses_open = None

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

    def call(self, method: str, args: list = None, *, cached: bool = True):
        """Send ``method``; a cacheable read may come from the cache unless
        ``cached`` is false, as for the editor gate's reads."""
        args = args or []
        # None for an unregistered method, which _post refuses before sending.
        kind = _METHODS.get(method)
        cacheable = cached and self.cache_enabled and isinstance(kind, Read) and kind.cache
        if cacheable:
            key = self._cache_key(method, args)
            hit = self._cache_get(key)
            if hit is not None:
                return hit
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
            # {"error": ...} (measured for updateBlock, removeBlock, the property
            # writes and an unknown method), which went on as the result and
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
               editing=_UNSET, count: int = 1, proof_args=None):
        """Send one write the way its ``_METHODS`` entry says, and count it.

        Every public write goes through here, so the steps below hold for all
        of them and no command can go around one:

        1. ask checkEditing once, if the entry can refuse, ``texts`` hold
           Block Refs, or this is the call's first write and the call asked
           for it (``editing``, when given, is that answer already, so
           set_blocks_id does not ask again); refuse the first write here
           if a block is open and the call asked for that
           (:meth:`refuse_open_before_first_write`);
        2. find the Block Ref targets in ``texts`` that need an id, ``own``
           excepted (reads only);
        3. refuse if the open block is one the entry's ``editor`` rule
           protects;
        4. store the targets' ids with the answer from step 1, which refuses
           if one of them is the open block (setBlocksId writes into it);
        5. the write;
        6. prove it, through ``_prove_<proof>``, which answers the result the
           method returns; ``proof_args`` are what it compares with beyond
           the target (a batch: what it sent, and its place as read before).
           From the write until the proof holds or refuses, the write is
           ``write_unproven``, unless an earlier write of the call still is;
        7. ``count`` more writes landed (a batch counts its blocks; the
           removal of a block written earlier in the call takes one back):
           after the proof, so a write that fails it does not count. A batch that
           landed in part counts those blocks in its proof before it raises,
           and so does setBlocksId.

        Asked before the ids are stored, not after: setBlocksId would
        otherwise write an id:: into a block for a write the gate then
        refuses. The gate reads what it compares when it asks, past the
        cache. What stays open is the time from those reads to the write: the
        ref targets are read, their ids may be stored, and the write is sent;
        a block someone enters or makes in those milliseconds is not seen,
        a gap that is known and accepted.

        A proof name with no ``_prove_`` method behind it fails at the lookup
        instead of passing as a check nobody made (and
        test_api_endpoint_binding names it).
        """
        kind = _METHODS[method]
        can_refuse = kind.editor != "never"
        refs = [u for t in texts for u in block_ref_uuids(t)]
        first_refuses, self._first_write_refuses_open = self._first_write_refuses_open, None
        if editing is _UNSET:
            editing = self.check_editing() if can_refuse or refs or first_refuses else None
        if editing is not None and first_refuses:
            self.refuse_open(editing, why=first_refuses)
        wanted = self._ref_targets_needing_id(refs, own=own)
        if editing is not None and can_refuse:
            getattr(self, f"_gate_{kind.editor}")(editing, target)
        # The ref targets are gated by setBlocksId's own rule, with the same
        # answer: it is the write into them, and the first write of all.
        if wanted:
            self.set_blocks_id(wanted, editing=editing)
        # A write of this call already in doubt stays so, and is the one
        # named: a failed batch left in doubt was followed by the removal of
        # its stand-in (strictinsert), which landed and cleared it, and a
        # retry wrote the batch twice.
        earlier = self.write_unproven
        self.write_unproven = earlier or method.rsplit(".", 1)[-1]
        try:
            result = self.call(method, args)
            result = getattr(self, f"_prove_{kind.proof}")(method, target, result,
                                                            **(proof_args or {}))
        except WriteRefused:
            # Refused or not shown: the refusal says so, this write is not in doubt.
            self.write_unproven = earlier
            raise
        self.write_unproven = earlier
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

    def _prove_batch(self, method, target, result, *, texts, place, before):
        """The new blocks' uuids, DFS pre-order: the blocks at ``place`` that
        were not in ``before``, as many as ``texts`` and with those texts.

        A batch is not atomic: the blocks that landed are counted before
        anything is raised, since they stay. The editor the batch opened is
        closed whether the proof holds or not, so that a failed one leaves
        no block open either; an editor that cannot be asked is added
        to the proof's error, or raised with the blocks counted."""
        old = {b.get("uuid") for b in before}
        new = [b for b in self._blocks_in(place) if b.get("uuid") not in old]
        failed, landed = None, len(new)
        if len(new) != len(texts):
            failed = _not_verified(method, target, _block_count_text(len(texts)), str(len(new)))
            landed = min(len(new), len(texts))
        else:
            for sent, block in zip(texts, new):
                read = block.get("content") or ""
                if not block_text_matches(sent, read):
                    failed = _not_verified(method, target, repr(sent), repr(read))
                    break
        try:
            self._close_editor_the_batch_opened({b.get("uuid") for b in new})
        except EditorStateUnknown as unknown:
            self.writes_landed += landed
            if failed is None:
                raise
            failed.args = (f"{failed} {unknown}",)
            raise failed from unknown
        if failed is not None:
            self.writes_landed += landed
            raise failed
        return [b["uuid"] for b in new]

    def _prove_move(self, method, target, result, *, to, before):
        """``target`` sits right in front of ``to`` (``before``), or among its
        children. Among, not first: moveBlock's own placement is not held to
        more than it was (that finding is left open)."""
        landed = self.get_block(to, include_children=True) or {}
        if before:
            # Directly in front: "same parent" would pass a move that did
            # nothing, since source and target often share one already.
            siblings = self._sibling_uuids_in_order(landed)
            try:
                ok = siblings.index(target) + 1 == siblings.index(to)
            except ValueError:
                ok = False
            where = f"it directly before block {to[:8]}..."
        else:
            ok = any(isinstance(c, dict) and c.get("uuid") == target
                     for c in landed.get("children") or [])
            where = f"it under block {to[:8]}..."
        if not ok:
            raise _not_verified(method, target, where, "it somewhere else")
        return result

    def _prove_text(self, method, target, result, *, content, properties):
        """The block reads back as ``content``, with ``properties`` stored.

        The lines of the keys sent along go by key, on both sides: Logseq
        writes them below the text and drops a line of the text with the
        same key (#66). Only those keys: a rule "no property lines" would
        pass a write that lost the caller's own. Their values are compared
        as ``_prove_property`` does."""
        block = self._block_to_prove(method, target, repr(content))
        keys = {stored_property_key(k) for k in properties}
        # Logseq drops a ref to the block from its own text, in the lower-case
        # form it writes (editor.cljs wrap-parse-block; measured, 0.10.15:
        # "see ((own)) here" reads back "see  here"). A ref that could only
        # point at itself.
        sent = content.replace(f"(({str(target).lower()}))", "")

        def without_sent_keys(text):
            return "\n".join(line for line in text.split("\n")
                             if not ((m := PROPERTY_LINE_RE.match(line))
                                     and stored_property_key(m.group(1)) in keys))

        read = block.get("content") or ""
        if not block_text_matches(without_sent_keys(sent), without_sent_keys(read)):
            raise _not_verified(method, target, repr(content), repr(read))
        if properties:
            texts = stored_properties(self, target)[1]
            for key, value in properties.items():
                self._prove_value(method, target, texts, key, value)
        return result

    def _prove_property(self, method, target, result, *, key, value):
        """The block holds ``key`` with ``value`` in its text form."""
        self._block_to_prove(method, target, f"{key}:: {_property_text(value).strip()}")
        self._prove_value(method, target, stored_properties(self, target)[1], key, value)
        return result

    def _prove_no_property(self, method, target, result, *, key):
        """The block holds no ``key``, neither as a value nor as a text, as
        Logseq's parser stores it or as sent: remove-property sends a key the
        parser would drop as given, and the database holds such a key as given
        when an earlier write stored it (upsertBlockProperty keeps any key)."""
        self._block_to_prove(method, target, f"no {key}::")
        values, texts = stored_properties(self, target)
        for spelt in dict.fromkeys((stored_property_key(key), key)):
            if spelt in values or spelt in texts:
                got = texts.get(spelt, values.get(spelt))
                raise _not_verified(method, target, f"no {key}::", f"{key}:: {got}")
        return result

    def _prove_no_block(self, method, target, result):
        """No block under ``target`` any more; its children went with it
        (measured)."""
        if self.get_block(target, include_children=False):
            raise _not_verified(method, target, "no block", "the block still there")
        return result

    def _prove_no_page(self, method, target, result):
        """No page under the name ``target`` any more (measured), or one Logseq
        keeps because other pages name it as their namespace: delete! then
        removes its blocks and file and leaves the page (page.cljs
        ``delete!``; measured, 0.10.15: getPage answers it,
        getPageBlocksTree ``[]``).
        Without blocks alone is not enough: a page that had none and was not
        deleted would pass."""
        page = self.get_page(target)
        if page and (self.get_page_blocks_tree(target) or not self._is_namespace(page)):
            raise _not_verified(method, target, "no page", "the page still there")
        return result

    def _is_namespace(self, page) -> bool:
        """Whether another page has ``page`` as its namespace (X for X/Y)."""
        uuid = page.get("uuid") if isinstance(page, dict) else None
        if not uuid:
            return False
        return bool(self.datascript_query(
            f'[:find ?c :where [?p :block/uuid #uuid "{uuid}"] [?c :block/namespace ?p]]'))

    def _prove_renamed(self, method, target, result, *, uuid, name):
        """``name`` finds the page ``uuid``, and ``originalName`` is ``name``."""
        page = self.get_page(name)
        if not isinstance(page, dict):
            got = "no page"
        elif page.get("uuid") != uuid:
            got = "another page"
        elif page.get("originalName") != name:
            got = f"it named {page.get('originalName')!r}"
        else:
            return result
        raise _not_verified(method, target, f"it named {name!r}", got)

    def _prove_page(self, method, target, result):
        """The answer names a page, and getPage under ``target``, the name
        create_page predicts, finds the same one; how the name resolves is
        left to Logseq."""
        uuid = result.get("uuid") if isinstance(result, dict) else None
        if not uuid:
            raise _not_verified(method, target, "the new page", "no page uuid in the answer")
        page = self.get_page(target)
        found = page.get("uuid") if isinstance(page, dict) else None
        if found != uuid:
            raise _not_verified(method, target, f"page {uuid}",
                                f"page {found}" if found else "no page")
        return result

    def _prove_ids(self, method, target, result):
        """Each block of ``target`` holds ``id`` among its properties. Those
        that do stay so, and count before the refusal."""
        missing = [u for u in target
                   if not ((self.get_block(u, include_children=False) or {})
                           .get("properties") or {}).get("id")]
        if missing:
            self.writes_landed += len(target) - len(missing)
            raise _not_verified(method, missing[0], "id:: among its properties", "none")
        return result

    def _block_to_prove(self, method, target, expected) -> dict:
        """The block ``target``, read from Logseq; WriteNotVerified if there is
        none. Read right away, without waiting: with no block open, getBlock
        shows a write at once (measured). The editor gate lets no write through to
        an open block, where getBlock would show the old text (measured)."""
        block = self.get_block(target, include_children=False)
        if not block:
            raise _not_verified(method, target, expected, "no block")
        return block

    @staticmethod
    def _prove_value(method, target, texts, key, value):
        """``texts`` (stored_properties) hold ``key`` as ``value`` does in
        text form. Both stripped: Logseq's parser trims a value
        (blockprops.py), as _page_shows compares too."""
        want = _property_text(value).strip()
        got = texts.get(stored_property_key(key))
        if got is None or str(got).strip() != want:
            raise _not_verified(method, target, f"{key}:: {want}",
                                "no such property" if got is None else f"{key}:: {got}")

    # Where a batch lands and what a move is checked against: a block's
    # parent, given as ("block", its id or uuid) or ("page", its name).
    def _place_of_parent(self, block):
        """The parent of ``block`` as a place, or ``None``.

        getBlock reports a parent as ``{"id": <int>}`` and accepts that id,
        so a nested block's siblings are one read away. Not at the top level:
        there the parent is the page, getBlock answers ``null`` for a page's
        id, and getPageBlocksTree refuses a number ("Expected string, got:
        number") and needs the page's name (measured, 0.10.15; #23).
        """
        parent_id = (block.get("parent") or {}).get("id")
        if parent_id is None:
            return None
        if parent_id == (block.get("page") or {}).get("id"):
            page = self.get_page(parent_id) or {}
            return ("page", page["name"]) if page.get("name") else None
        return ("block", parent_id)

    def _children_in(self, place) -> list:
        """The blocks directly at ``place``, each with its children."""
        if place is None:
            return []
        kind, key = place
        if kind == "page":
            children = self.get_page_blocks_tree(key)
        else:
            children = (self.get_block(key, include_children=True) or {}).get("children")
        return children if isinstance(children, list) else []

    def _blocks_in(self, place) -> list:
        """Every block at ``place`` and below it, DFS pre-order."""
        return preorder_blocks(self._children_in(place))

    def _sibling_uuids_in_order(self, block: dict) -> list:
        """UUIDs of ``block`` and its siblings, in order."""
        return [c.get("uuid") for c in self._children_in(self._place_of_parent(block))
                if isinstance(c, dict) and c.get("uuid")]

    def _batch_place(self, anchor: str, options: dict):
        """Where a batch at ``anchor`` lands: under the anchor, or with
        ``sibling`` beside it under its parent. A page's uuid as the anchor
        puts it at the head of the page. ``None`` for an anchor Logseq does
        not know, where nothing lands."""
        if options.get("sibling"):
            block = self.get_block(anchor, include_children=False)
            return self._place_of_parent(block) if block else None
        if self.get_block(anchor, include_children=True):
            return ("block", anchor)
        page = self.get_page(anchor)
        return ("page", page["name"]) if isinstance(page, dict) and page.get("name") else None

    def check_editing(self) -> str | None:
        """The uuid of the block open in Logseq's editor, lower case, or
        ``None`` when none is.

        Logseq answers with raw text, not JSON (measured): the uuid, or ``false``;
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
        """Close Logseq's editor, saving the open block (measured). Answers null."""
        return self.call("logseq.Editor.exitEditingMode")

    # The editor rules of _METHODS, one per ``Write.editor`` name. Each gets
    # the open block's uuid (lower case) and the write's target, and refuses
    # through refuse_open. Only called when a block is open, so the reads
    # that place it cost nothing while nobody types. Those reads go past the
    # cache: a subtree read earlier in the call lacks a child made since, and
    # the block typed in there went with its parent.
    def _gate_never(self, editing, target):
        """Inserts; _write does not call it (``never`` asks nothing)."""

    def _gate_target(self, editing, uuid):
        # Lower case on both sides: update-block passes --id through as
        # typed, checkEditing answers in lower case, and compared as typed
        # the write went past the gate (measured).
        if editing == uuid.lower():
            self.refuse_open(editing)

    def _gate_subtree(self, editing, uuid):
        if editing == uuid.lower():
            self.refuse_open(editing)
        block = self.get_block(uuid, include_children=True, cached=False)
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
        # (measured): no parsing of the text here.
        open_block = self.get_block(editing, include_children=False, cached=False)
        page = self.get_page(page_name, cached=False)
        if not (open_block and isinstance(page, dict) and page.get("id") is not None):
            return
        ids = {(open_block.get("page") or {}).get("id")}
        if links:
            ids |= {r.get("id") for r in open_block.get("refs") or [] if isinstance(r, dict)}
        if page["id"] in ids:
            self.refuse_open(editing, open_block)

    def refuse_open_before_first_write(self, why: str) -> None:
        """Refuse this call's first write, whatever it is, while any block
        is open in the editor, giving ``why``.

        For a call whose later write would be refused anyway, as a batch is
        (``_gate_any``): asked at the first one instead, a page or heading
        written first is not left behind by the refusal. Asked in _write,
        so no command can go around it by writing something else first.
        Only the first: the writes after it keep their own rules.
        """
        self._first_write_refuses_open = why

    def refuse_open(self, editing, open_block=None, *, why=DISCARDS_TYPING):
        """Raise EditorOpen for the open block, naming its page."""
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

    def get_page(self, page_name: str, *, cached: bool = True):
        return self.call("logseq.Editor.getPage", [page_name], cached=cached)

    def get_block(self, block_id: str, include_children: bool = True, *, cached: bool = True):
        """The block, or ``None`` when Logseq has none under ``block_id``.

        An unknown uuid comes back as ``null``, a malformed one as HTTP 200 with
        ``{"error": "... is not a valid UUID string."}`` (measured, 0.10.15).
        Both mean "no such block"; passing the error object on made it look
        like one to every ``if not block`` guard.
        """
        result = self.call(
            "logseq.Editor.getBlock", [block_id, {"includeChildren": include_children}],
            cached=cached,
        )
        if isinstance(result, dict) and "error" in result and "uuid" not in result:
            return None
        return result

    def create_page(self, page_name: str, properties: dict = None, *, first_block: bool = True):
        """Create a page. A name in the graph's date format is a journal.

        Logseq tells a journal by its name alone (measured, 0.10.15); a
        ``journal?`` property is not needed and lands as a line
        ``journal?:: true`` at the top of the file. ``first_block=False`` is
        for a caller that writes right after: otherwise the page starts with
        an empty block. A page created without a first block and without
        text gets no file (measured), so a caller that writes nothing keeps it.

        ``page_name`` is the name as the caller was given it, never one that
        ``page_name_to_create`` returned. The page is created under that
        function's name for it: ``[[X]]`` creates X, and a journal title in
        another format the journal under the graph's name; the check below
        and the proof read that name. A caller that writes to the page
        afterwards takes the name from the same function
        (``pagenames.page_to_write``).

        Raises PageExists for a page that exists, before anything is sent,
        and WriteNotVerified unless the new page shows (``_prove_page``).
        """
        created = page_name_to_create(self, page_name)
        # Sent as asked: create! cleans what it is sent in one pass, and the
        # cleaning is not idempotent, so the name it made once would be
        # cleaned again ("#[[X]]" makes "[[X]]", "[[X]]" makes "X"; measured).
        # A journal title in another format goes as the graph's title, which
        # create! keeps: sent as asked, createPage answers null (measured).
        sent = page_name if title_as_created(page_name) == created else created
        # Logseq answers createPage on a page that exists with that page, the
        # properties sent dropped (measured). The commands ask first and refuse with
        # their own advice; this holds for a caller that did not.
        if self.get_page(created):
            raise PageExists(
                f"Page '{created}' already exists; createPage would leave it as it "
                "is and drop the properties sent.",
                page=created,
            )
        # Without redirect: false, Logseq turns its view to the new page
        # (measured, 0.10.15) -- every page and journal the CLI created moved
        # the view.
        options = {"redirect": False}
        if not first_block:
            options["createFirstBlock"] = False
        return self._write("logseq.Editor.createPage",
                           [sent, properties or {}, options], target=created)

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
        """Insert a whole tree in ONE call; answers the new blocks' uuids in
        DFS pre-order.

        ``insertBatchBlock`` writes an arbitrarily deep ``[{content, children}]``
        tree against a single anchor, where :meth:`insert_block` needs one call
        per node. It answers ``null`` whether it wrote all, part or nothing, so
        the place it lands is read before and after (``_prove_batch``), which
        raises WriteNotVerified unless every block arrived with its text.

        Positioning also differs from :meth:`insert_block`: with
        ``sibling: false`` the batch lands at the HEAD of the child list and
        ``before: false`` does not change that, so appending means anchoring on
        the last existing child with ``sibling: true``.
        """
        refuse_split_tree(batch, command="logseq-cli", single_label="The text")
        options = options or {}
        # With keepUUID the id:: lines are the point of the call, vetted by
        # check_block_ids. Without it Logseq drops them (measured), so none
        # should arrive: one that does was decided on by no command.
        if not options.get("keepUUID"):
            refuse_id_lines_tree(batch)
        texts = list(tree_texts(batch))
        # Read before the editor question, so that only the reads _write
        # names lie between the question and the write.
        place = self._batch_place(block_uuid, options)
        return self._write(
            "logseq.Editor.insertBatchBlock", [block_uuid, batch, options],
            target=block_uuid, texts=texts, count=len(texts),
            proof_args={"texts": texts, "place": place, "before": self._blocks_in(place)},
        )

    def _close_editor_the_batch_opened(self, new_uuids):
        """Leave the block the batch just opened in Logseq's editor, if it did.

        Left open, it would refuse the agent's next write to it with
        open_in_editor. Here rather than in strictinsert, so it holds for
        every batch, --keep-ids included. Logseq opens it asynchronously
        (measured: 16–34 ms after the answer), so it is asked again every
        10 ms within ``batch_editor_wait_s``.

        Only a block of the batch (``new_uuids``) is closed. One someone else
        entered in that window stays open: Logseq does not save a block left
        while its last editor op is the batch's :paste-blocks
        (lifecycle.cljs ``will-unmount``, editor.cljs ``paste-blocks``,
        0.10.15; read in the code, not measured), so closing it would lose
        what is typed there.

        An answer that cannot be read, or no answer, raises
        EditorStateUnknown saying the batch was written: the caller counts
        the blocks first.
        """
        new_uuids = {u.lower() for u in new_uuids if u}
        polls = round(self.batch_editor_wait_s / _BATCH_EDITOR_POLL_S)
        for poll in range(polls + 1):
            try:
                editing = self.check_editing()
            except EditorStateUnknown as unknown:
                raise _editor_unknown_after_batch(unknown.fields["answer"]) from unknown
            except requests.RequestException as failed:
                raise _editor_unknown_after_batch(type(failed).__name__) from failed
            if editing in new_uuids:
                self.exit_editing_mode()
                return
            if poll < polls:
                self._sleep(_BATCH_EDITOR_POLL_S)

    def move_block(self, src_uuid: str, target_uuid: str, options: dict = None):
        """Move a block (with its children) next to / under ``target_uuid``.

        Structural move, unlike copy+remove: the block keeps its UUID, so
        ``((block-ref))`` backlinks survive. Logseq answers null whether it
        moved or not; ``_prove_move`` reads the block back where it was sent
        (``before``: right in front of ``target_uuid``; else among its
        children) and raises WriteNotVerified otherwise.

        Both uuids go in lower case, as Logseq's are: the proof compares them
        with what Logseq reads back, and one typed in capitals failed a move
        that landed.
        """
        src_uuid, target_uuid = src_uuid.lower(), target_uuid.lower()
        options = options or {}
        return self._write(
            "logseq.Editor.moveBlock", [src_uuid, target_uuid, options],
            target=src_uuid,
            proof_args={"to": target_uuid, "before": bool(options.get("before"))},
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

        Raises WriteNotVerified unless the block then reads back with
        ``content`` and ``properties`` (``_prove_text``).
        """
        refuse_split_block(content, command="logseq-cli", where="The text", replacing=replacing)
        refuse_id_lines(content, own=block_uuid, replacing=replacing)
        # Properties carried along are written again, a ref among them too.
        texts = [content, *map(str, (properties or {}).values())]
        args = [block_uuid, content]
        if properties:
            args.append({"properties": properties})
        return self._write("logseq.Editor.updateBlock", args,
                           target=block_uuid, texts=texts, own=block_uuid,
                           proof_args={"content": content, "properties": properties or {}})

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
        So is a page's property block: setBlocksId skips it (measured), and its
        proof would fail a write whose ref is written all the same.

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
            if block and block.get("page") and not (block.get("properties") or {}).get("id") \
                    and not block.get("preBlock?"):
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
        twice. Raises WriteNotVerified unless each block then holds its id
        (``_prove_ids``); counts one write per block."""
        return self._write("logseq.Editor.setBlocksId", [block_uuids], target=block_uuids,
                           editing=editing, count=len(block_uuids))

    def remove_block(self, block_uuid: str, *, written_here: bool = False):
        """Remove a block with its children; raises WriteNotVerified unless
        getBlock then finds none (``_prove_no_block``).

        ``written_here``: the block is one this call wrote a moment before
        and counted (strictinsert's stand-in). Once it is gone, neither write
        remains, and a refusal that counted them would tell a retry of writes
        that are gone: the removal takes the insert's count back instead of
        adding its own. A removal that fails raises before, and the block,
        which stays, counts."""
        return self._write("logseq.Editor.removeBlock", [block_uuid], target=block_uuid,
                           count=-1 if written_here else 1)

    def get_page_linked_references(self, page_name: str):
        """Get backlinks using native Logseq API (faster than brute-force search)."""
        return self.call("logseq.Editor.getPageLinkedReferences", [page_name])

    def upsert_block_property(self, block_uuid: str, key: str, value):
        """Set or update a property on a block; raises WriteNotVerified
        unless the block then holds it (``_prove_property``)."""
        refuse_split_property(key, value)
        return self._write("logseq.Editor.upsertBlockProperty", [block_uuid, key, value],
                           target=block_uuid, texts=[str(value)], own=block_uuid,
                           proof_args={"key": key, "value": value})

    def remove_block_property(self, block_uuid: str, key: str):
        """Remove a property from a block; raises WriteNotVerified unless the
        block then lacks it (``_prove_no_property``)."""
        return self._write("logseq.Editor.removeBlockProperty", [block_uuid, key],
                           target=block_uuid, proof_args={"key": key})

    def rename_refusal(self, old_name: str, new_name: str) -> str | None:
        """Why renaming ``old_name`` to ``new_name`` is refused, or ``None``.

        ``"empty"``: nothing is left of the name once trimmed as Logseq trims
        it (``js_trim``); Logseq does
        nothing and answers null, as for a rename (measured). ``"exists"``: getPage
        finds another page under the name, and Logseq would merge the two into
        it, the old page gone and its blocks under the other (measured). The same
        page, by uuid, is a change of case only, a rename that works.

        Not named for an endpoint: it wraps none. Shared by rename_page and
        rename-page --dry-run, so the preview refuses what the run refuses.
        """
        name = js_trim(new_name)
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
        """Rename a page; the new name is sent trimmed as Logseq trims it
        (``js_trim``), so the check and the proof read the name it gets.

        Raises RenameRefused before anything is sent, see rename_refusal, and
        WriteNotVerified unless the new name then finds the page, spelt as
        sent (``_prove_renamed``)."""
        why = self.rename_refusal(old_name, new_name)
        if why:
            raise rename_refused(old_name, new_name, why)
        page = self.get_page(old_name)
        name = js_trim(new_name)
        return self._write("logseq.Editor.renamePage", [old_name, name], target=old_name,
                           proof_args={"uuid": page.get("uuid") if isinstance(page, dict) else None,
                                       "name": name})

    def delete_page(self, page_name: str):
        """Delete a page; raises WriteNotVerified unless getPage then finds
        none (``_prove_no_page``)."""
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
