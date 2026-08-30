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
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


SOURCE_MANIFEST_URL = "https://smip-server.onrender.com/research/manifest.json"
SOURCE_CANONICAL_URL = "https://smip-server.onrender.com/research/canonical-daily.csv.gz"
EXPECTED_SCHEMA = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value_traded",
    "trades_count",
    "is_final",
    "partial",
    "source_layer",
    "source_run_id",
    "source_file_sha256",
]
HEX64 = re.compile(r"[0-9a-f]{64}")
ISO_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanonicalIndex:
    rows: dict[tuple[str, str], bytes]
    first_session: str
    last_session: str
    symbols: set[str]
    sessions: set[str]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise UpdateError(message)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_timestamp(value: Any, field: str) -> datetime:
    require(isinstance(value, str) and value, f"{field} is missing")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise UpdateError(f"{field} is not ISO-8601") from error


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
        manifest = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError(f"invalid manifest: {error}") from error
    require(isinstance(manifest, dict), "manifest root must be an object")
    return manifest


def validate_source_contract(manifest: dict[str, Any], canonical_path: Path) -> None:
    require(manifest.get("auth_scope") == "research-read-only", "source auth scope is not research-read-only")
    require(manifest.get("schema") == EXPECTED_SCHEMA, "source schema changed")
    require(manifest.get("first_session") == "2019-01-01", "source first_session changed")
    require(ISO_DATE.fullmatch(str(manifest.get("last_session"))) is not None, "invalid source last_session")
    require(isinstance(manifest.get("row_count"), int) and manifest["row_count"] > 0, "invalid source row_count")
    require(isinstance(manifest.get("symbol_count"), int) and manifest["symbol_count"] > 0, "invalid source symbol_count")
    require(isinstance(manifest.get("session_count"), int) and manifest["session_count"] > 0, "invalid source session_count")
    require(str(manifest.get("dataset_version", "")).startswith("smip-canonical-daily-v1-"), "invalid dataset_version")
    parse_timestamp(manifest.get("generated_at"), "generated_at")

    leakage = manifest.get("leakage_policy")
    require(isinstance(leakage, dict), "source leakage policy is missing")
    require(leakage.get("status") == "PASS", "source leakage policy is not PASS")
    require(leakage.get("future_derived_columns_included") == [], "future-derived columns are present")
    require(leakage.get("adjusted_close_included") is False, "adjusted close is present")
    require(leakage.get("same_session_observables_only") is True, "same-session-only policy is not asserted")

    completeness = manifest.get("completeness")
    require(isinstance(completeness, dict), "source completeness block is missing")
    require(completeness.get("gap_fill_applied") is False, "source reports gap filling")
    require(completeness.get("historical_prices_recalculated") is False, "source reports historical recalculation")
    require(completeness.get("conflicting_duplicate_rows") == 0, "source reports conflicting duplicate rows")

    canonical = manifest.get("canonical_file")
    require(isinstance(canonical, dict), "source canonical_file is missing")
    require(canonical.get("name") == "canonical-daily.csv.gz", "unexpected canonical filename")
    require(canonical.get("media_type") == "application/gzip", "unexpected canonical media type")
    require(HEX64.fullmatch(str(canonical.get("sha256"))) is not None, "invalid canonical SHA256")
    require(canonical_path.is_file(), "downloaded canonical file is missing")
    require(canonical_path.stat().st_size == canonical.get("bytes"), "downloaded canonical size mismatch")
    require(sha256_file(canonical_path) == canonical.get("sha256"), "downloaded canonical SHA256 mismatch")


def index_canonical(path: Path, schema: list[str]) -> CanonicalIndex:
    expected_header = (",".join(schema) + "\n").encode("utf-8")
    rows: dict[tuple[str, str], bytes] = {}
    symbols: set[str] = set()
    sessions: set[str] = set()
    first_session: str | None = None
    last_session: str | None = None
    previous_key: tuple[str, str] | None = None

    try:
        with gzip.open(path, "rb") as handle:
            header = handle.readline()
            require(header == expected_header, "canonical header differs from schema")
            for line_number, line in enumerate(handle, start=2):
                require(line.endswith(b"\n") and b"\r" not in line, f"invalid line ending at row {line_number}")
                try:
                    columns = next(csv.reader(io.StringIO(line.decode("utf-8").rstrip("\n")), strict=True))
                except (UnicodeDecodeError, csv.Error, StopIteration) as error:
                    raise UpdateError(f"invalid canonical CSV row {line_number}: {error}") from error
                require(len(columns) == len(schema), f"canonical column count mismatch at row {line_number}")
                symbol, session = columns[0], columns[1]
                require(symbol != "" and ISO_DATE.fullmatch(session) is not None, f"invalid canonical key at row {line_number}")
                key = (symbol, session)
                require(previous_key is None or key > previous_key, f"canonical order or duplicate error at row {line_number}")
                rows[key] = line
                symbols.add(symbol)
                sessions.add(session)
                first_session = session if first_session is None else min(first_session, session)
                last_session = session if last_session is None else max(last_session, session)
                previous_key = key
    except OSError as error:
        raise UpdateError(f"canonical gzip is unreadable: {error}") from error

    require(bool(rows) and first_session is not None and last_session is not None, "canonical dataset is empty")
    return CanonicalIndex(rows, first_session, last_session, symbols, sessions)


