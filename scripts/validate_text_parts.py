from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    parts = manifest["text_parts"]
    reconstruction = manifest["text_parts_reconstruction"]
    digest = hashlib.sha256()
    rows = 0
    header: bytes | None = None
    canonical_rows: list[tuple[tuple[str, str], bytes]] = []
    order_keys: list[tuple[str, int]] = []

    for item in parts:
        path = ROOT / item["path"]
        payload = path.read_bytes()
        period = item.get("period", "")
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", period):
            raise SystemExit(f"invalid monthly period: {period}")
        part_index = int(item.get("part_index", 1))
        part_count = int(item.get("part_count_for_period", 1))
        expected_name = (
            f"canonical-{period}.csv"
            if part_count == 1
            else f"canonical-{period}-part{part_index:02d}.csv"
        )
        if path.name != expected_name:
            raise SystemExit(f"invalid monthly filename: {path}")
        limit = reconstruction["max_part_size_bytes_exclusive"]
        if len(payload) >= limit or len(payload) != item["size_bytes"]:
            raise SystemExit(f"monthly size limit or manifest size mismatch: {path}")
        order_keys.append((period, part_index))
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            raise SystemExit(f"SHA256 mismatch: {path}")
        first_newline = payload.find(b"\n")
        if first_newline < 0:
            raise SystemExit(f"missing header newline: {path}")
        part_header = payload[: first_newline + 1]
        if header is None:
            header = part_header
        elif part_header != header:
            raise SystemExit(f"header mismatch: {path}")

        for line in payload[first_newline + 1 :].splitlines(keepends=True):
            text = line.decode("utf-8")
            columns = text.rstrip("\n").split(",", 2)
            if len(columns) < 3:
                raise SystemExit(f"invalid canonical row: {path}")
            if columns[1][:7] != period:
                raise SystemExit(f"row outside declared month: {path}")
            canonical_rows.append(((columns[0], columns[1]), line))
        rows += item["row_count"]

    if order_keys != sorted(order_keys) or len(order_keys) != len(set(order_keys)):
        raise SystemExit("monthly parts are not unique and ascending")

    if header is None:
        raise SystemExit("no text part header found")
    digest.update(header)
    size = len(header)
    for _, line in sorted(canonical_rows, key=lambda item: item[0]):
        digest.update(line)
        size += len(line)

    if digest.hexdigest() != reconstruction["expected_sha256"]:
        raise SystemExit("reconstructed canonical text SHA256 mismatch")
    if size != reconstruction["expected_size_bytes"]:
        raise SystemExit("reconstructed canonical text size mismatch")
    if rows != reconstruction["expected_row_count"]:
        raise SystemExit("reconstructed canonical text row count mismatch")
    if rows != manifest["row_count"]:
        raise SystemExit("text part rows do not match canonical manifest")

    forbidden = {
        "label",
        "target",
        "forward_return",
        "future_return",
        "adjusted_close",
    }
    if forbidden.intersection(manifest["schema"]):
        raise SystemExit("forbidden leakage columns found in schema")

    print(
        json.dumps(
            {
                "status": "PASS",
                "parts": len(parts),
                "first_period": order_keys[0][0],
                "last_period": order_keys[-1][0],
                "max_part_size_bytes": max(item["size_bytes"] for item in parts),
                "rows": rows,
                "reconstructed_sha256": digest.hexdigest(),
                "reconstructed_size_bytes": size,
                "gzip_sha256": sha256_file(ROOT / "canonical-daily.csv.gz"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
