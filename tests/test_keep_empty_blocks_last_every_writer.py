"""Every write at the end of a section knows about ``keep_empty_blocks_last`` (#110).

The rule lives in ``strictinsert``, and the commands reach it through
``before_empty_or_append`` and ``before_first_empty``. A function that writes at the end
of a section on its own, past those, would ignore the setting without a word.
This test finds every such call outside ``api.py`` and ``strictinsert.py``:
the direct writes (``append_block_in_page``, ``insert_block``,
``insert_batch_block``) and the helpers that append at the end
(``append_in_page``, ``insert_tree_at_page_end``, ``insert_block_at``,
``insert_block_tree_with_uuids``, ``insert_block_tree_at_page_top``). It asks the
function that makes the call either to use the rule (``before_empty_or_append`` or
``before_first_empty`` is called in its source) or to be on the list of the ones
that are left out, each with its reason. A new write at the end fails here until
it does one or the other.
"""
import ast
import pathlib
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent / "logseq_cli"
DIRECT_WRITES = {"append_block_in_page", "insert_block", "insert_batch_block"}
END_HELPERS = {"append_in_page", "insert_tree_at_page_end", "insert_block_at",
               "insert_block_tree_with_uuids", "insert_block_tree_at_page_top"}
WRITES = DIRECT_WRITES | END_HELPERS
SKIPPED_MODULES = {"api.py", "strictinsert.py"}

# (module, function): why the setting does not apply.
LEFT_OUT = {
    ("commands/journal.py", "add_journal_entry"):
        "deprecated, writes line by line at the end of the page and stays as it was",
    ("commands/pages.py", "create_page"):
        "a page that was just created has no empty blocks at its end",
    ("commands/properties.py", "_write_property_block"):
        "writes the property block at the top of a page, not at its end",
}


def _called(call):
    """The name a call goes by, as ``api.insert_block(...)`` or ``insert_block_at(...)``."""
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


RULE = {"before_empty_or_append", "before_first_empty"}


def called_in(source):
    """The names of the calls a function's source makes: a mention in a comment
    or a string does not count."""
    return {_called(n) for n in ast.walk(ast.parse(textwrap.dedent(source)))
            if isinstance(n, ast.Call)}


def direct_writers():
    """``{(module, function): ([name, ...], source)}`` for every function that
    calls a direct write or an end helper, nested functions on their own."""
    found = {}
    for path in sorted(ROOT.rglob("*.py")):
        if path.name in SKIPPED_MODULES:
            continue
        source = path.read_text(encoding="utf-8")
        for fn in ast.walk(ast.parse(source)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            methods = sorted({_called(n) for n in ast.walk(fn)
                              if isinstance(n, ast.Call) and _called(n) in WRITES})
            if methods:
                found[(path.relative_to(ROOT).as_posix(), fn.name)] = (
                    methods, ast.get_source_segment(source, fn))
    return found


def test_the_scan_sees_the_writers_it_is_meant_to():
    # A scan that finds nothing would pass every check below.
    seen = {key for key in direct_writers()}
    assert ("commands/edit.py", "add_block_ref") in seen
    assert ("commands/edit.py", "_copy_tree") in seen
    assert ("headings.py", "find_or_create_heading") in seen
    # Through the helpers, past a direct call:
    assert ("commands/edit.py", "insert_block_cmd") in seen
    assert ("commands/journal.py", "add_journal_block") in seen
    assert ("commands/pages.py", "add_note_content") in seen


@pytest.mark.parametrize("key", sorted(direct_writers()), ids=lambda k: "::".join(k))
def test_a_direct_write_uses_the_rule_or_is_left_out_on_purpose(key):
    methods, source = direct_writers()[key]
    uses_the_rule = bool(RULE & called_in(source))
    if key in LEFT_OUT:
        assert not uses_the_rule, f"{key} is left out but uses the rule: {LEFT_OUT[key]}"
        return
    assert uses_the_rule, (
        f"{key} calls {methods} past keep_empty_blocks_last (#110): use "
        "before_empty_or_append, or list it in LEFT_OUT with the reason")


def test_nothing_left_out_is_stale():
    assert set(LEFT_OUT) <= set(direct_writers()), "a LEFT_OUT entry no longer writes directly"
