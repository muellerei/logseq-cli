"""Property keys are read as the database stores them, never from the API's map.

The plugin API camel-cases property keys on the way out (``getBlock``,
``getPage``, ``getPageBlocksTree`` all go through ``normalize-keyword-for-json``).
``due-date`` arrives as ``dueDate``, and ``created_at`` too arrives as
``createdAt``, so the original spelling cannot be recovered from it. Every
command that used that map inherited the distortion:

- ``update-block`` carried the properties through its write under those keys,
  and Logseq wrote them back lower-cased: ``due-date::`` became ``duedate::``.
  Values were rewritten as well (``01234`` became ``1234``, ``beta`` became
  ``[[beta]]``), because the parsed values went back instead of the text.
- the ``--dry-run`` existence checks compared a kebab key against camel keys
  and reported "(not set)" for a key that was set.
- ``get-properties`` listed keys no file contains.

A datascript pull returns both the stored keys and the original text. Measured
against Logseq 0.10.15: carrying the text values through ``updateBlock`` left
``due-date``, ``01234`` and ``beta`` exactly as written.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from logseq_cli.cli import cli
from logseq_cli.helpers import stored_properties
from tests.conftest import split_runner

BLOCK = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a70"
PAGE = "7e1f2a3b-4c5d-4e6f-8a9b-0c1d2e3f4a71"

# What the database holds for the block, spelled as a pull returns it.
STORED = {"due-date": "2026-10-01", "created-at": "x", "zip": 1234,
          "tags": ["Alpha", "beta"]}
TEXTS = {"due-date": "2026-10-01", "created-at": "x", "zip": "01234",
         "tags": "[[Alpha]], beta"}
# What the plugin API reports for the same block.
CAMEL = {"dueDate": "2026-10-01", "createdAt": "x", "zip": 1234,
         "tags": ["Alpha", "beta"]}


def _api():
    api = MagicMock()
    block = {"uuid": BLOCK, "content": "block one\ndue-date:: 2026-10-01",
             "properties": CAMEL, "propertiesTextValues": dict(CAMEL)}
    api.get_block.return_value = block
    api.get_page_blocks_tree.return_value = [block]
    api.get_page.return_value = {"uuid": PAGE, "name": "page a",
                                 "originalName": "Page A", "properties": {}}

    def pull(query):
        if BLOCK in query:
            return [[{"properties": STORED, "properties-text-values": TEXTS}]]
        return [[None]]
    api.datascript_query.side_effect = pull
    return api


def _run(args, api):
    with patch("logseq_cli.group.LogseqAPI", return_value=api):
        return split_runner().invoke(cli, args)


class TestStoredProperties:
    def test_returns_stored_keys_and_original_text(self):
        assert stored_properties(_api(), BLOCK) == (STORED, TEXTS)

    def test_entity_without_properties_gives_empty_maps(self):
        assert stored_properties(_api(), PAGE) == ({}, {})

    def test_uuid_is_validated_before_it_reaches_the_query(self):
        api = _api()
        with pytest.raises(ValueError):
            stored_properties(api, 'x"] [?b :block/uuid')
        api.datascript_query.assert_not_called()


class TestUpdateBlockKeepsStoredProperties:
    def test_carries_the_original_text_under_the_stored_keys(self):
        api = _api()
        r = _run(["update-block", "--id", BLOCK, "--content", "edited"], api)
        assert r.exit_code == 0, r.stderr
        api.update_block.assert_called_once_with(BLOCK, "edited", properties=TEXTS)

    def test_json_reports_only_what_was_carried_through(self):
        # A markdown heading ("## Title") is in :block/properties as heading 2
        # but has no text value: it comes from the "##", not from a line. It is
        # not written back, so the output must not list it as kept.
        api = _api()
        api.datascript_query.side_effect = lambda q: [[{
            "properties": {**STORED, "heading": 2},
            "properties-text-values": TEXTS}]]
        r = _run(["update-block", "--id", BLOCK, "--content", "edited", "--json"], api)
        assert json.loads(r.stdout)["properties"] == STORED
        api.update_block.assert_called_once_with(BLOCK, "edited", properties=TEXTS)

    def test_dry_run_names_the_stored_keys(self):
        r = _run(["update-block", "--id", BLOCK, "--content", "edited", "--dry-run"], _api())
        assert "due-date::" in r.stdout and "dueDate" not in r.stdout


def _page_api():
    """The page's first block as its property block: set-property and
    remove-property --name read and write there (#80)."""
    api = _api()
    api.get_page_blocks_tree.return_value = [{
        **api.get_block.return_value, "preBlock?": True,
        "content": "due-date:: 2026-10-01\ncreated-at:: x\nzip:: 01234\ntags:: [[Alpha]], beta"}]
    return api


class TestDryRunSeesAKeyThatIsSet:
    def test_set_property(self):
        r = _run(["set-property", "--name", "Page A", "--key", "due-date",
                  "--value", "2026-12-01", "--dry-run", "--json"], _page_api())
        payload = json.loads(r.stdout)
        assert payload["existed"] is True
        assert payload["old_value"] == "2026-10-01"

    def test_set_block_property(self):
        r = _run(["set-block-property", "--id", BLOCK, "--key", "due-date",
                  "--value", "2026-12-01", "--dry-run", "--json"], _api())
        payload = json.loads(r.stdout)
        assert payload["existed"] is True

    @pytest.mark.parametrize("target", [["--name", "Page A"], ["--id", BLOCK]])
    def test_remove_property(self, target):
        r = _run(["remove-property", *target, "--key", "created-at",
                  "--dry-run", "--json"], _page_api())
        payload = json.loads(r.stdout)
        assert payload["present"] is True
        assert payload["value"] == "x"


def _page_level_api():
    """Page A carrying the properties itself, as its property block makes it."""
    api = _api()
    api.get_page.return_value = {"uuid": PAGE, "name": "page a",
                                 "originalName": "Page A", "properties": CAMEL}
    api.datascript_query.side_effect = lambda query: (
        [[{"properties": STORED, "properties-text-values": TEXTS}]] if PAGE in query else [[None]])
    return api


class TestGetPropertiesListsStoredKeys:
    def test_listing_uses_stored_keys(self):
        r = _run(["get-properties", "--name", "Page A", "--json"], _page_level_api())
        payload = json.loads(r.stdout)
        assert payload["properties"] == STORED
        assert payload["text_values"] == TEXTS

    def test_the_first_block_fallback_uses_stored_keys_too(self):
        """Without the block's own created-at, a key Logseq keeps for the
        block itself (#82)."""
        r = _run(["get-properties", "--name", "Page A", "--json"], _api())
        payload = json.loads(r.stdout)
        assert payload["properties"] == {k: v for k, v in STORED.items() if k != "created-at"}

    def test_page_level_properties_need_no_block_read(self):
        # the page object itself carries camel-cased keys when the page has them
        api = _page_level_api()
        r = _run(["get-properties", "--name", "Page A", "--json"], api)
        assert json.loads(r.stdout)["properties"] == STORED
        api.get_page_blocks_tree.assert_not_called()

    def test_plain_listing_shows_the_original_text(self):
        r = _run(["get-properties", "--name", "Page A"], _api())
        assert "due-date:: 2026-10-01" in r.stdout
        assert "zip:: 01234" in r.stdout

    def test_single_property_by_stored_key(self):
        r = _run(["get-properties", "--name", "Page A", "--property", "created-at",
                  "--json"], _page_level_api())
        payload = json.loads(r.stdout)
        assert payload["property"] == "created-at"
        assert payload["text"] == "x"
