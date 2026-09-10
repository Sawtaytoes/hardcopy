"""A very small IPP client: enough to validate a job, send one, and read the queue.

There is no printer address in this module and there must never be one. The host is
always supplied by the caller.

Two things about real printers shape this code:

* Many accept only ONE document per job. A second ``Send-Document`` returns 0x0509
  and aborts the whole job, so N pages goes out as N separate ``Print-Job`` calls and
  the result is single-sided. Duplex needs every page inside one multi-page raster,
  which this module does not build.
* Job history can clear within seconds of a job finishing. Do not confirm a print by
  looking for the job afterwards; watch for the queue to drain and the printer to
  return to idle instead, which is what ``wait_until_idle`` does.
"""

from __future__ import annotations

import socket
import struct
import time

CHARSET = 0x47
LANGUAGE = 0x48
NAME = 0x42
KEYWORD = 0x44
URI = 0x45
INTEGER = 0x21
ENUM = 0x23
BOOLEAN = 0x22
MIME = 0x49

OP_PRINT_JOB = 0x0002
OP_VALIDATE_JOB = 0x0004
OP_GET_JOBS = 0x000A
OP_GET_PRINTER_ATTRIBUTES = 0x000B

PRINTER_STATE_IDLE = 3
PRINTER_STATE_PROCESSING = 4
PRINTER_STATE_STOPPED = 5

# An IPP status code below 0x0100 is a success class.
STATUS_OK_MAX = 0x0100


class IppError(RuntimeError):
    """The printer rejected a request, or answered with something unparseable."""


def attribute(tag: int, name: bytes, value: bytes) -> bytes:
    return (
        struct.pack(">B", tag)
        + struct.pack(">H", len(name))
        + name
        + struct.pack(">H", len(value))
        + value
    )


def _body(host: str, resource: str, operation: int, operation_attributes: bytes,
          job_attributes: bytes, document: bytes) -> bytes:
    out = b"\x02\x00" + struct.pack(">H", operation) + b"\x00\x00\x00\x01" + b"\x01"
    out += attribute(CHARSET, b"attributes-charset", b"utf-8")
    out += attribute(LANGUAGE, b"attributes-natural-language", b"en-us")
    out += attribute(URI, b"printer-uri", f"ipp://{host}{resource}".encode())
    out += operation_attributes
    if job_attributes:
        out += b"\x02" + job_attributes
    return out + b"\x03" + document


