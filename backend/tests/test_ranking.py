from app.services.ranking_service import RankingService
from app.schemas.music import MusicPreference

def test_deduplicate_and_rank():
    rows = [{"id":"1","name":"A","artist":"X","tags":["calm"]},{"id":"1b","name":"A","artist":"X","tags":["calm"]},{"id":"2","name":"B","artist":"Y","tags":["rock"]}]
    result = RankingService().rank(rows, MusicPreference(preferred_moods=["calm"]), 5)
    assert len(result) == 2 and result[0].item["name"] == "A"

def test_disliked_artist_filtered():
    rows = [{"id":"1","name":"A","artist":"X","tags":[]}]
    assert not RankingService().rank(rows, MusicPreference(disliked_artists=["X"]), 5)
