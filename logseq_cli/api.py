import json
import os
import time
import requests


# Methods that are pure reads and safe to cache.
_CACHEABLE_METHODS = frozenset({
    "logseq.Editor.getPage",
    "logseq.Editor.getBlock",
    "logseq.Editor.getPageBlocksTree",
    "logseq.Editor.getPageProperties",
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
    "logseq.Editor.setBlockProperty",
    "logseq.Editor.replaceText",
})


class LogseqAPI:
    def __init__(self, host=None, port=None, token=None):
        self.host = host or os.getenv("LOGSEQ_HOST", "127.0.0.1")
        self.port = port or os.getenv("LOGSEQ_PORT", "12315")
        self.token = token or os.getenv("LOGSEQ_TOKEN", "")
        self.base_url = os.getenv(
            "LOGSEQ_API_URL", f"http://{self.host}:{self.port}/api"
        )
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
        return self.call(
            "logseq.Editor.getBlock", [block_id, {"includeChildren": include_children}]
        )

    def create_page(self, page_name: str, properties: dict = None, options: dict = None):
        args = [page_name]
        if properties:
            args.append(properties)
        if options:
            if len(args) == 1:
                args.append({})
            args.append(options)
        return self.call("logseq.Editor.createPage", args)

    def append_block_in_page(self, page_name: str, content: str):
        return self.call("logseq.Editor.appendBlockInPage", [page_name, content])

    def insert_block(self, block_uuid: str, content: str, options: dict = None):
        return self.call(
            "logseq.Editor.insertBlock", [block_uuid, content, options or {}]
        )

    def update_block(self, block_uuid: str, content: str):
        return self.call("logseq.Editor.updateBlock", [block_uuid, content])

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
        return self.call("logseq.DB.datascriptQuery", [query])
