"""The IPP encoder and decoder, with no printer on the network.

Every test here builds the bytes a printer would send. Nothing opens a socket, so the
suite runs on a CI machine that has never seen a printer.
"""

from __future__ import annotations

import struct

import pytest

from hardcopy import ipp


def response(status: int, attributes: bytes = b"") -> bytes:
    body = (
        b"\x02\x00"
        + struct.pack(">H", status)
        + b"\x00\x00\x00\x01"
        + b"\x01"
        + attributes
        + b"\x03"
    )
    return b"HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\n\r\n" + body


def test_attribute_layout():
    out = ipp.attribute(ipp.KEYWORD, b"sides", b"one-sided")
    assert out[0] == ipp.KEYWORD
    assert struct.unpack(">H", out[1:3])[0] == len(b"sides")
    assert out[3:8] == b"sides"
    assert struct.unpack(">H", out[8:10])[0] == len(b"one-sided")
    assert out[10:] == b"one-sided"


def test_parse_reads_status_and_integers():
    attrs = ipp.attribute(ipp.INTEGER, b"job-id", struct.pack(">i", 283))
    status, answer = ipp.parse(response(0x0000, attrs))
    assert status == 0x0000
    assert answer["job-id"] == [283]


def test_parse_reads_strings_and_repeats_a_multi_value():
    attrs = ipp.attribute(ipp.KEYWORD, b"printer-state-reasons", b"media-empty")
    attrs += ipp.attribute(ipp.KEYWORD, b"", b"cover-open")  # continuation, empty name
    _, answer = ipp.parse(response(0x0000, attrs))
    assert answer["printer-state-reasons"] == ["media-empty", "cover-open"]


def test_parse_reads_a_boolean():
    attrs = ipp.attribute(ipp.BOOLEAN, b"printer-is-accepting-jobs", b"\x01")
    _, answer = ipp.parse(response(0x0000, attrs))
    assert answer["printer-is-accepting-jobs"] == [True]


def test_parse_rejects_a_truncated_answer():
    with pytest.raises(ipp.IppError):
        ipp.parse(b"HTTP/1.1 200 OK\r\n\r\n\x02\x00")


def test_parse_rejects_a_body_with_no_http_header():
    with pytest.raises(ipp.IppError):
        ipp.parse(b"\x02\x00\x00\x00")


def test_job_attributes_are_always_one_sided():
    """Multi-page duplex needs one raster document, which this module does not build."""
    assert b"one-sided" in ipp.job_attributes()
    assert b"two-sided" not in ipp.job_attributes()


def test_job_attributes_default_to_monochrome():
    assert b"monochrome" in ipp.job_attributes()
    assert b"color" in ipp.job_attributes(colour=True)


def test_validate_raises_on_a_rejection(monkeypatch):
    monkeypatch.setattr(ipp, "request", lambda *a, **k: (0x0400, {"unsupported-media": ["x"]}))
    with pytest.raises(ipp.IppError, match="0x0400"):
        ipp.validate("printer.invalid", user="tester")


def test_print_document_returns_the_job_id(monkeypatch):
    monkeypatch.setattr(ipp, "request", lambda *a, **k: (0x0000, {"job-id": [7]}))
    assert ipp.print_document("printer.invalid", b"jpeg", user="tester", job_name="j") == 7


def test_print_document_raises_when_refused(monkeypatch):
    monkeypatch.setattr(ipp, "request", lambda *a, **k: (0x0509, {}))
    with pytest.raises(ipp.IppError, match="0x0509"):
        ipp.print_document("printer.invalid", b"jpeg", user="tester", job_name="j")


def test_wait_until_idle_returns_when_the_queue_drains(monkeypatch):
    states = iter([(ipp.PRINTER_STATE_PROCESSING, ["none"]),
                   (ipp.PRINTER_STATE_PROCESSING, ["none"]),
                   (ipp.PRINTER_STATE_IDLE, ["none"])])
    monkeypatch.setattr(ipp, "printer_state", lambda *a, **k: next(states))
    monkeypatch.setattr(ipp, "pending_jobs", lambda *a, **k: [])
    state, _ = ipp.wait_until_idle("printer.invalid", user="tester", sleep=lambda _: None)
    assert state == ipp.PRINTER_STATE_IDLE


def test_wait_until_idle_does_not_pass_a_printer_needing_attention(monkeypatch):
    """Out of paper must not be reported as a finished print."""
    monkeypatch.setattr(ipp, "printer_state",
                        lambda *a, **k: (ipp.PRINTER_STATE_STOPPED, ["media-empty"]))
    monkeypatch.setattr(ipp, "pending_jobs", lambda *a, **k: [1])
    with pytest.raises(ipp.IppError, match="media-empty"):
        ipp.wait_until_idle("printer.invalid", user="tester", sleep=lambda _: None)


def test_wait_until_idle_does_not_pass_while_jobs_remain(monkeypatch):
    monkeypatch.setattr(ipp, "printer_state", lambda *a, **k: (ipp.PRINTER_STATE_IDLE, ["none"]))
    monkeypatch.setattr(ipp, "pending_jobs", lambda *a, **k: [1])
    with pytest.raises(ipp.IppError, match="did not go idle"):
        ipp.wait_until_idle("printer.invalid", user="tester", timeout=0.2, interval=0.01,
                            sleep=lambda _: None)


def test_no_printer_address_is_committed_in_this_repository():
    """The household printer belongs in the workspace, never in this repo."""
    import re
    from pathlib import Path

    octet = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    root = Path(__file__).resolve().parent.parent
    for path in list(root.glob("hardcopy/*.py")) + list(root.glob("tests/*.py")):
        found = [m for m in octet.findall(path.read_text()) if not m.startswith("0.")]
        assert not found, f"{path.name} carries what looks like an address: {found}"
