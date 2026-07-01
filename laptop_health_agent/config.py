from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


IS_BUNDLED = bool(getattr(sys, "frozen", False))
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
CONFIG_PATH = ROOT / "config" / "defaults.json"
DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home())
    / "LaptopHealthAgent"
    if IS_BUNDLED
    else ROOT / "data"
)
LOG_PATH = DATA_DIR / "activity.jsonl"


@dataclass(frozen=True)
class AgentConfig:
    storage_roots: tuple[Path, ...]
    protected_paths: tuple[Path, ...]
    process_whitelist: frozenset[str]
    large_file_mb: int
    old_file_days: int
    scan_max_files_per_root: int
    duplicate_scan_max_files: int
    duplicate_hash_max_mb: int


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser().resolve()


def load_config() -> AgentConfig:
    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    return AgentConfig(
        storage_roots=tuple(_expand_path(path) for path in raw["storage_roots"]),
        protected_paths=tuple(_expand_path(path) for path in raw["protected_paths"]),
        process_whitelist=frozenset(raw["process_whitelist"]),
        large_file_mb=int(raw["large_file_mb"]),
        old_file_days=int(raw["old_file_days"]),
        scan_max_files_per_root=int(raw["scan_max_files_per_root"]),
        duplicate_scan_max_files=int(raw["duplicate_scan_max_files"]),
        duplicate_hash_max_mb=int(raw["duplicate_hash_max_mb"]),
    )


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
