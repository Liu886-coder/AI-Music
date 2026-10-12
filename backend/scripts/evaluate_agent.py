"""Offline reproducible checks, or aggregation of operational JSONL logs."""
import argparse
import asyncio
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent.planner import Planner
from app.agent.retrieval import RetrievalAgent
from app.core.config import get_settings
from app.schemas.agent import RetrievalPlan, RetrievalDecision, HardConstraints, ToolCall
from app.schemas.music import MusicIntent, RequestPreferences, MusicPreference, LearnedProfile


class FixtureTools:
    async def top_tracks_by_tag(self, tag, limit):
        return [{'id': tag + str(i), 'name': tag + str(i), 'artist': 'Artist ' + str(i),
                 'tags': [tag]} for i in range(min(25, limit))]

    async def enrich_tracks(self, items, max_items):
        return items


class FixturePlanner(Planner):
    def __init__(self, language, initial, retry):
        super().__init__()
        self.language, self.initial, self.retry = language, initial, retry

    async def plan(self, *args):
        self.mode = 'llm'
        return RetrievalPlan(intent=MusicIntent(language=self.language),
            hard_constraints=HardConstraints(language=self.language),
            calls=[ToolCall(tool='top_tracks_by_tag', tag=self.initial)])

    async def decide(self, *args):
        return RetrievalDecision(action='search', reason='fixture retry',
            calls=[ToolCall(tool='top_tracks_by_tag', tag=self.retry)])


async def offline():
    get_settings().agent_log_enabled = False
    cases = []
    for language, initial, retry, expected in [
        ('chinese', 'english', 'mandopop', 20),
        ('japanese', 'english', 'j-pop', 20),
        ('instrumental', 'piano', 'instrumental', 20),
        ('chinese', 'english', 'english', 0),
    ]:
        _, state, rows, _, _ = await RetrievalAgent(FixtureTools(), FixturePlanner(language, initial, retry)).run(
            'evaluation', 'fixture', RequestPreferences(language=language), MusicPreference(),
            LearnedProfile(), [], {}, {}, 20, [])
        passed = len(rows) == expected and state.evaluation['hard_constraint_violations'] == 0
        cases.append({'case': f'{language}:{initial}->{retry}', 'passed': passed, **state.evaluation})
    return {'mode': 'offline_scripted_planner', 'note': 'Verifies orchestration, not real LLM or provider quality',
            'passed': sum(c['passed'] for c in cases), 'total': len(cases), 'cases': cases}


def summarize(paths):
    events, malformed = [], 0
    for path in paths:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or not isinstance(event.get('event'), str):
                    raise ValueError('Invalid log record')
                events.append(event)
            except ValueError:
                malformed += 1
    evaluations = [e for e in events if e.get('event') == 'evaluation']
    retrievals = [e for e in evaluations if e.get('stop_reason') != 'clarification_required']
    outcomes = [e for e in events if e.get('event') in ('completed', 'failed', 'cancelled')]
    durations = sorted(e['elapsed_ms'] for e in events if e.get('event') in ('completed', 'failed', 'cancelled'))
    llm = [e for e in events if e.get('event') == 'llm_call']
    return {'mode': 'log_summary', 'requests': len({e.get('request_id') for e in events}),
        'outcomes': dict(Counter(e['event'] for e in events if e.get('event') in ('completed', 'failed', 'cancelled'))),
        'stop_reasons': dict(Counter(e.get('stop_reason') for e in evaluations)),
        'completion_rate': sum(e['event'] == 'completed' for e in outcomes) / len(outcomes) if outcomes else None,
        'target_met_rate': sum(e.get('stop_reason') == 'target_met' for e in retrievals) / len(retrievals) if retrievals else None,
        'mean_fill_rate': sum(e.get('fill_rate', 0) for e in retrievals) / len(retrievals) if retrievals else None,
        'hard_constraint_violations': sum(e.get('hard_constraint_violations', 0) for e in evaluations),
        'p95_latency_ms': durations[max(0, math.ceil(len(durations)*.95)-1)] if durations else None,
        'llm_calls': len(llm), 'llm_fallback_calls': sum(e.get('status') == 'fallback' for e in llm),
        'llm_fallback_rate': sum(e.get('status') == 'fallback' for e in llm) / len(llm) if llm else None,
        'llm_skipped_calls': sum(e.get('event') == 'llm_skipped' for e in events),
        'reported_tokens': sum((e.get('usage') or {}).get('total_tokens', 0) for e in llm),
        'calls_missing_token_usage': sum('total_tokens' not in (e.get('usage') or {}) for e in llm),
        'malformed_lines': malformed}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--logs', nargs='+')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = summarize(args.logs) if args.logs else asyncio.run(offline())
    content = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding='utf-8')
    print(content)
    if result.get('mode') == 'offline_scripted_planner' and result['passed'] != result['total']:
        sys.exit(1)
