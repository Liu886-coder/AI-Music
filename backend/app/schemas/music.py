from pydantic import BaseModel, Field, ConfigDict, AliasChoices
from typing import List, Literal, Annotated

Scene = Literal['any', 'workout', 'study', 'work', 'commute', 'relax', 'party', 'leisure']
Mood = Literal['any', 'energetic', 'happy', 'calm', 'focused', 'romantic', 'sad', 'inspiring']
Genre = Literal['pop', 'rock', 'electronic', 'hip-hop', 'jazz', 'classical', 'easy listening', 'rnb']
Language = Literal['any', 'chinese', 'english', 'japanese', 'korean', 'instrumental']
Energy = Literal['any', 'low', 'medium', 'high']
Familiarity = Literal['any', 'familiar', 'discover', 'mixed']
ShortText = Annotated[str, Field(min_length=1, max_length=100)]

class RequestPreferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scene: Scene = 'any'
    mood: Mood = 'any'
    genres: list[Genre] = Field(default_factory=list, max_length=8)
    language: Language = 'any'
    energy: Energy = 'any'
    familiarity: Familiarity = 'any'

class MusicIntent(RequestPreferences):
    tempo_preference: Literal['any', 'slow', 'medium', 'fast'] = 'any'
    include_tags: list[ShortText] = Field(default_factory=list, max_length=16)
    exclude_tags: list[ShortText] = Field(default_factory=list, max_length=16)
    avoid_artists: list[ShortText] = Field(default_factory=list, max_length=16)
    preferred_artists: list[ShortText] = Field(default_factory=list, max_length=8)
    intent_summary: str = Field(default='', max_length=600)

class RecommendationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    user_id: str = Field(default='demo_user', min_length=1, max_length=64, pattern=r'^[\w-]+$')
    request_text: str = Field(min_length=1, max_length=2000, validation_alias=AliasChoices('request_text', 'message'))
    preferences: RequestPreferences = Field(default_factory=RequestPreferences)
    limit: int = Field(default=20, ge=20, le=50)
    refresh: bool = False
    previous_track_ids: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(default_factory=list, max_length=50)

class MusicPreference(BaseModel):
    preferred_artists: List[str] = []
    disliked_artists: List[str] = []
    preferred_genres: List[str] = []
    disliked_genres: List[str] = []
    preferred_languages: List[str] = []
    preferred_moods: List[str] = []
    preferred_scenes: List[str] = []
    energy_level: str = ""
    tempo_preference: str = ""
    excluded_artists: List[str] = []
    excluded_tracks: List[str] = []

class RecommendationItem(BaseModel):
    track_id: str
    track_name: str
    artist_name: str
    album_name: str = ""
    score: float
    reason: str
    source: str
    url: str = ""
    image_url: str = ""
    evidence: list[str] = Field(default_factory=list)
    unverified_conditions: list[str] = Field(default_factory=list)
    feedback_type: Literal['like', 'dislike', 'skip', 'save'] | None = None
    model_config = ConfigDict(from_attributes=True)

class RecommendationResponse(BaseModel):
    request_id: str
    intent_summary: str
    intent: MusicIntent
    tracks: List[RecommendationItem]
    request_summary: str
    recommendations: List[RecommendationItem]
    used_tools: List[str] = []
    warnings: List[str] = []
    status: Literal['ok', 'partial', 'empty', 'needs_clarification'] = 'ok'
    retrieval_plan: dict = Field(default_factory=dict)
    agent_trace: list[dict] = Field(default_factory=list)
    evaluation: dict = Field(default_factory=dict)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    parser_mode: Literal['llm', 'mock', 'fallback'] = 'mock'
    music_mode: Literal['mock', 'lastfm'] = 'mock'

class FeedbackRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64, pattern=r'^[\w-]+$')
    track_id: str = Field(min_length=1, max_length=512)
    feedback_type: Literal["like", "dislike", "skip", "save"]

class PreferenceUpdate(MusicPreference):
    pass

class FeedbackResponse(BaseModel):
    ok: bool
    message: str

class LearnedSignal(BaseModel):
    name: str
    score: int
    positive_count: int
    negative_count: int
    evidence: list[dict] = Field(default_factory=list)

class LearnedProfile(BaseModel):
    feedback_count: int = 0
    liked_count: int = 0
    disliked_count: int = 0
    missing_metadata_count: int = 0
    tags: list[LearnedSignal] = Field(default_factory=list)
    artists: list[LearnedSignal] = Field(default_factory=list)

class PreferenceResponse(MusicPreference):
    learned: LearnedProfile = Field(default_factory=LearnedProfile)

class FeedbackState(BaseModel):
    track_id: str
    feedback_type: Literal['like', 'dislike', 'skip', 'save']

class HistoryItem(BaseModel):
    id: int = 0
    request_id: str = ''
    rank_position: int = 0
    intent: dict = Field(default_factory=dict)
    track_id: str
    track_name: str = ""
    artist_name: str = ""
    score: float
    reason: str
    request_text: str
    created_at: str
