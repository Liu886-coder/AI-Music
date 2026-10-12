from __future__ import annotations

import json
from app.agent.retrieval import RetrievalAgent
from app.agent.telemetry import Trace, current_trace, sanitize
import asyncio
from sqlalchemy.orm import Session

from app.models.entities import Track, RecommendationHistory
from app.services.preference_service import get_or_create_user, preference_from_db
from app.tools.music_tools import MusicTools
from app.schemas.music import RequestPreferences
from app.services.learning_service import rebuild_profile, latest_feedback, track_key
from app.clients.lastfm_client import MusicClientError


class RecommendationService:
    def __init__(self):
        self.tools = MusicTools()

    async def recommend(self, db, user_id, message, limit, controls=None, previous_ids=None):
        trace = Trace(message, controls or RequestPreferences())
        token = current_trace.set(trace)
        try:
            result = await self._recommend(db, user_id, message, limit, controls, previous_ids)
            trace.emit('completed', status='clarification' if result['needs_clarification'] else 'ok',
                       returned_count=len(result['items']))
            return result
        except asyncio.CancelledError:
            trace.emit('cancelled', reason='request_deadline_or_cancellation')
            raise
        except Exception as exc:
            trace.emit('failed', error_type=type(exc).__name__)
            label = '音乐数据源检索失败' if isinstance(exc, MusicClientError) else '推荐未完成'
            raise MusicClientError(f'{label}（请求 ID：{trace.request_id}，{type(exc).__name__}），请查看后端日志。') from exc
        finally:
            current_trace.reset(token)

    async def _recommend(self, db: Session, user_id: str, message: str, limit: int,
                        controls: RequestPreferences | None = None, previous_ids: list[str] | None = None):
        request_id = current_trace.get().request_id
        user = get_or_create_user(db, user_id)
        db.commit()
        stored = preference_from_db(user)
        profile = rebuild_profile(db, user_id)
        db.commit()
        latest = latest_feedback(db, user_id)
        feedback_tracks = {t.id: t for t in db.query(Track).filter(Track.id.in_(latest)).all()}
        states_by_song = {track_key(feedback_tracks[key].name, feedback_tracks[key].artist_name): row.feedback_type
                          for key, row in latest.items()}
        seeds = [{'id': feedback_tracks[key].id, 'name': feedback_tracks[key].name,
                  'artist': feedback_tracks[key].artist_name} for key, row in latest.items()
                 if row.feedback_type in ('like', 'save')][:3]
        mock_source = self.tools.client._is_mock()
        known = {track_key(t.name, t.artist_name): t for t in db.query(Track).all()
                 if t.id.startswith('mock') == mock_source}
        plan, state, rows, warnings, parser_mode = await RetrievalAgent(self.tools).run(
            user_id, message, controls or RequestPreferences(), stored, profile, seeds,
            states_by_song, known, limit, previous_ids)
        intent = plan.intent
        used_tools = ['get_user_preferences', 'learned_preferences', 'plan_retrieval',
                      *dict.fromkeys(state.used_tools), 'ranking_service']
        out = []
        for rank, row in enumerate(rows, 1):
            item = row.item
            track_id = item.get('id', 'unknown')
            track = db.get(Track, track_id) or Track(id=track_id, external_id=track_id, name=item.get('name', ''), artist_name=item.get('artist', ''), album_name=item.get('album', ''), url=item.get('url', ''), image_url=item.get('image_url', ''), tags=','.join(item.get('tags', [])))
            # Save newly returned metadata even when a track was recommended before.
            track.name, track.artist_name = item.get('name', track.name), item.get('artist', track.artist_name)
            track.tags = ','.join(item.get('tags', []))
            track.album_name = item.get('album') or track.album_name
            track.url = item.get('url') or track.url
            track.image_url = item.get('image_url') or track.image_url
            db.add(track)
            db.add(RecommendationHistory(user_id=user_id, track_id=track_id, score=row.score, reason=row.reason,
                                         request_text=message, request_id=request_id,
                                         intent_json=json.dumps(intent.model_dump(), ensure_ascii=False), rank_position=rank))
            evidence = row.evidence
            unknown = [name for name, value in [('能量', intent.energy), ('熟悉度', intent.familiarity)] if value != 'any']
            if intent.language != 'any':
                unknown.append('歌词语言（仅有数据源标签依据）')
            out.append({'track_id': track_id, 'track_name': track.name, 'artist_name': track.artist_name,
                        'album_name': track.album_name, 'score': row.score, 'reason': row.reason,
                        'source': 'lastfm (mock)' if track_id.startswith('mock') else 'lastfm', 'url': track.url,
                        'image_url': track.image_url, 'evidence': evidence, 'unverified_conditions': unknown, 'feedback_type': item.get('feedback_type')})
        db.flush()
        rebuild_profile(db, user_id)
        db.commit()
        return {'warnings': list(dict.fromkeys(warnings)), 'used_tools': used_tools, 'request_id': request_id, 'intent': intent, 'parser_mode': parser_mode, 'items': out,
                'retrieval_plan': sanitize(plan.model_dump()), 'agent_trace': sanitize(state.rounds),
                'evaluation': state.evaluation, 'needs_clarification': plan.need_clarification,
                'clarification_questions': plan.clarification_questions}
