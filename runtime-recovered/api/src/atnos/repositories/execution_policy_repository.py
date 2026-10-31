from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.execution_policy import ExecutionPolicy
from atnos.db.models.enums import PersonaProfile, ProviderPosture


class ExecutionPolicyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_user(self, user_id: int) -> ExecutionPolicy | None:
        statement = select(ExecutionPolicy).where(ExecutionPolicy.user_id == user_id)
        return self.session.scalar(statement)

    def create(self, **kwargs) -> ExecutionPolicy:
        policy = ExecutionPolicy(**kwargs)
        self.session.add(policy)
        self.session.flush()
        return policy

    def update_default_provider_posture(
        self,
        policy: ExecutionPolicy,
        provider_posture: ProviderPosture,
    ) -> ExecutionPolicy:
        policy.default_provider_posture = provider_posture
        self.session.add(policy)
        self.session.flush()
        return policy

    def update_default_persona_profile(
        self,
        policy: ExecutionPolicy,
        persona_profile: PersonaProfile,
    ) -> ExecutionPolicy:
        policy.default_persona_profile = persona_profile
        self.session.add(policy)
        self.session.flush()
        return policy

    def update(self, policy: ExecutionPolicy, **kwargs) -> ExecutionPolicy:
        for field, value in kwargs.items():
            setattr(policy, field, value)
        self.session.add(policy)
        self.session.flush()
        return policy
