import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.init_db import init_db
from app.main import app
from app.models.entities import Track, UserPreference, Feedback
from app.schemas.music import MusicIntent, MusicPreference, LearnedProfile, LearnedSignal, RequestPreferences
from app.services.feedback_service import record_feedback
from app.services.learning_service import rebuild_profile, latest_feedback, stored_profile
from app.services.preference_service import get_or_create_user
from app.services.ranking_service import RankingService
from app.services.recommendation_service import RecommendationService
from app.clients.lastfm_client import MusicClientError


@pytest.fixture
def database(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'learning.db'), connect_args={'check_same_thread': False})
    init_db(engine)
    yield engine
    engine.dispose()


def add_track(db, track_id, name, artist='Artist A', tags='rock'):
    track = Track(id=track_id, external_id=track_id, name=name, artist_name=artist, tags=tags)
    db.add(track)
    db.commit()
    return track


def test_learning_persists_reverses_and_isolates_users(database):
    with Session(database) as db:
        add_track(db, 'a', 'Song A')
        add_track(db, 'b', 'Song B', tags='')
        user = get_or_create_user(db, 'alice')
        user.preferences.preferred_genres = 'classical'
        db.commit()
        record_feedback(db, 'alice', 'a', 'like')
        record_feedback(db, 'alice', 'a', 'like')
        profile = rebuild_profile(db, 'alice')
        assert profile.feedback_count == 1 and profile.tags[0].score == 1
        assert profile.tags[0].evidence[0]['track_name'] == 'Song A'
        assert rebuild_profile(db, 'bob').feedback_count == 0
        record_feedback(db, 'alice', 'b', 'like')
        assert rebuild_profile(db, 'alice').missing_metadata_count == 1
        record_feedback(db, 'alice', 'a', 'dislike')
        assert rebuild_profile(db, 'alice').tags[0].score == -1
        record_feedback(db, 'alice', 'a', 'like')
        assert latest_feedback(db, 'alice')['a'].feedback_type == 'like'
        assert user.preferences.preferred_genres == 'classical'
    with Session(database) as db:
        profile = stored_profile(get_or_create_user(db, 'alice'))
        assert profile.liked_count == 2 and profile.disliked_count == 0
        assert profile.tags[0].score == 1
        # Different upstream IDs for the same song must not double-count or retain an old dislike.
        add_track(db, 'a-alias', 'Song A')
        record_feedback(db, 'alice', 'a-alias', 'dislike')
        assert rebuild_profile(db, 'alice').feedback_count == 2
        assert rebuild_profile(db, 'alice').tags[0].score == -1


def test_unknown_song_is_not_learned(database):
    with Session(database) as db:
        with pytest.raises(ValueError):
            record_feedback(db, 'alice', 'fabricated', 'like')
        assert db.query(Feedback).count() == 0
        assert db.query(Track).count() == 0


def test_request_priority_and_history_generalization():
    profile = LearnedProfile(tags=[LearnedSignal(name='rock', score=50, positive_count=50, negative_count=0)])
    tracks = [{'id': 'rock', 'name': 'New Rock Song', 'artist': 'New Artist', 'tags': ['rock']},
              {'id': 'classic', 'name': 'Classical', 'artist': 'Composer', 'tags': ['classical']},
              {'id': 'plain', 'name': 'Other', 'artist': 'Unknown', 'tags': []}]
    ranker = RankingService()
    rows = ranker.rank(tracks, MusicPreference(), 3, learned=profile, intent=MusicIntent())
    assert rows[0].item['id'] == 'rock'
    assert '历史反馈偏好标签 rock' in rows[0].reason
    rows = ranker.rank(tracks, MusicPreference(), 3, learned=profile, intent=MusicIntent(genres=['classical']))
    assert rows[0].item['id'] == 'classic'
    assert ranker.rank(tracks, MusicPreference(), 3, feedback_disliked={'rock'})[0].item['id'] != 'rock'


def test_recommendation_retrieves_similar_tracks_and_preserves_manual_preferences(database):
    service = RecommendationService()
    calls = []
    async def top(tag, limit):
        return []
    async def similar(artist, name, limit):
        calls.append((artist, name))
        return [{'id': 'new', 'name': 'New Song', 'artist': 'Artist B', 'tags': ['rock'], 'url': 'https://www.last.fm/'}]
    async def chart(limit):
        return []
    async def enrich(items, max_items):
        return items
    service.tools.top_tracks_by_tag = top
    service.tools.similar_tracks = similar
    service.tools.chart_tracks = chart
    service.tools.enrich_tracks = enrich
    with Session(database) as db:
        add_track(db, 'seed', 'Seed')
        record_feedback(db, 'alice', 'seed', 'like')
        result = asyncio.run(service.recommend(db, 'alice', '随便推荐一些音乐', 20))
        assert calls == [('Artist A', 'Seed')]
        assert result['items'][0]['track_id'] == 'new'
        assert '相似推荐来源' in result['items'][0]['reason']
        assert '历史反馈偏好标签 rock' in result['items'][0]['reason']
        assert get_or_create_user(db, 'alice').preferences.preferred_genres == ''
        assert 'track.getSimilar' in result['used_tools']
        async def unavailable(*args):
            raise MusicClientError('rate limited')
        service.tools.similar_tracks = unavailable
        service.tools.chart_tracks = service.tools.client.chart_tracks
        result = asyncio.run(service.recommend(db, 'alice', '随便推荐', 20))
        assert result['warnings'] and result['items']
        service.tools.chart_tracks = unavailable
        with pytest.raises(MusicClientError, match='音乐数据源检索失败'):
            asyncio.run(service.recommend(db, 'alice', '随便推荐', 20))


