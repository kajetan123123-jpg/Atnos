# Repository work

- Confirm the working root and read local instructions before editing. Preserve pre-existing dirty and untracked work.
- Trace the existing caller, contract and implementation before choosing a change. Prefer the smallest complete change in the existing subsystem.
- Reproduce a reported defect, capture evidence, state a falsifiable root-cause hypothesis, make the narrow fix and add a targeted regression test.
- Run the relevant tests and report exact commands and outcomes. Separate offline tests from live runtime verification.
- Do not claim completion when required checks are missing or failed. Do not reset, clean or delete user files to make the tree appear tidy.

## Complete a coding task

- Before editing, state the observable acceptance criteria and identify the existing tool/capability that can read, edit and verify this repository. A skill does not grant a missing filesystem or shell capability.
- Follow a bounded cycle: inspect -> plan -> reproduce/test -> edit -> review -> verify. When a check fails, inspect its actual output and change the hypothesis; do not repeat an identical permanent failure or expand the task silently.
- Keep local coding tasks strict_local. Do not invoke a cloud model or Codex CLI as an unannounced fallback. Treat a local worker's patch as a candidate until independent review and relevant tests support applying it.
- Finish with changed paths, commands actually executed, results, remaining risks and the smallest next step. If interrupted, save a concise handoff through the existing task/memory tools, with evidence links rather than a copied conversation.
- Only after verification, retain a reusable lesson when it would prevent recurrence. Include what was observed, the correction, its scope and test evidence; an untested model suggestion is not a learned rule.
