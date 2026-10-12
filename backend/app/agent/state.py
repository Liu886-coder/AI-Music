from dataclasses import dataclass, field
from typing import Any
@dataclass
class AgentState:
    user_id: str
    message: str
    used_tools: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    executed_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ''
    evaluation: dict = field(default_factory=dict)
