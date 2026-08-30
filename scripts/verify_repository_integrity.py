#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEX64 = re.compile(r"[0-9a-f]{64}")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
PERIOD = re.compile(r"20\d{2}-(0[1-9]|1[0-2])")


class VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowContext:
    repository: str
    commit_sha: str
    workflow_name: str
    workflow_run_id: str
    workflow_run_attempt: str
    workflow_ref: str
    workflow_url: str
    executed_at: str


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def parse_header(payload: bytes, path: Path) -> tuple[bytes, list[bytes]]:
    require(b"\r" not in payload, f"CR bytes are not allowed: {path}")
    require(payload.endswith(b"\n"), f"file must end with LF: {path}")
    first_newline = payload.find(b"\n")
    require(first_newline >= 0, f"missing header newline: {path}")
    header = payload[: first_newline + 1]
    lines = payload[first_newline + 1 :].splitlines(keepends=True)
    require(all(line.endswith(b"\n") for line in lines), f"non-LF row found: {path}")
    return header, lines


def parse_row(line: bytes, path: Path) -> list[str]:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as error:
        raise VerificationError(f"invalid UTF-8 row in {path}: {error}") from error
    try:
        return next(csv.reader(io.StringIO(text.rstrip("\n")), strict=True))
    except (csv.Error, StopIteration) as error:
        raise VerificationError(f"invalid CSV row in {path}: {error}") from error


