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

    try:
        sock = socket.create_connection((host, port), 30)
    except OSError as error:
        raise IppError(f"cannot reach the printer at {host}:{port}: {error}") from error
    try:
        sock.settimeout(timeout)
        sock.sendall(header + payload)
        body = _receive(sock)
    except TimeoutError as error:
        # A timeout is not the end of the answer. Parsing what arrived so far would read
        # a cut-off reply as a complete one.
        raise IppError(f"printer did not finish answering within {timeout:.0f}s") from error
    except OSError as error:
        raise IppError(f"connection to the printer failed: {error}") from error
    finally:
        sock.close()
    return parse_body(body)


def _receive(sock: socket.socket) -> bytes:
    """Read one HTTP reply and return its IPP body.

    Reading stops as soon as the HTTP framing says the reply is complete, so a printer
    that keeps the connection open despite ``Connection: close`` does not hold us until
    the timeout.
    """
    raw = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return http_body(raw, is_closed=True)
        raw += chunk
        body = http_body(raw, is_closed=False)
        if body is not None:
            return body


def http_body(raw: bytes, *, is_closed: bool) -> bytes | None:
    """Return the IPP body of an HTTP reply, or ``None`` while more bytes are due.

    ``is_closed`` says the printer has closed the connection, so nothing more will
    arrive and an incomplete reply is an error rather than a reason to keep reading.
    Handles ``Transfer-Encoding: chunked``, ``Content-Length``, and a body that runs to
    the end of the connection, and skips interim ``1xx`` replies.
    """
    split = raw.find(b"\r\n\r\n")
    if split < 0:
        if is_closed:
            raise IppError("no HTTP header terminator in the printer's answer")
        return None
    lines = raw[:split].decode("latin-1").split("\r\n")
    rest = raw[split + 4 :]

    status_line = lines[0].split(" ", 2)
    if (len(status_line) < 2 or not status_line[0].startswith("HTTP/")
            or not status_line[1].isdigit()):
        raise IppError(f"the printer's answer is not HTTP: {lines[0]!r}")
    code = int(status_line[1])
    if 100 <= code < 200:
        return http_body(rest, is_closed=is_closed)
    if code != 200:
        raise IppError(f"printer answered HTTP {code}")

    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()

    if "chunked" in headers.get("transfer-encoding", "").lower():
        return _dechunk(rest, is_closed=is_closed)
    if "content-length" in headers:
        length_field = headers["content-length"]
        if not length_field.isdigit():
            raise IppError(f"bad Content-Length in the printer's answer: {length_field!r}")
        length = int(length_field)
        if len(rest) >= length:
            return rest[:length]
        if is_closed:
            raise IppError(f"printer closed the connection after {len(rest)} of "
                           f"{length} bytes")
        return None
    return rest if is_closed else None


def _dechunk(data: bytes, *, is_closed: bool) -> bytes | None:
    """Decode a chunked HTTP body, or return ``None`` while more bytes are due."""
    out = bytearray()
    position = 0
    while True:
        line_end = data.find(b"\r\n", position)
        if line_end < 0:
            break
        size_field = data[position:line_end].split(b";", 1)[0].strip()
        if not size_field or any(c not in b"0123456789abcdefABCDEF" for c in size_field):
            raise IppError(f"bad chunk size in the printer's answer: {size_field!r}")
        size = int(size_field, 16)
        start = line_end + 2
        if size == 0:
            # The last chunk. Optional trailer lines follow, then a blank line.
            if data.find(b"\r\n\r\n", line_end) >= 0:
                return bytes(out)
            break
        end = start + size
        if len(data) < end + 2:
            break
        if data[end : end + 2] != b"\r\n":
            raise IppError("a chunk in the printer's answer does not end with CRLF")
        out += data[start:end]
        position = end + 2
    if is_closed:
        raise IppError("printer closed the connection inside a chunked answer")
    return None


def parse(raw: bytes) -> tuple[int, dict]:
    """Pull the status code and the attribute list out of a complete HTTP reply."""
    return parse_body(http_body(raw, is_closed=True))


def _take(body: bytes, position: int, length: int) -> bytes:
    if position + length > len(body):
        raise IppError("IPP answer ends inside an attribute")
    return body[position : position + length]


def parse_body(body: bytes) -> tuple[int, dict]:
    """Pull the status code and the attribute list out of an IPP message body."""
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
        name_length = struct.unpack(">H", _take(body, position, 2))[0]
        position += 2
        name = _take(body, position, name_length).decode("utf8", "replace")
        position += name_length
        value_length = struct.unpack(">H", _take(body, position, 2))[0]
        position += 2
        value = _take(body, position, value_length)
        position += value_length
        if name_length:
            current = name
        elif current is None:
            raise IppError("IPP answer starts a value with no attribute name")
        out.setdefault(current, []).append(_value(tag, value))
    return status, out


def _value(tag: int, raw: bytes):
    if tag in (INTEGER, ENUM):
        if len(raw) != 4:
            raise IppError(f"IPP integer is {len(raw)} bytes long, not 4")
        return struct.unpack(">i", raw)[0]
    if tag == BOOLEAN:
        if len(raw) != 1:
            raise IppError(f"IPP boolean is {len(raw)} bytes long, not 1")
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


def is_blocking_reason(reason: str) -> bool:
    """Say whether a ``printer-state-reasons`` value means a job cannot finish."""
    if reason in ("none", ""):
        return False
    return not reason.endswith(("-report", "-warning"))


def wait_until_idle(host: str, *, user: str, timeout: float = 300.0,
                    interval: float = 5.0, sleep=time.sleep) -> tuple[int, list[str]]:
    """Block until the queue drains and the printer is idle.

    This is the confirmation step, because the job history is gone by then. Raises if
    the printer reports a reason that needs attention -- out of paper, a jam, an open
    cover -- so a job that never reached paper is not reported as printed. A reason
    that ends in ``-report`` or ``-warning``, such as ``toner-low-warning``, does not
    stop printing, so it does not block. Every other reason does, including one with no
    suffix, because RFC 8011 says a ``printer-state-reasons`` value with no suffix is an
    error.
    """
    deadline = time.monotonic() + timeout
    state, reasons = printer_state(host, user=user)
    while time.monotonic() < deadline:
        state, reasons = printer_state(host, user=user)
        blocking = [r for r in reasons if is_blocking_reason(r)]
        if blocking:
            raise IppError(f"printer needs attention: {blocking}")
        if state == PRINTER_STATE_IDLE and not pending_jobs(host, user=user):
            return state, reasons
        sleep(interval)
    raise IppError(f"printer did not go idle within {timeout:.0f}s (state {state})")
