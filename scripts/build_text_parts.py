from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "canonical-daily.csv.gz"
MANIFEST = ROOT / "manifest.json"
PARTS_DIR = ROOT / "parts"
RAW_BASE = "https://raw.githubusercontent.com/waleed1971-lab/smip-canonical-data/main"
MAX_PART_SIZE_BYTES = 1_000_000
PART_NAME = re.compile(
    r"canonical-(?P<period>20\d{2}-(?:0[1-9]|1[0-2]))(?:-part(?P<part>\d{2}))?\.csv"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def split_oversized_month(path: Path, header: bytes) -> None:
    if path.stat().st_size < MAX_PART_SIZE_BYTES:
        return

    lines = path.read_bytes().splitlines(keepends=True)
    if not lines or lines[0] != header:
        raise SystemExit(f"invalid monthly header: {path}")

    part_number = 1
    target = PARTS_DIR / f"{path.stem}-part{part_number:02d}.csv"
    handle = target.open("wb")
    handle.write(header)
    size = len(header)
    try:
        for line in lines[1:]:
            if size + len(line) >= MAX_PART_SIZE_BYTES and size > len(header):
                handle.close()
                part_number += 1
                target = PARTS_DIR / f"{path.stem}-part{part_number:02d}.csv"
                handle = target.open("wb")
                handle.write(header)
                size = len(header)
            handle.write(line)
            size += len(line)
    finally:
        handle.close()
    path.unlink()


def inspect_part(path: Path) -> dict[str, object]:
    match = PART_NAME.fullmatch(path.name)
    if not match:
        raise SystemExit(f"invalid part filename: {path.name}")
    period = match.group("period")
    part_index = int(match.group("part") or "1")
    row_count = 0
    symbols: set[str] = set()
    first_session: str | None = None
    last_session: str | None = None

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            session = row["date"]
            if session[:7] != period:
                raise SystemExit(f"row outside declared month: {path}")
            row_count += 1
            symbols.add(row["symbol"])
            first_session = session if first_session is None else min(first_session, session)
            last_session = session if last_session is None else max(last_session, session)

    size_bytes = path.stat().st_size
    if size_bytes >= MAX_PART_SIZE_BYTES:
        raise SystemExit(
            f"monthly part {path.name} is {size_bytes} bytes; expected < {MAX_PART_SIZE_BYTES}"
        )
    if row_count == 0 or first_session is None or last_session is None:
        raise SystemExit(f"empty monthly part: {path}")

    return {
        "period": period,
        "year": int(period[:4]),
        "month": int(period[5:7]),
        "part_index": part_index,
        "path": f"parts/{path.name}",
        "url": f"{RAW_BASE}/parts/{path.name}",
        "encoding": "utf-8",
        "content_type": "text/csv; charset=utf-8",
        "size_bytes": size_bytes,
        "max_size_bytes_exclusive": MAX_PART_SIZE_BYTES,
        "sha256": sha256_file(path),
        "row_count": row_count,
        "symbol_count": len(symbols),
        "first_session": first_session,
        "last_session": last_session,
        "includes_header": True,
    }


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected_gzip_sha = manifest["canonical_file"]["sha256"]
    actual_gzip_sha = sha256_file(CANONICAL)
    if actual_gzip_sha != expected_gzip_sha:
        raise SystemExit("canonical gzip SHA256 does not match manifest")

    PARTS_DIR.mkdir(exist_ok=True)
    for old_part in PARTS_DIR.glob("canonical-*.csv"):
        old_part.unlink()

    handles: dict[str, object] = {}
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
                period = row[1][:7]
                if period not in handles:
                    path = PARTS_DIR / f"canonical-{period}.csv"
                    handle = path.open("wb")
                    handle.write(header)
                    handles[period] = handle
                handles[period].write(line)
    finally:
        for handle in handles.values():
            handle.close()

    for path in sorted(PARTS_DIR.glob("canonical-????-??.csv")):
        split_oversized_month(path, header)

    parts = [inspect_part(path) for path in PARTS_DIR.glob("canonical-*.csv")]
    parts.sort(key=lambda item: (str(item["period"]), int(item["part_index"])))
    period_part_counts = Counter(str(item["period"]) for item in parts)
    for item in parts:
        item["part_count_for_period"] = period_part_counts[str(item["period"])]

    total_rows = sum(int(item["row_count"]) for item in parts)
    if total_rows != manifest["row_count"]:
        raise SystemExit(
            f"text part rows {total_rows} do not match manifest {manifest['row_count']}"
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
        "partitioning": "calendar month; oversized months use numbered subparts",
        "filename_patterns": [
            "parts/canonical-YYYY-MM.csv",
            "parts/canonical-YYYY-MM-partNN.csv",
        ],
        "part_read_order": "ascending period, then part_index",
        "canonical_row_order": ["symbol", "date"],
        "max_part_size_bytes_exclusive": MAX_PART_SIZE_BYTES,
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
                "periods": sorted(period_part_counts),
                "period_count": len(period_part_counts),
                "parts": len(parts),
                "rows": total_rows,
                "max_part_size_bytes": max(int(item["size_bytes"]) for item in parts),
                "canonical_text_sha256": canonical_text_digest.hexdigest(),
                "canonical_text_size": canonical_text_size,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
