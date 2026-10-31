from __future__ import annotations

from atnos.production.sound_design.models import SFX_EVENTS, SoundEventRequest, SoundMixPolicy


def filter_sfx_for_pacing(
    events: list[SoundEventRequest],
    *,
    duration_sec: float,
    policy: SoundMixPolicy,
) -> tuple[list[SoundEventRequest], list[dict[str, str | float]]]:
    """Keep SFX sparse so sound design supports pacing instead of fatiguing viewers.

    Never auto-attaches motion SFX — only planner-declared events are considered.
    Selection is chronological; higher priority wins conflicts inside the min-gap window.
    """
    if policy.auto_attach_motion_sfx:
        raise ValueError("auto_attach_motion_sfx must remain disabled")

    sfx = [event for event in events if event.event in SFX_EVENTS]
    non_sfx = [event for event in events if event.event not in SFX_EVENTS]
    ordered = sorted(sfx, key=lambda item: (item.at_sec, -item.priority, item.event))

    kept: list[SoundEventRequest] = []
    dropped: list[dict[str, str | float]] = []
    per_minute_budget = max(1, int((duration_sec / 60.0) * policy.max_sfx_per_minute + 0.999))
    budget = min(policy.max_sfx_total, per_minute_budget)

    for event in ordered:
        if kept and event.at_sec - kept[-1].at_sec < policy.min_sfx_gap_sec:
            previous = kept[-1]
            if event.priority > previous.priority:
                dropped.append(
                    {
                        "event": previous.event,
                        "at_sec": previous.at_sec,
                        "reason": "replaced_by_higher_priority",
                    }
                )
                kept[-1] = event
            else:
                dropped.append({"event": event.event, "at_sec": event.at_sec, "reason": "min_gap"})
            continue
        if len(kept) >= budget:
            dropped.append({"event": event.event, "at_sec": event.at_sec, "reason": "max_sfx_budget"})
            continue
        kept.append(event)

    return [*non_sfx, *kept], dropped
