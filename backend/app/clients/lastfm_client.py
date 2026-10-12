from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MusicClientError(Exception):
    pass


def _connection_permission_denied(exc: BaseException) -> bool:
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OSError) and (
            exc.errno in (errno.EACCES, errno.EPERM) or getattr(exc, 'winerror', None) == 10013
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class LastFMClient:
    base_url = "https://ws.audioscrobbler.com/2.0/"

    def _mock_tracks(self) -> list[dict[str, Any]]:
        return json.loads((Path(__file__).parents[1] / "data/mock_tracks.json").read_text(encoding="utf-8"))

    @staticmethod
    def _image_url(images: Any) -> str:
        if not isinstance(images, list):
            return ""
        for image in reversed(images):
            if isinstance(image, dict) and image.get("#text"):
                return image["#text"]
        return ""

    @staticmethod
    def _track_id(name: str, artist: str, mbid: str = "", url: str = "") -> str:
        identity = mbid or url or f"{artist}:{name}"
        if not identity:
            identity = hashlib.sha1(f"{artist}:{name}".encode("utf-8")).hexdigest()
        return f"lastfm-{identity}"

    def _normalise_track(self, item: dict[str, Any]) -> dict[str, Any]:
        artist_value = item.get("artist", "")
        artist = artist_value.get("name", "") if isinstance(artist_value, dict) else str(artist_value or "")
        album_value = item.get("album", "")
        album = album_value.get("title", "") if isinstance(album_value, dict) else str(album_value or "")
        name = str(item.get("name", "") or "").strip()
        url = str(item.get("url", "") or "")
        tags = item.get("tags", [])
        if isinstance(tags, dict):
            tags = tags.get("tag", [])
        if isinstance(tags, list):
            tags = [t.get("name", "") if isinstance(t, dict) else str(t) for t in tags]
        else:
            tags = []
        return {
            "id": self._track_id(name, artist, str(item.get("mbid", "") or ""), url),
            "name": name,
            "artist": artist.strip(),
            "album": album.strip(),
            "tags": [tag.strip().lower() for tag in tags if tag and tag.strip()],
            "url": url,
            "image_url": self._image_url(item.get("image", [])),
        }

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        if not settings.lastfm_api_key.strip():
            raise MusicClientError('Last.fm 实时模式需要在 backend/.env 中配置 LASTFM_API_KEY。')
        params = {**params, "api_key": settings.lastfm_api_key, "format": "json"}
        try:
            async with httpx.AsyncClient(timeout=12, headers={"User-Agent": "MusicRecommendationAgent/1.0"}) as client:
                response = await client.get(self.base_url, params=params)
                response.raise_for_status()
                data = response.json()
            if data.get("error"):
                raise MusicClientError(data.get("message", "Last.fm 请求失败"))
            return data
        except MusicClientError:
            raise
        except httpx.TimeoutException as exc:
            raise MusicClientError('连接 Last.fm 超时，请检查后端网络和代理设置。') from exc
        except (httpx.ConnectError, httpx.ProxyError) as exc:
            logger.warning("Last.fm connection failed: %s", type(exc).__name__)
            if _connection_permission_denied(exc):
                message = '后端连接 Last.fm 被系统或运行环境拒绝（套接字权限错误），请在普通终端启动后端并检查网络和代理设置。'
            elif isinstance(exc, httpx.ProxyError):
                message = 'Last.fm 代理连接失败，请检查后端的代理设置。'
            else:
                message = '后端无法连接 Last.fm，请检查网络、DNS 和代理设置；尚未验证 API Key 是否有效。'
            raise MusicClientError(message) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("Last.fm HTTP error: %s", status)
            raise MusicClientError(f'Last.fm 返回 HTTP {status}，请检查服务状态及访问配置。') from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Last.fm request failed: %s", type(exc).__name__)
            raise MusicClientError("音乐数据服务暂时不可用") from exc

    def _is_mock(self) -> bool:
        settings = get_settings()
        return settings.mock_mode

    async def search_tracks(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if self._is_mock():
            data = self._mock_tracks()
            terms = set(query.lower().split())
            return [x for x in data if not terms or any(t in (x["name"] + x["artist"] + " ".join(x["tags"])).lower() for t in terms)][:limit] or data[:limit]
        data = await self._request({"method": "track.search", "track": query, "limit": limit})
        matches = data.get("results", {}).get("trackmatches", {}).get("track", [])
        if isinstance(matches, dict):
            matches = [matches]
        return [self._normalise_track(item) for item in matches if item.get("name")]

    async def top_tracks_by_tag(self, tag: str, limit: int = 30) -> list[dict[str, Any]]:
        if self._is_mock():
            return [x for x in self._mock_tracks() if tag.lower() in {t.lower() for t in x.get("tags", [])}][:limit]
        data = await self._request({"method": "tag.getTopTracks", "tag": tag, "limit": limit})
        tracks = data.get("tracks", {}).get("track", [])
        return [self._normalise_track(item) for item in tracks if item.get("name")]

    async def chart_tracks(self, limit: int = 30) -> list[dict[str, Any]]:
        if self._is_mock():
            return self._mock_tracks()[:limit]
        data = await self._request({"method": "chart.getTopTracks", "limit": limit})
        tracks = data.get("tracks", {}).get("track", [])
        return [self._normalise_track(item) for item in tracks if item.get("name")]

    async def get_track_info(self, artist: str, track: str) -> dict[str, Any]:
        if self._is_mock():
            results = await self.search_tracks(f"{artist} {track}", 1)
            return results[0] if results else {}
        data = await self._request({"method": "track.getInfo", "artist": artist, "track": track})
        item = data.get("track", {})
        if not item:
            return {}
        item["tags"] = item.get("toptags", {}).get("tag", [])
        return self._normalise_track(item)

    async def enrich_tracks(self, candidates: list[dict[str, Any]], max_items: int = 30) -> list[dict[str, Any]]:
        if self._is_mock():
            return candidates
        semaphore = asyncio.Semaphore(5)

        async def enrich(item: dict[str, Any]) -> dict[str, Any]:
            if item.get('tags'):
                return item
            async with semaphore:
                try:
                    details = await self.get_track_info(item.get("artist", ""), item.get("name", ""))
                    return {**item, **{key: value for key, value in details.items() if key != 'id' and value not in ("", [], None)}}
                except MusicClientError:
                    return item

        head = candidates[:max_items]
        tail = candidates[max_items:]
        return [*await asyncio.gather(*(enrich(item) for item in head)), *tail]

    async def similar_tracks(self, artist: str, track: str, limit: int = 10) -> list[dict[str, Any]]:
        if self._is_mock():
            data = self._mock_tracks()
            seed = next((x for x in data if x['name'].casefold() == track.casefold()
                         and x['artist'].casefold() == artist.casefold()), None)
            if not seed:
                return []
            tags = set(seed.get('tags', []))
            similar = [x for x in data if x['id'] != seed['id'] and
                       (tags & set(x.get('tags', [])) or x['artist'] == artist)]
            return sorted(similar, key=lambda x: len(tags & set(x.get('tags', []))), reverse=True)[:limit]
        data = await self._request({"method": "track.getSimilar", "artist": artist, "track": track, "limit": limit})
        tracks = data.get("similartracks", {}).get("track", [])
        return [self._normalise_track(item) for item in tracks if item.get("name")]
