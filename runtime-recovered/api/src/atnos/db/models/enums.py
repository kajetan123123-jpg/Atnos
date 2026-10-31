from __future__ import annotations

from enum import Enum


class AttachmentKind(str, Enum):
    DOCUMENT = "document"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TEXT = "text"
    OTHER = "other"


class AttachmentSource(str, Enum):
    UPLOAD = "upload"
    GENERATED = "generated"
    IMPORTED = "imported"


class AttachmentStatus(str, Enum):
    AVAILABLE = "available"
    PROCESSING = "processing"
    FAILED = "failed"
    DELETED = "deleted"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    PAUSED = "paused"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessageStatus(str, Enum):
    CREATED = "created"
    COMPLETED = "completed"
    FAILED = "failed"


class ExecutionTraceKind(str, Enum):
    INTENT = "intent_detection"
    CONTEXT = "context_assembly"
    PROVIDER = "provider_call"
    TOOL = "tool_execution"
    MEMORY = "memory_writeback"
    VERIFY = "verification"
    ANSWER = "answer_shaping"


class ExecutionTraceStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class FeedbackRating(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class FeedbackReasonCode(str, Enum):
    WRONG_TONE = "wrong_tone"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    MISSED_CONTEXT = "missed_context"
    BAD_EXECUTION_PATH = "bad_execution_path"
    INCORRECT_ANSWER = "incorrect_answer"
    OTHER = "other"


class ExecutionPathFeedback(str, Enum):
    GOOD = "good"
    BAD = "bad"
    UNSURE = "unsure"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    ON_HOLD = "on_hold"


class ProjectMemoryKind(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    TASK = "task"
    SUMMARY = "summary"
    PREFERENCE = "preference"
    NOTE = "note"


class ProjectMemoryPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RoleProfileScope(str, Enum):
    GLOBAL = "global"
    PROJECT = "project"


class ProviderKind(str, Enum):
    LOCAL_LLM = "local_llm"
    REMOTE_LLM = "remote_llm"
    MULTIMODAL = "multimodal"
    SPEECH = "speech"
    IMAGE = "image"


class ProviderTransport(str, Enum):
    LOCAL_RUNTIME = "local_runtime"
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"


class ProviderAuthMode(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    LOCAL = "local"


class ModelTask(str, Enum):
    GENERAL_CHAT = "general_chat"
    ORCHESTRATION = "orchestration"
    CODING = "coding"
    SEARCH_RESEARCH = "search_research"
    VISION = "vision"
    TRANSCRIPTION = "transcription"
    SYNTHESIS = "synthesis"
    IMAGE_GENERATION = "image_generation"


class ModelQualityTier(str, Enum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class PluginSource(str, Enum):
    BUILTIN = "builtin"
    LOCAL = "local"
    REMOTE = "remote"


class PluginPermissionPolicy(str, Enum):
    AUTO = "auto"
    ASK = "ask"
    DISABLED = "disabled"


class ExecutionVerificationMode(str, Enum):
    OFF = "off"
    SMART = "smart"
    STRICT = "strict"


class ExecutionAutomationLevel(str, Enum):
    MANUAL = "manual"
    ASSISTED = "assisted"
    AUTO = "auto"


class ProviderPosture(str, Enum):
    PREFER_LOCAL = "prefer-local"
    IMPROVE_WHEN_NEEDED = "improve-when-needed"
    ASK_BEFORE_EXTERNAL = "ask-before-external"
    STRICT_LOCAL = "strict-local"


class PersonaProfile(str, Enum):
    STRATEGIST = "strategist"
    CRITIC = "critic"
    EXECUTOR = "executor"
    MENTOR = "mentor"
    JARVIS_MODE = "jarvis-mode"


class JarvisAddressAs(str, Enum):
    NONE = "none"
    FIRST_NAME = "first_name"
    SIR_MADAM = "sir_madam"


class VoiceProviderMode(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


class ResponseLengthPreference(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


class ResponseTone(str, Enum):
    PROFESSIONAL = "professional"
    NATURAL = "natural"
    DIRECT = "direct"
    SUPPORTIVE = "supportive"


class ResponseStructure(str, Enum):
    PARAGRAPHS = "paragraphs"
    BULLETS = "bullets"
    MIXED = "mixed"


class SelfImprovementSeverity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class SelfImprovementDecision(str, Enum):
    ACCEPTED = "accepted"
    FOLLOW_UP = "follow_up"
    REJECTED = "rejected"
    UNDECIDED = "undecided"


class SelfImprovementStatus(str, Enum):
    OPEN = "open"
    SELECTED = "selected"
    CLOSED = "closed"


class DecisionLogKind(str, Enum):
    DECISION = "decision"
    COMMITMENT = "commitment"
    OPEN_QUESTION = "open_question"
    PENDING_ACTION = "pending_action"


class DecisionLogStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class OperationalWorkItemStatus(str, Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    WAITING = "waiting"
    DONE = "done"


class OperationalWorkItemPriority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"


class OperationalWorkItemReviewState(str, Enum):
    UNREVIEWED = "unreviewed"
    IN_REVIEW = "in_review"
    REVIEWED = "reviewed"


class RemoteNodeStatus(str, Enum):
    PROVISIONING = "provisioning"
    ENROLLING = "enrolling"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    PAUSED = "paused"
    REVOKED = "revoked"
    ERROR = "error"


class RemoteNodeTransport(str, Enum):
    SSH_BOOTSTRAP = "ssh_bootstrap"
    WEBSOCKET = "websocket"


class RemoteNodeTrustMode(str, Enum):
    SUPERVISED = "supervised"
    TRUSTED_AUTO = "trusted_auto"


class RemoteDisplayMode(str, Enum):
    X11 = "x11"
    WAYLAND = "wayland"
    UNKNOWN = "unknown"


class RemoteRoutingTarget(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


class RemoteExecutionOrigin(str, Enum):
    MANUAL = "manual"
    SCHEDULER = "scheduler"
    TRUSTED_AUTO = "trusted_auto"
    SYSTEM = "system"


class RemoteExecutionStatus(str, Enum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOCAL_REQUIRED = "local_required"
    PAUSED = "paused"


class RemoteExecutionStepStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RemoteExecutionExecutor(str, Enum):
    WORKER_CORE = "worker_core"
    DESKTOP_BRIDGE = "desktop_bridge"
    LOCAL_EXECUTOR = "local_executor"
