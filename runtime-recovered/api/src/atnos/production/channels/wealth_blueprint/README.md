# Wealth Blueprint Scene Planner

Decision layer between **script / narration timeline** and the Remotion renderer.

- Input: script, narration segments/words, WB profile, available assets, design system
- Output: `WealthBlueprintScenePlan` JSON (`schema_version: wealth_blueprint_scene_plan_1`)
- **Never renders** video — only plans

## Decisions

layout · on-screen text · Alex · chart/icon/number · simplify · asset needs · animations · SFX

## Rules (validated deterministically)

1. No micro scene cuts  
2. Limited simultaneous elements  
3. One dominant idea  
4. On-screen ≠ full VO  
5. Numbers visualized  
6. Alex sparingly  
7. No consecutive identical layouts  
8. Restrained motion  
9. Animation follows narration timing  
10. Wealth Blueprint channel profile  

## Usage

```python
from atnos.production.channels.wealth_blueprint import (
    ScenePlannerInput,
    WealthBlueprintScenePlanner,
)

plan = WealthBlueprintScenePlanner().plan(ScenePlannerInput(
    script="Wealth compounds over time. Start early.",
))
assert plan.validation and plan.validation.ok
```
