#!/usr/bin/env python3

from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from scripts.update_from_public_source import (
    EXPECTED_SCHEMA,
    UpdateError,
    determine_update,
    index_canonical,
)


HEADER = (",".join(EXPECTED_SCHEMA) + "\n").encode("utf-8")


def row(symbol: str, session: str, close: str = "2") -> bytes:
    return f"{symbol},{session},1,2,1,{close},10,,,,,baseline,run-a,{'a' * 64}\n".encode()


class AutomaticUpdateContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_gzip(self, name: str, rows: list[bytes]) -> Path:
        path = self.root / name
        path.write_bytes(gzip.compress(HEADER + b"".join(rows), mtime=0))
        return path

    def manifest(self, last_session: str, row_count: int, sha: str, generated_at: str) -> dict:
        return {
            "last_session": last_session,
            "row_count": row_count,
            "generated_at": generated_at,
            "canonical_file": {"sha256": sha},
        }

    def test_same_snapshot_is_idempotent_no_change(self) -> None:
        path = self.write_gzip("same.gz", [row("1000", "2026-01-01")])
        index = index_canonical(path, EXPECTED_SCHEMA)
        manifest = self.manifest("2026-01-01", 1, "a" * 64, "2026-01-01T15:00:00+00:00")
        changed, new_rows = determine_update(manifest, index, dict(manifest), index)
        self.assertFalse(changed)
        self.assertEqual(new_rows, 0)

    def test_new_session_is_append_only(self) -> None:
        current_path = self.write_gzip("current.gz", [row("1000", "2026-01-01")])
        source_path = self.write_gzip(
            "source.gz", [row("1000", "2026-01-01"), row("1000", "2026-01-02")]
        )
        current = index_canonical(current_path, EXPECTED_SCHEMA)
        source = index_canonical(source_path, EXPECTED_SCHEMA)
        changed, new_rows = determine_update(
            self.manifest("2026-01-01", 1, "a" * 64, "2026-01-01T15:00:00+00:00"),
            current,
            self.manifest("2026-01-02", 2, "b" * 64, "2026-01-02T15:00:00+00:00"),
            source,
        )
        self.assertTrue(changed)
        self.assertEqual(new_rows, 1)

    def test_historical_mutation_fails_closed(self) -> None:
        current_path = self.write_gzip("current.gz", [row("1000", "2026-01-01", "2")])
        source_path = self.write_gzip(
            "source.gz", [row("1000", "2026-01-01", "9"), row("1000", "2026-01-02")]
        )
        with self.assertRaisesRegex(UpdateError, "historical row changed"):
            determine_update(
                self.manifest("2026-01-01", 1, "a" * 64, "2026-01-01T15:00:00+00:00"),
                index_canonical(current_path, EXPECTED_SCHEMA),
                self.manifest("2026-01-02", 2, "b" * 64, "2026-01-02T15:00:00+00:00"),
                index_canonical(source_path, EXPECTED_SCHEMA),
            )

    def test_historical_backfill_fails_closed(self) -> None:
        current_path = self.write_gzip("current.gz", [row("1000", "2026-01-02")])
        source_path = self.write_gzip(
            "source.gz",
            [row("1000", "2026-01-02"), row("1000", "2026-01-03"), row("2000", "2026-01-01")],
        )
        with self.assertRaisesRegex(UpdateError, "historical backfill"):
            determine_update(
                self.manifest("2026-01-02", 1, "a" * 64, "2026-01-02T15:00:00+00:00"),
                index_canonical(current_path, EXPECTED_SCHEMA),
                self.manifest("2026-01-03", 3, "b" * 64, "2026-01-03T15:00:00+00:00"),
                index_canonical(source_path, EXPECTED_SCHEMA),
            )


if __name__ == "__main__":
    unittest.main()
