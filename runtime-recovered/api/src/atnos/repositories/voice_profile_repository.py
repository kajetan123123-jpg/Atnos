from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from atnos.db.models.voice_profile import VoiceProfile


class VoiceProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_user(self, user_id: int) -> list[VoiceProfile]:
        statement = select(VoiceProfile).where(VoiceProfile.user_id == user_id).order_by(VoiceProfile.name.asc())
        return list(self.session.scalars(statement))

    def get_default_for_user(self, user_id: int) -> VoiceProfile | None:
        statement = select(VoiceProfile).where(VoiceProfile.user_id == user_id, VoiceProfile.is_default.is_(True))
        return self.session.scalar(statement)

    def get(self, voice_profile_id: int) -> VoiceProfile | None:
        return self.session.get(VoiceProfile, voice_profile_id)

    def create(self, **kwargs) -> VoiceProfile:
        voice_profile = VoiceProfile(**kwargs)
        self.session.add(voice_profile)
        self.session.flush()
        return voice_profile

    def clear_default_for_user(self, user_id: int, *, exclude_voice_profile_id: int | None = None) -> None:
        for voice_profile in self.list_for_user(user_id):
            if exclude_voice_profile_id is not None and voice_profile.id == exclude_voice_profile_id:
                continue
            if voice_profile.is_default:
                voice_profile.is_default = False
                self.session.add(voice_profile)
        self.session.flush()

    def update(self, voice_profile: VoiceProfile, **kwargs) -> VoiceProfile:
        for field, value in kwargs.items():
            setattr(voice_profile, field, value)
        self.session.add(voice_profile)
        self.session.flush()
        return voice_profile
