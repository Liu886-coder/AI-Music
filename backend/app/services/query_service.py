from app.schemas.music import MusicIntent
from app.services.intent_service import SCENE_TAGS, MOOD_TAGS, LANGUAGE_TAGS, GENRE_TAGS

# Language evidence must be specific; ambient/piano alone do not prove no vocals.
LANGUAGE_EVIDENCE_TAGS = {
    'chinese': {'chinese', 'mandarin', 'mandopop', 'c-pop', 'chinese pop', '华语', '国语', '中文'},
    'english': {'english', 'english language'},
    'japanese': {'japanese', 'j-pop', 'jpop'},
    'korean': {'korean', 'k-pop', 'kpop'},
    'instrumental': {'instrumental', 'instrumental music'},
}

def language_evidence(item: dict, language: str) -> list[str]:
    tags = {t.strip().casefold() for t in item.get('tags', [])}
    return sorted(tags & LANGUAGE_EVIDENCE_TAGS.get(language, set()))


def intent_tag_groups(intent: MusicIntent) -> list[set[str]]:
    groups = [SCENE_TAGS.get(intent.scene, []), MOOD_TAGS.get(intent.mood, []),
              LANGUAGE_TAGS.get(intent.language, []),
              [tag for genre in intent.genres for tag in GENRE_TAGS.get(genre, [genre])]]
    return [set(group) for group in groups if group]


def build_search_queries(intent: MusicIntent) -> list[str]:
    # Round-robin so bounded queries cover all selected dimensions.
    groups = [sorted(group) for group in intent_tag_groups(intent)]
    queries = list(LANGUAGE_TAGS.get(intent.language, []))
    queries += [group[index] for index in range(max(map(len, groups), default=0))
               for group in groups if index < len(group)]
    queries.extend(intent.include_tags)
    excluded = {tag.casefold() for tag in intent.exclude_tags}
    return list(dict.fromkeys(x.casefold() for x in queries if x and x.casefold() not in excluded)) or ['popular']
