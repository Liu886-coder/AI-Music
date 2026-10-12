from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path
from pydantic import Field

class Settings(BaseSettings):
    app_name: str = "music-recommendation-agent"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_mock_mode: bool = False
    llm_timeout_seconds: float = 20
    recommendation_timeout_seconds: float = 45
    agent_max_rounds: int = Field(default=3, ge=1, le=3)
    agent_max_tool_calls: int = Field(default=12, ge=1, le=24)
    agent_calls_per_round: int = Field(default=4, ge=1, le=8)
    agent_tool_timeout_seconds: float = Field(default=10, gt=0, le=30)
    agent_llm_timeout_seconds: float = Field(default=8, gt=0, le=30)
    agent_retrieval_timeout_seconds: float = Field(default=32, gt=0, le=120)
    agent_enrichment_limit: int = Field(default=30, ge=0, le=60)
    agent_log_enabled: bool = True
    agent_log_raw_requests: bool = False
    agent_log_dir: str = str(Path(__file__).resolve().parents[2] / 'logs')
    lastfm_api_key: str = ""
    musicbrainz_user_agent: str = "MusicRecommendationAgent/1.0"
    database_url: str = "sqlite:///./music_agent.db"
    mock_mode: bool = True
    log_level: str = "INFO"
    preference_weight: float = 0.30
    similarity_weight: float = 0.25
    tag_weight: float = 0.15
    context_weight: float = 0.15
    feedback_weight: float = 0.10
    novelty_weight: float = 0.05
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / '.env', extra="ignore")

@lru_cache
def get_settings() -> Settings:
    return Settings()
