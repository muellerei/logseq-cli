"""#98: --resolve-refs names the page each resolved Block Ref came from.

The page came from ``getBlock``, which answers it as ``{id}`` alone when
asked without children (measured, 0.10.15), so the ``↳ <page>`` the README
promises never showed. The page's name is read with ``getPage`` by that id.
The tests run against the HTTP double, which answers as Logseq does; the old
MagicMock tests answered with a name and passed.
"""
from logseq_cli.cli import cli
from tests.conftest import split_runner
from tests.logseq_http_double import LogseqHttpDouble

TARGET = "00000000-0000-4000-8000-0000000000a1"


def _read(monkeypatch, target_text, ref_block, *args):
    double = LogseqHttpDouble.installed(monkeypatch, {
        "Meeting Notes": [{"uuid": TARGET, "content": target_text}],
        "Probe": [ref_block],
    })
    result = split_runner().invoke(
        cli, ["get-page", "--page", "Probe", "--no-backlinks", "--resolve-refs", *args])
    return result, double


class TestTheResolvedPage:
    def test_names_the_page_the_target_is_on(self, monkeypatch):
        result, _ = _read(monkeypatch, "TODO Draft the agenda", f"see (({TARGET})) here")
        assert result.exit_code == 0, result.stderr
        assert "- see TODO Draft the agenda ↳ Meeting Notes here" in result.stdout

    def test_looks_up_the_page_once_for_several_refs_to_it(self, monkeypatch):
        result, double = _read(monkeypatch, "TODO Draft the agenda",
                               f"(({TARGET})) and (({TARGET.upper()}))")
        assert result.exit_code == 0, result.stderr
        assert result.stdout.count("↳ Meeting Notes") == 2
        by_id = [a for a in double.sent("getPage") if isinstance(a[0], int)]
        assert len(by_id) == 1
