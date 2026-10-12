import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, 'mock_mode', True)
    monkeypatch.setattr(settings, 'llm_mock_mode', True)
    monkeypatch.setattr(settings, 'llm_api_key', '')
    monkeypatch.setattr(settings, 'agent_log_enabled', False)