def validate_index_against_manifest(index: CanonicalIndex, manifest: dict[str, Any]) -> None:
    require(len(index.rows) == manifest.get("row_count"), "canonical row_count differs from manifest")
    require(len(index.symbols) == manifest.get("symbol_count"), "canonical symbol_count differs from manifest")
    require(len(index.sessions) == manifest.get("session_count"), "canonical session_count differs from manifest")
    require(index.first_session == manifest.get("first_session"), "canonical first_session differs from manifest")
    require(index.last_session == manifest.get("last_session"), "canonical last_session differs from manifest")


def determine_update(
    current_manifest: dict[str, Any],
    current_index: CanonicalIndex,
    source_manifest: dict[str, Any],
    source_index: CanonicalIndex,
) -> tuple[bool, int]:
    current_canonical = current_manifest.get("canonical_file", {})
    source_canonical = source_manifest.get("canonical_file", {})
    current_sha = current_canonical.get("sha256")
    source_sha = source_canonical.get("sha256")

    if source_sha == current_sha:
        require(source_manifest.get("last_session") == current_manifest.get("last_session"), "same canonical SHA has a different last_session")
        require(source_manifest.get("row_count") == current_manifest.get("row_count"), "same canonical SHA has a different row_count")
        return False, 0

    current_last = str(current_manifest.get("last_session"))
    source_last = str(source_manifest.get("last_session"))
    require(source_last > current_last, "source differs but is not newer than the current snapshot")
    require(parse_timestamp(source_manifest.get("generated_at"), "source generated_at") > parse_timestamp(current_manifest.get("generated_at"), "current generated_at"), "new source generated_at is not newer")

    for key, row in current_index.rows.items():
        require(source_index.rows.get(key) == row, f"historical row changed or disappeared: {key[0]},{key[1]}")

    new_rows = 0
    for key in source_index.rows:
        if key not in current_index.rows:
            require(key[1] > current_last, f"historical backfill is not allowed: {key[0]},{key[1]}")
            new_rows += 1

    require(new_rows > 0, "new last_session has no new rows")
    require(len(source_index.rows) == len(current_index.rows) + new_rows, "source row delta is inconsistent")
    return True, new_rows


