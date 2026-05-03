from datetime import datetime
from enum import unique
from sqlalchemy import ForeignKey, String, BigInteger, Text, Integer, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

class Base(DeclarativeBase):
    pass

class UserProfile(Base):
    """Structured user's profile"""
    __tablename__ = "user_profiles"
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(55), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    language_code: Mapped[str] = mapped_column(String(10), default="ru")
    theme: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    sessions: Mapped[list["ConversationSession"]] = relationship(back_populates="user") # F. k. for sessions!
    
    def __repr__(self):
        return f"<UserProfile(user_id={self.user_id}, username={self.username})>"
    
    @classmethod
    async def get_with_lock(cls, session: AsyncSession, user_id: int) -> Optional["UserProfile"]:
        """Profile with row-level lock (OTA!)"""
        result = await session.execute(
            select(cls)
            .where(cls.user_id == user_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()
    
    @classmethod
    async def get_or_create_with_lock(cls, session: AsyncSession, user_id: int, username: str = None) -> "UserProfile":
        """Get or create locked profile!"""
        profile = await cls.get_with_lock(session, user_id)
        
        if profile:
            return profile
        
        profile = cls(user_id=user_id, username=username)
        session.add(profile)
        await session.flush()
        return profile


class ConversationSession(Base):
    """Session with dialoge"""
    __tablename__ = "conversation_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_profiles.user_id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    user: Mapped["UserProfile"] = relationship(back_populates="sessions") # F. k. for user!
    
    def __repr__(self):
        return f"<ConversationSession(id={self.id}, user_id={self.user_id})>"
    
    @classmethod
    async def get_active_with_lock(cls, session: AsyncSession, user_id: int) -> Optional["ConversationSession"]:
        """Get active session with lock!!"""
        result = await session.execute(
            select(cls)
            .where(
                cls.user_id == user_id,
                cls.ended_at == None
            )
            .order_by(cls.started_at.desc())
            .limit(1)
            .with_for_update()
        )
        return result.scalar_one_or_none()