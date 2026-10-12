import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from app.agent.planner import Planner
from app.agent.retrieval import RetrievalAgent
from app.agent.tool_executor import ToolExecutor
from app.agent.telemetry import Trace, current_trace
from app.core.config import get_settings
from app.schemas.agent import RetrievalPlan, RetrievalDecision, HardConstraints, ToolCall
from app.schemas.music import MusicIntent, MusicPreference, LearnedProfile, RequestPreferences


class Tools:
    def __init__(self):
        self.calls = []

    async def top_tracks_by_tag(self, tag, limit):
        self.calls.append(tag)
        language = 'mandopop' if tag == 'mandopop' else 'english'
        return [{'id': tag + str(i), 'name': tag + str(i), 'artist': 'Artist ' + str(i),
                 'tags': [language, 'pop']} for i in range(min(limit, 25))]

    async def chart_tracks(self, limit):
        return await self.top_tracks_by_tag('chart', limit)

    async def enrich_tracks(self, items, max_items):
        return items


class ScriptedPlanner(Planner):
    async def plan(self, *args):
        self.mode = 'llm'
        return RetrievalPlan(intent=MusicIntent(language='chinese'),
            hard_constraints=HardConstraints(language='chinese'),
            calls=[ToolCall(tool='top_tracks_by_tag', tag='pop')])

    async def decide(self, plan, state, seeds):
        assert state['raw_count'] >= 20
        assert state['eligible_count'] == 0
        assert plan.hard_constraints.language == 'chinese'
        return RetrievalDecision(action='search', reason='Need language evidence',
            calls=[ToolCall(tool='top_tracks_by_tag', tag='mandopop')])


def run_agent(tools, planner):
    return asyncio.run(RetrievalAgent(tools, planner).run('test', '散步', RequestPreferences(language='chinese'),
        MusicPreference(), LearnedProfile(), [], {}, {}, 20, []))


def test_second_round_uses_eligible_count_and_preserves_language():
    tools = Tools()
    plan, state, rows, warnings, mode = run_agent(tools, ScriptedPlanner())
    assert tools.calls == ['pop', 'mandopop']
    assert state.stop_reason == 'target_met'
    assert len(rows) == 20 and all('mandopop' in row.item['tags'] for row in rows)
    assert state.evaluation['hard_constraint_violations'] == 0
    assert state.rounds[0]['rejected_counts']['language_evidence_missing'] == 25
    assert len(state.rounds) == 2 and mode == 'llm'


@pytest.mark.parametrize('payload', [
    {'tool': '__getattribute__'}, {'tool': 'search_tracks'},
    {'tool': 'chart_tracks', 'query': 'unexpected'},
    {'tool': 'top_tracks_by_tag', 'tag': 'pop', 'limit': 500},
    {'tool': 'top_tracks_by_tag', 'tag': 'pop', 'url': 'http://arbitrary'},
])
def test_invalid_tools_rejected(payload):
    with pytest.raises(ValidationError):
        ToolCall.model_validate(payload)


def test_unknown_seed_never_invokes_tool():
    class NoCalls:
        async def similar_tracks(self, *args):
            pytest.fail('Unknown seed executed')
    _, record = asyncio.run(ToolExecutor(NoCalls(), []).execute(ToolCall(tool='similar_tracks', seed_id='invented')))
    assert record['status'] == 'error' and record['error_type'] == 'ValueError'


def test_repeated_calls_stop_without_consuming_budget_forever():
    class Repeat(ScriptedPlanner):
        async def decide(self, *args):
            return RetrievalDecision(action='search', reason='repeat',
                calls=[ToolCall(tool='top_tracks_by_tag', tag='pop', limit=50)])
    tools = Tools()
    _, state, rows, _, _ = run_agent(tools, Repeat())
    assert tools.calls == ['pop'] and not rows
    assert state.stop_reason == 'no_new_calls'


def test_tool_budget(monkeypatch):
    monkeypatch.setattr(get_settings(), 'agent_max_tool_calls', 1)
    _, state, rows, _, _ = run_agent(Tools(), ScriptedPlanner())
    assert state.stop_reason == 'tool_budget' and not rows


