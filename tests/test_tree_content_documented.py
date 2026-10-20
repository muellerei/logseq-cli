"""The help and the docs state the rule by which --content becomes a tree.

They said "Auto-detects tab-indented hierarchy", "Indent sub-bullets for
children" and "same tab-indent format": indentation alone makes no tree,
only an indented line that starts with "- " does (#146). The rule has one
source, TREE_CONTENT_RULE; tests/test_tree_content_rule.py pins the
behaviour it describes. Help and docs are wrapped by hand, so every
comparison runs on normalized whitespace.
"""
import pytest

from logseq_cli.cli import cli
from logseq_cli.commands.journal import add_journal_block
from logseq_cli.outlinetext import TREE_CONTENT_EXAMPLE, TREE_CONTENT_RULE
from tests.conftest import split_runner

COMMANDS = ["add-journal-block", "add-journal-entry", "add-journal-content", "insert-block"]
FORBIDDEN = [
    "Auto-detects tab-indented hierarchy", "auto-detects hierarchy",
    "auto-detects hierarchical content", "Indent sub-bullets",
    "--content without indentation", "Without indentation `--content`",
    "same tab-indent format", "Content read as a tree replaces", "were written as text",
    "goes in as one block, indented", "Further --content-file roots",
    "Further roots of `--content-file`",
]


def flat(text):
    return " ".join(text.split())


def help_of(command):
    r = split_runner().invoke(cli, [command, "--help"])
    assert r.exit_code == 0, r.stderr
    return flat(r.stdout)


def option_help(command, name):
    return flat(next(p for p in command.params if p.name == name).help)


@pytest.mark.parametrize("command", COMMANDS)
def test_no_help_says_indentation_alone_makes_children(command):
    text = help_of(command)
    assert [s for s in FORBIDDEN if flat(s) in text] == []


@pytest.mark.parametrize("command", ["add-journal-block", "insert-block"])
def test_the_help_states_the_rule(command):
    assert flat(TREE_CONTENT_RULE) in help_of(command)


def test_the_rule_says_which_lines_stay_with_the_block_above():
    # The parser attaches them indented or not: a rule that calls every
    # indented line a child sends "- owner:: bob" into the wrong place.
    rule = flat(TREE_CONTENT_RULE)
    assert "indented or not" in rule
    assert '"- key:: value" line indented deeper' in rule


def test_add_journal_block_shows_the_example_and_its_own_way_out():
    text = help_of("add-journal-block")
    shown = TREE_CONTENT_EXAMPLE.replace("\n", "\\n").replace("\t", "\\t")
    assert f"--content $'{shown}'" in text
    assert "use --content-file" in text
    assert "use --tree" not in text
    # --no-preserve reads no value as a tree, the rule's trigger included.
    assert "With --no-preserve no value is a tree" in text


def test_insert_block_names_tree_not_content_file():
    text = help_of("insert-block")
    assert "use --tree or --tree-file" in text
    assert "use --content-file" not in text
    assert "This holds for every target" in text


def test_add_journal_content_still_points_to_add_journal_block():
    assert "Prefer add-journal-block" in help_of("add-journal-content")


def test_upsert_heading_says_what_replaces_the_children():
    text = option_help(add_journal_block, "upsert_heading")
    for phrase in ("ambiguous", "first block", "replace the block's children"):
        assert phrase in text


def test_expect_tree_hash_names_where_the_first_tree_hash_comes_from():
    # The option's own help: the whole --help names get-block --json already.
    text = option_help(add_journal_block, "expect_tree_hash")
    for phrase in ("get-block --id UUID --json", "get-journal-range", "--under-heading"):
        assert phrase in text


# --- the docs ------------------------------------------------------------------

DOCS = ["docs/commands.md", "AGENTS.md", "skills/logseq-cli/SKILL.md", "README.md"]


def doc(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def paragraph(text, start):
    """The paragraph of ``text`` that holds ``start``, to the next blank line."""
    at = text.index(start)
    begin = text.rfind("\n\n", 0, at) + 2
    end = text.find("\n\n", at)
    return flat(text[begin:end if end != -1 else None])


@pytest.mark.parametrize("path", DOCS)
def test_no_doc_says_indentation_alone_makes_children(path):
    text = flat(doc(path))
    assert [s for s in FORBIDDEN if flat(s) in text] == []


def test_commands_md_states_the_rule():
    text = flat(doc("docs/commands.md"))
    assert "starts with `- `" in text and "like `--content-file`" in text


def test_agents_md_states_the_rule():
    text = flat(doc("AGENTS.md"))
    for phrase in ("start with `- `", "like `--content-file`", "four exceptions"):
        assert phrase in text


def test_readme_says_when_dictated_text_is_a_tree():
    assert "read as a tree" in flat(doc("README.md"))


def test_agents_md_names_written_uuids():
    assert "written_uuids" in doc("AGENTS.md")


@pytest.mark.parametrize("path, start", [
    ("AGENTS.md", "On `precondition_required`:"),
    ("skills/logseq-cli/SKILL.md", "`precondition_required`: pass"),
])
def test_precondition_required_names_the_tree_hash_of_an_upsert(path, start):
    text = paragraph(doc(path), start)
    assert "first block has children" in text
    assert "get-block --id UUID --json" in text
