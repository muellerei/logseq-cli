"""An example script must not turn a failed call into success.

People copy the examples. Four of them swallowed errors: backup-graph.sh ended
each page in ``|| true`` with stderr thrown away, so a page that failed left an
empty file and the backup was reported done; export-all-pages.sh counted such
empty files as exported; two others piped the CLI into jq without
``pipefail``, so a failure looked like an empty result (#93).
"""
import os
import pathlib
import re
import shutil
import stat
import subprocess

import pytest


EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "examples"
SCRIPTS = sorted(p for p in EXAMPLES.glob("*.sh") if "logseq-cli" in p.read_text())


def _stub(tmp_path, body):
    stub = tmp_path / "bin" / "logseq-cli"
    stub.parent.mkdir(exist_ok=True)
    stub.write_text("#!/bin/sh\n" + body)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return {**os.environ, "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}"}


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_a_script_that_pipes_the_cli_sets_pipefail(script):
    text = script.read_text()
    pipes_the_cli = re.search(r"logseq-cli[^\n]*(\\\n[^\n]*)*\|(?!\|)", text)
    if pipes_the_cli:
        assert re.search(r"^set -\w*o pipefail", text, re.M), script.name


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_call_is_forced_to_succeed_or_silenced(script):
    """No `|| true`, and no stderr thrown away: the error is the only place
    that says why a call failed, and the old scripts discarded it."""
    for line in script.read_text().splitlines():
        if "logseq-cli" in line:
            assert "|| true" not in line, f"{script.name}: {line.strip()}"
            assert "2>/dev/null" not in line, f"{script.name}: {line.strip()}"


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("python3"),
                    reason="needs bash and python3")
def test_backup_reports_a_failed_page_and_leaves_no_empty_file(tmp_path):
    """Run the real script against a stand-in logseq-cli that fails one page."""
    env = _stub(tmp_path,
                'case "$1" in\n'
                "  get-all-pages) echo '[{\"originalName\": \"Alpha\"}, {\"originalName\": \"Beta\"}]';;\n"
                '  get-page) if [ "$3" = "Beta" ]; then echo "Error: boom" >&2; exit 1; fi\n'
                "            echo '{\"page\": \"Alpha\"}';;\n"
                "esac\n")
    out = tmp_path / "backup"
    result = subprocess.run(["bash", str(EXAMPLES / "backup-graph.sh"), str(out)],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode != 0
    assert "could not export: Beta" in result.stderr
    assert (out / "Alpha.json").read_text().strip() == '{"page": "Alpha"}'
    assert not (out / "Beta.json").exists()
    assert "Done." not in result.stdout


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("jq"),
                    reason="needs bash and jq")
def test_daily_todos_exits_non_zero_when_the_query_fails(tmp_path):
    env = _stub(tmp_path, "echo 'Error: boom' >&2\nexit 1\n")
    result = subprocess.run(["bash", str(EXAMPLES / "daily-todos.sh")],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode != 0
    assert "the query failed" in result.stdout
    assert "Error: boom" in result.stderr


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("jq"),
                    reason="needs bash and jq")
def test_daily_todos_lists_tasks_in_the_shape_smart_query_returns(tmp_path):
    """Each result is a row holding one block; the script read `.results[]`
    as blocks, so jq failed on every graph with a task."""
    env = _stub(tmp_path, """cat <<'JSON'
{"results": [[{"marker": "TODO", "content": "write the report"}],
             [{"marker": "DOING", "content": "review"}]]}
JSON
""")
    result = subprocess.run(["bash", str(EXAMPLES / "daily-todos.sh")],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "- [TODO] write the report" in result.stdout
    assert "- [DOING] review" in result.stdout


@pytest.mark.skipif(not shutil.which("bash") or not shutil.which("python3"),
                    reason="needs bash and python3")
def test_backup_does_not_let_two_pages_share_a_file(tmp_path):
    """"a/b" and "a_b" sanitize to the same file name; the second export must
    neither overwrite nor delete the first, and the backup must not say done."""
    env = _stub(tmp_path, """case "$1" in
  get-all-pages) echo '[{"originalName": "a/b"}, {"originalName": "a_b"}]';;
  get-page) printf '{"page": "%s"}\\n' "$3";;
esac
""")
    out = tmp_path / "backup"
    result = subprocess.run(["bash", str(EXAMPLES / "backup-graph.sh"), str(out)],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode != 0
    assert "could not export: a_b (its file name is taken by another page)" in result.stderr
    assert (out / "a_b.json").read_text().strip() == '{"page": "a/b"}'
    assert list(out.glob("*.part")) == []
