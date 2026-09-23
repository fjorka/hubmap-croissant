#!/usr/bin/env python3
"""
Make a dataset manifest, and later check a downloaded copy against it.

The problem this solves: a Croissant file describes files with globs relative to
a dataset root. After a Globus transfer the user has that tree somewhere on their
own disk, under a directory name of their choosing, on an OS that adds its own
litter. This decides whether what they got is what we described.

    # data holder, once per dataset:
    python croissant_manifest.py build  /path/to/HBM279…  -o HBM279.manifest.tsv
    python croissant_manifest.py build  /path/to/HBM279…  -o HBM279.manifest.tsv --checksums

    # anyone, after downloading:
    python croissant_manifest.py verify /their/local/copy  -m HBM279.manifest.tsv
    python croissant_manifest.py verify /their/local/copy  -m HBM279.manifest.tsv --checksums

Manifest format: TSV, one row per file, sorted by path.
    rel_path <TAB> size_bytes <TAB> sha256_or_dash
A leading '#' line records the tolerance policy the verifier applies.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

# --- tolerance policy -------------------------------------------------------
# Files created by the DOWNLOADER's operating system or file manager. They are
# not part of the dataset and their presence or absence is never a mismatch.
IGNORE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
IGNORE_PREFIXES = ("._",)          # macOS AppleDouble sidecars
IGNORE_SUFFIXES = (".part", ".crdownload", ".tmp")   # interrupted transfers

POLICY = ("ignore=" + ",".join(sorted(IGNORE_NAMES))
          + "|prefixes=" + ",".join(IGNORE_PREFIXES)
          + "|suffixes=" + ",".join(IGNORE_SUFFIXES)
          + "|mtime=not-compared|empty-dirs=not-compared")


def ignored(rel: str) -> bool:
    b = os.path.basename(rel)
    return (b in IGNORE_NAMES
            or b.startswith(IGNORE_PREFIXES)
            or b.endswith(IGNORE_SUFFIXES))


def sha256(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def walk(root: str, do_hash: bool, label: str) -> dict[str, tuple[int, str]]:
    """rel_path -> (size, sha256 or '-'). Symlinks are recorded, never followed."""
    out: dict[str, tuple[int, str]] = {}
    skipped_links = 0
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if ignored(rel):
                continue
            if os.path.islink(full):
                skipped_links += 1
                continue
            try:
                size = os.path.getsize(full)
            except OSError as e:
                print(f"  ! unreadable: {rel} ({e})", file=sys.stderr)
                continue
            out[rel] = (size, sha256(full) if do_hash else "-")
            n += 1
            if n % 500 == 0:
                print(f"\r  {label}: {n} files", end="", file=sys.stderr)
    print(f"\r  {label}: {n} files" + (f"  ({skipped_links} symlinks skipped)" if skipped_links else ""),
          file=sys.stderr)
    return out


def cmd_build(a) -> int:
    files = walk(a.root, a.checksums, "scanned")
    total = sum(s for s, _ in files.values())
    with open(a.outfile, "w") as fh:
        fh.write(f"# croissant-manifest v1\tfiles={len(files)}\tbytes={total}\t"
                 f"checksums={'sha256' if a.checksums else 'none'}\tpolicy={POLICY}\n")
        fh.write("rel_path\tsize\tsha256\n")
        for rel in sorted(files):
            size, digest = files[rel]
            fh.write(f"{rel}\t{size}\t{digest}\n")
    print(f"\n[write] {a.outfile}")
    print(f"  {len(files)} files, {total/1e9:.2f} GB, "
          f"checksums={'yes' if a.checksums else 'no'}")
    print(f"  manifest sha256: {sha256(a.outfile)}")
    print("\n  Publish that digest in the Croissant as the manifest FileObject's sha256.")
    return 0


def read_manifest(path: str) -> tuple[dict[str, tuple[int, str]], str]:
    rows: dict[str, tuple[int, str]] = {}
    header = ""
    with open(path) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("#"):
                header = line
                continue
            if line.startswith("rel_path\t"):
                continue
            rel, size, digest = line.split("\t")
            rows[rel] = (int(size), digest)
    return rows, header


def cmd_verify(a) -> int:
    expected, header = read_manifest(a.manifest)
    has_digests = any(d != "-" for _, d in expected.values())
    want_hash = a.checksums and has_digests
    if a.checksums and not has_digests:
        print("  ! manifest carries no checksums; comparing paths and sizes only",
              file=sys.stderr)

    actual = walk(a.root, want_hash, "found")

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    shared = set(expected) & set(actual)
    wrong_size = sorted(p for p in shared if expected[p][0] != actual[p][0])
    wrong_hash = sorted(p for p in shared
                        if want_hash and expected[p][1] != "-"
                        and expected[p][1] != actual[p][1]) if want_hash else []

    print()
    print(header)
    print()
    print(f"  expected {len(expected)} files   found {len(actual)}")
    print(f"  missing         {len(missing)}")
    print(f"  unexpected      {len(extra)}")
    print(f"  size mismatch   {len(wrong_size)}")
    print(f"  digest mismatch {len(wrong_hash) if want_hash else '(not checked)'}")

    for label, rows in (("MISSING", missing), ("UNEXPECTED", extra),
                        ("SIZE MISMATCH", wrong_size), ("DIGEST MISMATCH", wrong_hash)):
        if rows:
            print(f"\n  --- {label} (first 20 of {len(rows)}) ---")
            for r in rows[:20]:
                if label == "SIZE MISMATCH":
                    print(f"     {r}\n        expected {expected[r][0]}  found {actual[r][0]}")
                else:
                    print(f"     {r}")

    ok = not (missing or extra or wrong_size or wrong_hash)
    print("\n  RESULT: " + ("MATCH — the local copy is what the Croissant describes."
                            if ok else "MISMATCH — see above."))
    if ok and not want_hash:
        print("  (paths and sizes only; rerun with --checksums to compare bytes)")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Write a manifest from a local dataset tree.")
    b.add_argument("root")
    b.add_argument("-o", "--outfile", required=True)
    b.add_argument("--checksums", action="store_true",
                   help="Compute sha256 for every file. Slow; do it once.")
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify", help="Check a downloaded copy against a manifest.")
    v.add_argument("root")
    v.add_argument("-m", "--manifest", required=True)
    v.add_argument("--checksums", action="store_true",
                   help="Also compare sha256 (requires a manifest built with them).")
    v.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
