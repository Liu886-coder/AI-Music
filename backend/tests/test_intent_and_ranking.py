import asyncio

from app.schemas.music import RequestPreferences, MusicPreference
from app.services.intent_service import parse_music_request
from app.services.query_service import build_search_queries
from app.services.ranking_service import RankingService


def test_explicit_controls_win_over_natural_language():
    controls = RequestPreferences(scene='study', mood='calm', genres=['classical'], language='instrumental', energy='low')
    intent, mode = asyncio.run(parse_music_request('给我一首充满活力的摇滚歌', controls))
    assert mode in {'mock', 'fallback'}
    assert intent.scene == 'study'
    assert intent.mood == 'calm'
    assert intent.genres == ['classical']
    assert intent.language == 'instrumental'


def test_queries_use_standard_values_as_lastfm_tags():
    intent, _ = asyncio.run(parse_music_request('健身时听', RequestPreferences(scene='workout', mood='energetic', genres=['rock'])))
    queries = build_search_queries(intent)
    assert 'workout' in queries and 'rock' in queries


def test_natural_language_workout_uses_valid_internal_scene():
    intent, mode = asyncio.run(parse_music_request('推荐适合健身和跑步时听的歌', RequestPreferences()))
    assert mode in {'mock', 'fallback'}
    assert intent.scene == 'workout'
    assert 'workout' in intent.include_tags


def test_dislike_removes_track_and_like_boosts_track():
    tracks = [
        {'id': 'like', 'name': 'A', 'artist': 'One', 'tags': ['rock']},
        {'id': 'dislike', 'name': 'B', 'artist': 'Two', 'tags': ['rock']},
        {'id': 'other', 'name': 'C', 'artist': 'Three', 'tags': ['rock']},
    ]
    rows = RankingService().rank(tracks, MusicPreference(preferred_genres=['rock']), 3, feedback_liked={'like'}, feedback_disliked={'dislike'})
    assert [row.item['id'] for row in rows] == ['like', 'other']
