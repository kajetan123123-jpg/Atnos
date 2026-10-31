# Recorded ordinary scoped review

Offline regression data from prod-60eff989e0b6, feedback00460d38b2fd, draft0, 2026-10-22.

candidate.json and pipeline-review.json are byte-for-byte copies from the production script_feedback directory. The latter is the stored **pipeline verdict**, including deterministic checks and model findings; it is not the original HTTP model-response body.

The candidate restores the exact requested CTA. The verdict nevertheless repeats a CTA complaint and has no editorial checks or quoted issue evidence. It also reports real remaining defects. Tests must not turn this rejection into an acceptance or a production quality receipt. These fixtures never authorize a model call.

`false-pass-candidate.json` and `false-pass-review.json` are byte copies of feedback ac99ee557b4f, draft0. The stored pipeline verdict passed79words but incorrectly accepted the explicitly disallowed hidden-deadline/sign-up contrast. The candidate also says to make sure automatic renewal is enabled, changing the requested neutral check into an instruction. These are recorded failures, not an approved script or raw HTTP response. Regression checks demonstrate that scoped literal exclusions reject those actual phrases independently of a model PASS; they do not claim to solve arbitrary semantic review.
