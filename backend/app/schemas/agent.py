from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.schemas.music import MusicIntent, Language, ShortText


class ToolCall(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    tool: Literal['top_tracks_by_tag', 'search_tracks', 'similar_tracks', 'chart_tracks']
    tag: str = Field(default='', max_length=100)
    query: str = Field(default='', max_length=200)
    seed_id: str = Field(default='', max_length=512)
    limit: int = Field(default=30, ge=1, le=50)

    @model_validator(mode='after')
    def arguments_match_tool(self):
        field = {'top_tracks_by_tag': 'tag', 'search_tracks': 'query', 'similar_tracks': 'seed_id'}.get(self.tool)
        if field and not getattr(self, field):
            raise ValueError(f'{self.tool} requires {field}')
        if any(getattr(self, name) for name in ('tag', 'query', 'seed_id') if name != field):
            raise ValueError('Unexpected tool argument')
        return self

    def signature(self):
        return (self.tool, self.tag.casefold(), self.query.casefold(), self.seed_id)


class HardConstraints(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: Language = 'any'
    exclude_artists: list[ShortText] = Field(default_factory=list, max_length=32)
    exclude_tags: list[ShortText] = Field(default_factory=list, max_length=32)
    required_tags: list[ShortText] = Field(default_factory=list, max_length=16)


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: MusicIntent
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    strategy: str = Field(default='', max_length=600)
    calls: list[ToolCall] = Field(default_factory=list, max_length=8)
    need_clarification: bool = False
    clarification_questions: list[ShortText] = Field(default_factory=list, max_length=3)
    confidence: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode='after')
    def clarification_has_question(self):
        if self.need_clarification and not self.clarification_questions:
            raise ValueError('Clarification requires a question')
        return self


class RetrievalDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['search', 'finish']
    reason: str = Field(min_length=1, max_length=600)
    calls: list[ToolCall] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def action_matches_calls(self):
        if (self.action == 'search') != bool(self.calls):
            raise ValueError('search requires calls; finish must not include calls')
        return self
