"""Izolacja testów od aktywnego runtime Atnosa na komputerze operatora."""

from __future__ import annotations

import os
from pathlib import Path
import re

import pytest


_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def _read_operator_environment_values() -> dict[str, str]:
    """Zapamiętuje wartości operatora bez udostępniania ich testom ani logom."""
    values = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("ATNOS_") or name in {"HF_HOME", "OLLAMA_MODELS"}
    }
    candidates: list[Path] = []
    configured = str(os.environ.get("ATNOS_ENV_FILE") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    for name in ("DATA_DIR", "ATNOS_STATE_DIR"):
        configured_data = str(os.environ.get(name) or "").strip()
        if configured_data:
            candidates.append(Path(configured_data).expanduser().parent / "config" / ".env")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8-sig").splitlines():
            match = _ENV_LINE.match(line)
            if not match:
                continue
            raw = match.group(2).strip().strip('"').strip("'")
            if raw:
                values[match.group(1)] = raw
    return values


_OPERATOR_ENVIRONMENT_VALUES = _read_operator_environment_values()


def _operator_runtime_roots() -> tuple[Path, ...]:
    """Zwraca katalogi danych operatora bez ujawniania ich zawartości."""
    roots: list[Path] = []
    for name in ("ATNOS_STATE_DIR", "DATA_DIR"):
        value = _OPERATOR_ENVIRONMENT_VALUES.get(name)
        if value:
            roots.append(Path(value).expanduser().resolve())
    env_file = _OPERATOR_ENVIRONMENT_VALUES.get("ATNOS_ENV_FILE")
    if env_file:
        roots.append(Path(env_file).expanduser().resolve().parent.parent)
    return tuple(dict.fromkeys(roots))


_OPERATOR_RUNTIME_ROOTS = _operator_runtime_roots()


def _remove_operator_runtime_environment() -> None:
    """Nie pozwala testom jednostkowym czytać lokalnych danych i blokad runtime."""
    from atnos.core.config import Settings

    settings_names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        settings_names.add(field_name.upper())
        alias = field.validation_alias
        if isinstance(alias, str):
            settings_names.add(alias)
        else:
            for choice in getattr(alias, "choices", ()):
                if isinstance(choice, str):
                    settings_names.add(choice)

    inherited_names = tuple(os.environ)
    for name in inherited_names:
        if (
            name.startswith("ATNOS_")
            or name in settings_names
            or name in {"HF_HOME", "OLLAMA_MODELS"}
        ):
            # Import Settings może wczytać zewnętrzny .env. Zachowujemy odcisk
            # tych wartości, aby usunąć je także po późniejszym reloadzie runtime.
            value = os.environ.get(name)
            if value:
                _OPERATOR_ENVIRONMENT_VALUES.setdefault(name, value)
            os.environ.pop(name, None)

    # Runtime readiness may call load_runtime_env_files() from inside an API
    # request. An explicit, missing file prevents that loader from discovering
    # the operator's real .env later in the same pytest process.
    sentinel = Path(__file__).resolve().parent / ".pytest-no-runtime-env"
    os.environ["ATNOS_ENV_FILE"] = str(sentinel)


def _remove_reloaded_operator_values() -> None:
    """Usuwa tylko realne wartości operatora ponownie wczytane między testami."""
    for name, value in tuple(os.environ.items()):
        if _OPERATOR_ENVIRONMENT_VALUES.get(name) == value:
            os.environ.pop(name, None)
            continue
        if not any(token in name for token in ("DIR", "FILE", "PATH", "ROOT")):
            continue
        try:
            resolved = Path(value).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if any(resolved == root or resolved.is_relative_to(root) for root in _OPERATOR_RUNTIME_ROOTS):
            os.environ.pop(name, None)


# conftest jest ładowany przed kolekcją modułów testowych, więc izolacja obejmuje
# również ustawienia tworzone na poziomie modułu, przed uruchomieniem fixture'ów.
_remove_operator_runtime_environment()


@pytest.fixture(autouse=True)
def _clear_cached_settings_between_tests():
    """Każdy test buduje ustawienia niezależnie od poprzedniego testu."""
    from atnos.core.config import get_settings

    # Część testów uruchamia wątki lub klienty aplikacji, które mogą zakończyć
    # pracę już po teardown własnego testu i ponownie wczytać środowisko runtime.
    # Nie wolno jednak usuwać wartości ustawionych celowo przez setUpClass.
    _remove_reloaded_operator_values()
    environment_before_test = dict(os.environ)
    get_settings.cache_clear()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(environment_before_test)
        _remove_reloaded_operator_values()
        get_settings.cache_clear()
