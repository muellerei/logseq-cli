"""The import rules the package split rests on, checked against the source.

`docs/adr/0001-explicit-command-registration.md` states three of them: a
command module decorates against the group, `commands/__init__.py` stays empty,
and `group.py` imports nothing from `commands/`, so the dependency runs one way
and there is no cycle.

Only one of the three fails on its own. Making `group.py` import a command
module is a real cycle and the suite collapses. The other two are quiet: a
command module reaching sideways for a helper works fine until two of them
reach for each other, and a `commands/__init__.py` that imports a module makes
that module load whether or not `cli.py` names it — which turns the explicit
import list into decoration and hides a missing entry.

Both were checked by `local/specs/audit-001-map.py` while the split was being
made. That script parses the module map out of a specification which is now
archived, and it runs nowhere on its own. These assertions do not depend on it.

`docs/adr/0003-shared-code-is-split-by-what-it-decides.md` adds two more: the
import graph of the whole package has no cycle, and the modules named in PURE
take no ``api`` and import no module that does.
"""
import ast
import importlib.util
import pathlib

import logseq_cli.cli

PACKAGE = pathlib.Path(logseq_cli.cli.__file__).parent
COMMANDS = PACKAGE / "commands"


def _imports(path):
    """Every `logseq_cli.*` module this file imports, by dotted name."""
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("logseq_cli"):
            out.add(node.module)
            for alias in node.names:
                out.add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("logseq_cli"))
    return out


def test_no_command_module_imports_another_command_module():
    """One module is one command domain; shared code lives beside them."""
    offenders = {}
    for path in sorted(COMMANDS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        sideways = {
            i for i in _imports(path)
            if i.startswith("logseq_cli.commands")
            and not i.startswith(f"logseq_cli.commands.{path.stem}")
        }
        if sideways:
            offenders[path.name] = sorted(sideways)
    assert not offenders, (
        f"{offenders} — a command module reached sideways. Either the helper "
        f"belongs in render.py/output.py, or it is shared and should move there; "
        f"a command module is not a library for its neighbours."
    )


def test_the_commands_package_init_stays_empty():
    """An importing __init__ loads modules cli.py never named."""
    init = COMMANDS / "__init__.py"
    body = init.read_text(encoding="utf-8").strip()
    assert body == "", (
        f"commands/__init__.py is not empty: {body!r}. Anything imported here "
        f"is registered regardless of the import list in cli.py, so a module "
        f"missing from that list would stop being visible as missing."
    )


def test_group_does_not_import_from_commands():
    """The dependency runs one way: commands -> group, never back."""
    offenders = sorted(
        i for i in _imports(PACKAGE / "group.py") if i.startswith("logseq_cli.commands")
    )
    assert not offenders, (
        f"group.py imports {offenders}, which closes the cycle the explicit "
        f"registration list exists to avoid"
    )


def test_only_the_entry_point_imports_from_commands():
    """Everything else in the package stays below the command layer."""
    offenders = {}
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == "cli.py":
            continue
        reaching = sorted(i for i in _imports(path) if i.startswith("logseq_cli.commands"))
        if reaching:
            offenders[path.name] = reaching
    assert not offenders, (
        f"{offenders} — only cli.py imports from commands/; a module below the "
        f"command layer that reaches up inverts the dependency"
    )


def _module_name(path):
    """``logseq_cli/render.py`` -> ``logseq_cli.render``; a package's __init__ is the package."""
    parts = path.relative_to(PACKAGE.parent).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _package_imports(path):
    """:func:`_imports`, plus relative imports resolved to their dotted name.

    ``from .api import LogseqAPI`` names no ``logseq_cli`` and would pass both
    tests below unseen; ruff's rule set here does not forbid the form."""
    here = _module_name(path)
    package = here if path.name == "__init__.py" else here.rpartition(".")[0]
    out = _imports(path)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            module = importlib.util.resolve_name("." * node.level + (node.module or ""), package)
            out.add(module)
            out.update(f"{module}.{alias.name}" for alias in node.names)
    return out


def test_the_package_import_graph_has_no_cycle():
    """No module reaches back to one that imports it.

    Some cycles fail on import and need no test. Others Python accepts in
    silence: an import moved into a function, the usual repair for the first
    kind, or a plain ``import`` placed after the names the other side needs.
    Those pass the whole suite (ADR 0003)."""
    paths = {_module_name(p): p for p in PACKAGE.rglob("*.py")}
    graph = {name: sorted(_package_imports(path) & set(paths) - {name})
             for name, path in paths.items()}
    done, trail = set(), []

    def visit(name):
        if name in trail:
            return trail[trail.index(name):] + [name]
        if name in done:
            return None
        trail.append(name)
        for dep in graph[name]:
            cycle = visit(dep)
            if cycle:
                return cycle
        trail.pop()
        done.add(name)
        return None

    for name in sorted(graph):
        cycle = visit(name)
        assert not cycle, (
            f"import cycle: {' -> '.join(cycle)}. One side has to stop importing "
            f"the other; moving the import into a function only hides it")


# Written out, not derived: the list is a decision, and adding or removing a
# module here should be a visible change.
PURE = ("dates", "outlinetext", "cliinput")


def _takes_api(path):
    """True for a module that handles a LogseqAPI or imports its module.

    Handling it covers a parameter named ``api`` and the way every command
    gets one, ``api = ctx.obj["api"]``: a name ``api`` or a lookup of the key."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            if any(a.arg == "api" for a in args):
                return True
        if isinstance(node, ast.Name) and node.id == "api":
            return True
        if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and node.slice.value == "api"):
            return True
    return "logseq_cli.api" in _package_imports(path)


def test_the_pure_modules_need_no_logseq_api():
    """These three need no LogseqAPI, and their docstrings and ADR 0003 say
    so. That was not why they were cut out, but it keeps them testable without
    a mock. Code that needs the API belongs elsewhere."""
    api_side = {_module_name(p) for p in PACKAGE.glob("*.py") if _takes_api(p)}
    offenders = {}
    for name in PURE:
        path = PACKAGE / f"{name}.py"
        reached = sorted(_package_imports(path) & api_side)
        if _takes_api(path):
            reached.append("handles an api or imports logseq_cli.api itself")
        if reached:
            offenders[name] = reached
    assert not offenders, (
        f"{offenders} — a module in PURE reaches LogseqAPI. Move what needs "
        f"the API into a module that already takes it, or take the module out "
        f"of PURE and say why in ADR 0003")
