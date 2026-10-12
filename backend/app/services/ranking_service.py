from dataclasses import dataclass, field
from app.schemas.music import MusicPreference, LearnedProfile, MusicIntent
from app.services.query_service import intent_tag_groups, language_evidence


@dataclass
class Ranked:
    item: dict
    score: float
    reason: str
    evidence: list[str] = field(default_factory=list)
    request_matches: int = 0


class RankingService:
    def rank(self, candidates: list[dict], preference: MusicPreference, limit: int, explicit_single_artist: bool = False,
             feedback_liked: set[str] | None = None, feedback_disliked: set[str] | None = None,
             previous_track_ids: set[str] | None = None, learned: LearnedProfile | None = None,
             intent: MusicIntent | None = None, stored: MusicPreference | None = None) -> list[Ranked]:
        liked, disliked, previous = feedback_liked or set(), feedback_disliked or set(), previous_track_ids or set()
        learned, stored = learned or LearnedProfile(), stored or MusicPreference()
        tag_signals = {s.name.casefold(): s.score for s in learned.tags}
        artist_signals = {s.name.casefold(): s.score for s in learned.artists}
        if intent:
            groups = intent_tag_groups(intent)
            if not groups and intent.include_tags:
                groups = [{t.casefold() for t in intent.include_tags}]
        else:
            groups = [{x.casefold() for x in preference.preferred_genres + preference.preferred_languages
                       + preference.preferred_moods + preference.preferred_scenes}]
            groups = [g for g in groups if g]
        seen, ranked = set(), []
        for item in candidates:
            artist = item.get('artist', '').strip().casefold()
            key = (item.get('name', '').strip().casefold(), artist)
            if key in seen:
                continue
            seen.add(key)
            track_id = item.get('id', '')
            tags = {t.strip().casefold() for t in item.get('tags', []) if t.strip()}
            language_hits = language_evidence(item, intent.language) if intent and intent.language != 'any' else []
            if intent and intent.language != 'any' and not language_hits:
                continue
            excluded_artists = preference.disliked_artists + stored.disliked_artists + stored.excluded_artists
            excluded_tags = preference.disliked_genres + stored.disliked_genres + (intent.exclude_tags if intent else [])
            if (track_id in disliked or track_id in stored.excluded_tracks
                    or artist in {a.casefold() for a in excluded_artists}
                    or tags & {g.casefold() for g in excluded_tags}):
                continue
            hits = sorted(set().union(*(g & tags for g in groups))) if groups else []
            matches = sum(bool(g & tags) for g in groups)
            if artist in {a.casefold() for a in preference.preferred_artists}:
                matches += 1
            evidence = [f'本次条件匹配标签：{", ".join(hits)}'] if hits else []
            if language_hits:
                evidence.append('语言筛选依据（数据源标签）：' + ', '.join(language_hits))
            learned_hits = [(t, tag_signals[t]) for t in sorted(tags) if tag_signals.get(t)]
            artist_score = artist_signals.get(artist, 0)
            history_score = max(-.08, min(.08, sum(s for _, s in learned_hits) * .015))
            history_score += max(-.04, min(.04, artist_score * .02))
            for tag, signal in learned_hits[:4]:
                evidence.append(f'历史反馈{"偏好" if signal > 0 else "降低偏好"}标签 {tag}（净支持 {signal:+d} 首）')
            if artist_score:
                evidence.append(f'历史反馈艺人：{item.get("artist")}（净支持 {artist_score:+d} 首）')
            manual_hits = tags & {x.casefold() for x in stored.preferred_genres + stored.preferred_languages
                                 + stored.preferred_moods + stored.preferred_scenes}
            manual_artist = artist in {a.casefold() for a in stored.preferred_artists}
            if manual_hits or manual_artist:
                evidence.append('匹配你手动保存的长期偏好')
            history_score += min(.04, len(manual_hits) * .01 + .02 * manual_artist)
            if track_id in liked:
                history_score += .03
                evidence.append('你曾喜欢或收藏这首歌')
            similar = item.get('similar_to', [])
            if similar:
                history_score += .02
                evidence.append('相似推荐来源：' + '、'.join(similar[:2]))
            score = .15 + .55 * matches / max(len(groups) + bool(preference.preferred_artists), 1) + history_score
            if track_id in previous:
                score -= .06
                evidence.append('上一批已出现，本次降低排序')
            reason = '；'.join(evidence) or '候选歌曲缺少可验证的匹配证据，作为探索结果展示'
            ranked.append(Ranked(item, round(max(0, min(1, score)), 3), reason, evidence, matches))
        # Current-request matches precede historical boosts; diversity operates within that tier.
        result, artists = [], {}
        while ranked and len(result) < limit:
            row = max(ranked, key=lambda r: (r.request_matches,
                      r.score - (0 if explicit_single_artist else .015 * artists.get(r.item.get('artist', '').casefold(), 0))))
            ranked.remove(row)
            result.append(row)
            artist = row.item.get('artist', '').casefold()
            artists[artist] = artists.get(artist, 0) + 1
        return result
