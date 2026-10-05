"""One list of task markers: tasks.ORDER and tasks.STATE.

Before them the code kept six lists of its own, and none was complete. This
reads the source of every module but tasks.py and finds each place where two
or more different marker words stand as whole words in one string literal or
one collection (a set, list, tuple, the keys of a dict): Datalog strings and
regexes included. EXCEPTIONS names the places that are still to be replaced;
the test wants the counts equal, so a new list in a module with an old
exception does not pass as "one of the old ones".

Prose is not a list. Docstrings and the first argument of ``fail`` are left
out; in ``help=`` and ``epilog=`` only an enumeration counts (two markers in a
row, separated by blanks, commas or slashes), not a sentence or an example
line, and not an arrow such as TODO->DONE.
"""
import ast
import collections
import pathlib
import re

import pytest

import logseq_cli.cli

PACKAGE = pathlib.Path(logseq_cli.cli.__file__).parent

# IN-PROGRESS first, so that it counts as one word.
MARKERS = ("IN-PROGRESS", "NOW", "LATER", "TODO", "DOING", "WAITING", "WAIT",
           "STARTED", "DONE", "CANCELED", "CANCELLED")
# A whole word: no letter, digit or underscore before or after. A hyphen is a
# boundary, so "TODO->DONE" holds TODO and DONE; WAIT does not count inside WAITING.
_ALT = "|".join(MARKERS)
WORD = re.compile(rf"(?<![A-Za-z0-9_])({_ALT})(?![A-Za-z0-9_])")
ENUMERATION = re.compile(
    rf"(?<![A-Za-z0-9_])(?:{_ALT})(?![A-Za-z0-9_])[\s,/]+(?:{_ALT})(?![A-Za-z0-9_])")

# (file, symbol) -> places still holding a marker list of their own.
EXCEPTIONS = {
    ("commands/analysis.py", "analyze_journal_patterns"): 2,
}


def _words(text):
    return {m.group(1) for m in WORD.finditer(text)}


def _literal_text(node):
    """The text of a str literal; of an f-string, its constant parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def _is_fail(call):
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "fail") or \
        (isinstance(func, ast.Attribute) and func.attr == "fail")


def sites(tree):
    """The places of a module as (line, markers)."""
    skip, enumerations, covered = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and _literal_text(first.value) is not None:
                skip.add(id(first.value))
        elif isinstance(node, ast.Call):
            if _is_fail(node) and node.args:
                skip.update(id(n) for n in ast.walk(node.args[0]))
            for kw in node.keywords:
                if kw.arg in ("help", "epilog") and _literal_text(kw.value) is not None:
                    enumerations.add(id(kw.value))
        elif isinstance(node, ast.JoinedStr):
            # Its constant parts count with it, once, not again one by one.
            covered.update(id(v) for v in node.values)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
            elts = node.keys if isinstance(node, ast.Dict) else node.elts
            elts = [e for e in elts if e is not None and id(e) not in skip]
            texts = [_literal_text(e) for e in elts]
            hit = set()
            for t in texts:
                hit |= _words(t or "")
            if len(hit) >= 2:
                found.append((node.lineno, hit))
                covered.update(id(n) for e in elts for n in ast.walk(e))
    for node in ast.walk(tree):
        if id(node) in covered or id(node) in skip:
            continue
        text = _literal_text(node)
        if text is None:
            continue
        if id(node) in enumerations:
            if ENUMERATION.search(text):
                found.append((node.lineno, _words(text)))
        elif len(_words(text)) >= 2:
            found.append((node.lineno, _words(text)))
    return sorted(found, key=lambda s: s[0])


def _symbol(tree, line):
    """The module-level function, class or variable holding ``line``."""
    for node in tree.body:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        if start <= line <= node.end_lineno:
            if hasattr(node, "name"):
                return node.name
            targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
            if isinstance(targets[0], ast.Name):
                return targets[0].id
            return "<module>"
    return "<module>"


def found_in_package():
    found = collections.Counter()
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "tasks.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, _ in sites(tree):
            found[(path.relative_to(PACKAGE).as_posix(), _symbol(tree, line))] += 1
    return found


def test_no_module_but_tasks_keeps_a_marker_list():
    found = collections.Counter(found_in_package())
    more = {k: n - EXCEPTIONS.get(k, 0) for k, n in found.items() if n > EXCEPTIONS.get(k, 0)}
    fewer = {k: n - found.get(k, 0) for k, n in EXCEPTIONS.items() if n > found.get(k, 0)}
    assert not more, f"more found than allowed, a new marker list; use tasks.ORDER/STATE: {more}"
    assert not fewer, f"fewer found than allowed, an outdated entry; lower the count or drop it: {fewer}"


def _count(source):
    return len(sites(ast.parse(source)))


class TestTheScanFindsWhatItShould:
    """The scanner on source of its own, so that it catches and not only counts.
    Every case is an assignment: an expression alone as a statement is a
    docstring, and docstrings are left out."""

    @pytest.mark.parametrize("source,expected", [
        ('x = {"TODO", "DONE"}', 1),
        ('x = ("TODO", "TODO")', 0),
        ('x = "a TODO and DONE b"', 1),
        ('x = "std::TODO_DONE"', 0),
        ('x = "TODO->DONE"', 1),
        ('x = f"{x} TODO DONE"', 1),
        ('x = f"{TODO} and {DONE}"', 0),
        ('x = "WAITING for"', 0),
        ('x = ["TODO", "DONE"]', 1),
        ('x = {"TODO": 1, "DONE": 2}', 1),
        ('"""TODO and DONE"""', 0),
        ('class A:\n    """TODO and DONE"""', 0),
        ('def f():\n    """TODO and DONE"""', 0),
        ('fail("TODO or DONE")', 0),
        ('output.fail("TODO or DONE")', 0),
        ('@cli.command(epilog="TODO->DONE")\ndef f(): pass', 0),
        ('@click.option(help="TODO, DOING")\ndef f(): pass', 1),
        ('@click.option(help="TODO/DOING")\ndef f(): pass', 1),
        ('@click.option(help="TODO DOING")\ndef f(): pass', 1),
        ('@click.option(help="only the marker DOING separates it from TODO")\ndef f(): pass', 0),
        ('@click.option(help="TODO, " "DOING")\ndef f(): pass', 1),
        ('@click.option(help=f"{x} TODO, DOING")\ndef f(): pass', 1),
        ('@cli.command(epilog="get-todos --status TODO --status DOING")\ndef f(): pass', 0),
    ])
    def test_scan(self, source, expected):
        assert _count(source) == expected
