"""No test reaches the Logseq running on the developer's machine.

conftest.py's autouse fixture ``block_real_hosts`` stands in for
``requests.post``: it answers ``checkEditing`` the way Logseq does when
nobody edits, and refuses every other request. These tests hold it to that.
"""
import pytest

import logseq_cli.api
from logseq_cli.api import LogseqAPI
from logseq_cli.commands import meta


def test_real_host_is_blocked(real_host_calls):
    with pytest.raises(RuntimeError, match="test reached a real host"):
        LogseqAPI().call("logseq.Editor.getPage", ["x"])
    assert [c["method"] for c in real_host_calls] == ["logseq.Editor.getPage"]
    # Deliberate: without clearing, this test would fail its own teardown.
    real_host_calls.clear()


def test_check_editing_answers_false_by_default(real_host_calls):
    resp = logseq_cli.api.requests.post(
        "http://127.0.0.1:12315/api",
        json={"method": "logseq.Editor.checkEditing", "args": []},
        timeout=30,
    )
    resp.raise_for_status()
    assert resp.status_code == 200
    assert resp.text == "false"
    assert resp.json() is False
    assert real_host_calls == []


def test_doctor_sees_no_listener_by_default():
    assert meta._port_has_listener("127.0.0.1", "12315") is False
