import asyncio
from time import perf_counter
from app.schemas.agent import ToolCall
from app.core.config import get_settings
from app.clients.lastfm_client import MusicClientError
from app.agent.telemetry import emit

API_NAMES = {'top_tracks_by_tag': 'tag.getTopTracks', 'search_tracks': 'track.search',
             'similar_tracks': 'track.getSimilar', 'chart_tracks': 'chart.getTopTracks'}


class ToolExecutor:
    def __init__(self, tools, seeds):
        self.tools = tools
        self.seeds = {s['id']: s for s in seeds}

    async def execute(self, call: ToolCall):
        call = ToolCall.model_validate(call.model_dump())
        started = perf_counter()
        try:
            async with asyncio.timeout(get_settings().agent_tool_timeout_seconds):
                if call.tool == 'top_tracks_by_tag':
                    items = await self.tools.top_tracks_by_tag(call.tag, call.limit)
                elif call.tool == 'search_tracks':
                    items = await self.tools.search_tracks(call.query, call.limit)
                elif call.tool == 'chart_tracks':
                    items = await self.tools.chart_tracks(call.limit)
                else:
                    if call.seed_id not in self.seeds:
                        raise ValueError('Unknown seed id')
                    seed = self.seeds[call.seed_id]
                    items = await self.tools.similar_tracks(seed['artist'], seed['name'], call.limit)
                    items = [{**item, 'similar_to': [seed['name'] + ' · ' + seed['artist']]}
                             for item in items if (item.get('name'), item.get('artist')) != (seed['name'], seed['artist'])]
            items = items[:call.limit]
            record = {'call': call.model_dump(), 'status': 'ok', 'returned': len(items)}
        except (MusicClientError, TimeoutError, ValueError) as exc:
            items = []
            record = {'call': call.model_dump(), 'status': 'error', 'error_type': type(exc).__name__, 'returned': 0}
        except asyncio.CancelledError:
            emit('tool_call', call=call.model_dump(), status='cancelled',
                 duration_ms=round((perf_counter() - started) * 1000))
            raise
        record['duration_ms'] = round((perf_counter() - started) * 1000)
        emit('tool_call', **record)
        return items, record
