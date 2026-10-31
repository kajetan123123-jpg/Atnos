---
name: atnos-evidence-and-data
description: Handle Atnos financial claims, source provenance, Project Brain facts, analytics or missing/periodic metrics. Use when evidence quality or data meaning matters; do not activate for unrelated UI styling.
---

## PURPOSE

Keep retrieved observations, calculations, hypotheses and absent data distinguishable throughout production.

## WHEN TO USE

A video contains a financial number, YouTube analytics is empty, a source is being saved to Project Brain, or a report risks treating expected values as measurements.

## REQUIRED WORKFLOW

1. Identify the exact claim or metric, unit, source, observation/retrieval time and measurement window. Prefer an available structured API to scraping; establish its freshness and limitations.
2. Under `runtime-recovered/api/src/atnos`, inspect the applicable contract: `integrations/public_data.py`, `capability_router.py`, `claim_evidence_gate.py`, `production/hypothetical_claim_evidence.py` or `services/project_brain_service.py`. For YouTube metrics trace `api/routes/connectors.py` → `services/youtube_connector_service.py` → actual `fetch_analytics` response.
3. Preserve null/unknown and explain why data is absent. UNKNOWN != 0; missing evidence != false; observed != expected; hypothesis != verified fact.
4. For WB hypothetical examples, record assumptions, calculate programmatically and bind the receipt to the canonical script. For externally stated facts, keep the supporting source and claim IDs. Do not turn an assumed return into a guaranteed outcome.
5. Pass the existing Claim Evidence Gate before media stages. Save source-backed facts through the established Project Brain envelope, not a parallel JSON “memory”.

## ATNOS INVARIANTS

No invented metrics or financial evidence. A source URL alone does not prove a specific number. Periodic metrics need a defined window and denominator where relevant.

## VERIFICATION

Test absent, zero, stale and malformed values separately. Recompute sample calculations and inspect the actual persisted claim/provenance receipt. Distinguish local calculation tests from a live data-provider result.

## DO NOT

Coerce empty analytics to zero, silently change a measurement window, lower the financial gate, or use a model paraphrase as original evidence.
