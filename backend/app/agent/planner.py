import asyncio
import json
from time import perf_counter

import httpx

from app.core.config import get_settings
from app.schemas.agent import RetrievalPlan, RetrievalDecision, HardConstraints, ToolCall
from app.schemas.music import MusicIntent
from app.services.intent_service import deterministic_intent
from app.services.query_service import build_search_queries
from app.agent.prompts import SYSTEM_PROMPT, ITERATION_PROMPT
from app.agent.telemetry import emit


class Planner:
    def __init__(self):
        self.mode = 'fallback'
        self.available = True

    async def ask(self, schema, system, context):
        settings = get_settings()
        if not self.available or settings.llm_mock_mode or not all((settings.llm_api_key, settings.llm_base_url, settings.llm_model)):
            emit('llm_skipped', stage=schema.__name__, reason='unavailable_or_not_configured')
            return None
        started = perf_counter()
        usage = None
        try:
            async with asyncio.timeout(settings.agent_llm_timeout_seconds):
                async with httpx.AsyncClient(timeout=settings.agent_llm_timeout_seconds) as client:
                    response = await client.post(settings.llm_base_url.rstrip('/') + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + settings.llm_api_key}, json={
                            'model': settings.llm_model, 'temperature': 0,
                            'response_format': {'type': 'json_object'}, 'max_tokens': 2200,
                            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(
                                {**context, 'output_schema': schema.model_json_schema()}, ensure_ascii=False)}]})
                    response.raise_for_status()
                    body = response.json()
            raw_usage = body.get('usage', {})
            usage = {key: raw_usage[key] for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                     if isinstance(raw_usage.get(key), int)} if isinstance(raw_usage, dict) else {}
            choice = body['choices'][0]
            if choice.get('finish_reason') == 'length' or choice['message'].get('refusal'):
                raise ValueError('Incomplete or refused output')
            result = schema.model_validate_json(choice['message']['content'])
            emit('llm_call', stage=schema.__name__, status='ok', usage=usage,
                 duration_ms=round((perf_counter() - started) * 1000))
            return result
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, IndexError) as exc:
            self.available = False
            emit('llm_call', stage=schema.__name__, status='fallback', error_type=type(exc).__name__,
                 usage=usage, duration_ms=round((perf_counter() - started) * 1000))
            return None

    async def plan(self, text, controls, stored, learned, seeds, target_count=20):
        fallback = deterministic_intent(text, controls, stored)
        plan = await self.ask(RetrievalPlan, SYSTEM_PROMPT, {'request_text': text,
            'explicit_preferences': controls.model_dump(), 'stored_preferences': stored.model_dump(),
            'learned_preferences': {k: v[:12] if isinstance(v, list) else v for k, v in learned.items()},
            'supplied_seeds': seeds, 'target_count': target_count,
            'budgets': {'max_rounds': get_settings().agent_max_rounds,
                        'calls_per_round': get_settings().agent_calls_per_round,
                        'max_tool_calls': get_settings().agent_max_tool_calls}})
        self.mode = 'llm' if plan else ('fallback' if get_settings().llm_api_key else 'mock')
        if plan is None:
            plan = RetrievalPlan(intent=fallback, strategy='规则回退：按本次条件检索并保留硬条件', confidence=0,
                hard_constraints=HardConstraints(language=fallback.language,
                    exclude_artists=fallback.avoid_artists, exclude_tags=fallback.exclude_tags))
        data = plan.intent.model_dump()
        for field in ('scene', 'mood', 'language', 'energy', 'familiarity'):
            if getattr(controls, field) != 'any':
                data[field] = getattr(controls, field)
        if controls.genres:
            data['genres'] = controls.genres
        # Freeze resolved language consistently in both representations.
        language = controls.language if controls.language != 'any' else (
            plan.hard_constraints.language if plan.hard_constraints.language != 'any' else data['language'])
        data['language'] = language
        plan.intent = MusicIntent.model_validate(data)
        plan.hard_constraints.language = language
        plan.hard_constraints.exclude_artists = list(dict.fromkeys(
            plan.hard_constraints.exclude_artists + plan.intent.avoid_artists + stored.disliked_artists))[:32]
        plan.hard_constraints.exclude_tags = list(dict.fromkeys(
            plan.hard_constraints.exclude_tags + plan.intent.exclude_tags + stored.disliked_genres))[:32]
        required = {t.casefold() for t in plan.hard_constraints.required_tags}
        excluded = {t.casefold() for t in plan.hard_constraints.exclude_tags}
        if required & excluded:
            plan.need_clarification = True
            plan.clarification_questions = ['必须包含和排除的标签有冲突，请明确希望保留哪项条件。']
        if not plan.calls:
            plan.calls = self.fallback_calls(plan, seeds)[:8]
        emit('plan', mode=self.mode, plan=plan.model_dump())
        return plan

    def fallback_calls(self, plan, seeds):
        tags = [ToolCall(tool='top_tracks_by_tag', tag=tag) for tag in build_search_queries(plan.intent)]
        similar = [ToolCall(tool='similar_tracks', seed_id=s['id']) for s in seeds[:3]]
        calls = tags[:3] + similar + tags[3:] if plan.intent.language != 'any' else similar + tags
        calls += [ToolCall(tool='chart_tracks')]
        return calls

    async def decide(self, plan, state, seeds):
        if self.mode != 'llm':
            return None
        return await self.ask(RetrievalDecision, ITERATION_PROMPT, {
            'frozen_plan': plan.model_dump(), 'observation': state, 'supplied_seeds': seeds})