def build_receipt(root: Path, context: WorkflowContext) -> dict[str, Any]:
    require(COMMIT_SHA.fullmatch(context.commit_sha) is not None, "invalid commit SHA")
    require(context.workflow_run_id.isdigit(), "invalid workflow run ID")
    require(context.workflow_run_attempt.isdigit(), "invalid workflow run attempt")

    manifest_path = root / "manifest.json"
    require(manifest_path.is_file(), "manifest.json is missing")
    manifest_payload = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"manifest is not valid UTF-8 JSON: {error}") from error

    parts = manifest.get("text_parts")
    reconstruction = manifest.get("text_parts_reconstruction")
    canonical_text = manifest.get("canonical_text")
    require(isinstance(parts, list) and parts, "manifest text_parts is missing")
    require(isinstance(reconstruction, dict), "manifest reconstruction contract is missing")
    require(isinstance(canonical_text, dict), "manifest canonical_text is missing")

    expected_schema = manifest.get("schema")
    require(isinstance(expected_schema, list) and expected_schema, "manifest schema is missing")
    expected_header = (",".join(expected_schema) + "\n").encode("utf-8")
    require(canonical_text.get("header") == expected_schema, "canonical_text header differs from schema")

    forbidden = {
        "label",
        "target",
        "forward_return",
        "future_return",
        "adjusted_close",
    }
    require(not forbidden.intersection(expected_schema), "forbidden leakage columns found")
    leakage = manifest.get("leakage_policy", {})
    require(leakage.get("status") == "PASS", "manifest leakage policy is not PASS")
    require(leakage.get("future_derived_columns_included") == [], "future-derived columns are present")

    expected_limit = reconstruction.get("max_part_size_bytes_exclusive")
    require(isinstance(expected_limit, int) and expected_limit > 0, "invalid part-size limit")
    require(reconstruction.get("automatic_update_enabled") is False, "automatic update must remain disabled")

    canonical_rows: list[tuple[tuple[str, str], bytes]] = []
    receipt_parts: list[dict[str, Any]] = []
    part_paths: set[str] = set()
    order_keys: list[tuple[str, int]] = []
    total_rows = 0
    common_header: bytes | None = None

    for item in parts:
        require(isinstance(item, dict), "invalid text_parts entry")
        rel_path = item.get("path")
        period = item.get("period")
        part_index = item.get("part_index")
        part_count = item.get("part_count_for_period")
        require(isinstance(rel_path, str), "text part path is missing")
        require(rel_path not in part_paths, f"duplicate part path: {rel_path}")
        require(not rel_path.startswith("/") and ".." not in Path(rel_path).parts, f"unsafe part path: {rel_path}")
        require(PERIOD.fullmatch(str(period)) is not None, f"invalid period: {period}")
        require(isinstance(part_index, int) and part_index >= 1, f"invalid part_index: {rel_path}")
        require(isinstance(part_count, int) and part_count >= part_index, f"invalid part count: {rel_path}")

        expected_name = (
            f"canonical-{period}.csv"
            if part_count == 1
            else f"canonical-{period}-part{part_index:02d}.csv"
        )
        require(Path(rel_path).name == expected_name, f"unexpected part filename: {rel_path}")

        path = root / rel_path
        require(path.is_file(), f"missing text part: {rel_path}")
        payload = path.read_bytes()
        actual_sha256 = sha256_bytes(payload)
        actual_size = len(payload)
        require(HEX64.fullmatch(str(item.get("sha256"))) is not None, f"invalid manifest SHA256: {rel_path}")
        require(actual_sha256 == item.get("sha256"), f"SHA256 mismatch: {rel_path}")
        require(actual_size == item.get("size_bytes"), f"size mismatch: {rel_path}")
        require(actual_size < expected_limit, f"part exceeds size limit: {rel_path}")
        require(item.get("max_size_bytes_exclusive") == expected_limit, f"part limit mismatch: {rel_path}")

        header, lines = parse_header(payload, path)
        require(header == expected_header, f"header differs from schema: {rel_path}")
        if common_header is None:
            common_header = header
        else:
            require(header == common_header, f"header mismatch: {rel_path}")

        symbols: set[str] = set()
        dates: list[str] = []
        for line in lines:
            columns = parse_row(line, path)
            require(len(columns) == len(expected_schema), f"column count mismatch: {rel_path}")
            symbol, session = columns[0], columns[1]
            require(session[:7] == period, f"row outside declared month: {rel_path}")
            symbols.add(symbol)
            dates.append(session)
            canonical_rows.append(((symbol, session), line))

        actual_rows = len(lines)
        first_session = min(dates) if dates else None
        last_session = max(dates) if dates else None
        require(actual_rows == item.get("row_count"), f"row count mismatch: {rel_path}")
        require(len(symbols) == item.get("symbol_count"), f"symbol count mismatch: {rel_path}")
        require(first_session == item.get("first_session"), f"first session mismatch: {rel_path}")
        require(last_session == item.get("last_session"), f"last session mismatch: {rel_path}")

        receipt_parts.append(
            {
                "path": rel_path,
                "url": item.get("url"),
                "period": period,
                "part_index": part_index,
                "part_count_for_period": part_count,
                "size_bytes": actual_size,
                "sha256": actual_sha256,
                "row_count": actual_rows,
                "symbol_count": len(symbols),
                "first_session": first_session,
                "last_session": last_session,
                "status": "PASS",
            }
        )
        part_paths.add(rel_path)
        order_keys.append((period, part_index))
        total_rows += actual_rows

    require(common_header is not None, "no common header found")
    require(order_keys == sorted(order_keys), "text parts are not in ascending order")
    require(len(order_keys) == len(set(order_keys)), "duplicate period and part index")

    reconstructed = hashlib.sha256()
    reconstructed.update(common_header)
    reconstructed_size = len(common_header)
    for _, line in sorted(canonical_rows, key=lambda row: row[0]):
        reconstructed.update(line)
        reconstructed_size += len(line)
    reconstructed_sha256 = reconstructed.hexdigest()

    require(total_rows == reconstruction.get("expected_row_count"), "reconstructed row count mismatch")
    require(total_rows == canonical_text.get("row_count"), "canonical_text row count mismatch")
    require(total_rows == manifest.get("row_count"), "manifest row count mismatch")
    require(reconstructed_size == reconstruction.get("expected_size_bytes"), "reconstructed size mismatch")
    require(reconstructed_size == canonical_text.get("size_bytes"), "canonical_text size mismatch")
    require(reconstructed_sha256 == reconstruction.get("expected_sha256"), "reconstructed SHA256 mismatch")
    require(reconstructed_sha256 == canonical_text.get("sha256"), "canonical_text SHA256 mismatch")

    canonical_file = manifest.get("canonical_file", {})
    gzip_path = root / str(canonical_file.get("name"))
    require(gzip_path.is_file(), "canonical gzip is missing")
    gzip_sha256 = sha256_file(gzip_path)
    gzip_size = gzip_path.stat().st_size
    require(gzip_sha256 == canonical_file.get("sha256"), "canonical gzip SHA256 mismatch")
    require(gzip_size == canonical_file.get("bytes"), "canonical gzip size mismatch")
    gzip_text_digest = hashlib.sha256()
    gzip_text_size = 0
    try:
        with gzip.open(gzip_path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                gzip_text_digest.update(chunk)
                gzip_text_size += len(chunk)
    except OSError as error:
        raise VerificationError(f"canonical gzip is unreadable: {error}") from error
    gzip_text_sha256 = gzip_text_digest.hexdigest()
    require(gzip_text_size == reconstructed_size, "canonical gzip text size mismatch")
    require(gzip_text_sha256 == reconstructed_sha256, "canonical gzip text SHA256 mismatch")

    return {
        "receipt_schema_version": "smip-repository-integrity-receipt-v1",
        "verification_type": "repository-side integrity attestation",
        "status": "PASS",
        "repository": context.repository,
        "commit_sha": context.commit_sha,
        "executed_at": context.executed_at,
        "workflow": {
            "name": context.workflow_name,
            "run_id": context.workflow_run_id,
            "run_attempt": context.workflow_run_attempt,
            "ref": context.workflow_ref,
            "url": context.workflow_url,
        },
        "manifest": {
            "path": "manifest.json",
            "sha256": sha256_bytes(manifest_payload),
            "dataset_version": manifest.get("dataset_version"),
            "generated_at": manifest.get("generated_at"),
            "first_session": manifest.get("first_session"),
            "last_session": manifest.get("last_session"),
            "row_count": manifest.get("row_count"),
            "symbol_count": manifest.get("symbol_count"),
            "leakage_policy_status": leakage.get("status"),
        },
        "canonical_gzip": {
            "path": canonical_file.get("name"),
            "size_bytes": gzip_size,
            "sha256": gzip_sha256,
            "decompressed_size_bytes": gzip_text_size,
            "decompressed_sha256": gzip_text_sha256,
            "status": "PASS",
        },
        "text_parts": {
            "count": len(receipt_parts),
            "period_count": len({part["period"] for part in receipt_parts}),
            "total_row_count": total_rows,
            "max_part_size_bytes": max(part["size_bytes"] for part in receipt_parts),
            "all_parts_status": "PASS",
            "items": receipt_parts,
        },
        "reconstruction": {
            "sort_order": reconstruction.get("canonical_row_order"),
            "line_ending": canonical_text.get("line_ending"),
            "size_bytes": reconstructed_size,
            "row_count": total_rows,
            "sha256": reconstructed_sha256,
            "last_session": manifest.get("last_session"),
            "status": "PASS",
        },
        "policy": {
            "fail_closed": True,
            "consumer_side_byte_verification": "NOT_PERFORMED",
            "automatic_update_enabled": False,
            "research_state_changed": False,
            "a_s_l_changed": False,
            "discovery_only_started": False,
            "research_judgment": "NO_PROVEN_EDGE",
        },
    }


def write_receipt_atomic(receipt: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(payload)
    os.replace(temp_path, output)


def context_from_environment() -> WorkflowContext:
    repository = os.environ.get("GITHUB_REPOSITORY", "local/smip-canonical-data")
    commit_sha = os.environ.get("GITHUB_SHA", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    workflow_name = os.environ.get("GITHUB_WORKFLOW", "local repository integrity verification")
    workflow_ref = os.environ.get("GITHUB_REF", "local")
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    workflow_url = f"{server_url}/{repository}/actions/runs/{run_id}" if run_id != "0" else "local"
    executed_at = os.environ.get("SMIP_VERIFICATION_EXECUTED_AT") or datetime.now(timezone.utc).isoformat()
    return WorkflowContext(
        repository=repository,
        commit_sha=commit_sha,
        workflow_name=workflow_name,
        workflow_run_id=run_id,
        workflow_run_attempt=attempt,
        workflow_ref=workflow_ref,
        workflow_url=workflow_url,
        executed_at=executed_at,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify SMIP repository snapshot and emit a PASS-only receipt")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("repository-integrity-receipt.json"))
    args = parser.parse_args()

    try:
        receipt = build_receipt(args.root.resolve(), context_from_environment())
        write_receipt_atomic(receipt, args.output.resolve())
    except VerificationError as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from error

    print(
        json.dumps(
            {
                "status": receipt["status"],
                "commit_sha": receipt["commit_sha"],
                "workflow_run_id": receipt["workflow"]["run_id"],
                "parts": receipt["text_parts"]["count"],
                "rows": receipt["reconstruction"]["row_count"],
                "last_session": receipt["reconstruction"]["last_session"],
                "canonical_text_sha256": receipt["reconstruction"]["sha256"],
                "receipt_path": str(args.output.resolve()),
                "receipt_sha256": sha256_file(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
