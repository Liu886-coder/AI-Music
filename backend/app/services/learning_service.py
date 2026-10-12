"""Rebuild explainable preferences from latest per-song feedback, never request text."""
import json

from sqlalchemy.orm import Session

from app.models.entities import Feedback, Track
from app.schemas.music import LearnedProfile, LearnedSignal
from app.services.preference_service import get_or_create_user


def track_key(name: str, artist: str) -> tuple[str, str]:
    return name.strip().casefold(), artist.strip().casefold()


def latest_feedback(db: Session, user_id: str) -> dict[str, Feedback]:
    rows = (db.query(Feedback, Track).join(Track, Feedback.track_id == Track.id)
            .filter(Feedback.user_id == user_id)
            .order_by(Feedback.created_at.desc(), Feedback.id.desc()).all())
    seen, result = set(), {}
    for feedback, track in rows:
        key = track_key(track.name, track.artist_name)
        if key not in seen:
            seen.add(key)
            result[track.id] = feedback
    return result


def rebuild_profile(db: Session, user_id: str) -> LearnedProfile:
    user = get_or_create_user(db, user_id)
    db.flush()
    latest = latest_feedback(db, user_id)
    tracks = {t.id: t for t in db.query(Track).filter(Track.id.in_(latest)).all()}
    signals = {'tags': {}, 'artists': {}}
    profile = LearnedProfile(feedback_count=len(latest))
    for track_id, feedback in latest.items():
        if feedback.feedback_type == 'skip':
            continue
        positive = feedback.feedback_type in ('like', 'save')
        profile.liked_count += int(positive)
        profile.disliked_count += int(not positive)
        track = tracks[track_id]
        tags = sorted({tag.strip().casefold() for tag in track.tags.split(',') if tag.strip()})
        artist = track.artist_name.strip()
        if not tags:
            profile.missing_metadata_count += 1
        evidence = {'track_id': track.id, 'track_name': track.name, 'artist_name': artist,
                    'feedback_type': feedback.feedback_type, 'url': track.url,
                    'source': 'mock' if track.id.startswith('mock') else 'lastfm'}
        for kind, values in [('tags', tags), ('artists', [artist] if artist and artist != 'Unknown' else [])]:
            for value in values:
                signal = signals[kind].setdefault(value.casefold(), LearnedSignal(
                    name=value, score=0, positive_count=0, negative_count=0))
                signal.score += 1 if positive else -1
                signal.positive_count += int(positive)
                signal.negative_count += int(not positive)
                if len(signal.evidence) < 5:
                    signal.evidence.append(evidence)
    for kind in signals:
        setattr(profile, kind, sorted(signals[kind].values(), key=lambda s: (-abs(s.score), s.name)))
    user.preferences.learned_profile_json = profile.model_dump_json()
    db.flush()
    return profile


def stored_profile(user) -> LearnedProfile:
    return LearnedProfile.model_validate(json.loads(user.preferences.learned_profile_json or '{}'))


def positive_tags(profile: LearnedProfile, limit: int = 3) -> list[str]:
    return [s.name for s in sorted(profile.tags, key=lambda s: (-s.score, s.name)) if s.score > 0][:limit]
