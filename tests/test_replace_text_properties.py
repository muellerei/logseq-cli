"""replace-text must not touch property lines inside a block's content.

A block's content carries its property lines verbatim (``id:: <uuid>`` and any
key:: value), sometimes with text after them. replace-text ran the pattern over
the whole content and wrote it back with a two-arg update_block, so a --find
that matched inside a property line rewrote it: an id:: hit breaks every
((block-ref)) pointing at that block, irreversibly. Property lines are left
untouched; only the visible text is replaced.
"""
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from logseq_cli.cli import cli


def _api(content, after=None):
    api = MagicMock()
    api.get_page_blocks_tree.return_value = [
        {"uuid": "b1", "content": content, "children": []}]
    # Echo whatever gets written, so the read-back verification passes when the
    # write is correct.
    written = {}

    def _update(uuid, new_content, properties=None, replacing=None):
        written["content"] = new_content
        return None

    def _get_block(uuid, include_children=False):
        return {"uuid": "b1", "content": written.get("content", content)}

    api.update_block.side_effect = _update
    api.get_block.side_effect = _get_block
    api._written = written
    return api


class TestReplaceTextSparesProperties:
    def test_id_line_is_not_touched_when_find_matches_the_uuid(self):
        # --find hits a hex fragment that occurs in the id:: uuid.
        uuid = "abcdef12-3456-7890-abcd-ef1234567890"
        content = f"DONE Service updaten 6e10 fixen\nid:: {uuid}"
        api = _api(content)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "6e10", "--replace", "XXXX"])
        assert r.exit_code == 0, r.output
        written = api._written["content"]
        # The id line must survive verbatim; the visible text is replaced.
        assert f"id:: {uuid}" in written
        assert "Service updaten XXXX fixen" in written

    def test_property_line_survives_even_with_text_after_it(self):
        content = ("DONE neuer Service anlegen\n"
                   "id:: abcdef12-3456-7890-abcd-ef1234567890\n"
                   "=> Service via alt route")
        api = _api(content)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "Service", "--replace", "Host"])
        assert r.exit_code == 0, r.output
        written = api._written["content"]
        assert "id:: abcdef12-3456-7890-abcd-ef1234567890" in written
        # Both text lines are replaced, the property line is not.
        assert "DONE neuer Host anlegen" in written
        assert "=> Host via alt route" in written

    def test_soft_property_line_is_not_replaced(self):
        content = "TODO Task A\nprio:: A\ncollapsed:: true"
        api = _api(content)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "A", "--replace", "Z"])
        assert r.exit_code == 0, r.output
        written = api._written["content"]
        assert "prio:: A" in written  # not "prio:: Z"
        assert "TODO Task Z" in written  # the text line IS replaced

    def test_plain_text_block_still_replaced(self):
        content = "old here"
        api = _api(content)
        with patch("logseq_cli.group.LogseqAPI", return_value=api):
            r = CliRunner().invoke(cli, [
                "replace-text", "--page", "P", "--find", "old", "--replace", "new"])
        assert r.exit_code == 0, r.output
        assert api._written["content"] == "new here"

