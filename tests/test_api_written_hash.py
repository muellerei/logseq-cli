"""The writes whose proof reads the target block return that block's hash.

A caller that writes the same block twice can then pass the hash of the
first write's result as ``--expect-hash`` to the second, without reading in
between. The hash is the proof read's own: no extra request, and it is the
state the proof saw, so it cannot disagree with what was verified.
"""
import inspect

import pytest

from logseq_cli.api import LogseqAPI, WriteNotVerified
from logseq_cli.blocktext import block_hash
from tests.logseq_http_double import LogseqHttpDouble

PAGE = "Project Alpha"
TEXT = "TODO ship the parser"

# The public write behind each proof that reads the target block.
WRITES = {
    "_prove_text": lambda api, u: api.update_block(u, "TODO ship the parser, fixed"),
    "_prove_property": lambda api, u: api.upsert_block_property(u, "prio", "high"),
    "_prove_no_property": lambda api, u: api.remove_block_property(u, "prio"),
}


@pytest.fixture
def double(monkeypatch):
    return LogseqHttpDouble.installed(monkeypatch, {PAGE: [TEXT + "\nprio:: low"]})


@pytest.fixture
def api(double):
    api = LogseqAPI(token="t")
    api.cache_enabled = False
    return api


def stored(double, uuid):
    return next(n["content"] for p in double.pages for n in double._walk(p["blocks"])
                if n["uuid"] == uuid)


@pytest.mark.parametrize("proof", sorted(WRITES))
def test_write_returns_the_hash_of_the_block_as_stored(double, api, proof):
    uuid = double.uuid_of(TEXT + "\nprio:: low")
    got = WRITES[proof](api, uuid)
    assert got == block_hash(stored(double, uuid))


@pytest.mark.parametrize("proof", ["_prove_property", "_prove_no_property"])
def test_no_content_in_the_proof_read_returns_no_hash(double, api, monkeypatch, proof):
    """block_hash counts a missing content as "": without the explicit check
    the hash of the empty text would pass for the block's."""
    uuid = double.uuid_of(TEXT + "\nprio:: low")
    out = double._block_out

    def without_content(*args, **kwargs):
        block = out(*args, **kwargs)
        block.pop("content", None)
        return block
    monkeypatch.setattr(double, "_block_out", without_content)
    assert WRITES[proof](api, uuid) is None


def test_failed_proof_raises_and_returns_nothing(double, api):
    uuid = double.uuid_of(TEXT + "\nprio:: low")
    double.set_mode("updateBlock", "noop")
    with pytest.raises(WriteNotVerified):
        api.update_block(uuid, "TODO ship the parser, fixed")


def test_every_proof_that_reads_the_target_block_returns_a_hash():
    """The set is derived from the source, so a new proof that reads the
    target block shows up here until it has a write in WRITES above."""
    reading = {name for name, fn in inspect.getmembers(LogseqAPI, inspect.isfunction)
               if name.startswith("_prove_") and "_block_to_prove(" in inspect.getsource(fn)}
    assert reading == set(WRITES)
