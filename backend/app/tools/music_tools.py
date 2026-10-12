from typing import Any
from app.clients.lastfm_client import LastFMClient

class MusicTools:
    def __init__(self): self.client = LastFMClient()
    async def search_tracks(self, query: str, limit: int = 10) -> list[dict[str, Any]]: return await self.client.search_tracks(query, limit)
    async def similar_tracks(self, artist: str, track: str, limit: int = 10) -> list[dict[str, Any]]: return await self.client.similar_tracks(artist, track, limit)
    async def top_tracks_by_tag(self, tag: str, limit: int = 30) -> list[dict[str, Any]]: return await self.client.top_tracks_by_tag(tag, limit)
    async def chart_tracks(self, limit: int = 30) -> list[dict[str, Any]]: return await self.client.chart_tracks(limit)
    async def enrich_tracks(self, candidates: list[dict[str, Any]], max_items: int = 30) -> list[dict[str, Any]]: return await self.client.enrich_tracks(candidates, max_items)
    async def similar_artists(self, artist: str, limit: int = 10) -> list[dict[str, Any]]:
        tracks = await self.client.search_tracks(artist, limit * 2)
        return [{"artist": x["artist"]} for x in tracks if x["artist"].lower() != artist.lower()][:limit]
    async def get_track_info(self, artist: str, track: str) -> dict[str, Any]:
        results = await self.client.search_tracks(f"{artist} {track}", 1); return results[0] if results else {}
    async def get_artist_info(self, artist: str) -> dict[str, Any]: return {"artist": artist}
    async def get_track_tags(self, artist: str, track: str) -> list[str]:
        item = await self.get_track_info(artist, track); return item.get("tags", [])
