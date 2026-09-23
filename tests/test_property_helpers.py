"""Unit tests for the property/output helper layer.

These helpers (coerce_property_value, parse_property_pairs, apply_block_properties,
uuid_fields, insert_formatted_content_with_uuids) were previously only exercised
indirectly through CLI end-to-end tests. These tests pin their behaviour directly.
"""
import pytest
from unittest.mock import MagicMock

from logseq_cli.helpers import (
    coerce_property_value,
    parse_property_pairs,
    apply_block_properties,
    uuid_fields,
    insert_formatted_content_with_uuids,
)


class TestCoercePropertyValue:
    def test_integer_string_becomes_int(self):
        assert coerce_property_value("42") == 42
        assert isinstance(coerce_property_value("42"), int)

    def test_zero_becomes_int(self):
        assert coerce_property_value("0") == 0
        assert isinstance(coerce_property_value("0"), int)

    def test_surrounding_whitespace_is_ignored_like_the_parser_does(self):
        # --property "prio = 5" hands over " 5"; Logseq reads that line as 5.
        assert coerce_property_value(" 5") == 5
        assert coerce_property_value("5 ") == 5

    def test_largest_exact_integer_becomes_int(self):
        assert coerce_property_value("9007199254740991") == 9007199254740991

    # #35: a value is sent as a number only where Logseq's parser makes one AND
    # printing that number gives back the typed text. Everything else is sent
    # as typed, because the file shows whatever upsertBlockProperty receives.
    # Measured against Logseq 0.10.15; the comments say what the file would
    # have shown had the value gone out as a number, or why it stays text.
    @pytest.mark.parametrize("typed", [
        "01234",             # leading zero: 1234 in the file
        "00",
        "1.50",              # 1.5
        "3.14",              # parser keeps text
        "-7",                # parser keeps text
        "+5",                # 5
        "1e3",               # 1000
        "1_0",               # 10
        "\u0665",           # Arabic-Indic five: 5
        "9007199254740992",  # parser keeps text above 2^53-1
        "9007199254740993",  # ...2992: JSON carries it as a double
        "nan", "inf", "-inf", "1e400",  # floats JSON cannot carry
        "1" * 5000,          # past Python's int() digit limit: must not raise
    ])
    def test_value_is_sent_as_typed(self, typed):
        assert coerce_property_value(typed) == typed
        assert isinstance(coerce_property_value(typed), str)

    def test_plain_string_unchanged(self):
        assert coerce_property_value("hello") == "hello"

    def test_bool_like_stays_string(self):
        # Logseq stores booleans as text; coercion must not turn them into Python bools.
        assert coerce_property_value("true") == "true"
        assert coerce_property_value("false") == "false"

    def test_date_like_stays_string(self):
        assert coerce_property_value("2026-06-11") == "2026-06-11"


class TestParsePropertyPairs:
    def test_basic_pair(self):
        assert parse_property_pairs(["type=note"]) == [("type", "note")]

    def test_value_may_contain_commas_and_spaces(self):
        # Split on FIRST '=' only.
        assert parse_property_pairs(["tags=mcp, agents"]) == [("tags", "mcp, agents")]

    def test_value_may_contain_equals(self):
        assert parse_property_pairs(["expr=a=b"]) == [("expr", "a=b")]

    def test_numeric_value_is_coerced(self):
        assert parse_property_pairs(["count=5"]) == [("count", 5)]

    def test_key_is_stripped(self):
        assert parse_property_pairs(["  k =v"]) == [("k", "v")]

    def test_multiple_pairs_preserve_order(self):
        assert parse_property_pairs(["a=1", "b=2"]) == [("a", 1), ("b", 2)]

    def test_missing_equals_raises(self):
        with pytest.raises(ValueError, match="expected KEY=VALUE"):
            parse_property_pairs(["nopair"])

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="empty key"):
            parse_property_pairs(["=value"])


class TestApplyBlockProperties:
    def test_upserts_each_pair_and_returns_applied(self):
        api = MagicMock()
        applied = apply_block_properties(api, "blk-uuid", ["type=note", "count=5"])
        assert applied == {"type": "note", "count": 5}
        calls = {(c.args[1], c.args[2]) for c in api.upsert_block_property.call_args_list}
        assert calls == {("type", "note"), ("count", 5)}
        # Always called on the given block uuid.
        for c in api.upsert_block_property.call_args_list:
            assert c.args[0] == "blk-uuid"

    def test_invalid_pair_raises_before_any_upsert(self):
        api = MagicMock()
        with pytest.raises(ValueError):
            apply_block_properties(api, "blk-uuid", ["ok=1", "broken"])
        # parse_property_pairs validates the whole list first → no partial writes.
        api.upsert_block_property.assert_not_called()


class TestUuidFields:
    def test_multiple_uuids(self):
        assert uuid_fields(["a", "b", "c"]) == {"uuid": "a", "uuids": ["a", "b", "c"]}

    def test_single_uuid(self):
        assert uuid_fields(["only"]) == {"uuid": "only", "uuids": ["only"]}

    def test_empty_list_yields_none_root(self):
        assert uuid_fields([]) == {"uuid": None, "uuids": []}

    def test_returns_a_copy_not_the_input_list(self):
        src = ["a", "b"]
        out = uuid_fields(src)
        out["uuids"].append("c")
        assert src == ["a", "b"]  # input must not be mutated


class TestInsertFormattedContentWithUuids:
    def test_flat_top_level_blocks_appended_in_order(self):
        api = MagicMock()
        api.append_block_in_page.side_effect = [
            {"uuid": "u1"}, {"uuid": "u2"}
        ]
        uuids = insert_formatted_content_with_uuids(api, "Page", "- A\n- B")
        assert uuids == ["u1", "u2"]
        assert api.append_block_in_page.call_count == 2
        api.insert_block.assert_not_called()

    def test_children_nest_under_parent_dfs_preorder(self):
        api = MagicMock()
        api.append_block_in_page.return_value = {"uuid": "parent"}
        api.insert_block.return_value = {"uuid": "child"}
        uuids = insert_formatted_content_with_uuids(api, "Page", "- Parent\n\t- Child")
        # DFS pre-order: parent before child.
        assert uuids == ["parent", "child"]
        # Child inserted as non-sibling under the parent uuid.
        args = api.insert_block.call_args
        assert args.args[0] == "parent"
        assert args.args[2] == {"sibling": False}

    def test_string_result_also_yields_uuid(self):
        # API may return a bare uuid string instead of a dict.
        api = MagicMock()
        api.append_block_in_page.return_value = "bare-uuid"
        uuids = insert_formatted_content_with_uuids(api, "Page", "- Solo")
        assert uuids == ["bare-uuid"]