def test_api_roundtrip_and_feedback_restore(database):
    def override():
        with Session(database) as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        # No lifespan startup: schema lives in the isolated test engine only.
        client = TestClient(app)
        payload = {'user_id': 'alice', 'request_text': '推荐音乐', 'limit': 20}
        first = client.post('/api/recommend', json=payload)
        assert first.status_code == 200, first.text
        diagnostics = first.json()
        assert diagnostics['retrieval_plan']['calls']
        assert diagnostics['agent_trace']
        assert diagnostics['evaluation']['returned_count'] == len(diagnostics['tracks'])
        assert diagnostics['evaluation']['hard_constraint_violations'] == 0
        song = first.json()['tracks'][0]
        track_id = song['track_id']
        for state in ['dislike', 'like']:
            response = client.post('/api/feedback', json={'user_id': 'alice', 'track_id': track_id, 'feedback_type': state})
            assert response.status_code == 200
        assert client.get('/api/feedback/alice').json() == [{'track_id': track_id, 'feedback_type': 'like'}]
        profile = client.get('/api/preferences/alice').json()
        assert profile['learned']['liked_count'] == 1
        assert profile['learned']['disliked_count'] == 0
        assert profile['learned']['tags']
        assert client.get('/api/preferences/bob').json()['learned']['feedback_count'] == 0
        again = client.post('/api/recommend', json=payload)
        assert again.status_code == 200, again.text
        assert any(t['feedback_type'] == 'like' for t in again.json()['tracks'])
        assert 'track.getSimilar' in again.json()['used_tools']
        history = client.get('/api/history/alice').json()
        assert history[0]['request_id']
        unknown = client.post('/api/feedback', json={'user_id': 'alice', 'track_id': 'missing', 'feedback_type': 'like'})
        assert unknown.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_api_clarification_forwards_questions_without_history(database, monkeypatch):
    from app.agent.planner import Planner
    from app.schemas.agent import RetrievalPlan
    from app.models.entities import RecommendationHistory
    async def clarify(self, *args):
        return RetrievalPlan(intent=MusicIntent(), need_clarification=True,
                             clarification_questions=['必须为纯音乐，还是允许少量人声？'])
    monkeypatch.setattr(Planner, 'plan', clarify)
    def override():
        with Session(database) as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        response = TestClient(app).post('/api/recommend', json={
            'user_id': 'clarify', 'request_text': 'request', 'limit': 20})
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'needs_clarification' and data['needs_clarification']
        assert data['clarification_questions'] == data['warnings']
        assert data['evaluation']['tool_calls'] == 0 and not data['tracks']
        with Session(database) as db:
            assert db.query(RecommendationHistory).count() == 0
    finally:
        app.dependency_overrides.clear()


def test_language_candidates_get_enrichment_before_generic_candidates(database):
    service = RecommendationService()
    async def top(tag, limit):
        if tag == 'mandopop':
            return [{'id': 'zh', 'name': 'Chinese Song', 'artist': 'Singer', 'tags': []}]
        if tag in ('chinese', 'c-pop'):
            return []
        return [{'id': f'en-{i}', 'name': f'English {i}', 'artist': 'Other', 'tags': ['pop', 'english']} for i in range(35)]
    async def chart(limit):
        return []
    async def enrich(items, max_items):
        assert items[0]['id'] == 'zh'
        return [{**item, 'tags': ['mandopop']} if item['id'] == 'zh' else item for item in items]
    service.tools.top_tracks_by_tag = top
    service.tools.chart_tracks = chart
    service.tools.enrich_tracks = enrich
    with Session(database) as db:
        result = asyncio.run(service.recommend(db, 'alice', '散步', 20,
            RequestPreferences(language='chinese', scene='commute', mood='happy')))
        assert [item['track_id'] for item in result['items']] == ['zh']
        assert any('不使用其他语言' in warning for warning in result['warnings'])
        assert '歌词语言（仅有数据源标签依据）' in result['items'][0]['unverified_conditions']


def test_additive_migration_preserves_old_data_and_is_idempotent(database):
    with Session(database) as db:
        user = get_or_create_user(db, 'existing')
        user.preferences.preferred_genres = 'jazz'
        db.commit()
    with database.begin() as connection:
        connection.execute(text('ALTER TABLE user_preferences DROP COLUMN learned_profile_json'))
    init_db(database)
    init_db(database)
    with Session(database) as db:
        assert db.query(UserPreference).one().preferred_genres == 'jazz'
        assert db.query(UserPreference).one().learned_profile_json == '{}'
