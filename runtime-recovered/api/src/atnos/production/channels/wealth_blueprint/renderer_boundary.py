"""ProjectManifest is the ONLY Remotion renderer input contract.

Boundary rule (Wealth Blueprint canonical path):

  Production Orders → order artifacts → adapters → ProjectManifest
       → existing remotion-renderer → sound mix → mux → video_qc → media_qa → final MP4

  Order artifacts / WealthBlueprintScenePlan / Studio state / Comfy requests
       │
       ▼  (deterministic adapters — no AI)
  ProjectManifest (+ referenced asset file paths)
       │
       ▼
  remotion-renderer  (must NOT import Order / ScenePlan / order_assets / Studio / Comfy)

Python package: atnos.production.project_manifest
TS package:     remotion-renderer/src/contracts.ts

Adapters:
  - scene_plan_to_project_manifest
  - order_artifacts_to_project_manifest

SUPERSEDED / OBSOLETE (do not implement):
  VideoProject as a second render-facing contract, a generic remotion-engine package,
  or any parallel order→VideoProject adapter. ProjectManifest remains the sole boundary;
  existing remotion-renderer remains the sole engine.
"""

RENDERER_BOUNDARY = "ProjectManifest"
FORBIDDEN_REMOTION_INPUTS = (
    "WealthBlueprintScenePlan",
    "order artifacts (script/voice/visuals dicts)",
    "order_assets module types",
    "Studio UI state",
    "ComfyUI generation requests",
    "VideoProject (superseded — never a second renderer contract)",
)
