"""Turn a natural language request and explicit controls into a validated intent.

The LLM is optional. Its output is treated as an interpreter only; it never creates
track metadata. A small deterministic parser keeps the application usable without
an LLM key and is also the safety fallback for malformed model responses.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.core.config import get_settings
from app.schemas.music import MusicIntent, RequestPreferences
from app.services.preference_service import infer_preference

logger = logging.getLogger(__name__)

SCENE_TAGS = {'workout': ['workout', 'running', 'exercise', 'upbeat'], 'study': ['study', 'focus', 'instrumental'],
              'work': ['focus', 'background'], 'commute': ['road trip', 'pop'], 'relax': ['chill', 'ambient', 'relaxing'],
              'party': ['party', 'dance', 'upbeat'], 'leisure': ['pop']}
MOOD_TAGS = {'energetic': ['energetic', 'upbeat', 'dance'], 'happy': ['happy', 'joyful', 'pop'], 'calm': ['calm', 'chill', 'ambient'],
             'focused': ['focus', 'study', 'instrumental'], 'romantic': ['romantic', 'love'], 'sad': ['sad', 'melancholy'],
             'inspiring': ['inspiring', 'uplifting', 'rock']}
LANGUAGE_TAGS = {'chinese': ['chinese', 'mandopop', 'c-pop'], 'english': ['english'], 'japanese': ['japanese', 'j-pop'],
                 'korean': ['korean', 'k-pop'], 'instrumental': ['instrumental', 'ambient', 'piano']}
GENRE_TAGS = {'pop': ['pop'], 'rock': ['rock', 'alternative'], 'electronic': ['electronic', 'dance'], 'hip-hop': ['hip hop', 'rap'],
              'jazz': ['jazz'], 'classical': ['classical'], 'easy listening': ['easy listening', 'ambient'], 'rnb': ['r&b', 'rnb']}

def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(x.strip().lower() for x in values if isinstance(x, str) and x.strip()))

def deterministic_intent(text: str, controls: RequestPreferences, stored: Any = None) -> MusicIntent:
    inferred = infer_preference(text)
    mood = controls.mood if controls.mood != 'any' else (inferred.preferred_moods[0] if inferred.preferred_moods else 'any')
    scene = controls.scene if controls.scene != 'any' else (inferred.preferred_scenes[0] if inferred.preferred_scenes else 'any')
    language = controls.language
    if language == 'any' and inferred.preferred_languages:
        language = inferred.preferred_languages[0]
    genres = list(controls.genres)
    if not genres and inferred.preferred_genres:
        genres = [x for x in inferred.preferred_genres if x in GENRE_TAGS]
    energy = controls.energy
    if energy == 'any' and any(x in text for x in ('高能量', '节奏强', '动感')): energy = 'high'
    tags = _dedupe(SCENE_TAGS.get(scene, []) + MOOD_TAGS.get(mood, []) + LANGUAGE_TAGS.get(language, []) + sum((GENRE_TAGS.get(g, []) for g in genres), []))
    avoid = list(getattr(stored, 'disliked_artists', []) or []) if stored else []
    return MusicIntent(scene=scene, mood=mood, genres=genres, language=language, energy=energy,
                       familiarity=controls.familiarity, tempo_preference='fast' if energy == 'high' else 'any',
                       include_tags=tags, avoid_artists=avoid,
                       intent_summary=text.strip() or '根据所选条件推荐音乐。')

async def _llm_intent(text: str, controls: RequestPreferences, stored: Any, learned=None) -> MusicIntent | None:
    settings = get_settings()
    if not settings.llm_api_key or not settings.llm_base_url or not settings.llm_model or settings.llm_mock_mode:
        return None
    endpoint = settings.llm_base_url.rstrip('/') + '/chat/completions'
    system = ('你是音乐需求解析器。只输出符合给定字段的 JSON，不生成歌曲。历史学习信号仅供参考，不得覆盖本次文字或明确筛选。'
              '本次未指定的条件可以保持 any；历史偏好会由后续排序单独加权。严格使用枚举值：'
              'scene any/workout/study/work/commute/relax/party/leisure；mood any/energetic/happy/calm/focused/romantic/sad/inspiring；'
              'language any/chinese/english/japanese/korean/instrumental；energy any/low/medium/high；familiarity any/familiar/discover/mixed。')
    payload = {'model': settings.llm_model, 'temperature': 0, 'response_format': {'type': 'json_object'},
               'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps({'request_text': text, 'explicit_preferences': controls.model_dump(), 'stored_preferences': getattr(stored, 'model_dump', lambda: {})(), 'learned_preferences': learned or {}, 'output_schema': MusicIntent.model_json_schema()}, ensure_ascii=False)}]}
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(endpoint, headers={'Authorization': f'Bearer {settings.llm_api_key}'}, json=payload)
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
        data = json.loads(content) if isinstance(content, str) else content
        return MusicIntent.model_validate({**deterministic_intent(text, controls, stored).model_dump(), **data, 'intent_summary': data.get('intent_summary') or text[:600]})
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning('LLM intent parser unavailable; using deterministic fallback: %s', type(exc).__name__)
        return None

async def parse_music_request(text: str, controls: RequestPreferences, stored: Any = None, learned=None) -> tuple[MusicIntent, str]:
    result = await _llm_intent(text, controls, stored, learned)
    if result:
        # Explicit controls have priority over model guesses.
        data = result.model_dump()
        for field in ('scene', 'mood', 'language', 'energy', 'familiarity'):
            value = getattr(controls, field)
            if value != 'any': data[field] = value
        if controls.genres: data['genres'] = controls.genres
        return MusicIntent.model_validate(data), 'llm'
    return deterministic_intent(text, controls, stored), 'fallback' if get_settings().llm_api_key else 'mock'
