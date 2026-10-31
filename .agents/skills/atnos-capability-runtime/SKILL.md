---
name: atnos-capability-runtime
description: Implement or debug Atnos intent-to-capability routing, provider selection, permission checks, execution status or provenance. Use when the routing or runtime contract is the problem, not for simple provider-specific copy changes.
---

## PURPOSE

Make a user's task reach a real executor with honest status and provenance.

## WHEN TO USE

The planner chooses an unavailable tool, ignores project restrictions, reports success without execution, or requires a new runtime binding.

## REQUIRED WORKFLOW

1. Trace TASK → INTENT → CAPABILITY → PROVIDER → PERMISSION → EXECUTION → VERIFICATION → PROVENANCE in the actual caller.
2. Inspect `runtime-recovered/api/src/atnos/integrations/capability_task_planner.py`, `capability_router.py`, `capability_runtime.py`, `capability_executors.py` and `capability_workflow_service.py`. Determine whether this is public-data routing or the executable tool workflow; reuse its registry.
3. Express the semantic capability in the planner. Resolve providers in the registry using enabled/setup state, project access, health, freshness and cost. Do not encode repository or provider names in business intent.
4. Check permission before dispatch and verify the result. A definite refusal and an uncertain dispatched outcome differ: the latter must reconcile existing evidence before another provider/request can run. Preserve the schema and map SUCCESS, FAILED, BLOCKED, REQUIRES_SETUP, REQUIRES_DATA, REQUIRES_APPROVAL and NOT_INSTALLED explicitly.
5. Retain request identity, provider/capability, observation time and source information in the established provenance path; check the real Chat or delegation entry point.

## ATNOS INVARIANTS

Fallback follows the existing policy; a paid provider is not an implicit remedy. Missing data, installation and permission are not successful execution. This skill provides instructions, not a new Codex tool.

## VERIFICATION

Cover correct selection, unsupported intent, missing setup/data, denied permission and primary-provider failure. Compare the normal caller's live read-only result with registry and provenance evidence.

## DO NOT

Hardcode provider names in the planner, substitute model guesses for provider output, or broaden permissions to make a smoke test pass.