def test_clarification_performs_no_retrieval():
    class Clarify(ScriptedPlanner):
        async def plan(self, *args):
            return RetrievalPlan(intent=MusicIntent(), need_clarification=True,
                clarification_questions=['需要哪一种语言？'])
    tools = Tools()
    _, state, rows, warnings, _ = run_agent(tools, Clarify())
    assert not tools.calls and not rows and warnings
    assert state.stop_reason == 'clarification_required'


def test_deadline_returns_collected_candidates(monkeypatch):
    monkeypatch.setattr(get_settings(), 'agent_retrieval_timeout_seconds', 0.03)
    class Slow(ScriptedPlanner):
        async def decide(self, *args):
            await asyncio.sleep(5)
    _, state, _, warnings, _ = run_agent(Tools(), Slow())
    assert state.stop_reason == 'retrieval_deadline' and state.candidates and warnings


def test_llm_plan_http_contract_controls_override_and_usage(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'llm_mock_mode', False)
    monkeypatch.setattr(settings, 'llm_api_key', 'secret-value')
    monkeypatch.setattr(settings, 'llm_base_url', 'https://llm.example/v1')
    monkeypatch.setattr(settings, 'llm_model', 'configured-model')
    original = httpx.AsyncClient
    def respond(request):
        body = json.loads(request.content)
        assert body['model'] == 'configured-model'
        assert 'output_schema' in json.loads(body['messages'][1]['content'])
        plan = RetrievalPlan(intent=MusicIntent(language='english'),
            hard_constraints=HardConstraints(language='english'), calls=[ToolCall(tool='chart_tracks')])
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': plan.model_dump_json()}}], 'usage': {'total_tokens': 99}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    trace = Trace('private input', RequestPreferences())
    token = current_trace.set(trace)
    try:
        planner = Planner()
        plan = asyncio.run(planner.plan('private input', RequestPreferences(language='chinese'), MusicPreference(), {}, []))
        assert plan.intent.language == plan.hard_constraints.language == 'chinese'
        assert planner.mode == 'llm'
        assert next(e for e in trace.events if e['event'] == 'llm_call')['usage']['total_tokens'] == 99
        assert 'secret-value' not in json.dumps(trace.events)
    finally:
        current_trace.reset(token)


def test_invalid_llm_output_falls_back(monkeypatch):
    settings = get_settings()
    for key, value in [('llm_mock_mode', False), ('llm_api_key', 'key'),
                       ('llm_base_url', 'https://llm.example'), ('llm_model', 'test')]:
        monkeypatch.setattr(settings, key, value)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(**kwargs,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'choices': [{'message': {'content': '{bad json'}}]}))))
    planner = Planner()
    plan = asyncio.run(planner.plan('华语', RequestPreferences(language='chinese'), MusicPreference(), {}, []))
    assert planner.mode == 'fallback' and not planner.available
    assert plan.hard_constraints.language == 'chinese' and plan.calls


