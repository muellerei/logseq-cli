"""The rule and the setting behind ``[graph] keep_empty_blocks_last`` (#110).

Two things that need no Logseq: which blocks at the end of a section count as
free space (``trailing_empty_run``), and how the setting is read from the
config file and the environment (``keep_empty_blocks_last``).
"""
import pytest

from logseq_cli.config import ConfigError, keep_empty_blocks_last
from logseq_cli.outlinetext import trailing_empty_run

ENV = "LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST"


def block(uuid, content="", children=None, properties=None):
    return {"uuid": uuid, "content": content, "children": children or [],
            "properties": properties or {}}


class TestTrailingEmptyRun:
    def test_the_empty_blocks_at_the_end_in_order(self):
        children = [block("a", "TODO a"), block("e1"), block("e2")]
        assert trailing_empty_run(children) == ["e1", "e2"]

    def test_none_when_the_last_block_has_text(self):
        assert trailing_empty_run([block("e1"), block("a", "TODO a")]) == []

    def test_empty_blocks_in_the_middle_are_not_part_of_the_run(self):
        children = [block("a", "TODO a"), block("e1"), block("b", "TODO b")]
        assert trailing_empty_run(children) == []

    def test_a_run_stops_at_the_last_block_with_text(self):
        children = [block("e0"), block("a", "TODO a"), block("e1"), block("e2")]
        assert trailing_empty_run(children) == ["e1", "e2"]

    def test_no_children_no_run(self):
        assert trailing_empty_run([]) == []
        assert trailing_empty_run(None) == []

    def test_spaces_only_count_as_empty(self):
        assert trailing_empty_run([block("a", "x"), block("e1", "  \t")]) == ["e1"]

    def test_an_empty_block_with_a_child_is_not_empty(self):
        # The second one indented under the first, which Tab makes and two
        # Enter do not: the children were put there on purpose.
        nested = block("e1", children=[block("e2")])
        assert trailing_empty_run([block("a", "x"), nested]) == []

    def test_a_property_makes_a_block_not_empty(self):
        children = [block("a", "x"), block("e1", properties={"id": "e1"})]
        assert trailing_empty_run(children) == []

    def test_a_child_that_is_a_bare_ref_is_not_empty(self):
        # getBlock may hand a child back as a bare uuid: nothing is known of
        # it, so it is not taken for free space.
        assert trailing_empty_run([block("a", "x"), ["uuid", "e1"]]) == []


class TestTheSetting:
    @pytest.fixture(autouse=True)
    def no_env(self, monkeypatch):
        monkeypatch.delenv(ENV, raising=False)

    def test_off_by_default(self):
        assert keep_empty_blocks_last({}) is False
        assert keep_empty_blocks_last({"graph": {}}) is False

    def test_on_in_the_graph_section(self):
        assert keep_empty_blocks_last({"graph": {"keep_empty_blocks_last": True}}) is True

    def test_not_a_bool_is_a_config_error_that_names_the_key(self):
        with pytest.raises(ConfigError, match=r"\[graph\] keep_empty_blocks_last must be true or false"):
            keep_empty_blocks_last({"graph": {"keep_empty_blocks_last": "yes"}, "_path": "c.toml"})

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_the_environment_turns_it_on_over_the_file(self, monkeypatch, value):
        monkeypatch.setenv(ENV, value)
        assert keep_empty_blocks_last({"graph": {"keep_empty_blocks_last": False}}) is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off"])
    def test_the_environment_turns_it_off_over_the_file(self, monkeypatch, value):
        monkeypatch.setenv(ENV, value)
        assert keep_empty_blocks_last({"graph": {"keep_empty_blocks_last": True}}) is False

    def test_an_empty_variable_is_not_set(self, monkeypatch):
        monkeypatch.setenv(ENV, "")
        assert keep_empty_blocks_last({"graph": {"keep_empty_blocks_last": True}}) is True

    def test_an_unknown_value_counts_as_off_and_says_so(self, monkeypatch, capsys):
        monkeypatch.setenv(ENV, "maybe")
        assert keep_empty_blocks_last({"graph": {"keep_empty_blocks_last": True}}) is False
        assert ENV in capsys.readouterr().err
