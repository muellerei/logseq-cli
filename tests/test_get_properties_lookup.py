"""get-properties --property must find a key however the user spells it.

Keys are stored kebab-cased (exclude-from-graph-view), while the API and
Logseq's UI show them camel-cased (excludeFromGraphView), so users type
either. Plain .lower() matched neither form against the other, and every
multi-word key reported "not found". The camel-cased stored keys below are
not what a re-indexed graph holds; they stand for a key written verbatim
by an older client and not yet re-read, which the lookup must find as well.
"""
import json
from unittest.mock import MagicMock, patch

from logseq_cli.cli import cli
from tests.conftest import answer_property_pulls, split_runner


def _api_with_page(properties, text_values=None):
    api = answer_property_pulls(MagicMock())
    api.get_page.return_value = {
        "uuid": "00000000-0000-4000-8000-00000000c0e1",
        "name": "contents", "originalName": "Contents",
        "properties": properties,
        "propertiesTextValues": text_values or {},
    }
    return api


def _invoke(api, key):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
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
