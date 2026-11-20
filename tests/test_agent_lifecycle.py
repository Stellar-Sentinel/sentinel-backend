from types import SimpleNamespace

from app.agents import monitor
from app.config import Settings


def result(address: str) -> dict:
    return {
        "address": address,
        "score": 85,
        "threshold_exceeded": True,
        "risk_level": "high",
        "threshold": 70,
        "scoring_policy_version": "1.0.0",
        "as_of": "2026-01-01T00:00:00+00:00",
    }


def test_submission_failure_isolated_per_candidate(monkeypatch):
    first = "G" + "A" * 55
    second = "G" + "B" * 55
    agent = object.__new__(monitor.MonitoringAgent)
    agent.settings = Settings(agent_flag_cooldown_seconds=3600)
    agent.keypair = SimpleNamespace(public_key="G" + "S" * 55)
    agent._last_flagged_at = {}

    monkeypatch.setattr(
        monitor,
        "recent_network_accounts",
        lambda *_args, **_kwargs: [first, second],
    )
    monkeypatch.setattr(monitor, "score_account", lambda address, _settings: result(address))

    filed = []

    def file_flag(address, _result):
        if address == first:
            raise RuntimeError("simulated submission failure")
        filed.append(address)

    monkeypatch.setattr(agent, "_file_flag", file_flag)

    assert agent.run_once() == 1
    assert filed == [second]
    assert agent._on_cooldown(first)
