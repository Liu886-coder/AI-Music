from sqlalchemy.orm import Session
from app.models.entities import Feedback, Track
from app.services.preference_service import get_or_create_user
from app.services.learning_service import rebuild_profile

def record_feedback(db: Session, user_id: str, track_id: str, feedback_type: str):
    if not db.get(Track, track_id):
        raise ValueError('歌曲不存在，请先获取推荐结果。')
    get_or_create_user(db, user_id)
    db.add(Feedback(user_id=user_id, track_id=track_id, feedback_type=feedback_type))
    db.flush()
    rebuild_profile(db, user_id)
    db.commit()
