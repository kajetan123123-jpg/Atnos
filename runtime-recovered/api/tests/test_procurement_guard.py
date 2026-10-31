from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient

TESTS_DIR = Path(__file__).resolve().parent
SRC_ROOT = TESTS_DIR.parents[0] / "src"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from test_support import create_test_app  # noqa: E402


def test_procurement_status_exposes_no_card_storage_policy(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/procurement/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["policy"]["card_data_storage"] == "forbidden"
    assert payload["policy"]["purchase_execution"] == "manual_checkout_required"
    assert payload["policy"]["autopurchase_enabled"] is False
    assert any(item["provider_id"] == "openai_api" for item in payload["catalog"])


def test_procurement_request_is_created_inside_budget(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.atnos_monthly_spend_limit_pln = 200
    app.state.settings.atnos_daily_spend_limit_pln = 200

    with TestClient(app) as client:
        response = client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "openai_api",
                "reason": "Need premium TTS and image generation for public-quality Shorts.",
                "account_id": "yt-wealth-blueprint",
                "estimated_monthly_cost_pln": 25,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending_approval"
    assert payload["provider_id"] == "openai_api"
    assert payload["card_data_storage"] == "forbidden"
    assert payload["budget_decision"]["allowed"] is True
    assert payload["smart_spending_decision"]["allowed"] is True

    with TestClient(app) as client:
        notifications = client.get("/api/procurement/expense-notifications").json()

    assert notifications[0]["event_type"] == "procurement_request"
    assert notifications[0]["provider"] == "openai_api"
    assert notifications[0]["delivery_channel"] == "gmail"
    assert notifications[0]["delivery_status"] == "pending_delivery"
    assert notifications[0]["card_data_storage"] == "forbidden"


def test_procurement_blocks_request_over_budget(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.atnos_monthly_spend_limit_pln = 20
    app.state.settings.atnos_daily_spend_limit_pln = 20

    with TestClient(app) as client:
        response = client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "rendi",
                "reason": "Need cloud video generation experiments.",
                "estimated_monthly_cost_pln": 80,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "blocked_by_budget"
    assert payload["budget_decision"]["allowed"] is False
    assert "monthly_budget_exceeded" in payload["budget_decision"]["blockers"]


def test_procurement_blocks_request_over_smart_spending_balance(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.atnos_monthly_spend_limit_pln = 200
    app.state.settings.atnos_daily_spend_limit_pln = 200

    with TestClient(app) as client:
        response = client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "openai_api",
                "reason": "Need premium TTS and image generation for public-quality Shorts.",
                "estimated_monthly_cost_pln": 80,
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "blocked_by_smart_spending"
    assert payload["budget_decision"]["allowed"] is False
    assert "smart_spending_balance_exceeded" in payload["budget_decision"]["blockers"]


def test_procurement_rejects_payment_card_data_in_request(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    test_card = " ".join(["4111"] * 4)

    with TestClient(app) as client:
        response = client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "openai_api",
                "reason": f"Use card {test_card} CVV 123 for billing.",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "payment_secret_rejected"


def test_procurement_manual_checkout_and_access_ready_flow(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "brave_search",
                "reason": "Need fallback search for trend intelligence.",
                "estimated_monthly_cost_pln": 5,
            },
        ).json()
        approved = client.post(f"/api/procurement/requests/{created['id']}/approve-manual-checkout")
        ready = client.post(
            f"/api/procurement/requests/{created['id']}/access-ready",
            json={"access_note": "BRAVE_SEARCH_KEY configured in runtime secret store."},
        )

    assert approved.status_code == 200
    assert approved.json()["status"] == "manual_checkout_required"
    assert "card" in approved.json()["message"].casefold()
    assert ready.status_code == 200
    assert ready.json()["status"] == "access_ready"
    assert ready.json()["metadata"]["access_note"] == "BRAVE_SEARCH_KEY configured in runtime secret store."


def test_smart_spending_starts_with_50_pln_and_revenue_unlocks_bigger_spend(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.atnos_monthly_spend_limit_pln = 500
    app.state.settings.atnos_daily_spend_limit_pln = 500

    with TestClient(app) as client:
        start = client.get("/api/procurement/smart-spending/status").json()
        blocked = client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "openai_api",
                "reason": "Need premium TTS and image generation for public-quality Shorts.",
                "estimated_monthly_cost_pln": 80,
            },
        ).json()
        revenue = client.post(
            "/api/procurement/smart-spending/revenue",
            json={"amount_pln": 100, "reason": "First revenue from reviewed content operations."},
        ).json()
        allowed = client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "openai_api",
                "reason": "Need premium TTS and image generation after revenue increased operating balance.",
                "estimated_monthly_cost_pln": 80,
            },
        ).json()
        end = client.get("/api/procurement/smart-spending/status").json()

    assert start["starting_balance_pln"] == 50
    assert start["available_balance_pln"] == 50
    assert start["earned_total_pln"] == 0
    assert start["spent_total_pln"] == 0
    assert start["max_autonomous_spend_pln"] == 50
    assert start["money_awareness"]["must_earn_before_spending"] is True
    assert start["card_access"]["can_use_card"] is False
    assert blocked["status"] == "blocked_by_smart_spending"
    assert revenue["status"] == "recorded"
    assert allowed["status"] == "pending_approval"
    assert end["revenue_pln"] == 100
    assert end["earned_total_pln"] == 100
    assert end["committed_expenses_pln"] == 80
    assert end["available_balance_pln"] == 70


def test_smart_spending_awareness_and_card_access_are_explicit(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        awareness = client.get("/api/procurement/smart-spending/awareness").json()
        card = client.get("/api/procurement/card-access/check").json()

    assert awareness["current_cash_pln"] == 50
    assert awareness["earned_total_pln"] == 0
    assert awareness["spent_total_pln"] == 0
    assert awareness["max_autonomous_spend_pln"] == 50
    assert "earn before" in awareness["rule"].casefold()
    assert awareness["card_access"]["can_use_card"] is False
    assert card["can_use_card"] is False
    assert card["can_verify_card"] is False
    assert card["payment_execution"] == "manual_checkout_only"
    assert card["card_data_storage"] == "forbidden"


def test_autonomy_plan_reports_missing_purchases_and_manual_access(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.openai_api_key = ""
    app.state.settings.shorts_allow_cloud_tts = False
    app.state.settings.youtube_data_api_key = ""

    with TestClient(app) as client:
        response = client.get("/api/procurement/autonomy-plan?include_optional=false")

    assert response.status_code == 200
    payload = response.json()
    provider_ids = {item["provider_id"] for item in payload["purchase_candidates"]}
    access_ids = {item["id"] for item in payload["access_tasks"]}
    assert payload["summary"]["autopurchase_enabled"] is False
    assert payload["summary"]["card_data_storage"] == "forbidden"
    assert payload["financial_awareness"]["current_cash_pln"] == 50
    assert payload["financial_awareness"]["card_access"]["can_use_card"] is False
    assert "openai_api" in provider_ids
    assert "youtube.google_cloud_oauth" in access_ids
    assert any(item["action"] == "fully_autonomous_purchase" for item in payload["blocked_actions"])


def test_autonomy_plan_can_create_and_deduplicate_procurement_requests(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)
    app.state.settings.atnos_monthly_spend_limit_pln = 500
    app.state.settings.atnos_daily_spend_limit_pln = 500
    app.state.settings.openai_api_key = ""
    app.state.settings.shorts_allow_cloud_tts = False

    with TestClient(app) as client:
        first = client.post(
            "/api/procurement/autonomy-plan/propose",
            json={"include_optional": False, "max_requests": 3},
        )
        second = client.post(
            "/api/procurement/autonomy-plan/propose",
            json={"include_optional": False, "max_requests": 3},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert any(item["provider_id"] == "openai_api" for item in first_payload["created"])
    assert any(item["reason"] == "active_request_exists" for item in second_payload["skipped"])
    assert all(item["card_data_storage"] == "forbidden" for item in first_payload["created"])


def test_expense_notification_dispatch_reports_missing_gmail_access(tmp_path: Path) -> None:
    app = create_test_app(tmp_path)

    with TestClient(app) as client:
        client.post(
            "/api/procurement/requests",
            json={
                "provider_id": "brave_search",
                "reason": "Need fallback search for trend intelligence.",
                "estimated_monthly_cost_pln": 5,
            },
        )
        dispatch = client.post("/api/procurement/expense-notifications/dispatch-gmail")

    assert dispatch.status_code == 200
    payload = dispatch.json()
    assert payload["attempted"] == 1
    assert payload["sent"] == 0
    assert payload["failed"] == 1
    assert payload["items"][0]["delivery_status"] == "failed"
    assert payload["items"][0]["card_data_storage"] == "forbidden"
