from datetime import datetime
from sqlalchemy import String, Text, Float, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.database import Base

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(120), default="User")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    preferences: Mapped["UserPreference"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")

class UserPreference(Base):
    __tablename__ = "user_preferences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    preferred_artists: Mapped[str] = mapped_column(Text, default="")
    disliked_artists: Mapped[str] = mapped_column(Text, default="")
    preferred_genres: Mapped[str] = mapped_column(Text, default="")
    disliked_genres: Mapped[str] = mapped_column(Text, default="")
    preferred_languages: Mapped[str] = mapped_column(Text, default="")
    preferred_moods: Mapped[str] = mapped_column(Text, default="")
    preferred_scenes: Mapped[str] = mapped_column(Text, default="")
    energy_level: Mapped[str] = mapped_column(String(32), default="")
    tempo_preference: Mapped[str] = mapped_column(String(32), default="")
    learned_profile_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    user: Mapped[User] = relationship(back_populates="preferences")

class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(256))
    artist_name: Mapped[str] = mapped_column(String(256))
    album_name: Mapped[str] = mapped_column(String(256), default="")
    url: Mapped[str] = mapped_column(String(512), default="")
    image_url: Mapped[str] = mapped_column(String(512), default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class RecommendationHistory(Base):
    __tablename__ = "recommendation_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"))
    track: Mapped["Track"] = relationship()
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    request_text: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(36), default='')
    intent_json: Mapped[str] = mapped_column(Text, default='{}')
    rank_position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"))
    feedback_type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
