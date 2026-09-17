"""Each API wrapper must reach the endpoint its own name promises.

The wrappers in :class:`LogseqAPI` are one-liners around ``call()``, so what
they assert is not a return value but *which* endpoint goes out with which
arguments. Nothing held them to that: a mutation pointing ``delete_page`` at
``logseq.Editor.renamePage`` left the whole suite green, and a destructive
command silently calling a different endpoint is the worst version of this
defect.

The existing set-vs-reality test in ``test_api_cache.py`` cannot catch it. It
asserts a method name appears *somewhere* in ``api.py``; after swapping two
endpoints between wrappers both names are still there.

These tests therefore derive the expectation instead of restating it. A table
mapping wrapper to endpoint would be copied out of ``api.py`` and checked
against ``api.py`` — it would pin down whatever is written there, including a
mistake. The wrapper's own name is the independent source: ``snake_case``
turned to ``camelCase`` is the endpoint's leaf, without exception across all
18 wrappers.
"""

import ast
import pathlib

import pytest

from logseq_cli.api import _CACHEABLE_METHODS, _MUTATING_METHODS


def _camel(snake: str) -> str:
    head, *tail = snake.split("_")
    return head + "".join(word.capitalize() for word in tail)


def _wrapper_endpoints():
    """{wrapper name: endpoint} read off the AST, not a list kept by hand."""
    source = pathlib.Path(
        pathlib.Path(__file__).resolve().parent.parent / "logseq_cli" / "api.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "LogseqAPI"
    )
    found = {}
    for node in cls.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        endpoints = [
            sub.value for sub in ast.walk(node)
            if isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and sub.value.startswith("logseq.")
        ]
        if len(endpoints) == 1:
            found[node.name] = endpoints[0]
        elif endpoints:
            raise AssertionError(
                f"{node.name} names more than one endpoint: {endpoints}. "
                "The name-derived check below cannot decide which one is meant."
            )
    return found


WRAPPERS = _wrapper_endpoints()


def test_every_wrapper_was_found():
    """Guards the reader itself: an empty result would make every test pass."""
    assert len(WRAPPERS) >= 18, WRAPPERS


@pytest.mark.parametrize("wrapper", sorted(WRAPPERS))
def test_endpoint_leaf_matches_wrapper_name(wrapper):
    """``get_page_blocks_tree`` must call ``…getPageBlocksTree``, not another read."""
    endpoint = WRAPPERS[wrapper]
    leaf = endpoint.rsplit(".", 1)[1]
    assert leaf == _camel(wrapper), (
        f"{wrapper}() calls {endpoint}; its name promises "
        f"…{_camel(wrapper)}"
    )


@pytest.mark.parametrize("wrapper", sorted(WRAPPERS))
def test_endpoint_is_classified_for_the_cache(wrapper):
    """Every endpoint is either cacheable or mutating — never unclassified.

    An endpoint in neither set is read from the network every time *and* leaves
    a stale cache behind it, which is the failure mode that is hardest to see:
    both halves look like they work.
    """
    endpoint = WRAPPERS[wrapper]
    assert (endpoint in _CACHEABLE_METHODS) != (endpoint in _MUTATING_METHODS), (
        f"{endpoint} ({wrapper}) must be in exactly one of "
        "_CACHEABLE_METHODS / _MUTATING_METHODS"
    )


def test_every_mutating_method_has_a_wrapper():
    """The other direction: no entry without a call site.

    test_api_cache.py holds _CACHEABLE_METHODS to this and the mutating list
    had no counterpart, which is how two entries survived that were never
    sent -- setBlockProperty and replaceText, dating from the initial import.
    The find was not the entries but the missing check: an inventory nobody
    verifies describes what the tool once did, not what it does.
    """
    unused = sorted(_MUTATING_METHODS - set(WRAPPERS.values()))
    assert not unused, (
        f"listed as mutating but no wrapper sends them: {unused}"
    )


def test_every_cacheable_method_has_a_wrapper():
    """Same guard for the read list, bound to wrappers rather than to text.

    test_api_cache.py asserts the name appears somewhere in api.py; that stays
    true for an entry whose wrapper was deleted. This binds it to a call site.
    """
    unused = sorted(_CACHEABLE_METHODS - set(WRAPPERS.values()))
    assert not unused, (
        f"listed as cacheable but no wrapper sends them: {unused}"
    )


def test_cache_invalidation_covers_every_mutating_wrapper():
    """Every wrapper classified as mutating must clear the cache when called.

    ``call()`` clears the cache in an ``elif`` on ``_MUTATING_METHODS``. A write
    missing from that set would leave reads answering from a cache the write
    just invalidated — success reported, stale data served.
    """
    import json
    from unittest.mock import MagicMock, patch

    from logseq_cli.api import LogseqAPI

    mutating = {w: e for w, e in WRAPPERS.items() if e in _MUTATING_METHODS}
    assert mutating, "no mutating wrapper found — the reader is broken"

    for wrapper, endpoint in sorted(mutating.items()):
        api = LogseqAPI(host="localhost", port="12315", token="t")
        api._cache[("logseq.Editor.getPage", json.dumps(["Foo"]))] = (
            {"stale": True}, float("inf"),
        )
        resp = MagicMock(status_code=200)
        resp.json.return_value = {}
        resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=resp):
            api.call(endpoint, [])
        assert api._cache == {}, (
            f"{wrapper}() calls {endpoint} without clearing the cache"
        )
