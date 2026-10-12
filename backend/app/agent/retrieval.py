import asyncio
from collections import Counter
from time import perf_counter

from app.agent.planner import Planner
from app.agent.state import AgentState
from app.agent.telemetry import emit
from app.agent.tool_executor import ToolExecutor, API_NAMES
from app.core.config import get_settings
from app.schemas.music import MusicPreference
from app.services.query_service import language_evidence, LANGUAGE_EVIDENCE_TAGS
from app.services.learning_service import track_key
from app.services.ranking_service import RankingService
from app.clients.lastfm_client import MusicClientError


def rejection_reasons(item, hard, stored, feedback):
    tags = {t.strip().casefold() for t in item.get('tags', [])}
    artist = item.get('artist', '').strip().casefold()
    reasons = []
    if hard.language != 'any' and not language_evidence(item, hard.language):
        reasons.append('language_evidence_missing')
    if artist in {a.casefold() for a in hard.exclude_artists + stored.disliked_artists + stored.excluded_artists}:
        reasons.append('excluded_artist')
    if tags & {t.casefold() for t in hard.exclude_tags + stored.disliked_genres}:
        reasons.append('excluded_tag')
    if not {t.casefold() for t in hard.required_tags} <= tags:
        reasons.append('required_tag_missing')
    if feedback.get(track_key(item.get('name', ''), item.get('artist', ''))) == 'dislike' or item.get('id') in stored.excluded_tracks:
        reasons.append('excluded_track')
    return reasons


