"""Command line: prepare a photographed page, write it out, or send it to a printer."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import cv2
import numpy as np

from hardcopy import ipp
from hardcopy.prepare import PAGE_SIZES, prepare_page


def _add_page_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("photos", nargs="+", type=Path, help="page photos, in page order")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--page", default="letter", choices=sorted(PAGE_SIZES))
    parser.add_argument("--margin", type=float, default=0.3, help="page margin in inches")


def _prepared(args) -> list[tuple[Path, np.ndarray]]:
    out = []
    for photo in args.photos:
        image = cv2.imread(str(photo))
        if image is None:
            sys.exit(f"error: cannot read {photo}")
        page = prepare_page(image, dpi=args.dpi, page=args.page, margin_inches=args.margin)
        print(f"{photo.name}: {image.shape[1]}x{image.shape[0]} -> "
              f"{page.shape[1]}x{page.shape[0]} at {args.dpi} dpi")
        out.append((photo, page))
    return out


def _encode(page: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("could not encode the prepared page as JPEG")
    return buffer.tobytes()


def cmd_prepare(args) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for photo, page in _prepared(args):
        target = args.out_dir / f"{photo.stem}.jpg"
        cv2.imwrite(str(target), page, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(f"   wrote {target}")
    return 0


def cmd_pdf(args) -> int:
    from PIL import Image

    pages = [Image.fromarray(page) for _, page in _prepared(args)]
    pages[0].save(args.out, "PDF", resolution=float(args.dpi), save_all=True,
                  append_images=pages[1:], title=args.title or args.out.stem)
    print(f"   wrote {args.out} ({len(pages)} page(s))")
    return 0


def cmd_print(args) -> int:
    user = args.user or getpass.getuser()
    pages = _prepared(args)

    ipp.validate(args.host, user=user, job_name=args.job_name,
                 media=args.media, colour=args.colour)
    print(f"printer at {args.host} accepted the job attributes; "
          f"{len(pages)} page(s) will print single-sided")
    if args.validate_only:
        return 0

    for number, (photo, page) in enumerate(pages, start=1):
        job = ipp.print_document(
            args.host,
            _encode(page),
            user=user,
            job_name=f"{args.job_name} p{number:02d}",
            media=args.media,
            colour=args.colour,
        )
        print(f"   page {number}/{len(pages)} ({photo.name}) -> job {job}")

    # The job history clears within seconds, so idle-plus-empty-queue is the
    # confirmation, not a lookup of the jobs just sent.
    state, reasons = ipp.wait_until_idle(args.host, user=user, timeout=args.timeout)
    print(f"done: queue empty, printer-state {state} (idle), reasons {reasons or ['none']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hardcopy", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="write the cleaned pages as JPEGs")
    _add_page_options(prepare)
    prepare.add_argument("-o", "--out-dir", type=Path, default=Path("."))
    prepare.set_defaults(func=cmd_prepare)

    pdf = sub.add_parser("pdf", help="write the cleaned pages as one PDF")
    _add_page_options(pdf)
    pdf.add_argument("-o", "--out", type=Path, required=True)
    pdf.add_argument("--title", default="")
    pdf.set_defaults(func=cmd_pdf)

    send = sub.add_parser("print", help="clean the pages and send them to a printer")
    _add_page_options(send)
    # Required on purpose. This repo holds no printer address.
    send.add_argument("--host", required=True, help="printer address or hostname")
    send.add_argument("--user", default="", help="defaults to the current user")
    send.add_argument("--job-name", default="hardcopy")
    send.add_argument("--media", default="na_letter_8.5x11in")
    send.add_argument("--colour", "--color", dest="colour", action="store_true")
    send.add_argument("--validate-only", action="store_true")
    send.add_argument("--timeout", type=float, default=300.0)
    send.set_defaults(func=cmd_print)

    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ipp.IppError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
