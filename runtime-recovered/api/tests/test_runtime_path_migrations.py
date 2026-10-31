from __future__ import annotations

import json
from pathlib import Path

from atnos.core.config import Settings
from atnos.core.paths import normalize_atnos_legacy_paths
from atnos.jarvis.core.config import JarvisConfigStore
from atnos.services.asset_dedupe_service import AssetDedupeService


def _settings(root: Path) -> Settings:
    return Settings(app_root=root / "app", data_dir=root / "data", _env_file=root / ".env")


def test_watch_folder_migration_rebases_only_obsolete_atnos_uploads(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = JarvisConfigStore(settings)
    store.paths.root_dir.mkdir(parents=True)
    store.paths.watch_folders_path.write_text(
        json.dumps(
            {
                "watch_folders": [
                    "F:/AtnosSystem/AppData/Roaming/Atnos/data/uploads",
                    "C:/Users/example/Downloads",
                ]
            }
        ),
        encoding="utf-8",
    )

    store.ensure_layout()

    payload = json.loads(store.paths.watch_folders_path.read_text(encoding="utf-8"))
    assert payload["watch_folders"] == [
        settings.uploads_dir.as_posix(),
        "C:/Users/example/Downloads",
    ]


def test_asset_catalog_migration_rebases_old_data_root(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = AssetDedupeService(settings)
    service.catalog_path.write_text(
        json.dumps(
            {
                "key": {
                    "storage_path": "F:/AtnosSystem/AppData/Roaming/Atnos/data/atnos-outputs/1/clip.mp4"
                }
            }
        ),
        encoding="utf-8",
    )

    catalog = service._load_catalog()

    assert catalog["key"]["storage_path"] == (
        settings.data_dir / "atnos-outputs" / "1" / "clip.mp4"
    ).resolve().as_posix()


def test_asset_catalog_migration_keeps_external_paths_unchanged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    service = AssetDedupeService(settings)
    external = "D:/ExternalAssets/clip.mp4"
    service.catalog_path.write_text(
        json.dumps({"key": {"storage_path": external}}),
        encoding="utf-8",
    )

    catalog = service._load_catalog()

    assert catalog["key"]["storage_path"] == external


def test_runtime_path_normalizer_rebases_known_legacy_storage_roots() -> None:
    source = (
        "D:/Atnos/Data/atnos-outputs/5/final.mp4 | "
        "F:/AtnosLibrary/curated/report.md | "
        "F:/Apps/whispercpp/bin/Release/whisper-cli.exe"
    )

    assert normalize_atnos_legacy_paths(source) == (
        "E:/Atnos/Storage/Data/atnos-outputs/5/final.mp4 | "
        "E:/Atnos/Knowledge/Library/curated/report.md | "
        "E:/Atnos/Tools/whispercpp/bin/Release/whisper-cli.exe"
    )
