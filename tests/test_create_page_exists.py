"""Tests for create-page refusing to write onto a page that already exists.

The defect this guards: ``create-page`` called Logseq's createPage without
asking whether the page was there. Logseq answers with the existing page, so a
second call reported ``created`` and exit 0 while creating nothing — and
``--content`` was appended to the page that was already there. An agent
retrying after a timeout duplicated content and was told the write succeeded.

The preview added alongside reports the same state the check reads, which is
why both live in one test module.
"""
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from logseq_cli.cli import cli
from tests.conftest import split_runner


@pytest.fixture
def api():
    mock = MagicMock()
    mock.create_page.return_value = {"id": 1, "name": "new page"}
    mock.append_block_in_page.return_value = {"uuid": "u1"}
    with patch("logseq_cli.group.LogseqAPI", return_value=mock):
        yield mock


class TestExistingPageIsRefused:
    def test_existing_page_fails(self, api):
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = CliRunner().invoke(cli, ["create-page", "--page", "Example"])
        assert r.exit_code != 0, r.output
        api.create_page.assert_not_called()

    def test_existing_page_does_not_append_content(self, api):
        """The duplication path: content must not land on the existing page."""
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Example", "--content", "second"])
        assert r.exit_code != 0, r.output
        api.append_block_in_page.assert_not_called()

    def test_error_is_json_when_asked(self, api):
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = split_runner().invoke(
            cli, ["create-page", "--page", "Example", "--json"])
        assert r.exit_code != 0
        payload = json.loads(r.stderr)
        assert "error" in payload
        assert payload.get("page") == "Example"

    def test_absent_page_is_created(self, api):
        """An empty result from getPage means absent, and must still work."""
        api.get_page.return_value = None
        r = CliRunner().invoke(cli, ["create-page", "--page", "Fresh"])
        assert r.exit_code == 0, r.output
        api.create_page.assert_called_once()


class TestDryRun:
    def test_dry_run_writes_nothing(self, api):
        api.get_page.return_value = None
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Fresh", "--content", "x", "--dry-run"])
        assert r.exit_code == 0, r.output
        api.create_page.assert_not_called()
        api.append_block_in_page.assert_not_called()

    def test_dry_run_reports_the_existing_page(self, api):
        """The preview must name the state that would make the real run fail."""
        api.get_page.return_value = {"id": 42, "name": "example"}
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Example", "--dry-run", "--json"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["exists"] is True
        assert payload["would_create"] is False
        api.create_page.assert_not_called()

    def test_dry_run_reports_a_new_page(self, api):
        api.get_page.return_value = None
        r = CliRunner().invoke(
            cli, ["create-page", "--page", "Fresh", "--content", "x",
                  "--dry-run", "--json"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert payload["exists"] is False
        assert payload["would_create"] is True
        assert payload["has_content"] is True
