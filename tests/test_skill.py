"""skills/logseq-cli/SKILL.md must not be able to disagree with the CLI.

The skill is read by agents that have not used the tool yet, so a command or
an option it names that does not exist sends them off on a wrong call first
thing. Like the README tables (test_readme_documents_options), it is a view
on the command registry, and this compares the two.

The frontmatter follows the Agent Skills specification (agentskills.io):
``name`` is lower-case letters, digits and single hyphens, at most 64
characters, and the same as the folder; ``description`` at most 1024
characters. Fields only one agent understands are left out: claude.ai
refuses a skill that carries Claude Code's own fields.
"""
import pathlib
import re

import click

from logseq_cli.cli import cli

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "logseq-cli" / "SKILL.md"
SPEC_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def _parts():
    text = SKILL.read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    fields = dict(line.split(": ", 1) for line in front.strip().splitlines())
    return text, fields, body


TEXT, FIELDS, BODY = _parts()
# click keeps --help out of ``params``; it is an option all the same.
OPTIONS = {name: {opt for param in command.params
                  for opt in [*getattr(param, "opts", ()), *getattr(param, "secondary_opts", ())]
                  if opt.startswith("--")} | set(click.Context(command).help_option_names)
           for name, command in cli.commands.items()}


def _spans(body):
    """Each `code span` of ``body``, split into words, without a leading
    ``logseq-cli``."""
    spans = [span.split() for span in re.findall(r"`([^`\n]+)`", body)]
    return [words[1:] if words[0] == "logseq-cli" else words for words in spans
            if words != ["logseq-cli"]]


class TestFrontmatter:
    def test_only_fields_of_the_specification(self):
        assert set(FIELDS) <= SPEC_FIELDS, set(FIELDS) - SPEC_FIELDS

    def test_the_name_is_the_folder(self):
        assert FIELDS["name"] == SKILL.parent.name
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", FIELDS["name"])
        assert len(FIELDS["name"]) <= 64

    def test_the_description_fits(self):
        assert 0 < len(FIELDS["description"]) <= 1024


def test_the_skill_stays_short():
    # Spec 008: a long skill is not read. AGENTS.md carries the reference.
    assert len(TEXT.splitlines()) < 80


def test_the_scan_sees_the_commands():
    """Guards the guard: a scan that finds nothing would pass silently."""
    named = {words[0] for words in _spans(BODY) if words[0] in cli.commands}
    assert {"doctor", "get-page", "delete-page", "find-block"} <= named


def test_every_command_named_exists():
    """A renamed command must not live on in the skill: any code span that
    starts with a word shaped like a command name has to be one."""
    shaped = {words[0] for words in _spans(BODY)
              if re.fullmatch(r"[a-z]+(-[a-z]+)+", words[0])}
    assert shaped - set(cli.commands) == set()


def test_every_option_named_with_its_command_exists():
    wrong = [" ".join(words) for words in _spans(BODY)
             if words[0] in cli.commands
             for word in words[1:] if word.startswith("--") and word not in OPTIONS[words[0]]]
    assert not wrong, f"the skill names options these commands do not have: {wrong}"


def test_every_option_named_alone_exists_somewhere():
    every = set().union(*OPTIONS.values())
    alone = {words[0] for words in _spans(BODY) if words[0].startswith("--")}
    assert alone, "the scan found no option named on its own"
    assert alone <= every, alone - every


def test_the_commands_said_to_preview_do_preview():
    bullet = BODY.split("**Preview destructive writes with `--dry-run`**", 1)[1].split("\n- ", 1)[0]
    named = [words[0] for words in _spans(bullet) if words[0] in cli.commands]
    assert len(named) >= 5
    assert [name for name in named if "--dry-run" not in OPTIONS[name]] == []


def test_the_pointer_names_a_file_the_repository_has():
    assert "/blob/main/AGENTS.md" in BODY
    assert (ROOT / "AGENTS.md").is_file()
