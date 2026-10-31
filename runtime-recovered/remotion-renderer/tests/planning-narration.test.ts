import {describe,it,expect} from 'vitest';
import {planningDeskReviewFocus,supportsPlanningDesk} from '../src/WBPlanningDeskScene';
import type {ManifestScene} from '../src/contracts';

describe('recorded WB narration and the declared renderer action',()=>{
  it.each([
    ['Inspect the single balance to locate any portion currently counted for both purposes simultaneously right now.','shared-balance','shared-balance'],
    ['Check which money is already assigned to both purposes twice.','shared-balance','shared-balance'],
    ['Check what is already assigned to the future purchase.','commitments','existing-assignments'],
    ['Assigning the same funds to two competing claims reduces availability for one purpose if an emergency occurs unexpectedly.','shared-balance','shared-balance'],
  ])('uses the same action as the planner for %s',(narration,focus,action)=>{
    const scene={narration,visual_relation:{kind:'reserve_readiness',stage:'review',objects:[],planning_desk_action:action}} as unknown as ManifestScene;
    expect(planningDeskReviewFocus(scene)).toBe(focus);
    expect(supportsPlanningDesk(scene)).toBe(true);
  });
  it('rejects an old declared assignment action for an explicit double-counting comparison',()=>{
    const scene={narration:'Inspect the portion counted for both purposes.',
      visual_relation:{kind:'reserve_readiness',stage:'review',planning_desk_action:'existing-assignments'}} as unknown as ManifestScene;
    expect(supportsPlanningDesk(scene)).toBe(false);
  });
});
