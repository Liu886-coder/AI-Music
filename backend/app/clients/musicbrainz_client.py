class MusicBrainzClient:
    """MusicBrainz 元数据客户端占位；MVP 推荐主要依赖 Last.fm，保留接口便于扩展。"""
    async def get_artist_info(self, artist: str) -> dict:
        return {"artist": artist, "source": "musicbrainz", "available": False}
