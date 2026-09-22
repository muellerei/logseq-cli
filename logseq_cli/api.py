import json
import os
import time
import requests


# Methods that are pure reads and safe to cache.
# Deliberately absent: logseq.Editor.getPageProperties. It is declared in
# Logseq's plugin API (LSPlugin.ts), which is presumably where this list was
# first copied from, but the HTTP server does not expose it — it answers
# `MethodNotExist: get_page_properties` (checked against 0.10.15). It was in
# this set from the initial commit and never called even once, so the entry
# claimed a read the tool does not make. Page properties are read through
# get_page plus the first block instead; see the 0.6.0 changelog entry.
_CACHEABLE_METHODS = frozenset({
    "logseq.Editor.getPage",
    "logseq.Editor.getBlock",
    "logseq.Editor.getPageBlocksTree",
    "logseq.Editor.getPageLinkedReferences",
    "logseq.Editor.getAllPages",
    "logseq.App.getUserConfigs",
    "logseq.DB.datascriptQuery",
})

# Methods whose invocation must invalidate the entire cache.
_MUTATING_METHODS = frozenset({
    "logseq.Editor.createPage",
    "logseq.Editor.deletePage",
    "logseq.Editor.renamePage",
    "logseq.Editor.appendBlockInPage",
    "logseq.Editor.insertBlock",
    "logseq.Editor.updateBlock",
    "logseq.Editor.removeBlock",
    "logseq.Editor.upsertBlockProperty",
    "logseq.Editor.removeBlockProperty",
    "logseq.Editor.insertBatchBlock",
    "logseq.Editor.moveBlock",
})


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

    def call(self, method: str, args: list = None):
        args = args or []
        cacheable = self.cache_enabled and method in _CACHEABLE_METHODS
        if cacheable:
            key = self._cache_key(method, args)
            cached = self._cache_get(key)
            if cached is not None:
                return cached
        else:
            key = None

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
        try:
            data = resp.json()
        except requests.exceptions.JSONDecodeError:
            raise RuntimeError(f"Logseq API returned non-JSON response: {resp.text[:200]}")

        if cacheable and key is not None:
            # Error payloads must not outlive their cause: a cached
            # {"error": ...} would keep answering as a failure for up to TTL
            # seconds after the caller fixed the input.
            if not (isinstance(data, dict) and "error" in data):
                self._cache_set(key, data)
        elif method in _MUTATING_METHODS:
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

    def create_page(self, page_name: str, properties: dict = None, options: dict = None):
        args = [page_name]
        if properties:
            args.append(properties)
        if options:
            if len(args) == 1:
                args.append({})
            args.append(options)
        return self.call("logseq.Editor.createPage", args)

    def append_block_in_page(self, page_name: str, content: str, options: dict = None):
        # Options reach insertBlock unchanged (append_block_in_page in api.cljs),
        # so customUUID works here as it does there.
        args = [page_name, content]
        if options:
            args.append(options)
        return self.call("logseq.Editor.appendBlockInPage", args)

    def insert_block(self, block_uuid: str, content: str, options: dict = None):
        return self.call(
            "logseq.Editor.insertBlock", [block_uuid, content, options or {}]
        )

    def insert_batch_block(self, block_uuid: str, batch: list, options: dict = None):
        """Insert a whole tree in ONE call. Returns null even on success.

        ``insertBatchBlock`` writes an arbitrarily deep ``[{content, children}]``
        tree against a single anchor, where :meth:`insert_block` needs one call
        per node. It answers ``null`` both when it wrote and when it did not, so
        the return value carries no success signal at all: callers must verify by
        reading the anchor's children back (see
        :func:`helpers.insert_block_tree_batched`).

        Positioning also differs from :meth:`insert_block`: with
        ``sibling: false`` the batch lands at the HEAD of the child list and
        ``before: false`` does not change that, so appending means anchoring on
        the last existing child with ``sibling: true``.
        """
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

    def update_block(self, block_uuid: str, content: str, properties: dict = None):
        """Replace a block's content, optionally carrying its properties along.

        Block properties live *inside* the content (``prio:: 1`` as a line of
        the same block), so a plain content replacement drops every one of
        them. Passing them through the documented third parameter
        (``opts.properties``) makes Logseq re-emit them below the new text.

        Logseq writes each entry out as ``key:: value`` text, so what goes in
        decides what lands in the file. Pass the original text under the keys
        the database stores (``helpers.stored_properties``), not the block's
        own ``properties`` map: that one is camel-cased by the API and parsed,
        and writing it back turned ``due-date::`` into ``duedate::`` and
        ``zip:: 01234`` into ``zip:: 1234`` (measured, Logseq 0.10.15). ``id::`` is part of the map and is written
        back unchanged, so block references stay intact.
        """
        args = [block_uuid, content]
        if properties:
            args.append({"properties": properties})
        return self.call("logseq.Editor.updateBlock", args)

    def remove_block(self, block_uuid: str):
        return self.call("logseq.Editor.removeBlock", [block_uuid])

    def get_page_linked_references(self, page_name: str):
        """Get backlinks using native Logseq API (faster than brute-force search)."""
        return self.call("logseq.Editor.getPageLinkedReferences", [page_name])

    def upsert_block_property(self, block_uuid: str, key: str, value):
        """Set or update a property on a block."""
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
