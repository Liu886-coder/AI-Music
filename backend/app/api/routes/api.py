from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.music import *
from app.agent.agent import MusicAgent
from app.services.preference_service import get_or_create_user, preference_from_db, update_preferences
from app.services.feedback_service import record_feedback
from app.services.learning_service import rebuild_profile, latest_feedback
from app.models.entities import RecommendationHistory
from app.core.config import get_settings
import json
import asyncio

router = APIRouter(prefix="/api")
agent = MusicAgent()

@router.get("/health")
async def health():
    settings = get_settings()
    configured = settings.mock_mode or bool(settings.lastfm_api_key.strip())
    return {"status": "ok" if configured else "configuration_error", "mock_mode": settings.mock_mode}

@router.post("/recommend", response_model=RecommendationResponse)
async def recommend(payload: RecommendationRequest, db: Session = Depends(get_db)):
    try:
        settings = get_settings()
        if not settings.mock_mode and not settings.lastfm_api_key.strip():
            raise HTTPException(503, detail="Last.fm 实时模式需要在 backend/.env 中配置 LASTFM_API_KEY。")
        result = await asyncio.wait_for(
            agent.run(db, payload.user_id, payload.request_text, payload.limit, payload.preferences, payload.previous_track_ids),
            timeout=settings.recommendation_timeout_seconds,
        )
        items = result['items']
        warnings = result.get('warnings', [])
        needs_clarification = result.get('needs_clarification', False)
        if len(items) < payload.limit and not needs_clarification:
            warnings.append(f"当前数据源只返回了 {len(items)} 首匹配歌曲（目标 {payload.limit} 首），可以放宽场景或流派条件后重试。")
        status = 'ok' if len(items) >= payload.limit else ('partial' if items else 'empty')
        if needs_clarification:
            status = 'needs_clarification'
        settings = get_settings()
        return RecommendationResponse(request_id=result['request_id'], intent_summary=result['intent'].intent_summary,
            intent=result['intent'], tracks=items, request_summary=payload.request_text, recommendations=items,
            used_tools=result.get('used_tools', []),
            retrieval_plan=result.get('retrieval_plan', {}), agent_trace=result.get('agent_trace', []),
            evaluation=result.get('evaluation', {}), needs_clarification=needs_clarification,
            clarification_questions=result.get('clarification_questions', []),
            warnings=warnings, status=status, parser_mode=result['parser_mode'], music_mode='mock' if settings.mock_mode else 'lastfm')
    except TimeoutError as exc:
        db.rollback()
        raise HTTPException(504, detail="推荐请求超时，请检查 Last.fm / LLM 服务的网络连接后重试。") from exc
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(502, detail=f"推荐服务暂时不可用：{exc}")

@router.post("/feedback", response_model=FeedbackResponse)
def feedback(payload: FeedbackRequest, db: Session = Depends(get_db)):
    try:
        record_feedback(db, payload.user_id, payload.track_id, payload.feedback_type)
    except ValueError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return FeedbackResponse(ok=True, message="反馈已记录，将影响后续推荐")

@router.get('/feedback/{user_id}', response_model=list[FeedbackState])
def feedback_states(user_id: str, db: Session = Depends(get_db)):
    return [FeedbackState(track_id=key, feedback_type=row.feedback_type)
            for key, row in latest_feedback(db, user_id).items()]

@router.get("/preferences/{user_id}", response_model=PreferenceResponse)
def preferences(user_id: str, db: Session = Depends(get_db)):
    profile = rebuild_profile(db, user_id)
    user = get_or_create_user(db, user_id)
    db.commit()
    return PreferenceResponse(**preference_from_db(user).model_dump(), learned=profile)

@router.put("/preferences/{user_id}", response_model=MusicPreference)
def put_preferences(user_id: str, payload: PreferenceUpdate, db: Session = Depends(get_db)):
    return update_preferences(db, user_id, payload)

@router.get("/history/{user_id}", response_model=list[HistoryItem])
def history(user_id: str, db: Session = Depends(get_db)):
    rows = db.query(RecommendationHistory).filter_by(user_id=user_id).order_by(RecommendationHistory.created_at.desc()).limit(50).all()
    return [HistoryItem(id=x.id, request_id=x.request_id, rank_position=x.rank_position, intent=json.loads(x.intent_json or '{}'), track_id=x.track_id, track_name=x.track.name if x.track else "", artist_name=x.track.artist_name if x.track else "", score=x.score, reason=x.reason, request_text=x.request_text, created_at=x.created_at.isoformat()) for x in rows]
