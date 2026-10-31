from __future__ import annotations

import os
from pathlib import Path


WINDOWS_ATNOS_PROFILE_ROOT = Path("E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos")
WINDOWS_ATNOS_REPO_ROOT = Path("E:/Atnos/App")
WINDOWS_ATNOS_KNOWLEDGE_ROOT = Path("E:/Atnos/Knowledge")

LEGACY_ATNOS_PATH_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("F:\\Apps\\Atnos", "E:\\Atnos\\App"),
    ("F:/Apps/Atnos", "E:/Atnos/App"),
    (
        "F:\\AtnosSystem\\AppData\\Roaming\\Atnos",
        "E:\\Atnos\\Profiles\\AtnosSystem\\AppData\\Roaming\\Atnos",
    ),
    (
        "F:/AtnosSystem/AppData/Roaming/Atnos",
        "E:/Atnos/Profiles/AtnosSystem/AppData/Roaming/Atnos",
    ),
    ("F:\\obsydian\\atnos", "E:\\Atnos\\Knowledge\\Obsidian\\Atnos-Brain"),
    ("F:/obsydian/atnos", "E:/Atnos/Knowledge/Obsidian/Atnos-Brain"),
    ("F:\\Comfy-Desktop", "E:\\Atnos\\Tools\\Comfy-Desktop"),
    ("F:/Comfy-Desktop", "E:/Atnos/Tools/Comfy-Desktop"),
    ("F:\\AtnosModels\\ollama", "E:\\Atnos\\Models\\ollama"),
    ("F:/AtnosModels/ollama", "E:/Atnos/Models/ollama"),
    ("F:\\AtnosLibrary", "E:\\Atnos\\Knowledge\\Library"),
    ("F:/AtnosLibrary", "E:/Atnos/Knowledge/Library"),
    ("F:\\Apps\\whispercpp", "E:\\Atnos\\Tools\\whispercpp"),
    ("F:/Apps/whispercpp", "E:/Atnos/Tools/whispercpp"),
    (
        "D:\\Atnos\\Data",
        "E:\\Atnos\\Storage\\Data",
    ),
    ("D:/Atnos/Data", "E:/Atnos/Storage/Data"),
    ("D:\\AtnosLearningRuns", "E:\\Atnos\\LearningRuns"),
    ("D:/AtnosLearningRuns", "E:/Atnos/LearningRuns"),
    ("D:\\AtnosColdData", "E:\\Atnos\\ColdData"),
    ("D:/AtnosColdData", "E:/Atnos/ColdData"),
    ("index on F:", "index in the canonical E: data directory"),
    ("roots on F:", "roots on E:"),
)


def normalize_atnos_legacy_paths(value: str) -> str:
    """Replace retired Atnos locations before text becomes operational context."""
    normalized = value
    for legacy_path, canonical_path in LEGACY_ATNOS_PATH_REPLACEMENTS:
        normalized = normalized.replace(legacy_path, canonical_path)
    return normalized


def atnos_state_dir() -> Path:
    """Return the canonical Atnos data directory outside the repository."""
    for variable in ("ATNOS_STATE_DIR", "DATA_DIR"):
        configured = os.getenv(variable, "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return (WINDOWS_ATNOS_PROFILE_ROOT / "data").resolve()
    return (Path.home() / ".local" / "share" / "atnos").resolve()


def atnos_profile_root() -> Path:
    """Return the canonical runtime profile without legacy F: compatibility paths."""
    configured = os.getenv("ATNOS_PROFILE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return WINDOWS_ATNOS_PROFILE_ROOT.resolve()
    return atnos_state_dir().parent.resolve()


def atnos_repo_root() -> Path:
    """Return the canonical repository; junctions are migration-only entry points."""
    configured = os.getenv("ATNOS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return WINDOWS_ATNOS_REPO_ROOT.resolve()
    return Path.cwd().resolve()


def atnos_knowledge_root() -> Path:
    """Return the canonical knowledge directory independent of historical notes."""
    configured = os.getenv("ATNOS_KNOWLEDGE_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return WINDOWS_ATNOS_KNOWLEDGE_ROOT.resolve()
    return (atnos_state_dir() / "knowledge").resolve()
