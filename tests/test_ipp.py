"""The IPP encoder and decoder, with no printer on the network.

Every test here builds the bytes a printer would send. Nothing opens a real socket -- a
fake one plays the bytes back -- so the suite runs on a CI machine that has never seen a
printer.
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


@pytest.mark.parametrize("reason", ["toner-low-warning", "media-low-report",
                                    "marker-supply-low-warning"])
def test_wait_until_idle_passes_a_report_or_a_warning(monkeypatch, reason):
    """Low toner does not stop a print, so it must not turn a finished print into an error."""
    monkeypatch.setattr(ipp, "printer_state",
                        lambda *a, **k: (ipp.PRINTER_STATE_IDLE, [reason]))
    monkeypatch.setattr(ipp, "pending_jobs", lambda *a, **k: [])
    state, reasons = ipp.wait_until_idle("printer.invalid", user="tester",
                                         sleep=lambda _: None)
    assert state == ipp.PRINTER_STATE_IDLE
    assert reasons == [reason]


@pytest.mark.parametrize("reason", ["toner-empty-error", "media-jam", "door-open"])
def test_wait_until_idle_blocks_on_an_error_or_an_unsuffixed_reason(monkeypatch, reason):
    """RFC 8011 says a reason with no suffix is an error, so it blocks like ``-error``."""
    monkeypatch.setattr(ipp, "printer_state",
                        lambda *a, **k: (ipp.PRINTER_STATE_STOPPED,
                                         ["toner-low-warning", reason]))
    monkeypatch.setattr(ipp, "pending_jobs", lambda *a, **k: [1])
    with pytest.raises(ipp.IppError, match=reason) as caught:
        ipp.wait_until_idle("printer.invalid", user="tester", sleep=lambda _: None)
    assert "toner-low-warning" not in str(caught.value)


def ipp_body(status: int = 0x0000, attributes: bytes = b"") -> bytes:
    return response(status, attributes).split(b"\r\n\r\n", 1)[1]


def chunked(body: bytes, size: int = 5) -> bytes:
    out = b""
    for start in range(0, len(body), size):
        piece = body[start : start + size]
        out += f"{len(piece):x}".encode() + b";ext=1\r\n" + piece + b"\r\n"
    return out + b"0\r\nX-Trailer: yes\r\n\r\n"


JOB_ID = ipp.attribute(ipp.INTEGER, b"job-id", struct.pack(">i", 283))
CHUNKED_HEADER = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"


def test_parse_decodes_a_chunked_answer():
    status, answer = ipp.parse(CHUNKED_HEADER + chunked(ipp_body(0x0000, JOB_ID)))
    assert status == 0x0000
    assert answer["job-id"] == [283]


def test_parse_rejects_a_chunked_answer_cut_off_before_the_last_chunk():
    whole = chunked(ipp_body(0x0000, JOB_ID))
    with pytest.raises(ipp.IppError, match="chunked"):
        ipp.parse(CHUNKED_HEADER + whole[: len(whole) // 2])


def test_parse_rejects_a_bad_chunk_size():
    with pytest.raises(ipp.IppError, match="chunk size"):
        ipp.parse(CHUNKED_HEADER + b"zz\r\nabc\r\n0\r\n\r\n")


def test_parse_reads_only_content_length_bytes():
    body = ipp_body(0x0000, JOB_ID)
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body + b"junk"
    assert ipp.parse(raw)[1]["job-id"] == [283]


def test_parse_rejects_an_answer_shorter_than_its_content_length():
    body = ipp_body(0x0000, JOB_ID)
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % (len(body) + 10) + body
    with pytest.raises(ipp.IppError, match="closed the connection"):
        ipp.parse(raw)


def test_parse_skips_an_interim_continue():
    raw = b"HTTP/1.1 100 Continue\r\n\r\n" + response(0x0000, JOB_ID)
    assert ipp.parse(raw)[1]["job-id"] == [283]


def test_parse_rejects_an_http_error():
    with pytest.raises(ipp.IppError, match="HTTP 401"):
        ipp.parse(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")


@pytest.mark.parametrize("cut", [1, 2, 3, 6, 10, len(JOB_ID) - 1])
def test_parse_raises_ipp_error_not_struct_error_on_a_cut_attribute(cut):
    """A reply cut inside an attribute must surface as IppError, which the CLI reports."""
    head = b"\x02\x00\x00\x00\x00\x00\x00\x01\x01"
    with pytest.raises(ipp.IppError):
        ipp.parse_body(head + JOB_ID[:cut])


def test_parse_rejects_an_integer_of_the_wrong_length():
    attrs = ipp.attribute(ipp.INTEGER, b"job-id", b"\x01\x02")
    with pytest.raises(ipp.IppError, match="integer"):
        ipp.parse(response(0x0000, attrs))


def test_parse_rejects_an_empty_boolean():
    attrs = ipp.attribute(ipp.BOOLEAN, b"printer-is-accepting-jobs", b"")
    with pytest.raises(ipp.IppError, match="boolean"):
        ipp.parse(response(0x0000, attrs))


class FakeSocket:
    """Plays back a list of ``recv`` results. An exception in the list is raised."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = b""
        self.is_closed = False

    def settimeout(self, _):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, _):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def close(self):
        self.is_closed = True


