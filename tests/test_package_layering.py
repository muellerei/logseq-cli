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
"""
import ast
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