def request(host: str, operation: int, *, port: int = 631, resource: str = "/ipp/print",
            operation_attributes: bytes = b"", job_attributes: bytes = b"",
            document: bytes = b"", timeout: float = 180.0) -> tuple[int, dict]:
    """Send one IPP operation. Returns ``(status, attributes)``."""
    payload = _body(host, resource, operation, operation_attributes, job_attributes, document)
    header = (
        f"POST {resource} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        f"Content-Type: application/ipp\r\nContent-Length: {len(payload)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()

    sock = socket.create_connection((host, port), 30)
    try:
        sock.settimeout(timeout)
        sock.sendall(header + payload)
        chunks = []
        while True:
            try:
                chunk = sock.recv(65536)
            except TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        sock.close()
    return parse(b"".join(chunks))


def parse(raw: bytes) -> tuple[int, dict]:
    """Pull the status code and the attribute list out of an IPP response."""
    split = raw.find(b"\r\n\r\n")
    if split < 0:
        raise IppError("no HTTP header terminator in the printer's answer")
    body = raw[split + 4 :]
    if len(body) < 8:
        raise IppError("IPP answer is too short to hold a status code")

    status = struct.unpack(">H", body[2:4])[0]
    position, current, out = 8, None, {}
    while position < len(body):
        tag = body[position]
        position += 1
        if tag in (1, 2, 3, 4, 5):
            if tag == 3:  # end-of-attributes
                break
            continue
        name_length = struct.unpack(">H", body[position : position + 2])[0]
        position += 2
        name = body[position : position + name_length].decode("utf8", "replace")
        position += name_length
        value_length = struct.unpack(">H", body[position : position + 2])[0]
        position += 2
        value = body[position : position + value_length]
        position += value_length
        if name_length:
            current = name
        out.setdefault(current, []).append(_value(tag, value))
    return status, out


def _value(tag: int, raw: bytes):
    if tag in (INTEGER, ENUM):
        return struct.unpack(">i", raw)[0]
    if tag == BOOLEAN:
        return raw[0] == 1
    return raw.decode("utf8", "replace")


def job_attributes(*, media: str = "na_letter_8.5x11in", colour: bool = False,
                   scaling: str = "fit", copies: int = 1) -> bytes:
    return (
        attribute(KEYWORD, b"sides", b"one-sided")
        + attribute(KEYWORD, b"print-color-mode", b"color" if colour else b"monochrome")
        + attribute(KEYWORD, b"media", media.encode())
        + attribute(KEYWORD, b"print-scaling", scaling.encode())
        + attribute(INTEGER, b"copies", struct.pack(">i", copies))
    )


def _operation_attributes(user: str, job_name: str, document_format: str) -> bytes:
    return (
        attribute(NAME, b"requesting-user-name", user.encode())
        + attribute(NAME, b"job-name", job_name.encode())
        + attribute(MIME, b"document-format", document_format.encode())
    )


def validate(host: str, *, user: str, job_name: str = "hardcopy",
             document_format: str = "image/jpeg", **kwargs) -> dict:
    """Ask the printer whether it would accept the job. Raises on rejection."""
    status, answer = request(
        host,
        OP_VALIDATE_JOB,
        operation_attributes=_operation_attributes(user, job_name, document_format),
        job_attributes=job_attributes(**kwargs),
    )
    if status >= STATUS_OK_MAX:
        unsupported = {k: v for k, v in answer.items() if "unsupported" in k}
        raise IppError(f"printer rejected the job (0x{status:04x}) {unsupported}")
    return answer


def print_document(host: str, document: bytes, *, user: str, job_name: str,
                   document_format: str = "image/jpeg", **kwargs) -> int:
    """Send one document as one job. Returns the job id."""
    status, answer = request(
        host,
        OP_PRINT_JOB,
        operation_attributes=_operation_attributes(user, job_name, document_format),
        job_attributes=job_attributes(**kwargs),
        document=document,
    )
    if status >= STATUS_OK_MAX:
        raise IppError(f"printer refused the document (0x{status:04x}) {answer}")
    return answer["job-id"][0]


def pending_jobs(host: str, *, user: str) -> list[int]:
    _, answer = request(
        host,
        OP_GET_JOBS,
        operation_attributes=(
            attribute(NAME, b"requesting-user-name", user.encode())
            + attribute(KEYWORD, b"which-jobs", b"not-completed")
        ),
    )
    return list(answer.get("job-id", []))


def printer_state(host: str, *, user: str) -> tuple[int, list[str]]:
    _, answer = request(
        host,
        OP_GET_PRINTER_ATTRIBUTES,
        operation_attributes=attribute(NAME, b"requesting-user-name", user.encode()),
    )
    return answer.get("printer-state", [0])[0], answer.get("printer-state-reasons", [])


def wait_until_idle(host: str, *, user: str, timeout: float = 300.0,
                    interval: float = 5.0, sleep=time.sleep) -> tuple[int, list[str]]:
    """Block until the queue drains and the printer is idle.

    This is the confirmation step, because the job history is gone by then. Raises if
    the printer reports any state reason other than ``none`` -- out of paper, a jam,
    an open cover -- so a job that never reached paper is not reported as printed.
    """
    deadline = time.monotonic() + timeout
    state, reasons = printer_state(host, user=user)
    while time.monotonic() < deadline:
        state, reasons = printer_state(host, user=user)
        blocking = [r for r in reasons if r not in ("none", "")]
        if blocking:
            raise IppError(f"printer needs attention: {blocking}")
        if state == PRINTER_STATE_IDLE and not pending_jobs(host, user=user):
            return state, reasons
        sleep(interval)
    raise IppError(f"printer did not go idle within {timeout:.0f}s (state {state})")
