import type { ConversationMessage, ConversationSummary } from '../types/message'
import type { PipelineSnapshot, PipelineStep, PipelineStepId, PipelineStepState } from '../types/pipeline'
import type { SettingsControlCenterPayload } from '../types/settings'
import type { BriefingPayload } from '../types/briefing'
import { localCapabilityHeaders } from './localCapabilitySession'
import type {
  AccessChange,
  AccessRealitySnapshot,
  AccessSubjectStatus,
  AmbientState,
  AgentActionPreview,
  AgentArtifactSummary,
  AgentEmergencyStopResult,
  AgentLogEntry,
  AgentRecentLogsPayload,
  AgentStatusPayload,
  AgentToolDescriptor,
  CapabilityFlags,
  ContentReadinessItem,
  ContentReadinessSnapshot,
  JarvisBrowserSessionSummary,
  LocalHostChromeBridgeResult,
  LocalHostChromeDiagnostics,
  LocalHostControlSummary,
  ProviderCard,
  ProviderCardTestResult,
  RemoteExecutionRun,
  RemoteExecutionStep,
  RemoteWorkerCapabilitySnapshot,
  RemoteWorkerSummary,
  RuntimeSummary,
  TrustedAutoAccount,
  TrustedAutoAccountFeedback,
  TrustedAutoExecutionRun,
  TrustedAutoHardwareSnapshot,
  TrustedAutoMediaPipelinePreset,
  TrustedAutoBrowserRuntime,
  TrustedAutoSchedulerJob,
  TrustedAutoSummary,
  TrustedAutoTrustGrant,
} from '../types/system'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.trim() || 'http://127.0.0.1:8000'

export interface HealthPayload {
  status: string
  app?: string
  version?: string
  environment?: string
  deploymentMode?: string
}

export interface RuntimeHealthComponent {
  key: string
  label: string
  status: string
  verdict?: string
  detail: string
  nextAction: string
  firstAction?: string
  evidence?: Record<string, unknown>
  metadata: Record<string, unknown>
}

export interface RuntimeHealthPayload {
  status: string
  summary: {
    ready: number
    degraded: number
    missing: number
    blocked: number
  }
  readiness?: {
    verdict: string
    canRunCore: boolean
    headline: string
    firstAction: string
    blockingComponents: string[]
    degradedComponents: string[]
    evidence: Record<string, unknown>
  }
  components: RuntimeHealthComponent[]
  launcher: {
    scriptPath: string
    exists: boolean
    command: string
    logDir: string
  } | null
}

export interface RuntimeInventoryPayload {
  providers: Array<{
    provider_key: string
    label: string
    kind: string
    local_first: boolean
    is_enabled: boolean
    has_runtime_adapter: boolean
    status: string
    status_reason: string
    health_status: string
    health_reason: string
    installed_models: string[]
  }>
  models: Array<{
    model_key: string
    provider_key: string
    task: string
    is_enabled: boolean
    is_default: boolean
    status: string
    runtime_available: boolean | null
    runtime_model_present: boolean | null
    resolved_runtime_model_name: string | null
    runtime_status_reason: string
  }>
  tasks: Array<{
    task: string
    has_local_default: boolean
    has_external_default: boolean
    ready: boolean
    default_local_model_key: string | null
    default_external_model_key: string | null
  }>
  summary: {
    ready_providers: number
    enabled_models: number
    tasks_ready: number
    has_local_chat: boolean
    has_external_fallback: boolean
  }
  voice_stt: {
    effective_engine: string
    status: string
    status_reason: string
  }
}

export interface AiWorkerStatus {
  workerKey: string
  label: string
  status: string
  ready: boolean
  reason: string
  capabilities: string[]
  allowedTaskTypes: string[]
  requiresBrowser: boolean
  safetyMode: string
  metadata: Record<string, unknown>
}

export interface AiWorkersStatusPayload {
  mode: string
  workers: AiWorkerStatus[]
  guardrails: Record<string, unknown>
}

export interface QualityCouncilProblem {
  severity: 'critical' | 'high' | 'medium' | 'low' | string
  title: string
  cause: string
  suggestedFix: string
  files: string[]
  verification: string
  regressionRisk: string
}

export interface QualityCouncilReport {
  runId: string
  key: string
  title: string
  createdAt: string
  summary: string
  worksWell: string[]
  worksWrong: string[]
  notWorking: string[]
  notVerified: string[]
  confidence: string
  problems: QualityCouncilProblem[]
  markdownPath: string | null
  jsonPath: string | null
}

export interface QualityCouncilRun {
  runId: string
  createdAt: string
  runDir: string
  reports: QualityCouncilReport[]
  coordinator: QualityCouncilReport
  delivery: {
    status: string
    reason: string
    reportPath: string | null
    sent: boolean
    oauthStatus?: string
  }
}

export interface QualityCouncilStatusPayload {
  ready: boolean
  reportsDir: string
  latestRun: QualityCouncilRun | null
  gmailDelivery: {
    ready: boolean
    oauthStatus: string
    reason: string
  }
}

export interface AiWorkerTaskRun {
  id: number
  workerKey: string
  status: string
  durationMs: number | null
  resultSummary: string | null
  errorMessage: string | null
  startedAt: string | null
  completedAt: string | null
}

export interface AiWorkerTask {
  id: number
  taskType: string
  status: string
  priority: string
  requestedBy: string
  workerKey: string | null
  privacyMode: string
  parentTaskId: number | null
  taskGroup: string | null
  taskRole: string
  sequenceIndex: number
  requiresReview: boolean
  verificationStatus: string
  inputSummary: string | null
  redactedPrompt: string
  payloadJson: Record<string, unknown>
  resultSummary: string | null
  resultJson: Record<string, unknown> | null
  errorMessage: string | null
  blockedReason: string | null
  approvalRequired: boolean
  traceId: number | null
  createdAt: string | null
  updatedAt: string | null
  startedAt: string | null
  completedAt: string | null
  runs: AiWorkerTaskRun[]
  children: Array<{
    id: number
    taskType: string
    status: string
    workerKey: string | null
    taskRole: string
    sequenceIndex: number
    requiresReview: boolean
    verificationStatus: string
    inputSummary: string | null
    blockedReason: string | null
    resultSummary: string | null
  }>
}

export type ProductionReadinessStatus =
  | 'ready'
  | 'needs_configuration'
  | 'missing'
  | 'degraded'
  | 'optional_missing'

export interface ProductionReadinessItem {
  key: string
  label: string
  status: ProductionReadinessStatus | string
  required: boolean
  ready: boolean
  message: string
  evidence: Record<string, unknown>
}

export interface ProductionReadinessPayload {
  status: 'ready' | 'ready_for_private_review' | 'needs_configuration' | string
  ready: boolean
  local_runtime_ready?: boolean
  publish_ready?: boolean
  private_review_ready?: boolean
  private_upload_ready?: boolean
  public_upload_enabled?: boolean
  public_publish_ready?: boolean
  full_pipeline_ready?: boolean
  blocking_count: number
  warning_count: number
  blocking: string[]
  local_runtime_blocking?: string[]
  publish_blocking?: string[]
  public_publish_blocking?: string[]
  warnings: string[]
  items: ProductionReadinessItem[]
}

export type MediaObservabilityAlert = 'HEALTHY' | 'DEGRADED' | 'BLOCKED' | 'ERROR'

export interface MediaObservabilityErrorItem {
  component: string
  timestamp: string | null
  error_code: string
  short_message: string
  job_request_id: string | null
  retry_count: number
}

export interface MediaObservabilityPayload {
  schema_version: string
  timestamp: string
  overall_alert: MediaObservabilityAlert
  live: boolean
  comfy: Record<string, unknown>
  gpu: Record<string, unknown>
  cache: Record<string, unknown>
  assets: Record<string, unknown>
  quality: Record<string, unknown>
  models: Record<string, unknown>
  orders: Record<string, unknown>
  channels: Record<string, unknown>
  errors: MediaObservabilityErrorItem[]
  performance: Record<string, unknown>
  history: Record<string, unknown>
}

export interface ReferenceLearningStatus {
  status: 'not_started' | 'starting' | 'running' | 'completed' | 'failed' | string
  phase: string
  profile_key?: string
  target_count: number
  selected_count: number
  transcript_count: number
  visual_count: number
  failure_count: number
  channel_distribution?: Record<string, number>
  current_channel?: string
  current_video_id?: string
  message?: string
  started_at?: string
  updated_at?: string
  completed_at?: string
  report_path?: string
  evidence_path?: string
  editorial_profile_path?: string
  policy: string
}

export interface ChannelConfig {
  channel_id: string
  channel_name: string
  language: string
  region: string
  format: string
  max_duration_seconds: number
  niches: string[]
  visual_style: string
  narration_style?: string
  allowed_topics?: string[]
  forbidden_topics?: string[]
  reference_channels: string[]
  competitor_channels: string[]
  publish_schedule: {
    enabled?: boolean
    frequency_per_week: number
    days_of_week: string[]
    time_utc: string
  }
  target_audience: string
  [key: string]: unknown
}

export interface ChannelConfigResponse {
  config: ChannelConfig
  warnings: string[]
  path: string
  status?: string
}

export interface EnsureModelPipelineResult {
  status: 'download_started' | 'ready' | 'partial' | string
  pipeline: string
  results?: Record<string, boolean> | null
}

export interface ModelDownloadProgressPayload {
  pipeline: string
  status: 'idle' | 'running' | 'ready' | 'partial' | string
  started_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
  current_model_id?: string | null
  progress: Record<string, number>
  messages: Record<string, string>
  results?: Record<string, boolean> | null
}

export interface ProductionStartResult {
  job_id: string
  account_id: string
  topic_id: string
  status: string
  preflight?: Record<string, unknown>
  delegation_review?: ProductionDelegationReviewResult | ProductionDelegationReviewStatus | null
}

export interface ProductionDelegationReviewChildTask {
  id: number
  task_type: string
  status: string
  worker_key: string | null
  task_role: string
  sequence_index: number
  requires_review: boolean
  verification_status: string
  input_summary: string | null
  blocked_reason: string | null
  result_summary: string | null
}

export interface ProductionDelegationReviewRootTask {
  id: number
  task_type: string
  status: string
  worker_key: string | null
  task_role: string
  task_group: string | null
  result_summary: string | null
  blocked_reason: string | null
  children: ProductionDelegationReviewChildTask[]
}

export interface ProductionDelegationReviewSummary {
  verdict: 'production_ready' | 'blocked' | string
  can_start_production: boolean
  headline: string
  first_action: string
  completed_tasks: number
  total_tasks: number
  completed_review_tasks: number
  required_review_tasks: number
  queued_tasks: number
  running_tasks: number
  blocked_tasks: number
  failed_tasks: number
  blockers: string[]
  evidence: {
    source: string
    root_task_id: number | null
    child_task_ids: number[]
    latest_result_summaries: string[]
  }
}

export interface ProductionDelegationReviewStatus {
  account_id: string
  topic_id: string
  has_review_plan: boolean
  ready_for_review: boolean
  ready_for_execution?: boolean
  ready_for_human_review?: boolean
  ready_for_production_start?: boolean
  production_gate_blockers?: string[]
  status: string
  reason: string
  root_task: ProductionDelegationReviewRootTask | null
  counts: {
    matching_root_tasks: number
    latest_child_tasks: number
    latest_review_tasks: number
    latest_completed_review_tasks?: number
    latest_queued_child_tasks?: number
    latest_running_child_tasks?: number
    latest_completed_child_tasks?: number
    latest_blocked_child_tasks?: number
    latest_failed_child_tasks?: number
  }
  review_summary?: ProductionDelegationReviewSummary
  next_action: string
}

export interface ProductionDelegationReviewResult {
  status: string
  account_id: string
  topic_id: string
  ready_for_review: boolean
  source: string
  evidence: Record<string, unknown>
  root_task: ProductionDelegationReviewRootTask
  latest: ProductionDelegationReviewStatus
}

export interface ProductionDelegationReviewRunResult {
  status: string
  account_id: string
  topic_id: string
  ran_task: Record<string, unknown> | null
  reason: string
  latest: ProductionDelegationReviewStatus
  review_summary?: ProductionDelegationReviewSummary
}

export interface ProductionDelegationReviewRunAllResult {
  status: string
  account_id: string
  topic_id: string
  ran_count: number
  limit: number
  results: ProductionDelegationReviewRunResult[]
  reason: string
  latest: ProductionDelegationReviewStatus
  review_summary?: ProductionDelegationReviewSummary
}

export interface LocalHostScreenArtifact {
  kind: string
  path: string
  metadataPath: string
  label: string
  createdAt: string
  durationSeconds: number | null
}

export interface ProductionJobStep {
  step: string
  status: string
  progress: number
  attempt?: number
  message?: string | null
  started_at?: string | null
  completed_at?: string | null
}

export interface ProductionJobPayload {
  id: string
  account_id: string
  topic_id: string
  topic_title?: string | null
  topic_description?: string | null
  topic_total_score?: number | null
  status: string
  current_step?: string | null
  error?: string | null
  request?: Record<string, unknown>
  profile_hash?: string | null
  worker_live?: boolean
  can_resume?: boolean
  recovery?: ProductionOrderRecovery
  autonomous_recovery?: ProductionOrderAutonomy
  reserved_cost_units?: number
  budget_limit_usd?: number
  provider_calls?: number
  limited_provider_calls?: number
  local_work_unlimited?: boolean
  current_correction?: {
    status: 'registered' | 'unverified'
    source: 'checkpoint_metadata'
    action_id?: string
    kind?: 'voice_and_render' | 'render'
    registered_at?: string
    expensive_stage_attempt_limit?: number | null
    completed?: number
    total?: number
    stages: Array<{ stage: string; status: string; reused: boolean; completed_at?: string | null }>
  } | null
  local_image_action?: {
    status: 'verified' | 'integrity_blocked'
    provider: 'comfyui-local'
    action_key?: string
    max_new_requests?: number
    used_new_requests?: number
    remaining_new_requests?: number
    pending_or_unknown?: boolean
  } | null
  max_provider_calls?: number
  can_extend_call_limit?: boolean
  artifact_dir?: string | null
  qa_report_path?: string | null
  upload_package_path?: string | null
  youtube_video_id?: string | null
  youtube_url?: string | null
  review_status?: string | null
  quality_review_status?: string | null
  quality_review_result?: ProductionQualityReviewJobResult | null
  quality_review_checked_at?: string | null
  private_upload_blockers?: string[]
  public_promotion_blockers?: string[]
  public_quality_evidence?: Record<string, unknown>
  ready_for_review: boolean
  ready_for_private_upload: boolean
  ready_for_public_promotion?: boolean
  steps: ProductionJobStep[]
  created_at?: string | null
  updated_at?: string | null
}

export interface ProductionOrderAutonomy {
  enabled: boolean
  used?: number
  maximum?: number | null
  limit_reached?: boolean
  publication_enabled?: boolean
}

export interface ProductionOrderRecovery {
  job_id: string
  kind: 'script' | 'assets' | 'resource' | 'integrity' | 'other'
  message: string
  issues: Array<{ code: string; segment_ids: string[]; detail: string; message?: string }>
  can_repair_script: boolean
  repair_blocker: string | null
  worker_live: boolean
}

export interface ProductionAssetCandidate {
  scene_id: string
  asset_id: string
  acquisition_status: string | null
  quality_status: string
  qc_verdict: string | null
  qc_failed: string[]
  reject_reason: string | null
  reject_notes: string
  reviewed_by: string | null
  pre_gate: Record<string, unknown>
  image_url: string | null
  can_approve: boolean
  can_reject: boolean
  approval_blocker: string | null
}

export interface ProductionAssetCandidatesResponse {
  job_id: string
  candidates: ProductionAssetCandidate[]
}

export interface ProductionReviewPackage {
  job_id: string
  status: string
  ready_for_review: boolean
  title: string
  description: string
  tags: string[]
  video_ready: boolean
  thumbnail_ready: boolean
  script_preview?: {
    text: string
    word_count: number
    segments: { id: string; text: string; visual_intent: string; on_screen: string }[]
    characters: { id: string; name: string; appearance: string }[]
    qa: { status: string; human_review: string }
  } | null
  video_url: string | null
  thumbnail_url: string | null
  blocking_failures: string[]
  quality_review_status?: string | null
  updated_at?: string | null
}

export interface ProductionDistributionPlatform {
  platform: 'youtube_shorts' | 'tiktok' | 'instagram_reels' | string
  label: string
  connector_key: string
  status: string
  export_ready: boolean
  upload_ready: boolean
  private_upload_ready: boolean
  public_upload_ready: boolean
  requires_manual_upload: boolean
  requires_owner_approval: boolean
  connector_status: string
  blockers: string[]
  public_blockers: string[]
  warnings: string[]
  artifact_paths: Record<string, string>
  metadata: Record<string, unknown>
  next_actions: string[]
  endpoints: Record<string, string>
}

export interface ProductionDistributionPlan {
  job_id: string
  account_id?: string | null
  topic_id?: string | null
  status: string
  export_ready: boolean
  public_quality_ready: boolean
  artifact_blockers: string[]
  public_quality_blockers: string[]
  quality_evidence: Record<string, unknown>
  platforms: ProductionDistributionPlatform[]
  next_actions: string[]
}

export interface ProductionDistributionPackageItem {
  platform: string
  label: string
  status: string
  package_dir: string
  manifest_path: string
  readme_path: string
  caption_path: string
  title_path: string
  description_path: string
  hashtags_path: string
  video_file_path: string
}

export interface ProductionDistributionPackageResult {
  job_id: string
  account_id?: string | null
  topic_id?: string | null
  status: string
  package_root: string
  created_count: number
  blocked_count: number
  packages: ProductionDistributionPackageItem[]
  blocked: Array<{
    platform: string
    label: string
    status: string
    blockers: string[]
    public_blockers: string[]
  }>
  plan: ProductionDistributionPlan
}

export interface DistributionCockpitPlatform {
  platform: string
  label: string
  status: string
  connector_status: string
  account_connected: boolean
  account_status: string
  account_count: number
  export_ready: boolean
  release_package_ready: boolean
  api_private_ready: boolean
  automatic_upload_ready: boolean
  requires_manual_upload: boolean
  requires_owner_approval: boolean
  blockers: string[]
  next_actions: string[]
  artifact_paths: Record<string, string>
  metadata: Record<string, unknown>
}

export interface DistributionCockpitPayload {
  generated_at: string
  account_id?: string | null
  status: string
  latest_job?: ProductionJobPayload | Record<string, unknown> | null
  latest_plan?: ProductionDistributionPlan | null
  jobs: Array<Record<string, unknown>>
  platform_accounts: Record<string, Record<string, unknown>>
  platforms: DistributionCockpitPlatform[]
  ready_package_count: number
  blockers: string[]
  capabilities: {
    can_prepare_release_packages: boolean
    can_upload_youtube_private_via_api: boolean
    can_upload_tiktok_automatically: boolean
    can_upload_instagram_automatically: boolean
    requires_owner_review: boolean
  }
  next_actions: string[]
  safety: Record<string, unknown>
}

export interface DistributionPackageLatestResult {
  status: string
  reason?: string
  job_id?: string
  account_id?: string | null
  package?: ProductionDistributionPackageResult
  packages?: unknown[]
  blocked?: unknown[]
  cockpit: DistributionCockpitPayload
}

export interface ProductionPrivateUploadResult {
  status: 'uploaded_private' | 'connection_required' | 'error' | string
  job_id: string
  youtube_video_id?: string
  youtube_url?: string
  visibility: 'private' | string
  message?: string
  analytics_followups?: Array<{
    job_id: string
    window_hours: number
    due_at: string
    status: string
  }>
  upload?: Record<string, unknown>
}

export interface ProductionRegenerateResult {
  status: string
  source_job_id: string
  job_id: string
  account_id: string
  topic_id: string
  reason: string
  message?: string
}

export interface ProductionQualityReviewStatusPayload {
  enabled: boolean
  require_public_quality: boolean
  interval_minutes: number
  max_jobs_per_tick: number
  pending_count: number
}

export interface ProductionQualityReviewJobResult {
  status: string
  job_id: string
  account_id?: string | null
  topic_id?: string | null
  checked_at?: string | null
  blockers: string[]
  public_quality_ready?: boolean
  public_quality_evidence?: Record<string, unknown>
  thumbnail_quality?: Record<string, unknown>
  thumbnail_semantic_audit?: Record<string, unknown>
  first_frame_semantic_audit?: Record<string, unknown>
  upload_package_public_quality_ready?: boolean | null
  upload_package_public_quality_blockers?: string[]
  next_action?: string | null
  reason?: string | null
}

export interface ProductionQualityReviewRunResult {
  status: string
  checked_count: number
  blocked_count: number
  passed_count: number
  jobs: ProductionQualityReviewJobResult[]
}

export interface ProductionQualityRemediationStatusPayload {
  enabled: boolean
  run_jobs: boolean
  interval_minutes: number
  max_jobs_per_tick: number
  candidate_count: number
}

export interface ProductionQualityRemediationRunResult {
  status: string
  planned_count: number
  started_count: number
  results: Array<Record<string, unknown>>
}

export interface ProductionPublicQualityStatusPayload {
  ready: boolean
  blockers: string[]
  voice_provider: {
    checked?: boolean
    public_voice_ready?: boolean
    selected_tts_backend?: string
    next_action?: string
    report_path?: string | null
    providers?: Record<string, Record<string, unknown>>
  }
  visual_backend: {
    checked?: boolean
    public_visual_ready?: boolean
    selected_video_backend?: string
    effective_video_backend?: string
    next_action?: string
    report_path?: string | null
    providers?: Record<string, Record<string, unknown>>
  }
  style_guide: {
    ready?: boolean
    status?: string
    source?: string | null
    used_for_account?: string | null
    next_action?: string | null
  }
  next_actions: string[]
}

export interface ProductionStyleGuideEnsureResult {
  status: string
  created: boolean
  path: string | null
  message: string
  public_quality: ProductionPublicQualityStatusPayload
}

export interface ProductionProviderDoctorCard {
  key: string
  label: string
  status: 'ready' | 'blocked' | 'warning' | 'unknown' | string
  summary: string
  next_action: string
  evidence: Record<string, unknown>
  critical: boolean
}

export interface ProductionProviderDoctorAction {
  id: string
  card_key: string
  label: string
  kind: 'frontend_api' | 'manual_endpoint' | 'manual_review' | string
  method?: string | null
  endpoint?: string | null
  safe_to_run: boolean
  requires_owner_action: boolean
  reason: string
  payload: Record<string, unknown>
}

export interface ProductionProviderDoctorPayload {
  generated_at: string
  status: 'ready' | 'needs_attention' | string
  account_id?: string | null
  critical_blockers: string[]
  warnings: string[]
  cards: ProductionProviderDoctorCard[]
  operator_actions: ProductionProviderDoctorAction[]
  public_quality: {
    ready: boolean
    blockers: string[]
    next_actions: string[]
  }
  money_machine: {
    status?: string | null
    public_upload_enabled: boolean
    human_approval_before_upload: boolean
    global_blockers: string[]
    next_actions: Array<Record<string, unknown> | string>
    totals?: Record<string, unknown>
    accounts?: Array<Record<string, unknown>>
  }
}

export interface ProductionProviderDoctorActionResult {
  status: string
  action: ProductionProviderDoctorAction
  result: Record<string, unknown>
  provider_doctor: ProductionProviderDoctorPayload
}

export interface F5TTSRuntimeStatus {
  transport?: 'cli' | 'gradio'
  transport_ready?: boolean
  endpoint: string
  endpoint_ready: boolean
  endpoint_status_code: number | null
  endpoint_error: string | null
  package_installed: boolean
  package_module_path: string | null
  gradio_installed: boolean
  runtime_python: string | null
  runtime_python_exists: boolean
  runtime_package_installed: boolean
  runtime_gradio_installed: boolean
  runtime_probe_error: string | null
  install_dir: string | null
  install_exists: boolean
  detected_files: string[]
  launch_command_configured: boolean
  launch_supported: boolean
  reference_audio_path: string | null
  reference_audio_exists: boolean
  diagnosis_code: string
  diagnosis_message: string
  blocking_reasons: string[]
  next_action: string
  suggested_commands: string[]
  started: boolean
}

export interface ProductionOperatingStandardItem {
  key: string
  label: string
  purpose: string
  status: string
  gate: string
  required: boolean
}

export interface ProductionOperatingStandardSection {
  key: string
  title: string
  items: ProductionOperatingStandardItem[]
}

export interface ProductionOperatingStandardsPayload {
  status: string
  project_id?: string | null
  project_type: string
  generated_at: string
  source: {
    path?: string | null
    exists: boolean
    last_modified?: string | null
  }
  summary: string
  sections: ProductionOperatingStandardSection[]
  forbidden: string[]
  metrics: {
    required_count: number
    blocked_until_configured_count: number
  }
  next_actions: string[]
}

export interface MoneyMachineSummaryJob {
  account_id: string
  job_id: string
  topic_id: string
  topic_title: string
  action: string
  reason: string
  public_quality_blockers: string[]
  recovery_action: string
  recovery_endpoint: string
  upload_package_path: string
  qa_report_path: string
  quality?: {
    voice?: string | null
    video_backend?: string | null
    scene_concreteness_risk_count?: number | null
    generated_image_text_risk_count?: number | null
    generated_image_visual_risk_count?: number | null
  }
}

export interface MoneyMachineActiveJob {
  account_id: string
  job_id: string
  topic_id?: string | null
  topic_title?: string | null
  topic_total_score?: number | null
  status: string
  current_step?: string | null
  created_at?: string | null
  updated_at?: string | null
  heartbeat_age_seconds?: number | null
  stale_after_seconds?: number | null
  stale_candidate?: boolean | null
}

export interface MoneyMachineSummaryAccount {
  account_id: string
  channel_title?: string | null
  next_action: string
  ready_private_upload_count: number
  blocked_private_upload_count?: number
  active_jobs?: number
  active_job_details?: MoneyMachineActiveJob[]
  topic_buffer?: number
  pending_topics?: number
  approved_topics?: number
  max_pending_score?: number
  oauth_ready?: boolean
  captions_api_ready?: boolean
  custom_thumbnail_ready?: boolean
  own_performance_rows?: number
  topic_counts?: {
    pending?: number
    approved?: number
    rejected?: number
    in_production?: number
    done?: number
    buffer_count?: number
    max_scores?: Record<string, number>
  }
  job_counts?: {
    qa_passed?: number
    uploaded_private?: number
    active?: number
    ready_private?: number
    public?: number
    failed?: number
  }
  signals?: {
    youtube_trend_videos?: number
    competitor_videos?: number
    google_trends_keywords?: number
  }
  analytics?: {
    own_performance_rows?: number
    last_own_performance_at?: string | null
    scheduled_followups?: number
    due_followups?: number
    completed_followups?: number
    followups?: {
      completed?: number
      scheduled?: number
    }
  }
  youtube_oauth?: {
    ready?: boolean
    connected?: boolean
    configured?: boolean
    account_id?: number | null
    mapped_email?: string | null
    warnings?: string[]
    granted_scopes?: string[]
  }
  upload_capabilities?: {
    captions_api?: {
      ready?: boolean
      status?: string
      reason?: string
      mode?: string
    }
    custom_thumbnail?: {
      ready?: boolean
      status?: string
      reason?: string
      mode?: string
      last_warning?: string
    }
    recent_warnings?: string[]
  }
  blockers: string[]
}

export interface MoneyMachineCostGuardSummary {
  enabled: boolean
  allowed?: boolean | null
  blocked: boolean
  warning: boolean
  currency: string
  estimated_action_cost_pln: number
  monthly_spent_pln: number
  monthly_limit_pln?: number | null
  monthly_remaining_pln?: number | null
  daily_spent_pln: number
  daily_limit_pln?: number | null
  daily_remaining_pln?: number | null
}

export interface MoneyMachineOperatorSummaryPayload {
  status: string
  generated_at?: string | null
  public_upload_enabled: boolean
  human_approval_before_upload: boolean
  voice_provider_ready: boolean
  cost_guard?: MoneyMachineCostGuardSummary
  totals: {
    accounts: number
    ready_private_upload_jobs: number
    public_ready_private_upload_jobs: number
    blocked_private_upload_jobs: number
    public_promotion_jobs: number
    active_jobs: number
    topic_buffer: number
    approved_topics: number
    due_analytics_followups: number
  }
  next_operator_action: string
  active_jobs?: MoneyMachineActiveJob[]
  private_upload_ready: MoneyMachineSummaryJob[]
  recovery_ready: MoneyMachineSummaryJob[]
  accounts: MoneyMachineSummaryAccount[]
  next_actions: Array<{ scope: string; action: string }>
  global_blockers: string[]
  notes: string[]
}

export interface MoneyMachineRunNextPayload {
  accountId?: string
  maxUploads?: number
  perAccountLimit?: number
  execute?: boolean
  approvedPrivateUpload?: boolean
  executeRecovery?: boolean
}

export interface MoneyMachineRunNextResult {
  status: string
  execute?: boolean
  approved_private_upload?: boolean
  execute_recovery?: boolean
  planned_count?: number
  blocked_count?: number
  executed_count?: number
  recovery_count?: number
  planned?: MoneyMachineSummaryJob[]
  blocked?: MoneyMachineSummaryJob[]
  results?: Array<Record<string, unknown>>
  recovery_results?: Array<Record<string, unknown>>
}

export interface MoneyMachinePublicationJob {
  account_id: string
  job_id: string
  topic_id?: string
  topic_title: string
  youtube_video_id?: string
  youtube_url?: string
  action: string
  reason: string
  public_quality_blockers?: string[]
  public_quality_evidence?: Record<string, unknown>
  recovery_action?: string
  recovery_endpoint?: string
}

export interface MoneyMachineRunPublicationPayload {
  accountId?: string
  maxPublications?: number
  perAccountLimit?: number
  execute?: boolean
  approvedPublicPromotion?: boolean
  publishAt?: string | null
}

export interface MoneyMachineRunPublicationResult {
  status: string
  execute?: boolean
  approved_public_promotion?: boolean
  per_account_limit?: number
  publish_at?: string | null
  planned_count?: number
  blocked_count?: number
  executed_count?: number
  planned?: MoneyMachinePublicationJob[]
  blocked?: MoneyMachinePublicationJob[]
  results?: Array<Record<string, unknown>>
  money_machine?: MoneyMachineOperatorSummaryPayload
}

export interface ProductionContentLoopRunResult {
  status: string
  generated_count: number
  approved_count: number
  rejected_count: number
  started_count: number
  jobs: Array<Record<string, unknown>>
  errors: Array<Record<string, unknown> | string>
  accounts: Array<Record<string, unknown>>
  production?: Record<string, unknown>
}

export interface ArtifactDescriptor {
  filename: string
  mime: string
  storagePath: string
  sizeBytes: number
  fileExtension: string
}

export interface ScheduledTaskDescriptor {
  scheduledTaskId: number
  conversationId: number | null
  projectId: number | null
  projectName: string | null
  userId: number | null
  promptTemplate: string
  cronSpec: string
  nextRunAt: string | null
  lastRunAt: string | null
  lastRunStatus: string | null
  isActive: boolean
  createdAt: string
  updatedAt: string
}

export interface IntelligenceYoutubeSignal {
  id?: number
  video_id: string
  title: string
  view_count: number
  like_count: number
  comment_count: number
  published_at?: string | null
  channel_id?: string | null
  channel_title?: string | null
  thumbnail_url?: string | null
  niche?: string | null
  collected_at: string
}

export interface IntelligenceGoogleTrendSignal {
  id?: number
  keyword: string
  interest_score: number
  related_rising: Array<{ query: string; value?: number | string | null }>
  related_topics?: Array<{ query: string; value?: number | string | null }>
  timeframe: string
  geo: string
  collected_at: string
  status?: string
}

export interface IntelligenceCompetitorChannel {
  id?: number
  account_id?: string
  channel_id: string
  channel_title?: string | null
  avg_views_7d: number
  posting_frequency_per_week: number
  top_format?: string | null
  last_refreshed?: string | null
}

export interface IntelligenceCompetitorVideo {
  id?: number
  channel_id: string
  video_id: string
  title: string
  views: number
  likes: number
  comments: number
  published_at?: string | null
  duration_seconds?: number | null
  thumbnail_url?: string | null
}

export interface IntelligenceOwnPerformance {
  id?: number
  account_id: string
  video_id: string
  title?: string | null
  views: number
  watch_time_min: number
  avg_view_pct?: number | null
  ctr?: number | null
  subs_gained: number
  collected_at: string
}

export interface IntelligenceReferenceChannel {
  id?: number
  channel_id: string
  channel_title?: string | null
  hook_patterns: string[]
  avg_duration_sec: number
  posting_cadence_per_week: number
  avg_engagement_rate: number
  thumbnail_style_tags?: string[]
  hook_pattern_summary?: string | null
  last_refreshed?: string | null
}

export interface IntelligenceRunPayload {
  accountId: string
  niches: string[]
  competitorUrls: string[]
  referenceUrls: string[]
  region?: string
}

export type IntelligenceAutomationMode = 'manual' | 'auto_private' | 'auto_public'

export interface IntelligenceAccountConfig {
  account_id: string
  niches: string[]
  region: string
  timeframe: string
  enabled: boolean
  automation_mode: IntelligenceAutomationMode
  channel_id?: string | null
  channel_title?: string | null
  content_profile?: string | null
  last_refresh_at?: string | null
  next_refresh_at?: string | null
}

export type TopicQueueStatus = 'pending' | 'approved' | 'rejected' | 'in_production' | 'done'

export interface TopicScoreDimensions {
  viral_potential: number
  search_demand: number
  competition_gap: number
  trend_velocity: number
  niche_fit: number
  freshness: number
}

export interface TopicScoreFlags {
  clickbait_risk: boolean
  evergreen: boolean
  shorts_potential: boolean
  llm_unavailable?: boolean
}

export interface TopicQueueItem {
  id: string
  account_id: string
  title: string
  topic?: string
  description: string
  inspired_by?: string
  status: TopicQueueStatus
  total_score: number
  dimensions: TopicScoreDimensions
  flags: TopicScoreFlags
  created_at: string
  updated_at: string
}

export type OperationalMemoryWorkItemStatus = 'active' | 'blocked' | 'waiting' | 'done'
export type OperationalMemoryWorkItemPriority = 'P0' | 'P1' | 'P2'
export type OperationalMemoryWorkItemReviewState =
  | 'unreviewed'
  | 'in_review'
  | 'reviewed'
export type OperationalMemorySourceNoteDecision =
  | 'accepted'
  | 'follow_up'
  | 'rejected'
  | 'undecided'
export type OperationalMemorySourceNoteStatus = 'open' | 'selected' | 'closed'
export type OperationalMemoryDecisionStatus = 'active' | 'resolved' | 'expired'
export type OperationalMemoryDecisionKind =
  | 'decision'
  | 'commitment'
  | 'open_question'
  | 'pending_action'

export interface OperationalMemoryWorkItemDecisionContext {
  activeCount: number
  latestSummary: string | null
  latestKind: OperationalMemoryDecisionKind | null
  latestDueAt: string | null
  recentEntries: OperationalMemoryDecisionEntry[]
}

export interface OperationalMemoryWorkItem {
  id: number
  createdAt: string
  updatedAt: string
  projectId: number | null
  sourceNoteId: number | null
  title: string
  summary: string
  nextStep: string | null
  status: OperationalMemoryWorkItemStatus
  priority: OperationalMemoryWorkItemPriority
  reviewState: OperationalMemoryWorkItemReviewState
  blockedReason: string | null
  sourceNote: OperationalMemorySourceNote | null
  decisionContext: OperationalMemoryWorkItemDecisionContext | null
}

export interface OperationalMemorySourceNote {
  id: number
  source: string
  view: string
  friction: string
  severity: OperationalMemoryWorkItemPriority
  proposedFix: string | null
  chosenNextPatch: string | null
  decision: OperationalMemorySourceNoteDecision
  status: OperationalMemorySourceNoteStatus
}

export interface OperationalMemoryDecisionEntry {
  id: number
  conversationId: number | null
  projectId: number | null
  kind: OperationalMemoryDecisionKind
  summary: string
  madeAt: string
  status: OperationalMemoryDecisionStatus
  dueAt: string | null
  resolvedAt: string | null
  resolutionSummary: string | null
  evidenceMessageIds: number[]
}

export interface OperationalMemorySnapshot {
  projectId: number | null
  sourceNote: OperationalMemorySourceNote | null
  workItems: OperationalMemoryWorkItem[]
  activeDecisions: OperationalMemoryDecisionEntry[]
}

export interface OperationalMemoryPromotionDraft {
  sourceNoteId: number
  title: string
  summary: string
  nextStep: string | null
  priority: OperationalMemoryWorkItemPriority
}

export interface OperationalMemoryPromotionPreview {
  projectId: number
  note: OperationalMemorySourceNote | null
  workItemDraft: OperationalMemoryPromotionDraft | null
  eligible: boolean
  reason: string | null
  warnings: string[]
}

export interface BootstrapPayload {
  uiVersion: string
  generatedAt: string
  projects: Array<{
    id: number
    name: string
    slug: string
    description: string | null
    summary: string | null
    status: string
    isPinned: boolean
    providerPosture: ProjectProviderPosture | null
    personaProfile: ProjectPersonaProfile | null
  }>
  recentConversations: ConversationSummary[]
  runtimeSummary: RuntimeSummary
  providerCards: ProviderCard[]
  oauthConnectors: Array<{
    connectorKey: string
    displayName: string
    status: string
    enabled: boolean
    configured: boolean
    connectionId: number | null
    connectedAt: string | null
    setupWarnings: string[]
  }>
  capabilities: CapabilityFlags
  pipeline: PipelineSnapshot
  trustedAutoSummary: TrustedAutoSummary
  accessReality: AccessRealitySnapshot | null
  ambientState: AmbientState | null
  remoteWorkers: RemoteWorkerSummary[]
}

export interface BootstrapShellPayload {
  uiVersion: string
  generatedAt: string
  projects: BootstrapPayload['projects']
  recentConversations: ConversationSummary[]
}

export interface VoiceTranscriptionPayload {
  transcript: string
  language: string
  engine: string
  durationMs: number
}

export interface RemoteNodeBootstrapPayload {
  host: string
  sshPort?: number
  user: string
  privateKeyPath: string
  controlPlaneBaseUrl: string
  label?: string | null
}

export interface RemoteNodeBootstrapResult {
  remoteWorker: RemoteWorkerSummary
  bootstrapStatus: string
}

export interface LocalHostCommandResult {
  actionKind: string
  command: string
  exitCode: number
  stdout: string
  stderr: string
  workdir: string
  elevated: boolean
  startedAt: string
  finishedAt: string
}

export interface RemoteExecutionDispatchPayload {
  taskKind: string
  payload: Record<string, unknown>
  preferredNodeId?: number | null
  origin?: 'manual' | 'scheduler' | 'trusted_auto' | 'system'
}

export type ProjectProviderPosture =
  | 'prefer-local'
  | 'improve-when-needed'
  | 'ask-before-external'
  | 'strict-local'

export type ProjectPersonaProfile = 'strategist' | 'critic' | 'executor' | 'mentor' | 'jarvis-mode'

export interface ProjectMutationPayload {
  name: string
  description?: string | null
  providerPosture?: ProjectProviderPosture | null
  personaProfile?: ProjectPersonaProfile | null
}

export interface ProjectMutationResult {
  projectId: number
  name: string
  slug: string
  description: string | null
  summary: string | null
  status: string
  isPinned: boolean
  providerPosture: ProjectProviderPosture | null
  personaProfile: ProjectPersonaProfile | null
}

export type ProjectPlatformResourceType =
  | 'account'
  | 'channel'
  | 'mailbox'
  | 'broker_account'
  | 'workspace'
  | 'profile'

export type ProjectPlatformEnvironment = 'demo' | 'test' | 'production'

export type ProjectPlatformPermissionProfile =
  | 'metadata_only'
  | 'read_only'
  | 'draft_write'
  | 'supervised_write_later'
  | 'publish_preview_later'
  | 'financial_read_later'
  | 'trading_preview_later'

export interface ProjectPlatformAccountSummary {
  id: number
  label: string
  providerKey: string
  accountIdentifier: string
  status: string
  riskLevel: string
  linkedIntegrationKey: string | null
}

export interface ProjectPlatformBinding {
  id: number
  projectId: number
  integrationKey: string
  customIntegrationId: number | null
  accountId: number | null
  accountSummary: ProjectPlatformAccountSummary | null
  resourceType: ProjectPlatformResourceType
  resourceExternalId: string | null
  resourceLabel: string | null
  bindingRole: string
  environment: ProjectPlatformEnvironment
  confirmationPolicy: 'none' | 'supervised' | 'always_confirm'
  delegationPolicy: 'disabled' | 'project_only' | 'approved_workers'
  allowSharedAccount: boolean
  permissionScopes: string[]
  connectionStatus: string
  connectionDetail: string | null
  lastVerifiedAt: string | null
  verificationEvidence: Record<string, unknown>
  label: string
  purpose: string | null
  status: 'planned' | 'configured' | 'disabled' | 'needs_review'
  permissionProfile: ProjectPlatformPermissionProfile
  allowedCapabilities: string[]
  safetyNotes: string | null
  notes: string | null
  integrationLabel: string
  createdAt: string
  updatedAt: string
}

export interface ProjectPlatformBindingCreatePayload {
  integrationKey: string
  accountId?: number | null
  label: string
  purpose?: string | null
  status?: ProjectPlatformBinding['status']
  permissionProfile: ProjectPlatformPermissionProfile
  resourceType: ProjectPlatformResourceType
  resourceExternalId: string
  resourceLabel?: string | null
  environment: ProjectPlatformEnvironment
  permissionScopes: string[]
  bindingRole?: string
  confirmationPolicy?: ProjectPlatformBinding['confirmationPolicy']
  delegationPolicy?: ProjectPlatformBinding['delegationPolicy']
  allowSharedAccount?: boolean
}

export interface ProjectPlatformBindingUpdatePayload {
  accountId?: number | null
  label?: string
  purpose?: string | null
  status?: ProjectPlatformBinding['status']
  permissionProfile?: ProjectPlatformPermissionProfile
  resourceType?: ProjectPlatformResourceType
  resourceExternalId?: string
  resourceLabel?: string | null
  environment?: ProjectPlatformEnvironment
  permissionScopes?: string[]
  bindingRole?: string
  confirmationPolicy?: ProjectPlatformBinding['confirmationPolicy']
  delegationPolicy?: ProjectPlatformBinding['delegationPolicy']
  allowSharedAccount?: boolean
}

export interface ProjectReadinessRequirement {
  id: string
  label: string
  status: 'ready' | 'limited' | 'missing' | string
  detail: string
}

export interface ProjectAccessPlaybook {
  mode: string
  required_connections: Array<{
    key: string
    label: string
    status: 'ready' | 'missing' | 'limited' | string
    where: string
    notes: string
  }>
  allowed_now: string[]
  blocked_until_configured: string[]
  operator_warning: string
}

export interface ProjectDistributionTarget {
  platform: string
  label: string
  status: 'ready' | 'missing' | 'limited' | string
  connected: boolean
  account_identifier?: string | null
  ready_for_private: boolean
  ready_for_public: boolean
  required_connection: string
  next_action: string
}

export interface ProjectToolingItem {
  key: string
  label: string
  purpose: string
  status: 'ready' | 'limited' | 'missing' | 'planned' | 'blocked' | string
  detail?: string
  next_action: string
  link?: string | null
}

export interface ProjectToolingPlan {
  report_ref?: string
  report_kind?: string
  report_updated_at?: string
  must_have: ProjectToolingItem[]
  nice_to_have: ProjectToolingItem[]
  not_now: ProjectToolingItem[]
  cross_platform_ready?: boolean
  platform_count?: number
}

export interface ProjectFinancials {
  currency: string
  available_balance_pln: number
  current_cash_pln?: number
  starting_balance_pln?: number
  earned_total_pln?: number
  spent_total_pln?: number
  committed_expenses_pln?: number
  net_profit_pln?: number
  next_private_render_estimated_cost_pln?: number
  budget_allowed: boolean
  budget_blocked?: boolean
  budget_warning?: boolean
  budget_blockers?: string[]
  monthly_remaining_pln?: number | null
  daily_remaining_pln?: number | null
  monthly_spent_pln?: number
  daily_spent_pln?: number
  manual_checkout_only: boolean
  autonomous_card_charges: boolean
  card_connected: boolean
  can_use_card: boolean
  card_data_storage: 'forbidden' | string
  payment_execution: string
  rule?: string
}

export interface ProjectOperatorDecision {
  state: string
  label: string
  primary_action: string
  primary_endpoint?: string | null
  requires_owner_approval: boolean
  safe_to_run_now: boolean
  risk_level: 'low' | 'medium' | 'high' | string
  reason: string
  estimated_cost_pln: number
  available_balance_pln: number
  budget_allowed: boolean
  budget_blockers: string[]
  manual_checkout_only: boolean
  card_data_storage: 'forbidden' | string
  public_quality_ready: boolean
  public_upload_enabled: boolean
  forbidden_actions: string[]
}

export interface ProjectReadinessProject {
  project_id: string
  project_type: string
  name: string
  description: string
  status: 'ready' | 'limited' | 'blocked' | string
  safe_mode: boolean
  capabilities: {
    can_research: boolean
    can_generate_private: boolean
    can_upload_private: boolean
    can_publish_public: boolean
    can_trade_or_purchase: boolean
  }
  requirements: ProjectReadinessRequirement[]
  access_playbook?: ProjectAccessPlaybook
  distribution_targets?: ProjectDistributionTarget[]
  tooling_plan?: ProjectToolingPlan
  blockers: string[]
  next_action: string
  operator_decision?: ProjectOperatorDecision
  financials?: ProjectFinancials
  evidence: Record<string, unknown>
}

export interface ProjectReadinessPayload {
  generated_at: string
  source: string
  summary: {
    total: number
    ready: number
    limited: number
    blocked: number
    safe_mode: number
    youtube_private_upload_ready: number
    cross_platform_ready?: number
    youtube_quality_review_blocked?: number
    public_publish_enabled: boolean
  }
  financials?: ProjectFinancials
  money_machine: {
    status?: string | null
    autopilot_enabled: boolean
    human_approval_before_upload: boolean
    public_upload_enabled: boolean
    global_blockers: string[]
    next_actions: string[]
  }
  projects: ProjectReadinessProject[]
}

export interface ProjectCommandEvidence {
  key: string
  source: string
  status: string
  verified: boolean
  checked_at: string | null
  summary: string
}

export interface ProjectCommandBlocker {
  source: string
  key: string
  title: string
  detail: string | null
  next_action: string | null
  verified: boolean
}

export interface ProjectYouTubeChannelIdentitySnapshot {
  status: string
  identity: {
    account_binding_key: string
    youtube_channel_id: string | null
    channel_handle: string | null
    channel_url?: string | null
    display_name: string | null
    owner_label?: string | null
    language_code?: string | null
    target_market?: string | null
    niche?: string | null
    owner_confirmed: boolean
    owner_confirmed_at: string | null
    last_verified_at?: string | null
    oauth_connection_id?: number | null
    verification_evidence?: Record<string, unknown>
  } | null
  manifest: {
    status: string
    approved_at?: string | null
    missing_fields: string[]
    value?: Record<string, unknown>
  }
  publishing_policy: {
    status: string
    approved_at?: string | null
    missing_fields: string[]
    value?: Record<string, unknown>
  }
  oauth?: {
    status: string
    connection_id: number | null
    account_id?: number | null
    connected_at?: string | null
    ready: boolean
  }
  publish_eligibility: {
    status: string
    eligible_by_local_policy: boolean
    external_upload_checked: boolean
    reasons: Array<{ code: string; detail: string }>
    note: string
  }
}

export interface ProjectCommandOverview {
  project: {
    id: number
    name: string
    slug: string
    description: string | null
    summary: string | null
    status: string
    provider_posture: ProjectProviderPosture | null
    persona_profile: ProjectPersonaProfile | null
  }
  status: 'verified' | 'partially_verified' | 'blocked' | 'insufficient_evidence' | string
  checked_at: string
  runtime: RuntimeHealthPayload
  readiness: Record<string, unknown> | null
  channel_identity?: ProjectYouTubeChannelIdentitySnapshot
  blockers: ProjectCommandBlocker[]
  next_step: {
    source: string
    action: string
    reason: string
    requires_confirmation: boolean
  }
  work_items: Array<{
    id: number
    title: string
    summary: string
    next_step: string | null
    status: string
    priority: string
    review_state: string
    blocked_reason: string | null
    updated_at: string | null
  }>
  delegations: Array<{
    id: number
    task_type: string
    status: string
    worker_key: string | null
    verification_status: string
    result_summary: string | null
    error_message: string | null
    blocked_reason: string | null
    created_at: string | null
    updated_at: string | null
  }>
  knowledge: {
    query: string
    library_matches: Array<{
      source: string
      title: string
      path: string
      topic: string
      score: number
      excerpt: string
      verified: boolean
      source_label?: string
      source_url?: string | null
      source_updated_at?: string
      indexed_at?: string
      citation?: string
      excerpt_line_start?: number
      excerpt_line_end?: number
      source_type?: string
      project_id?: string | null
      claim_type?: 'current_source_fact' | 'stored_fact' | 'inference' | 'recommendation' | 'missing_data' | string
    }>
    semantic_matches?: Array<{
      source: string
      title: string
      path: string
      topic: string
      score: number
      semantic_score: number
      lexical_score: number
      excerpt: string
      source_label?: string
      source_url?: string | null
      source_updated_at?: string
      source_type?: string
      project_id?: string | null
      claim_type?: 'current_source_fact' | 'stored_fact' | 'inference' | 'recommendation' | 'missing_data' | string
      citation?: string
      line_start?: number
      line_end?: number
      match_reasons?: string[]
      verified: boolean
    }>
    claim_legend?: Record<string, string>
    library_error: string | null
    obsidian: {
      source: string
      configured: boolean
      readable: boolean
      note_count: number
      warnings: string[]
      matches: Array<{
        title: string
        path: string
        modified_at: string
        excerpt: string
      tags: string[]
      source_type?: string
      project_id?: string | null
      claim_type?: 'current_source_fact' | 'stored_fact' | 'inference' | 'recommendation' | 'missing_data' | string
      verified?: boolean
      }>
    }
  }
  recent_evidence: ProjectCommandEvidence[]
  rollback_points?: Array<{
    id: number
    label: string
    status: string
    target_root: string
    manifest_path: string
    failure_detail: string | null
    updated_at: string | null
  }>
  brain?: {
    state: {
      phase: string
      readiness_status: string
      summary: string
      working: Array<{ key: string; label: string; status: string; detail: string; next_action?: string | null }>
      failing: Array<{ key: string; label: string; status: string; detail: string; next_action?: string | null }>
      risks: Array<{ title: string; source: string; verified: boolean; next_action?: string | null }>
      open_questions: Array<{ question: string; source: string }>
      next_step: Record<string, unknown>
      observed_at: string
      source_hash: string
      confidence_score: number
      verified: boolean
    } | null
    goals: Array<{
      id: number
      title: string
      description: string | null
      status: string
      source: string
      source_ref: string | null
      observed_at: string
      confidence_score: number
      verified: boolean
    }>
    blockers: Array<{
      id: number
      source_key: string
      title: string
      detail: string | null
      next_action: string | null
      severity: string
      status: string
      source: string
      observed_at: string
      verified: boolean
    }>
    facts: Array<{
      key: string
      content: string
      source: string
      source_ref: string | null
      observed_at: string
      expires_at: string | null
      confidence_score: number
      verification_status: string
    }>
    evidence: Array<{
      key: string
      type: string
      source: string
      source_ref: string | null
      summary: string
      collected_at: string
      verified: boolean
      content_hash: string
    }>
    recent_traces: Array<Record<string, unknown>>
  }
}

export interface ProjectCommandResult {
  status: string
  intent: string
  title: string
  answer: string
  data: Record<string, unknown>
  evidence: ProjectCommandEvidence[]
  requires_confirmation: boolean
  verified: boolean
  verification_status: 'verified' | 'partially_verified' | 'failed' | 'insufficient_evidence' | string
  trace_id: number
  trace_status: string
  conversation_id: number
  conversation_title: string
  user_message_id: number
  assistant_message_id: number
}

export interface SmartSpendingEntry {
  id: string
  created_at: string
  spending_account_id: string
  entry_type: 'seed_capital' | 'revenue' | 'expense_reserved' | 'expense_actual' | 'expense_released' | string
  amount_pln: number
  provider: string
  operation: string
  reason: string
  account_id?: string | null
  job_id?: string | null
  source_id?: string | null
  metadata: Record<string, unknown>
  card_data_storage: string
}

export interface SmartSpendingStatus {
  spending_account_id: string
  currency: string
  starting_balance_pln: number
  revenue_pln: number
  earned_total_pln: number
  spent_total_pln: number
  reserved_expenses_pln: number
  released_expenses_pln: number
  committed_expenses_pln: number
  available_balance_pln: number
  max_autonomous_spend_pln: number
  net_profit_pln: number
  money_awareness: {
    state: string
    instruction: string
    must_earn_before_spending: boolean
    can_spend_now_pln: number
  }
  card_access: {
    can_use_card: boolean
    can_verify_card: boolean
    card_connected: boolean
    card_data_storage: string
    payment_execution: string
    reason: string
    safe_path: string
  }
  policy: Record<string, unknown>
  card_data_storage: string
  recent_entries: SmartSpendingEntry[]
}

export interface ProcurementRequestItem {
  id: string
  created_at: string
  updated_at: string
  provider_id: string
  provider_name?: string | null
  status: string
  reason: string
  estimated_monthly_cost_pln: number
  account_id?: string | null
  metadata: Record<string, unknown>
  card_data_storage: string
}

export interface ExpenseNotificationItem {
  id: string
  created_at: string
  updated_at: string
  event_type: string
  provider: string
  operation: string
  amount_pln: number
  account_id?: string | null
  job_id?: string | null
  reason: string
  delivery_channel: string
  delivery_status: string
  subject: string
  body: string
  metadata: Record<string, unknown>
  card_data_storage: string
}

export interface InvestmentWatchlistItem {
  symbol: string
  name?: string | null
  asset_type: string
  thesis: string
  risk_notes: string
  created_at: string
  updated_at: string
}

export interface InvestmentPaperTrade {
  id: string
  created_at: string
  symbol: string
  side: 'buy' | 'sell' | string
  quantity: number
  price_pln: number
  reason: string
  status: string
}

export interface InvestmentPaperPosition {
  symbol: string
  net_quantity: number
  average_buy_price_pln: number
  gross_buy_cost_pln: number
  gross_sell_value_pln: number
  real_market_value_pln?: number | null
  mode: string
}

export interface PortfolioAlphaSettings {
  project_id: string
  broker_name: string
  broker_portal_url: string
  broker_account_identifier: string
  credential_vault_ref: string
  broker_read_access: 'not_configured' | 'read_only_candidate' | 'read_only_verified' | string
  account_mode: 'demo_paper' | 'demo_read_only' | 'live_read_only' | string
  market_data_sources: string[]
  learning_resources: string[]
  monthly_spend_budget_pln: number
  payment_method_ref: string
  payment_method_configured: boolean
  real_trading_blocked: boolean
  trade_approval_rules_enabled: boolean
  created_at?: string | null
  updated_at?: string | null
  stores_plaintext_secret: boolean
}

export interface PortfolioAlphaSettingsPayload {
  brokerName?: string
  brokerPortalUrl?: string
  brokerAccountIdentifier?: string
  credentialVaultRef?: string
  brokerReadAccess?: 'not_configured' | 'read_only_candidate' | 'read_only_verified'
  accountMode?: 'demo_paper' | 'demo_read_only' | 'live_read_only'
  marketDataSources?: string[]
  learningResources?: string[]
  monthlySpendBudgetPln?: number
  paymentMethodRef?: string
  paymentMethodConfigured?: boolean
  realTradingBlocked?: boolean
  tradeApprovalRulesEnabled?: boolean
}

export interface XtbMarketDataCheck {
  id: string
  created_at: string
  provider_key: string
  environment: string
  status: string
  symbols: string[]
  result: Record<string, unknown>
  real_orders_enabled: boolean
}

export interface XtbMarketDataStatus {
  provider_key: string
  environment: string
  api_available?: boolean
  provider_status?: string
  api_retired_at?: string
  retirement_source?: string
  registered: boolean
  account_identifier: string
  credential_account_id?: number | null
  has_credential_ref: boolean
  broker_read_access: string
  broker_status: string
  read_only_verified: boolean
  last_check?: XtbMarketDataCheck | null
  allowed_commands: string[]
  forbidden_trading_commands: string[]
  real_trading_enabled: boolean
  next_action?: string | null
}

export interface XtbReadOnlyVerifyResult {
  status: string
  provider_key: string
  environment: string
  real_trading_enabled: boolean
  broker_access: string
  market_data_check: XtbMarketDataCheck
  evidence: Record<string, unknown>
}

export interface AccountSecretStorageStatus {
  storageBackend: string
  isSecretStorageEnabled: boolean
  summary: string
  warnings: string[]
}

export interface XtbQuotesResult {
  provider_key: string
  environment: string
  symbols: string[]
  quotes: Array<Record<string, unknown>>
  real_trading_enabled: boolean
}

export interface AlpacaPaperStatus {
  provider_key: 'alpaca-paper'
  environment: 'paper'
  api_available: boolean
  provider_status: string
  registered: boolean
  credential_account_id?: number | null
  has_credential_ref: boolean
  broker_status: string
  read_only_verified: boolean
  last_check?: XtbMarketDataCheck | null
  allowed_operations: string[]
  forbidden_operations: string[]
  real_trading_enabled: false
  next_action?: string | null
}

export interface AlpacaReadOnlyVerifyResult {
  status: string
  provider_key: 'alpaca-paper'
  environment: 'paper'
  real_trading_enabled: false
  broker_access: string
  market_data_check: XtbMarketDataCheck
  evidence: Record<string, unknown>
}

export interface InvestmentRiskPolicy {
  mode: 'baseline_safe_policy' | 'configured_safe_policy' | string
  configured: boolean
  real_trading_enabled: false
  paper_trading_enabled: boolean
  paper_only: true
  emergency_stop: boolean
  max_single_paper_trade_pln: number
  max_paper_position_pln: number
  max_daily_realized_loss_pln: number
  max_weekly_realized_loss_pln: number
  forbidden_instruments_for_real_money: string[]
  paper_only_instruments_allowed: string[]
  owner_approval_required_for_real_orders: boolean
  configured_at?: string | null
  updated_at?: string | null
}

export interface InvestmentRiskMetrics {
  basis: 'realized_pnl_only' | string
  daily_realized_pnl_pln: number
  weekly_realized_pnl_pln: number
  daily_realized_loss_pln: number
  weekly_realized_loss_pln: number
  unrealized_pnl_available: false
  calculated_at: string
}

export interface InvestmentRiskPolicyPayload {
  maxSinglePaperTradePln: number
  maxPaperPositionPln: number
  maxDailyRealizedLossPln: number
  maxWeeklyRealizedLossPln: number
  emergencyStop: boolean
}

export interface InvestmentRiskPolicyResponse {
  policy: InvestmentRiskPolicy
  metrics: InvestmentRiskMetrics
}

export interface InvestmentCockpitStatus {
  project_id: string
  mode: string
  status: string
  generated_at: string
  real_trading_enabled: boolean
  broker_connected: boolean
  broker_access: string
  portfolio_settings?: PortfolioAlphaSettings
  market_data_status: string
  market_data_sources?: string[]
  learning_resources?: string[]
  education_sources?: InvestmentEducationSource[]
  verified_education_source_count?: number
  education_reports?: InvestmentEducationReport[]
  watchlist: InvestmentWatchlistItem[]
  watchlist_count: number
  paper_positions: InvestmentPaperPosition[]
  paper_position_count: number
  paper_trades: InvestmentPaperTrade[]
  paper_trade_count: number
  strategy_runs?: InvestmentStrategyRun[]
  strategy_run_count?: number
  autopilot_schedule?: InvestmentAutopilotSchedule
  risk_policy: InvestmentRiskPolicy
  risk_metrics: InvestmentRiskMetrics
  blockers: string[]
  capabilities: {
    can_research: boolean
    can_manage_watchlist: boolean
    can_paper_trade: boolean
    can_run_autonomous_paper_strategy: boolean
    can_trade_real_money: boolean
    can_use_card_or_bank: boolean
  }
  next_actions: string[]
  safety: Record<string, unknown>
}

export interface InvestmentEducationSource {
  source_id: string
  title: string
  publisher: string
  category: string
  url: string
  access_scope: 'public_reference' | 'public_reference_only' | 'external_copilot_only' | string
  limitation: string
  verification_status: 'available' | 'unavailable' | 'external_copilot_only' | string
  http_status?: number | null
  checked_at?: string | null
  last_error?: string | null
}

export interface InvestmentEducationSourcesPayload {
  sources: InvestmentEducationSource[]
  verified_count: number
  learning_state: string
  real_trading_enabled: boolean
}

export interface InvestmentEducationBootstrapResult extends InvestmentEducationSourcesPayload {
  status: string
  checked_at: string
  verified_runtime_urls: string[]
  unavailable_count: number
  external_copilot_only_count: number
  next_action: string
  portfolio_settings: PortfolioAlphaSettings
}

export interface InvestmentEducationCitation {
  label?: string | null
  locator?: string | null
  origin_uri?: string | null
  quote?: string | null
}

export interface InvestmentEducationReport {
  id: string
  created_at: string
  status: string
  source_count: number
  citation_count: number
  digest: {
    kind?: string
    entries?: Array<{
      source_id: string
      title: string
      publisher: string
      url: string
      excerpt: string
      access_scope: string
    }>
    errors?: string[]
    generated_from_verified_sources_only?: boolean
  }
  citations: InvestmentEducationCitation[]
  llm_synthesis_status: string
  error_message?: string | null
  report_markdown?: string | null
}

export interface InvestmentEducationReportsPayload {
  reports: InvestmentEducationReport[]
  real_trading_enabled: boolean
  safe_for_paper_strategy: boolean
}

export interface InvestmentEducationResearchResult extends InvestmentEducationReport {
  safe_for_paper_strategy: boolean
  real_trading_enabled: boolean
  next_action: string
}

export interface InvestmentWatchlistPayload {
  symbol: string
  name?: string | null
  assetType?: string
  thesis?: string
  riskNotes?: string
}

export interface InvestmentPaperTradePayload {
  symbol: string
  side: 'buy' | 'sell'
  quantity: number
  pricePln: number
  reason: string
}

export interface InvestmentStrategyDecision {
  symbol: string | null
  name?: string | null
  risk_bucket?: string | null
  quote_range_signal?: number | null
  synthetic_signal?: number | null
  signal_source?: string | null
  synthetic_price_pln?: number | null
  market_price?: number | null
  price_source?: 'xtb_demo_quotes' | 'synthetic_fallback' | string
  price_currency?: string | null
  quote_bid?: number | null
  quote_ask?: number | null
  quote_high?: number | null
  quote_low?: number | null
  quote_timestamp?: string | number | null
  quote_checked_at?: string | null
  action: string
  reason?: string | null
  trade_id?: string | null
  quantity?: number | null
}

export interface InvestmentStrategyRun {
  id: string
  created_at: string
  strategy_id: string
  mode: string
  status: string
  reason?: string | null
  blockers?: string[]
  thesis?: string | null
  decisions: InvestmentStrategyDecision[]
  executed_trade_count: number
  rejected_trade_count: number
  real_trading_enabled: boolean
  safety?: Record<string, unknown>
}

export interface InvestmentAutopilotSchedule {
  enabled: boolean
  strategy_id: string
  interval_minutes: number
  max_trades_per_tick: number
  alerts_enabled: boolean
  alert_on_fallback: boolean
  alert_on_unusual_decision: boolean
  last_run_at?: string | null
  next_due_at?: string | null
  due: boolean
  last_run?: InvestmentStrategyRun | null
  real_trading_enabled: boolean
}

export interface InvestmentAutopilotRunPayload {
  strategyId?: string
  maxTrades?: number
}

export interface CreatorCockpitProject {
  id: number
  project_id?: number | null
  name: string
  project_type: string
  status: string
  persona_type: string
  disclosure_policy: string
  niche?: string | null
  target_audience?: string | null
  monetization_goal?: string | null
  content_boundaries?: string | null
  safety_boundaries?: string | null
  platforms: string[]
  linked_account_ids: number[]
  operating_mode: string
  approval_policy: string
}

export interface CreatorCockpitWorkItem {
  id?: number | null
  creator_project_id: number
  title: string
  work_type: string
  status: string
  risk_level: string
  approval_required: boolean
  platform?: string | null
  draft_payload?: Record<string, unknown> | null
  review_notes?: string | null
  next_action?: string | null
  safety_flags: string[]
}

export interface CreatorCockpitAssetLedgerItem {
  source: string
  work_item_id?: number | null
  title: string
  work_type: string
  platform?: string | null
  status: string
  risk_level: string
  approval_required: boolean
  draft_payload: Record<string, unknown>
  license_status: string
  disclosure_required: boolean
}

export interface CreatorCockpitStatus {
  status: string
  project: CreatorCockpitProject | null
  work_items: CreatorCockpitWorkItem[]
  calendar: Array<Record<string, unknown>>
  asset_ledger: CreatorCockpitAssetLedgerItem[]
  counts: {
    work_items: number
    drafts: number
    needs_review: number
    approved: number
    calendar_items: number
    asset_ledger?: number
  }
  capabilities: {
    can_initialize: boolean
    can_plan: boolean
    can_generate_private_drafts: boolean
    can_generate_preview_batch: boolean
    can_publish_external: boolean
    can_run_paid_platform: boolean
    has_linked_platform_account: boolean
  }
  blockers: string[]
  next_actions: string[]
  safety: Record<string, unknown>
}

export interface CreatorCockpitSeedResult {
  status: string
  creator_project_id: number
  result: Record<string, unknown>
  cockpit: CreatorCockpitStatus
}

export interface ChannelReviewQueueItem {
  channelId: string
  itemId: string
  topic: string
  status: string
  finalMp4: string | null
  outputDir: string | null
  reviewPath: string | null
  qualityScore: number | null
  visualQualityScore: number | null
  scriptQualityScore: number | null
  renderOk: boolean | null
  youtubeShortsReady: boolean | null
  uploadApprovalId: string | null
  youtubeVideoId: string | null
  updatedAt: string
}

export interface ChannelReviewQueuePayload {
  itemCount: number
  readyCount: number
  repairCount: number
  jsonPath: string
  markdownPath: string
  items: ChannelReviewQueueItem[]
}

export interface ProjectDeleteResult {
  projectId: number
  name: string
  deleted: boolean
}

export interface ConversationDetailPayload {
  conversation: {
    id: number
    title: string
    projectId: number | null
    summary: string | null
    lastMessageAt: string | null
    updatedAt: string
    activeWorkMode: string
  }
  messages: ConversationMessage[]
  settings: {
    activeWorkMode: 'auto' | 'quick' | 'deep' | 'execute'
    responseStyle: string | null
    memoryEnabled: boolean
    fileEnabled: boolean
    internetEnabled: boolean
    personaProfile: string
    providerPosture: string
    qualityProfile: 'fast-turnaround' | 'standard' | 'premium'
    sources: Record<string, string>
  }
  memoryCards: {
    conversation: {
      conversationId: number
      title: string
      summary: string | null
      hasSummary: boolean
      projectId: number | null
    }
    project: {
      projectId: number
      name: string
      summary: string | null
      hasSummary: boolean
    } | null
    obsidian: {
      vaultName: string
      linkedLabel: string | null
      projectLinked: boolean
      pathConfigured: boolean
      exportEnabled: boolean
      readyForPreview: boolean
      readyForConfirm: boolean
      relativePathHint: string
      helperText: string
    } | null
  }
}

export interface AgentCapabilityPreview {
  action: string
  title: string
  summary: string
  payload: Record<string, unknown>
  requiresConfirmation: boolean
}

export interface ActionDraftAccountSummary {
  id: number
  label: string
  providerKey: string
  accountIdentifier: string
  status: string
  riskLevel: string
  maskedSecretState: string
  allowedCapabilities: string[]
}

export interface ActionDraft {
  id: number
  accountId: number
  integrationKey: string
  projectId: number | null
  projectName: string | null
  actionType: string
  actionTitle: string
  actionSummary: string
  riskLevel: string
  status: string
  requiresApproval: boolean
  previewPayload: Record<string, unknown>
  approvalNotes: string | null
  rejectionReason: string | null
  auditLog: Array<Record<string, unknown>>
  createdBy: string
  approvedAt: string | null
  rejectedAt: string | null
  executedAt: string | null
  requiredCapabilities: string[]
  accountSummary: ActionDraftAccountSummary
  createdAt: string
  updatedAt: string
}

export interface ActionDraftExecutionResponse {
  actionDraft: ActionDraft
  executionResult: Record<string, unknown>
}

export interface CodeGenerationPreviewResponse {
  mode: string
  actionDraftId: number
  title: string
  summary: string
  metadata: Record<string, unknown>
}

export interface ProjectTestRunResponse {
  runner: string
  command: string[]
  cwd: string
  exitCode: number
  summary: string
  firstFailures: string[]
  stdout: string
  stderr: string
}

export interface AgentBrowserRecipeRunResponse {
  preview: AgentActionPreview | null
  run: TrustedAutoExecutionRun | null
}

export interface ChatAttachmentPayload {
  filename: string
  storage_path: string
  kind: string
  detected_kind?: string | null
  understanding_status?: string | null
  understanding_reason?: string | null
  preview_text?: string | null
  truncated?: boolean
  media_type?: string | null
  file_extension?: string | null
  size_bytes?: number | null
  extracted_text?: string | null
}

interface ChatRequestPayload {
  content: string
  conversation_id?: number
  project_id?: number
  prompt_improved?: boolean
  force_external?: boolean
  execution_preferences?: {
    response_style?: string | null
    persona_profile?: string | null
    provider_posture?: string | null
    quality_profile?: 'fast-turnaround' | 'standard' | 'premium' | null
    memory_enabled?: boolean | null
    file_enabled?: boolean | null
    internet_enabled?: boolean | null
    active_work_mode?: 'auto' | 'quick' | 'deep' | 'execute' | null
  }
  attachments?: ChatAttachmentPayload[]
  message_metadata_json?: Record<string, unknown>
  assistant_metadata_json?: Record<string, unknown>
}

export interface ConversationSettingsUpdatePayload {
  activeWorkMode?: 'auto' | 'quick' | 'deep' | 'execute'
  responseStyleOverride?: 'concise-direct' | 'professional-clear' | 'supportive-guide' | 'critical-deep' | null
  memoryEnabledOverride?: boolean | null
  fileEnabledOverride?: boolean | null
  internetEnabledOverride?: boolean | null
}

export interface PromptImproveResult {
  originalContent: string
  improvedContent: string
  strategyNote: string
  changed: boolean
}

export function buildAgentArtifactDownloadUrl(storagePath: string) {
  return `${API_BASE_URL}/agent/artifacts/download?storage_path=${encodeURIComponent(storagePath)}`
}

export async function fetchPendingAgentActionPreview(): Promise<AgentActionPreview | null> {
  const payload = await fetchJson<Record<string, unknown>>('/agent/action-previews/pending')
  if (!payload.preview || typeof payload.preview !== 'object') {
    return null
  }
  return normalizeAgentActionPreview(payload.preview as Record<string, unknown>)
}

export async function fetchAgentStatus(): Promise<AgentStatusPayload> {
  return normalizeAgentStatusPayload(await fetchJson<Record<string, unknown>>('/agent/status'))
}

export async function fetchAgentRecentLogs(limit = 20): Promise<AgentRecentLogsPayload> {
  const payload = await fetchJson<Record<string, unknown>>(`/agent/logs/recent?limit=${encodeURIComponent(String(limit))}`)
  return {
    decisions: Array.isArray(payload.decisions)
      ? payload.decisions.map((item) => normalizeAgentLogEntry(item as Record<string, unknown>))
      : [],
    toolCalls: Array.isArray(payload.tool_calls)
      ? payload.tool_calls.map((item) => normalizeAgentLogEntry(item as Record<string, unknown>))
      : [],
  }
}

export async function fetchAgentTools(): Promise<AgentToolDescriptor[]> {
  const payload = await fetchJson<Record<string, unknown>>('/agent/tools')
  return Array.isArray(payload.tools)
    ? payload.tools.map((item) => normalizeAgentTool(item as Record<string, unknown>))
    : []
}

export async function fetchAgentPendingApprovals(): Promise<AgentActionPreview[]> {
  const payload = await fetchJson<Record<string, unknown>>('/agent/pending-approvals')
  return Array.isArray(payload.pending_approvals)
    ? payload.pending_approvals.map((item) => normalizeAgentActionPreview(item as Record<string, unknown>))
    : []
}

export async function emergencyStopAgent(): Promise<AgentEmergencyStopResult> {
  const payload = await fetchJson<Record<string, unknown>>('/agent/emergency-stop', {
    method: 'POST',
  })
  return {
    ok: Boolean(payload.ok),
    event: String(payload.event ?? 'agent_emergency_stop'),
    interruptedRuns: Array.isArray(payload.interrupted_runs)
      ? payload.interrupted_runs.map((item) => item as Record<string, unknown>)
      : [],
    ubuntuStop:
      payload.ubuntu_stop && typeof payload.ubuntu_stop === 'object'
        ? {
            ok: Boolean((payload.ubuntu_stop as Record<string, unknown>).ok),
            result:
              ((payload.ubuntu_stop as Record<string, unknown>).result as Record<string, unknown> | undefined) ?? {},
          }
        : { ok: false, result: {} },
    message: String(payload.message ?? ''),
  }
}

export async function approveAgentAction(runId: number): Promise<TrustedAutoExecutionRun | AgentActionPreview> {
  const payload = await fetchJson<Record<string, unknown>>('/agent/approve-action', {
    method: 'POST',
    body: JSON.stringify({ run_id: runId }),
  })
  if (payload.run && typeof payload.run === 'object') {
    return normalizeTrustedAutoExecutionRun(payload.run as Record<string, unknown>)
  }
  return normalizeAgentActionPreview(payload.preview as Record<string, unknown>)
}

export async function rejectAgentAction(runId: number, reason?: string | null): Promise<AgentActionPreview> {
  const payload = await fetchJson<Record<string, unknown>>('/agent/reject-action', {
    method: 'POST',
    body: JSON.stringify({ run_id: runId, reason: reason ?? null }),
  })
  return normalizeAgentActionPreview(payload.preview as Record<string, unknown>)
}

export async function approveAgentActionPreview(runId: number): Promise<TrustedAutoExecutionRun> {
  const payload = await fetchJson<Record<string, unknown>>(`/agent/action-previews/${runId}/approve`, {
    method: 'POST',
    headers: localCapabilityHeaders(),
  })
  return normalizeTrustedAutoExecutionRun(payload.run as Record<string, unknown>)
}

export async function improveChatPrompt(content: string): Promise<PromptImproveResult> {
  const payload = await fetchJson<Record<string, unknown>>('/chat/prompt-improve', {
    method: 'POST',
    body: JSON.stringify({ content }),
  })

  return {
    originalContent: String(payload.original_content ?? content),
    improvedContent: String(payload.improved_content ?? content),
    strategyNote: String(payload.strategy_note ?? ''),
    changed: Boolean(payload.changed),
  }
}

export async function rejectAgentActionPreview(
  runId: number,
  reason?: string | null,
): Promise<AgentActionPreview> {
  const payload = await fetchJson<Record<string, unknown>>(`/agent/action-previews/${runId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason: reason ?? null }),
  })
  return normalizeAgentActionPreview(payload.preview as Record<string, unknown>)
}

export async function fetchLatestAgentExecution(): Promise<TrustedAutoExecutionRun | null> {
  const payload = await fetchJson<Record<string, unknown>>('/agent/executions/latest')
  if (!payload.run || typeof payload.run !== 'object') {
    return null
  }
  return normalizeTrustedAutoExecutionRun(payload.run as Record<string, unknown>)
}

export async function fetchAgentExecution(runId: number): Promise<TrustedAutoExecutionRun> {
  const payload = await fetchJson<Record<string, unknown>>(`/agent/executions/${runId}`)
  return normalizeTrustedAutoExecutionRun(payload.run as Record<string, unknown>)
}

export async function pauseAgentExecution(runId: number): Promise<TrustedAutoExecutionRun> {
  const payload = await fetchJson<Record<string, unknown>>(`/agent/executions/${runId}/pause`, {
    method: 'POST',
  })
  return normalizeTrustedAutoExecutionRun(payload.run as Record<string, unknown>)
}

export async function resumeAgentExecution(runId: number): Promise<TrustedAutoExecutionRun> {
  const payload = await fetchJson<Record<string, unknown>>(`/agent/executions/${runId}/resume`, {
    method: 'POST',
  })
  return normalizeTrustedAutoExecutionRun(payload.run as Record<string, unknown>)
}

export async function interruptAgentExecution(runId: number): Promise<TrustedAutoExecutionRun> {
  const payload = await fetchJson<Record<string, unknown>>(`/agent/executions/${runId}/interrupt`, {
    method: 'POST',
  })
  return normalizeTrustedAutoExecutionRun(payload.run as Record<string, unknown>)
}

export interface LocalChatModelSwitchResult {
  modelId: number
  modelKey: string
  runtimeModelName: string
}

export async function switchLocalChatModel(
  runtimeModelName: string,
): Promise<LocalChatModelSwitchResult> {
  const payload = await fetchJson<Record<string, unknown>>('/settings/runtime/local-chat-model', {
    method: 'POST',
    body: JSON.stringify({
      runtime_model_name: runtimeModelName,
    }),
  })
  return {
    modelId: Number(payload.model_id),
    modelKey: String(payload.model_key),
    runtimeModelName: String(payload.runtime_model_name),
  }
}

function localizeProviderStatusReason(raw: string): string {
  const map: Record<string, string> = {
    'No secret or metadata has been configured yet.':
      'Nie skonfigurowano jeszcze sekretu ani metadanych.',
    'Provider card is disabled.': 'Karta providera jest wyłączona.',
    'Secret is stored and ready for explicit test.':
      'Sekret jest zapisany i gotowy do jawnego testu.',
    'Only metadata is configured. Add a secret to enable live tests.':
      'Skonfigurowano tylko metadane. Dodaj sekret, aby uruchomić test live.',
    'Ollama provider is not registered in this runtime.':
      'Provider Ollama nie jest zarejestrowany w tym runtime.',
    'Ollama provider is disabled.': 'Provider Ollama jest wyłączony.',
    'Ollama local runtime responded to /api/tags.':
      'Lokalny runtime Ollamy odpowiedział na /api/tags.',
  }

  return map[raw] ?? raw
}

function localizePipelineReason(raw: string): string {
  const map: Record<string, string> = {
    'No creator project is available yet.': 'Nie ma jeszcze dostępnego projektu creatorowego.',
    'Creator project could not be loaded.': 'Nie udało się wczytać projektu creatorowego.',
    'Preview package is awaiting explicit human approval.':
      'Pakiet preview czeka na jawną akceptację człowieka.',
    'Pipeline is mapped from creator planning and preview packages.':
      'Pipeline jest mapowany z creator planningu i pakietów preview.',
  }

  return map[raw] ?? raw
}

export interface ChatTurnResponse {
  conversationId: number
  conversationTitle: string
  conversationProjectId: number | null
  userMessageId: number
  assistantMessageId: number
  userMessage: string
  userMessageAttachmentOnly: boolean
  assistantMessage: string
  selectedProviderKey: string
  selectedModelKey: string
  routingReason: string
  assistantMessageMetadataJson: Record<string, unknown>
  skillExecution: ChatSkillExecution | null
  runtimeError?: RuntimeErrorDetail | null
}

export interface ChatSkillExecution {
  selectedSkills: string[]
  reasoningSummary: string
  instructionStatus: string
  loadedInstructionSkills: string[]
  missingInstructionSkills: string[]
}

export interface RuntimeErrorDetail {
  failureType:
    | 'runtime_unreachable'
    | 'model_missing'
    | 'request_timeout'
    | 'runtime_busy'
    | 'invalid_endpoint'
    | 'capability_mismatch'
    | 'context_too_large'
    | 'generation_error'
    | 'unknown'
  providerKey: string
  modelKey: string
  runtimeModelName: string | null
  userTitle: string
  userDetail: string
  retryable: boolean
  canFallback: boolean
  suggestedActions: string[]
  diagnostics?: Record<string, unknown> | null
}

export interface ChatBriefingResponse {
  conversationId: number
  conversationTitle: string
  assistantMessageId: number
  content: string
  metadataJson: Record<string, unknown>
}

export interface AnswerQuestionRequest {
  conversationId: number
  assistantMessageId: number
  questionId: string
  selectedIds: string[]
}

export interface AnswerQuestionResponse {
  ok: boolean
  assistantMessageId: number
  userMessageId: number
  nextAssistantMessageId: number | null
}

export interface DecisionLogEntryView {
  decisionId: number
  conversationId: number | null
  projectId: number | null
  kind: 'decision' | 'commitment' | 'open_question' | 'pending_action'
  summary: string
  madeAt: string
  status: 'active' | 'resolved' | 'expired'
  dueAt: string | null
  resolvedAt: string | null
  resolutionSummary: string | null
  evidenceMessageIds: number[]
}

export interface ChatDecisionListResponse {
  entries: DecisionLogEntryView[]
  overdueCount: number
}

export interface TrustedAutoAccountMutationPayload {
  accountId?: number | null
  label: string
  providerKey: string
  accountIdentifier: string
  secretType: string
  secretValue?: string | null
  linkedProjectId?: number | null
  linkedIntegrationKey?: string | null
  notes?: string | null
  allowedCapabilities?: string[]
  loginIdentifier?: string | null
  passwordValue?: string | null
  totpValue?: string | null
  backupCodesValue?: string | null
  recoveryEmail?: string | null
  recoveryEmailSecretValue?: string | null
  connectorMappingJson?: Record<string, unknown> | null
}

export interface AccountCredentialPayload {
  label: string
  providerKey: string
  accountIdentifier: string
  secretType: string
  secretValue?: string | null
  riskLevel?: 'low' | 'medium' | 'high' | 'critical'
  allowedCapabilities?: string[]
  linkedIntegrationKey?: string | null
  linkedProjectId?: number | null
  notes?: string | null
}

export interface AccountCredentialResult {
  id: number
  label: string
  providerKey: string
  accountIdentifier: string
  secretType: string
  status: string
  hasSecret: boolean
  riskLevel: string
  allowedCapabilities: string[]
  linkedIntegrationKey: string | null
  linkedProjectId: number | null
  notes: string | null
  maskedSecretState: string
}

export interface TrustedAutoTrustGrantMutationPayload {
  trustGrantId?: number | null
  projectId: number
  accountId: number
  connectorKey: string
  mode: 'blocked' | 'supervised' | 'trusted_auto'
  domainAllowlist?: string[]
  actionFamilies?: string[]
  fileWriteScope?: string[]
  loginAllowed?: boolean
  twoFactorAllowed?: boolean
  publishAllowed?: boolean
  browserAutomationAllowed?: boolean
  connectorWriteAllowed?: boolean
  localRenderAllowed?: boolean
  maxRetries?: number
  cooldownMinutes?: number
  isEnabled?: boolean
  notes?: string | null
}

export interface TrustedAutoSchedulerSettingsPayload {
  autonomyMode?: 'blocked' | 'supervised' | 'trusted_auto'
  schedulerEnabled?: boolean
  emergencyStopEnabled?: boolean
  pauseAllJobs?: boolean
  loginAutomationEnabled?: boolean
  twoFactorAutomationEnabled?: boolean
  publishAutomationEnabled?: boolean
  defaultRetryBudget?: number
  defaultVramFloorGb?: number
}

export interface TrustedAutoSchedulerJobMutationPayload {
  jobId?: number | null
  projectId: number
  jobType: string
  triggerMode?: string
  connectorKey?: string | null
  platformKey?: string | null
  scheduledFor?: string | null
  cronExpression?: string | null
  payloadJson?: Record<string, unknown>
  retryBudget?: number | null
  requiresTrustedAuto?: boolean
  cooldownKey?: string | null
}

export interface TrustedAutoMediaPipelinePresetMutationPayload {
  presetId?: number | null
  projectId?: number | null
  name: string
  targetPlatform: string
  ttsEngine: string
  imageProvider: string
  videoRenderer: string
  subtitleEngine: string
  promptStyle?: string | null
  targetDurationSec?: number
  vramFloorGb?: number
  publishMode?: string
  cooldownHours?: number
  stepsJson?: Record<string, unknown>
  isDefault?: boolean
}

export interface TrustedAutoRunJobPayload {
  projectId: number
  jobType: string
  goal: string
  connectorKey?: string | null
  platformKey?: string | null
  accountId?: number | null
  trustGrantId?: number | null
  payloadJson?: Record<string, unknown>
  requiresTrustedAuto?: boolean
}

export interface TrustedAutoRunJobResult {
  job: TrustedAutoSchedulerJob
  run: TrustedAutoExecutionRun | null
}

export function normalizeTrustedAutoHardwareSnapshot(
  item: Record<string, unknown> | null | undefined,
): TrustedAutoHardwareSnapshot | null {
  if (!item) {
    return null
  }
  return {
    freeVramMb: item.free_vram_mb == null ? null : Number(item.free_vram_mb),
    usedVramMb: item.used_vram_mb == null ? null : Number(item.used_vram_mb),
    totalVramMb: item.total_vram_mb == null ? null : Number(item.total_vram_mb),
    freeRamMb: item.free_ram_mb == null ? null : Number(item.free_ram_mb),
    totalRamMb: item.total_ram_mb == null ? null : Number(item.total_ram_mb),
    usedRamMb: item.used_ram_mb == null ? null : Number(item.used_ram_mb),
    cpuPercent: item.cpu_percent == null ? null : Number(item.cpu_percent),
    gpuName: item.gpu_name == null ? null : String(item.gpu_name),
    freeDiskGb: item.free_disk_gb == null ? null : Number(item.free_disk_gb),
    diskPath: item.disk_path == null ? null : String(item.disk_path),
    status: String(item.status ?? 'unknown'),
    notes: item.notes == null ? null : String(item.notes),
  }
}

export function normalizeAmbientState(
  item: Record<string, unknown> | null | undefined,
): AmbientState | null {
  if (!item) {
    return null
  }
  const snapshot = item.context_snapshot as Record<string, unknown> | null | undefined
  const hardware = snapshot?.hardware as Record<string, unknown> | null | undefined
  return {
    ambientListeningEnabled: Boolean(item.ambient_listening_enabled),
    ambientWakeWord: String(item.ambient_wake_word ?? 'Atnos'),
    ambientMuted: Boolean(item.ambient_muted),
    ambientContextEnabled: Boolean(item.ambient_context_enabled),
    voiceRepliesEnabled: Boolean(item.voice_replies_enabled),
    voiceReplyVoiceId:
      item.voice_reply_voice_id == null ? null : Number(item.voice_reply_voice_id),
    listeningAvailable: Boolean(item.listening_available),
    listeningReason: String(item.listening_reason ?? ''),
    contextSnapshot: snapshot
      ? {
          foregroundWindowTitle:
            snapshot.foreground_window_title == null
              ? null
              : String(snapshot.foreground_window_title),
          activeAppName:
            snapshot.active_app_name == null ? null : String(snapshot.active_app_name),
          capturedAt: String(snapshot.captured_at ?? ''),
          timeOfDayLabel: String(snapshot.time_of_day_label ?? ''),
          dayOfWeekLabel: String(snapshot.day_of_week_label ?? ''),
          cpuPercent: snapshot.cpu_percent == null ? null : Number(snapshot.cpu_percent),
          ramUsedMb: snapshot.ram_used_mb == null ? null : Number(snapshot.ram_used_mb),
          ramTotalMb: snapshot.ram_total_mb == null ? null : Number(snapshot.ram_total_mb),
          networkOnline:
            snapshot.network_online == null ? null : Boolean(snapshot.network_online),
          projectId: snapshot.project_id == null ? null : Number(snapshot.project_id),
          projectName: snapshot.project_name == null ? null : String(snapshot.project_name),
          lastUserInteractionAt:
            snapshot.last_user_interaction_at == null
              ? null
              : String(snapshot.last_user_interaction_at),
          hardware: hardware
            ? {
                totalVramMb: hardware.total_vram_mb == null ? null : Number(hardware.total_vram_mb),
                freeVramMb: hardware.free_vram_mb == null ? null : Number(hardware.free_vram_mb),
                usedVramMb: hardware.used_vram_mb == null ? null : Number(hardware.used_vram_mb),
                freeRamMb: hardware.free_ram_mb == null ? null : Number(hardware.free_ram_mb),
                totalRamMb: hardware.total_ram_mb == null ? null : Number(hardware.total_ram_mb),
                freeDiskGb: hardware.free_disk_gb == null ? null : Number(hardware.free_disk_gb),
                diskPath: hardware.disk_path == null ? null : String(hardware.disk_path),
                status: String(hardware.status ?? 'unknown'),
                notes: hardware.notes == null ? null : String(hardware.notes),
              }
            : null,
        }
      : null,
  }
}

export function normalizeRemoteExecutionStep(
  item: Record<string, unknown>,
): RemoteExecutionStep {
  return {
    stepId: Number(item.step_id),
    stepIndex: Number(item.step_index ?? 0),
    executor: String(item.executor ?? 'worker_core') as RemoteExecutionStep['executor'],
    actionKind: String(item.action_kind ?? 'shell'),
    command: item.command == null ? null : String(item.command),
    browserTarget: item.browser_target == null ? null : String(item.browser_target),
    sudo: Boolean(item.sudo),
    status: String(item.status ?? 'queued') as RemoteExecutionStep['status'],
    stdout: item.stdout == null ? null : String(item.stdout),
    stderr: item.stderr == null ? null : String(item.stderr),
    exitCode: item.exit_code == null ? null : Number(item.exit_code),
    resultJson: (item.result_json as Record<string, unknown> | null) ?? {},
    startedAt: item.started_at == null ? null : String(item.started_at),
    finishedAt: item.finished_at == null ? null : String(item.finished_at),
  }
}

export function normalizeLocalHostControlSummary(
  item: Record<string, unknown> | null | undefined,
): LocalHostControlSummary {
  return {
    hostLabel: String(item?.host_label ?? 'windows-control-plane'),
    platform: String(item?.platform ?? 'nt'),
    currentUser: String(item?.current_user ?? ''),
    currentWorkdir: String(item?.current_workdir ?? ''),
    powershellAvailable: Boolean(item?.powershell_available),
    browserOpenAvailable: Boolean(item?.browser_open_available),
    playwrightAdapterAvailable: Boolean(item?.playwright_adapter_available),
    desktopInputAvailable: Boolean(item?.desktop_input_available),
    browserRecipeAvailable: Boolean(item?.browser_recipe_available),
    elevated: Boolean(item?.elevated),
    executionEnabled: Boolean(item?.execution_enabled),
    trustedAutoEnabled: Boolean(item?.trusted_auto_enabled),
    commandLogPath: String(item?.command_log_path ?? ''),
    lastCheckedAt: String(item?.last_checked_at ?? ''),
    desktopApps: Array.isArray(item?.desktop_apps)
      ? item!.desktop_apps.map((entry) => ({
          appKey: String((entry as Record<string, unknown>).app_key ?? ''),
          label: String((entry as Record<string, unknown>).label ?? ''),
          category: String((entry as Record<string, unknown>).category ?? 'unknown'),
          installed: Boolean((entry as Record<string, unknown>).installed),
          running: Boolean((entry as Record<string, unknown>).running),
          windowVisible: Boolean((entry as Record<string, unknown>).window_visible),
          launchSupported: Boolean((entry as Record<string, unknown>).launch_supported),
          focusSupported: Boolean((entry as Record<string, unknown>).focus_supported),
          preferredEntryPath:
            (entry as Record<string, unknown>).preferred_entry_path == null
              ? null
              : String((entry as Record<string, unknown>).preferred_entry_path),
          preferredEntryKind:
            (entry as Record<string, unknown>).preferred_entry_kind == null
              ? null
              : String((entry as Record<string, unknown>).preferred_entry_kind),
          runningProcesses: Array.isArray((entry as Record<string, unknown>).running_processes)
            ? ((entry as Record<string, unknown>).running_processes as unknown[]).map((item) =>
                String(item),
              )
            : [],
          visibleWindowTitles: Array.isArray((entry as Record<string, unknown>).visible_window_titles)
            ? ((entry as Record<string, unknown>).visible_window_titles as unknown[]).map((item) =>
                String(item),
              )
            : [],
          notes: Array.isArray((entry as Record<string, unknown>).notes)
            ? ((entry as Record<string, unknown>).notes as unknown[]).map((item) => String(item))
            : [],
        }))
      : [],
    chromeDiagnostics:
      item?.chrome_diagnostics && typeof item.chrome_diagnostics === 'object'
        ? normalizeLocalHostChromeDiagnostics(item.chrome_diagnostics as Record<string, unknown>)
        : null,
  }
}

export function normalizeLocalHostChromeDiagnostics(
  item: Record<string, unknown> | null | undefined,
): LocalHostChromeDiagnostics {
  return {
    browserRunning: Boolean(item?.browser_running),
    processCount: Number(item?.process_count ?? 0),
    mode: String(item?.mode ?? 'unavailable') as LocalHostChromeDiagnostics['mode'],
    summary: String(item?.summary ?? ''),
    reasons: Array.isArray(item?.reasons) ? item.reasons.map((entry) => String(entry)) : [],
    remoteDebuggingPort:
      item?.remote_debugging_port == null ? null : Number(item.remote_debugging_port),
    visibleWindows: Array.isArray(item?.visible_windows)
      ? item.visible_windows.map((entry) => ((entry as Record<string, unknown>) ?? {}))
      : [],
    tabs: Array.isArray(item?.tabs)
      ? item.tabs.map((entry) => ({
          title:
            (entry as Record<string, unknown>).title == null
              ? null
              : String((entry as Record<string, unknown>).title),
          url: String((entry as Record<string, unknown>).url ?? ''),
          source: String((entry as Record<string, unknown>).source ?? 'session') as 'devtools' | 'session',
        }))
      : [],
    detectedApps: Array.isArray(item?.detected_apps)
      ? item.detected_apps.map((entry) => String(entry))
      : [],
    sessionSourceFile:
      item?.session_source_file == null ? null : String(item.session_source_file),
    checkedAt: String(item?.checked_at ?? ''),
  }
}

export function normalizeLocalHostCommandResult(
  item: Record<string, unknown>,
): LocalHostCommandResult {
  return {
    actionKind: String(item.action_kind ?? 'powershell'),
    command: String(item.command ?? ''),
    exitCode: Number(item.exit_code ?? 1),
    stdout: String(item.stdout ?? ''),
    stderr: String(item.stderr ?? ''),
    workdir: String(item.workdir ?? ''),
    elevated: Boolean(item.elevated),
    startedAt: String(item.started_at ?? ''),
    finishedAt: String(item.finished_at ?? ''),
  }
}

export function normalizeRemoteExecutionRun(
  item: Record<string, unknown>,
): RemoteExecutionRun {
  return {
    runId: Number(item.run_id),
    origin: String(item.origin ?? 'manual') as RemoteExecutionRun['origin'],
    requestedBy: item.requested_by == null ? null : String(item.requested_by),
    taskKind: String(item.task_kind ?? ''),
    routingTarget: String(item.routing_target ?? 'local') as RemoteExecutionRun['routingTarget'],
    assignedNodeId: item.assigned_node_id == null ? null : Number(item.assigned_node_id),
    assignedNodeLabel:
      item.assigned_node_label == null ? null : String(item.assigned_node_label),
    status: String(item.status ?? 'queued') as RemoteExecutionRun['status'],
    label: item.label == null ? null : String(item.label),
    requestPayloadJson:
      (item.request_payload_json as Record<string, unknown> | null) ?? {},
    resultJson: (item.result_json as Record<string, unknown> | null) ?? {},
    errorJson: (item.error_json as Record<string, unknown> | null) ?? {},
    fallbackReason: item.fallback_reason == null ? null : String(item.fallback_reason),
    startedAt: item.started_at == null ? null : String(item.started_at),
    finishedAt: item.finished_at == null ? null : String(item.finished_at),
    createdAt: String(item.created_at ?? ''),
    updatedAt: String(item.updated_at ?? ''),
    steps: Array.isArray(item.steps)
      ? item.steps.map((step) => normalizeRemoteExecutionStep(step as Record<string, unknown>))
      : [],
  }
}

export function normalizeRemoteWorkerCapabilitySnapshot(
  item: Record<string, unknown> | null | undefined,
): RemoteWorkerCapabilitySnapshot | null {
  if (!item) {
    return null
  }
  return {
    snapshotId: Number(item.snapshot_id),
    cpuPercent: item.cpu_percent == null ? null : Number(item.cpu_percent),
    loadAvg: Array.isArray(item.load_avg) ? item.load_avg.map((value) => Number(value)) : [],
    ramTotalMb: item.ram_total_mb == null ? null : Number(item.ram_total_mb),
    ramFreeMb: item.ram_free_mb == null ? null : Number(item.ram_free_mb),
    diskFreeGb: item.disk_free_gb == null ? null : Number(item.disk_free_gb),
    networkOnline: item.network_online == null ? null : Boolean(item.network_online),
    sudoAvailable: Boolean(item.sudo_available),
    ollamaReady: Boolean(item.ollama_ready),
    browserReady: Boolean(item.browser_ready),
    desktopBridgeReady: Boolean(item.desktop_bridge_ready),
    ffmpegReady: Boolean(item.ffmpeg_ready),
    dockerReady: Boolean(item.docker_ready),
    displayMode: String(item.display_mode ?? 'unknown') as RemoteWorkerCapabilitySnapshot['displayMode'],
    browserMode: String(item.browser_mode ?? 'personal_profile'),
    pythonVersion: item.python_version == null ? null : String(item.python_version),
    ffmpegVersion: item.ffmpeg_version == null ? null : String(item.ffmpeg_version),
    capturedAt: String(item.captured_at ?? ''),
    rawPayloadJson: (item.raw_payload_json as Record<string, unknown> | null) ?? {},
  }
}

export function normalizeRemoteWorkerSummary(
  item: Record<string, unknown>,
): RemoteWorkerSummary {
  return {
    nodeId: Number(item.node_id),
    label: String(item.label ?? ''),
    nodeType: String(item.node_type ?? ''),
    platform: String(item.platform ?? ''),
    hostname: item.hostname == null ? null : String(item.hostname),
    host: String(item.host ?? ''),
    sshPort: Number(item.ssh_port ?? 22),
    sshUser: String(item.ssh_user ?? 'atnos'),
    status: String(item.status ?? 'disconnected') as RemoteWorkerSummary['status'],
    trustMode: String(item.trust_mode ?? 'trusted_auto') as RemoteWorkerSummary['trustMode'],
    transport: String(item.transport ?? 'websocket') as RemoteWorkerSummary['transport'],
    displayMode: String(item.display_mode ?? 'unknown') as RemoteWorkerSummary['displayMode'],
    browserDefaultTarget: String(item.browser_default_target ?? 'personal_profile'),
    publicCapabilities:
      (item.public_capabilities as Record<string, unknown> | null) ?? {},
    metadataJson: (item.metadata_json as Record<string, unknown> | null) ?? {},
    pauseRequested: Boolean(item.pause_requested),
    revokedAt: item.revoked_at == null ? null : String(item.revoked_at),
    lastSeenAt: item.last_seen_at == null ? null : String(item.last_seen_at),
    lastError: item.last_error == null ? null : String(item.last_error),
    capabilitySnapshot: normalizeRemoteWorkerCapabilitySnapshot(
      (item.capability_snapshot as Record<string, unknown> | null) ?? null,
    ),
    recentRuns: Array.isArray(item.recent_runs)
      ? item.recent_runs.map((run) => normalizeRemoteExecutionRun(run as Record<string, unknown>))
      : [],
  }
}

function normalizeTrustedAutoBrowserRuntime(
  item: Record<string, unknown> | null | undefined,
): TrustedAutoBrowserRuntime | null {
  if (!item) {
    return null
  }
  return {
    profileDir: String(item.profile_dir ?? ''),
    downloadsDir: String(item.downloads_dir ?? ''),
    traceDir: String(item.trace_dir ?? ''),
    browsersPath: String(item.browsers_path ?? ''),
    browserExecutable: item.browser_executable == null ? null : String(item.browser_executable),
    desktopShell: item.desktop_shell == null ? null : String(item.desktop_shell),
    ready: Boolean(item.ready),
    statusReason: String(item.status_reason ?? ''),
  }
}

export function normalizeContentReadinessItem(
  item: Record<string, unknown>,
): ContentReadinessItem {
  return {
    platformKey: String(item.platform_key ?? ''),
    displayName: String(item.display_name ?? ''),
    status: String(item.status ?? 'blocked') as ContentReadinessItem['status'],
    availableActions: Array.isArray(item.available_actions)
      ? (item.available_actions as string[])
      : [],
    blockingReasons: Array.isArray(item.blocking_reasons)
      ? (item.blocking_reasons as string[])
      : [],
    accountLabel: item.account_label == null ? null : String(item.account_label),
    connectorKey: item.connector_key == null ? null : String(item.connector_key),
    requiresApproval: Boolean(item.requires_approval),
    lastCheckedAt: String(item.last_checked_at ?? ''),
    connected: Boolean(item.connected),
    needsAuth: Boolean(item.needs_auth),
    missingGrant: Boolean(item.missing_grant),
    connectionId: item.connection_id == null ? null : Number(item.connection_id),
    accountId: item.account_id == null ? null : Number(item.account_id),
    contentAccountId: item.content_account_id == null ? null : String(item.content_account_id),
    grantedScopes: Array.isArray(item.granted_scopes) ? (item.granted_scopes as string[]) : [],
    linkedProjects: Array.isArray(item.linked_projects) ? (item.linked_projects as string[]) : [],
  }
}

export function normalizeContentReadinessSnapshot(
  item: Record<string, unknown> | null | undefined,
): ContentReadinessSnapshot | null {
  if (!item) {
    return null
  }
  const summary = (item.summary as Record<string, unknown> | null) ?? {}
  return {
    snapshotKey: String(item.snapshot_key ?? ''),
    checkedAt: String(item.checked_at ?? ''),
    summary: {
      totalItems: Number(summary.total_items ?? 0),
      blocked: Number(summary.blocked ?? 0),
      metadataOnly: Number(summary.metadata_only ?? 0),
      readReady: Number(summary.read_ready ?? 0),
      draftReady: Number(summary.draft_ready ?? 0),
      supervisedReady: Number(summary.supervised_ready ?? 0),
      publishMode: String(summary.publish_mode ?? 'draft_only'),
      primaryYoutubeStatus:
        summary.primary_youtube_status == null ? null : String(summary.primary_youtube_status),
    },
    items: Array.isArray(item.items)
      ? item.items.map((entry) => normalizeContentReadinessItem(entry as Record<string, unknown>))
      : [],
  }
}

export function normalizeAccessSubjectStatus(
  item: Record<string, unknown>,
): AccessSubjectStatus {
  return {
    subjectKey: String(item.subject_key ?? ''),
    subjectType: String(item.subject_type ?? 'unknown'),
    displayName: String(item.display_name ?? ''),
    accessLevel: String(item.access_level ?? 'unavailable') as AccessSubjectStatus['accessLevel'],
    summary: String(item.summary ?? ''),
    reasons: Array.isArray(item.reasons) ? (item.reasons as string[]) : [],
    availableActions: Array.isArray(item.available_actions) ? (item.available_actions as string[]) : [],
    accountLabel: item.account_label == null ? null : String(item.account_label),
    connectorKey: item.connector_key == null ? null : String(item.connector_key),
    metadata: (item.metadata as Record<string, unknown> | null) ?? {},
    lastCheckedAt: String(item.last_checked_at ?? ''),
  }
}

export function normalizeAccessChange(
  item: Record<string, unknown>,
): AccessChange {
  return {
    subjectKey: String(item.subject_key ?? ''),
    subjectType: String(item.subject_type ?? 'unknown'),
    displayName: String(item.display_name ?? ''),
    fromAccessLevel: String(item.from_access_level ?? 'none') as AccessChange['fromAccessLevel'],
    toAccessLevel: String(item.to_access_level ?? 'none') as AccessChange['toAccessLevel'],
    changeKind: String(item.change_kind ?? 'state'),
    title: String(item.title ?? 'Zmienil sie dostep'),
    detail: String(item.detail ?? ''),
    userVisible: Boolean(item.user_visible),
  }
}

export function normalizeAccessRealitySnapshot(
  item: Record<string, unknown> | null | undefined,
): AccessRealitySnapshot | null {
  if (!item) {
    return null
  }
  const summary = (item.summary as Record<string, unknown> | null) ?? {}
  return {
    snapshotKey: String(item.snapshot_key ?? ''),
    checkedAt: String(item.checked_at ?? ''),
    summary: {
      totalSubjects: Number(summary.total_subjects ?? 0),
      none: Number(summary.none ?? 0),
      identityOnly: Number(summary.identity_only ?? 0),
      metadataOnly: Number(summary.metadata_only ?? 0),
      configured: Number(summary.configured ?? 0),
      liveRead: Number(summary.live_read ?? 0),
      liveWrite: Number(summary.live_write ?? 0),
      blocked: Number(summary.blocked ?? 0),
      unavailable: Number(summary.unavailable ?? 0),
    },
    subjects: Array.isArray(item.subjects)
      ? item.subjects.map((subject) => normalizeAccessSubjectStatus(subject as Record<string, unknown>))
      : [],
    operationalContext: (item.operational_context as Record<string, unknown> | null) ?? {},
  }
}

function normalizeTrustedAutoTrustGrant(item: Record<string, unknown>): TrustedAutoTrustGrant {
  return {
    trustGrantId: Number(item.trust_grant_id),
    projectId: Number(item.project_id),
    projectName: item.project_name == null ? null : String(item.project_name),
    accountId: Number(item.account_id),
    accountLabel: item.account_label == null ? null : String(item.account_label),
    connectorKey: String(item.connector_key ?? ''),
    mode: String(item.mode ?? 'blocked'),
    domainAllowlist: Array.isArray(item.domain_allowlist) ? (item.domain_allowlist as string[]) : [],
    actionFamilies: Array.isArray(item.action_families) ? (item.action_families as string[]) : [],
    fileWriteScope: Array.isArray(item.file_write_scope) ? (item.file_write_scope as string[]) : [],
    loginAllowed: Boolean(item.login_allowed),
    twoFactorAllowed: Boolean(item.two_factor_allowed),
    publishAllowed: Boolean(item.publish_allowed),
    browserAutomationAllowed: Boolean(item.browser_automation_allowed),
    connectorWriteAllowed: Boolean(item.connector_write_allowed),
    localRenderAllowed: Boolean(item.local_render_allowed),
    maxRetries: Number(item.max_retries ?? 0),
    cooldownMinutes: Number(item.cooldown_minutes ?? 0),
    isEnabled: Boolean(item.is_enabled),
    notes: item.notes == null ? null : String(item.notes),
    updatedAt: String(item.updated_at ?? ''),
  }
}

function normalizeTrustedAutoAccount(item: Record<string, unknown>): TrustedAutoAccount {
  const profile = item.credential_profile as Record<string, unknown> | null | undefined
  return {
    accountId: Number(item.account_id),
    label: String(item.label ?? ''),
    providerKey: String(item.provider_key ?? ''),
    accountIdentifier: String(item.account_identifier ?? ''),
    secretType: String(item.secret_type ?? 'oauth_token_later'),
    status: String(item.status ?? 'unknown'),
    hasSecret: Boolean(item.has_secret),
    maskedSecretState: String(item.masked_secret_state ?? ''),
    linkedProjectId: item.linked_project_id == null ? null : Number(item.linked_project_id),
    linkedIntegrationKey:
      item.linked_integration_key == null ? null : String(item.linked_integration_key),
    allowedCapabilities: Array.isArray(item.allowed_capabilities)
      ? (item.allowed_capabilities as string[])
      : [],
    credentialProfile: profile
      ? {
          credentialProfileId: Number(profile.credential_profile_id),
          loginIdentifier:
            profile.login_identifier == null ? null : String(profile.login_identifier),
          hasPassword: Boolean(profile.has_password),
          hasTotp: Boolean(profile.has_totp),
          hasBackupCodes: Boolean(profile.has_backup_codes),
          recoveryEmail: profile.recovery_email == null ? null : String(profile.recovery_email),
          hasRecoveryEmailSecret: Boolean(profile.has_recovery_email_secret),
          connectorMappingJson:
            (profile.connector_mapping_json as Record<string, unknown> | null) ?? {},
          status: String(profile.status ?? 'unknown'),
          notes: profile.notes == null ? null : String(profile.notes),
        }
      : null,
    trustGrants: Array.isArray(item.trust_grants)
      ? item.trust_grants.map((grant) => normalizeTrustedAutoTrustGrant(grant as Record<string, unknown>))
      : [],
  }
}

function normalizeTrustedAutoAccountFeedback(
  item: Record<string, unknown>,
): TrustedAutoAccountFeedback {
  return {
    sequence: Number(item.sequence ?? 0),
    accountId: Number(item.account_id ?? 0),
    label: String(item.label ?? ''),
    providerKey: String(item.provider_key ?? ''),
    accountIdentifier: String(item.account_identifier ?? ''),
    url: item.url == null ? null : String(item.url),
    hasSecret: Boolean(item.has_secret),
    grantCount: Number(item.grant_count ?? 0),
    checkedAt: String(item.checked_at ?? ''),
    status: String(item.status ?? 'config_missing') as TrustedAutoAccountFeedback['status'],
    httpStatus: item.http_status == null ? null : Number(item.http_status),
    reason: String(item.reason ?? ''),
  }
}

export function normalizeTrustedAutoSchedulerJob(item: Record<string, unknown>): TrustedAutoSchedulerJob {
  return {
    jobId: Number(item.job_id),
    projectId: Number(item.project_id),
    projectName: item.project_name == null ? null : String(item.project_name),
    jobType: String(item.job_type ?? ''),
    triggerMode: String(item.trigger_mode ?? 'manual'),
    status: String(item.status ?? 'queued'),
    connectorKey: item.connector_key == null ? null : String(item.connector_key),
    platformKey: item.platform_key == null ? null : String(item.platform_key),
    scheduledFor: item.scheduled_for == null ? null : String(item.scheduled_for),
    cronExpression: item.cron_expression == null ? null : String(item.cron_expression),
    payloadJson: (item.payload_json as Record<string, unknown> | null) ?? {},
    retryCount: Number(item.retry_count ?? 0),
    retryBudget: Number(item.retry_budget ?? 0),
    requiresTrustedAuto: Boolean(item.requires_trusted_auto),
    cooldownKey: item.cooldown_key == null ? null : String(item.cooldown_key),
    lastError: item.last_error == null ? null : String(item.last_error),
    lastRunAt: item.last_run_at == null ? null : String(item.last_run_at),
    nextRunAt: item.next_run_at == null ? null : String(item.next_run_at),
    lockedAt: item.locked_at == null ? null : String(item.locked_at),
    cancelledAt: item.cancelled_at == null ? null : String(item.cancelled_at),
  }
}

export function normalizeTrustedAutoExecutionRun(item: Record<string, unknown>): TrustedAutoExecutionRun {
  return {
    executionRunId: Number(item.execution_run_id),
    projectId: Number(item.project_id),
    projectName: item.project_name == null ? null : String(item.project_name),
    schedulerJobId: item.scheduler_job_id == null ? null : Number(item.scheduler_job_id),
    accountId: item.account_id == null ? null : Number(item.account_id),
    trustGrantId: item.trust_grant_id == null ? null : Number(item.trust_grant_id),
    runType: String(item.run_type ?? ''),
    status: String(item.status ?? 'unknown'),
    goal: String(item.goal ?? ''),
    connectorKey: item.connector_key == null ? null : String(item.connector_key),
    platformKey: item.platform_key == null ? null : String(item.platform_key),
    riskLevel: String(item.risk_level ?? 'medium'),
    inputJson: (item.input_json as Record<string, unknown> | null) ?? {},
    outputJson: (item.output_json as Record<string, unknown> | null) ?? {},
    artifactsJson: (item.artifacts_json as Record<string, unknown> | null) ?? {},
    errorMessage: item.error_message == null ? null : String(item.error_message),
    startedAt: item.started_at == null ? null : String(item.started_at),
    finishedAt: item.finished_at == null ? null : String(item.finished_at),
    preview:
      item.preview && typeof item.preview === 'object'
        ? normalizeAgentActionPreview(item.preview as Record<string, unknown>)
        : null,
    steps: Array.isArray(item.steps)
      ? item.steps.map((step) => ({
          executionStepId: Number((step as Record<string, unknown>).execution_step_id),
          stepIndex: Number((step as Record<string, unknown>).step_index ?? 0),
          stepType: String((step as Record<string, unknown>).step_type ?? ''),
          stepName: String((step as Record<string, unknown>).step_name ?? ''),
          status: String((step as Record<string, unknown>).status ?? 'unknown'),
          selectorHint:
            (step as Record<string, unknown>).selector_hint == null
              ? null
              : String((step as Record<string, unknown>).selector_hint),
          errorMessage:
            (step as Record<string, unknown>).error_message == null
              ? null
              : String((step as Record<string, unknown>).error_message),
          screenshotPath:
            (step as Record<string, unknown>).screenshot_path == null
              ? null
              : String((step as Record<string, unknown>).screenshot_path),
          startedAt:
            (step as Record<string, unknown>).started_at == null
              ? null
              : String((step as Record<string, unknown>).started_at),
          finishedAt:
            (step as Record<string, unknown>).finished_at == null
              ? null
              : String((step as Record<string, unknown>).finished_at),
        }))
      : [],
  }
}

export function normalizeAgentActionPreview(
  item: Record<string, unknown>,
): AgentActionPreview {
  return {
    executionRunId: Number(item.execution_run_id),
    projectId: Number(item.project_id),
    kind: String(item.kind ?? 'browser_navigate') as AgentActionPreview['kind'],
    title: String(item.title ?? ''),
    summary: String(item.summary ?? ''),
    impactSummary: String(item.impact_summary ?? ''),
    requiredApprovalLevel: String(item.required_approval_level ?? 'single') as AgentActionPreview['requiredApprovalLevel'],
    previewPayload: (item.preview_payload as Record<string, unknown> | null) ?? {},
    status: String(item.status ?? 'waiting_for_input'),
    createdAt: String(item.created_at ?? ''),
    updatedAt: String(item.updated_at ?? ''),
  }
}

function normalizeAgentStatusPayload(payload: Record<string, unknown>): AgentStatusPayload {
  const agent = (payload.agent as Record<string, unknown> | null) ?? {}
  return {
    agent: {
      state: String(agent.state ?? 'unknown'),
      activeTasks: Number(agent.active_tasks ?? 0),
      pendingApprovals: Number(agent.pending_approvals ?? 0),
      errors: Number(agent.errors ?? 0),
      lastDecisionAt: agent.last_decision_at == null ? null : String(agent.last_decision_at),
      lastToolCallAt: agent.last_tool_call_at == null ? null : String(agent.last_tool_call_at),
    },
    activeTasks: Array.isArray(payload.active_tasks)
      ? payload.active_tasks.map((item) => item as Record<string, unknown>)
      : [],
    ubuntu: (payload.ubuntu as Record<string, unknown> | null) ?? {},
    browser: (payload.browser as Record<string, unknown> | null) ?? {},
    shortPipeline: (payload.short_pipeline as Record<string, unknown> | null) ?? {},
    youtubeAutomation: (payload.youtube_automation as Record<string, unknown> | null) ?? {},
    monetizationFactory: normalizeMonetizationFactoryStatus(
      (payload.monetization_factory as Record<string, unknown> | null) ?? {},
    ),
    qualityRuntime: normalizeAgentQualityRuntime(
      (payload.quality_runtime as Record<string, unknown> | null) ?? {},
    ),
    recentArtifacts: Array.isArray(payload.recent_artifacts)
      ? payload.recent_artifacts.map((item) => normalizeAgentArtifact(item as Record<string, unknown>))
      : [],
    pendingApprovals: Array.isArray(payload.pending_approvals)
      ? payload.pending_approvals.map((item) => normalizeAgentActionPreview(item as Record<string, unknown>))
      : [],
    errors: Array.isArray(payload.errors)
      ? payload.errors.map((item) => item as Record<string, unknown>)
      : [],
    logs: (payload.logs as Record<string, string> | null) ?? {},
  }
}

function normalizeMonetizationFactoryStatus(
  payload: Record<string, unknown>,
): AgentStatusPayload['monetizationFactory'] {
  return {
    status: String(payload.status ?? 'not_initialized'),
    ready: Boolean(payload.ready),
    revenueVector: String(payload.revenue_vector ?? 'automated_shorts_factory'),
    stateMachineReady: Boolean(payload.state_machine_ready),
    channelOpsReady: Boolean(payload.channel_ops_ready),
    shortsContractReady: Boolean(payload.shorts_contract_ready),
    approvalGateReady: Boolean(payload.approval_gate_ready),
    youtubeReadinessStatus: String(payload.youtube_readiness_status ?? 'unknown'),
    privateUploadOnly: payload.private_upload_only == null ? true : Boolean(payload.private_upload_only),
    manualUploadReadySupported: Boolean(payload.manual_upload_ready_supported),
    missingRequirements: Array.isArray(payload.missing_requirements)
      ? payload.missing_requirements.map((item) => String(item))
      : [],
    requiredArtifacts: Array.isArray(payload.required_artifacts)
      ? payload.required_artifacts.map((item) => String(item))
      : [],
    statusPath: payload.status_path == null ? null : String(payload.status_path),
    logPath: payload.log_path == null ? null : String(payload.log_path),
    createdAt: payload.created_at == null ? null : String(payload.created_at),
    statusReason: payload.status_reason == null ? null : String(payload.status_reason),
  }
}

function normalizeAgentLogEntry(item: Record<string, unknown>): AgentLogEntry {
  return {
    timestamp: item.timestamp == null ? null : String(item.timestamp),
    event: item.event == null ? undefined : String(item.event),
    toolName: item.tool_name == null ? null : String(item.tool_name),
    step: item.step == null ? null : String(item.step),
    status: item.status == null ? null : String(item.status),
    success: item.success == null ? null : Boolean(item.success),
    error: item.error == null ? null : String(item.error),
    message: item.message == null ? null : String(item.message),
    command: item.command == null ? null : String(item.command),
    riskLevel: item.risk_level == null ? null : String(item.risk_level),
    raw: item,
  }
}

function normalizeAgentTool(item: Record<string, unknown>): AgentToolDescriptor {
  return {
    key: String(item.key ?? ''),
    label: String(item.label ?? item.key ?? ''),
    status: String(item.status ?? 'unknown'),
    riskLevel: String(item.risk_level ?? 'low'),
    logPath: item.log_path == null ? null : String(item.log_path),
    remoteLogPath: item.remote_log_path == null ? null : String(item.remote_log_path),
    actions: Array.isArray(item.actions) ? item.actions.map((action) => String(action)) : [],
  }
}

function normalizeAgentQualityRuntime(payload: Record<string, unknown>): AgentStatusPayload['qualityRuntime'] {
  return {
    ready: Boolean(payload.ready),
    runsDir: payload.runs_dir == null ? null : String(payload.runs_dir),
    statusReason: payload.status_reason == null ? null : String(payload.status_reason),
    latestRun:
      payload.latest_run && typeof payload.latest_run === 'object'
        ? normalizeAgentQualityRun(payload.latest_run as Record<string, unknown>)
        : null,
  }
}

function normalizeAgentQualityRun(payload: Record<string, unknown>): AgentStatusPayload['qualityRuntime']['latestRun'] {
  return {
    runId: String(payload.run_id ?? ''),
    createdAt: payload.created_at == null ? null : String(payload.created_at),
    task: String(payload.task ?? ''),
    area: String(payload.area ?? ''),
    status: String(payload.status ?? 'DONE_NEEDS_REVIEW'),
    score: payload.score == null ? null : Number(payload.score),
    reportPath: payload.report_path == null ? null : String(payload.report_path),
    jsonPath: payload.json_path == null ? null : String(payload.json_path),
    evidenceCount: Number(payload.evidence_count ?? 0),
  }
}

function normalizeAgentArtifact(item: Record<string, unknown>): AgentArtifactSummary {
  return {
    path: String(item.path ?? ''),
    storagePath: String(item.storage_path ?? ''),
    filename: String(item.filename ?? ''),
    extension: String(item.extension ?? ''),
    sizeBytes: Number(item.size_bytes ?? 0),
    updatedAt: String(item.updated_at ?? ''),
  }
}

function normalizeTrustedAutoMediaPipelinePreset(
  item: Record<string, unknown>,
): TrustedAutoMediaPipelinePreset {
  return {
    presetId: Number(item.preset_id),
    projectId: item.project_id == null ? null : Number(item.project_id),
    name: String(item.name ?? ''),
    targetPlatform: String(item.target_platform ?? ''),
    ttsEngine: String(item.tts_engine ?? ''),
    imageProvider: String(item.image_provider ?? ''),
    videoRenderer: String(item.video_renderer ?? ''),
    subtitleEngine: String(item.subtitle_engine ?? ''),
    promptStyle: item.prompt_style == null ? null : String(item.prompt_style),
    targetDurationSec: Number(item.target_duration_sec ?? 40),
    vramFloorGb: Number(item.vram_floor_gb ?? 4),
    publishMode: String(item.publish_mode ?? 'trusted_auto'),
    cooldownHours: Number(item.cooldown_hours ?? 24),
    stepsJson: (item.steps_json as Record<string, unknown> | null) ?? {},
    isDefault: Boolean(item.is_default),
  }
}

function normalizeTrustedAutoSummary(item: Record<string, unknown>): TrustedAutoSummary {
  const runtimeControls = (item.runtime_controls as Record<string, unknown> | null) ?? {}
  const youtubeConnector = (item.youtube_connector as Record<string, unknown> | null) ?? null
  return {
    runtimeControls: {
      autonomyMode: String(runtimeControls.autonomy_mode ?? 'blocked') as TrustedAutoSummary['runtimeControls']['autonomyMode'],
      schedulerEnabled: Boolean(runtimeControls.scheduler_enabled),
      emergencyStopEnabled: Boolean(runtimeControls.emergency_stop_enabled),
      pauseAllJobs: Boolean(runtimeControls.pause_all_jobs),
      loginAutomationEnabled: Boolean(runtimeControls.login_automation_enabled),
      twoFactorAutomationEnabled: Boolean(runtimeControls.two_factor_automation_enabled),
      publishAutomationEnabled: Boolean(runtimeControls.publish_automation_enabled),
      defaultRetryBudget: Number(runtimeControls.default_retry_budget ?? 2),
      defaultVramFloorGb: Number(runtimeControls.default_vram_floor_gb ?? 4),
    },
    accounts: Array.isArray(item.accounts)
      ? item.accounts.map((account) => normalizeTrustedAutoAccount(account as Record<string, unknown>))
      : [],
    trustGrants: Array.isArray(item.trust_grants)
      ? item.trust_grants.map((grant) => normalizeTrustedAutoTrustGrant(grant as Record<string, unknown>))
      : [],
    schedulerJobs: Array.isArray(item.scheduler_jobs)
      ? item.scheduler_jobs.map((job) => normalizeTrustedAutoSchedulerJob(job as Record<string, unknown>))
      : [],
    executionRuns: Array.isArray(item.execution_runs)
      ? item.execution_runs.map((run) => normalizeTrustedAutoExecutionRun(run as Record<string, unknown>))
      : [],
    mediaPipelinePresets: Array.isArray(item.media_pipeline_presets)
      ? item.media_pipeline_presets.map((preset) =>
          normalizeTrustedAutoMediaPipelinePreset(preset as Record<string, unknown>),
        )
      : [],
    hardwareSnapshot: normalizeTrustedAutoHardwareSnapshot(
      (item.hardware_snapshot as Record<string, unknown> | null) ?? null,
    ),
    browserRuntime: normalizeTrustedAutoBrowserRuntime(
      (item.browser_runtime as Record<string, unknown> | null) ?? null,
    ),
    youtubeConnector: youtubeConnector
      ? {
          connectorKey: String(youtubeConnector.connector_key ?? 'youtube-studio'),
          displayName: String(youtubeConnector.display_name ?? 'YouTube Studio'),
          configured: Boolean(youtubeConnector.configured),
          connected: Boolean(youtubeConnector.connected),
          ready: Boolean(youtubeConnector.ready),
          warnings: Array.isArray(youtubeConnector.warnings)
            ? (youtubeConnector.warnings as string[])
            : [],
          accountId:
            youtubeConnector.account_id == null ? null : Number(youtubeConnector.account_id),
          channelTitle:
            youtubeConnector.channel_title == null
              ? null
              : String(youtubeConnector.channel_title),
          grantedScopes: Array.isArray(youtubeConnector.granted_scopes)
            ? (youtubeConnector.granted_scopes as string[])
            : [],
        }
      : null,
    contentReadiness: normalizeContentReadinessSnapshot(
      (item.content_readiness as Record<string, unknown> | null) ?? null,
    ),
  }
}

function normalizeSettingsOmegaProtocol(
  item: Record<string, unknown> | null | undefined,
): SettingsControlCenterPayload['omegaProtocol'] {
  const browserRuntime = (item?.browser_runtime as Record<string, unknown> | null) ?? {}
  const runtimeProbes = Array.isArray(item?.runtime_probes) ? item?.runtime_probes : []
  return {
    profileKey: String(item?.profile_key ?? 'omega_expanded'),
    mode: String(item?.mode ?? 'expanded'),
    activated: Boolean(item?.activated),
    readyForBrowserWorkflows: Boolean(item?.ready_for_browser_workflows),
    readyForAccountLogin: Boolean(item?.ready_for_account_login),
    readyForTwoFactor: Boolean(item?.ready_for_two_factor),
    readyForPublish: Boolean(item?.ready_for_publish),
    webAdvisorFallbackEnabled: Boolean(item?.web_advisor_fallback_enabled),
    trustedAccountsCount: Number(item?.trusted_accounts_count ?? 0),
    trustGrantsCount: Number(item?.trust_grants_count ?? 0),
    runtimeControls:
      ((item?.runtime_controls as Record<string, unknown> | null) ?? {}),
    browserRuntime: {
      profileDir: String(browserRuntime.profile_dir ?? ''),
      downloadsDir: String(browserRuntime.downloads_dir ?? ''),
      traceDir: String(browserRuntime.trace_dir ?? ''),
      browsersPath: String(browserRuntime.browsers_path ?? ''),
      browserExecutable:
        browserRuntime.browser_executable == null ? null : String(browserRuntime.browser_executable),
      desktopShell: browserRuntime.desktop_shell == null ? null : String(browserRuntime.desktop_shell),
      ready: Boolean(browserRuntime.ready),
      statusReason: String(browserRuntime.status_reason ?? ''),
    },
    trustedFilesystemRoots: Array.isArray(item?.trusted_filesystem_roots)
      ? (item?.trusted_filesystem_roots as string[])
      : [],
    runtimeProbes: runtimeProbes.map((probe) => ({
      key: String((probe as Record<string, unknown>).key ?? ''),
      label: String((probe as Record<string, unknown>).label ?? ''),
      status: String((probe as Record<string, unknown>).status ?? 'unknown'),
      ready: Boolean((probe as Record<string, unknown>).ready),
      reason: String((probe as Record<string, unknown>).reason ?? ''),
    })),
    allowedCapabilities: Array.isArray(item?.allowed_capabilities)
      ? (item?.allowed_capabilities as string[])
      : [],
    blockedCapabilities: Array.isArray(item?.blocked_capabilities)
      ? (item?.blocked_capabilities as string[])
      : [],
    recommendedNextSteps: Array.isArray(item?.recommended_next_steps)
      ? (item?.recommended_next_steps as string[])
      : [],
    personaProtocol: Array.isArray(item?.persona_protocol)
      ? (item?.persona_protocol as string[])
      : [],
    notePath: item?.note_path == null ? null : String(item?.note_path),
    noteWrittenAt: item?.note_written_at == null ? null : String(item?.note_written_at),
  }
}

function normalizeSettingsControlCenter(
  payload: Record<string, unknown>,
): SettingsControlCenterPayload {
  const runtimeSummaryPayload = (payload.runtime_summary ?? {}) as Record<string, unknown>
  const voiceSttSummaryPayload = (runtimeSummaryPayload.voice_stt ?? {}) as Record<string, unknown>

  return {
    profile: {
      userId: Number((payload.profile as Record<string, unknown>).user_id),
      displayName: String((payload.profile as Record<string, unknown>).display_name ?? ''),
      handle: String((payload.profile as Record<string, unknown>).handle ?? ''),
      email:
        (payload.profile as Record<string, unknown>).email == null
          ? null
          : String((payload.profile as Record<string, unknown>).email),
      bio:
        (payload.profile as Record<string, unknown>).bio == null
          ? null
          : String((payload.profile as Record<string, unknown>).bio),
      locale: String((payload.profile as Record<string, unknown>).locale ?? 'pl-PL'),
      timezone: String((payload.profile as Record<string, unknown>).timezone ?? 'Europe/Warsaw'),
    },
    style: {
      responseLength: String((payload.style as Record<string, unknown>).response_length ?? 'medium') as SettingsControlCenterPayload['style']['responseLength'],
      tone: String((payload.style as Record<string, unknown>).tone ?? 'natural') as SettingsControlCenterPayload['style']['tone'],
      structure: String((payload.style as Record<string, unknown>).structure ?? 'mixed') as SettingsControlCenterPayload['style']['structure'],
      criticalityLevel: Number((payload.style as Record<string, unknown>).criticality_level ?? 3),
      supportivenessLevel: Number((payload.style as Record<string, unknown>).supportiveness_level ?? 3),
      professionalDefault: Boolean((payload.style as Record<string, unknown>).professional_default),
    },
    policy: {
      automationLevel: String((payload.policy as Record<string, unknown>).automation_level ?? 'assisted') as SettingsControlCenterPayload['policy']['automationLevel'],
      verificationMode: String((payload.policy as Record<string, unknown>).verification_mode ?? 'smart') as SettingsControlCenterPayload['policy']['verificationMode'],
      allowLocalFileRead: Boolean((payload.policy as Record<string, unknown>).allow_local_file_read),
      allowLocalFileWrite: Boolean((payload.policy as Record<string, unknown>).allow_local_file_write),
      allowWebRead: Boolean((payload.policy as Record<string, unknown>).allow_web_read),
      allowExternalFallback: Boolean((payload.policy as Record<string, unknown>).allow_external_fallback),
      allowExternalAttachmentMetadata: Boolean(
        (payload.policy as Record<string, unknown>).allow_external_attachment_metadata,
      ),
      allowExternalAttachmentContent: Boolean(
        (payload.policy as Record<string, unknown>).allow_external_attachment_content,
      ),
      preferLocalLlm: Boolean((payload.policy as Record<string, unknown>).prefer_local_llm),
      defaultProviderPosture: String(
        (payload.policy as Record<string, unknown>).default_provider_posture ?? 'prefer-local',
      ) as SettingsControlCenterPayload['policy']['defaultProviderPosture'],
      defaultPersonaProfile: String(
        (payload.policy as Record<string, unknown>).default_persona_profile ?? 'strategist',
      ) as SettingsControlCenterPayload['policy']['defaultPersonaProfile'],
      jarvisAddressAs: String(
        (payload.policy as Record<string, unknown>).jarvis_address_as ?? 'none',
      ) as SettingsControlCenterPayload['policy']['jarvisAddressAs'],
      maxExternalAttachmentTextChars:
        (payload.policy as Record<string, unknown>).max_external_attachment_text_chars == null
          ? null
          : Number((payload.policy as Record<string, unknown>).max_external_attachment_text_chars),
      maxDailyCost:
        (payload.policy as Record<string, unknown>).max_daily_cost == null
          ? null
          : Number((payload.policy as Record<string, unknown>).max_daily_cost),
      maxMonthlyCost:
        (payload.policy as Record<string, unknown>).max_monthly_cost == null
          ? null
          : Number((payload.policy as Record<string, unknown>).max_monthly_cost),
    },
    runtime: {
      browserControlEnabled: Boolean((payload.runtime as Record<string, unknown>).browser_control_enabled),
      stealthHeadlessEnabled: Boolean((payload.runtime as Record<string, unknown>).stealth_headless_enabled),
      browserAllowAnyUrl: Boolean((payload.runtime as Record<string, unknown>).browser_allow_any_url),
      externalActionsEnabled: Boolean((payload.runtime as Record<string, unknown>).external_actions_enabled),
      schedulerEnabled: Boolean((payload.runtime as Record<string, unknown>).scheduler_enabled),
      oauthEnabled: Boolean((payload.runtime as Record<string, unknown>).oauth_enabled),
      autonomyMode: String((payload.runtime as Record<string, unknown>).autonomy_mode ?? 'blocked') as SettingsControlCenterPayload['runtime']['autonomyMode'],
      emergencyStopEnabled: Boolean((payload.runtime as Record<string, unknown>).emergency_stop_enabled),
      pauseAllJobs: Boolean((payload.runtime as Record<string, unknown>).pause_all_jobs),
      loginAutomationEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).login_automation_enabled,
      ),
      twoFactorAutomationEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).two_factor_automation_enabled,
      ),
      publishAutomationEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).publish_automation_enabled,
      ),
      publicUploadEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).public_upload_enabled,
      ),
      defaultRetryBudget: Number((payload.runtime as Record<string, unknown>).default_retry_budget ?? 2),
      defaultVramFloorGb: Number((payload.runtime as Record<string, unknown>).default_vram_floor_gb ?? 4),
      voiceSttEngine: String(
        (payload.runtime as Record<string, unknown>).voice_stt_engine ?? 'browser-speech',
      ) as SettingsControlCenterPayload['runtime']['voiceSttEngine'],
      voiceSttModelPath:
        (payload.runtime as Record<string, unknown>).voice_stt_model_path == null
          ? null
          : String((payload.runtime as Record<string, unknown>).voice_stt_model_path),
      voiceSttDevice: String((payload.runtime as Record<string, unknown>).voice_stt_device ?? 'cpu'),
      ambientListeningEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).ambient_listening_enabled,
      ),
      ambientWakeWord: String(
        (payload.runtime as Record<string, unknown>).ambient_wake_word ?? 'Atnos',
      ),
      ambientMicDevice:
        (payload.runtime as Record<string, unknown>).ambient_mic_device == null
          ? null
          : String((payload.runtime as Record<string, unknown>).ambient_mic_device),
      ambientMuted: Boolean((payload.runtime as Record<string, unknown>).ambient_muted),
      ambientContextEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).ambient_context_enabled,
      ),
      trendWatchEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).trend_watch_enabled,
      ),
      trendWatchQuery: String(
        (payload.runtime as Record<string, unknown>).trend_watch_query ?? 'short video hooks',
      ),
      trendWatchRegion: String(
        (payload.runtime as Record<string, unknown>).trend_watch_region ?? 'PL',
      ),
      trendWatchIntervalMinutes: Number(
        (payload.runtime as Record<string, unknown>).trend_watch_interval_minutes ?? 180,
      ),
      trendWatchLastRunAt:
        (payload.runtime as Record<string, unknown>).trend_watch_last_run_at == null
          ? null
          : String((payload.runtime as Record<string, unknown>).trend_watch_last_run_at),
      voiceRepliesEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).voice_replies_enabled,
      ),
      voiceReplyVolume: Number(
        (payload.runtime as Record<string, unknown>).voice_reply_volume ?? 0.8,
      ),
      voiceReplyVoiceId:
        (payload.runtime as Record<string, unknown>).voice_reply_voice_id == null
          ? null
          : Number((payload.runtime as Record<string, unknown>).voice_reply_voice_id),
      ambientListeningAvailable: Boolean(
        (payload.runtime as Record<string, unknown>).ambient_listening_available,
      ),
      ambientListeningReason: String(
        (payload.runtime as Record<string, unknown>).ambient_listening_reason ?? '',
      ),
      lastUserInteractionAt:
        (payload.runtime as Record<string, unknown>).last_user_interaction_at == null
          ? null
          : String((payload.runtime as Record<string, unknown>).last_user_interaction_at),
      lastBriefingAt:
        (payload.runtime as Record<string, unknown>).last_briefing_at == null
          ? null
          : String((payload.runtime as Record<string, unknown>).last_briefing_at),
      voiceRepliesAvailable: Boolean(
        (payload.runtime as Record<string, unknown>).voice_replies_available,
      ),
      voiceRepliesReason: String(
        (payload.runtime as Record<string, unknown>).voice_replies_reason ?? '',
      ),
      localShortsEnabled: Boolean(
        (payload.runtime as Record<string, unknown>).local_shorts_enabled,
      ),
      localShortsReady: Boolean(
        (payload.runtime as Record<string, unknown>).local_shorts_ready,
      ),
      localShortsReason: String(
        (payload.runtime as Record<string, unknown>).local_shorts_reason ?? '',
      ),
      localShortsRuntime: Array.isArray(
        (payload.runtime as Record<string, unknown>).local_shorts_runtime,
      )
        ? (
            (payload.runtime as Record<string, unknown>).local_shorts_runtime as Array<Record<string, unknown>>
          ).map((item) => ({
            key: String(item.key ?? 'runtime'),
            label: String(item.label ?? 'Runtime'),
            ready: Boolean(item.ready),
            path: item.path == null ? null : String(item.path),
            reason: String(item.reason ?? ''),
          }))
        : [],
      executionEnabled: Boolean((payload.runtime as Record<string, unknown>).execution_enabled),
      pcOperatorMode: String((payload.runtime as Record<string, unknown>).pc_operator_mode ?? 'planning_only'),
      actionBrokerMode: String(
        (payload.runtime as Record<string, unknown>).action_broker_mode ?? 'preview_approval_only',
      ),
      playwrightAdapterAvailable: Boolean(
        (payload.runtime as Record<string, unknown>).playwright_adapter_available,
      ),
    },
    capabilities: {
      browserControlEnabled: Boolean(
        (payload.capabilities as Record<string, unknown>).browser_control_enabled,
      ),
      externalActionsEnabled: Boolean(
        (payload.capabilities as Record<string, unknown>).external_actions_enabled,
      ),
      schedulerEnabled: Boolean((payload.capabilities as Record<string, unknown>).scheduler_enabled),
      playwrightAdapterAvailable: Boolean(
        (payload.capabilities as Record<string, unknown>).playwright_adapter_available,
      ),
      oauthEnabled: Boolean((payload.capabilities as Record<string, unknown>).oauth_enabled),
      obsidianExportEnabled: Boolean(
        (payload.capabilities as Record<string, unknown>).obsidian_export_enabled,
      ),
      autonomyMode:
        (payload.capabilities as Record<string, unknown>).autonomy_mode == null
          ? undefined
          : String((payload.capabilities as Record<string, unknown>).autonomy_mode),
      executionEnabled:
        (payload.capabilities as Record<string, unknown>).execution_enabled == null
          ? undefined
          : Boolean((payload.capabilities as Record<string, unknown>).execution_enabled),
    },
    obsidian: {
      vaultPath:
        (payload.obsidian as Record<string, unknown>).vault_path == null
          ? null
          : String((payload.obsidian as Record<string, unknown>).vault_path),
      exportEnabled: Boolean((payload.obsidian as Record<string, unknown>).export_enabled),
      pathExists: Boolean((payload.obsidian as Record<string, unknown>).path_exists),
      source: String((payload.obsidian as Record<string, unknown>).source ?? 'unknown'),
      warnings: Array.isArray((payload.obsidian as Record<string, unknown>).warnings)
        ? ((payload.obsidian as Record<string, unknown>).warnings as string[])
        : [],
      vaultName: String((payload.obsidian as Record<string, unknown>).vault_name ?? 'obsydian'),
      readable: Boolean((payload.obsidian as Record<string, unknown>).readable),
      noteCount: Number((payload.obsidian as Record<string, unknown>).note_count ?? 0),
    },
    voices: Array.isArray(payload.voices)
      ? payload.voices.map((item) => ({
          voiceId: Number((item as Record<string, unknown>).voice_id),
          providerId:
            (item as Record<string, unknown>).provider_id == null
              ? null
              : Number((item as Record<string, unknown>).provider_id),
          name: String((item as Record<string, unknown>).name ?? ''),
          voiceKey: String((item as Record<string, unknown>).voice_key ?? ''),
          providerMode: String((item as Record<string, unknown>).provider_mode ?? 'remote') as 'local' | 'remote',
          style:
            (item as Record<string, unknown>).style == null
              ? null
              : String((item as Record<string, unknown>).style),
          language: String((item as Record<string, unknown>).language ?? 'pl-PL'),
          speakingRate:
            (item as Record<string, unknown>).speaking_rate == null
              ? null
              : String((item as Record<string, unknown>).speaking_rate),
          supportsRealtime: Boolean((item as Record<string, unknown>).supports_realtime),
          autoReadResponses: Boolean((item as Record<string, unknown>).auto_read_responses),
          isDefault: Boolean((item as Record<string, unknown>).is_default),
          notes:
            (item as Record<string, unknown>).notes == null
              ? null
              : String((item as Record<string, unknown>).notes),
        }))
      : [],
    roles: Array.isArray(payload.roles)
      ? payload.roles.map((item) => ({
          roleId: Number((item as Record<string, unknown>).role_id),
          name: String((item as Record<string, unknown>).name ?? ''),
          slug: String((item as Record<string, unknown>).slug ?? ''),
          description:
            (item as Record<string, unknown>).description == null
              ? null
              : String((item as Record<string, unknown>).description),
          systemPrompt:
            (item as Record<string, unknown>).system_prompt == null
              ? null
              : String((item as Record<string, unknown>).system_prompt),
          tone:
            (item as Record<string, unknown>).tone == null
              ? null
              : String((item as Record<string, unknown>).tone),
          responseStyle:
            (item as Record<string, unknown>).response_style == null
              ? null
              : String((item as Record<string, unknown>).response_style),
          criticalityLevel: Number((item as Record<string, unknown>).criticality_level ?? 3),
          empathyLevel: Number((item as Record<string, unknown>).empathy_level ?? 3),
          firmnessLevel: Number((item as Record<string, unknown>).firmness_level ?? 3),
          scope: String((item as Record<string, unknown>).scope ?? 'global') as 'global' | 'project',
          isDefault: Boolean((item as Record<string, unknown>).is_default),
          isBuiltin: Boolean((item as Record<string, unknown>).is_builtin),
          isArchived: Boolean((item as Record<string, unknown>).is_archived),
        }))
      : [],
    plugins: Array.isArray(payload.plugins)
      ? payload.plugins.map((item) => ({
          pluginId: Number((item as Record<string, unknown>).plugin_id),
          key: String((item as Record<string, unknown>).key ?? ''),
          name: String((item as Record<string, unknown>).name ?? ''),
          summary: String((item as Record<string, unknown>).summary ?? ''),
          source: String((item as Record<string, unknown>).source ?? 'builtin'),
          permissionPolicy: String((item as Record<string, unknown>).permission_policy ?? 'ask') as 'auto' | 'ask' | 'disabled',
          requiresConfirmation: Boolean((item as Record<string, unknown>).requires_confirmation),
          isEnabled: Boolean((item as Record<string, unknown>).is_enabled),
          configJson:
            (item as Record<string, unknown>).config_json == null
              ? null
              : ((item as Record<string, unknown>).config_json as Record<string, unknown>),
        }))
      : [],
    providerOptions: Array.isArray(payload.provider_options)
      ? payload.provider_options.map((item) => ({
          providerId: Number((item as Record<string, unknown>).provider_id),
          providerKey: String((item as Record<string, unknown>).provider_key),
          label: String((item as Record<string, unknown>).label),
          kind: String((item as Record<string, unknown>).kind),
          isEnabled: Boolean((item as Record<string, unknown>).is_enabled),
        }))
      : [],
    providerCards: Array.isArray(payload.provider_cards)
      ? payload.provider_cards.map((item) => normalizeProviderCard(item as Record<string, unknown>))
      : [],
    oauthConnectors: Array.isArray(payload.oauth_connectors)
      ? payload.oauth_connectors.map((connector) => ({
          connectorKey: String((connector as Record<string, unknown>).connector_key),
          displayName: String((connector as Record<string, unknown>).display_name),
          status: String((connector as Record<string, unknown>).status),
          enabled: Boolean((connector as Record<string, unknown>).enabled),
          configured: Boolean((connector as Record<string, unknown>).configured),
          connectionId:
            (connector as Record<string, unknown>).connection_id == null
              ? null
              : Number((connector as Record<string, unknown>).connection_id),
          connectedAt:
            (connector as Record<string, unknown>).connected_at == null
              ? null
              : String((connector as Record<string, unknown>).connected_at),
          setupWarnings: Array.isArray((connector as Record<string, unknown>).setup_warnings)
            ? ((connector as Record<string, unknown>).setup_warnings as string[])
            : [],
        }))
      : [],
    runtimeSummary: {
      providersReady: Number(runtimeSummaryPayload.providers_ready ?? 0),
      enabledModels: Number(runtimeSummaryPayload.enabled_models ?? 0),
      tasksReady: Number(runtimeSummaryPayload.tasks_ready ?? 0),
      hasLocalChat: Boolean(runtimeSummaryPayload.has_local_chat),
      hasExternalFallback: Boolean(runtimeSummaryPayload.has_external_fallback),
      voiceStt: {
        configuredEngine: String(voiceSttSummaryPayload.configured_engine ?? 'browser-speech'),
        effectiveEngine: String(voiceSttSummaryPayload.effective_engine ?? 'browser-speech'),
        status: String(voiceSttSummaryPayload.status ?? 'unknown'),
        statusReason: String(voiceSttSummaryPayload.status_reason ?? ''),
      },
    },
    trustedAuto: normalizeTrustedAutoSummary(
      (payload.trusted_auto as Record<string, unknown> | null) ?? {},
    ),
    accessReality: normalizeAccessRealitySnapshot(
      (payload.access_reality as Record<string, unknown> | null) ?? null,
    ),
    omegaProtocol: normalizeSettingsOmegaProtocol(
      (payload.omega_protocol as Record<string, unknown> | null) ?? null,
    ),
    skillsDashboard: {
      summary: {
        totalSkills: Number(
          ((payload.skills_dashboard as Record<string, unknown> | null)?.summary as Record<string, unknown> | null)
            ?.total_skills ?? 0,
        ),
        ready: Number(
          ((payload.skills_dashboard as Record<string, unknown> | null)?.summary as Record<string, unknown> | null)
            ?.ready ?? 0,
        ),
        partial: Number(
          ((payload.skills_dashboard as Record<string, unknown> | null)?.summary as Record<string, unknown> | null)
            ?.partial ?? 0,
        ),
        blueprint: Number(
          ((payload.skills_dashboard as Record<string, unknown> | null)?.summary as Record<string, unknown> | null)
            ?.blueprint ?? 0,
        ),
        future: Number(
          ((payload.skills_dashboard as Record<string, unknown> | null)?.summary as Record<string, unknown> | null)
            ?.future ?? 0,
        ),
      },
      categories: Array.isArray((payload.skills_dashboard as Record<string, unknown> | null)?.categories)
        ? (((payload.skills_dashboard as Record<string, unknown>).categories as Record<string, unknown>[]).map((item) => ({
            key: String(item.key ?? 'creation') as SettingsControlCenterPayload['skillsDashboard']['categories'][number]['key'],
            label: String(item.label ?? ''),
            count: Number(item.count ?? 0),
          })))
        : [],
      items: Array.isArray((payload.skills_dashboard as Record<string, unknown> | null)?.items)
        ? (((payload.skills_dashboard as Record<string, unknown>).items as Record<string, unknown>[]).map((item) => ({
            key: String(item.key ?? ''),
            label: String(item.label ?? ''),
            category: String(item.category ?? 'creation') as SettingsControlCenterPayload['skillsDashboard']['items'][number]['category'],
            readiness: String(item.readiness ?? 'blueprint') as SettingsControlCenterPayload['skillsDashboard']['items'][number]['readiness'],
            summary: String(item.summary ?? ''),
            repoName: String(item.repo_name ?? ''),
            repoUrl: String(item.repo_url ?? ''),
            whyItMatters: String(item.why_it_matters ?? ''),
            amdProfile: String(item.amd_profile ?? ''),
            nextIntegrationStep: String(item.next_integration_step ?? ''),
            relatedModules: Array.isArray(item.related_modules) ? (item.related_modules as string[]) : [],
            blockers: Array.isArray(item.blockers) ? (item.blockers as string[]) : [],
            toolContracts: Array.isArray(item.tool_contracts)
              ? ((item.tool_contracts as Record<string, unknown>[]).map((tool) => ({
                  toolName: String(tool.tool_name ?? ''),
                  description: String(tool.description ?? ''),
                  schema: (tool.schema as Record<string, unknown> | null) ?? {},
                })))
              : [],
            learningNotePath: item.learning_note_path == null ? null : String(item.learning_note_path),
            statusReason: String(item.status_reason ?? ''),
          })))
        : [],
    },
    decisionLog: Array.isArray(payload.decision_log)
      ? payload.decision_log.map((item) => ({
          decisionId: Number((item as Record<string, unknown>).decision_id),
          conversationId:
            (item as Record<string, unknown>).conversation_id == null
              ? null
              : Number((item as Record<string, unknown>).conversation_id),
          projectId:
            (item as Record<string, unknown>).project_id == null
              ? null
              : Number((item as Record<string, unknown>).project_id),
          kind: String((item as Record<string, unknown>).kind ?? 'decision') as SettingsControlCenterPayload['decisionLog'][number]['kind'],
          summary: String((item as Record<string, unknown>).summary ?? ''),
          madeAt: String((item as Record<string, unknown>).made_at ?? ''),
          status: String((item as Record<string, unknown>).status ?? 'active') as SettingsControlCenterPayload['decisionLog'][number]['status'],
          dueAt:
            (item as Record<string, unknown>).due_at == null
              ? null
              : String((item as Record<string, unknown>).due_at),
          resolvedAt:
            (item as Record<string, unknown>).resolved_at == null
              ? null
              : String((item as Record<string, unknown>).resolved_at),
          resolutionSummary:
            (item as Record<string, unknown>).resolution_summary == null
              ? null
              : String((item as Record<string, unknown>).resolution_summary),
          evidenceMessageIds: Array.isArray((item as Record<string, unknown>).evidence_message_ids)
            ? ((item as Record<string, unknown>).evidence_message_ids as unknown[])
                .map((value) => Number(value))
                .filter((value) => Number.isFinite(value))
            : [],
        }))
      : [],
    localHost: normalizeLocalHostControlSummary(
      (payload.local_host as Record<string, unknown> | null) ?? null,
    ),
    remoteWorkers: Array.isArray(payload.remote_workers)
      ? payload.remote_workers.map((item) =>
          normalizeRemoteWorkerSummary(item as Record<string, unknown>),
        )
      : [],
    trendWatch: {
      enabled: Boolean((payload.trend_watch as Record<string, unknown> | null)?.enabled),
      query: String((payload.trend_watch as Record<string, unknown> | null)?.query ?? 'short video hooks'),
      region: String((payload.trend_watch as Record<string, unknown> | null)?.region ?? 'PL'),
      intervalMinutes: Number(
        (payload.trend_watch as Record<string, unknown> | null)?.interval_minutes ?? 180,
      ),
      lastRunAt:
        ((payload.trend_watch as Record<string, unknown> | null)?.last_run_at ?? null) == null
          ? null
          : String((payload.trend_watch as Record<string, unknown>).last_run_at),
      latestSnapshot:
        ((payload.trend_watch as Record<string, unknown> | null)?.latest_snapshot as Record<string, unknown> | null) ==
        null
          ? null
          : {
              snapshotId: Number(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .snapshot_id,
              ),
              projectId:
                (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .project_id ?? null) == null
                  ? null
                  : Number(
                      ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                        .project_id,
                    ),
              topicQuery: String(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .topic_query ?? '',
              ),
              regionCode: String(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .region_code ?? 'PL',
              ),
              status: String(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .status ?? 'unknown',
              ),
              sourceMix: Array.isArray(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .source_mix,
              )
                ? ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .source_mix as Record<string, unknown>[]))
                : [],
              topSignals: Array.isArray(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .top_signals,
              )
                ? ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .top_signals as Record<string, unknown>[]))
                : [],
              summary: {
                query: String(
                  ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.query ?? ''),
                ),
                regionCode: String(
                  ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.region_code ?? 'PL'),
                ),
                topKeywords: Array.isArray(
                  (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.top_keywords,
                )
                  ? (((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                      .summary as Record<string, unknown>).top_keywords as string[]))
                  : [],
                topHashtags: Array.isArray(
                  (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.top_hashtags,
                )
                  ? (((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                      .summary as Record<string, unknown>).top_hashtags as string[]))
                  : [],
                recommendedReelTypes: Array.isArray(
                  (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.recommended_reel_types,
                )
                  ? (((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                      .summary as Record<string, unknown>).recommended_reel_types as string[]))
                  : [],
                dominantSources: Array.isArray(
                  (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.dominant_sources,
                )
                  ? (((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                      .summary as Record<string, unknown>).dominant_sources as string[]))
                  : [],
                signalsCount: Number(
                  ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.signals_count ?? 0),
                ),
                intervalMinutes: Number(
                  ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.interval_minutes ?? 180),
                ),
                collectedAt:
                  ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .summary as Record<string, unknown> | null)?.collected_at ?? null) == null
                    ? null
                    : String(
                        (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                          .summary as Record<string, unknown>).collected_at,
                      ),
              },
              evaluationNotes: Array.isArray(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .evaluation_notes,
              )
                ? ((((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                    .evaluation_notes as string[]))
                : [],
              notePath:
                (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .note_path ?? null) == null
                  ? null
                  : String(
                      ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                        .note_path,
                    ),
              failureReason:
                (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .failure_reason ?? null) == null
                  ? null
                  : String(
                      ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                        .failure_reason,
                    ),
              collectedAt: String(
                ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .collected_at ?? '',
              ),
              expiresAt:
                (((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                  .expires_at ?? null) == null
                  ? null
                  : String(
                      ((payload.trend_watch as Record<string, unknown>).latest_snapshot as Record<string, unknown>)
                        .expires_at,
                    ),
            },
    },
    n8nYoutubeAutomation: normalizeSettingsN8nYoutubeAutomation(
      (payload.n8n_youtube_automation as Record<string, unknown> | null) ?? null,
    ),
    creativeAssetLanes: Array.isArray(payload.creative_asset_lanes)
      ? payload.creative_asset_lanes.map((item) => ({
          laneKey: String((item as Record<string, unknown>).lane_key ?? ''),
          displayName: String((item as Record<string, unknown>).display_name ?? ''),
          ready: Boolean((item as Record<string, unknown>).ready),
          status: String((item as Record<string, unknown>).status ?? 'unknown') as SettingsControlCenterPayload['creativeAssetLanes'][number]['status'],
          statusReason: String((item as Record<string, unknown>).status_reason ?? ''),
          lastRecordedStatus: String((item as Record<string, unknown>).last_recorded_status ?? ''),
          lastRecordedReason: String((item as Record<string, unknown>).last_recorded_reason ?? ''),
          lastRecordedAt:
            (item as Record<string, unknown>).last_recorded_at == null
              ? null
              : String((item as Record<string, unknown>).last_recorded_at),
        }))
      : [],
    creativeAssetLaneSummary: {
      lastRetestAt:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_retest_at == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_retest_at),
      lastOutputLaneKey:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_lane_key == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_lane_key),
      lastOutputDisplayName:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_display_name == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_display_name),
      lastOutputFilename:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_filename == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_filename),
      lastOutputStoragePath:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_storage_path == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_storage_path),
      lastOutputAbsolutePath:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_absolute_path == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_absolute_path),
      lastOutputWidth:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_width == null
          ? null
          : Number((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_width),
      lastOutputHeight:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_height == null
          ? null
          : Number((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_height),
      lastOutputSizeBytes:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_size_bytes == null
          ? null
          : Number((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_size_bytes),
      lastOutputAt:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_output_at == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_output_at),
      lastBlockerLaneKey:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_blocker_lane_key == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_blocker_lane_key),
      lastBlockerDisplayName:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_blocker_display_name == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_blocker_display_name),
      lastBlockerSummary:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_blocker_summary == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_blocker_summary),
      lastBlockerReason:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_blocker_reason == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_blocker_reason),
      lastBlockerScreenshotPath:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_blocker_screenshot_path == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_blocker_screenshot_path),
      lastBlockerAt:
        (payload.creative_asset_lane_summary as Record<string, unknown> | null)?.last_blocker_at == null
          ? null
          : String((payload.creative_asset_lane_summary as Record<string, unknown>).last_blocker_at),
    },
  }
}

function normalizeSettingsN8nYoutubeAutomation(
  item: Record<string, unknown> | null,
): SettingsControlCenterPayload['n8nYoutubeAutomation'] {
  if (!item) return null
  return {
    enabled: Boolean(item.enabled),
    baseUrl: String(item.base_url ?? ''),
    mode: String(item.mode ?? ''),
    dataDir: String(item.data_dir ?? ''),
    dockerStatus: String(item.docker_status ?? 'unknown'),
    dockerReady: Boolean(item.docker_ready),
    dockerAvailable: Boolean(item.docker_available),
    dockerMessage: String(item.docker_message ?? ''),
    virtualizationFirmwareEnabled:
      item.virtualization_firmware_enabled == null
        ? null
        : Boolean(item.virtualization_firmware_enabled),
    fallbackRunnerStatus: String(item.fallback_runner_status ?? 'unknown'),
    fallbackRunnerAvailable: Boolean(item.fallback_runner_available),
    fallbackRunnerPath: String(item.fallback_runner_path ?? ''),
    workflowTemplatesStatus: String(item.workflow_templates_status ?? 'unknown'),
    workflowTemplatesCount: Number(item.workflow_templates_count ?? 0),
    workflowTemplatesDir: String(item.workflow_templates_dir ?? ''),
    workflowTemplateErrors: Array.isArray(item.workflow_template_errors)
      ? (item.workflow_template_errors as string[]).map(String)
      : [],
  }
}

class ApiRequestError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
  }
}

function apiErrorMessage(value: unknown, depth = 0): string {
  if (depth > 4) return ''
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(item => apiErrorMessage(item, depth + 1)).filter(Boolean).join('; ')
  if (value && typeof value === 'object') {
    const error = value as Record<string, unknown>
    // Validation payloads can include submitted inputs; only show the message.
    return apiErrorMessage(error.detail ?? error.message ?? error.msg ?? error.error, depth + 1)
  }
  return ''
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const retryDelaysMs = [250, 800]
  const isFormData =
    typeof FormData !== 'undefined' && init?.body instanceof FormData
  const requestUrl = `${API_BASE_URL}${path}`
  let lastError: unknown = null

  for (let attempt = 0; attempt <= retryDelaysMs.length; attempt += 1) {
    try {
      const response = await fetch(requestUrl, {
        ...init,
        headers: isFormData
          ? (init?.headers ?? {})
          : {
              'Content-Type': 'application/json',
              ...(init?.headers ?? {}),
            },
      })
      if (!response.ok) {
        const detail = await response.text()
        let message = detail
        try {
          message = apiErrorMessage(JSON.parse(detail))
        } catch {
          message = detail
        }
        if (response.status === 404) {
          throw new ApiRequestError(
            `Endpoint API niedostępny (${path}). Uruchom ponownie backend Atnosa albo sprawdź, czy ta trasa jest zarejestrowana.`,
            response.status,
          )
        }
        throw new ApiRequestError(message || `Zapytanie do API nie powiodło się (${path}).`, response.status)
      }
      return (await response.json()) as T
    } catch (error) {
      lastError = error

      const requestAborted = init?.signal?.aborted === true
      const abortedByBrowser =
        typeof DOMException !== 'undefined' &&
        error instanceof DOMException &&
        error.name === 'AbortError'
      const transientNetworkFailure =
        error instanceof TypeError ||
        (error instanceof Error && /failed to fetch|networkerror|load failed/i.test(error.message))

      if (requestAborted || abortedByBrowser || !transientNetworkFailure || attempt === retryDelaysMs.length) {
        break
      }

      await new Promise((resolve) => globalThis.setTimeout(resolve, retryDelaysMs[attempt]))
    }
  }

  if (
    lastError instanceof TypeError ||
    (lastError instanceof Error && /failed to fetch|networkerror|load failed/i.test(lastError.message))
  ) {
    throw new Error('Nie udało się połączyć z lokalnym runtime Atnosa.')
  }

  throw (lastError instanceof Error ? lastError : new Error(`Request failed for ${path}`))
}

export async function fetchHealth(options?: { signal?: AbortSignal }): Promise<HealthPayload> {
  const response = await fetch(`${API_BASE_URL}/health`, {
    method: 'GET',
    cache: 'no-store',
    signal: options?.signal,
  })
  if (!response.ok) {
    throw new Error(`Health check failed with HTTP ${response.status}`)
  }
  const payload = (await response.json()) as Record<string, unknown>
  return {
    status: String(payload.status ?? 'unknown'),
    app: payload.app == null ? undefined : String(payload.app),
    version: payload.version == null ? undefined : String(payload.version),
    environment: payload.environment == null ? undefined : String(payload.environment),
    deploymentMode:
      payload.deployment_mode == null ? undefined : String(payload.deployment_mode),
  }
}

function normalizeRuntimeHealth(payload: Record<string, unknown>): RuntimeHealthPayload {
  const summary = (payload.summary as Record<string, unknown> | null) ?? {}
  const launcher = (payload.launcher as Record<string, unknown> | null) ?? null
  const readiness = (payload.readiness as Record<string, unknown> | null) ?? null
  return {
    status: String(payload.status ?? 'unknown'),
    summary: {
      ready: Number(summary.ready ?? 0),
      degraded: Number(summary.degraded ?? 0),
      missing: Number(summary.missing ?? 0),
      blocked: Number(summary.blocked ?? 0),
    },
    readiness: readiness
      ? {
          verdict: String(readiness.verdict ?? 'unknown'),
          canRunCore: Boolean(readiness.can_run_core),
          headline: String(readiness.headline ?? ''),
          firstAction: String(readiness.first_action ?? ''),
          blockingComponents: Array.isArray(readiness.blocking_components)
            ? readiness.blocking_components.map((item) => String(item))
            : [],
          degradedComponents: Array.isArray(readiness.degraded_components)
            ? readiness.degraded_components.map((item) => String(item))
            : [],
          evidence: (readiness.evidence as Record<string, unknown> | null) ?? {},
        }
      : undefined,
    components: Array.isArray(payload.components)
      ? payload.components.map((item) => {
          const component = (item as Record<string, unknown>) ?? {}
          return {
            key: String(component.key ?? ''),
            label: String(component.label ?? ''),
            status: String(component.status ?? 'unknown'),
            verdict: component.verdict == null ? undefined : String(component.verdict),
            detail: String(component.detail ?? ''),
            nextAction: String(component.next_action ?? ''),
            firstAction: component.first_action == null ? undefined : String(component.first_action),
            evidence: (component.evidence as Record<string, unknown> | null) ?? undefined,
            metadata: (component.metadata as Record<string, unknown> | null) ?? {},
          }
        })
      : [],
    launcher: launcher
      ? {
          scriptPath: String(launcher.script_path ?? ''),
          exists: Boolean(launcher.exists),
          command: String(launcher.command ?? ''),
          logDir: String(launcher.log_dir ?? ''),
        }
      : null,
  }
}

function normalizeAiWorkerStatus(payload: Record<string, unknown>): AiWorkerStatus {
  return {
    workerKey: String(payload.worker_key ?? ''),
    label: String(payload.label ?? ''),
    status: String(payload.status ?? 'unknown'),
    ready: Boolean(payload.ready),
    reason: String(payload.reason ?? ''),
    capabilities: Array.isArray(payload.capabilities) ? payload.capabilities.map((item) => String(item)) : [],
    allowedTaskTypes: Array.isArray(payload.allowed_task_types)
      ? payload.allowed_task_types.map((item) => String(item))
      : [],
    requiresBrowser: Boolean(payload.requires_browser),
    safetyMode: String(payload.safety_mode ?? 'supervised_high_trust'),
    metadata: (payload.metadata as Record<string, unknown> | null) ?? {},
  }
}

function normalizeQualityCouncilProblem(payload: Record<string, unknown>): QualityCouncilProblem {
  return {
    severity: String(payload.severity ?? 'low'),
    title: String(payload.title ?? ''),
    cause: String(payload.cause ?? ''),
    suggestedFix: String(payload.suggested_fix ?? ''),
    files: Array.isArray(payload.files) ? payload.files.map((item) => String(item)) : [],
    verification: String(payload.verification ?? ''),
    regressionRisk: String(payload.regression_risk ?? ''),
  }
}

function normalizeQualityCouncilReport(payload: Record<string, unknown>): QualityCouncilReport {
  return {
    runId: String(payload.run_id ?? ''),
    key: String(payload.key ?? ''),
    title: String(payload.title ?? ''),
    createdAt: String(payload.created_at ?? ''),
    summary: String(payload.summary ?? ''),
    worksWell: Array.isArray(payload.works_well) ? payload.works_well.map((item) => String(item)) : [],
    worksWrong: Array.isArray(payload.works_wrong) ? payload.works_wrong.map((item) => String(item)) : [],
    notWorking: Array.isArray(payload.not_working) ? payload.not_working.map((item) => String(item)) : [],
    notVerified: Array.isArray(payload.not_verified) ? payload.not_verified.map((item) => String(item)) : [],
    confidence: String(payload.confidence ?? 'unknown'),
    problems: Array.isArray(payload.problems)
      ? payload.problems.map((item) => normalizeQualityCouncilProblem((item as Record<string, unknown>) ?? {}))
      : [],
    markdownPath: payload.markdown_path == null ? null : String(payload.markdown_path),
    jsonPath: payload.json_path == null ? null : String(payload.json_path),
  }
}

function normalizeQualityCouncilRun(payload: Record<string, unknown>): QualityCouncilRun {
  return {
    runId: String(payload.run_id ?? ''),
    createdAt: String(payload.created_at ?? ''),
    runDir: String(payload.run_dir ?? ''),
    reports: Array.isArray(payload.reports)
      ? payload.reports.map((item) => normalizeQualityCouncilReport((item as Record<string, unknown>) ?? {}))
      : [],
    coordinator: normalizeQualityCouncilReport((payload.coordinator as Record<string, unknown>) ?? {}),
    delivery: (() => {
      const delivery = (payload.delivery as Record<string, unknown> | null) ?? {}
      return {
        status: String(delivery.status ?? 'unknown'),
        reason: String(delivery.reason ?? ''),
        reportPath: delivery.report_path == null ? null : String(delivery.report_path),
        sent: Boolean(delivery.sent),
        oauthStatus: delivery.oauth_status == null ? undefined : String(delivery.oauth_status),
      }
    })(),
  }
}

function normalizeAiWorkerTask(payload: Record<string, unknown>): AiWorkerTask {
  return {
    id: Number(payload.id ?? 0),
    taskType: String(payload.task_type ?? ''),
    status: String(payload.status ?? ''),
    priority: String(payload.priority ?? 'normal'),
    requestedBy: String(payload.requested_by ?? ''),
    workerKey: payload.worker_key == null ? null : String(payload.worker_key),
    privacyMode: String(payload.privacy_mode ?? ''),
    parentTaskId: payload.parent_task_id == null ? null : Number(payload.parent_task_id),
    taskGroup: payload.task_group == null ? null : String(payload.task_group),
    taskRole: String(payload.task_role ?? 'single'),
    sequenceIndex: Number(payload.sequence_index ?? 0),
    requiresReview: Boolean(payload.requires_review),
    verificationStatus: String(payload.verification_status ?? 'unverified'),
    inputSummary: payload.input_summary == null ? null : String(payload.input_summary),
    redactedPrompt: String(payload.redacted_prompt ?? ''),
    payloadJson: (payload.payload_json as Record<string, unknown> | null) ?? {},
    resultSummary: payload.result_summary == null ? null : String(payload.result_summary),
    resultJson: (payload.result_json as Record<string, unknown> | null) ?? null,
    errorMessage: payload.error_message == null ? null : String(payload.error_message),
    blockedReason: payload.blocked_reason == null ? null : String(payload.blocked_reason),
    approvalRequired: Boolean(payload.approval_required),
    traceId: payload.trace_id == null ? null : Number(payload.trace_id),
    createdAt: payload.created_at == null ? null : String(payload.created_at),
    updatedAt: payload.updated_at == null ? null : String(payload.updated_at),
    startedAt: payload.started_at == null ? null : String(payload.started_at),
    completedAt: payload.completed_at == null ? null : String(payload.completed_at),
    runs: Array.isArray(payload.runs)
      ? payload.runs.map((item) => {
          const run = (item as Record<string, unknown>) ?? {}
          return {
            id: Number(run.id ?? 0),
            workerKey: String(run.worker_key ?? ''),
            status: String(run.status ?? ''),
            durationMs: run.duration_ms == null ? null : Number(run.duration_ms),
            resultSummary: run.result_summary == null ? null : String(run.result_summary),
            errorMessage: run.error_message == null ? null : String(run.error_message),
            startedAt: run.started_at == null ? null : String(run.started_at),
            completedAt: run.completed_at == null ? null : String(run.completed_at),
          }
        })
      : [],
    children: Array.isArray(payload.children)
      ? payload.children.map((item) => {
          const child = (item as Record<string, unknown>) ?? {}
          return {
            id: Number(child.id ?? 0),
            taskType: String(child.task_type ?? ''),
            status: String(child.status ?? ''),
            workerKey: child.worker_key == null ? null : String(child.worker_key),
            taskRole: String(child.task_role ?? 'single'),
            sequenceIndex: Number(child.sequence_index ?? 0),
            requiresReview: Boolean(child.requires_review),
            verificationStatus: String(child.verification_status ?? 'unverified'),
            inputSummary: child.input_summary == null ? null : String(child.input_summary),
            blockedReason: child.blocked_reason == null ? null : String(child.blocked_reason),
            resultSummary: child.result_summary == null ? null : String(child.result_summary),
          }
        })
      : [],
  }
}

export async function fetchRuntimeHealth(): Promise<RuntimeHealthPayload> {
  return normalizeRuntimeHealth(await fetchJson<Record<string, unknown>>('/api/runtime/health'))
}

export async function fetchRuntimeInventory(): Promise<RuntimeInventoryPayload> {
  return fetchJson<RuntimeInventoryPayload>('/settings/runtime-inventory?include_live_health=true')
}

export async function fetchProjectCommandOverview(
  projectId: number,
  knowledgeQuery?: string,
): Promise<ProjectCommandOverview> {
  const params = new URLSearchParams()
  if (knowledgeQuery?.trim()) params.set('knowledge_query', knowledgeQuery.trim())
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return fetchJson<ProjectCommandOverview>(`/api/project-command/${projectId}/overview${suffix}`)
}

export async function verifyYouTubeChannelIdentityLive(
  projectId: number,
): Promise<ProjectYouTubeChannelIdentitySnapshot> {
  return fetchJson<ProjectYouTubeChannelIdentitySnapshot>(
    `/api/youtube-channel-identities/projects/${projectId}/identity/live-verify`,
    { method: 'POST' },
  )
}

export interface YouTubeChannelIdentityInput {
  accountBindingKey: string
  youtubeChannelId?: string | null
  channelHandle?: string | null
  channelUrl?: string | null
  displayName?: string | null
  ownerLabel?: string | null
  languageCode?: string | null
  targetMarket?: string | null
  niche?: string | null
  oauthConnectionId?: number | null
}

export interface OAuthConnectionSummary {
  id: number
  connectorKey: string
  displayName: string
  status: string
  accountId: number | null
  connectedAt: string | null
  metadata: Record<string, unknown>
}

export async function fetchOAuthConnections(): Promise<OAuthConnectionSummary[]> {
  const payload = await fetchJson<Array<Record<string, unknown>>>('/oauth/connections')
  return payload.map((connection) => ({
    id: Number(connection.id ?? 0),
    connectorKey: String(connection.connector_key ?? ''),
    displayName: String(connection.display_name ?? ''),
    status: String(connection.status ?? ''),
    accountId: connection.account_id == null ? null : Number(connection.account_id),
    connectedAt: connection.connected_at == null ? null : String(connection.connected_at),
    metadata: (connection.metadata as Record<string, unknown> | undefined) ?? {},
  }))
}

export async function saveYouTubeChannelIdentity(
  projectId: number,
  input: YouTubeChannelIdentityInput,
): Promise<ProjectYouTubeChannelIdentitySnapshot> {
  return fetchJson<ProjectYouTubeChannelIdentitySnapshot>(
    `/api/youtube-channel-identities/projects/${projectId}/identity`,
    {
      method: 'PUT',
      body: JSON.stringify({
        account_binding_key: input.accountBindingKey,
        youtube_channel_id: input.youtubeChannelId?.trim() || null,
        channel_handle: input.channelHandle?.trim() || null,
        channel_url: input.channelUrl?.trim() || null,
        display_name: input.displayName?.trim() || null,
        owner_label: input.ownerLabel?.trim() || null,
        language_code: input.languageCode?.trim() || null,
        target_market: input.targetMarket?.trim() || null,
        niche: input.niche?.trim() || null,
        oauth_connection_id: input.oauthConnectionId ?? null,
      }),
    },
  )
}

export async function confirmYouTubeChannelIdentity(
  projectId: number,
  channelId: string,
): Promise<ProjectYouTubeChannelIdentitySnapshot> {
  return fetchJson<ProjectYouTubeChannelIdentitySnapshot>(
    `/api/youtube-channel-identities/projects/${projectId}/identity/confirm`,
    { method: 'POST', body: JSON.stringify({ confirmed_channel_id: channelId }) },
  )
}

export async function saveYouTubeChannelManifest(
  projectId: number,
  manifest: Record<string, unknown>,
): Promise<ProjectYouTubeChannelIdentitySnapshot> {
  return fetchJson<ProjectYouTubeChannelIdentitySnapshot>(
    `/api/youtube-channel-identities/projects/${projectId}/manifest`,
    { method: 'PUT', body: JSON.stringify({ manifest }) },
  )
}

export async function approveYouTubeChannelManifest(
  projectId: number,
): Promise<ProjectYouTubeChannelIdentitySnapshot> {
  return fetchJson<ProjectYouTubeChannelIdentitySnapshot>(
    `/api/youtube-channel-identities/projects/${projectId}/manifest/approve`,
    { method: 'POST' },
  )
}

export async function saveYouTubePublishingPolicy(
  projectId: number,
  policy: Record<string, unknown>,
): Promise<ProjectYouTubeChannelIdentitySnapshot> {
  return fetchJson<ProjectYouTubeChannelIdentitySnapshot>(
    `/api/youtube-channel-identities/projects/${projectId}/publishing-policy`,
    { method: 'PUT', body: JSON.stringify({ policy }) },
  )
}

export async function approveYouTubePublishingPolicy(
  projectId: number,
): Promise<ProjectYouTubeChannelIdentitySnapshot> {
  return fetchJson<ProjectYouTubeChannelIdentitySnapshot>(
    `/api/youtube-channel-identities/projects/${projectId}/publishing-policy/approve`,
    { method: 'POST' },
  )
}

export async function executeProjectCommand(payload: {
  projectId: number
  content: string
  conversationId?: number | null
  autoDelegate?: boolean
  privacyMode?: 'strict_local' | 'allow_browser_ai' | 'allow_cloud_api'
}): Promise<ProjectCommandResult> {
  return fetchJson<ProjectCommandResult>(`/api/project-command/${payload.projectId}/execute`, {
    method: 'POST',
    body: JSON.stringify({
      content: payload.content,
      conversation_id: payload.conversationId ?? null,
      auto_delegate: payload.autoDelegate ?? false,
      privacy_mode: payload.privacyMode ?? 'strict_local',
    }),
  })
}

export async function fetchAiWorkersStatus(): Promise<AiWorkersStatusPayload> {
  const payload = await fetchJson<Record<string, unknown>>('/api/ai-workers/status')
  return {
    mode: String(payload.mode ?? 'supervised_high_trust'),
    workers: Array.isArray(payload.workers)
      ? payload.workers.map((item) => normalizeAiWorkerStatus((item as Record<string, unknown>) ?? {}))
      : [],
    guardrails: (payload.guardrails as Record<string, unknown> | null) ?? {},
  }
}

export async function fetchQualityCouncilStatus(): Promise<QualityCouncilStatusPayload> {
  const payload = await fetchJson<Record<string, unknown>>('/api/quality-council/status')
  const gmailDelivery = (payload.gmail_delivery as Record<string, unknown> | null) ?? {}
  return {
    ready: Boolean(payload.ready),
    reportsDir: String(payload.reports_dir ?? ''),
    latestRun: payload.latest_run == null ? null : normalizeQualityCouncilRun(payload.latest_run as Record<string, unknown>),
    gmailDelivery: {
      ready: Boolean(gmailDelivery.ready),
      oauthStatus: String(gmailDelivery.oauth_status ?? 'unknown'),
      reason: String(gmailDelivery.reason ?? ''),
    },
  }
}

export async function fetchQualityCouncilReports(limit = 8): Promise<QualityCouncilRun[]> {
  const payload = await fetchJson<Record<string, unknown>>(`/api/quality-council/reports?limit=${Math.max(1, Math.min(limit, 50))}`)
  return Array.isArray(payload.runs)
    ? payload.runs.map((item) => normalizeQualityCouncilRun((item as Record<string, unknown>) ?? {}))
    : []
}

export async function runQualityCouncil(payload: {
  audits?: Array<'application_ui' | 'video_quality' | 'investment_strategy' | 'security_permissions' | 'claude_design' | 'kimi_models'>
  includeExternalHandoffs?: boolean
  attemptGmailDelivery?: boolean
} = {}): Promise<QualityCouncilRun> {
  return normalizeQualityCouncilRun(
    await fetchJson<Record<string, unknown>>('/api/quality-council/run', {
      method: 'POST',
      body: JSON.stringify({
        audits: payload.audits,
        include_external_handoffs: payload.includeExternalHandoffs ?? true,
        attempt_gmail_delivery: payload.attemptGmailDelivery ?? true,
      }),
    }),
  )
}

export async function fetchAiWorkerTasks(payload: {
  status?: string | null
  parentId?: number | null
  rootsOnly?: boolean
  limit?: number
} = {}): Promise<AiWorkerTask[]> {
  const params = new URLSearchParams()
  if (payload.status) params.set('status', payload.status)
  if (payload.parentId != null) params.set('parent_id', String(payload.parentId))
  if (payload.rootsOnly) params.set('roots_only', 'true')
  if (payload.limit) params.set('limit', String(payload.limit))
  const suffix = params.toString() ? `?${params.toString()}` : ''
  const response = await fetchJson<Record<string, unknown>>(`/api/ai-workers/tasks${suffix}`)
  return Array.isArray(response.tasks)
    ? response.tasks.map((item) => normalizeAiWorkerTask((item as Record<string, unknown>) ?? {}))
    : []
}

export async function delegateAiWorkerPlan(payload: {
  taskType: string
  prompt: string
  context?: Record<string, unknown>
  projectId?: number | null
  preferredWorker?: string | null
  allowedWorkers?: string[]
  privacyMode?: 'strict_local' | 'allow_browser_ai' | 'allow_cloud_api'
  priority?: 'low' | 'normal' | 'high'
  requiresApprovalForResult?: boolean
  maxSteps?: number
}): Promise<AiWorkerTask> {
  const response = await fetchJson<Record<string, unknown>>('/api/ai-workers/delegate-plan', {
    method: 'POST',
    body: JSON.stringify({
      task_type: payload.taskType,
      prompt: payload.prompt,
      context: payload.context ?? {},
      project_id: payload.projectId ?? null,
      preferred_worker: payload.preferredWorker ?? null,
      allowed_workers: payload.allowedWorkers ?? [],
      privacy_mode: payload.privacyMode ?? 'strict_local',
      priority: payload.priority ?? 'normal',
      requires_approval_for_result: payload.requiresApprovalForResult ?? false,
      max_steps: payload.maxSteps ?? 6,
    }),
  })
  return normalizeAiWorkerTask(response)
}

export async function delegateAiWorkerTask(payload: {
  taskType: string
  prompt: string
  context?: Record<string, unknown>
  projectId?: number | null
  preferredWorker?: string | null
  allowedWorkers?: string[]
  privacyMode?: 'strict_local' | 'allow_browser_ai' | 'allow_cloud_api'
  priority?: 'low' | 'normal' | 'high'
  requiresApprovalForResult?: boolean
}): Promise<AiWorkerTask> {
  const response = await fetchJson<Record<string, unknown>>('/api/ai-workers/delegate', {
    method: 'POST',
    body: JSON.stringify({
      task_type: payload.taskType,
      prompt: payload.prompt,
      context: payload.context ?? {},
      project_id: payload.projectId ?? null,
      preferred_worker: payload.preferredWorker ?? null,
      allowed_workers: payload.allowedWorkers ?? [],
      privacy_mode: payload.privacyMode ?? 'strict_local',
      priority: payload.priority ?? 'normal',
      requires_approval_for_result: payload.requiresApprovalForResult ?? false,
    }),
  })
  return normalizeAiWorkerTask(response)
}

export async function probeAiWorker(workerKey: string): Promise<AiWorkerStatus> {
  const response = await fetchJson<Record<string, unknown>>(`/api/ai-workers/probe/${encodeURIComponent(workerKey)}`, {
    method: 'POST',
  })
  return normalizeAiWorkerStatus(response)
}

export async function cancelAiWorkerTask(taskId: number): Promise<AiWorkerTask> {
  return normalizeAiWorkerTask(
    await fetchJson<Record<string, unknown>>(`/api/ai-workers/tasks/${taskId}/cancel`, {
      method: 'PATCH',
    }),
  )
}

export async function runAiWorkerTask(taskId: number): Promise<AiWorkerTask> {
  return normalizeAiWorkerTask(
    await fetchJson<Record<string, unknown>>(`/api/ai-workers/tasks/${taskId}/run`, {
      method: 'POST',
    }),
  )
}

export async function runAiWorkerSafePlan(taskId: number): Promise<AiWorkerTask> {
  return normalizeAiWorkerTask(
    await fetchJson<Record<string, unknown>>(`/api/ai-workers/tasks/${taskId}/run-safe-plan`, {
      method: 'POST',
    }),
  )
}

export async function fetchIntelligenceSignals(niche?: string): Promise<IntelligenceYoutubeSignal[]> {
  const query = niche ? `?niche=${encodeURIComponent(niche)}` : ''
  return fetchJson<IntelligenceYoutubeSignal[]>(`/api/intelligence/signals${query}`)
}

export async function fetchChannelConfig(): Promise<ChannelConfigResponse> {
  return fetchJson<ChannelConfigResponse>('/api/channel/config')
}

export async function saveChannelConfig(payload: ChannelConfig): Promise<ChannelConfigResponse> {
  return fetchJson<ChannelConfigResponse>('/api/channel/config', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

function normalizeLocalHostScreenArtifact(payload: Record<string, unknown>): LocalHostScreenArtifact {
  return {
    kind: String(payload.kind ?? ''),
    path: String(payload.path ?? ''),
    metadataPath: String(payload.metadata_path ?? ''),
    label: String(payload.label ?? ''),
    createdAt: String(payload.created_at ?? ''),
    durationSeconds: payload.duration_seconds == null ? null : Number(payload.duration_seconds),
  }
}

export async function captureLocalHostScreen(label = 'manual-access-check'): Promise<LocalHostScreenArtifact> {
  const payload = await fetchJson<Record<string, unknown>>('/local-host/screen/screenshot', {
    method: 'POST',
    body: JSON.stringify({ label }),
  })
  return normalizeLocalHostScreenArtifact(payload)
}

export async function recordLocalHostScreen(
  label = 'manual-access-check',
  durationSeconds = 5,
): Promise<LocalHostScreenArtifact> {
  const payload = await fetchJson<Record<string, unknown>>('/local-host/screen/record', {
    method: 'POST',
    body: JSON.stringify({ label, duration_seconds: durationSeconds }),
  })
  return normalizeLocalHostScreenArtifact(payload)
}

export async function fetchIntelligenceGoogleTrends(): Promise<IntelligenceGoogleTrendSignal[]> {
  return fetchJson<IntelligenceGoogleTrendSignal[]>('/api/intelligence/gtrends/signals')
}

export async function fetchIntelligenceCompetitors(): Promise<{
  channels: IntelligenceCompetitorChannel[]
  videos: IntelligenceCompetitorVideo[]
}> {
  return fetchJson<{ channels: IntelligenceCompetitorChannel[]; videos: IntelligenceCompetitorVideo[] }>(
    '/api/intelligence/competitors',
  )
}

export async function fetchIntelligenceOwnPerformance(): Promise<IntelligenceOwnPerformance[]> {
  return fetchJson<IntelligenceOwnPerformance[]>('/api/intelligence/own/performance')
}

export async function fetchIntelligenceReferenceChannels(): Promise<IntelligenceReferenceChannel[]> {
  return fetchJson<IntelligenceReferenceChannel[]>('/api/intelligence/reference')
}

export async function runIntelligenceCollectors(payload: IntelligenceRunPayload): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/api/intelligence/run', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      niches: payload.niches,
      competitor_urls: payload.competitorUrls,
      reference_urls: payload.referenceUrls,
      region: payload.region ?? 'PL',
    }),
  })
}

export async function fetchIntelligenceAccounts(): Promise<IntelligenceAccountConfig[]> {
  const payload = await fetchJson<{ accounts: IntelligenceAccountConfig[] }>('/api/intelligence/accounts')
  return payload.accounts
}

export async function configureIntelligenceAccount(payload: {
  accountId: string
  niches: string[]
  region?: string
  timeframe?: string
  channelId?: string
  channelTitle?: string
  contentProfile?: string
  automationMode?: IntelligenceAutomationMode
  refreshNow?: boolean
}): Promise<IntelligenceAccountConfig> {
  const response = await fetchJson<{ account: IntelligenceAccountConfig }>('/api/intelligence/accounts', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      niches: payload.niches,
      region: payload.region ?? 'PL',
      timeframe: payload.timeframe ?? 'now 7-d',
      channel_id: payload.channelId,
      channel_title: payload.channelTitle,
      content_profile: payload.contentProfile,
      automation_mode: payload.automationMode,
      refresh_now: payload.refreshNow ?? false,
    }),
  })
  return response.account
}

export async function fetchProductionReadiness(live = false): Promise<ProductionReadinessPayload> {
  return fetchJson<ProductionReadinessPayload>(`/api/production/readiness?live=${live ? 'true' : 'false'}`)
}

export async function fetchMediaObservability(options?: {
  historyHours?: number
  maxJobs?: number
  simulate?: string
}): Promise<MediaObservabilityPayload> {
  const params = new URLSearchParams()
  if (options?.historyHours != null) params.set('history_hours', String(options.historyHours))
  if (options?.maxJobs != null) params.set('max_jobs', String(options.maxJobs))
  if (options?.simulate) params.set('simulate', options.simulate)
  const suffix = params.toString() ? `?${params.toString()}` : ''
  return fetchJson<MediaObservabilityPayload>(`/api/production/media-observability${suffix}`)
}

export async function fetchReferenceLearningStatus(): Promise<ReferenceLearningStatus> {
  return fetchJson<ReferenceLearningStatus>('/api/production/reference-learning/status')
}

export async function startReferenceLearning(): Promise<ReferenceLearningStatus> {
  return fetchJson<ReferenceLearningStatus>('/api/production/reference-learning/start', {
    method: 'POST',
    body: JSON.stringify({ profile_key: 'wealth_blueprint', target_count: 40 }),
  })
}

export async function fetchProjectReadiness(): Promise<ProjectReadinessPayload> {
  return fetchJson<ProjectReadinessPayload>('/api/projects/readiness')
}

export async function fetchSmartSpendingStatus(): Promise<SmartSpendingStatus> {
  return fetchJson<SmartSpendingStatus>('/api/procurement/smart-spending/status')
}

export async function fetchProcurementRequests(payload: {
  status?: string
  limit?: number
} = {}): Promise<ProcurementRequestItem[]> {
  const query = new URLSearchParams()
  if (payload.status) query.set('status', payload.status)
  if (payload.limit) query.set('limit', String(payload.limit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<ProcurementRequestItem[]>(`/api/procurement/requests${suffix}`)
}

export async function fetchExpenseNotifications(payload: {
  deliveryStatus?: string
  limit?: number
} = {}): Promise<ExpenseNotificationItem[]> {
  const query = new URLSearchParams()
  if (payload.deliveryStatus) query.set('delivery_status', payload.deliveryStatus)
  if (payload.limit) query.set('limit', String(payload.limit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<ExpenseNotificationItem[]>(`/api/procurement/expense-notifications${suffix}`)
}

export async function fetchInvestmentCockpit(): Promise<InvestmentCockpitStatus> {
  return fetchJson<InvestmentCockpitStatus>('/api/investment/cockpit')
}

export async function fetchInvestmentEducationSources(): Promise<InvestmentEducationSourcesPayload> {
  return fetchJson<InvestmentEducationSourcesPayload>('/api/investment/education/sources')
}

export async function bootstrapInvestmentEducationSources(): Promise<InvestmentEducationBootstrapResult> {
  return fetchJson<InvestmentEducationBootstrapResult>('/api/investment/education/bootstrap', {
    method: 'POST',
  })
}

export async function fetchInvestmentEducationReports(): Promise<InvestmentEducationReportsPayload> {
  return fetchJson<InvestmentEducationReportsPayload>('/api/investment/education/reports')
}

export async function runInvestmentEducationResearch(
  payload: { maxSources?: number } = {},
): Promise<InvestmentEducationResearchResult> {
  return fetchJson<InvestmentEducationResearchResult>('/api/investment/education/research', {
    method: 'POST',
    body: JSON.stringify({ max_sources: payload.maxSources ?? 4 }),
  })
}

export async function fetchPortfolioAlphaSettings(): Promise<PortfolioAlphaSettings> {
  return fetchJson<PortfolioAlphaSettings>('/api/investment/settings/portfolio-alpha')
}

export async function updatePortfolioAlphaSettings(
  payload: PortfolioAlphaSettingsPayload,
): Promise<PortfolioAlphaSettings> {
  return fetchJson<PortfolioAlphaSettings>('/api/investment/settings/portfolio-alpha', {
    method: 'PUT',
    body: JSON.stringify({
      broker_name: payload.brokerName ?? 'XTB Demo',
      broker_portal_url: payload.brokerPortalUrl ?? 'https://xstation5.xtb.com/',
      broker_account_identifier: payload.brokerAccountIdentifier ?? '',
      credential_vault_ref: payload.credentialVaultRef ?? '',
      broker_read_access: payload.brokerReadAccess ?? 'not_configured',
      account_mode: payload.accountMode ?? 'demo_paper',
      market_data_sources: payload.marketDataSources ?? [],
      learning_resources: payload.learningResources ?? [],
      monthly_spend_budget_pln: payload.monthlySpendBudgetPln ?? 0,
      payment_method_ref: payload.paymentMethodRef ?? '',
      payment_method_configured: payload.paymentMethodConfigured ?? false,
      real_trading_blocked: payload.realTradingBlocked ?? true,
      trade_approval_rules_enabled: payload.tradeApprovalRulesEnabled ?? false,
    }),
  })
}

export async function fetchInvestmentRiskPolicy(): Promise<InvestmentRiskPolicyResponse> {
  return fetchJson<InvestmentRiskPolicyResponse>('/api/investment/risk-policy')
}

export async function updateInvestmentRiskPolicy(
  payload: InvestmentRiskPolicyPayload,
): Promise<InvestmentRiskPolicyResponse> {
  return fetchJson<InvestmentRiskPolicyResponse>('/api/investment/risk-policy', {
    method: 'PUT',
    body: JSON.stringify({
      max_single_paper_trade_pln: payload.maxSinglePaperTradePln,
      max_paper_position_pln: payload.maxPaperPositionPln,
      max_daily_realized_loss_pln: payload.maxDailyRealizedLossPln,
      max_weekly_realized_loss_pln: payload.maxWeeklyRealizedLossPln,
      paper_only: true,
      emergency_stop: payload.emergencyStop,
    }),
  })
}

export async function fetchXtbMarketDataStatus(): Promise<XtbMarketDataStatus> {
  return fetchJson<XtbMarketDataStatus>('/api/investment/market-data/xtb-demo/status')
}

export async function configureXtbDemoAccount(payload: {
  accountIdentifier: string
  credentialAccountId?: number | null
}): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/api/investment/brokers/xtb-demo', {
    method: 'POST',
    body: JSON.stringify({
      account_identifier: payload.accountIdentifier,
      credential_account_id: payload.credentialAccountId ?? null,
    }),
  })
}

export async function fetchAccountSecretStorageStatus(): Promise<AccountSecretStorageStatus> {
  const payload = await fetchJson<Record<string, unknown>>('/accounts/storage-status')
  return {
    storageBackend: String(payload.storage_backend ?? ''),
    isSecretStorageEnabled: Boolean(payload.is_secret_storage_enabled),
    summary: String(payload.summary ?? ''),
    warnings: Array.isArray(payload.warnings) ? payload.warnings.map((item) => String(item)) : [],
  }
}

export async function verifyXtbReadOnly(payload: {
  symbols?: string[]
  timeoutSeconds?: number
} = {}): Promise<XtbReadOnlyVerifyResult> {
  return fetchJson<XtbReadOnlyVerifyResult>('/api/investment/market-data/xtb-demo/verify-read-only', {
    method: 'POST',
    body: JSON.stringify({
      symbols: payload.symbols ?? ['US500', 'EURUSD'],
      timeout_s: payload.timeoutSeconds ?? 10,
    }),
  })
}

export async function fetchXtbQuotes(symbols: string[] = ['US500']): Promise<XtbQuotesResult> {
  const query = new URLSearchParams()
  query.set('symbols', symbols.join(','))
  return fetchJson<XtbQuotesResult>(`/api/investment/market-data/xtb-demo/quotes?${query.toString()}`)
}

export async function fetchAlpacaPaperStatus(): Promise<AlpacaPaperStatus> {
  return fetchJson<AlpacaPaperStatus>('/api/investment/market-data/alpaca-paper/status')
}

export async function configureAlpacaPaperAccount(payload: {
  credentialAccountId: number
}): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/api/investment/brokers/alpaca-paper', {
    method: 'POST',
    body: JSON.stringify({
      credential_account_id: payload.credentialAccountId,
    }),
  })
}

export async function verifyAlpacaPaperReadOnly(payload: {
  symbols?: string[]
  timeoutSeconds?: number
} = {}): Promise<AlpacaReadOnlyVerifyResult> {
  return fetchJson<AlpacaReadOnlyVerifyResult>('/api/investment/market-data/alpaca-paper/verify-read-only', {
    method: 'POST',
    body: JSON.stringify({
      symbols: payload.symbols ?? ['SPY', 'QQQ'],
      timeout_s: payload.timeoutSeconds ?? 10,
    }),
  })
}

export async function addInvestmentWatchlistItem(
  payload: InvestmentWatchlistPayload,
): Promise<InvestmentWatchlistItem> {
  return fetchJson<InvestmentWatchlistItem>('/api/investment/watchlist', {
    method: 'POST',
    body: JSON.stringify({
      symbol: payload.symbol,
      name: payload.name ?? null,
      asset_type: payload.assetType ?? 'stock',
      thesis: payload.thesis ?? '',
      risk_notes: payload.riskNotes ?? '',
    }),
  })
}

export async function recordInvestmentPaperTrade(
  payload: InvestmentPaperTradePayload,
): Promise<InvestmentPaperTrade> {
  return fetchJson<InvestmentPaperTrade>('/api/investment/paper-trades', {
    method: 'POST',
    body: JSON.stringify({
      symbol: payload.symbol,
      side: payload.side,
      quantity: payload.quantity,
      price_pln: payload.pricePln,
      reason: payload.reason,
    }),
  })
}

export async function runInvestmentPaperAutopilot(
  payload: InvestmentAutopilotRunPayload = {},
): Promise<InvestmentStrategyRun> {
  return fetchJson<InvestmentStrategyRun>('/api/investment/autopilot/run-once', {
    method: 'POST',
    body: JSON.stringify({
      strategy_id: payload.strategyId ?? 'frontend_operator_run_v1',
      max_trades: payload.maxTrades ?? 3,
    }),
  })
}

export async function fetchCreatorCockpit(): Promise<CreatorCockpitStatus> {
  return fetchJson<CreatorCockpitStatus>('/api/creator-cockpit/status')
}

export async function ensureDefaultCreatorCockpit(): Promise<CreatorCockpitStatus> {
  return fetchJson<CreatorCockpitStatus>('/api/creator-cockpit/ensure-default', {
    method: 'POST',
  })
}

export async function seedCreatorCockpitWorkItems(): Promise<CreatorCockpitSeedResult> {
  return fetchJson<CreatorCockpitSeedResult>('/api/creator-cockpit/seed-work-items', {
    method: 'POST',
  })
}

export async function fetchProductionPublicQualityStatus(
  accountId?: string,
): Promise<ProductionPublicQualityStatusPayload> {
  const query = new URLSearchParams()
  if (accountId) query.set('account_id', accountId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<ProductionPublicQualityStatusPayload>(`/api/production/public-quality/status${suffix}`)
}

export async function fetchProductionQualityReviewStatus(): Promise<ProductionQualityReviewStatusPayload> {
  return fetchJson<ProductionQualityReviewStatusPayload>('/api/production/quality-review/status')
}

export async function runProductionQualityReviewOnce(payload: {
  limit?: number
} = {}): Promise<ProductionQualityReviewRunResult> {
  return fetchJson<ProductionQualityReviewRunResult>('/api/production/quality-review/run-once', {
    method: 'POST',
    body: JSON.stringify({
      limit: payload.limit ?? null,
    }),
  })
}

export async function fetchProductionQualityRemediationStatus(): Promise<ProductionQualityRemediationStatusPayload> {
  return fetchJson<ProductionQualityRemediationStatusPayload>('/api/production/quality-remediation/status')
}

export async function runProductionQualityRemediationOnce(payload: {
  limit?: number
} = {}): Promise<ProductionQualityRemediationRunResult> {
  return fetchJson<ProductionQualityRemediationRunResult>('/api/production/quality-remediation/run-once', {
    method: 'POST',
    body: JSON.stringify({
      limit: payload.limit ?? null,
    }),
  })
}

export async function fetchProductionProviderDoctor(
  accountId?: string,
): Promise<ProductionProviderDoctorPayload> {
  const query = new URLSearchParams()
  if (accountId) query.set('account_id', accountId)
  query.set('include_oauth', 'true')
  return fetchJson<ProductionProviderDoctorPayload>(`/api/production/provider-doctor?${query.toString()}`)
}

export async function runProductionVoiceProviderCheck(): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/api/production/voice-provider/check', {
    method: 'POST',
  })
}

export async function fetchProductionF5TTSRuntimeStatus(): Promise<F5TTSRuntimeStatus> {
  return fetchJson<F5TTSRuntimeStatus>('/api/production/voice-provider/f5-runtime/status')
}

export async function startProductionF5TTSRuntime(
  waitTimeoutSeconds = 45,
): Promise<F5TTSRuntimeStatus> {
  return fetchJson<F5TTSRuntimeStatus>('/api/production/voice-provider/f5-runtime/start', {
    method: 'POST',
    body: JSON.stringify({ wait_timeout_s: waitTimeoutSeconds }),
  })
}

export async function stopProductionF5TTSRuntime(): Promise<F5TTSRuntimeStatus> {
  return fetchJson<F5TTSRuntimeStatus>('/api/production/voice-provider/f5-runtime/stop', {
    method: 'POST',
  })
}

export async function runProductionVisualBackendCheck(): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/api/production/visual-backend/check', {
    method: 'POST',
  })
}

export async function ensureProductionStyleGuide(payload: {
  accountId: string
  channelName?: string | null
  niche?: string | null
  overwrite?: boolean
}): Promise<ProductionStyleGuideEnsureResult> {
  return fetchJson<ProductionStyleGuideEnsureResult>('/api/production/style-guide/ensure', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      channel_name: payload.channelName ?? null,
      niche: payload.niche || 'faceless YouTube Shorts',
      overwrite: payload.overwrite ?? false,
    }),
  })
}

export async function runProductionProviderDoctorAction(
  actionId: string,
  accountId?: string,
): Promise<ProductionProviderDoctorActionResult> {
  const query = new URLSearchParams()
  if (accountId) query.set('account_id', accountId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<ProductionProviderDoctorActionResult>(
    `/api/production/provider-doctor/actions/${encodeURIComponent(actionId)}${suffix}`,
    { method: 'POST' },
  )
}

export async function fetchProductionOperatingStandards(
  projectId?: string,
): Promise<ProductionOperatingStandardsPayload> {
  const query = new URLSearchParams()
  if (projectId) query.set('project_id', projectId)
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<ProductionOperatingStandardsPayload>(`/api/production/operating-standards${suffix}`)
}

export async function fetchMoneyMachineSummary(payload: {
  accountId?: string
  includeOauth?: boolean
  maxUploads?: number
  perAccountLimit?: number
} = {}): Promise<MoneyMachineOperatorSummaryPayload> {
  const query = new URLSearchParams()
  if (payload.accountId) query.set('account_id', payload.accountId)
  if (payload.includeOauth !== undefined) query.set('include_oauth', payload.includeOauth ? 'true' : 'false')
  if (payload.maxUploads) query.set('max_uploads', String(payload.maxUploads))
  if (payload.perAccountLimit) query.set('per_account_limit', String(payload.perAccountLimit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<MoneyMachineOperatorSummaryPayload>(`/api/production/money-machine/summary${suffix}`)
}

export async function runMoneyMachineNext(payload: MoneyMachineRunNextPayload = {}): Promise<MoneyMachineRunNextResult> {
  return fetchJson<MoneyMachineRunNextResult>('/api/production/money-machine/run-next', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      max_uploads: payload.maxUploads ?? 1,
      per_account_limit: payload.perAccountLimit ?? 1,
      execute: payload.execute ?? false,
      approved_private_upload: payload.approvedPrivateUpload ?? false,
      execute_recovery: payload.executeRecovery ?? false,
    }),
  })
}

export async function runMoneyMachinePublication(
  payload: MoneyMachineRunPublicationPayload = {},
): Promise<MoneyMachineRunPublicationResult> {
  return fetchJson<MoneyMachineRunPublicationResult>('/api/production/money-machine/run-publication', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      max_publications: payload.maxPublications ?? 1,
      per_account_limit: payload.perAccountLimit ?? 1,
      execute: payload.execute ?? false,
      approved_public_promotion: payload.approvedPublicPromotion ?? false,
      publish_at: payload.publishAt ?? null,
    }),
  })
}

export async function runProductionContentLoopOnce(payload: {
  accountId?: string
  maxJobs?: number
} = {}): Promise<ProductionContentLoopRunResult> {
  return fetchJson<ProductionContentLoopRunResult>('/api/production/content-loop/run-once', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      max_jobs: payload.maxJobs ?? 1,
    }),
  })
}

export async function ensureModelPipeline(
  pipeline: string,
  options: { wait?: boolean } = {},
): Promise<EnsureModelPipelineResult> {
  const wait = options.wait === true ? '?wait=true' : ''
  return fetchJson<EnsureModelPipelineResult>(`/api/models/ensure-pipeline/${encodeURIComponent(pipeline)}${wait}`, {
    method: 'POST',
  })
}

export async function fetchModelDownloadProgress(pipeline: string): Promise<ModelDownloadProgressPayload> {
  return fetchJson<ModelDownloadProgressPayload>(`/api/models/downloads/${encodeURIComponent(pipeline)}`)
}

export async function startProduction(payload: {
  accountId: string
  topicId: string
}): Promise<ProductionStartResult> {
  return fetchJson<ProductionStartResult>('/api/production/start', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      topic_id: payload.topicId,
    }),
  })
}

export async function createProductionOrder(payload: {
  topic: string
  direction?: string
  optionalNotes?: string
  requestKey?: string
  project?: string
  deliveryMode?: 'local_only' | 'project_policy'
  productionScope?: 'film' | 'script_preview'
  imageProvider?: 'comfyui-local'
  imageReferenceId?: 'wb-original-board'
  visualStyleId?: 'A' | 'B' | 'C' | 'D'
  voiceCandidate?: 'Ryan-B' | 'Ryan-neutral'
  providerPolicy?: 'strict_local'
  targetDurationSeconds?: number
  productionSetId?: 'wb-editorial-v1'
}): Promise<ProductionJobPayload> {
  const headers = payload.requestKey ? { 'Idempotency-Key': payload.requestKey } : undefined
  return fetchJson<ProductionJobPayload>('/api/production/orders', {
    method: 'POST',
    headers,
    body: JSON.stringify({
      project: payload.project ?? 'wealth_blueprint',
      topic: payload.topic,
      direction: payload.direction ?? '',
      optional_notes: payload.optionalNotes ?? '',
      ...(payload.deliveryMode ? { delivery_mode: payload.deliveryMode } : {}),
      ...(payload.productionScope ? { production_scope: payload.productionScope } : {}),
      ...(payload.imageProvider ? { image_provider: payload.imageProvider } : {}),
      ...(payload.imageReferenceId ? { image_reference_id: payload.imageReferenceId } : {}),
      ...(payload.visualStyleId ? { visual_style_id: payload.visualStyleId } : {}),
      ...(payload.voiceCandidate ? { voice_candidate: payload.voiceCandidate } : {}),
      ...(payload.providerPolicy ? { provider_policy: payload.providerPolicy } : {}),
      ...(payload.targetDurationSeconds ? { target_duration_seconds: payload.targetDurationSeconds } : {}),
      ...(payload.productionSetId ? { production_set_id: payload.productionSetId } : {}),
    }),
  })
}

export interface ProductionImageOptions {
  providers: Array<{ id: 'comfyui-local'; label: string; available: boolean; requires_reference: boolean }>
  references: Array<{ id: 'wb-original-board'; label: string; available: boolean; reason?: 'missing' | 'invalid' }>
  styles: Array<{ id: 'A' | 'B' | 'C' | 'D'; name: string }>
}

export async function fetchProductionImageOptions(): Promise<ProductionImageOptions> {
  const value = await fetchJson<unknown>('/api/production/image-options')
  const isObject = (item: unknown): item is Record<string, unknown> =>
    typeof item === 'object' && item !== null && !Array.isArray(item)
  const isOptions = (candidate: unknown): candidate is ProductionImageOptions => {
    if (!isObject(candidate)) return false
    const { providers, references, styles } = candidate
    return Array.isArray(providers)
    && providers.length > 0
    && providers.every(item => isObject(item) && item.id === 'comfyui-local'
      && typeof item.label === 'string' && item.label.trim().length > 0
      && typeof item.available === 'boolean' && typeof item.requires_reference === 'boolean')
    && Array.isArray(references)
    && references.length > 0
    && references.every(item => isObject(item) && item.id === 'wb-original-board'
      && typeof item.label === 'string' && item.label.trim().length > 0
      && typeof item.available === 'boolean'
      && (item.reason === undefined || item.reason === 'missing' || item.reason === 'invalid'))
    && Array.isArray(styles)
    && styles.every(item => isObject(item) && ['A', 'B', 'C', 'D'].includes(String(item.id))
      && typeof item.name === 'string' && item.name.trim().length > 0)
    && ['A', 'B', 'C', 'D'].every(id => styles.some(item => isObject(item) && item.id === id
      && typeof item.name === 'string' && item.name.trim().length > 0))
  }
  if (!isOptions(value)) throw new Error('Odpowiedź opcji grafiki z API ma nieprawidłowy format.')
  return value
}

export interface ProductionWorkspaceChannel {
  key: string
  name: string
  category: string
  description: string
  language: string
  can_order: boolean
  can_prepare_story?: boolean
  blockers: string[]
  formats: string[]
  duration_seconds?: [number, number]
  max_cost_usd?: number
  voice_provider?: string
  local_voice?: { id: 'Ryan-B'; label: string; available: boolean; reason?: string }
  local_voices?: Array<{ id: 'Ryan-B' | 'Ryan-neutral'; label: string; available: boolean; reason?: string }>
  production_sets?: Array<{ id: 'wb-editorial-v1'; label: string; available: boolean; reason: string; digest?: string; scope?: string }>
}

export async function fetchProductionWorkspace(): Promise<{ channels: ProductionWorkspaceChannel[]; note: string }> {
  return fetchJson('/api/production/workspace')
}

export async function fetchProductionOrders(): Promise<{ orders: ProductionJobPayload[] }> {
  return fetchJson('/api/production/orders?limit=30')
}

export interface MemoryIndexDocument {
  doc_id: string
  path: string
  title: string
  topic: string
  source_type: string
  project_id: string | null
  citation_path: string
  source_mtime: string
  indexed_at: string
}

export interface MemoryIndexMap {
  status: 'indexed' | 'missing' | 'unavailable'
  source: string
  document_count: number | null
  source_counts: Record<string, number>
  documents: MemoryIndexDocument[]
  last_indexed_at: string | null
  truncated: boolean
}

export async function fetchMemoryIndexMap(source?: string): Promise<MemoryIndexMap> {
  const params = new URLSearchParams({ limit: '80' })
  if (source && source !== 'all') params.set('source_type', source)
  return fetchJson(`/memory/index-map?${params.toString()}`)
}

export async function fetchProductionDelegationReviewStatus(payload: {
  accountId: string
  topicId: string
}): Promise<ProductionDelegationReviewStatus> {
  const params = new URLSearchParams({
    account_id: payload.accountId,
    topic_id: payload.topicId,
  })
  return fetchJson<ProductionDelegationReviewStatus>(`/api/production/delegation-review/status?${params.toString()}`)
}

export async function createProductionDelegationReviewPlan(payload: {
  accountId: string
  topicId: string
  allowBrowserAi?: boolean
  maxSteps?: number
}): Promise<ProductionDelegationReviewResult> {
  return fetchJson<ProductionDelegationReviewResult>('/api/production/delegation-review/plan', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      topic_id: payload.topicId,
      allow_browser_ai: payload.allowBrowserAi ?? false,
      max_steps: payload.maxSteps ?? 6,
    }),
  })
}

export async function runProductionDelegationReviewNext(payload: {
  accountId: string
  topicId: string
}): Promise<ProductionDelegationReviewRunResult> {
  return fetchJson<ProductionDelegationReviewRunResult>('/api/production/delegation-review/run-next', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      topic_id: payload.topicId,
    }),
  })
}

export async function runProductionDelegationReviewAll(payload: {
  accountId: string
  topicId: string
  limit?: number
}): Promise<ProductionDelegationReviewRunAllResult> {
  return fetchJson<ProductionDelegationReviewRunAllResult>('/api/production/delegation-review/run-all', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      topic_id: payload.topicId,
      limit: payload.limit ?? 6,
    }),
  })
}

export async function fetchProductionJob(jobId: string): Promise<ProductionJobPayload> {
  return fetchJson<ProductionJobPayload>(`/api/production/jobs/${encodeURIComponent(jobId)}`)
}

export async function fetchProductionStudioJob(jobId: string): Promise<ProductionJobPayload> {
  try {
    return await fetchJson<ProductionJobPayload>(`/api/production/orders/${encodeURIComponent(jobId)}`)
  } catch (error) {
    if (!(error instanceof ApiRequestError) || error.status !== 404) throw error
    return fetchJson<ProductionJobPayload>(`/api/production/jobs/${encodeURIComponent(jobId)}`)
  }
}

export async function fetchProductionAssetCandidates(jobId: string): Promise<ProductionAssetCandidatesResponse> {
  return fetchJson<ProductionAssetCandidatesResponse>(`/api/production/orders/${encodeURIComponent(jobId)}/asset-candidates`)
}

export async function fetchProductionAssetRejectReasons(): Promise<{ reasons: string[] }> {
  return fetchJson<{ reasons: string[] }>('/api/production/asset-brain/reject-reasons')
}

export async function approveProductionAssetCandidate(assetId: string): Promise<void> {
  await fetchJson(`/api/production/asset-brain/assets/${encodeURIComponent(assetId)}/approve`, {
    method: 'POST',
    body: JSON.stringify({ reviewed_by: 'operator' }),
  })
}

export async function rejectProductionAssetCandidate(assetId: string, reason: string): Promise<void> {
  await fetchJson(`/api/production/asset-brain/assets/${encodeURIComponent(assetId)}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason, reviewed_by: 'operator' }),
  })
}

export async function fetchProductionReviewPackage(jobId: string): Promise<ProductionReviewPackage> {
  return fetchJson<ProductionReviewPackage>(
    `/api/production/jobs/${encodeURIComponent(jobId)}/review-package`,
  )
}

export async function fetchProductionStudioReviewPackage(jobId: string): Promise<ProductionReviewPackage> {
  try {
    return await fetchJson<ProductionReviewPackage>(
      `/api/production/orders/${encodeURIComponent(jobId)}/review-package`,
    )
  } catch (error) {
    if (!(error instanceof ApiRequestError) || error.status !== 404) throw error
    return fetchJson<ProductionReviewPackage>(
      `/api/production/jobs/${encodeURIComponent(jobId)}/review-package`,
    )
  }
}

export async function resumeProductionOrder(jobId: string): Promise<ProductionJobPayload> {
  return fetchJson<ProductionJobPayload>(`/api/production/orders/${encodeURIComponent(jobId)}/resume`, { method: 'POST' })
}

export async function reconcileProductionLocalOperation(jobId: string): Promise<ProductionJobPayload> {
  return fetchJson<ProductionJobPayload>(`/api/production/orders/${encodeURIComponent(jobId)}/reconcile-local-operation`, { method: 'POST' })
}

export async function fetchProductionOrderRecovery(jobId: string): Promise<ProductionOrderRecovery | null> {
  try {
    return await fetchJson<ProductionOrderRecovery>(`/api/production/orders/${encodeURIComponent(jobId)}/recovery`)
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) return null
    throw error
  }
}

export async function repairProductionOrderScript(jobId: string, payload: { note: string; requestKey: string }): Promise<ProductionJobPayload> {
  return fetchJson<ProductionJobPayload>(`/api/production/orders/${encodeURIComponent(jobId)}/script-repair`, {
    method: 'POST',
    body: JSON.stringify({ note: payload.note, request_key: payload.requestKey }),
  })
}

export interface PresentationRepairSnapshot {
  can_repair: boolean
  reason: string
  scope: 'renderer_and_downstream_only'
  package_sha256?: string
  renderer_source?: string
}

export function fetchPresentationRepair(jobId: string): Promise<PresentationRepairSnapshot> {
  return fetchJson(`/api/production/orders/${encodeURIComponent(jobId)}/presentation-repair`)
}

export function applyPresentationRepair(jobId: string, payload: {
  request_key: string; expected_package_sha256: string; expected_renderer_source: string; note: string
}): Promise<ProductionJobPayload> {
  return fetchJson(`/api/production/orders/${encodeURIComponent(jobId)}/presentation-repair`, {
    method: 'POST', body: JSON.stringify(payload),
  })
}

export async function authorizeProductionCallLimit(jobId: string, maxCalls: number): Promise<ProductionJobPayload> {
  return fetchJson<ProductionJobPayload>(`/api/production/orders/${encodeURIComponent(jobId)}/call-limit`, {
    method: 'POST',
    body: JSON.stringify({ max_calls: maxCalls }),
  })
}

export interface PublicationPolicyPayload {
  enabled: boolean
  channel_id: string | null
  times_utc: string[]
  authorized_after?: string | null
}

export interface PublicationStatusPayload {
  policy: PublicationPolicyPayload
  write_blocked: boolean
  runtime?: { running: boolean; last_tick: number | null; error: string | null }
  jobs: Array<{ job_id: string; status: string; publish_at: string; error: string | null; youtube_url: string | null }>
}

export function fetchPublicationStatus(jobId: string): Promise<PublicationStatusPayload> {
  return fetchJson(`/api/production/publication?job_id=${encodeURIComponent(jobId)}`)
}

export function savePublicationPolicy(policy: PublicationPolicyPayload): Promise<PublicationPolicyPayload> {
  return fetchJson('/api/production/publication/policy', { method: 'PUT', headers: localCapabilityHeaders(), body: JSON.stringify({
    policy: { enabled: policy.enabled, channel_id: policy.channel_id, times_utc: policy.times_utc },
    approve_scheduled_publication: policy.enabled,
  }) })
}

export function verifyPublicationChannel(): Promise<{ status: string; message: string; channel_id?: string; title?: string }> {
  return fetchJson('/api/production/publication/verify', { method: 'POST', headers: localCapabilityHeaders() })
}

export function retryPublication(jobId: string): Promise<PublicationStatusPayload> {
  return fetchJson(`/api/production/publication/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST', headers: localCapabilityHeaders() })
}

export interface ProductionFeedbackPayload {
  id: string
  source_job_id: string
  scope: 'production' | 'series' | 'project'
  category: string
  note: string
  active: boolean
}

export async function fetchProductionFeedback(jobId: string): Promise<{ feedback: ProductionFeedbackPayload[] }> {
  return fetchJson(`/api/production/orders/${encodeURIComponent(jobId)}/feedback`)
}

export async function addProductionFeedback(jobId: string, payload: { note: string; scope: ProductionFeedbackPayload['scope'] }): Promise<ProductionFeedbackPayload> {
  return fetchJson(`/api/production/orders/${encodeURIComponent(jobId)}/feedback`, { method: 'POST', body: JSON.stringify(payload) })
}

export async function disableProductionFeedback(sourceJobId: string, feedbackId: string): Promise<{ disabled: boolean }> {
  return fetchJson(`/api/production/orders/${encodeURIComponent(sourceJobId)}/feedback/${encodeURIComponent(feedbackId)}`, { method: 'DELETE' })
}

export function buildProductionReviewArtifactUrl(path: string | null): string | null {
  if (!path) return null
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`
}

export async function fetchProductionDistributionPlan(jobId: string): Promise<ProductionDistributionPlan> {
  return fetchJson<ProductionDistributionPlan>(
    `/api/production/jobs/${encodeURIComponent(jobId)}/distribution-plan`,
  )
}

export async function createProductionDistributionPackage(payload: {
  jobId: string
  platforms?: string[]
}): Promise<ProductionDistributionPackageResult> {
  return fetchJson<ProductionDistributionPackageResult>(
    `/api/production/jobs/${encodeURIComponent(payload.jobId)}/distribution-package`,
    {
      method: 'POST',
      body: JSON.stringify({ platforms: payload.platforms ?? null }),
    },
  )
}

export async function fetchDistributionCockpit(payload: {
  accountId?: string
  limit?: number
} = {}): Promise<DistributionCockpitPayload> {
  const query = new URLSearchParams()
  if (payload.accountId) query.set('account_id', payload.accountId)
  if (payload.limit) query.set('limit', String(payload.limit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<DistributionCockpitPayload>(`/api/distribution/cockpit${suffix}`)
}

export async function createLatestDistributionPackage(payload: {
  accountId?: string
  platforms?: string[]
} = {}): Promise<DistributionPackageLatestResult> {
  return fetchJson<DistributionPackageLatestResult>('/api/distribution/package-latest', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId ?? null,
      platforms: payload.platforms ?? null,
    }),
  })
}

export async function fetchProductionJobs(payload: {
  accountId?: string
  limit?: number
} = {}): Promise<{ jobs: ProductionJobPayload[] }> {
  const query = new URLSearchParams()
  if (payload.accountId) query.set('account_id', payload.accountId)
  if (payload.limit) query.set('limit', String(payload.limit))
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return fetchJson<{ jobs: ProductionJobPayload[] }>(`/api/production/jobs${suffix}`)
}

export async function uploadProductionJobPrivate(jobId: string): Promise<ProductionPrivateUploadResult> {
  return fetchJson<ProductionPrivateUploadResult>(
    `/api/production/jobs/${encodeURIComponent(jobId)}/upload-private`,
    { method: 'POST' },
  )
}

export async function regenerateProductionJob(
  jobId: string,
  reason = 'dashboard_recovery',
): Promise<ProductionRegenerateResult> {
  return fetchJson<ProductionRegenerateResult>(
    `/api/production/jobs/${encodeURIComponent(jobId)}/regenerate`,
    {
      method: 'POST',
      body: JSON.stringify({ reason, run_in_background: true }),
    },
  )
}

export async function generateTopics(payload: {
  accountId: string
  count?: number
  geo?: string
}): Promise<TopicQueueItem[]> {
  return fetchJson<TopicQueueItem[]>('/api/topics/generate', {
    method: 'POST',
    body: JSON.stringify({
      account_id: payload.accountId,
      count: payload.count ?? 20,
      geo: payload.geo ?? 'PL',
    }),
  })
}

export async function fetchTopicQueue(payload: {
  accountId: string
  status?: TopicQueueStatus | 'all'
}): Promise<TopicQueueItem[]> {
  const status = payload.status ?? 'pending'
  return fetchJson<TopicQueueItem[]>(
    `/api/topics/queue?account_id=${encodeURIComponent(payload.accountId)}&status=${encodeURIComponent(status)}`,
  )
}

export async function updateTopicQueueStatus(
  topicId: string,
  status: TopicQueueStatus,
): Promise<TopicQueueItem> {
  return fetchJson<TopicQueueItem>(`/api/topics/${encodeURIComponent(topicId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export async function deleteTopicQueueItem(topicId: string): Promise<{ deleted: boolean; topic_id: string }> {
  return fetchJson<{ deleted: boolean; topic_id: string }>(`/api/topics/${encodeURIComponent(topicId)}`, {
    method: 'DELETE',
  })
}

function normalizeConversationSummary(item: Record<string, unknown>): ConversationSummary {
  return {
    id: Number(item.id),
    title: String(item.title ?? 'Rozmowa'),
    projectId: item.project_id == null ? null : Number(item.project_id),
    summary: item.summary == null ? null : String(item.summary),
    lastMessageAt: item.last_message_at == null ? null : String(item.last_message_at),
    updatedAt: String(item.updated_at ?? ''),
    archived: Boolean(item.archived),
    matchedSource: item.matched_source == null ? null : String(item.matched_source),
  }
}

function normalizeProjectSummary(item: Record<string, unknown>): BootstrapPayload['projects'][number] {
  return {
    id: Number(item.id),
    name: String(item.name),
    slug: String(item.slug),
    description: item.description == null ? null : String(item.description),
    summary: item.summary == null ? null : String(item.summary),
    status: String(item.status),
    isPinned: Boolean(item.is_pinned),
    providerPosture:
      item.provider_posture == null ? null : (String(item.provider_posture) as ProjectProviderPosture),
    personaProfile:
      item.persona_profile == null ? null : (String(item.persona_profile) as ProjectPersonaProfile),
  }
}

function normalizeProjectMutation(
  item: Record<string, unknown>,
  fallback: ProjectMutationPayload,
): ProjectMutationResult {
  return {
    projectId: Number(item.project_id),
    name: String(item.name ?? fallback.name),
    slug: String(item.slug ?? ''),
    description: item.description == null ? fallback.description ?? null : String(item.description),
    summary: item.summary == null ? null : String(item.summary),
    status: String(item.status ?? 'active'),
    isPinned: Boolean(item.is_pinned),
    providerPosture:
      item.provider_posture == null
        ? fallback.providerPosture ?? null
        : (String(item.provider_posture) as ProjectProviderPosture),
    personaProfile:
      item.persona_profile == null
        ? fallback.personaProfile ?? null
        : (String(item.persona_profile) as ProjectPersonaProfile),
  }
}

function normalizeProjectPlatformBinding(item: Record<string, unknown>): ProjectPlatformBinding {
  const account = item.account_summary as Record<string, unknown> | null | undefined
  return {
    id: Number(item.id),
    projectId: Number(item.project_id),
    integrationKey: String(item.integration_key ?? ''),
    customIntegrationId: item.custom_integration_id == null ? null : Number(item.custom_integration_id),
    accountId: item.account_id == null ? null : Number(item.account_id),
    accountSummary: account
      ? {
          id: Number(account.id),
          label: String(account.label ?? ''),
          providerKey: String(account.provider_key ?? ''),
          accountIdentifier: String(account.account_identifier ?? ''),
          status: String(account.status ?? 'unknown'),
          riskLevel: String(account.risk_level ?? 'unknown'),
          linkedIntegrationKey:
            account.linked_integration_key == null ? null : String(account.linked_integration_key),
        }
      : null,
    resourceType: String(item.resource_type ?? 'account') as ProjectPlatformResourceType,
    resourceExternalId: item.resource_external_id == null ? null : String(item.resource_external_id),
    resourceLabel: item.resource_label == null ? null : String(item.resource_label),
    bindingRole: String(item.binding_role ?? 'primary'),
    environment: String(item.environment ?? 'production') as ProjectPlatformEnvironment,
    confirmationPolicy: String(item.confirmation_policy ?? 'supervised') as ProjectPlatformBinding['confirmationPolicy'],
    delegationPolicy: String(item.delegation_policy ?? 'project_only') as ProjectPlatformBinding['delegationPolicy'],
    allowSharedAccount: Boolean(item.allow_shared_account),
    permissionScopes: Array.isArray(item.permission_scopes) ? item.permission_scopes.map(String) : [],
    connectionStatus: String(item.connection_status ?? 'not_verified'),
    connectionDetail: item.connection_detail == null ? null : String(item.connection_detail),
    lastVerifiedAt: item.last_verified_at == null ? null : String(item.last_verified_at),
    verificationEvidence:
      item.verification_evidence && typeof item.verification_evidence === 'object'
        ? { ...(item.verification_evidence as Record<string, unknown>) }
        : {},
    label: String(item.label ?? ''),
    purpose: item.purpose == null ? null : String(item.purpose),
    status: String(item.status ?? 'configured') as ProjectPlatformBinding['status'],
    permissionProfile: String(item.permission_profile ?? 'metadata_only') as ProjectPlatformPermissionProfile,
    allowedCapabilities: Array.isArray(item.allowed_capabilities) ? item.allowed_capabilities.map(String) : [],
    safetyNotes: item.safety_notes == null ? null : String(item.safety_notes),
    notes: item.notes == null ? null : String(item.notes),
    integrationLabel: String(item.integration_label ?? item.integration_key ?? ''),
    createdAt: String(item.created_at ?? ''),
    updatedAt: String(item.updated_at ?? ''),
  }
}

function normalizeProviderCard(item: Record<string, unknown>): ProviderCard {
  return {
    providerKey: String(item.provider_key) as ProviderCard['providerKey'],
    displayName: String(item.display_name),
    status: String(item.status),
    statusReason: localizeProviderStatusReason(String(item.status_reason ?? '')),
    supportsApiKey: Boolean(item.supports_api_key),
    supportsBaseUrl: Boolean(item.supports_base_url),
    providerEnabled: Boolean(item.provider_enabled),
    baseUrl: item.base_url == null ? null : String(item.base_url),
    accountId: item.account_id == null ? null : Number(item.account_id),
    hasSecret: Boolean(item.has_secret),
    maskedSecretState: String(item.masked_secret_state ?? 'Not configured'),
    runtimeAdapterAvailable: Boolean(item.runtime_adapter_available),
    runtimeHealthStatus: String(item.runtime_health_status ?? 'unknown'),
    runtimeHealthReason: String(item.runtime_health_reason ?? ''),
    sanityStatus: String(item.sanity_status ?? 'unknown'),
    sanityReason: String(item.sanity_reason ?? ''),
    readinessStatus: String(item.readiness_status ?? 'blocked'),
    readinessReason: String(item.readiness_reason ?? ''),
    oauthConnectorKey: item.oauth_connector_key == null ? null : String(item.oauth_connector_key),
  }
}

function normalizePipelineStep(item: Record<string, unknown>): PipelineStep {
  return {
    id: String(item.id) as PipelineStepId,
    label: String(item.label),
    state: String(item.state) as PipelineStepState,
    progress: Number(item.progress ?? 0),
    durationMs: item.duration_ms == null ? null : Number(item.duration_ms),
    errorMessage: item.error_message == null ? null : String(item.error_message),
    previewOnly: Boolean(item.preview_only),
    workItemId: item.work_item_id == null ? null : Number(item.work_item_id),
    workType: item.work_type == null ? null : String(item.work_type),
    reviewNotes: item.review_notes == null ? null : String(item.review_notes),
  }
}

export function normalizePipelineSnapshot(item: Record<string, unknown>): PipelineSnapshot {
  return {
    previewOnly: Boolean(item.preview_only),
    job: item.job
      ? {
          creatorProjectId: Number((item.job as Record<string, unknown>).creator_project_id),
          projectId:
            (item.job as Record<string, unknown>).project_id == null
              ? null
              : Number((item.job as Record<string, unknown>).project_id),
          title: String((item.job as Record<string, unknown>).title ?? 'ATNOS job'),
          projectType: String((item.job as Record<string, unknown>).project_type ?? 'unknown'),
          status: String((item.job as Record<string, unknown>).status ?? 'active'),
          platforms: Array.isArray((item.job as Record<string, unknown>).platforms)
            ? ((item.job as Record<string, unknown>).platforms as string[])
            : [],
          operatingMode: String((item.job as Record<string, unknown>).operating_mode ?? 'planning_only'),
          approvalPolicy: String((item.job as Record<string, unknown>).approval_policy ?? 'always_require_approval'),
          updatedAt: String((item.job as Record<string, unknown>).updated_at ?? ''),
        }
      : null,
    steps: Array.isArray(item.steps)
      ? item.steps.map((step) => normalizePipelineStep(step as Record<string, unknown>))
      : [],
    latestVideoArtifact: item.latest_video_artifact
      ? {
          title: String((item.latest_video_artifact as Record<string, unknown>).title ?? 'Ostatnie wideo'),
          storagePath:
            (item.latest_video_artifact as Record<string, unknown>).storage_path == null
              ? null
              : String((item.latest_video_artifact as Record<string, unknown>).storage_path),
          externalUrl:
            (item.latest_video_artifact as Record<string, unknown>).external_url == null
              ? null
              : String((item.latest_video_artifact as Record<string, unknown>).external_url),
          updatedAt:
            (item.latest_video_artifact as Record<string, unknown>).updated_at == null
              ? null
              : String((item.latest_video_artifact as Record<string, unknown>).updated_at),
        }
      : null,
    awaitingApproval: Boolean(item.awaiting_approval),
    status: String(item.status ?? 'idle') as PipelineSnapshot['status'],
    statusReason: localizePipelineReason(String(item.status_reason ?? '')),
    approvalPackage: item.approval_package
      ? {
          workItem: { ...((item.approval_package as Record<string, unknown>).work_item as Record<string, unknown>) },
          project: { ...((item.approval_package as Record<string, unknown>).project as Record<string, unknown>) },
          risk: String((item.approval_package as Record<string, unknown>).risk ?? 'medium'),
          safetyFlags: Array.isArray((item.approval_package as Record<string, unknown>).safety_flags)
            ? ((item.approval_package as Record<string, unknown>).safety_flags as string[])
            : [],
          reviewNotes:
            (item.approval_package as Record<string, unknown>).review_notes == null
              ? null
              : String((item.approval_package as Record<string, unknown>).review_notes),
          proposedNextAction: String((item.approval_package as Record<string, unknown>).proposed_next_action ?? ''),
          actionBrokerPayloadPreview: {
            ...((item.approval_package as Record<string, unknown>).action_broker_payload_preview as Record<
              string,
              unknown
            >),
          },
        }
      : null,
    lastReviewUpdate: item.last_review_update
      ? {
          workItemId: Number((item.last_review_update as Record<string, unknown>).work_item_id),
          status: String((item.last_review_update as Record<string, unknown>).status),
          reviewNotes:
            (item.last_review_update as Record<string, unknown>).review_notes == null
              ? null
              : String((item.last_review_update as Record<string, unknown>).review_notes),
        }
      : undefined,
  }
}

export async function fetchBootstrap(): Promise<BootstrapPayload> {
  const payload = await fetchJson<Record<string, unknown>>('/ui/bootstrap')
  const runtimeSummaryPayload = (payload.runtime_summary ?? {}) as Record<string, unknown>
  const voiceSttSummaryPayload = (runtimeSummaryPayload.voice_stt ?? {}) as Record<string, unknown>

  return {
    uiVersion: String(payload.ui_version),
    generatedAt: String(payload.generated_at),
    projects: Array.isArray(payload.projects)
      ? payload.projects.map((project) => normalizeProjectSummary(project as Record<string, unknown>))
      : [],
    recentConversations: Array.isArray(payload.recent_conversations)
      ? payload.recent_conversations.map((item) =>
          normalizeConversationSummary(item as Record<string, unknown>),
        )
      : [],
    runtimeSummary: {
      providersReady: Number(runtimeSummaryPayload.providers_ready ?? 0),
      enabledModels: Number(runtimeSummaryPayload.enabled_models ?? 0),
      tasksReady: Number(runtimeSummaryPayload.tasks_ready ?? 0),
      hasLocalChat: Boolean(runtimeSummaryPayload.has_local_chat),
      hasExternalFallback: Boolean(runtimeSummaryPayload.has_external_fallback),
      voiceStt: {
        configuredEngine: String(voiceSttSummaryPayload.configured_engine ?? 'browser-speech'),
        effectiveEngine: String(voiceSttSummaryPayload.effective_engine ?? 'browser-speech'),
        status: String(voiceSttSummaryPayload.status ?? 'unknown'),
        statusReason: String(voiceSttSummaryPayload.status_reason ?? ''),
      },
    },
    providerCards: Array.isArray(payload.provider_cards)
      ? payload.provider_cards.map((item) => normalizeProviderCard(item as Record<string, unknown>))
      : [],
    oauthConnectors: Array.isArray(payload.oauth_connectors)
      ? payload.oauth_connectors.map((connector) => ({
          connectorKey: String((connector as Record<string, unknown>).connector_key),
          displayName: String((connector as Record<string, unknown>).display_name),
          status: String((connector as Record<string, unknown>).status),
          enabled: Boolean((connector as Record<string, unknown>).enabled),
          configured: Boolean((connector as Record<string, unknown>).configured),
          connectionId:
            (connector as Record<string, unknown>).connection_id == null
              ? null
              : Number((connector as Record<string, unknown>).connection_id),
          connectedAt:
            (connector as Record<string, unknown>).connected_at == null
              ? null
              : String((connector as Record<string, unknown>).connected_at),
          setupWarnings: Array.isArray((connector as Record<string, unknown>).setup_warnings)
            ? ((connector as Record<string, unknown>).setup_warnings as string[])
            : [],
        }))
      : [],
    capabilities: {
      browserControlEnabled: Boolean((payload.capabilities as Record<string, unknown>).browser_control_enabled),
      externalActionsEnabled: Boolean((payload.capabilities as Record<string, unknown>).external_actions_enabled),
      schedulerEnabled: Boolean((payload.capabilities as Record<string, unknown>).scheduler_enabled),
      playwrightAdapterAvailable: Boolean(
        (payload.capabilities as Record<string, unknown>).playwright_adapter_available,
      ),
      oauthEnabled: Boolean((payload.capabilities as Record<string, unknown>).oauth_enabled),
      obsidianExportEnabled: Boolean((payload.capabilities as Record<string, unknown>).obsidian_export_enabled),
      autonomyMode:
        (payload.capabilities as Record<string, unknown>).autonomy_mode == null
          ? undefined
          : String((payload.capabilities as Record<string, unknown>).autonomy_mode),
      executionEnabled:
        (payload.capabilities as Record<string, unknown>).execution_enabled == null
          ? undefined
          : Boolean((payload.capabilities as Record<string, unknown>).execution_enabled),
    },
    pipeline: normalizePipelineSnapshot(payload.pipeline as Record<string, unknown>),
    trustedAutoSummary: normalizeTrustedAutoSummary(
      (payload.trusted_auto_summary as Record<string, unknown> | null) ?? {},
    ),
    accessReality: normalizeAccessRealitySnapshot(
      (payload.access_reality as Record<string, unknown> | null) ?? null,
    ),
    ambientState: normalizeAmbientState(
      (payload.ambient_state as Record<string, unknown> | null) ?? null,
    ),
    remoteWorkers: Array.isArray(payload.remote_workers)
      ? payload.remote_workers.map((item) =>
          normalizeRemoteWorkerSummary(item as Record<string, unknown>),
        )
      : [],
  }
}

export async function fetchBootstrapShell(): Promise<BootstrapShellPayload> {
  const payload = await fetchJson<Record<string, unknown>>('/ui/shell')
  return {
    uiVersion: String(payload.ui_version),
    generatedAt: String(payload.generated_at),
    projects: Array.isArray(payload.projects)
      ? payload.projects.map((project) => normalizeProjectSummary(project as Record<string, unknown>))
      : [],
    recentConversations: Array.isArray(payload.recent_conversations)
      ? payload.recent_conversations.map((item) =>
          normalizeConversationSummary(item as Record<string, unknown>),
        )
      : [],
  }
}

export async function createProject(payload: ProjectMutationPayload): Promise<ProjectMutationResult> {
  const response = await fetchJson<Record<string, unknown>>('/projects', {
    method: 'POST',
    body: JSON.stringify({
      name: payload.name,
      description: payload.description,
      provider_posture: payload.providerPosture,
      persona_profile: payload.personaProfile,
    }),
  })

  return normalizeProjectMutation(response, payload)
}

export async function fetchProjectPlatformBindings(projectId: number): Promise<ProjectPlatformBinding[]> {
  const response = await fetchJson<Array<Record<string, unknown>>>(`/projects/${projectId}/app-links`)
  return response.map(normalizeProjectPlatformBinding)
}

export async function createProjectPlatformBinding(
  projectId: number,
  payload: ProjectPlatformBindingCreatePayload,
): Promise<ProjectPlatformBinding> {
  const response = await fetchJson<Record<string, unknown>>(`/projects/${projectId}/app-links`, {
    method: 'POST',
    body: JSON.stringify({
      integration_key: payload.integrationKey,
      account_id: payload.accountId ?? null,
      label: payload.label,
      purpose: payload.purpose ?? null,
      status: payload.status ?? 'configured',
      permission_profile: payload.permissionProfile,
      allowed_capabilities: [],
      safety_notes: null,
      is_default_for_project: false,
      is_default_for_content: false,
      is_default_for_analytics: false,
      is_default_for_storage: false,
      is_default_for_publish_preview: false,
      notes: null,
      resource_type: payload.resourceType,
      resource_external_id: payload.resourceExternalId,
      resource_label: payload.resourceLabel ?? null,
      binding_role: payload.bindingRole ?? 'primary',
      environment: payload.environment,
      confirmation_policy: payload.confirmationPolicy ?? 'supervised',
      delegation_policy: payload.delegationPolicy ?? 'project_only',
      allow_shared_account: payload.allowSharedAccount ?? false,
      permission_scopes: payload.permissionScopes,
    }),
  })
  return normalizeProjectPlatformBinding(response)
}

export async function updateProjectPlatformBinding(
  projectId: number,
  bindingId: number,
  payload: ProjectPlatformBindingUpdatePayload,
): Promise<ProjectPlatformBinding> {
  const response = await fetchJson<Record<string, unknown>>(
    `/projects/${projectId}/app-links/${bindingId}`,
    {
      method: 'PATCH',
      body: JSON.stringify({
        account_id: payload.accountId,
        label: payload.label,
        purpose: payload.purpose,
        status: payload.status,
        permission_profile: payload.permissionProfile,
        resource_type: payload.resourceType,
        resource_external_id: payload.resourceExternalId,
        resource_label: payload.resourceLabel,
        environment: payload.environment,
        permission_scopes: payload.permissionScopes,
        binding_role: payload.bindingRole,
        confirmation_policy: payload.confirmationPolicy,
        delegation_policy: payload.delegationPolicy,
        allow_shared_account: payload.allowSharedAccount,
      }),
    },
  )
  return normalizeProjectPlatformBinding(response)
}

export async function disableProjectPlatformBinding(
  projectId: number,
  bindingId: number,
): Promise<ProjectPlatformBinding> {
  const response = await fetchJson<Record<string, unknown>>(
    `/projects/${projectId}/app-links/${bindingId}`,
    { method: 'DELETE' },
  )
  return normalizeProjectPlatformBinding(response)
}

export async function verifyProjectPlatformBinding(
  projectId: number,
  bindingId: number,
): Promise<ProjectPlatformBinding> {
  const response = await fetchJson<Record<string, unknown>>(
    `/projects/${projectId}/app-links/${bindingId}/verify`,
    { method: 'POST' },
  )
  return normalizeProjectPlatformBinding(response)
}

export async function updateProject(
  projectId: number,
  payload: ProjectMutationPayload,
): Promise<ProjectMutationResult> {
  const response = await fetchJson<Record<string, unknown>>(`/projects/${projectId}`, {
    method: 'PUT',
    body: JSON.stringify({
      name: payload.name,
      description: payload.description,
      provider_posture: payload.providerPosture,
      persona_profile: payload.personaProfile,
    }),
  })

  return normalizeProjectMutation(response, payload)
}

export async function deleteProject(projectId: number): Promise<ProjectDeleteResult> {
  const response = await fetchJson<Record<string, unknown>>(`/projects/${projectId}`, {
    method: 'DELETE',
  })

  return {
    projectId: Number(response.project_id),
    name: String(response.name ?? 'Projekt'),
    deleted: Boolean(response.deleted),
  }
}

export async function fetchConversationDetail(conversationId: number): Promise<ConversationDetailPayload> {
  const payload = await fetchJson<Record<string, unknown>>(`/ui/chat/conversations/${conversationId}`)
  return {
    conversation: {
      id: Number((payload.conversation as Record<string, unknown>).id),
      title: String((payload.conversation as Record<string, unknown>).title),
      projectId:
        (payload.conversation as Record<string, unknown>).project_id == null
          ? null
          : Number((payload.conversation as Record<string, unknown>).project_id),
      summary:
        (payload.conversation as Record<string, unknown>).summary == null
          ? null
          : String((payload.conversation as Record<string, unknown>).summary),
      lastMessageAt:
        (payload.conversation as Record<string, unknown>).last_message_at == null
          ? null
          : String((payload.conversation as Record<string, unknown>).last_message_at),
      updatedAt: String((payload.conversation as Record<string, unknown>).updated_at),
      activeWorkMode: String((payload.conversation as Record<string, unknown>).active_work_mode),
    },
    messages: Array.isArray(payload.messages)
      ? payload.messages.map((message) => ({
          id: (message as Record<string, unknown>).id as number | string,
          role: String((message as Record<string, unknown>).role) as ConversationMessage['role'],
          status: String((message as Record<string, unknown>).status ?? 'completed'),
          sequenceNumber:
            (message as Record<string, unknown>).sequence_number == null
              ? undefined
              : Number((message as Record<string, unknown>).sequence_number),
          content: String((message as Record<string, unknown>).content ?? ''),
          contentFormat:
            (message as Record<string, unknown>).content_format == null
              ? undefined
              : String((message as Record<string, unknown>).content_format),
          promptImproved: Boolean((message as Record<string, unknown>).prompt_improved),
          attachmentOnly: Boolean((message as Record<string, unknown>).attachment_only),
          metadataJson:
            (message as Record<string, unknown>).metadata_json &&
            typeof (message as Record<string, unknown>).metadata_json === 'object'
              ? ((message as Record<string, unknown>).metadata_json as Record<string, unknown>)
              : undefined,
          attachments: Array.isArray((message as Record<string, unknown>).attachments)
            ? ((message as Record<string, unknown>).attachments as Array<Record<string, unknown>>).map(
                (attachment) => ({
                  attachmentId: Number(attachment.attachment_id),
                  filename: String(attachment.filename ?? ''),
                  storagePath: String(attachment.storage_path ?? ''),
                  kind: String(attachment.kind ?? 'other'),
                  source: String(attachment.source ?? 'upload'),
                  status: String(attachment.status ?? 'available'),
                  mediaType:
                    attachment.media_type == null ? null : String(attachment.media_type),
                  fileExtension:
                    attachment.file_extension == null ? null : String(attachment.file_extension),
                  sizeBytes:
                    attachment.size_bytes == null ? null : Number(attachment.size_bytes),
                  extractedText:
                    attachment.extracted_text == null ? null : String(attachment.extracted_text),
                  metadataJson:
                    attachment.metadata_json && typeof attachment.metadata_json === 'object'
                      ? (attachment.metadata_json as Record<string, unknown>)
                      : null,
                }),
              )
            : [],
          createdAt:
            (message as Record<string, unknown>).created_at == null
              ? undefined
              : String((message as Record<string, unknown>).created_at),
          updatedAt:
            (message as Record<string, unknown>).updated_at == null
              ? undefined
              : String((message as Record<string, unknown>).updated_at),
        }))
      : [],
    settings: {
      activeWorkMode:
        (payload.conversation as Record<string, unknown>).active_work_mode === 'quick' ||
        (payload.conversation as Record<string, unknown>).active_work_mode === 'deep' ||
        (payload.conversation as Record<string, unknown>).active_work_mode === 'execute'
          ? ((payload.conversation as Record<string, unknown>).active_work_mode as
              | 'quick'
              | 'deep'
              | 'execute')
          : 'auto',
      responseStyle:
        (payload.settings as Record<string, unknown>).response_style == null
          ? null
          : String((payload.settings as Record<string, unknown>).response_style),
      memoryEnabled: Boolean((payload.settings as Record<string, unknown>).memory_enabled),
      fileEnabled: Boolean((payload.settings as Record<string, unknown>).file_enabled),
      internetEnabled: Boolean((payload.settings as Record<string, unknown>).internet_enabled),
      personaProfile: String((payload.settings as Record<string, unknown>).persona_profile),
      providerPosture: String((payload.settings as Record<string, unknown>).provider_posture),
      qualityProfile:
        (payload.settings as Record<string, unknown>).quality_profile === 'fast-turnaround' ||
        (payload.settings as Record<string, unknown>).quality_profile === 'premium'
          ? ((payload.settings as Record<string, unknown>).quality_profile as
              | 'fast-turnaround'
              | 'premium')
          : 'standard',
      sources: ((payload.settings as Record<string, unknown>).sources as Record<string, string>) ?? {},
    },
    memoryCards: payload.memory_cards as ConversationDetailPayload['memoryCards'],
  }
}

function normalizeConversationSettingsResponse(
  payload: Record<string, unknown>,
): ConversationDetailPayload['settings'] {
  return {
    activeWorkMode:
      payload.active_work_mode === 'quick' ||
      payload.active_work_mode === 'deep' ||
      payload.active_work_mode === 'execute'
        ? (payload.active_work_mode as 'quick' | 'deep' | 'execute')
        : 'auto',
    responseStyle:
      payload.resolved_response_style == null ? null : String(payload.resolved_response_style),
    memoryEnabled: Boolean(payload.resolved_memory_enabled),
    fileEnabled: Boolean(payload.resolved_file_enabled),
    internetEnabled: Boolean(payload.resolved_internet_enabled),
    personaProfile: String(payload.resolved_persona_profile ?? 'strategist'),
    providerPosture: String(payload.resolved_provider_posture ?? 'prefer-local'),
    qualityProfile: 'standard',
    sources: {
      active_work_mode: String(payload.active_work_mode_source ?? 'default'),
      response_style: String(payload.resolved_response_style_source ?? 'default'),
      memory_enabled: String(payload.resolved_memory_enabled_source ?? 'default'),
      file_enabled: String(payload.resolved_file_enabled_source ?? 'default'),
      internet_enabled: String(payload.resolved_internet_enabled_source ?? 'default'),
      persona_profile: String(payload.resolved_persona_profile_source ?? 'default'),
      provider_posture: String(payload.resolved_provider_posture_source ?? 'default'),
      quality_profile: 'default',
    },
  }
}

export async function updateConversationSettings(
  conversationId: number,
  payload: ConversationSettingsUpdatePayload,
): Promise<ConversationDetailPayload['settings']> {
  const response = await fetchJson<Record<string, unknown>>(`/conversations/${conversationId}/settings`, {
    method: 'PUT',
    body: JSON.stringify({
      active_work_mode: payload.activeWorkMode,
      response_style_override: payload.responseStyleOverride,
      memory_enabled_override: payload.memoryEnabledOverride,
      file_enabled_override: payload.fileEnabledOverride,
      internet_enabled_override: payload.internetEnabledOverride,
    }),
  })
  return normalizeConversationSettingsResponse(response)
}

export async function archiveConversation(
  conversationId: number,
  payload?: { projectId?: number | null },
): Promise<{ conversationId: number; archived: boolean }> {
  const response = await fetchJson<Record<string, unknown>>(`/conversations/${conversationId}`, {
    method: 'DELETE',
    body: JSON.stringify({
      project_id: payload?.projectId ?? null,
    }),
  })
  return {
    conversationId: Number(response.conversation_id),
    archived: Boolean(response.archived),
  }
}

export async function sendChatTurn(
  payload: ChatRequestPayload,
  options?: { signal?: AbortSignal },
): Promise<ChatTurnResponse> {
  const response = await fetchJson<Record<string, unknown>>('/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal: options?.signal,
  })
  return normalizeChatTurnResponse(response)
}

export type ChatStreamEvent =
  | { type: 'conversation'; conversation_id: number; conversation_title: string; user_message_id: number }
  | { type: 'status'; stage?: string; title?: string; detail?: string }
  | { type: 'token'; text: string; turn?: number }
  | { type: 'tool_start'; name: string; arguments?: Record<string, unknown>; turn?: number }
  | { type: 'tool_result'; name: string; status?: string; summary?: string; turn?: number }
  | { type: 'done'; response: Record<string, unknown> }
  | { type: 'error'; status_code?: number; detail?: unknown }

export async function sendChatTurnStream(
  payload: ChatRequestPayload,
  options: {
    signal?: AbortSignal
    onEvent: (event: ChatStreamEvent) => void
  },
): Promise<ChatTurnResponse> {
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: options.signal,
  })
  if (!response.ok) {
    const detail = await response.text()
    let message = detail
    try {
      message = apiErrorMessage(JSON.parse(detail))
    } catch {
      message = detail
    }
    throw new ApiRequestError(message || 'Streaming chat failed.', response.status)
  }
  if (response.body == null) {
    throw new ApiRequestError('Streaming chat returned an empty body.', 502)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let donePayload: Record<string, unknown> | null = null
  let streamError: ApiRequestError | null = null

  const flushEvent = (raw: string) => {
    const trimmed = raw.trim()
    if (!trimmed.startsWith('data:')) {
      return
    }
    const data = trimmed.slice(5).trim()
    if (!data) {
      return
    }
    let parsed: ChatStreamEvent
    try {
      parsed = JSON.parse(data) as ChatStreamEvent
    } catch {
      return
    }
    options.onEvent(parsed)
    if (parsed.type === 'done' && parsed.response && typeof parsed.response === 'object') {
      donePayload = parsed.response
    }
    if (parsed.type === 'error') {
      const detail =
        typeof parsed.detail === 'string'
          ? parsed.detail
          : apiErrorMessage(parsed.detail) || 'Streaming chat failed.'
      streamError = new ApiRequestError(detail, Number(parsed.status_code ?? 500))
    }
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() ?? ''
    for (const chunk of chunks) {
      flushEvent(chunk)
    }
  }
  if (buffer.trim()) {
    flushEvent(buffer)
  }
  if (streamError != null) {
    throw streamError
  }
  if (donePayload == null) {
    throw new ApiRequestError('Streaming chat ended without a final response.', 502)
  }
  return normalizeChatTurnResponse(donePayload)
}

function normalizeChatTurnResponse(response: Record<string, unknown>): ChatTurnResponse {
  const decision =
    response.skill_decision && typeof response.skill_decision === 'object'
      ? (response.skill_decision as Record<string, unknown>)
      : null
  const traces = Array.isArray(response.traces) ? response.traces : []
  const instructionContext = traces
    .map((trace) =>
      trace && typeof trace === 'object'
        ? (trace as Record<string, unknown>).metadata_json
        : null,
    )
    .filter((metadata): metadata is Record<string, unknown> => Boolean(metadata && typeof metadata === 'object'))
    .map((metadata) => metadata.instruction_context)
    .find((context) => context && typeof context === 'object') as Record<string, unknown> | undefined
  const skillExecution: ChatSkillExecution | null = decision
    ? {
        selectedSkills: Array.isArray(decision.selected_skills)
          ? decision.selected_skills.filter((item): item is string => typeof item === 'string')
          : [],
        reasoningSummary: String(decision.reasoning_summary ?? ''),
        instructionStatus: String(instructionContext?.instruction_status ?? 'unknown'),
        loadedInstructionSkills: Array.isArray(instructionContext?.loaded_instructions)
          ? instructionContext.loaded_instructions
              .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
              .map((item) => item.skill_id)
              .filter((item): item is string => typeof item === 'string')
          : [],
        missingInstructionSkills: Array.isArray(instructionContext?.missing_instruction_skills)
          ? instructionContext.missing_instruction_skills.filter((item): item is string => typeof item === 'string')
          : [],
      }
    : null
  return {
    conversationId: Number(response.conversation_id),
    conversationTitle: String(response.conversation_title),
    conversationProjectId: response.conversation_project_id == null ? null : Number(response.conversation_project_id),
    userMessageId: Number(response.user_message_id),
    assistantMessageId: Number(response.assistant_message_id),
    userMessage: String(response.user_message ?? ''),
    userMessageAttachmentOnly: Boolean(response.user_message_attachment_only),
    assistantMessage: String(response.assistant_message ?? ''),
    selectedProviderKey: String(response.selected_provider_key ?? 'ollama-local'),
    selectedModelKey: String(response.selected_model_key ?? 'unknown'),
    routingReason: String(response.routing_reason ?? ''),
    assistantMessageMetadataJson:
      response.assistant_message_metadata_json &&
      typeof response.assistant_message_metadata_json === 'object'
        ? (response.assistant_message_metadata_json as Record<string, unknown>)
        : {},
    skillExecution,
    runtimeError: normalizeRuntimeErrorDetail(
      response.runtime_error as Record<string, unknown> | null | undefined,
    ),
  }
}

export async function answerQuestion(
  payload: AnswerQuestionRequest,
): Promise<AnswerQuestionResponse> {
  const response = await fetchJson<Record<string, unknown>>('/agent/answer-question', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: payload.conversationId,
      assistant_message_id: payload.assistantMessageId,
      question_id: payload.questionId,
      selected_ids: payload.selectedIds,
    }),
  })

  return {
    ok: Boolean(response.ok),
    assistantMessageId: Number(response.assistant_message_id),
    userMessageId: Number(response.user_message_id),
    nextAssistantMessageId:
      response.next_assistant_message_id == null
        ? null
        : Number(response.next_assistant_message_id),
  }
}

function normalizeRuntimeErrorDetail(
  payload: Record<string, unknown> | null | undefined,
): RuntimeErrorDetail | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }
  return {
    failureType: String(payload.failure_type ?? 'unknown') as RuntimeErrorDetail['failureType'],
    providerKey: String(payload.provider_key ?? ''),
    modelKey: String(payload.model_key ?? ''),
    runtimeModelName:
      payload.runtime_model_name == null ? null : String(payload.runtime_model_name),
    userTitle: String(payload.user_title ?? ''),
    userDetail: String(payload.user_detail ?? ''),
    retryable: Boolean(payload.retryable),
    canFallback: Boolean(payload.can_fallback),
    suggestedActions: Array.isArray(payload.suggested_actions)
      ? payload.suggested_actions.map((item) => String(item))
      : [],
    diagnostics:
      payload.diagnostics && typeof payload.diagnostics === 'object'
        ? (payload.diagnostics as Record<string, unknown>)
        : null,
  }
}

function normalizeChatAttachmentPayload(payload: Record<string, unknown>): ChatAttachmentPayload {
  return {
    filename: String(payload.filename ?? ''),
    storage_path: String(payload.storage_path ?? ''),
    kind: String(payload.kind ?? 'other'),
    detected_kind: payload.detected_kind == null ? null : String(payload.detected_kind),
    understanding_status:
      payload.understanding_status == null ? null : String(payload.understanding_status),
    understanding_reason:
      payload.understanding_reason == null ? null : String(payload.understanding_reason),
    preview_text: payload.preview_text == null ? null : String(payload.preview_text),
    truncated: Boolean(payload.truncated),
    media_type: payload.media_type == null ? null : String(payload.media_type),
    file_extension: payload.file_extension == null ? null : String(payload.file_extension),
    size_bytes: payload.size_bytes == null ? null : Number(payload.size_bytes),
    extracted_text: payload.extracted_text == null ? null : String(payload.extracted_text),
  }
}

export async function uploadChatFile(file: File): Promise<ChatAttachmentPayload> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetchJson<Record<string, unknown>>('/uploads', {
    method: 'POST',
    body: formData,
  })
  return normalizeChatAttachmentPayload(response)
}

export async function transcribeVoice(
  audio: Blob,
  options?: {
    filename?: string
    language?: string | null
    conversationId?: number | null
    transcriptHint?: string | null
  },
): Promise<VoiceTranscriptionPayload> {
  const formData = new FormData()
  const filename = options?.filename ?? 'push-to-talk.webm'
  formData.append('audio', audio, filename)
  if (options?.language) {
    formData.append('language', options.language)
  }
  if (options?.conversationId != null) {
    formData.append('conversation_id', String(options.conversationId))
  }
  if (options?.transcriptHint) {
    formData.append('transcript_hint', options.transcriptHint)
  }

  const response = await fetchJson<Record<string, unknown>>('/voice/transcriptions', {
    method: 'POST',
    body: formData,
  })

  return {
    transcript: String(response.transcript ?? ''),
    language: String(response.language ?? options?.language ?? 'pl'),
    engine: String(response.engine ?? 'unknown'),
    durationMs: Number(response.duration_ms ?? 0),
  }
}

export async function deleteUpload(storagePath: string): Promise<void> {
  await fetchJson<Record<string, unknown>>('/uploads', {
    method: 'DELETE',
    body: JSON.stringify({
      storage_path: storagePath,
    }),
  })
}

export async function createChatBriefing(payload?: {
  conversationId?: number | null
  projectId?: number | null
}): Promise<ChatBriefingResponse> {
  const query = new URLSearchParams()
  if (payload?.conversationId != null) {
    query.set('conversation_id', String(payload.conversationId))
  }
  if (payload?.projectId != null) {
    query.set('project_id', String(payload.projectId))
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : ''
  const response = await fetchJson<Record<string, unknown>>(`/chat/briefing${suffix}`, {
    method: 'POST',
  })
  return {
    conversationId: Number(response.conversation_id),
    conversationTitle: String(response.conversation_title ?? 'Briefing'),
    assistantMessageId: Number(response.assistant_message_id),
    content: String(response.content ?? ''),
    metadataJson: normalizeBriefingMetadata(
      (response.metadata_json as Record<string, unknown> | null | undefined) ?? {},
    ),
  }
}

function normalizeBriefingMetadata(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const kind = String(payload.kind ?? '')
  if (kind !== 'briefing') {
    return payload
  }
  const rawPayload = payload.payload
  if (!rawPayload || typeof rawPayload !== 'object') {
    return payload
  }
  return {
    ...payload,
    payload: normalizeBriefingPayload(rawPayload as Record<string, unknown>),
  }
}

function normalizeBriefingPayload(payload: Record<string, unknown>): BriefingPayload {
  return {
    generatedAt: String(payload.generated_at ?? ''),
    projectId: payload.project_id == null ? null : Number(payload.project_id),
    sections: Array.isArray(payload.sections)
      ? payload.sections
          .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
          .map((item) => ({
            kind: String(item.kind ?? 'overview'),
            title: String(item.title ?? 'Briefing'),
            summary: String(item.summary ?? ''),
            items: Array.isArray(item.items)
              ? item.items
                  .filter(
                    (entry): entry is Record<string, unknown> =>
                      Boolean(entry) && typeof entry === 'object',
                  )
                  .map((entry) => ({
                    label: String(entry.label ?? ''),
                    detail: String(entry.detail ?? ''),
                    urgency:
                      entry.urgency === 'high' || entry.urgency === 'medium'
                        ? (entry.urgency as 'high' | 'medium')
                        : 'low',
                    actionUrl: entry.action_url == null ? null : String(entry.action_url),
                    metadata:
                      entry.metadata && typeof entry.metadata === 'object'
                        ? (entry.metadata as Record<string, unknown>)
                        : {},
                  }))
              : [],
          }))
      : [],
  }
}

function normalizeDecisionLogEntry(payload: Record<string, unknown>): DecisionLogEntryView {
  return {
    decisionId: Number(payload.decision_id),
    conversationId: payload.conversation_id == null ? null : Number(payload.conversation_id),
    projectId: payload.project_id == null ? null : Number(payload.project_id),
    kind: String(payload.kind ?? 'decision') as DecisionLogEntryView['kind'],
    summary: String(payload.summary ?? ''),
    madeAt: String(payload.made_at ?? ''),
    status: String(payload.status ?? 'active') as DecisionLogEntryView['status'],
    dueAt: payload.due_at == null ? null : String(payload.due_at),
    resolvedAt: payload.resolved_at == null ? null : String(payload.resolved_at),
    resolutionSummary: payload.resolution_summary == null ? null : String(payload.resolution_summary),
    evidenceMessageIds: Array.isArray(payload.evidence_message_ids)
      ? payload.evidence_message_ids
          .map((value) => Number(value))
          .filter((value) => Number.isFinite(value))
      : [],
  }
}

export async function fetchChatDecisions(payload?: {
  projectId?: number | null
}): Promise<ChatDecisionListResponse> {
  const query = new URLSearchParams()
  if (payload?.projectId != null) {
    query.set('project_id', String(payload.projectId))
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : ''
  const response = await fetchJson<Record<string, unknown>>(`/chat/decisions${suffix}`)
  return {
    entries: Array.isArray(response.entries)
      ? response.entries
          .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
          .map(normalizeDecisionLogEntry)
      : [],
    overdueCount: Number(response.overdue_count ?? 0),
  }
}

export async function resolveDecisionLogEntry(
  decisionId: number,
  resolutionSummary?: string | null,
): Promise<DecisionLogEntryView> {
  const response = await fetchJson<Record<string, unknown>>(`/chat/decisions/${decisionId}/resolve`, {
    method: 'POST',
    body: JSON.stringify({
      resolution_summary: resolutionSummary ?? null,
    }),
  })
  return normalizeDecisionLogEntry(response)
}

function normalizeScheduledTaskDescriptor(payload: Record<string, unknown>): ScheduledTaskDescriptor {
  return {
    scheduledTaskId: Number(payload.scheduled_task_id),
    conversationId: payload.conversation_id == null ? null : Number(payload.conversation_id),
    projectId: payload.project_id == null ? null : Number(payload.project_id),
    projectName: payload.project_name == null ? null : String(payload.project_name),
    userId: payload.user_id == null ? null : Number(payload.user_id),
    promptTemplate: String(payload.prompt_template ?? ''),
    cronSpec: String(payload.cron_spec ?? ''),
    nextRunAt: payload.next_run_at == null ? null : String(payload.next_run_at),
    lastRunAt: payload.last_run_at == null ? null : String(payload.last_run_at),
    lastRunStatus: payload.last_run_status == null ? null : String(payload.last_run_status),
    isActive: Boolean(payload.is_active),
    createdAt: String(payload.created_at ?? ''),
    updatedAt: String(payload.updated_at ?? ''),
  }
}

export async function fetchScheduledTasks(projectId?: number | null): Promise<ScheduledTaskDescriptor[]> {
  const suffix = projectId ? `?project_id=${projectId}` : ''
  const payload = await fetchJson<Record<string, unknown>>(`/agent/scheduled-tasks${suffix}`)
  return Array.isArray(payload.scheduled_tasks)
    ? (payload.scheduled_tasks as Array<Record<string, unknown>>).map(normalizeScheduledTaskDescriptor)
    : []
}

export async function toggleScheduledTask(
  taskId: number,
  payload: { isActive: boolean },
): Promise<ScheduledTaskDescriptor> {
  const response = await fetchJson<Record<string, unknown>>(`/agent/scheduled-tasks/${taskId}`, {
    method: 'PATCH',
    body: JSON.stringify({
      is_active: payload.isActive,
    }),
  })
  return normalizeScheduledTaskDescriptor(response.scheduled_task as Record<string, unknown>)
}

export async function deleteScheduledTask(taskId: number): Promise<void> {
  await fetchJson<Record<string, unknown>>(`/agent/scheduled-tasks/${taskId}`, {
    method: 'DELETE',
  })
}

export async function previewAgentCapability(payload: {
  action: string
  conversationId?: number | null
  projectId?: number | null
  payload?: Record<string, unknown>
}): Promise<AgentCapabilityPreview> {
  const response = await fetchJson<Record<string, unknown>>('/agent/preview', {
    method: 'POST',
    body: JSON.stringify({
      action: payload.action,
      conversation_id: payload.conversationId,
      project_id: payload.projectId,
      payload: payload.payload ?? {},
    }),
  })
  const preview = response.preview as Record<string, unknown>
  return {
    action: String(preview.action ?? ''),
    title: String(preview.title ?? ''),
    summary: String(preview.summary ?? ''),
    payload:
      preview.payload && typeof preview.payload === 'object'
        ? (preview.payload as Record<string, unknown>)
        : {},
    requiresConfirmation: Boolean(preview.requires_confirmation),
  }
}

export async function executeAgentCapability(payload: {
  action: string
  conversationId?: number | null
  projectId?: number | null
  payload?: Record<string, unknown>
}): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>('/agent/execute', {
    method: 'POST',
    body: JSON.stringify({
      action: payload.action,
      conversation_id: payload.conversationId,
      project_id: payload.projectId,
      payload: payload.payload ?? {},
    }),
  })
}

export async function fetchProviderCards(): Promise<ProviderCard[]> {
  const payload = await fetchJson<Record<string, unknown>[]>('/settings/provider-cards')
  return payload.map((item) => normalizeProviderCard(item))
}

export async function fetchSettingsControlCenter(): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center'),
  )
}

function normalizeOperationalMemoryWorkItem(
  payload: Record<string, unknown>,
): OperationalMemoryWorkItem {
  return {
    id: Number(payload.id ?? 0),
    createdAt: String(payload.created_at ?? ''),
    updatedAt: String(payload.updated_at ?? ''),
    projectId: payload.project_id == null ? null : Number(payload.project_id),
    sourceNoteId: payload.source_note_id == null ? null : Number(payload.source_note_id),
    title: String(payload.title ?? ''),
    summary: String(payload.summary ?? ''),
    nextStep: payload.next_step == null ? null : String(payload.next_step),
    status: String(payload.status ?? 'active') as OperationalMemoryWorkItemStatus,
    priority: String(payload.priority ?? 'P1') as OperationalMemoryWorkItemPriority,
    reviewState: String(
      payload.review_state ?? 'unreviewed',
    ) as OperationalMemoryWorkItemReviewState,
    blockedReason:
      payload.blocked_reason == null ? null : String(payload.blocked_reason),
    sourceNote:
      payload.source_note && typeof payload.source_note === 'object'
        ? normalizeOperationalMemorySourceNote(payload.source_note as Record<string, unknown>)
        : null,
    decisionContext:
      payload.decision_context && typeof payload.decision_context === 'object'
        ? {
            activeCount: Number(
              (payload.decision_context as Record<string, unknown>).active_count ?? 0,
            ),
            latestSummary:
              (payload.decision_context as Record<string, unknown>).latest_summary == null
                ? null
                : String(
                    (payload.decision_context as Record<string, unknown>).latest_summary,
                  ),
            latestKind:
              (payload.decision_context as Record<string, unknown>).latest_kind == null
                ? null
                : (String(
                    (payload.decision_context as Record<string, unknown>).latest_kind,
                  ) as OperationalMemoryDecisionKind),
            latestDueAt:
              (payload.decision_context as Record<string, unknown>).latest_due_at == null
                ? null
                : String((payload.decision_context as Record<string, unknown>).latest_due_at),
            recentEntries: Array.isArray(
              (payload.decision_context as Record<string, unknown>).recent_entries,
            )
              ? ((payload.decision_context as Record<string, unknown>).recent_entries as Array<
                  Record<string, unknown>
                >).map(normalizeOperationalMemoryDecisionEntry)
              : [],
          }
        : null,
  }
}

function normalizeOperationalMemorySourceNote(
  payload: Record<string, unknown>,
): OperationalMemorySourceNote {
  return {
    id: Number(payload.id ?? 0),
    source: String(payload.source ?? ''),
    view: String(payload.view ?? ''),
    friction: String(payload.friction ?? ''),
    severity: String(payload.severity ?? 'P1') as OperationalMemoryWorkItemPriority,
    proposedFix: payload.proposed_fix == null ? null : String(payload.proposed_fix),
    chosenNextPatch:
      payload.chosen_next_patch == null ? null : String(payload.chosen_next_patch),
    decision: String(payload.decision ?? 'undecided') as OperationalMemorySourceNoteDecision,
    status: String(payload.status ?? 'open') as OperationalMemorySourceNoteStatus,
  }
}

function normalizeOperationalMemoryDecisionEntry(
  payload: Record<string, unknown>,
): OperationalMemoryDecisionEntry {
  return {
    id: Number(payload.id ?? 0),
    conversationId:
      payload.conversation_id == null ? null : Number(payload.conversation_id),
    projectId: payload.project_id == null ? null : Number(payload.project_id),
    kind: String(payload.kind ?? 'decision') as OperationalMemoryDecisionKind,
    summary: String(payload.summary ?? ''),
    madeAt: String(payload.made_at ?? ''),
    status: String(payload.status ?? 'active') as OperationalMemoryDecisionStatus,
    dueAt: payload.due_at == null ? null : String(payload.due_at),
    resolvedAt: payload.resolved_at == null ? null : String(payload.resolved_at),
    resolutionSummary:
      payload.resolution_summary == null ? null : String(payload.resolution_summary),
    evidenceMessageIds: Array.isArray(payload.evidence_message_ids)
      ? payload.evidence_message_ids
          .map((value) => Number(value))
          .filter((value) => Number.isFinite(value))
      : [],
  }
}

function normalizeOperationalMemorySnapshot(
  payload: Record<string, unknown>,
): OperationalMemorySnapshot {
  return {
    projectId: payload.project_id == null ? null : Number(payload.project_id),
    sourceNote:
      payload.source_note && typeof payload.source_note === 'object'
        ? normalizeOperationalMemorySourceNote(
            payload.source_note as Record<string, unknown>,
          )
        : null,
    workItems: Array.isArray(payload.work_items)
      ? payload.work_items
          .filter(
            (item): item is Record<string, unknown> =>
              Boolean(item) && typeof item === 'object',
          )
          .map(normalizeOperationalMemoryWorkItem)
      : [],
    activeDecisions: Array.isArray(payload.active_decisions)
      ? payload.active_decisions
          .filter(
            (item): item is Record<string, unknown> =>
              Boolean(item) && typeof item === 'object',
          )
          .map(normalizeOperationalMemoryDecisionEntry)
      : [],
  }
}

function normalizeOperationalMemoryPromotionDraft(
  payload: Record<string, unknown>,
): OperationalMemoryPromotionDraft {
  return {
    sourceNoteId: Number(payload.source_note_id ?? 0),
    title: String(payload.title ?? ''),
    summary: String(payload.summary ?? ''),
    nextStep: payload.next_step == null ? null : String(payload.next_step),
    priority: String(payload.priority ?? 'P1') as OperationalMemoryWorkItemPriority,
  }
}

function normalizeOperationalMemoryPromotionPreview(
  payload: Record<string, unknown>,
): OperationalMemoryPromotionPreview {
  return {
    projectId: Number(payload.project_id ?? 0),
    note:
      payload.note && typeof payload.note === 'object'
        ? normalizeOperationalMemorySourceNote(payload.note as Record<string, unknown>)
        : null,
    workItemDraft:
      payload.work_item_draft && typeof payload.work_item_draft === 'object'
        ? normalizeOperationalMemoryPromotionDraft(
            payload.work_item_draft as Record<string, unknown>,
          )
        : null,
    eligible: Boolean(payload.eligible),
    reason: payload.reason == null ? null : String(payload.reason),
    warnings: Array.isArray(payload.warnings)
      ? payload.warnings.map((value) => String(value))
      : [],
  }
}

export async function fetchOperationalMemorySnapshot(payload: {
  projectId?: number | null
  sourceNoteId?: number | null
}): Promise<OperationalMemorySnapshot> {
  const query = new URLSearchParams()
  if (payload.projectId != null) {
    query.set('project_id', String(payload.projectId))
  }
  if (payload.sourceNoteId != null) {
    query.set('source_note_id', String(payload.sourceNoteId))
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : ''
  return normalizeOperationalMemorySnapshot(
    await fetchJson<Record<string, unknown>>(`/operational-memory/snapshot${suffix}`),
  )
}

export async function previewOperationalMemoryPromotion(payload: {
  projectId: number
  noteId?: number | null
}): Promise<OperationalMemoryPromotionPreview> {
  return normalizeOperationalMemoryPromotionPreview(
    await fetchJson<Record<string, unknown>>('/operational-memory/promotions/preview', {
      method: 'POST',
      body: JSON.stringify({
        project_id: payload.projectId,
        note_id: payload.noteId ?? null,
      }),
    }),
  )
}

export async function promoteOperationalMemorySelectedNote(payload: {
  projectId: number
  noteId?: number | null
}): Promise<OperationalMemoryWorkItem> {
  return normalizeOperationalMemoryWorkItem(
    await fetchJson<Record<string, unknown>>('/operational-memory/promotions', {
      method: 'POST',
      body: JSON.stringify({
        project_id: payload.projectId,
        note_id: payload.noteId ?? null,
      }),
    }),
  )
}

function normalizeJarvisBrowserSessionSummary(
  payload: Record<string, unknown>,
): JarvisBrowserSessionSummary {
  return {
    service: String(payload.service ?? ''),
    displayName: String(payload.display_name ?? payload.service ?? ''),
    loginUrl: String(payload.login_url ?? ''),
    validationUrl: String(payload.validation_url ?? ''),
    storageStatePath: String(payload.storage_state_path ?? ''),
    ready: Boolean(payload.ready),
    expired: Boolean(payload.expired),
    pendingManualLogin: Boolean(payload.pending_manual_login),
    hasStorageState: Boolean(payload.has_storage_state),
    loginWindowOpen: Boolean(payload.login_window_open),
    statusReason: String(payload.status_reason ?? ''),
    semaphoreFree: payload.semaphore_free == null ? null : Number(payload.semaphore_free),
  }
}

export async function fetchJarvisBrowserSessions(): Promise<JarvisBrowserSessionSummary[]> {
  const payload = await fetchJson<Record<string, unknown>>('/jarvis/browser-sessions')
  return Array.isArray(payload.sessions)
    ? payload.sessions.map((item) =>
        normalizeJarvisBrowserSessionSummary(item as Record<string, unknown>),
      )
    : []
}

export async function startJarvisBrowserSessionManualLogin(
  service: string,
): Promise<{ ok: boolean; session: JarvisBrowserSessionSummary }> {
  const payload = await fetchJson<Record<string, unknown>>(
    `/jarvis/browser-sessions/${encodeURIComponent(service)}/manual-login/start`,
    {
      method: 'POST',
    },
  )
  return {
    ok: Boolean(payload.ok ?? true),
    session: normalizeJarvisBrowserSessionSummary(
      (payload.session as Record<string, unknown> | null) ?? {},
    ),
  }
}

export async function startJarvisBrowserSessionManualLoginForUrl(payload: {
  service: string
  displayName: string
  loginUrl: string
  validationUrl?: string | null
}): Promise<{ ok: boolean; session: JarvisBrowserSessionSummary }> {
  const response = await fetchJson<Record<string, unknown>>(
    '/jarvis/browser-sessions/manual-login/start',
    {
      method: 'POST',
      body: JSON.stringify({
        service: payload.service,
        display_name: payload.displayName,
        login_url: payload.loginUrl,
        validation_url: payload.validationUrl ?? null,
      }),
    },
  )
  return {
    ok: Boolean(response.ok ?? true),
    session: normalizeJarvisBrowserSessionSummary(
      (response.session as Record<string, unknown> | null) ?? {},
    ),
  }
}

export async function confirmJarvisBrowserSessionManualLogin(
  service: string,
): Promise<{ ok: boolean; session: JarvisBrowserSessionSummary }> {
  const payload = await fetchJson<Record<string, unknown>>(
    `/jarvis/browser-sessions/${encodeURIComponent(service)}/manual-login/confirm`,
    {
      method: 'POST',
    },
  )
  return {
    ok: Boolean(payload.ok),
    session: normalizeJarvisBrowserSessionSummary(
      (payload.session as Record<string, unknown> | null) ?? {},
    ),
  }
}

export async function startOAuthConnection(
  connectorKey: string,
  input?: { contentAccountId?: string | null },
): Promise<{
  authorizationUrl: string
  connectorKey: string
  status: string
  scopes: string[]
  connectionId: number
  contentAccountId?: string | null
}> {
  const payload = await fetchJson<Record<string, unknown>>(`/oauth/${connectorKey}/start`, {
    method: 'POST',
    body: input?.contentAccountId ? JSON.stringify({ content_account_id: input.contentAccountId }) : undefined,
  })
  return {
    authorizationUrl: String(payload.authorization_url ?? ''),
    connectorKey: String(payload.connector_key ?? connectorKey),
    status: String(payload.status ?? 'pending'),
    scopes: Array.isArray(payload.scopes) ? (payload.scopes as string[]) : [],
    connectionId: Number(payload.connection_id ?? 0),
    contentAccountId: payload.content_account_id == null ? null : String(payload.content_account_id),
  }
}

export async function configureYouTubeOAuthRuntime(input: {
  clientId: string
  clientSecret?: string
  redirectBaseUrl?: string
  enableOAuth?: boolean
}): Promise<{
  configured: boolean
  clientSecretRef: string | null
  secretStored: boolean
  envPath: string
}> {
  const payload = await fetchJson<Record<string, unknown>>('/oauth/youtube-studio/runtime-config', {
    method: 'POST',
    body: JSON.stringify({
      client_id: input.clientId,
      client_secret: input.clientSecret?.trim() ? input.clientSecret : undefined,
      redirect_base_url: input.redirectBaseUrl ?? 'http://127.0.0.1:8000',
      enable_oauth: input.enableOAuth ?? true,
    }),
  })
  const connector = (payload.connector as Record<string, unknown> | undefined) ?? {}
  return {
    configured: Boolean(connector.configured),
    clientSecretRef: payload.client_secret_ref == null ? null : String(payload.client_secret_ref),
    secretStored: Boolean(payload.secret_stored),
    envPath: String(payload.env_path ?? ''),
  }
}

export async function importYouTubeOAuthClientSecret(input: {
  clientSecretPath?: string
  redirectBaseUrl?: string
  enableOAuth?: boolean
}): Promise<{
  configured: boolean
  clientSecretRef: string | null
  clientId: string | null
  clientSecretPath: string
  redirectUri: string
  envPath: string
  authorizationStartEndpoint: string
  warnings: string[]
}> {
  const payload = await fetchJson<Record<string, unknown>>('/oauth/youtube-studio/import-client-secret', {
    method: 'POST',
    body: JSON.stringify({
      client_secret_path: input.clientSecretPath?.trim() || undefined,
      redirect_base_url: input.redirectBaseUrl ?? 'http://127.0.0.1:8000',
      enable_oauth: input.enableOAuth ?? true,
    }),
  })
  const connector = (payload.connector as Record<string, unknown> | undefined) ?? {}
  return {
    configured: Boolean(connector.configured),
    clientSecretRef: payload.client_secret_ref == null ? null : String(payload.client_secret_ref),
    clientId: payload.client_id == null ? null : String(payload.client_id),
    clientSecretPath: String(payload.client_secret_path ?? ''),
    redirectUri: String(payload.redirect_uri ?? ''),
    envPath: String(payload.env_path ?? ''),
    authorizationStartEndpoint: String(payload.authorization_start_endpoint ?? ''),
    warnings: Array.isArray(payload.warnings) ? (payload.warnings as string[]) : [],
  }
}

export async function disconnectOAuthConnection(connectionId: number) {
  return fetchJson<Record<string, unknown>>(`/oauth/${connectionId}/disconnect`, {
    method: 'POST',
  })
}

export async function probeYouTubeConnector(accountId?: number | null) {
  const query = accountId ? `?account_id=${encodeURIComponent(String(accountId))}` : ''
  return fetchJson<Record<string, unknown>>(`/connectors/youtube/probe${query}`, {
    method: 'POST',
  })
}

export async function probeGitHubConnector() {
  return fetchJson<Record<string, unknown>>('/connectors/github/probe', {
    method: 'POST',
  })
}

export async function probeGoogleCalendarConnector() {
  return fetchJson<Record<string, unknown>>('/connectors/google-calendar/probe', {
    method: 'POST',
  })
}

export async function probeGmailConnector() {
  return fetchJson<Record<string, unknown>>('/connectors/gmail/probe', {
    method: 'POST',
  })
}

function normalizeActionDraft(payload: Record<string, unknown>): ActionDraft {
  const accountSummary = payload.account_summary as Record<string, unknown>
  return {
    id: Number(payload.id),
    accountId: Number(payload.account_id),
    integrationKey: String(payload.integration_key ?? ''),
    projectId: payload.project_id == null ? null : Number(payload.project_id),
    projectName: payload.project_name == null ? null : String(payload.project_name),
    actionType: String(payload.action_type ?? ''),
    actionTitle: String(payload.action_title ?? ''),
    actionSummary: String(payload.action_summary ?? ''),
    riskLevel: String(payload.risk_level ?? ''),
    status: String(payload.status ?? ''),
    requiresApproval: Boolean(payload.requires_approval),
    previewPayload:
      payload.preview_payload && typeof payload.preview_payload === 'object'
        ? (payload.preview_payload as Record<string, unknown>)
        : {},
    approvalNotes: payload.approval_notes == null ? null : String(payload.approval_notes),
    rejectionReason: payload.rejection_reason == null ? null : String(payload.rejection_reason),
    auditLog: Array.isArray(payload.audit_log)
      ? payload.audit_log.map((item) => ((item as Record<string, unknown>) ?? {}))
      : [],
    createdBy: String(payload.created_by ?? ''),
    approvedAt: payload.approved_at == null ? null : String(payload.approved_at),
    rejectedAt: payload.rejected_at == null ? null : String(payload.rejected_at),
    executedAt: payload.executed_at == null ? null : String(payload.executed_at),
    requiredCapabilities: Array.isArray(payload.required_capabilities)
      ? payload.required_capabilities.map((item) => String(item))
      : [],
    accountSummary: {
      id: Number(accountSummary.id ?? 0),
      label: String(accountSummary.label ?? ''),
      providerKey: String(accountSummary.provider_key ?? ''),
      accountIdentifier: String(accountSummary.account_identifier ?? ''),
      status: String(accountSummary.status ?? ''),
      riskLevel: String(accountSummary.risk_level ?? ''),
      maskedSecretState: String(accountSummary.masked_secret_state ?? ''),
      allowedCapabilities: Array.isArray(accountSummary.allowed_capabilities)
        ? accountSummary.allowed_capabilities.map((item) => String(item))
        : [],
    },
    createdAt: String(payload.created_at ?? ''),
    updatedAt: String(payload.updated_at ?? ''),
  }
}

export async function approveActionDraft(draftId: number): Promise<ActionDraft> {
  const payload = await fetchJson<Record<string, unknown>>(`/action-drafts/${draftId}/approve`, {
    method: 'POST',
  })
  return normalizeActionDraft(payload)
}

export async function rejectActionDraft(
  draftId: number,
  rejectionReason: string,
): Promise<ActionDraft> {
  const payload = await fetchJson<Record<string, unknown>>(`/action-drafts/${draftId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ rejection_reason: rejectionReason }),
  })
  return normalizeActionDraft(payload)
}

export async function executeActionDraft(
  draftId: number,
  payload: { confirm?: boolean } = {},
): Promise<ActionDraftExecutionResponse> {
  const response = await fetchJson<Record<string, unknown>>(`/action-drafts/${draftId}/execute`, {
    method: 'POST',
    body: JSON.stringify({ confirm: payload.confirm ?? false }),
  })
  return {
    actionDraft: normalizeActionDraft(response.action_draft as Record<string, unknown>),
    executionResult:
      response.execution_result && typeof response.execution_result === 'object'
        ? (response.execution_result as Record<string, unknown>)
        : {},
  }
}

export async function generateApplicationScaffoldPreview(payload: {
  appType: string
  language: string
  framework: string
  description: string
  targetDir: string
  projectId?: number | null
}): Promise<CodeGenerationPreviewResponse> {
  const response = await fetchJson<Record<string, unknown>>('/self-improvement/codegen/scaffold-preview', {
    method: 'POST',
    body: JSON.stringify({
      app_type: payload.appType,
      language: payload.language,
      framework: payload.framework,
      description: payload.description,
      target_dir: payload.targetDir,
      project_id: payload.projectId ?? null,
    }),
  })
  return {
    mode: String(response.mode ?? ''),
    actionDraftId: Number(response.action_draft_id),
    title: String(response.title ?? ''),
    summary: String(response.summary ?? ''),
    metadata: (response.metadata as Record<string, unknown>) ?? {},
  }
}

export async function editExistingAppPreview(payload: {
  projectRoot: string
  taskDescription: string
  filesToModify?: string[] | null
  projectId?: number | null
}): Promise<CodeGenerationPreviewResponse> {
  const response = await fetchJson<Record<string, unknown>>('/self-improvement/codegen/edit-preview', {
    method: 'POST',
    body: JSON.stringify({
      project_root: payload.projectRoot,
      task_description: payload.taskDescription,
      files_to_modify: payload.filesToModify ?? null,
      project_id: payload.projectId ?? null,
    }),
  })
  return {
    mode: String(response.mode ?? ''),
    actionDraftId: Number(response.action_draft_id),
    title: String(response.title ?? ''),
    summary: String(response.summary ?? ''),
    metadata: (response.metadata as Record<string, unknown>) ?? {},
  }
}

export async function executeCodeApplyDraft(draftId: number): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>(`/self-improvement/codegen/action-drafts/${draftId}/execute`, {
    method: 'POST',
  })
}

export async function runProjectTests(payload: {
  projectRoot: string
  timeoutS?: number
}): Promise<ProjectTestRunResponse> {
  const response = await fetchJson<Record<string, unknown>>('/self-improvement/codegen/run-tests', {
    method: 'POST',
    body: JSON.stringify({
      project_root: payload.projectRoot,
      timeout_s: payload.timeoutS ?? 300,
    }),
  })
  return {
    runner: String(response.runner ?? ''),
    command: Array.isArray(response.command) ? response.command.map((item) => String(item)) : [],
    cwd: String(response.cwd ?? ''),
    exitCode: Number(response.exit_code ?? 1),
    summary: String(response.summary ?? ''),
    firstFailures: Array.isArray(response.first_failures)
      ? response.first_failures.map((item) => String(item))
      : [],
    stdout: String(response.stdout ?? ''),
    stderr: String(response.stderr ?? ''),
  }
}

export async function listRemoteNodes(): Promise<RemoteWorkerSummary[]> {
  const payload = await fetchJson<Record<string, unknown>>('/remote-nodes')
  return Array.isArray(payload.remote_workers)
    ? payload.remote_workers.map((item) =>
        normalizeRemoteWorkerSummary(item as Record<string, unknown>),
      )
      : []
}

export async function fetchLocalHostCapabilities(): Promise<LocalHostControlSummary> {
  const payload = await fetchJson<Record<string, unknown>>('/local-host/capabilities')
  return normalizeLocalHostControlSummary(payload)
}

export async function runLocalHostCommand(payload: {
  command: string
  timeoutS?: number
  workdir?: string | null
}): Promise<LocalHostCommandResult> {
  const response = await fetchJson<Record<string, unknown>>('/local-host/run', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...localCapabilityHeaders(),
    },
    body: JSON.stringify({
      command: payload.command,
      timeout_s: payload.timeoutS ?? 120,
      workdir: payload.workdir ?? null,
    }),
  })
  return normalizeLocalHostCommandResult(response)
}

export async function dispatchLocalHostInput(payload: {
  action: 'move_mouse' | 'click_mouse' | 'scroll_mouse' | 'type_text' | 'press_keys'
  x?: number | null
  y?: number | null
  button?: 'left' | 'right' | 'middle'
  clicks?: number
  delta?: number
  text?: string | null
  keys?: string[] | null
}): Promise<LocalHostCommandResult> {
  const response = await fetchJson<Record<string, unknown>>('/local-host/input', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...localCapabilityHeaders(),
    },
    body: JSON.stringify({
      action: payload.action,
      x: payload.x ?? null,
      y: payload.y ?? null,
      button: payload.button ?? 'left',
      clicks: payload.clicks ?? 1,
      delta: payload.delta ?? 120,
      text: payload.text ?? null,
      keys: payload.keys ?? null,
    }),
  })
  return normalizeLocalHostCommandResult(response)
}

export async function openLocalHostDesktopApp(appKey: string): Promise<LocalHostCommandResult> {
  const response = await fetchJson<Record<string, unknown>>(`/local-host/apps/${encodeURIComponent(appKey)}/open`, {
    method: 'POST',
  })
  return normalizeLocalHostCommandResult(response)
}

export async function openLocalHostBrowserUrl(url: string): Promise<LocalHostCommandResult> {
  const response = await fetchJson<Record<string, unknown>>('/local-host/browser/open', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
  return normalizeLocalHostCommandResult(response)
}

export async function startLocalHostChromeBridge(payload?: {
  port?: number
  restartExisting?: boolean
  timeoutS?: number
}): Promise<LocalHostChromeBridgeResult> {
  const response = await fetchJson<Record<string, unknown>>('/local-host/browser/chrome-bridge/start', {
    method: 'POST',
    body: JSON.stringify({
      port: payload?.port ?? 9222,
      restart_existing: payload?.restartExisting ?? true,
      timeout_s: payload?.timeoutS ?? 20,
    }),
  })
  return {
    ok: Boolean(response.ok),
    status: String(response.status ?? 'launched') as LocalHostChromeBridgeResult['status'],
    port: Number(response.port ?? 9222),
    browserExecutable: String(response.browser_executable ?? ''),
    usingDefaultProfile: Boolean(response.using_default_profile),
    restartedExisting: Boolean(response.restarted_existing),
    command: Array.isArray(response.command) ? response.command.map((entry) => String(entry)) : [],
    notes: Array.isArray(response.notes) ? response.notes.map((entry) => String(entry)) : [],
    startedAt: String(response.started_at ?? ''),
    diagnostics: normalizeLocalHostChromeDiagnostics(
      (response.diagnostics as Record<string, unknown> | null) ?? null,
    ),
  }
}

export async function focusLocalHostDesktopApp(appKey: string): Promise<LocalHostCommandResult> {
  const response = await fetchJson<Record<string, unknown>>(`/local-host/apps/${encodeURIComponent(appKey)}/focus`, {
    method: 'POST',
  })
  return normalizeLocalHostCommandResult(response)
}

export async function bootstrapRemoteNode(
  payload: RemoteNodeBootstrapPayload,
): Promise<RemoteNodeBootstrapResult> {
  const response = await fetchJson<Record<string, unknown>>('/remote-nodes/bootstrap', {
    method: 'POST',
    body: JSON.stringify({
      host: payload.host,
      ssh_port: payload.sshPort ?? 22,
      user: payload.user,
      private_key_path: payload.privateKeyPath,
      control_plane_base_url: payload.controlPlaneBaseUrl,
      label: payload.label ?? null,
    }),
  })
  return {
    remoteWorker: normalizeRemoteWorkerSummary(
      response.remote_worker as Record<string, unknown>,
    ),
    bootstrapStatus: String(response.bootstrap_status ?? 'unknown'),
  }
}

export async function pauseRemoteNode(nodeId: number): Promise<RemoteWorkerSummary> {
  const response = await fetchJson<Record<string, unknown>>(`/remote-nodes/${nodeId}/pause`, {
    method: 'POST',
  })
  return normalizeRemoteWorkerSummary(response.remote_worker as Record<string, unknown>)
}

export async function resumeRemoteNode(nodeId: number): Promise<RemoteWorkerSummary> {
  const response = await fetchJson<Record<string, unknown>>(`/remote-nodes/${nodeId}/resume`, {
    method: 'POST',
  })
  return normalizeRemoteWorkerSummary(response.remote_worker as Record<string, unknown>)
}

export async function revokeRemoteNode(nodeId: number): Promise<RemoteWorkerSummary> {
  const response = await fetchJson<Record<string, unknown>>(`/remote-nodes/${nodeId}/revoke`, {
    method: 'POST',
  })
  return normalizeRemoteWorkerSummary(response.remote_worker as Record<string, unknown>)
}

export async function dispatchRemoteExecution(
  payload: RemoteExecutionDispatchPayload,
): Promise<RemoteExecutionRun> {
  const response = await fetchJson<Record<string, unknown>>('/remote-execution/dispatch', {
    method: 'POST',
    body: JSON.stringify({
      task_kind: payload.taskKind,
      payload: payload.payload,
      preferred_node_id: payload.preferredNodeId ?? null,
      origin: payload.origin ?? 'manual',
    }),
  })
  return normalizeRemoteExecutionRun(response.remote_execution_run as Record<string, unknown>)
}

export async function cancelRemoteExecution(runId: number): Promise<RemoteExecutionRun> {
  const response = await fetchJson<Record<string, unknown>>(
    `/remote-execution/${runId}/cancel`,
    {
      method: 'POST',
    },
  )
  return normalizeRemoteExecutionRun(response.remote_execution_run as Record<string, unknown>)
}

export async function updateSettingsProfile(payload: {
  displayName?: string
  handle?: string
  email?: string | null
  bio?: string | null
  locale?: string
  timezone?: string
}): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/profile', {
      method: 'PATCH',
      body: JSON.stringify({
        display_name: payload.displayName,
        handle: payload.handle,
        email: payload.email,
        bio: payload.bio,
        locale: payload.locale,
        timezone: payload.timezone,
      }),
    }),
  )
}

export async function updateSettingsStyle(payload: {
  responseLength?: 'short' | 'medium' | 'long'
  tone?: 'professional' | 'natural' | 'direct' | 'supportive'
  structure?: 'paragraphs' | 'bullets' | 'mixed'
  criticalityLevel?: number
  supportivenessLevel?: number
  professionalDefault?: boolean
}): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/style', {
      method: 'PATCH',
      body: JSON.stringify({
        response_length: payload.responseLength,
        tone: payload.tone,
        structure: payload.structure,
        criticality_level: payload.criticalityLevel,
        supportiveness_level: payload.supportivenessLevel,
        professional_default: payload.professionalDefault,
      }),
    }),
  )
}

export async function updateSettingsPolicy(payload: {
  automationLevel?: 'manual' | 'assisted' | 'auto'
  verificationMode?: 'off' | 'smart' | 'strict'
  allowLocalFileRead?: boolean
  allowLocalFileWrite?: boolean
  allowWebRead?: boolean
  allowExternalFallback?: boolean
  allowExternalAttachmentMetadata?: boolean
  allowExternalAttachmentContent?: boolean
  preferLocalLlm?: boolean
  defaultProviderPosture?: 'prefer-local' | 'improve-when-needed' | 'ask-before-external' | 'strict-local'
  defaultPersonaProfile?: 'strategist' | 'critic' | 'executor' | 'mentor' | 'jarvis-mode'
  jarvisAddressAs?: 'none' | 'first_name' | 'sir_madam'
  maxExternalAttachmentTextChars?: number | null
  maxDailyCost?: number | null
  maxMonthlyCost?: number | null
}): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/policy', {
      method: 'PATCH',
      body: JSON.stringify({
        automation_level: payload.automationLevel,
        verification_mode: payload.verificationMode,
        allow_local_file_read: payload.allowLocalFileRead,
        allow_local_file_write: payload.allowLocalFileWrite,
        allow_web_read: payload.allowWebRead,
        allow_external_fallback: payload.allowExternalFallback,
        allow_external_attachment_metadata: payload.allowExternalAttachmentMetadata,
        allow_external_attachment_content: payload.allowExternalAttachmentContent,
        prefer_local_llm: payload.preferLocalLlm,
        default_provider_posture: payload.defaultProviderPosture,
        default_persona_profile: payload.defaultPersonaProfile,
        jarvis_address_as: payload.jarvisAddressAs,
        max_external_attachment_text_chars: payload.maxExternalAttachmentTextChars,
        max_daily_cost: payload.maxDailyCost,
        max_monthly_cost: payload.maxMonthlyCost,
      }),
    }),
  )
}

export async function updateSettingsRuntime(payload: {
  browserControlEnabled?: boolean
  stealthHeadlessEnabled?: boolean
  browserAllowAnyUrl?: boolean
  externalActionsEnabled?: boolean
  schedulerEnabled?: boolean
  oauthEnabled?: boolean
  publicUploadEnabled?: boolean
  voiceSttEngine?: 'browser-speech' | 'whisper-local' | 'faster-whisper-local'
  voiceSttModelPath?: string | null
  voiceSttDevice?: string
  ambientListeningEnabled?: boolean
  ambientWakeWord?: string | null
  ambientMicDevice?: string | null
  ambientMuted?: boolean
  ambientContextEnabled?: boolean
  trendWatchEnabled?: boolean
  trendWatchQuery?: string | null
  trendWatchRegion?: string | null
  trendWatchIntervalMinutes?: number
  voiceRepliesEnabled?: boolean
  voiceReplyVolume?: number
  voiceReplyVoiceId?: number | null
}): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/runtime', {
      method: 'PATCH',
      body: JSON.stringify({
        browser_control_enabled: payload.browserControlEnabled,
        stealth_headless_enabled: payload.stealthHeadlessEnabled,
        browser_allow_any_url: payload.browserAllowAnyUrl,
        external_actions_enabled: payload.externalActionsEnabled,
        scheduler_enabled: payload.schedulerEnabled,
        oauth_enabled: payload.oauthEnabled,
        public_upload_enabled: payload.publicUploadEnabled,
        voice_stt_engine: payload.voiceSttEngine,
        voice_stt_model_path: payload.voiceSttModelPath,
        voice_stt_device: payload.voiceSttDevice,
        ambient_listening_enabled: payload.ambientListeningEnabled,
        ambient_wake_word: payload.ambientWakeWord,
        ambient_mic_device: payload.ambientMicDevice,
        ambient_muted: payload.ambientMuted,
        ambient_context_enabled: payload.ambientContextEnabled,
        trend_watch_enabled: payload.trendWatchEnabled,
        trend_watch_query: payload.trendWatchQuery,
        trend_watch_region: payload.trendWatchRegion,
        trend_watch_interval_minutes: payload.trendWatchIntervalMinutes,
        voice_replies_enabled: payload.voiceRepliesEnabled,
        voice_reply_volume: payload.voiceReplyVolume,
        voice_reply_voice_id: payload.voiceReplyVoiceId,
      }),
    }),
  )
}

export async function enableSettingsFullAccess(): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/runtime/full-access', {
      method: 'POST',
    }),
  )
}

export async function retestSettingsCreativeAssetLanes(): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/creative-assets/retest', {
      method: 'POST',
    }),
  )
}

export async function clearSettingsCreativeAssetLaneBlockers(): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/creative-assets/clear-blockers', {
      method: 'POST',
    }),
  )
}

export async function runSettingsTrendWatch(): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/trend-watch/run', {
      method: 'POST',
    }),
  )
}

export async function activateOmegaProtocol(): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/omega-protocol/activate', {
      method: 'POST',
    }),
  )
}

function normalizeAccountCredential(item: Record<string, unknown>): AccountCredentialResult {
  return {
    id: Number(item.id ?? 0),
    label: String(item.label ?? ''),
    providerKey: String(item.provider_key ?? ''),
    accountIdentifier: String(item.account_identifier ?? ''),
    secretType: String(item.secret_type ?? ''),
    status: String(item.status ?? ''),
    hasSecret: Boolean(item.has_secret),
    riskLevel: String(item.risk_level ?? 'medium'),
    allowedCapabilities: Array.isArray(item.allowed_capabilities)
      ? (item.allowed_capabilities as unknown[]).map((value) => String(value))
      : [],
    linkedIntegrationKey: item.linked_integration_key == null ? null : String(item.linked_integration_key),
    linkedProjectId: item.linked_project_id == null ? null : Number(item.linked_project_id),
    notes: item.notes == null ? null : String(item.notes),
    maskedSecretState: String(item.masked_secret_state ?? ''),
  }
}

export async function fetchAccountCredentials(): Promise<AccountCredentialResult[]> {
  const response = await fetchJson<Array<Record<string, unknown>>>('/accounts')
  return response.map(normalizeAccountCredential)
}

export async function createAccountCredential(
  payload: AccountCredentialPayload,
): Promise<AccountCredentialResult> {
  const response = await fetchJson<Record<string, unknown>>('/accounts', {
    method: 'POST',
    body: JSON.stringify({
      label: payload.label,
      provider_key: payload.providerKey,
      account_identifier: payload.accountIdentifier,
      secret_type: payload.secretType,
      secret_value: payload.secretValue || null,
      risk_level: payload.riskLevel ?? 'medium',
      allowed_capabilities: payload.allowedCapabilities ?? [],
      linked_integration_key: payload.linkedIntegrationKey ?? null,
      linked_project_id: payload.linkedProjectId ?? null,
      notes: payload.notes ?? null,
      intelligence_refresh_now: false,
    }),
  })
  return normalizeAccountCredential(response)
}

export async function upsertTrustedAutoAccount(
  payload: TrustedAutoAccountMutationPayload,
): Promise<TrustedAutoAccount[]> {
  const response = await fetchJson<Record<string, unknown>>('/settings/accounts', {
    method: 'PUT',
    body: JSON.stringify({
      account_id: payload.accountId,
      label: payload.label,
      provider_key: payload.providerKey,
      account_identifier: payload.accountIdentifier,
      secret_type: payload.secretType,
      secret_value: payload.secretValue,
      linked_project_id: payload.linkedProjectId,
      linked_integration_key: payload.linkedIntegrationKey,
      notes: payload.notes,
      allowed_capabilities: payload.allowedCapabilities ?? [],
      login_identifier: payload.loginIdentifier,
      password_value: payload.passwordValue,
      totp_value: payload.totpValue,
      backup_codes_value: payload.backupCodesValue,
      recovery_email: payload.recoveryEmail,
      recovery_email_secret_value: payload.recoveryEmailSecretValue,
      connector_mapping_json: payload.connectorMappingJson ?? {},
    }),
  })
  return Array.isArray(response.accounts)
    ? response.accounts.map((account) => normalizeTrustedAutoAccount(account as Record<string, unknown>))
    : []
}

export async function probeTrustedAutoAccounts(
  timeoutSeconds = 12,
): Promise<TrustedAutoAccountFeedback[]> {
  const response = await fetchJson<Record<string, unknown>>('/settings/accounts/feedback-probe', {
    method: 'POST',
    body: JSON.stringify({
      timeout_seconds: timeoutSeconds,
    }),
  })
  return Array.isArray(response.results)
    ? response.results.map((item) =>
        normalizeTrustedAutoAccountFeedback(item as Record<string, unknown>),
      )
    : []
}

export async function upsertTrustedAutoTrustGrant(
  payload: TrustedAutoTrustGrantMutationPayload,
): Promise<TrustedAutoTrustGrant[]> {
  const response = await fetchJson<Record<string, unknown>>('/settings/trust-grants', {
    method: 'PUT',
    body: JSON.stringify({
      trust_grant_id: payload.trustGrantId,
      project_id: payload.projectId,
      account_id: payload.accountId,
      connector_key: payload.connectorKey,
      mode: payload.mode,
      domain_allowlist: payload.domainAllowlist ?? [],
      action_families: payload.actionFamilies ?? [],
      file_write_scope: payload.fileWriteScope ?? [],
      login_allowed: payload.loginAllowed ?? false,
      two_factor_allowed: payload.twoFactorAllowed ?? false,
      publish_allowed: payload.publishAllowed ?? false,
      browser_automation_allowed: payload.browserAutomationAllowed ?? false,
      connector_write_allowed: payload.connectorWriteAllowed ?? false,
      local_render_allowed: payload.localRenderAllowed ?? false,
      max_retries: payload.maxRetries ?? 2,
      cooldown_minutes: payload.cooldownMinutes ?? 60,
      is_enabled: payload.isEnabled ?? true,
      notes: payload.notes,
    }),
  })
  return Array.isArray(response.trust_grants)
    ? response.trust_grants.map((grant) =>
        normalizeTrustedAutoTrustGrant(grant as Record<string, unknown>),
      )
    : []
}

export async function updateTrustedAutoSchedulerSettings(
  payload: TrustedAutoSchedulerSettingsPayload,
): Promise<TrustedAutoSummary['runtimeControls']> {
  const response = await fetchJson<Record<string, unknown>>('/settings/scheduler', {
    method: 'PUT',
    body: JSON.stringify({
      autonomy_mode: payload.autonomyMode,
      scheduler_enabled: payload.schedulerEnabled,
      emergency_stop_enabled: payload.emergencyStopEnabled,
      pause_all_jobs: payload.pauseAllJobs,
      login_automation_enabled: payload.loginAutomationEnabled,
      two_factor_automation_enabled: payload.twoFactorAutomationEnabled,
      publish_automation_enabled: payload.publishAutomationEnabled,
      default_retry_budget: payload.defaultRetryBudget,
      default_vram_floor_gb: payload.defaultVramFloorGb,
    }),
  })
  return normalizeTrustedAutoSummary({
    runtime_controls: response.runtime_controls,
    scheduler_jobs: response.scheduler_jobs,
    execution_runs: response.execution_runs,
    hardware_snapshot: response.hardware_snapshot,
  }).runtimeControls
}

export async function upsertTrustedAutoSchedulerJob(
  payload: TrustedAutoSchedulerJobMutationPayload,
): Promise<TrustedAutoSchedulerJob[]> {
  const response = await fetchJson<Record<string, unknown>>('/settings/scheduler/jobs', {
    method: 'PUT',
    body: JSON.stringify({
      job_id: payload.jobId,
      project_id: payload.projectId,
      job_type: payload.jobType,
      trigger_mode: payload.triggerMode ?? 'manual',
      connector_key: payload.connectorKey,
      platform_key: payload.platformKey,
      scheduled_for: payload.scheduledFor,
      cron_expression: payload.cronExpression,
      payload_json: payload.payloadJson ?? {},
      retry_budget: payload.retryBudget,
      requires_trusted_auto: payload.requiresTrustedAuto ?? true,
      cooldown_key: payload.cooldownKey,
    }),
  })
  return Array.isArray(response.scheduler_jobs)
    ? response.scheduler_jobs.map((job) => normalizeTrustedAutoSchedulerJob(job as Record<string, unknown>))
    : []
}

export async function upsertTrustedAutoMediaPipelinePreset(
  payload: TrustedAutoMediaPipelinePresetMutationPayload,
): Promise<TrustedAutoMediaPipelinePreset[]> {
  const response = await fetchJson<Record<string, unknown>>('/settings/media-pipeline', {
    method: 'PUT',
    body: JSON.stringify({
      preset_id: payload.presetId,
      project_id: payload.projectId,
      name: payload.name,
      target_platform: payload.targetPlatform,
      tts_engine: payload.ttsEngine,
      image_provider: payload.imageProvider,
      video_renderer: payload.videoRenderer,
      subtitle_engine: payload.subtitleEngine,
      prompt_style: payload.promptStyle,
      target_duration_sec: payload.targetDurationSec ?? 40,
      vram_floor_gb: payload.vramFloorGb ?? 4,
      publish_mode: payload.publishMode ?? 'trusted_auto',
      cooldown_hours: payload.cooldownHours ?? 24,
      steps_json: payload.stepsJson ?? {},
      is_default: payload.isDefault ?? false,
    }),
  })
  return Array.isArray(response.presets)
    ? response.presets.map((preset) =>
        normalizeTrustedAutoMediaPipelinePreset(preset as Record<string, unknown>),
      )
    : []
}

export async function runTrustedAutoJob(
  payload: TrustedAutoRunJobPayload,
): Promise<TrustedAutoRunJobResult> {
  const response = await fetchJson<Record<string, unknown>>('/jobs/run', {
    method: 'POST',
    body: JSON.stringify({
      project_id: payload.projectId,
      job_type: payload.jobType,
      goal: payload.goal,
      connector_key: payload.connectorKey,
      platform_key: payload.platformKey,
      account_id: payload.accountId,
      trust_grant_id: payload.trustGrantId,
      payload_json: payload.payloadJson ?? {},
      requires_trusted_auto: payload.requiresTrustedAuto ?? true,
    }),
  })
  return {
    job: normalizeTrustedAutoSchedulerJob(response.job as Record<string, unknown>),
    run:
      response.run == null ? null : normalizeTrustedAutoExecutionRun(response.run as Record<string, unknown>),
  }
}

export async function cancelTrustedAutoJob(jobId: number): Promise<TrustedAutoSchedulerJob> {
  const response = await fetchJson<Record<string, unknown>>(`/jobs/${jobId}/cancel`, {
    method: 'POST',
  })
  return normalizeTrustedAutoSchedulerJob(response.job as Record<string, unknown>)
}

export async function retryTrustedAutoJob(jobId: number): Promise<TrustedAutoSchedulerJob> {
  const response = await fetchJson<Record<string, unknown>>(`/jobs/${jobId}/retry`, {
    method: 'POST',
  })
  return normalizeTrustedAutoSchedulerJob(response.job as Record<string, unknown>)
}

export async function approveTrustedAutoRun(runId: number): Promise<TrustedAutoExecutionRun> {
  const response = await fetchJson<Record<string, unknown>>(`/runs/${runId}/approve-publish`, {
    method: 'POST',
  })
  return normalizeTrustedAutoExecutionRun(response.run as Record<string, unknown>)
}

export async function updateSettingsObsidian(payload: {
  vaultPath: string | null
  exportEnabled: boolean
}): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/obsidian', {
      method: 'PATCH',
      body: JSON.stringify({
        vault_path: payload.vaultPath,
        export_enabled: payload.exportEnabled,
      }),
    }),
  )
}

export async function createSettingsVoice(payload: {
  providerId?: number | null
  name: string
  voiceKey: string
  providerMode: 'local' | 'remote'
  style?: string | null
  language?: string | null
  speakingRate?: string | null
  supportsRealtime?: boolean
  autoReadResponses?: boolean
  isDefault?: boolean
  notes?: string | null
}): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/voices', {
      method: 'POST',
      body: JSON.stringify({
        provider_id: payload.providerId,
        name: payload.name,
        voice_key: payload.voiceKey,
        provider_mode: payload.providerMode,
        style: payload.style,
        language: payload.language,
        speaking_rate: payload.speakingRate,
        supports_realtime: payload.supportsRealtime ?? false,
        auto_read_responses: payload.autoReadResponses ?? false,
        is_default: payload.isDefault ?? false,
        notes: payload.notes,
      }),
    }),
  )
}

export async function updateSettingsVoice(
  voiceId: number,
  payload: {
    providerId?: number | null
    name: string
    voiceKey: string
    providerMode: 'local' | 'remote'
    style?: string | null
    language?: string | null
    speakingRate?: string | null
    supportsRealtime?: boolean
    autoReadResponses?: boolean
    isDefault?: boolean
    notes?: string | null
  },
): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>(`/settings/control-center/voices/${voiceId}`, {
      method: 'PUT',
      body: JSON.stringify({
        provider_id: payload.providerId,
        name: payload.name,
        voice_key: payload.voiceKey,
        provider_mode: payload.providerMode,
        style: payload.style,
        language: payload.language,
        speaking_rate: payload.speakingRate,
        supports_realtime: payload.supportsRealtime ?? false,
        auto_read_responses: payload.autoReadResponses ?? false,
        is_default: payload.isDefault ?? false,
        notes: payload.notes,
      }),
    }),
  )
}

export async function createSettingsRole(payload: {
  name: string
  description?: string | null
  systemPrompt?: string | null
  tone?: string | null
  responseStyle?: string | null
  criticalityLevel?: number
  empathyLevel?: number
  firmnessLevel?: number
  scope?: 'global' | 'project'
  isDefault?: boolean
  isArchived?: boolean
}): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>('/settings/control-center/roles', {
      method: 'POST',
      body: JSON.stringify({
        name: payload.name,
        description: payload.description,
        system_prompt: payload.systemPrompt,
        tone: payload.tone,
        response_style: payload.responseStyle,
        criticality_level: payload.criticalityLevel ?? 3,
        empathy_level: payload.empathyLevel ?? 3,
        firmness_level: payload.firmnessLevel ?? 3,
        scope: payload.scope ?? 'global',
        is_default: payload.isDefault ?? false,
        is_archived: payload.isArchived ?? false,
      }),
    }),
  )
}

export async function updateSettingsRole(
  roleId: number,
  payload: {
    name: string
    description?: string | null
    systemPrompt?: string | null
    tone?: string | null
    responseStyle?: string | null
    criticalityLevel?: number
    empathyLevel?: number
    firmnessLevel?: number
    scope?: 'global' | 'project'
    isDefault?: boolean
    isArchived?: boolean
  },
): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>(`/settings/control-center/roles/${roleId}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: payload.name,
        description: payload.description,
        system_prompt: payload.systemPrompt,
        tone: payload.tone,
        response_style: payload.responseStyle,
        criticality_level: payload.criticalityLevel ?? 3,
        empathy_level: payload.empathyLevel ?? 3,
        firmness_level: payload.firmnessLevel ?? 3,
        scope: payload.scope ?? 'global',
        is_default: payload.isDefault ?? false,
        is_archived: payload.isArchived ?? false,
      }),
    }),
  )
}

export async function updateSettingsPlugin(
  pluginKey: string,
  payload: {
    permissionPolicy?: 'auto' | 'ask' | 'disabled'
    requiresConfirmation?: boolean
    isEnabled?: boolean
    configJson?: Record<string, unknown> | null
  },
): Promise<SettingsControlCenterPayload> {
  return normalizeSettingsControlCenter(
    await fetchJson<Record<string, unknown>>(`/settings/control-center/plugins/${pluginKey}`, {
      method: 'PATCH',
      body: JSON.stringify({
        permission_policy: payload.permissionPolicy,
        requires_confirmation: payload.requiresConfirmation,
        is_enabled: payload.isEnabled,
        config_json: payload.configJson,
      }),
    }),
  )
}

export async function updateProviderCard(
  providerKey: ProviderCard['providerKey'],
  payload: { apiKey?: string; baseUrl?: string | null; enabled?: boolean },
) {
  return normalizeProviderCard(
    await fetchJson<Record<string, unknown>>(`/settings/provider-cards/${providerKey}`, {
      method: 'PUT',
      body: JSON.stringify({
        api_key: payload.apiKey,
        base_url: payload.baseUrl,
        enabled: payload.enabled,
      }),
    }),
  )
}

export async function testProviderCard(
  providerKey: ProviderCard['providerKey'],
  draft?: { apiKey?: string; baseUrl?: string },
) {
  const payload = await fetchJson<Record<string, unknown>>(`/settings/provider-cards/${providerKey}/test`, {
    method: 'POST',
    body: JSON.stringify({
      api_key: draft?.apiKey,
      base_url: draft?.baseUrl,
    }),
  })
  return {
    providerKey: String(payload.provider_key),
    status: String(payload.status),
    connectionState: String(payload.connection_state ?? payload.status ?? 'error') as ProviderCardTestResult['connectionState'],
    detail: String(payload.detail ?? ''),
    message: String(payload.message ?? payload.detail ?? ''),
    healthStatus: String(payload.health_status ?? 'unknown'),
    latencyMs: payload.latency_ms == null ? null : Number(payload.latency_ms),
    installedModels: Array.isArray(payload.installed_models) ? (payload.installed_models as string[]) : [],
    resetInSec: payload.reset_in_sec == null ? null : Number(payload.reset_in_sec),
    testedAt: String(payload.tested_at ?? ''),
  } satisfies ProviderCardTestResult
}

export async function fetchCurrentPipeline(): Promise<PipelineSnapshot> {
  return normalizePipelineSnapshot(await fetchJson<Record<string, unknown>>('/ui/pipeline/current'))
}

function normalizeChannelReviewQueueItem(
  item: Record<string, unknown>,
): ChannelReviewQueueItem {
  return {
    channelId: String(item.channel_id ?? ''),
    itemId: String(item.item_id ?? ''),
    topic: String(item.topic ?? ''),
    status: String(item.status ?? ''),
    finalMp4: item.final_mp4 == null ? null : String(item.final_mp4),
    outputDir: item.output_dir == null ? null : String(item.output_dir),
    reviewPath: item.review_path == null ? null : String(item.review_path),
    qualityScore: item.quality_score == null ? null : Number(item.quality_score),
    visualQualityScore:
      item.visual_quality_score == null ? null : Number(item.visual_quality_score),
    scriptQualityScore:
      item.script_quality_score == null ? null : Number(item.script_quality_score),
    renderOk: item.render_ok == null ? null : Boolean(item.render_ok),
    youtubeShortsReady:
      item.youtube_shorts_ready == null ? null : Boolean(item.youtube_shorts_ready),
    uploadApprovalId:
      item.upload_approval_id == null ? null : String(item.upload_approval_id),
    youtubeVideoId: item.youtube_video_id == null ? null : String(item.youtube_video_id),
    updatedAt: String(item.updated_at ?? ''),
  }
}

function normalizeChannelReviewQueue(
  payload: Record<string, unknown>,
): ChannelReviewQueuePayload {
  return {
    itemCount: Number(payload.item_count ?? 0),
    readyCount: Number(payload.ready_count ?? 0),
    repairCount: Number(payload.repair_count ?? 0),
    jsonPath: String(payload.json_path ?? ''),
    markdownPath: String(payload.markdown_path ?? ''),
    items: Array.isArray(payload.items)
      ? (payload.items as Record<string, unknown>[]).map(normalizeChannelReviewQueueItem)
      : [],
  }
}

export async function fetchChannelReviewQueue(): Promise<ChannelReviewQueuePayload> {
  return normalizeChannelReviewQueue(
    await fetchJson<Record<string, unknown>>('/channel-ops/review-queue'),
  )
}

export async function updatePipelineApproval(
  workItemId: number,
  payload: { action: 'approve_review' | 'request_changes'; reviewNotes?: string | null },
): Promise<PipelineSnapshot> {
  return normalizePipelineSnapshot(
    await fetchJson<Record<string, unknown>>(`/ui/pipeline/approval/${workItemId}`, {
      method: 'POST',
      body: JSON.stringify({
        action: payload.action,
        review_notes: payload.reviewNotes,
      }),
    }),
  )
}

export function getWebSocketUrl() {
  return import.meta.env.VITE_WS_URL ?? 'ws://127.0.0.1:8000/agent'
}