def download(url: str, destination: Path, max_bytes: int) -> None:
    require(url in {SOURCE_MANIFEST_URL, SOURCE_CANONICAL_URL}, "source URL is not allow-listed")
    request = urllib.request.Request(url, headers={"User-Agent": "smip-canonical-updater/1"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response, destination.open("wb") as output:
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                require(total <= max_bytes, f"download exceeds limit: {url}")
                output.write(chunk)
    except (OSError, urllib.error.URLError) as error:
        raise UpdateError(f"source download failed: {url}: {error}") from error


def run_script(script: Path, *args: str, cwd: Path, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(completed.stdout, end="")
    require(completed.returncode == 0, f"script failed: {script.name}")


def stage_snapshot(
    root: Path,
    source_manifest_path: Path,
    source_canonical_path: Path,
    source_manifest_sha256: str,
) -> Path:
    staging = Path(tempfile.mkdtemp(prefix="smip-canonical-stage-"))
    (staging / "scripts").mkdir()
    for name in ("build_text_parts.py", "validate_text_parts.py", "verify_repository_integrity.py"):
        shutil.copy2(root / "scripts" / name, staging / "scripts" / name)
    shutil.copy2(source_manifest_path, staging / "manifest.json")
    shutil.copy2(source_canonical_path, staging / "canonical-daily.csv.gz")

    run_script(staging / "scripts/build_text_parts.py", cwd=staging)
    manifest = load_manifest(staging / "manifest.json")
    manifest["text_parts_reconstruction"]["automatic_update_enabled"] = True
    manifest["automatic_update"] = {
        "enabled": True,
        "mode": "github-actions-public-pull",
        "source_manifest_url": SOURCE_MANIFEST_URL,
        "source_canonical_url": SOURCE_CANONICAL_URL,
        "source_manifest_sha256": source_manifest_sha256,
        "source_dataset_version": manifest.get("dataset_version"),
        "source_generated_at": manifest.get("generated_at"),
        "source_last_session": manifest.get("last_session"),
        "schedule": {
            "timezone": "Asia/Riyadh",
            "trading_days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"],
            "scheduled_times_local": ["18:45", "18:55"],
            "cron_utc": ["45 15 * * 0-4", "55 15 * * 0-4"],
        },
        "no_change_is_idempotent": True,
        "fail_closed": True,
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    run_script(staging / "scripts/validate_text_parts.py", cwd=staging)
    env = os.environ.copy()
    env.update(
        {
            "GITHUB_REPOSITORY": "waleed1971-lab/smip-canonical-data",
            "GITHUB_SHA": "0" * 40,
            "GITHUB_RUN_ID": "0",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW": "pre-commit automatic snapshot verification",
            "GITHUB_REF": "refs/heads/main",
        }
    )
    run_script(
        staging / "scripts/verify_repository_integrity.py",
        "--root",
        str(staging),
        "--output",
        str(staging / "precommit-receipt.json"),
        cwd=staging,
        env=env,
    )
    return staging


def apply_staged_snapshot(root: Path, staging: Path) -> None:
    staged_parts = staging / "parts"
    require(staged_parts.is_dir(), "staged parts directory is missing")
    replacement_parts = root / ".parts-automatic-update"
    if replacement_parts.exists():
        shutil.rmtree(replacement_parts)
    shutil.copytree(staged_parts, replacement_parts)

    for name in ("manifest.json", "canonical-daily.csv.gz"):
        temp_target = root / f".{name}.automatic-update"
        shutil.copy2(staging / name, temp_target)
        os.replace(temp_target, root / name)

    current_parts = root / "parts"
    if current_parts.exists():
        shutil.rmtree(current_parts)
    os.replace(replacement_parts, current_parts)


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_github_output(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"changed={str(bool(payload['changed'])).lower()}\n")
        handle.write(f"change_type={payload['change_type']}\n")
        handle.write(f"source_session={payload['source_last_session']}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Update SMIP canonical repository from the public read-only source")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-manifest-file", type=Path)
    parser.add_argument("--source-canonical-file", type=Path)
    parser.add_argument("--result", type=Path, default=Path("automatic-update-result.json"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    result_path = args.result.resolve()
    try:
        current_manifest = load_manifest(root / "manifest.json")
        current_index = index_canonical(root / "canonical-daily.csv.gz", current_manifest.get("schema"))
        validate_index_against_manifest(current_index, current_manifest)

        with tempfile.TemporaryDirectory(prefix="smip-public-source-") as temp_dir:
            temp = Path(temp_dir)
            source_manifest_path = temp / "manifest.json"
            source_canonical_path = temp / "canonical-daily.csv.gz"
            if args.source_manifest_file or args.source_canonical_file:
                require(bool(args.source_manifest_file and args.source_canonical_file), "both source files are required")
                shutil.copy2(args.source_manifest_file, source_manifest_path)
                shutil.copy2(args.source_canonical_file, source_canonical_path)
            else:
                download(SOURCE_MANIFEST_URL, source_manifest_path, 2_000_000)
                download(SOURCE_CANONICAL_URL, source_canonical_path, 100_000_000)

            source_manifest = load_manifest(source_manifest_path)
            validate_source_contract(source_manifest, source_canonical_path)
            source_index = index_canonical(source_canonical_path, source_manifest.get("schema"))
            validate_index_against_manifest(source_index, source_manifest)
            data_changed, new_rows = determine_update(current_manifest, current_index, source_manifest, source_index)
            enablement_required = (
                current_manifest.get("text_parts_reconstruction", {}).get("automatic_update_enabled") is not True
            )
            changed = data_changed or enablement_required
            change_type = "data" if data_changed else "enablement" if enablement_required else "none"
            source_manifest_sha256 = sha256_file(source_manifest_path)

            if changed:
                staging = stage_snapshot(root, source_manifest_path, source_canonical_path, source_manifest_sha256)
                try:
                    apply_staged_snapshot(root, staging)
                finally:
                    shutil.rmtree(staging, ignore_errors=True)

            result = {
                "status": "PASS",
                "changed": changed,
                "change_type": change_type,
                "current_last_session": current_manifest.get("last_session"),
                "source_last_session": source_manifest.get("last_session"),
                "source_generated_at": source_manifest.get("generated_at"),
                "source_dataset_version": source_manifest.get("dataset_version"),
                "source_manifest_sha256": source_manifest_sha256,
                "source_canonical_sha256": source_manifest.get("canonical_file", {}).get("sha256"),
                "source_row_count": source_manifest.get("row_count"),
                "new_rows": new_rows,
                "automatic_update_enabled": True,
                "fail_closed": True,
            }
            write_result(result_path, result)
            if args.github_output:
                write_github_output(args.github_output.resolve(), result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except UpdateError as error:
        failure = {"status": "FAIL", "changed": False, "fail_closed": True, "error": str(error)}
        write_result(result_path, failure)
        print(json.dumps(failure, ensure_ascii=False))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
