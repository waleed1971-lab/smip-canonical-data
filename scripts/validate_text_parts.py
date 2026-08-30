from __future__ import annotations

import hashlib
import json
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

    for index, item in enumerate(parts):
        path = ROOT / item["path"]
        payload = path.read_bytes()
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
            canonical_rows.append(((columns[0], columns[1]), line))
        rows += item["row_count"]

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
