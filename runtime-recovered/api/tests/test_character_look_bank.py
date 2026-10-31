from pathlib import Path

from atnos.production.character_look_bank import install_all, kit_status


def test_install_character_look_banks(tmp_path, monkeypatch):
    from atnos.core.config import Settings

    settings = Settings(data_dir=tmp_path / "data")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Point install at real config kits shipped with the repo.
    installed = install_all(settings)
    assert {item["kit_key"] for item in installed} == {
        "wealth-blueprint-alex-01",
        "archipelag-snow-v1",
    }
    wealth = kit_status(settings, "wealth-blueprint-alex-01")
    kids = kit_status(settings, "archipelag-snow-v1")
    assert wealth["status"] == "ready_for_training"
    assert kids["channel"] == "children_stories"
    assert wealth["slots_total"] >= 10
    assert kids["slots_approved"] == 0
    assert (settings.data_dir / "brand-kits/archipelag-snow-v1/characters/niko.json").is_file()
    assert (settings.data_dir / "brand-kits/wealth-blueprint-alex-01/training/look-bank-manifest.json").is_file()
