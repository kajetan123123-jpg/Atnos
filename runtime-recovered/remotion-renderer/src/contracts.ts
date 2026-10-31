import {verifyCompoundSeries, type CalculationClaim, type VerifiedCompoundSeries} from './compound-series';
export const RENDERER_VERSION = 'wealth-blueprint-reference-v0.47';
export const COMPOSITION_ID = 'WealthBlueprintShortV1';
export const WIDTH = 1080;
export const HEIGHT = 1920;
export const FPS = 30;

export const SCENE_TYPES = [
  'HookScene',
  'ImageScene',
  'NumberScene',
  'ChartScene',
  'CTAScene',
] as const;

export type SceneType = (typeof SCENE_TYPES)[number];
export type SemanticOverlayKind =
  | 'reserve_readiness'
  | 'income_split'
  | 'payday_arrival'
  | 'convenience_chain'
  | 'cumulative_spending'
  | 'allocation_sorter'
  | 'auto_invest'
  | 'purchase_pause'
  | 'raise_absorption'
  | 'savings_account'
  | 'weekly_transfer'
  | 'notebook_log'
  | 'habit_repeat'
  | 'compound_layers'
  | 'compound_reinvestment'
  | 'compound_time'
  | 'compound_early_start'
  | 'compound_scenario';
export const SEMANTIC_OVERLAY_KINDS: SemanticOverlayKind[] = [
  'reserve_readiness',
  'income_split',
  'payday_arrival',
  'convenience_chain',
  'cumulative_spending',
  'allocation_sorter',
  'auto_invest',
  'purchase_pause',
  'raise_absorption',
  'savings_account',
  'weekly_transfer',
  'notebook_log',
  'habit_repeat',
  'compound_layers',
  'compound_reinvestment',
  'compound_time',
  'compound_early_start',
  'compound_scenario',
];

export type ManifestAsset = {
  asset_id: string;
  role: 'image_candidate' | 'selected_image' | 'scene_video' | 'audio' | 'caption';
  path: string;
  exists: boolean;
  provenance: {
    artifact_hash?: string | null;
    generation_parameters?: {
      fit?: 'contain' | 'cover' | string;
      preserve_alpha?: boolean;
      production_role?: string;
      width?: number;
      height?: number;
      channel?: string;
      version?: string;
      [key: string]: unknown;
    };
  };
  qa?: {
    status: 'pending' | 'passed' | 'warning' | 'failed';
    action: 'PASS' | 'WARN' | 'REGENERATE_SCENE' | 'REGENERATE_AUDIO' | 'BLOCK_FINAL';
    score?: number | null;
    reasons?: string[];
  };
};

export type CaptionWord = {
  text: string;
  start_sec: number;
  end_sec: number;
  segment_id?: string;
};

export type CaptionCue = {
  text: string;
  start_sec: number;
  end_sec: number;
  words: CaptionWord[];
};

export type CaptionStyleId = 'plain_phrase' | 'paper_karaoke' | 'soft_pill' | 'outline_bold';

export const DEFAULT_CAPTION_STYLE: CaptionStyleId = 'plain_phrase';
export const CAPTION_MIN_FONT_PX = 76;
export const CAPTION_MAX_LINES = 2;
export const CAPTION_MAX_WORDS = 8;