class RetrievalAgent:
    def __init__(self, tools, planner=None):
        self.tools = tools
        self.planner = planner or Planner()

    async def run(self, user_id, text, controls, stored, learned, seeds, feedback, known, limit, previous):
        settings = get_settings()
        started = perf_counter()
        state = AgentState(user_id=user_id, message=text)
        plan = await self.planner.plan(text, controls, stored, learned.model_dump(), seeds, limit)
        warnings, records, rows = [], [], []
        if plan.need_clarification:
            state.stop_reason = 'clarification_required'
            state.evaluation = {'stop_reason': state.stop_reason, 'returned_count': 0,
                'target_count': limit, 'rounds': 0, 'tool_calls': 0, 'hard_constraint_violations': 0}
            emit('evaluation', **state.evaluation)
            warnings.extend(plan.clarification_questions)
            return plan, state, rows, warnings, self.planner.mode
        executor = ToolExecutor(self.tools, seeds)
        seen_calls, attempted_details, merged = set(), set(), {}
        pending = list(plan.calls)
        enrichment_remaining = settings.agent_enrichment_limit
        preference = MusicPreference(preferred_artists=plan.intent.preferred_artists,
            preferred_genres=plan.intent.genres, disliked_artists=plan.hard_constraints.exclude_artists)

        def evaluate():
            rejected, eligible = Counter(), []
            for item in merged.values():
                reasons = rejection_reasons(item, plan.hard_constraints, stored, feedback)
                if reasons:
                    rejected.update(reasons)
                else:
                    eligible.append(item)
            liked = {i['id'] for i in eligible if i.get('feedback_type') in ('like', 'save')}
            ranked = RankingService().rank(eligible, preference, limit, feedback_liked=liked,
                previous_track_ids=set(previous or []), learned=learned, intent=plan.intent, stored=stored)
            return ranked, {'raw_count': sum(r['returned'] for r in records), 'unique_count': len(merged),
                'eligible_count': len(eligible), 'rejected_counts': dict(rejected), 'returned_count': len(ranked),
                'target_count': limit, 'fill_rate': len(ranked) / limit,
                'unique_artist_count': len({r.item['artist'].casefold() for r in ranked}),
                'previous_overlap_count': sum(r.item['id'] in set(previous or []) for r in ranked),
                'mean_request_matches': sum(r.request_matches for r in ranked) / len(ranked) if ranked else 0,
                'hard_constraint_violations': sum(bool(rejection_reasons(r.item, plan.hard_constraints, stored, feedback)) for r in ranked),
                'evidence_scope': 'community_tags_not_verified_audio_or_lyrics'}

        def choose(calls):
            picked = []
            for call in calls:
                if call.signature() in seen_calls:
                    emit('tool_rejected', reason='duplicate', call=call.model_dump())
                    continue
                if len(picked) >= min(settings.agent_calls_per_round, settings.agent_max_tool_calls - len(seen_calls)):
                    break
                picked.append(call)
                # Local duplicate suppression, without consuming future calls.
                if sum(c.signature() == call.signature() for c in picked) > 1:
                    picked.pop()
            return picked

        try:
            async with asyncio.timeout(settings.agent_retrieval_timeout_seconds):
                for round_number in range(1, settings.agent_max_rounds + 1):
                    if round_number > 1:
                        observation = {**state.evaluation, 'rounds': state.rounds,
                            'executed_calls': state.executed_calls,
                            'remaining_tool_calls': settings.agent_max_tool_calls - len(seen_calls),
                            'remaining_rounds': settings.agent_max_rounds - round_number + 1}
                        decision = await self.planner.decide(plan, observation, seeds)
                        if decision:
                            emit('decision', round=round_number, decision=decision.model_dump())
                            if decision.action == 'finish':
                                state.stop_reason = 'planner_finished'
                                break
                            pending = decision.calls
                        else:
                            pending = list(plan.calls) + self.planner.fallback_calls(plan, seeds)
                    calls = choose(pending)
                    if not calls:
                        state.stop_reason = 'no_new_calls'
                        break
                    for call in calls:
                        seen_calls.add(call.signature())
                        state.executed_calls.append(call.model_dump())
                        state.used_tools.append(API_NAMES[call.tool])
                    before = len(merged)
                    async def execute_and_merge(call):
                        items, record = await executor.execute(call)
                        records.append(record)
                        if record['status'] == 'error':
                            warnings.append('部分音乐检索失败：' + record['error_type'])
                        for item in items:
                            key = track_key(item.get('name', ''), item.get('artist', ''))
                            if not all(key) or not item.get('id'):
                                continue
                            item = {**item, 'language_query': call.tag.casefold() in LANGUAGE_EVIDENCE_TAGS.get(plan.intent.language, set())}
                            if key in merged:
                                old = merged[key]
                                item = {**old, 'tags': list(dict.fromkeys(old.get('tags', []) + item.get('tags', []))),
                                    'similar_to': list(dict.fromkeys(old.get('similar_to', []) + item.get('similar_to', []))),
                                    'language_query': old.get('language_query') or item['language_query']}
                            cached = known.get(key)
                            if cached:
                                item['id'] = cached.id
                                item['tags'] = list(dict.fromkeys(item.get('tags', []) + [t for t in cached.tags.split(',') if t]))
                                item['album'] = item.get('album') or cached.album_name
                            item['feedback_type'] = feedback.get(key)
                            merged[key] = item
                        return items, record

                    results = await asyncio.gather(*(execute_and_merge(c) for c in calls))
                    detail = sorted((i for k, i in merged.items() if k not in attempted_details and not i.get('tags')),
                        key=lambda i: (not i.get('language_query'), not bool(i.get('similar_to'))))[:enrichment_remaining]
                    if detail:
                        attempted_details.update(track_key(i['name'], i['artist']) for i in detail)
                        enrichment_remaining -= len(detail)
                        detail_started = perf_counter()
                        try:
                            async with asyncio.timeout(settings.agent_tool_timeout_seconds):
                                enriched = await self.tools.enrich_tracks(detail, max_items=len(detail))
                            for item in enriched:
                                key = track_key(item['name'], item['artist'])
                                if key in merged:
                                    merged[key] = {**merged[key], **item}
                            with_tags = sum(bool(item.get('tags')) for item in enriched)
                            emit('enrichment', attempted=len(detail), status='completed',
                                 with_tags=with_tags, without_tags=len(detail)-with_tags,
                                 duration_ms=round((perf_counter()-detail_started)*1000))
                        except (TimeoutError, MusicClientError):
                            warnings.append('部分歌曲详情未能补全；缺少硬条件依据的歌曲将排除。')
                            emit('enrichment', attempted=len(detail), status='failed')
                    rows, state.evaluation = evaluate()
                    state.rounds.append({'round': round_number, 'new_unique_count': len(merged)-before,
                                         **state.evaluation, 'tool_results': [r for _, r in results]})
                    emit('retrieval_round', **state.rounds[-1])
                    if len(rows) >= limit:
                        state.stop_reason = 'target_met'
                        break
                    if len(seen_calls) >= settings.agent_max_tool_calls:
                        state.stop_reason = 'tool_budget'
                        break
                else:
                    state.stop_reason = 'round_budget'
        except TimeoutError:
            state.stop_reason = 'retrieval_deadline'
            warnings.append('检索达到时间预算，返回已取得的合格结果。')
        except asyncio.CancelledError:
            rows, state.evaluation = evaluate()
            emit('evaluation', **state.evaluation, stop_reason='request_cancelled',
                 tool_calls=len(seen_calls), rounds=len(state.rounds))
            raise
        rows, state.evaluation = evaluate()
        state.candidates = list(merged.values())
        state.evaluation.update(stop_reason=state.stop_reason, tool_calls=len(seen_calls),
            completed_tool_calls=len(records), unfinished_tool_calls=len(seen_calls)-len(records),
            detail_candidates_attempted=len(attempted_details),
            tool_errors=sum(r['status'] == 'error' for r in records), rounds=len(state.rounds),
            duration_ms=round((perf_counter()-started)*1000))
        emit('evaluation', **state.evaluation)
        if not merged and any(r['status'] == 'error' for r in records):
            raise MusicClientError('音乐数据源检索失败；请根据请求 ID 查看工具错误日志。')
        if plan.intent.language != 'any':
            warnings.append('语言筛选依据为社区标签，尚未核验歌词语言。')
            if len(rows) < limit:
                warnings.append('语言证据不足时不使用其他语言歌曲补足数量。')
        if self.planner.mode != 'llm' or not self.planner.available:
            warnings.append('LLM 规划未启用或调用失败，已使用规则检索回退。')
        return plan, state, rows, list(dict.fromkeys(warnings)), self.planner.mode
