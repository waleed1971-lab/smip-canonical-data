#!/usr/bin/env python3

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_repository_integrity import VerificationError, WorkflowContext, build_receipt, write_receipt_atomic


HEADER = b"symbol,date,open,high,low,close,volume,value_traded,trades_count,is_final,partial,source_layer,source_run_id,source_file_sha256\n"


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class RepositoryIntegrityReceiptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "parts").mkdir()
        self.rows = [
            b"1000,2026-01-01,1,2,1,2,10,,,,,baseline,run-a,aaa\n",
            b"2000,2026-01-01,3,4,3,4,20,,,,,baseline,run-a,bbb\n",
        ]
        self.part_payload = HEADER + b"".join(self.rows)
        self.part_path = self.root / "parts/canonical-2026-01.csv"
        self.part_path.write_bytes(self.part_payload)
        canonical_payload = HEADER + b"".join(self.rows)
        gzip_path = self.root / "canonical-daily.csv.gz"
        gzip_path.write_bytes(gzip.compress(canonical_payload, mtime=0))

        self.manifest = {
            "dataset_version": "test-v1",
            "generated_at": "2026-08-30T00:00:00+00:00",
            "first_session": "2026-01-01",
            "last_session": "2026-01-01",
            "row_count": 2,
            "symbol_count": 2,
            "schema": HEADER.decode().rstrip("\n").split(","),
            "leakage_policy": {
                "status": "PASS",
                "future_derived_columns_included": [],
            },
            "canonical_file": {
                "name": "canonical-daily.csv.gz",
                "bytes": gzip_path.stat().st_size,
                "sha256": hashlib.sha256(gzip_path.read_bytes()).hexdigest(),
            },
            "canonical_text": {
                "header": HEADER.decode().rstrip("\n").split(","),
                "line_ending": "LF",
                "size_bytes": len(canonical_payload),
                "sha256": sha256(canonical_payload),
                "row_count": 2,
            },
            "text_parts": [
                {
                    "period": "2026-01",
                    "part_index": 1,
                    "part_count_for_period": 1,
                    "path": "parts/canonical-2026-01.csv",
                    "url": "https://example.invalid/canonical-2026-01.csv",
                    "size_bytes": len(self.part_payload),
                    "max_size_bytes_exclusive": 1_000_000,
                    "sha256": sha256(self.part_payload),
                    "row_count": 2,
                    "symbol_count": 2,
                    "first_session": "2026-01-01",
                    "last_session": "2026-01-01",
                }
            ],
            "text_parts_reconstruction": {
                "canonical_row_order": ["symbol", "date"],
                "max_part_size_bytes_exclusive": 1_000_000,
                "expected_sha256": sha256(canonical_payload),
                "expected_size_bytes": len(canonical_payload),
                "expected_row_count": 2,
                "automatic_update_enabled": False,
            },
        }
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.context = WorkflowContext(
            repository="waleed1971-lab/smip-canonical-data",
            commit_sha="d" * 40,
            workflow_name="Repository integrity attestation",
            workflow_run_id="123",
            workflow_run_attempt="1",
            workflow_ref="refs/heads/main",
            workflow_url="https://github.com/example/actions/runs/123",
            executed_at="2026-08-30T00:00:00+00:00",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_emits_pass_receipt_with_required_fields(self) -> None:
        receipt = build_receipt(self.root, self.context)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["commit_sha"], "d" * 40)
        self.assertEqual(receipt["workflow"]["run_id"], "123")
        self.assertEqual(receipt["text_parts"]["count"], 1)
        self.assertEqual(receipt["reconstruction"]["sha256"], sha256(HEADER + b"".join(self.rows)))
        self.assertEqual(receipt["reconstruction"]["row_count"], 2)
        self.assertEqual(receipt["reconstruction"]["last_session"], "2026-01-01")
        self.assertEqual(receipt["text_parts"]["items"][0]["sha256"], sha256(self.part_payload))
        self.assertFalse(receipt["policy"]["automatic_update_enabled"])

    def test_accepts_enabled_automatic_update_with_allow_listed_public_source(self) -> None:
        self.manifest["text_parts_reconstruction"]["automatic_update_enabled"] = True
        self.manifest["automatic_update"] = {
            "enabled": True,
            "mode": "github-actions-public-pull",
            "source_manifest_url": "https://smip-server.onrender.com/research/manifest.json",
            "source_canonical_url": "https://smip-server.onrender.com/research/canonical-daily.csv.gz",
            "fail_closed": True,
        }
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        receipt = build_receipt(self.root, self.context)
        self.assertTrue(receipt["policy"]["automatic_update_enabled"])
        self.assertEqual(receipt["automatic_update"]["mode"], "github-actions-public-pull")

    def test_enabled_automatic_update_without_metadata_fails_closed(self) -> None:
        self.manifest["text_parts_reconstruction"]["automatic_update_enabled"] = True
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        with self.assertRaisesRegex(VerificationError, "metadata is missing"):
            build_receipt(self.root, self.context)

    def test_tamper_fails_and_does_not_replace_last_pass_receipt(self) -> None:
        output = self.root / "repository-integrity-receipt.json"
        previous = {"status": "PASS", "commit_sha": "a" * 40}
        write_receipt_atomic(previous, output)
        previous_bytes = output.read_bytes()

        self.part_path.write_bytes(self.part_payload + b"tampered\n")
        with self.assertRaisesRegex(VerificationError, "SHA256 mismatch"):
            build_receipt(self.root, self.context)
        self.assertEqual(output.read_bytes(), previous_bytes)


if __name__ == "__main__":
    unittest.main()