def connect_to(monkeypatch, replies) -> FakeSocket:
    fake = FakeSocket(replies)
    monkeypatch.setattr(ipp.socket, "create_connection", lambda *a, **k: fake)
    return fake


def test_request_treats_a_timeout_as_an_error_not_as_the_end_of_the_answer(monkeypatch):
    whole = response(0x0000, JOB_ID)
    fake = connect_to(monkeypatch, [whole[:-4], TimeoutError("timed out")])
    with pytest.raises(ipp.IppError, match="did not finish answering"):
        ipp.request("printer.invalid", ipp.OP_GET_JOBS)
    assert fake.is_closed


def test_request_decodes_a_chunked_answer_split_across_reads(monkeypatch):
    raw = CHUNKED_HEADER + chunked(ipp_body(0x0000, JOB_ID), size=3)
    reads = [raw[i : i + 7] for i in range(0, len(raw), 7)]
    # No end-of-stream: the request must stop at the last chunk on its own.
    connect_to(monkeypatch, reads + [AssertionError("read past the end of the answer")])
    assert ipp.request("printer.invalid", ipp.OP_GET_JOBS) == (0x0000, {"job-id": [283]})


def test_request_stops_at_content_length_when_the_printer_keeps_the_connection(monkeypatch):
    body = ipp_body(0x0000, JOB_ID)
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body
    connect_to(monkeypatch, [raw[:20], raw[20:],
                             AssertionError("read past the end of the answer")])
    assert ipp.request("printer.invalid", ipp.OP_GET_JOBS)[1] == {"job-id": [283]}


def test_request_reads_a_close_delimited_answer(monkeypatch):
    raw = response(0x0000, JOB_ID)
    connect_to(monkeypatch, [raw[:15], raw[15:], b""])
    assert ipp.request("printer.invalid", ipp.OP_GET_JOBS)[1] == {"job-id": [283]}


@pytest.mark.parametrize("raw", [
    "HTTP/1.1 200 OK\r\nContent-Length: \u00b2\r\n\r\n".encode("latin-1"),
    b"HTTP/1.1 200 OK\r\nContent-Length: " + b"9" * 4301 + b"\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: -5\r\n\r\n",
    "HTTP/1.1 2\u00b20 OK\r\n\r\n".encode("latin-1"),
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"f" * 40 + b"\r\n",
])
def test_request_reports_a_malformed_number_as_ipp_error(monkeypatch, raw):
    """``str.isdigit`` passes a superscript two, and ``int`` rejects it with ValueError."""
    fake = connect_to(monkeypatch, [raw, b""])
    with pytest.raises(ipp.IppError):
        ipp.request("printer.invalid", ipp.OP_GET_JOBS)
    assert fake.is_closed


