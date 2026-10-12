import re
from sqlalchemy.orm import Session
from app.models.entities import User, UserPreference, Feedback
from app.schemas.music import MusicPreference, PreferenceUpdate

KEYWORDS = {
 "chinese": ["中文", "华语"], "english": ["欧美", "英文", "英语"], "rock": ["摇滚", "rock"],
 "calm": ["安静", "平静", "放松", "学习", "晚上"], "workout": ["跑步", "运动", "健身"],
 "sad": ["悲伤", "伤感"], "happy": ["开心", "快乐"], "energetic": ["活力", "激情", "节奏感"]
}

def split_text(value: str) -> list[str]: return [x.strip() for x in value.split(",") if x.strip()]

def get_or_create_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if not user:
        user = User(id=user_id, username=user_id); db.add(user); db.flush()
        user.preferences = UserPreference(user_id=user_id)
        db.flush()
    elif user.preferences is None:
        user.preferences = UserPreference(user_id=user_id)
        db.flush()
    return user

def preference_from_db(user: User) -> MusicPreference:
    p = user.preferences or UserPreference(user_id=user.id)
    return MusicPreference(**{k: split_text(getattr(p, k, "")) for k in ["preferred_artists","disliked_artists","preferred_genres","disliked_genres","preferred_languages","preferred_moods","preferred_scenes"]}, energy_level=p.energy_level, tempo_preference=p.tempo_preference)

def infer_preference(message: str) -> MusicPreference:
    lower = message.lower(); p = MusicPreference()
    for tag, words in KEYWORDS.items():
        if any(w.lower() in lower for w in words):
            if tag in ("chinese", "english"): p.preferred_languages.append(tag)
            elif tag in ("rock",): p.preferred_genres.append(tag)
            elif tag in ("calm", "sad", "happy", "energetic"): p.preferred_moods.append(tag)
            elif tag == "workout": p.preferred_scenes.append(tag)
    # 轻量抽取：常见“喜欢/不要/不喜欢 + 人名或流派”
    for marker, target in [("不喜欢", p.disliked_genres), ("不要", p.disliked_artists), ("喜欢", p.preferred_artists)]:
        m = re.search(marker + r"([\u4e00-\u9fffA-Za-z0-9 ]{2,12})", message)
        if m: target.append(m.group(1).strip())
    return p

def update_preferences(db: Session, user_id: str, data: PreferenceUpdate) -> MusicPreference:
    user = get_or_create_user(db, user_id); p = user.preferences
    for field in ["preferred_artists","disliked_artists","preferred_genres","disliked_genres","preferred_languages","preferred_moods","preferred_scenes"]: setattr(p, field, ",".join(getattr(data, field)))
    p.energy_level, p.tempo_preference = data.energy_level, data.tempo_preference
    db.commit(); db.refresh(user); return preference_from_db(user)
