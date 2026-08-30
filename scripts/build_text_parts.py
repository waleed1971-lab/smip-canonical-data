from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical-daily.csv.gz"
MANIFEST = ROOT / "manifest.json"
PARTS_DIR = ROOT / "parts"
RAW_BASE = "https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected_gzip_sha = manifest["canonical_file"]["sha256"]
    actual_gzip_sha = sha256_file(CANONICAL)
    if actual_gzip_sha != expected_gzip_sha:
        raise SystemExit("canonical gzip SHA256 does not match manifest")

    PARTS_DIR.mkdir(exist_ok=True)
    for old_part in PARTS_DIR.glob("canonical-*.csv"):
        old_part.unlink()

    handles: dict[int, object] = {}
    row_counts: dict[int, int] = defaultdict(int)
    symbols: dict[int, set[str]] = defaultdict(set)
    first_sessions: dict[int, str] = {}
    last_sessions: dict[int, str] = {}
    canonical_text_digest = hashlib.sha256()
    canonical_text_size = 0
    header = b""

    try:
        with gzip.open(CANONICAL, "rb") as source:
            for line_number, line in enumerate(source):
                canonical_text_digest.update(line)
                canonical_text_size += len(line)
                if line_number == 0:
                    header = line
                    continue

                decoded = line.decode("utf-8")
                row = next(csv.reader([decoded]))
                if len(row) != len(manifest["schema"]):
                    raise SystemExit(f"schema mismatch at canonical row {line_number + 1}")
                symbol, session = row[0], row[1]
                year = int(session[:4])
                if year not in handles:
                    path = PARTS_DIR / f"canonical-{year}.csv"
                    handle = path.open("wb")
                    handle.write(header)
                    handles[year] = handle
                handles[year].write(line)
                row_counts[year] += 1
                symbols[year].add(symbol)
                first_sessions.setdefault(year, session)
                last_sessions[year] = session
    finally:
        for handle in handles.values():
            handle.close()

    total_rows = sum(row_counts.values())
    if total_rows != manifest["row_count"]:
        raise SystemExit(
            f"text part rows {total_rows} do not match manifest {manifest['row_count']}"
        )

    parts = []
    for year in sorted(row_counts):
        path = PARTS_DIR / f"canonical-{year}.csv"
        parts.append(
            {
                "year": year,
                "path": f"parts/{path.name}",
                "url": f"{RAW_BASE}/parts/{path.name}",
                "encoding": "utf-8",
                "content_type": "text/csv; charset=utf-8",
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "row_count": row_counts[year],
                "symbol_count": len(symbols[year]),
                "first_session": first_sessions[year],
                "last_session": last_sessions[year],
                "includes_header": True,
            }
        )

    manifest["canonical_text"] = {
        "encoding": "utf-8",
        "line_ending": "LF",
        "size_bytes": canonical_text_size,
        "sha256": canonical_text_digest.hexdigest(),
        "row_count": total_rows,
        "header": manifest["schema"],
    }
    manifest["text_parts"] = parts
    manifest["text_parts_reconstruction"] = {
        "part_read_order": "ascending year",
        "canonical_row_order": ["symbol", "date"],
        "procedure": [
            "Read every part as UTF-8 CSV and keep one copy of the common header.",
            "Collect every data row from all parts without changing its field bytes.",
            "Sort the collected rows ascending by symbol, then date.",
            "Write the common header followed by the sorted original row bytes using LF line endings.",
            "The reconstructed UTF-8 bytes must match canonical_text.sha256 and canonical_text.size_bytes.",
        ],
        "expected_sha256": canonical_text_digest.hexdigest(),
        "expected_size_bytes": canonical_text_size,
        "expected_row_count": total_rows,
        "automatic_update_enabled": False,
    }

    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "years": sorted(row_counts),
                "parts": len(parts),
                "rows": total_rows,
                "canonical_text_sha256": canonical_text_digest.hexdigest(),
                "canonical_text_size": canonical_text_size,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
