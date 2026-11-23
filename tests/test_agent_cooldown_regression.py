from types import SimpleNamespace

from app.agents import monitor
from app.config import Settings


AGENT_ADDRESS = "G" + "S" * 55
CANDIDATE_A = "G" + "A" * 55
CANDIDATE_B = "G" + "B" * 55
AGENT_SECRET = "SBUBHLJEAFN5Q4O4WJELZPDDXN4MNHX3X7QOAFO3LRY7DDJMWEURLKX7"
CONTRACT_ID = "C" + "A" * 55


def _result(address: str) -> dict:
    return {
        "address": address,
        "score": 85,
        "threshold": 70,
        "threshold_exceeded": True,
        "risk_level": "high",
        "scoring_policy_version": "1.0.0",
        "as_of": "2026-01-01T00:00:00+00:00",
    }


def _agent(cooldown_seconds: int = 3600):
    agent = object.__new__(monitor.MonitoringAgent)
    agent.settings = Settings(
        agent_secret_key=AGENT_SECRET,
        contract_id=CONTRACT_ID,
        agent_flag_cooldown_seconds=cooldown_seconds,
    )
    agent.keypair = SimpleNamespace(public_key=AGENT_ADDRESS)
    agent._last_flagged_at = {}
    return agent


def test_cooldown_is_scoped_to_each_candidate(monkeypatch):
    agent = _agent()
    monkeypatch.setattr(monitor, "recent_network_accounts", lambda *_args, **_kwargs: [CANDIDATE_A, CANDIDATE_B])
    monkeypatch.setattr(monitor, "score_account", lambda address, _settings: _result(address))
    filed_for = []
    monkeypatch.setattr(agent, "_file_flag", lambda address, _result: filed_for.append(address))

    assert agent.run_once() == 2
    assert filed_for == [CANDIDATE_A, CANDIDATE_B]

    agent._last_flagged_at[CANDIDATE_A] = monitor.time.monotonic()
    filed_for.clear()
    assert agent.run_once() == 0
    assert filed_for == []
    assert agent._on_cooldown(CANDIDATE_A)
    assert not agent._on_cooldown(CANDIDATE_B)


def test_cooldown_resets_after_process_restart(monkeypatch):
    agent = _agent()
    monkeypatch.setattr(monitor, "recent_network_accounts", lambda *_args, **_kwargs: [CANDIDATE_A])
    monkeypatch.setattr(monitor, "score_account", lambda address, _settings: _result(address))
    filed_for = []
    monkeypatch.setattr(agent, "_file_flag", lambda address, _result: filed_for.append(address))

    assert agent.run_once() == 1
    restarted = _agent()
    monkeypatch.setattr(restarted, "_file_flag", lambda address, _result: filed_for.append(address))

    assert restarted.run_once() == 1
    assert filed_for == [CANDIDATE_A, CANDIDATE_A]


def test_cooldown_boundary_allows_flag_after_window(monkeypatch):
    agent = _agent(cooldown_seconds=60)
    monkeypatch.setattr(monitor, "recent_network_accounts", lambda *_args, **_kwargs: [CANDIDATE_A])
    monkeypatch.setattr(monitor, "score_account", lambda address, _settings: _result(address))
    filed_for = []
    monkeypatch.setattr(agent, "_file_flag", lambda address, _result: filed_for.append(address))

    agent._last_flagged_at[CANDIDATE_A] = monitor.time.monotonic() - 60
    assert agent.run_once() == 1
    assert filed_for == [CANDIDATE_A]


def test_duplicate_candidates_are_deduplicated_by_network_reader(monkeypatch):
    records = [
        {"source_account": CANDIDATE_A},
        {"source_account": CANDIDATE_A},
        {"source_account": CANDIDATE_B},
        {"source_account": CANDIDATE_B},
    ]
    monkeypatch.setattr(monitor, "_get", lambda *args, **kwargs: {"_embedded": {"records": records}})

    assert monitor.recent_network_accounts(Settings(), limit=10) == [CANDIDATE_A, CANDIDATE_B]
