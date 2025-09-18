#!/usr/bin/env python3
"""Lean check orchestrator for InitialJsonConvert workflows."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from LeanCheck.parallel_build_checker import run_parallel_build_check  # type: ignore


def _copy_selected_files(stems: Iterable[str], mapping: List[Dict[str, Any]], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    stem_set = set(stems)
    for entry in mapping:
        if entry["stem"] in stem_set:
            src = Path(entry["path"])
            if src.exists():
                shutil.copy2(src, dest / entry["filename"])


def _run_group(
    *,
    label: str,
    lean_dir: Path,
    mapping: List[Dict[str, Any]],
    log_root: Path,
    success_root: Path,
    failed_root: Path,
    max_workers: int,
) -> Dict[str, Any]:
    group_log_dir = log_root / label
    group_log_dir.mkdir(parents=True, exist_ok=True)
    success_group_dir = success_root / label
    failed_group_dir = failed_root / label
    success_group_dir.mkdir(parents=True, exist_ok=True)
    failed_group_dir.mkdir(parents=True, exist_ok=True)

    summary = run_parallel_build_check(
        lean_dir,
        group_log_dir,
        max_workers=max_workers,
        pattern="*.lean",
    )

    success_ids = set(summary.get("successful_blocks", []))
    failed_ids = set(summary.get("failed_blocks", []))

    _copy_selected_files(success_ids, mapping, success_group_dir)
    _copy_selected_files(failed_ids, mapping, failed_group_dir)

    total = len(mapping)
    success_count = len(success_ids)
    failed_count = len(failed_ids)
    success_rate = summary.get("success_rate", 0.0)
    failed_rate = 100.0 - success_rate if total else 0.0

    return {
        "label": label,
        "total": total,
        "success": success_count,
        "failed": failed_count,
        "success_rate": success_rate,
        "failed_rate": failed_rate,
        "error_count": failed_count,
        "error_rate": failed_rate,
        "failed_ids": sorted(failed_ids),
        "success_ids": sorted(success_ids),
        "logs_dir": str(group_log_dir),
        "summary_file": str(group_log_dir / "build_summary.json"),
        "success_dir": str(success_group_dir),
        "failed_dir": str(failed_group_dir),
    }


def run_leancheck(
    groups: List[Dict[str, Any]],
    *,
    session_name: str,
    base_dir: Path,
    max_workers: int = 50,
) -> Dict[str, Any]:
    """Execute Lean build checks for provided groups."""
    if not groups:
        return {"enabled": False, "reason": "No Lean groups provided"}

    total_files = sum(len(g.get("mapping", [])) for g in groups)
    if total_files == 0:
        return {"enabled": False, "reason": "No Lean files to check"}

    base_dir = Path(base_dir)
    lean_check_dir = base_dir / "lean_check"
    log_root = lean_check_dir / "logs"
    success_root = lean_check_dir / "success"
    failed_root = lean_check_dir / "failed"
    lean_check_dir.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    success_root.mkdir(parents=True, exist_ok=True)
    failed_root.mkdir(parents=True, exist_ok=True)

    group_summaries: List[Dict[str, Any]] = []
    aggregate_failed: List[str] = []
    aggregate_success = 0
    all_items: List[Dict[str, Any]] = []
    all_success_items: List[Dict[str, Any]] = []
    all_failed_items: List[Dict[str, Any]] = []

    for group in groups:
        label = group.get("label", "default")
        lean_dir = Path(group["lean_dir"])
        mapping = group.get("mapping", [])
        stem_lookup = {entry["stem"]: entry for entry in mapping}

        if not mapping:
            success_dir = success_root / label
            failed_dir = failed_root / label
            success_dir.mkdir(parents=True, exist_ok=True)
            failed_dir.mkdir(parents=True, exist_ok=True)
            group_summaries.append(
                {
                    "label": label,
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "success_rate": 0.0,
                    "failed_rate": 0.0,
                    "error_count": 0,
                    "error_rate": 0.0,
                    "failed_ids": [],
                    "success_ids": [],
                    "logs_dir": str(log_root / label),
                    "summary_file": None,
                    "success_dir": str(success_dir),
                    "failed_dir": str(failed_dir),
                    "group_json": None,
                    "success_json": None,
                    "failed_json": None,
                    "skipped": True,
                }
            )
            continue

        summary = _run_group(
            label=label,
            lean_dir=lean_dir,
            mapping=mapping,
            log_root=log_root,
            success_root=success_root,
            failed_root=failed_root,
            max_workers=max_workers,
        )

        success_items: List[Dict[str, Any]] = []
        failed_items: List[Dict[str, Any]] = []

        for stem in summary["success_ids"]:
            entry = stem_lookup.get(stem)
            if entry:
                success_items.append(entry["item"])
        for stem in summary["failed_ids"]:
            entry = stem_lookup.get(stem)
            if entry:
                failed_items.append(entry["item"])

        group_items = success_items + failed_items

        group_json = lean_check_dir / f"{label}.json"
        group_success_json = lean_check_dir / f"{label}_success.json"
        group_failed_json = lean_check_dir / f"{label}_failed.json"
        group_json.write_text(json.dumps(group_items, ensure_ascii=False, indent=2), encoding="utf-8")
        group_success_json.write_text(json.dumps(success_items, ensure_ascii=False, indent=2), encoding="utf-8")
        group_failed_json.write_text(json.dumps(failed_items, ensure_ascii=False, indent=2), encoding="utf-8")

        summary["group_json"] = str(group_json)
        summary["success_json"] = str(group_success_json)
        summary["failed_json"] = str(group_failed_json)

        group_summaries.append(summary)
        aggregate_failed.extend(summary.get("failed_ids", []))
        aggregate_success += summary.get("success", 0)
        all_items.extend(group_items)
        all_success_items.extend(success_items)
        all_failed_items.extend(failed_items)

    total_groups_files = sum(gs.get("total", 0) for gs in group_summaries)
    failed_total = len(set(aggregate_failed))
    failed_rate = (failed_total / total_groups_files * 100) if total_groups_files else 0.0
    success_total = aggregate_success
    success_rate = (success_total / total_groups_files * 100) if total_groups_files else 0.0

    all_json_path = lean_check_dir / "all_difficult.json"
    all_success_path = lean_check_dir / "all_difficult_success.json"
    all_failed_path = lean_check_dir / "all_difficult_failed.json"
    all_json_path.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    all_success_path.write_text(json.dumps(all_success_items, ensure_ascii=False, indent=2), encoding="utf-8")
    all_failed_path.write_text(json.dumps(all_failed_items, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "enabled": True,
        "session_name": session_name,
        "total": total_groups_files,
        "success": success_total,
        "failed": failed_total,
        "success_rate": success_rate,
        "failed_rate": failed_rate,
        "error_count": failed_total,
        "error_rate": failed_rate,
        "failed_ids": sorted(set(aggregate_failed)),
        "logs_root": str(log_root),
        "lean_check_dir": str(lean_check_dir),
        "success_root": str(success_root),
        "failed_root": str(failed_root),
        "all_json": str(all_json_path),
        "all_success_json": str(all_success_path),
        "all_failed_json": str(all_failed_path),
        "groups": group_summaries,
    }