export type ManifestScene = {
  scene_id: string;
  scene_type: SceneType;
  timing: {start_sec: number; end_sec: number};
  narration: string;
  caption: string;
  headline?: string;
  overlay_text?: string;
  layout_id?: string;
  animation_presets?: string[];
  caption_words?: CaptionWord[];
  number_value?: string | null;
  chart_values?: number[];
  chart_labels?: string[];
  chart_unit?: string;
  claim_ids?: string[];
  verified_series?: VerifiedCompoundSeries | null;
  comparison_left?: string;
  comparison_right?: string;
  semantic_overlay_kind?: SemanticOverlayKind | null;
  visual_prompt?: string;
  visual_relation?: {
    kind?: string;
    stage?: string;
    access_focus?: 'overview' | 'terms';
    bill_focus?: 'calendar' | 'list';
    planning_desk_action?: 'account-to-purposes' | 'purpose-context' | 'write-purposes' | 'existing-assignments' | 'shared-balance' | 'revisit-goal' | 'purpose-distinction' | 'review-both';
    editorial_direction?: {version:number; mode:string; narration_hash:string; timing_source:string; duration_sec:number;
      measurement:string; hold_seconds:number; action_sec:number; risk_sec:number; visible_change:string;
      subject?:string; check?:string; risk?:string; planning_action?:string; cue_times?:Record<string,number>};
    objects?: Array<{role: string; label: string}>;
    source?: string | null;
    destination?: string | null;
    direction?: string | null;
    action?: string;
    initial_state?: string;
    final_state?: string;
    condition?: string | null;
    blocked?: boolean;
  } | null;
  visual_story?: {sequence: string; state: string; previous_state: string | null; cue_sec: number; motion_seconds: number; hold_seconds: number; timing_source: string};
  assets: ManifestAsset[];
};

export type ProjectManifest = {
  review_mode?: string;
  schema_version: '1.0';
  manifest_id: string;
  job_id: string;
  project_id: string;
  channel: string;
  language_code: string;
  brand_profile_version: string;
  visual_style_id?: 'A' | 'B' | 'C' | 'D' | null;
  visual_style_version?: string | null;
  visual_style_definition_hash?: string | null;
  renderer_version: string;
  content_hash: string;
  brand?: BrandSpec;
  scenes: ManifestScene[];
  calculation_claims?: CalculationClaim[];
};

export type BrandSpec = {
  comparisonStyle?: 'painted_editorial' | 'illustrated_explainer' | 'pigment_motion' | 'editorial_magazine';
  visualVariant?: 'professional' | 'professional_host' | 'editorial_motion';
  /** Opt-in art-direction candidate; not proof of aesthetic approval. */
  illustrationSystem?: 'wb_editorial_vector_v1' | 'wb_planning_desk_v1' | 'wb_planning_desk_painted_v1';
  presentation?: 'standard' | 'educational_illustration';
  background: string;
  panel: string;
  accent: string;
  accentSoft: string;
  text: string;
  muted: string;
  wordmark: string;
  /** Burned caption look — production default paper_karaoke. */
  captionStyle?: CaptionStyleId;
};

export type RendererProps = {
  manifest: ProjectManifest;
  assetData: Record<string, string>;
  fontRegularDataUri?: string;
  fontExtraBoldDataUri?: string;
  brand?: Partial<BrandSpec>;
};

