"""get-properties --property must find a key however the user spells it.

The properties dict from the API carries camelCase keys for multi-word
properties (excludeFromGraphView), while datalog and habit spell them
kebab-cased. Plain .lower() matched neither, so every multi-word key
reported "not found" in both spellings.
"""
import json
from unittest.mock import MagicMock, patch

from logseq_cli.cli import cli
from tests.conftest import split_runner


def _api_with_page(properties, text_values=None):
    api = MagicMock()
    api.get_page.return_value = {
        "name": "contents", "originalName": "Contents",
        "properties": properties,
        "propertiesTextValues": text_values or {},
    }
    return api


def _invoke(api, key):
    with patch("logseq_cli.cli.LogseqAPI", return_value=api):
        return split_runner().invoke(
            cli, ["get-properties", "--name", "Contents", "--property", key, "--json"])


class TestPropertyLookupSpellings:
    def test_camelcase_key_finds_camelcase_stored(self):
        r = _invoke(_api_with_page({"excludeFromGraphView": True}), "excludeFromGraphView")
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["value"] is True

    def test_kebab_key_finds_camelcase_stored(self):
        r = _invoke(_api_with_page({"excludeFromGraphView": True}), "exclude-from-graph-view")
        assert r.exit_code == 0, r.output
        data = json.loads(r.stdout)
        assert data["value"] is True
        # The stored spelling is reported, not the typed one.
        assert data["property"] == "excludeFromGraphView"

    def test_camelcase_key_finds_kebab_stored(self):
        r = _invoke(_api_with_page({"exclude-from-graph-view": True}), "excludeFromGraphView")
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["value"] is True

    def test_single_word_key_unchanged(self):
        r = _invoke(_api_with_page({"type": "Person"}, {"type": "Person"}), "type")
        assert r.exit_code == 0, r.output
        assert json.loads(r.stdout)["value"] == "Person"

    def test_uppercase_typed_single_word_still_matches(self):
        r = _invoke(_api_with_page({"type": "Person"}), "Type")
        assert r.exit_code == 0, r.output

    def test_missing_key_still_fails(self):
        r = _invoke(_api_with_page({"type": "Person"}), "team")
        assert r.exit_code == 1
