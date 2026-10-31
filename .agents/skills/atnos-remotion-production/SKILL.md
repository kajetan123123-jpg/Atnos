---
name: atnos-remotion-production
description: Build or assess real Atnos WB storyboard-to-Remotion films, visual meaning, motion, audio timing, plain captions and rendered quality. For a reported rendering defect combine with atnos-systematic-debugging; exclude web-page CSS and separate demo renderers.
---

## PURPOSE

Make the actual WB film explain the narration through the normal Atnos production path.

## WHEN TO USE

A transfer moves backward, illustration does not match speech, captions clip, movement distracts, or a rendered film fails acceptance.

## REQUIRED WORKFLOW

1. Inspect the canonical script/calculation receipt, storyboard meaning contract and selected Asset Brain files. Visual intent precedes layout: define initial state → semantic action → final state. An approved flag does not prove artwork quality or user acceptance.
2. Trace `production/order_execution.py` → `channels/wealth_blueprint/remotion_order_edit.py` → `ProjectManifest` → `production/remotion_renderer.py` → existing Remotion composition → sound mix → QA/package under `runtime-recovered/api/src/atnos`. Preserve this path.
3. Read the WB standard and directing plans in `reports/wb-quality-standard-20260920` when applicable. Use the existing `my-video/.agents/skills/remotion-captions/SKILL.md` only for caption APIs, or the relevant existing Remotion skill; do not duplicate their API manuals.
4. Drive timing from final audio/alignment. Give BUILD/HOLD/TRANSITION a purpose and reading time; show the correct direction, decision and consequence. Use programmatic verified numeric data, functional Alex actions, and stable `plain_phrase` captions without karaoke.
5. Verify phone-first safe areas and font measurement in final/intermediate frames. Run storyboard/video_qc/media_qa and the applicable semantic checks. Repair sources/manifest/renderer and rerun normal production.

## ATNOS INVARIANTS

Separate candidate, reference and qualified production assets. No invented claims, implicit user approval or manual final-MP4 rescue. Renderer success is not visual quality.

## VERIFICATION

Inspect real rendered frames at 1080×1920 and phone size, watch motion at normal speed and listen to complete audio. Mark each AV/human review as unperformed unless actually done; preserve timestamps and concrete rejection reasons.

## DO NOT

Replace semantic action with icons, force decorative Alex, lower QA, rebuild the renderer without evidence, or declare fluid motion from screenshots or 30 fps.
