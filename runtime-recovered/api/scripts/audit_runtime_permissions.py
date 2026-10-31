from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from urllib.request import urlopen


def _get_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=20) as response:
        return json.load(response)


def _availability(available: bool) -> str:
    return "dostepny" if available else "brakujacy"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an evidence-backed Atnos permission matrix.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    local_host = _get_json(f"{args.api}/local-host/capabilities")
    gmail = _get_json(f"{args.api}/connectors/gmail/status")
    youtube = _get_json(f"{args.api}/connectors/youtube/status")
    xtb = _get_json(f"{args.api}/api/investment/market-data/xtb-demo/status")
    secrets = _get_json(f"{args.api}/accounts/storage-status")

    db_path = Path(args.database).expanduser().resolve()
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        execution_policy = dict(connection.execute("SELECT * FROM execution_policies ORDER BY id LIMIT 1").fetchone())
        runtime_controls = dict(
            connection.execute("SELECT * FROM runtime_control_settings ORDER BY id LIMIT 1").fetchone()
        )
    finally:
        connection.close()

    gmail_available = bool(
        gmail.get("connected")
        and gmail.get("token_present")
        and gmail.get("live_read_enabled")
        and gmail.get("live_write_enabled")
    )
    youtube_available = bool(youtube.get("connected") and youtube.get("ready"))
    xtb_available = bool(xtb.get("read_only_verified"))
    secrets_available = bool(secrets.get("is_secret_storage_enabled"))

    matrix = [
        {
            "access": "system_plikow",
            "required": "Odczyt kodu oraz zapis wyłącznie do data/logs/outputs/tmp.",
            "classification": "nadmiarowy",
            "available": bool(
                execution_policy.get("allow_local_file_read")
                and execution_policy.get("allow_local_file_write")
            ),
            "evidence": (
                f"execution_policies read={execution_policy.get('allow_local_file_read')} "
                f"write={execution_policy.get('allow_local_file_write')}; brak allowlisty katalogów."
            ),
            "recommendation": "Ograniczyć zapis do jawnej allowlisty ścieżek Atnosa.",
        },
        {
            "access": "wykonywanie_polecen",
            "required": "FFmpeg, testy, launchery i kontrolowane narzędzia lokalne.",
            "classification": "nadmiarowy",
            "available": bool(local_host.get("execution_enabled")),
            "evidence": (
                f"PowerShell={local_host.get('powershell_available')}; "
                f"execution={local_host.get('execution_enabled')}; "
                f"trusted_auto={local_host.get('trusted_auto_enabled')}; "
                f"elevated={local_host.get('elevated')}."
            ),
            "recommendation": "Zostawić brak elevacji, ale wymusić allowlistę komend i approval.",
        },
        {
            "access": "gmail",
            "required": "Wysyłka raportów i kontrolowany odczyt odpowiedzi właściciela.",
            "classification": _availability(gmail_available),
            "available": gmail_available,
            "evidence": (
                f"oauth_status={gmail.get('oauth_status')}; connected={gmail.get('connected')}; "
                f"token_present={gmail.get('token_present')}; write={gmail.get('live_write_enabled')}."
            ),
            "recommendation": "Utrzymać token w szyfrowanym vault poza repo i wykonywać live probe.",
        },
        {
            "access": "xtb_demo_read_only",
            "required": "Realne notowania demo/read-only bez komend transakcyjnych.",
            "classification": _availability(xtb_available),
            "available": xtb_available,
            "evidence": (
                f"registered={xtb.get('registered')}; credential_ref={xtb.get('has_credential_ref')}; "
                f"read_only_verified={xtb.get('read_only_verified')}; "
                f"real_trading={xtb.get('real_trading_enabled')}."
            ),
            "recommendation": "Zapisać credential ref w vault i wykonać read-only verification; bez live tradingu.",
        },
        {
            "access": "youtube_api",
            "required": "Prywatny upload, odczyt kanału i Analytics.",
            "classification": _availability(youtube_available),
            "available": youtube_available,
            "evidence": (
                f"connected={youtube.get('connected')}; ready={youtube.get('ready')}; "
                f"account_id={youtube.get('account_id')}; configured={youtube.get('configured')}."
            ),
            "recommendation": "Połączyć właściwe konto; public upload pozostawić wyłączony do QA.",
        },
        {
            "access": "sekrety",
            "required": "OAuth i klucze zapisane poza repo, bez plaintextu w logach.",
            "classification": _availability(secrets_available),
            "available": secrets_available,
            "evidence": (
                f"storage_backend={secrets.get('storage_backend')}; "
                f"warnings={len(secrets.get('warnings') or [])}."
            ),
            "recommendation": "Nie dodawać tokenów do repo; rotować wyłącznie po decyzji właściciela.",
        },
        {
            "access": "operacje_destrukcyjne_i_zewnetrzne",
            "required": "Wyłącznie przez action broker, confirm i log audytowy.",
            "classification": "nadmiarowy",
            "available": True,
            "evidence": (
                f"external_actions={runtime_controls.get('external_actions_enabled')}; "
                f"browser_allow_any_url={runtime_controls.get('browser_allow_any_url')}; "
                f"login_automation={runtime_controls.get('login_automation_enabled')}; "
                f"two_factor_automation={runtime_controls.get('two_factor_automation_enabled')}; "
                f"publish_automation={runtime_controls.get('publish_automation_enabled')}; "
                f"public_upload={runtime_controls.get('public_upload_enabled')}."
            ),
            "recommendation": "Wyłączyć any-URL, 2FA automation i global trusted_auto; zachować approval.",
        },
    ]
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database": str(db_path),
        "matrix": matrix,
        "evidence_sources": [
            "GET /local-host/capabilities",
            "GET /connectors/gmail/status",
            "GET /connectors/youtube/status",
            "GET /api/investment/market-data/xtb-demo/status",
            "GET /accounts/storage-status",
            "SQLite execution_policies and runtime_control_settings (read-only)",
        ],
    }
    json_path = output_dir / "permission-matrix.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Atnos - macierz uprawnień", ""]
    for item in matrix:
        lines.extend(
            [
                f"## {item['access']} - {item['classification']}",
                f"- Wymagane: {item['required']}",
                f"- Dostępne: {item['available']}",
                f"- Dowód: {item['evidence']}",
                f"- Rekomendacja: {item['recommendation']}",
                "",
            ]
        )
    md_path = output_dir / "permission-matrix.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "items": len(matrix)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
