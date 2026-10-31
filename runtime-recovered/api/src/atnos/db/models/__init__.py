"""ORM models registered for metadata discovery."""

from atnos.db.models.account_action_draft import AccountActionDraft
from atnos.db.models.ai_worker_task import AiWorkerRun, AiWorkerTask
from atnos.db.models.attachment import Attachment
from atnos.db.models.account_credential import AccountCredential
from atnos.db.models.browser_domain_whitelist import BrowserDomainWhitelist
from atnos.db.models.credential_profile import CredentialProfile
from atnos.db.models.connected_account_control import ConnectedAccountControl
from atnos.db.models.conversation import Conversation
from atnos.db.models.creator_project import ContentCalendarItem, CreatorProject, CreatorWorkItem
from atnos.db.models.decision_log import DecisionLogEntry
from atnos.db.models.execution_run import ExecutionRun, ExecutionStep
from atnos.db.models.custom_integration import CustomIntegration
from atnos.db.models.execution_policy import ExecutionPolicy
from atnos.db.models.execution_trace import ExecutionTrace
from atnos.db.models.hardware_snapshot import HardwareSnapshot
from atnos.db.models.integration_config import IntegrationConfig
from atnos.db.models.local_file_grant import LocalFileGrant
from atnos.db.models.media_pipeline_preset import MediaPipelinePreset
from atnos.db.models.message import Message
from atnos.db.models.model_config import ModelConfig
from atnos.db.models.operational_work_item import OperationalWorkItem
from atnos.db.models.oauth_connection import OAuthConnection
from atnos.db.models.pc_operator_plan import PCOperatorPlan
from atnos.db.models.plugin import Plugin
from atnos.db.models.provider import Provider
from atnos.db.models.project import Project, ProjectMemory
from atnos.db.models.project_command_task import ProjectCommandTask
from atnos.db.models.project_brain import ProjectBlocker, ProjectEvidence, ProjectFact, ProjectGoal, ProjectState
from atnos.db.models.youtube_channel_identity import YouTubeChannelIdentity
from atnos.db.models.rollback_point import RollbackPoint
from atnos.db.models.project_app_link import ProjectAppLink
from atnos.db.models.role_profile import RoleProfile
from atnos.db.models.remote_node import (
    RemoteExecutionRun,
    RemoteExecutionStep,
    RemoteNode,
    RemoteNodeCapabilitySnapshot,
    RemoteNodeCredential,
    RemoteNodeSession,
    RemoteRoutingPolicy,
)
from atnos.db.models.runtime_control_settings import RuntimeControlSettings
from atnos.db.models.scheduled_task import ScheduledTask
from atnos.db.models.scheduler_job import SchedulerJob
from atnos.db.models.self_improvement_note import SelfImprovementNote
from atnos.db.models.trust_grant import TrustGrant
from atnos.db.models.trend_watch_snapshot import TrendWatchSnapshot
from atnos.db.models.turn_feedback import TurnFeedback
from atnos.db.models.user import User
from atnos.db.models.user_style_preferences import UserStylePreferences
from atnos.db.models.voice_profile import VoiceProfile

__all__ = [
    "AccountActionDraft",
    "AiWorkerRun",
    "AiWorkerTask",
    "Attachment",
    "AccountCredential",
    "BrowserDomainWhitelist",
    "CredentialProfile",
    "ConnectedAccountControl",
    "Conversation",
    "ContentCalendarItem",
    "CreatorProject",
    "CreatorWorkItem",
    "DecisionLogEntry",
    "ExecutionRun",
    "ExecutionStep",
    "CustomIntegration",
    "ExecutionPolicy",
    "ExecutionTrace",
    "HardwareSnapshot",
    "IntegrationConfig",
    "LocalFileGrant",
    "MediaPipelinePreset",
    "Message",
    "ModelConfig",
    "OperationalWorkItem",
    "OAuthConnection",
    "PCOperatorPlan",
    "Plugin",
    "Provider",
    "Project",
    "ProjectBlocker",
    "ProjectAppLink",
    "ProjectEvidence",
    "ProjectFact",
    "ProjectGoal",
    "ProjectMemory",
    "ProjectCommandTask",
    "ProjectState",
    "YouTubeChannelIdentity",
    "RollbackPoint",
    "RemoteExecutionRun",
    "RemoteExecutionStep",
    "RemoteNode",
    "RemoteNodeCapabilitySnapshot",
    "RemoteNodeCredential",
    "RemoteNodeSession",
    "RemoteRoutingPolicy",
    "RoleProfile",
    "RuntimeControlSettings",
    "ScheduledTask",
    "SchedulerJob",
    "SelfImprovementNote",
    "TrustGrant",
    "TrendWatchSnapshot",
    "TurnFeedback",
    "User",
    "UserStylePreferences",
    "VoiceProfile",
]