def test_request_survives_many_interim_replies(monkeypatch):
    """Each interim reply used to cost a stack frame, so enough of them crashed."""
    raw = b"HTTP/1.1 100 Continue\r\n\r\n" * 1100 + response(0x0000, JOB_ID)
    connect_to(monkeypatch, [raw, b""])
    assert ipp.request("printer.invalid", ipp.OP_GET_JOBS)[1] == {"job-id": [283]}


def test_parse_rejects_an_answer_with_no_end_of_attributes_tag():
    """A reply cut at an attribute boundary must not read as a complete, healthy one."""
    state = ipp.attribute(ipp.ENUM, b"printer-state", struct.pack(">i", ipp.PRINTER_STATE_IDLE))
    whole = response(0x0000, state)
    with pytest.raises(ipp.IppError, match="end-of-attributes"):
        ipp.parse(whole[:-1])
    with pytest.raises(ipp.IppError, match="end-of-attributes"):
        ipp.parse_body(ipp_body()[:8])


def test_request_cut_short_cannot_confirm_a_print(monkeypatch):
    state = ipp.attribute(ipp.ENUM, b"printer-state", struct.pack(">i", ipp.PRINTER_STATE_IDLE))
    connect_to(monkeypatch, [response(0x0000, state)[:-1], b""])
    with pytest.raises(ipp.IppError, match="end-of-attributes"):
        ipp.printer_state("printer.invalid", user="tester")


def test_request_has_a_deadline_for_the_whole_answer(monkeypatch):
    """A printer that trickles interim replies forever must not hold the request open."""
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(ipp.time, "monotonic", lambda: next(clock))
    fake = connect_to(monkeypatch, [b"HTTP/1.1 100 Continue\r\n\r\n"] * 1000)
    with pytest.raises(ipp.IppError, match="did not finish answering"):
        ipp.request("printer.invalid", ipp.OP_GET_JOBS, timeout=60)
    assert fake.is_closed
    assert len(fake.replies) > 900


def test_request_starts_the_reply_deadline_after_the_upload(monkeypatch):
    """A slow upload must not use up the time the printer has to answer."""
    now = [0.0]
    monkeypatch.setattr(ipp.time, "monotonic", lambda: now[0])
    raw = response(0x0000, JOB_ID)

    class SlowUpload(FakeSocket):
        def sendall(self, data):
            now[0] += 179.0
            super().sendall(data)

        def recv(self, size):
            now[0] += 1.0
            return super().recv(size)

    fake = SlowUpload([raw[:10], raw[10:20], raw[20:], b""])
    monkeypatch.setattr(ipp.socket, "create_connection", lambda *a, **k: fake)
    assert ipp.request("printer.invalid", ipp.OP_PRINT_JOB, timeout=180)[1] == {"job-id": [283]}


def test_request_refuses_an_endless_answer(monkeypatch):
    fake = connect_to(monkeypatch, [b"HTTP/1.1 200 OK\r\n\r\n"] + [b"x" * 65536] * 300)
    with pytest.raises(ipp.IppError, match="larger than"):
        ipp.request("printer.invalid", ipp.OP_GET_JOBS)
    assert fake.is_closed


def test_request_reports_an_unreachable_printer_as_ipp_error(monkeypatch):
    def refuse(*_a, **_k):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(ipp.socket, "create_connection", refuse)
    with pytest.raises(ipp.IppError, match="cannot reach"):
        ipp.request("printer.invalid", ipp.OP_GET_JOBS)


def test_no_printer_address_is_committed_in_this_repository():
    """The household printer belongs in the workspace, never in this repo."""
    import re
    from pathlib import Path

    octet = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    root = Path(__file__).resolve().parent.parent
    for path in list(root.glob("hardcopy/*.py")) + list(root.glob("tests/*.py")):
        found = [m for m in octet.findall(path.read_text()) if not m.startswith("0.")]
        assert not found, f"{path.name} carries what looks like an address: {found}"