export const DEFAULT_BRAND: BrandSpec = {
  // wealth_blueprint_visual_v1 — VISUAL-BIBLE navy/cream/gold (neon mint superseded)
  background: '#142633',
  panel: '#1C3344',
  accent: '#D5AD6C',
  accentSoft: '#426579',
  text: '#F0E5D2',
  muted: '#4B7977',
  wordmark: 'WEALTH BLUEPRINT',
  captionStyle: DEFAULT_CAPTION_STYLE,
};

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const assertString: (value: unknown, label: string) => asserts value is string = (value, label) => {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${label} must be a non-empty string`);
  }
};

export const validateManifest = (value: unknown): ProjectManifest => {
  if (!isObject(value)) {
    throw new Error('manifest must be an object');
  }
  if (value.schema_version !== '1.0') {
    throw new Error('manifest schema_version must equal 1.0');
  }
  assertString(value.manifest_id, 'manifest_id');
  assertString(value.job_id, 'job_id');
  assertString(value.project_id, 'project_id');
  assertString(value.channel, 'channel');
  assertString(value.language_code, 'language_code');
  assertString(value.brand_profile_version, 'brand_profile_version');
  if (value.visual_style_id != null && !['A', 'B', 'C', 'D'].includes(String(value.visual_style_id))) {
    throw new Error('unsupported visual_style_id');
  }
  assertString(value.renderer_version, 'renderer_version');
  if (value.renderer_version !== RENDERER_VERSION) {
    throw new Error(`renderer_version must equal ${RENDERER_VERSION}`);
  }
  assertString(value.content_hash, 'content_hash');
  if (!Array.isArray(value.scenes) || value.scenes.length === 0) {
    throw new Error('manifest must contain at least one scene');
  }

  let previousEnd = 0;
  const sceneIds = new Set<string>();
  const manifestSourceText = (value.scenes as unknown[])
    .filter(isObject)
    .map((scene) => sourceTextForScene(scene as ManifestScene))
    .join(' ');
  for (const [index, candidate] of value.scenes.entries()) {
    if (!isObject(candidate)) {
      throw new Error(`scenes[${index}] must be an object`);
    }
    assertString(candidate.scene_id, `scenes[${index}].scene_id`);
    if (sceneIds.has(candidate.scene_id)) {
      throw new Error(`duplicate scene_id: ${candidate.scene_id}`);
    }
    sceneIds.add(candidate.scene_id);
    if (!SCENE_TYPES.includes(candidate.scene_type as SceneType)) {
      throw new Error(`unsupported scene_type: ${String(candidate.scene_type)}`);
    }
    if (!isObject(candidate.timing)) {
      throw new Error(`scenes[${index}].timing must be an object`);
    }
    const start = candidate.timing.start_sec;
    const end = candidate.timing.end_sec;
    if (typeof start !== 'number' || typeof end !== 'number' || start < 0 || end <= start) {
      throw new Error(`scenes[${index}] has invalid timing`);
    }
    if (start + 0.001 < previousEnd) {
      throw new Error(`scenes[${index}] overlaps the previous scene`);
    }
    previousEnd = end;
    assertString(candidate.caption, `scenes[${index}].caption`);
    if (candidate.caption_words != null) {
      if (!Array.isArray(candidate.caption_words)) {
        throw new Error(`scenes[${index}].caption_words must be an array`);
      }
      let previousWordEnd = -1;
      const joined: string[] = [];
      for (const [wordIndex, rawWord] of candidate.caption_words.entries()) {
        if (!isObject(rawWord)) {
          throw new Error(`scenes[${index}].caption_words[${wordIndex}] must be an object`);
        }
        assertString(rawWord.text, `scenes[${index}].caption_words[${wordIndex}].text`);
        const wordStart = rawWord.start_sec;
        const wordEnd = rawWord.end_sec;
        if (typeof wordStart !== 'number' || typeof wordEnd !== 'number' || wordEnd <= wordStart) {
          throw new Error(`scenes[${index}].caption_words[${wordIndex}] has invalid timing`);
        }
        if (wordStart + 0.001 < previousWordEnd) {
          throw new Error(`scenes[${index}].caption_words[${wordIndex}] overlaps the previous word`);
        }
        previousWordEnd = wordEnd;
        joined.push(rawWord.text.trim());
      }
      if (joined.length > 0 && joined.join(' ') !== String(candidate.caption).trim()) {
        throw new Error(`scenes[${index}].caption must equal joined caption_words`);
      }
    }
    if (!Array.isArray(candidate.assets)) {
      throw new Error(`scenes[${index}].assets must be an array`);
    }
    if (
      candidate.semantic_overlay_kind != null &&
      !SEMANTIC_OVERLAY_KINDS.includes(candidate.semantic_overlay_kind as SemanticOverlayKind)
    ) {
      throw new Error(`${String(candidate.scene_type)} ${candidate.scene_id} has unsupported semantic_overlay_kind`);
    }
    if (candidate.scene_type === 'NumberScene' && !numericToken(candidate as ManifestScene)) {
      throw new Error(`NumberScene ${candidate.scene_id} has no source-backed number`);
    }
    if (candidate.semantic_overlay_kind === 'reserve_readiness') {
      const relation = (candidate as ManifestScene).visual_relation;
      if (candidate.scene_type !== 'HookScene' || relation?.kind !== 'reserve_readiness'
        || !['need','identify','access','separate','review'].includes(relation.stage ?? '')
        || (relation.access_focus !== undefined && (relation.stage !== 'access' || !['overview','terms'].includes(relation.access_focus)))
        || (relation.bill_focus !== undefined && (relation.stage !== 'identify' || !['calendar','list'].includes(relation.bill_focus)
          || !relation.objects?.some(item=>item.role==='expense' && item.label==='ANNUAL BILL')))
        || !relation.objects?.some((item) => item.role === 'reserve')) {
        throw new Error(`Reserve scene ${candidate.scene_id} has no supported qualitative meaning contract`);
      }
    }
    if (candidate.verified_series != null && candidate.scene_type !== 'ChartScene') {
      throw new Error('Derived compound series requires ChartScene');
    }
    if (candidate.scene_type === 'ChartScene') {
      const chartScene = candidate as ManifestScene;
      verifyCompoundSeries(chartScene, value.calculation_claims as CalculationClaim[] ?? []);
      const chartSourceText = chartScene.semantic_overlay_kind === 'compound_reinvestment'
        ? manifestSourceText
        : sourceTextForScene(chartScene);
      const hasComparison = Boolean(candidate.comparison_left || candidate.comparison_right);
      if (
        Array.isArray(candidate.chart_values) &&
        candidate.chart_values.some((value) => typeof value !== 'number' || !Number.isFinite(value))
      ) {
        throw new Error(`ChartScene ${candidate.scene_id} chart_values must be finite numbers`);
      }
      if (
        hasComparison &&
        (typeof candidate.comparison_left !== 'string' || candidate.comparison_left.trim() === '' ||
          typeof candidate.comparison_right !== 'string' || candidate.comparison_right.trim() === '')
      ) {
        throw new Error(`ChartScene ${candidate.scene_id} comparison needs two explicit labels`);
      }
      if (chartValues(chartScene).length < 2) {
        throw new Error(`ChartScene ${candidate.scene_id} needs at least two source-backed values`);
      }
      if (!chartScene.verified_series && Array.isArray(candidate.chart_values) && !hasSourceBackedValueOrder(chartScene, chartSourceText)) {
        throw new Error(`ChartScene ${candidate.scene_id} reorders, repeats, or invents values not backed by narration`);
      }
      if (candidate.chart_labels != null) {
        const labels = candidate.chart_labels;
        if (
          !Array.isArray(labels) ||
          (labels.length > 0 &&
            (labels.length !== chartValues(chartScene).length ||
              labels.some((label) => typeof label !== 'string' || label.trim() === '') ||
              (!chartScene.verified_series && !hasSourceBackedLabelOrder(labels as string[], chartScene, chartSourceText))))
        ) {
          throw new Error(`ChartScene ${candidate.scene_id} labels must match its source-backed values`);
        }
      }
    }
    if (candidate.visual_story) {
      const story = candidate.visual_story as ManifestScene['visual_story'];
      if (!story || story.sequence !== 'raise-pilot-v1' || !['raise','spending','costs','choice','goal','question'].includes(story.state) || story.timing_source !== 'final_voice_word_alignment') {
        throw new Error('Unsupported or unaligned WB visual story');
      }
      const layers = candidate.assets as ManifestAsset[];
      const roles = new Set(layers.map(a=>a.provenance.generation_parameters?.layer_role));
      if (layers.length !== 5 || ['background','income','savings','goal','costs'].some(role=>!roles.has(role))) throw new Error('WB visual story requires five illustration layers');
      for (const layer of layers) {
        if (!layer.exists || layer.qa?.status !== 'passed' || !layer.provenance.artifact_hash) throw new Error('WB layer validation missing');
        if (!layer.provenance.generation_parameters?.user_approved && value.review_mode !== 'candidate_preview') throw new Error('WB candidate forbidden in production');
      }
      if (value.review_mode === 'candidate_preview' && end > 30) throw new Error('Candidate preview exceeds thirty seconds');
    } else if (candidate.scene_type === 'ImageScene') {
      const selected = (candidate.assets as ManifestAsset[]).filter(
        (asset) => asset.role === 'selected_image' && asset.exists && asset.qa?.status === 'passed',
      );
      if (selected.length !== 1) {
        throw new Error(`ImageScene ${candidate.scene_id} needs exactly one QA-passed selected image`);
      }
      assertString(selected[0].provenance?.artifact_hash, `ImageScene ${candidate.scene_id} artifact_hash`);
    }
  }
  if (previousEnd > 60.001) {
    throw new Error('manifest duration exceeds 60 seconds');
  }
  return value as ProjectManifest;
};

export const durationInFrames = (manifest: ProjectManifest): number => {
  const seconds = Math.max(...manifest.scenes.map((scene) => scene.timing.end_sec));
  return Math.max(1, Math.ceil(seconds * FPS));
};

// Quantize absolute boundaries once: rounding start and duration independently
// leaves holes (or overlaps) when aligned speech ends between video frames.
export const sceneFrameRange = (scene: ManifestScene, finalScene = false) => {
  const from = Math.round(scene.timing.start_sec * FPS);
  const end = finalScene ? Math.ceil(scene.timing.end_sec * FPS) : Math.round(scene.timing.end_sec * FPS);
  return {from, durationInFrames: Math.max(1, end - from)};
};

export const numericToken = (scene: ManifestScene): string | null => {
  if (scene.number_value?.trim()) {
    return scene.number_value.trim();
  }
  const text = `${scene.headline ?? ''} ${scene.overlay_text ?? ''} ${scene.caption}`;
  return text.match(/(?:[$€£])?-?\d+(?:[.,]\d+)?(?:\s?[%xX])?/)?.[0] ?? null;
};

export const chartValues = (scene: ManifestScene): number[] => {
  if (Array.isArray(scene.chart_values) && scene.chart_values.length >= 2) {
    return scene.chart_values.filter((value) => Number.isFinite(value));
  }
  const matches = `${scene.headline ?? ''} ${scene.overlay_text ?? ''} ${scene.caption}`.matchAll(
    /(?<![\w.])-?\d+(?:[.,]\d+)?/g,
  );
  return [...matches]
    .map((match) => Number.parseFloat(match[0].replace(',', '.')))
    .filter((value) => Number.isFinite(value));
};

const sourceTextForScene = (scene: ManifestScene): string => {
  const narration = scene.narration?.trim() ?? '';
  const caption = scene.caption.trim();
  // Source-backed charts/numbers may cite either narration or subtitle text.
  return narration === caption ? narration : `${narration} ${caption}`.trim();
};

const sourceChartValues = (sourceText: string): number[] => {
  const matches = sourceText.matchAll(/(?<![\w.])-?\d+(?:[.,]\d+)?/g);
  return [...matches]
    .map((match) => Number.parseFloat(match[0].replace(',', '.')))
    .filter((value) => Number.isFinite(value));
};

const hasSourceBackedValueOrder = (scene: ManifestScene, sourceText: string): boolean => {
  const source = sourceChartValues(sourceText);
  let cursor = 0;
  for (const value of scene.chart_values ?? []) {
    const match = source.findIndex((candidate, index) => index >= cursor && Math.abs(candidate - value) <= 1e-9);
    if (match < 0) return false;
    cursor = match + 1;
  }
  return true;
};

const sourceBackedLabelPosition = (label: string, sourceText: string, startAt: number): number => {
  const normalizedLabel = normalizeChartLabelSource(label);
  const normalizedSource = normalizeChartLabelSource(sourceText);
  if (!normalizedLabel || !normalizedSource) return -1;
  const remaining = normalizedSource.slice(startAt);
  const pattern = normalizedLabel === 'start'
    ? /\bstart(?:ing)?\b/
    : new RegExp(`\\b${normalizedLabel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`);
  const match = remaining.search(pattern);
  return match < 0 ? -1 : startAt + match;
};

const normalizeChartLabelSource = (value: string): string => value
  .toLowerCase()
  .replace(/[^a-z0-9]+/g, ' ')
  .trim()
  .replace(/\byear\s+one\b/g, 'year 1')
  .replace(/\byear\s+two\b/g, 'year 2');

const hasSourceBackedLabelOrder = (labels: string[], scene: ManifestScene, sourceText = sourceTextForScene(scene)): boolean => {
  let cursor = 0;
  for (const label of labels) {
    const position = sourceBackedLabelPosition(label, sourceText, cursor);
    if (position < 0) return false;
    cursor = position + 1;
  }
  return true;
};

export const headlineFor = (scene: ManifestScene, maxWords = 6): string => {
  const source = scene.headline?.trim() || scene.overlay_text?.trim() || scene.caption.trim();
  const words = source.split(/\s+/).filter(Boolean).slice(0, Math.max(1, maxWords));
  const dangling = /^(?:to|of|for|and|or|when|where|your|with|before|after|from|into|the|a)$/i;
  while (words.length > 2 && dangling.test(words[words.length - 1].replace(/[^A-Za-z]+$/g, ''))) {
    words.pop();
  }
  return words.join(' ').toUpperCase();
};

/** An authored image headline is a complete thought, not a narration excerpt. */
export const imageHeadlineFor = (scene: ManifestScene): string => {
  const authored = scene.headline?.trim();
  return authored ? authored.toUpperCase() : headlineFor(scene, 14);
};

export const captionChunks = (caption: string, wordsPerChunk = 3): string[] => {
  const words = caption.trim().split(/\s+/).filter(Boolean);
  const chunks: string[] = [];
  for (let index = 0; index < words.length; index += wordsPerChunk) {
    chunks.push(words.slice(index, index + wordsPerChunk).join(' '));
  }
  return chunks.length > 0 ? chunks : [''];
};

export const captionCuesFromWords = (
  words: CaptionWord[],
  maxWords = 5,
  maxCharacters = 36,
): CaptionCue[] => {
  const cues: CaptionCue[] = [];
  let current: CaptionWord[] = [];
  const flush = () => {
    if (!current.length) return;
    cues.push({
      text: current.map((word) => word.text).join(' '),
      start_sec: current[0].start_sec,
      end_sec: current[current.length - 1].end_sec,
      words: [...current],
    });
    current = [];
  };
  const softBreak = (text: string) => /[,:;]$|--|—/.test(text);
  for (const word of words) {
    const text = word.text.trim();
    if (!text) continue;
    if (text.length > maxCharacters) {
      throw new Error(`Caption word exceeds readable length: ${text}`);
    }
    const trial = [...current.map((item) => item.text), text].join(' ');
    const segmentChanged =
      current.length > 0 &&
      Boolean(current[current.length - 1].segment_id) &&
      Boolean(word.segment_id) &&
      current[current.length - 1].segment_id !== word.segment_id;
    const pauseGap =
      current.length > 0 && word.start_sec - current[current.length - 1].end_sec > 0.35;
    if (
      current.length > 0 &&
      (trial.length > maxCharacters ||
        current.length >= maxWords ||
        segmentChanged ||
        pauseGap ||
        softBreak(current[current.length - 1].text))
    ) {
      // Keep amounts/currency and short noun/verb phrases together at soft boundaries.
      // A real pause or sentence boundary takes precedence: never reveal future speech in it.
      const last = current[current.length - 1];
      const lastBare = last.text.replace(/[^A-Za-z]+$/g, '');
      const danglingLeadIn =
        /^(?:a|an|the|your|our|to|of|for|set|this|that|and|or|when|with|before|after|from|into|on|because|so|by|at|how|over|much)$/i;
      const attached =
        danglingLeadIn.test(lastBare) ||
        /^(?:[$€£]|USD|GBP|EUR)$/i.test(last.text) ||
        (/^\d[\d,.]*$/.test(last.text) && /^(?:dollars?|pounds?|euros?|USD|GBP|EUR)[,.!?]?$/i.test(text));
      // Never end a plate on a dangling lead-in when the next spoken word continues the phrase.
      const carry =
        !segmentChanged && !pauseGap && attached && current.length > 1 ? current.pop() : undefined;
      flush();
      if (carry) current.push(carry);
    }
    current.push({...word, text});
    if (/[.?!]$/.test(text)) {
      flush();
    }
  }
  flush();
  const orphan = /^(?:it|a|an|the|to|of|and|or|your|you)\.?$/i;
  if (cues.length > 1 && orphan.test(cues[cues.length - 1].text) && cues[cues.length - 2].words.length < maxWords + 1) {
    const tail = cues.pop()!;
    const prev = cues[cues.length - 1];
    prev.words.push(...tail.words);
    prev.text = prev.words.map((word) => word.text).join(' ');
    prev.end_sec = tail.end_sec;
  }
  // Merge a plate that still ends on a dangling lead-in into the next plate when timing allows.
  const danglingTail =
    /^(?:a|an|the|your|our|to|of|for|set|this|that|and|or|when|with|before|after|from|into|on|because|so|by|at|how|over|much)$/i;
  const repaired: CaptionCue[] = [];
  for (let index = 0; index < cues.length; index += 1) {
    const cue = cues[index];
    const next = cues[index + 1];
    const lastToken = cue.text.replace(/[^A-Za-z]+$/g, '').split(/\s+/).pop() || '';
    if (
      next &&
      danglingTail.test(lastToken) &&
      next.start_sec - cue.end_sec <= 0.35 &&
      `${cue.text} ${next.text}`.length <= maxCharacters * 1.35
    ) {
      repaired.push({
        text: `${cue.text} ${next.text}`,
        start_sec: cue.start_sec,
        end_sec: next.end_sec,
        words: [...(cue.words || []), ...(next.words || [])],
      });
      index += 1;
      continue;
    }
    repaired.push(cue);
  }
  return repaired;
};

export const activeCaptionCue = (cues: CaptionCue[], timeSec: number): CaptionCue | null => {
  for (const cue of cues) {
    if (cue.start_sec <= timeSec && timeSec < cue.end_sec) {
      return cue;
    }
  }
  // Hold the final cue through its last word end for phone readability (not past last cue).
  if (cues.length) {
    const last = cues[cues.length - 1];
    if (timeSec >= last.start_sec && timeSec <= last.end_sec) {
      return last;
    }
  }
  return null;
};

export const activeCaptionText = (cues: CaptionCue[], timeSec: number): string => {
  return activeCaptionCue(cues, timeSec)?.text ?? '';
};

export const activeWordIndexInCue = (cue: CaptionCue, timeSec: number): number => {
  for (let index = 0; index < cue.words.length; index += 1) {
    const word = cue.words[index];
    if (timeSec >= word.start_sec && timeSec < word.end_sec) {
      return index;
    }
  }
  if (cue.words.length && timeSec >= cue.words[cue.words.length - 1].end_sec) {
    return cue.words.length - 1;
  }
  return 0;
};

export const captionStateAtSceneTime = (
  scene: ManifestScene,
  localFrame: number,
  fps = FPS,
): {text: string; cue: CaptionCue | null; activeWordIndex: number; absoluteSec: number} => {
  const absoluteSec = scene.timing.start_sec + localFrame / fps;
  if (scene.caption_words && scene.caption_words.length > 0) {
    const cues = captionCuesFromWords(scene.caption_words);
    const cue = activeCaptionCue(cues, absoluteSec);
    return {
      text: cue?.text ?? '',
      cue,
      activeWordIndex: cue ? activeWordIndexInCue(cue, absoluteSec) : -1,
      absoluteSec,
    };
  }
  // Legacy demos without timings: even split of subtitle text (never headline).
  const chunks = captionChunks(scene.caption, 5);
  const durationFrames = Math.max(1, Math.round((scene.timing.end_sec - scene.timing.start_sec) * fps));
  const index = Math.min(chunks.length - 1, Math.floor((localFrame / durationFrames) * chunks.length));
  const text = chunks[index] ?? '';
  return {text, cue: null, activeWordIndex: -1, absoluteSec};
};

export const captionTextAtSceneTime = (scene: ManifestScene, localFrame: number, fps = FPS): string => {
  return captionStateAtSceneTime(scene, localFrame, fps).text;
};

export const selectImageAsset = (
  scene: ManifestScene,
  assetData: Record<string, string>,
): string | null => {
  for (const asset of scene.assets) {
    if (asset.role === 'selected_image' && asset.exists && asset.qa?.status === 'passed' && assetData[asset.asset_id]) {
      return assetData[asset.asset_id];
    }
  }
  return null;
};