def test_log_redacts_credentials_and_raw_request(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, 'agent_log_enabled', True)
    monkeypatch.setattr(settings, 'agent_log_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'llm_api_key', 'my-secret')
    trace = Trace('private sentence', RequestPreferences())
    trace.emit('plan', summary='private sentence', error='my-secret', api_key='my-secret')
    content = (tmp_path / 'agent.jsonl').read_text(encoding='utf-8')
    assert 'my-secret' not in content and 'private sentence' not in content
    assert trace.request_id in content


def test_deadline_retains_fast_result_in_same_round(monkeypatch):
    monkeypatch.setattr(get_settings(), 'agent_retrieval_timeout_seconds', 0.03)
    class MixedTools(Tools):
        async def top_tracks_by_tag(self, tag, limit):
            if tag == 'slow':
                await asyncio.sleep(5)
            return await super().top_tracks_by_tag(tag, limit)
    class MixedPlan(ScriptedPlanner):
        async def plan(self, *args):
            plan = await super().plan(*args)
            plan.calls = [ToolCall(tool='top_tracks_by_tag', tag=t) for t in ['mandopop', 'slow']]
            return plan
    _, state, rows, _, _ = run_agent(MixedTools(), MixedPlan())
    assert len(rows) == 20 and state.stop_reason == 'retrieval_deadline'
    assert state.evaluation['completed_tool_calls'] == 1
    assert state.evaluation['unfinished_tool_calls'] == 1


def test_cancellation_audits_and_propagates():
    async def scenario():
        entered = asyncio.Event()
        class SlowTools(Tools):
            async def top_tracks_by_tag(self, tag, limit):
                entered.set()
                await asyncio.sleep(5)
        trace = Trace('private sentence', RequestPreferences())
        token = current_trace.set(trace)
        try:
            task = asyncio.create_task(RetrievalAgent(SlowTools(), ScriptedPlanner()).run(
                'test', 'private sentence', RequestPreferences(), MusicPreference(),
                LearnedProfile(), [], {}, {}, 20, []))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert any(e['event'] == 'tool_call' and e['status'] == 'cancelled' for e in trace.events)
            assert any(e['event'] == 'evaluation' and e['stop_reason'] == 'request_cancelled' for e in trace.events)
        finally:
            current_trace.reset(token)
    asyncio.run(scenario())


def test_conflicting_constraints_require_clarification():
    class Conflict(Planner):
        async def ask(self, *args):
            return RetrievalPlan(intent=MusicIntent(),
                hard_constraints=HardConstraints(required_tags=['rock'], exclude_tags=['ROCK']))
    plan = asyncio.run(Conflict().plan('request', RequestPreferences(), MusicPreference(), {}, []))
    assert plan.need_clarification and plan.clarification_questions


def test_exclusions_and_required_tags_survive_iteration():
    class HardPlan(ScriptedPlanner):
        async def plan(self, *args):
            plan = await super().plan(*args)
            plan.hard_constraints.required_tags = ['pop']
            plan.hard_constraints.exclude_artists = ['Artist 0']
            plan.hard_constraints.exclude_tags = ['live']
            return plan
    class TaggedTools(Tools):
        async def top_tracks_by_tag(self, tag, limit):
            items = await super().top_tracks_by_tag(tag, limit)
            items[1]['tags'].append('live')
            items[2]['tags'].remove('pop')
            return items
    _, state, rows, _, _ = run_agent(TaggedTools(), HardPlan())
    assert len(rows) == 20
    assert all(row.item['artist'] != 'Artist 0' and 'pop' in row.item['tags'] and 'live' not in row.item['tags'] for row in rows)
    assert state.evaluation['rejected_counts']['required_tag_missing'] == 2


def test_log_summary_distinguishes_completion_target_and_fallback(tmp_path):
    from scripts.evaluate_agent import summarize
    events = [
        {'event': 'evaluation', 'request_id': 'a', 'stop_reason': 'target_met', 'fill_rate': 1},
        {'event': 'evaluation', 'request_id': 'b', 'stop_reason': 'round_budget', 'fill_rate': 0.5},
        {'event': 'evaluation', 'request_id': 'c', 'stop_reason': 'clarification_required'},
        {'event': 'completed', 'request_id': 'a', 'elapsed_ms': 100},
        {'event': 'completed', 'request_id': 'b', 'elapsed_ms': 200},
        {'event': 'completed', 'request_id': 'c', 'elapsed_ms': 300},
        {'event': 'failed', 'request_id': 'd', 'elapsed_ms': 400},
        {'event': 'llm_call', 'status': 'ok', 'usage': {'total_tokens': 30}, 'request_id': 'a'},
        {'event': 'llm_call', 'status': 'fallback', 'request_id': 'b'},
    ]
    path = tmp_path / 'test.jsonl'
    path.write_text('\n'.join(json.dumps(e) for e in events) + '\n[]\ninvalid', encoding='utf-8')
    result = summarize([path])
    assert result['requests'] == 4 and result['completion_rate'] == .75
    assert result['target_met_rate'] == .5 and result['mean_fill_rate'] == .75
    assert result['llm_fallback_rate'] == .5 and result['reported_tokens'] == 30
    assert result['calls_missing_token_usage'] == 1 and result['p95_latency_ms'] == 400
    assert result['malformed_lines'] == 2


def test_short_request_does_not_corrupt_log_identity():
    trace = Trace('e', RequestPreferences())
    trace.emit('completed', summary='e')
    assert trace.events[-1]['event'] == 'completed'
    assert trace.events[-1]['request_id'] == trace.request_id
    assert trace.events[-1]['summary'] == '[REQUEST REDACTED]'
