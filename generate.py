#!/usr/bin/env python3
"""
generate.py
===========
Command-line front end for `hubmap_croissant`. Give it one or more HuBMAP dataset
identifiers; it writes a metadata-only Croissant JSON-LD file for each.

    python generate.py HBM279.TQRS.775
    python generate.py HBM279.TQRS.775 HBM836.QGXW.892 --outdir examples
    python generate.py HBM279.TQRS.775 --no-validate

Nothing is hard-coded — the dataset(s) come entirely from the arguments you pass.
No files are downloaded and Croissant Baker is not used (see README).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hubmap_croissant import build_croissant, validate


def _safe_name(identifier: str) -> str:
    """A filesystem-friendly stem from a HuBMAP id/uuid."""
    return "".join(c if (c.isalnum() or c in ".-_") else "_" for c in identifier)


def generate_one(identifier: str, outdir: Path, do_validate: bool) -> Path:
    croissant = build_croissant(identifier)
    if do_validate:
        validate(croissant)
        print(f"[ok]   {identifier}: valid Croissant 1.1")
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / f"croissant_{_safe_name(identifier)}.jsonld"
    out.write_text(json.dumps(croissant, indent=2, ensure_ascii=False))
    print(f"[write] {out}")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Generate metadata-only Croissant files for HuBMAP datasets.")
    p.add_argument("identifiers", nargs="+",
                   help="One or more HuBMAP dataset IDs (e.g. HBM279.TQRS.775) or UUIDs.")
    p.add_argument("--outdir", default=".", type=Path,
                   help="Directory to write the .jsonld files into (default: current dir).")
    p.add_argument("--no-validate", action="store_true",
                   help="Skip mlcroissant validation of the result.")
    args = p.parse_args(argv)

    failures = 0
    for ident in args.identifiers:
        try:
            generate_one(ident, args.outdir, not args.no_validate)
        except Exception as exc:  # keep going through the rest of the list
            failures += 1
            print(f"[fail] {ident}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
