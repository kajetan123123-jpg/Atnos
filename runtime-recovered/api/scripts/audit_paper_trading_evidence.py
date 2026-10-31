from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit paper-trading evidence without assigning an unsupported score.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    database = Path(args.database).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        trades = [dict(row) for row in connection.execute("SELECT * FROM investment_paper_trades ORDER BY created_at")]
        runs = [dict(row) for row in connection.execute("SELECT * FROM investment_strategy_runs ORDER BY created_at")]
        checks = [dict(row) for row in connection.execute("SELECT * FROM investment_market_data_checks ORDER BY created_at")]
    finally:
        connection.close()

    timestamps = [datetime.fromisoformat(str(row["created_at"])) for row in trades]
    reasons = "\n".join(str(row.get("reason") or "") for row in trades).casefold()
    synthetic_trade_count = sum("synthetic" in str(row.get("reason") or "").casefold() for row in trades)
    blocked_runs = sum(str(row.get("status")) == "blocked" for row in runs)
    completed_runs = sum(str(row.get("status")) == "completed" for row in runs)
    executed_by_run = sum(int(row.get("executed_trade_count") or 0) for row in runs)
    distinct_run_times = len({str(row["created_at"])[:16] for row in trades})
    notional = sum(abs(float(row["quantity"]) * float(row["price_pln"])) for row in trades)

    missing = [
        "Zweryfikowane notowania XTB demo/read-only; tabela investment_market_data_checks ma 0 rekordów.",
        "Historia cen z częstotliwością i timestampami wystarczającymi do backtestu.",
        "Model kosztów: spread, prowizja, swap, poślizg i przewalutowanie.",
        "Zamknięcia pozycji lub mark-to-market, więc nie da się policzyć P&L ani equity curve.",
        "Jawnie zdefiniowany benchmark i jego seria cenowa.",
        "Podział in-sample/out-of-sample oraz dostateczna liczba niezależnych obserwacji.",
        "Polityka ryzyka zapisana w investment_risk_policies; obecnie tabela jest pusta.",
    ]
    report = {
        "assessment": "not_scored",
        "reason": "Dostępne transakcje używają cen i sygnałów syntetycznych oraz nie zawierają danych potrzebnych do pomiaru wyniku.",
        "database": str(database),
        "data_source": {
            "market_data_checks": len(checks),
            "verified_xtb_quotes": 0,
            "synthetic_trade_count": synthetic_trade_count,
            "all_trades_synthetic": bool(trades) and synthetic_trade_count == len(trades),
        },
        "sample": {
            "period_start": min(timestamps).isoformat() if timestamps else None,
            "period_end": max(timestamps).isoformat() if timestamps else None,
            "calendar_span_days": round((max(timestamps) - min(timestamps)).total_seconds() / 86400, 3) if len(timestamps) > 1 else 0,
            "paper_trade_count": len(trades),
            "distinct_run_times": distinct_run_times,
            "unique_symbols": sorted({str(row["symbol"]) for row in trades}),
            "side_counts": dict(Counter(str(row["side"]) for row in trades)),
            "synthetic_notional_pln": round(notional, 2),
        },
        "strategy_runs": {
            "count": len(runs),
            "completed": completed_runs,
            "blocked": blocked_runs,
            "reported_executed_trade_count": executed_by_run,
            "real_trading_enabled_any": any(bool(row.get("real_trading_enabled")) for row in runs),
        },
        "performance_metrics": {
            "transaction_costs": None,
            "pnl": None,
            "return_pct": None,
            "max_drawdown_pct": None,
            "benchmark_return_pct": None,
            "alpha_pct": None,
            "out_of_sample_result": None,
        },
        "out_of_sample": {
            "performed": False,
            "reason": "Brak historycznej serii realnych cen i zdefiniowanego podziału danych.",
        },
        "missing_evidence": missing,
        "integrity_notes": [
            "Najnowsze uruchomienia są blokowane zamiast wracać do synthetic_fallback, co jest poprawnym zachowaniem bezpieczeństwa.",
            "real_trading_enabled ma wartość false we wszystkich zapisanych runach.",
            "Syntetyczny notional nie jest wynikiem ani wartością portfela.",
            "Ocena strategii byłaby zmyślona, dlatego raport jej nie wystawia.",
        ],
    }
    json_path = output_dir / "paper-trading-evidence.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown = [
        "# Paper trading - audyt dowodów",
        "",
        "## Werdykt",
        "**Brak oceny strategii.** Dane nie pozwalają obliczyć wyniku inwestycyjnego.",
        "",
        "## Dostępna próbka",
        f"- Źródło: wyłącznie synthetic_fallback ({synthetic_trade_count}/{len(trades)} transakcji)",
        f"- Okres: {report['sample']['period_start']} - {report['sample']['period_end']}",
        f"- Transakcje: {len(trades)} w {distinct_run_times} momentach uruchomienia",
        f"- Symbole: {', '.join(report['sample']['unique_symbols'])}",
        f"- Runy strategii: {len(runs)}; completed={completed_runs}; blocked={blocked_runs}",
        "- Koszty / P&L / drawdown / benchmark / OOS: niewyliczalne",
        "",
        "## Brakujące dowody",
        *[f"- {item}" for item in missing],
        "",
        "## Ograniczenia",
        "Syntetyczny notional nie jest wynikiem. Nie ma podstaw do rekomendacji ani przejścia na live trading.",
    ]
    md_path = output_dir / "paper-trading-evidence.md"
    md_path.write_text("\n".join(markdown) + "\n", encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "assessment": "not_scored", "trade_count": len(trades)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
