from __future__ import annotations

from atnos.local_brain.role_contracts import (
    ApprovalPolicy,
    BrainModelRole,
    ROLE_CONTRACTS,
    VerificationPolicy,
    get_role_contract,
    unbound_role_bindings,
)


def test_four_roles_exist_without_vendor_bindings() -> None:
    assert set(ROLE_CONTRACTS) == set(BrainModelRole)
    for role, contract in ROLE_CONTRACTS.items():
        assert contract.role is role
        assert "qwen" not in contract.purpose.lower()
        assert "deepseek" not in contract.purpose.lower()
        assert "ollama" not in contract.purpose.lower()
        assert "llama.cpp" not in contract.purpose.lower()
    bindings = unbound_role_bindings()
    assert bindings == {"CHAT": None, "OPERATOR": None, "CODING": None, "CLASSIFIER": None}


def test_operator_requires_runtime_verification() -> None:
    op = get_role_contract(BrainModelRole.OPERATOR)
    assert op.tool_calling_required is True
    assert op.structured_output_required is True
    assert op.verification_policy is VerificationPolicy.RUNTIME_AND_VERIFIER_REQUIRED
    assert op.approval_policy is ApprovalPolicy.TOOLS_WITH_HARD_STOP


def test_chat_and_classifier_block_high_impact_defaults() -> None:
    chat = get_role_contract("CHAT")
    clf = get_role_contract("CLASSIFIER")
    assert chat.may_execute_high_impact_tools is False
    assert clf.may_execute_high_impact_tools is False
    assert clf.tool_calling_required is False
