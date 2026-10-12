import asyncio

import pytest

from app.schemas.music import MusicIntent, MusicPreference, RequestPreferences, LearnedProfile, LearnedSignal
from app.services import intent_service
from app.services.query_service import build_search_queries
from app.services.ranking_service import RankingService


def test_chinese_filter_beats_history_and_pop_matches():
    candidates = [
        {'id': 'en', 'name': 'Get You', 'artist': 'Daniel Caesar', 'tags': ['pop', 'rnb', 'english']},
        {'id': 'unknown', 'name': '中文歌名', 'artist': 'Unknown', 'tags': ['pop']},
        {'id': 'zh', 'name': 'Chinese Song', 'artist': 'Singer', 'tags': ['mandopop']},
    ]
    learned = LearnedProfile(artists=[LearnedSignal(name='Daniel Caesar', score=50, positive_count=50, negative_count=0)])
    rows = RankingService().rank(candidates, MusicPreference(), 20,
        intent=MusicIntent(scene='commute', mood='happy', language='chinese'),
        learned=learned, feedback_liked={'en'})
    assert [row.item['id'] for row in rows] == ['zh']
    assert '语言筛选依据' in rows[0].reason
    assert len(RankingService().rank(candidates, MusicPreference(), 20, intent=MusicIntent())) == 3


@pytest.mark.parametrize('language,tag', [('chinese', 'mandarin'), ('english', 'english'),
    ('japanese', 'j-pop'), ('korean', 'k-pop'), ('instrumental', 'instrumental')])
def test_language_evidence_required(language, tag):
    candidates = [
        {'id': 'match', 'name': 'Match', 'artist': 'A', 'tags': [tag]},
        {'id': 'unknown', 'name': 'Unknown', 'artist': 'B', 'tags': ['ambient', 'piano']},
    ]
    rows = RankingService().rank(candidates, MusicPreference(), 20, intent=MusicIntent(language=language))
    assert [row.item['id'] for row in rows] == ['match']


def test_explicit_language_wins_over_llm(monkeypatch):
    async def incorrect_model(*args):
        return MusicIntent(language='english')
    monkeypatch.setattr(intent_service, '_llm_intent', incorrect_model)
    intent, mode = asyncio.run(intent_service.parse_music_request(
        '我现在在马路边散步', RequestPreferences(language='chinese')))
    assert intent.language == 'chinese' and mode == 'llm'
    assert build_search_queries(intent)[:3] == ['chinese', 'mandopop', 'c-pop']
