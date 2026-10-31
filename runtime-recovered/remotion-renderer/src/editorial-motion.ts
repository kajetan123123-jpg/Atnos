import type {ManifestScene} from './contracts';

export const EDITORIAL_COLORS={ink:'#081523',paper:'#F2EDDE',teal:'#60D4CD',gold:'#EDB85B',risk:'#ED836F',muted:'#9BAFB7'};

export function editorialDirection(scene:ManifestScene) {
  const d=scene.visual_relation?.editorial_direction;
  const duration=scene.timing.end_sec-scene.timing.start_sec;
  if(!d||d.version!==1||d.timing_source!=='final_voice_word_alignment'
    ||!scene.caption_words?.length||Math.abs(d.duration_sec-duration)>.001
    ||!Number.isFinite(d.action_sec)||!Number.isFinite(d.risk_sec)||d.action_sec<0||d.risk_sec<0
    ||d.action_sec>duration-.8||d.risk_sec>duration-.8||d.hold_seconds<.8)
    throw new Error('Missing or invalid audio-bound editorial shot plan');
  const modes=['question','execution_to_cash','per_share_tradeoff','repeat_business','verified_curve','closing'];
  if(!modes.includes(d.mode))throw new Error('Unsupported editorial mechanism; no text-card fallback');
  if(['execution_to_cash','per_share_tradeoff','repeat_business'].includes(d.mode)){
    if(scene.visual_relation?.kind!=='research_check'||!d.subject||!d.check||!d.risk
      ||!scene.narration.includes(d.check)||!scene.narration.includes(d.risk))
      throw new Error('Editorial diagram does not match the narrated research');
    const objects=scene.visual_relation.objects??[];
    for(const key of ['subject','check','risk'] as const)
      if(objects.find(o=>o.role===key)?.label!==d[key])throw new Error('Editorial clause changed');
  }
  if(d.mode==='verified_curve'&&!scene.verified_series)throw new Error('No verified data for editorial curve');
  return d;
}

export const ease=(t:number)=>{const x=Math.min(1,Math.max(0,t));return x*x*(3-2*x);};
export function editorialState(scene:ManifestScene,seconds:number) {
  const d=editorialDirection(scene);
  const build=ease((seconds-d.action_sec)/.7),risk=ease((seconds-d.risk_sec)/.5);
  return {build,risk,
    cameraScale:d.mode==='execution_to_cash'?1.12-.12*build:1,
    cameraX:d.mode==='execution_to_cash'?500+40*build:540,
    entrance:ease(seconds/.42), // purposeful reveal, then stable reading hold
    shot:seconds<d.risk_sec?'mechanism':'risk-context'};
}
