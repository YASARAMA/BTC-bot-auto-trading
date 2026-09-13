"""Generate license keys.

    python scripts/make_licenses.py --count 100

Writes two files:
  bot/license_hashes.json   hashes only — committed and shipped inside the app
  licenses-private.csv      the actual keys — KEEP PRIVATE, never commit this

Running it again with --count replaces the whole list, so previously issued keys stop
working. Use --add to mint extra keys while keeping the existing ones valid.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.licensing import HASHES_FILE, generate_keys, key_hash, load_valid_hashes, write_hash_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "licenses-private.csv"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=100, help="how many keys to generate (default 100)")
    p.add_argument("--add", action="store_true", help="keep the existing keys valid and append new ones")
    p.add_argument("--out", default=str(PRIVATE), help="where to write the private key list")
    args = p.parse_args(argv)

    keys = generate_keys(args.count)
    rows = [{"key": k, "hash": key_hash(k), "issued_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%d"),
             "issued_to": "", "note": ""} for k in keys]

    out = Path(args.out)
    existing_hashes = set()
    if args.add:
        existing_hashes = load_valid_hashes()
        if out.exists():
            with out.open(newline="", encoding="utf-8") as fh:
                rows = [*csv.DictReader(fh), *rows]

    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["key", "hash", "issued_at", "issued_to", "note"])
        writer.writeheader()
        writer.writerows(rows)

    payload = write_hash_file([r["key"] for r in rows] if args.add else keys, HASHES_FILE)
    if args.add:
        merged = sorted(existing_hashes | set(payload["hashes"]))
        write_hash_file([], HASHES_FILE)  # rewrite with the merged set below
        import json

        HASHES_FILE.write_text(json.dumps({**payload, "count": len(merged), "hashes": merged}, indent=2) + "\n",
                               encoding="utf-8")
        payload["count"] = len(merged)

    print(f"{len(keys)} new key(s); {payload['count']} valid in total")
    print(f"private keys  -> {out}   (KEEP THIS FILE PRIVATE)")
    print(f"shipped hashes -> {HASHES_FILE}")
    print("\nFirst five:")
    for k in keys[:5]:
        print("  " + k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
