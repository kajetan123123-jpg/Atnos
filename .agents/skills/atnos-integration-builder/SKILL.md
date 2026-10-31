---
name: atnos-integration-builder
description: Add or materially change an Atnos public API endpoint, external API adapter, MCP, local tool, data source, social provider, repository adapter or skill-pack integration. Use for executable wiring and setup, not ordinary UI copy or CSS.
---

## PURPOSE

Connect an API entry point or external source through the existing Atnos subsystem, with usable setup and verifiable execution.

## WHEN TO USE

The outcome introduces an API entry point or external dependency, or changes its execution contract. A repository download alone is not an executable capability.

## REQUIRED WORKFLOW

1. Classify inbound API versus outbound provider/tool. For inbound API inspect existing routes, request/response schema, auth and service contract. For external material verify the official source, version/commit, license and dependencies without executing downloaded code; record adaptations and exclusions.
2. Reuse `runtime-recovered/api/src/atnos/api/routes` and the owning service for endpoints. For data providers, libraries, applications, catalogs or instruction packs inspect `integrations/external_repositories.py`, `public_data.py`, `capability_runtime.py` under that same atnos root before selecting a registration path.
3. Follow source/license when applicable → scoped security review → capability/service → existing registry if applicable → Settings → Vault if needed → health → tests → authorized live read-only smoke → documentation. An inbound endpoint does not require a fake external-provider entry.
4. Implement unavailable, malformed, timeout, setup and permission paths. An uncertain dispatched request must not silently trigger a second paid or external mutation.
5. Exercise the normal caller: a direct adapter test does not prove Chat, delegation or production reaches it.

## ATNOS INVARIANTS

Keep project capability restrictions, source provenance and cost/permission boundaries. Use `SecretVaultService` references; never persist raw credentials in examples or health output. Pin analysis sources outside core code on E:; do not auto-update them.

## VERIFICATION

Report installation, registration, configuration, health and live execution separately. Preserve redacted request/result evidence and current failure reasons. For OAuth or secrets, use `atnos-security-review` for the affected boundary.

## DO NOT

Declare “cloned = integrated”, execute an unreviewed repository, invent capabilities from tool names, or treat a skill instruction as an installed provider.
