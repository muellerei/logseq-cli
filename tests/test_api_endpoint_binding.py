"""Each API wrapper must reach the endpoint its own name promises.

The wrappers in :class:`LogseqAPI` are one-liners around ``call()``, so what
they assert is not a return value but *which* endpoint goes out with which
arguments. Nothing held them to that: a mutation pointing ``delete_page`` at
``logseq.Editor.renamePage`` left the whole suite green, and a destructive
command silently calling a different endpoint is the worst version of this
defect.

A check that a method name appears *somewhere* in ``api.py`` cannot catch
it: after swapping two endpoints between wrappers both names are still there.

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

from logseq_cli.api import (
    _CACHEABLE_METHODS,
    _METHODS,
    _MUTATING_METHODS,
    UI,
    LogseqAPI,
    Read,
    UnknownMethod,
    Write,
)

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "logseq_cli"


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
def test_endpoint_is_registered_with_one_kind(wrapper):
    """Every endpoint is a read, a write or a UI call — never unclassified.

    Before the registry this asked for "exactly one of _CACHEABLE_METHODS /
    _MUTATING_METHODS": an endpoint in neither was read from the network every
    time *and* left a stale cache behind it. ``call()`` now refuses such an
    endpoint outright, so the question here is only whether the entry is one
    of the three kinds ``call()`` knows how to treat.
    """
    endpoint = WRAPPERS[wrapper]
    assert type(_METHODS.get(endpoint)) in (Read, Write, UI), (
        f"{endpoint} ({wrapper}) must be in _METHODS as Read, Write or UI"
    )


def _method_constants():
    """{endpoint: [file:line, ...]} for every endpoint string in the package.

    Read off the AST, so a name in a comment does not count and one built in
    a helper module does. Only the plugin API's namespaces: a bare
    ``logseq.`` prefix would also match property names such as
    ``logseq.tldraw.page`` (commands/properties.py), which are no endpoints.
    """
    prefixes = ("logseq.Editor.", "logseq.App.", "logseq.DB.", "logseq.UI.")
    found = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.startswith(prefixes)):
                where = f"{path.relative_to(PACKAGE.parent)}:{node.lineno}"
                found.setdefault(node.value, []).append(where)
    return found


def test_every_method_constant_is_registered():
    """An endpoint named anywhere in the package is one ``call()`` accepts.

    ``call()`` refuses what the registry does not list, so a name missing
    from it is a command that fails the first time it runs. This finds it
    before then — including a call outside the wrappers, such as doctor's
    direct ``api.call("logseq.App.getUserConfigs")``.
    """
    constants = _method_constants()
    assert "logseq.Editor.getBlock" in constants, "the scan found nothing"
    missing = {m: where for m, where in constants.items() if m not in _METHODS}
    assert not missing, f"named but not in _METHODS: {missing}"


def test_call_refuses_unknown_method():
    """A method outside the registry never reaches Logseq.

    ``setBlockProperty`` sat in the mutating list from the initial import
    and was never sent; the point here is the other way round: a new write
    that nobody registered would run past cache, editor gate and proof
    alike. Refused before the request, so nothing is written.
    """
    from unittest.mock import patch

    api = LogseqAPI(host="localhost", port="12315", token="t")
    with patch("logseq_cli.api.requests.post") as post:
        with pytest.raises(UnknownMethod, match="logseq.Editor.setBlockProperty"):
            api.call("logseq.Editor.setBlockProperty", ["uuid", "k", "v"])
    post.assert_not_called()


def test_derived_sets_match_registry():
    """The two sets derived from the registry are the ones kept by hand before.

    Written out on purpose, unlike everything else in this file: this pins
    the move to the registry, which changed no method's cache behaviour.
    A later change to either set changes this list with it, in the same diff.
    """
    assert _CACHEABLE_METHODS == {
        "logseq.Editor.getPage",
        "logseq.Editor.getBlock",
        "logseq.Editor.getPageBlocksTree",
        "logseq.Editor.getPageLinkedReferences",
        "logseq.Editor.getAllPages",
        "logseq.App.getUserConfigs",
        "logseq.DB.datascriptQuery",
    }
    assert _MUTATING_METHODS == {
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
        "logseq.Editor.setBlocksId",
    }


@pytest.mark.parametrize(
    "method", sorted(m for m, kind in _METHODS.items() if isinstance(kind, UI)))
def test_ui_call_neither_caches_nor_clears(method):
    """A UI call changes no data: the cache stays, and its answer is not kept.

    ``checkEditing`` asks what the editor holds right now; a cached answer
    would report a block closed that was opened a second ago.
    """
    import json
    from unittest.mock import MagicMock, patch

    api = LogseqAPI(host="localhost", port="12315", token="t")
    kept = ("logseq.Editor.getPage", json.dumps(["Foo"]))
    api._cache[kept] = ({"cached": True}, float("inf"))
    resp = MagicMock(status_code=200)
    resp.json.return_value = False
    resp.raise_for_status = MagicMock()
    with patch("logseq_cli.api.requests.post", return_value=resp) as post:
        api.call(method)
        api.call(method)
    assert post.call_count == 2
    assert list(api._cache) == [kept]


# The task that gives each write its proof completes its entry; the gate
# rules come earlier (030-B3), so the proof decides when a case turns green.
# A write missing here has its proof.
_PROOF_TASK = {
    "logseq.Editor.updateBlock": "030-C4",
    "logseq.Editor.upsertBlockProperty": "030-C4",
    "logseq.Editor.removeBlockProperty": "030-C4",
    "logseq.Editor.removeBlock": "030-C5",
    "logseq.Editor.deletePage": "030-C5",
    "logseq.Editor.renamePage": "030-C5",
    "logseq.Editor.createPage": "030-C5",
    "logseq.Editor.setBlocksId": "030-C5",
}


@pytest.mark.parametrize("method", [
    pytest.param(m, marks=[pytest.mark.xfail(
        strict=True, reason=f"spec 030: {_PROOF_TASK[m]}")] if m in _PROOF_TASK else [])
    for m in sorted(m for m, kind in _METHODS.items() if isinstance(kind, Write))
])
def test_every_write_has_rule_and_proof(method):
    """Each write names its editor rule and its proof, and both exist.

    The names are what ``_write`` dispatches on (``_gate_<editor>``,
    ``_prove_<proof>``). A name without its method would be a label nothing
    acts on, and a label can disagree with the code it describes.
    """
    kind = _METHODS[method]
    assert kind.editor is not None and kind.proof is not None, (
        f"{method}: editor={kind.editor!r}, proof={kind.proof!r}"
    )
    assert callable(getattr(LogseqAPI, f"_gate_{kind.editor}", None)), kind.editor
    assert callable(getattr(LogseqAPI, f"_prove_{kind.proof}", None)), kind.proof


def test_every_mutating_method_has_a_wrapper():
    """The other direction: no entry without a call site.

    The read list was held to its call sites and the mutating list had no
    counterpart, which is how two entries survived that were never
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

    test_api_cache.py once asserted the name appears somewhere in api.py; that
    stays true for an entry whose wrapper was deleted, and with the registry
    in api.py it could not fail at all. This binds it to a call site.
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
