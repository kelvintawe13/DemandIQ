#!/usr/bin/env python3
"""Score the OCR ingestion pipeline against a verified reference sheet.

The only way to know whether a parser change helped is to run it over pages a
human has checked and read the numbers, so this is the measurement that should
precede the next recognition improvement.

Usage:
    python scripts/ocr_accuracy.py --images reference/pages --truth reference/truth.csv
    python scripts/ocr_accuracy.py --images reference/pages --truth reference/truth.csv \\
        --json reference/report.json --min-pair-accuracy 0.95
    python scripts/ocr_accuracy.py --self-check

`--self-check` renders a few clean log pages, writes their reference CSV and
scores them. It proves the harness (image read, OCR, pairing, boxes, scoring) is
wired correctly; it is printed text, so a perfect score there is not a claim
about handwriting.

Reference CSV columns: `image,date,demand_qty[,box]`. `date` is ISO; `box` is
the optional date-and-count region as `x,y,w,h` fractions of the photo.

Exit codes: 0 within target, 1 below `--min-pair-accuracy`, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.services.ingestion.ocr_benchmark import (
    evaluate_directory,
    format_report,
    load_reference,
    write_self_check,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure OCR extraction against a verified reference sheet.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--images", type=Path, help="directory holding the reference photos")
    parser.add_argument("--truth", type=Path, help="reference CSV (image,date,demand_qty[,box])")
    parser.add_argument("--json", type=Path, help="also write the full report as JSON here")
    parser.add_argument("--limit", type=int, help="score only the first N pages (sorted by name)")
    parser.add_argument(
        "--min-pair-accuracy",
        type=float,
        default=None,
        metavar="0..1",
        help="exit 1 when complete date-count accuracy is below this value",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="render synthetic pages and score them, to verify the harness itself",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.self_check:
        with tempfile.TemporaryDirectory() as folder:
            truth = write_self_check(folder)
            report = evaluate_directory(folder, load_reference(truth))
        print("Self-check: synthetic printed pages, not handwriting.\n")
    elif args.images and args.truth:
        try:
            reference = load_reference(args.truth)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not args.images.is_dir():
            print(f"error: {args.images} is not a directory", file=sys.stderr)
            return 2
        report = evaluate_directory(args.images, reference, limit=args.limit)
    else:
        parser.print_help()
        return 2

    print(format_report(report))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nJSON report: {args.json}")

    pair_accuracy = report["totals"]["pair_accuracy"]
    if args.min_pair_accuracy is not None:
        if pair_accuracy is None:
            print("error: no reference rows to score", file=sys.stderr)
            return 2
        if pair_accuracy < args.min_pair_accuracy:
            print(
                f"below target: pair accuracy {pair_accuracy:.3f} "
                f"< {args.min_pair_accuracy:.3f}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
