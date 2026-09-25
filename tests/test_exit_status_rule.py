"""The documentation promises nothing about exit codes the code does not keep.

Until 0.15 the documentation made three promises at once. The README said
there was deliberately no second exit code; AGENTS.md and the agent skill told
agents that 1 meant a failure and 2 a refused call, and to rely on that; the
code kept neither, with most refusals exiting 1 and a few exiting 2. An agent
that followed the skill read meaning into a number that had none.

The rule now is the one the code keeps: 0 means the call did what it says,
anything else means it did not, and the error says why. The number itself
carries no meaning. So no document a caller reads names 1 or 2 for a kind of
error, and this test keeps it that way. CHANGELOG and the ADRs are history
and may name what used to be.
"""
import ast
import pathlib
import re

from logseq_cli.cli import cli


ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ["README.md", "AGENTS.md", "CONTRIBUTING.md", "docs/configuration.md",
        "skills/logseq-cli/SKILL.md"]

# A number given a meaning as an exit status: "exits 1", "exit with code 1",
# "Exit 2", across a line break too; "1 = something is wrong"; and the form
# the skill used, "1 a failure, 2 input the tool refused" / "1 for a failure".
_MEANINGS = [
    re.compile(r"\bexit(?:s|ed)?\s+(?:with\s+)?(?:code\s+|status\s+)?[12]\b", re.I),
    re.compile(r"(?<![\w.\-])[12]\s*=\s", re.I),
    re.compile(r"(?<![\w.\-])[12]\s+(?:a|for)\s", re.I),
]


def _hits(text):
    return [m.group(0) for pattern in _MEANINGS for m in pattern.finditer(text)]


def _help_texts():
    for name, command in cli.commands.items():
        for text in (command.help, command.epilog):
            if text:
                yield f"{name} (help/epilog)", text
        for param in command.params:
            if getattr(param, "help", None):
                yield f"{name} {param.opts[0]}", param.help


def test_no_document_gives_an_exit_number_a_meaning():
    found = {}
    for rel in DOCS:
        hits = _hits((ROOT / rel).read_text(encoding="utf-8"))
        if hits:
            found[rel] = hits
    assert not found, (
        "exit 0 means done and anything else means not done; the number carries "
        f"no meaning, so no document may promise one: {found}")


def test_no_help_text_gives_an_exit_number_a_meaning():
    found = {where: _hits(text) for where, text in _help_texts() if _hits(text)}
    assert not found, found


def test_the_patterns_catch_what_the_documentation_used_to_say():
    """The check is only worth something if it would have caught the old text."""
    for old in ("1 for a failure, 2 for\n  input the tool refused",
                "1 for a failure.",
                "0 is success, 1 a failure, 2 input the tool refused",
                "exits\n1 rather than",
                "exit with code 1",
                "Exit 0 = ready, 1 = something is wrong",
                "fails with exit 2"):
        assert _hits(old), old


def test_only_two_places_set_an_exit_code_by_hand():
    """A number set by hand at one call site is how 1 and 2 drifted apart.

    The two that remain predate the rule and are left alone: under it their
    value is immaterial, and changing it would change behaviour for nothing.
    A new error goes through fail() with its default.
    """
    places = []
    for path in (ROOT / "logseq_cli").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "exit_code":
                places.append(path.name)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "exit"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "sys"):
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and arg.value not in (0, 1):
                    places.append(f"{path.name}: sys.exit({arg.value})")
    assert sorted(places) == ["output.py", "output.py"], places
