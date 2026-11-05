from app.agents import monitor
from app.config import Settings

AGENT_SECRET = "SBUBHLJEAFN5Q4O4WJELZPDDXN4MNHX3X7QOAFO3LRY7DDJMWEURLKX7"
AGENT_ADDRESS = "GAGY5MLPNZK6BHBADGGXX7FKQR62D2NILSB4B5OQXXE2TFD5NQF2UHGL"
CANDIDATE_A = "G" + "A" * 55
CANDIDATE_B = "G" + "B" * 55
CONTRACT_ID = "C" + "A" * 55


def _result(address: str, *, threshold_exceeded: bool, score: int = 85) -> dict:
    return {
        "address": address,
        "score": score,
        "risk_level": "high" if threshold_exceeded else "low",
        "threshold": 70,
        "threshold_exceeded": threshold_exceeded,
        "scoring_policy_version": "1.0.0",
        "as_of": "2026-01-01T00:00:00+00:00",
    }


def test_report_digest_is_32_bytes_and_field_scoped():
    base = _result(CANDIDATE_A, threshold_exceeded=True)
    digest = monitor.report_digest(base)
    assert len(digest) == 32
    assert digest == monitor.report_digest(dict(base))

    changed_score = dict(base, score=86)
    assert monitor.report_digest(changed_score) != digest

    with_extra_field = dict(base, operations_scanned=5)
    assert monitor.report_digest(with_extra_field) == digest


def test_recent_network_accounts_dedupes_preserves_order_and_skips_malformed(monkeypatch):
    records = [
        {"source_account": CANDIDATE_A},
        {"source_account": CANDIDATE_B},
        {"source_account": CANDIDATE_A},
        "not-a-dict",
        {"no_source": True},
    ]
    monkeypatch.setattr(monitor, "_get", lambda *args, **kwargs: {"_embedded": {"records": records}})

    accounts = monitor.recent_network_accounts(Settings(), limit=10)

    assert accounts == [CANDIDATE_A, CANDIDATE_B]


def test_monitoring_agent_requires_secret_and_contract_id():
    try:
        monitor.MonitoringAgent(Settings(agent_secret_key="", contract_id=CONTRACT_ID))
        assert False, "expected ValueError for missing secret key"
    except ValueError:
        pass

    try:
        monitor.MonitoringAgent(Settings(agent_secret_key=AGENT_SECRET, contract_id=""))
        assert False, "expected ValueError for missing contract id"
    except ValueError:
        pass


def test_run_once_skips_self_cooldown_and_non_threshold_candidates(monkeypatch):
    settings = Settings(agent_secret_key=AGENT_SECRET, contract_id=CONTRACT_ID, agent_flag_cooldown_seconds=3600)
    agent = monitor.MonitoringAgent(settings)

    monkeypatch.setattr(
        monitor,
        "recent_network_accounts",
        lambda *args, **kwargs: [AGENT_ADDRESS, CANDIDATE_A, CANDIDATE_B],
    )

    def fake_score_account(address, _settings):
        return _result(address, threshold_exceeded=(address == CANDIDATE_A))

    monkeypatch.setattr(monitor, "score_account", fake_score_account)

    filed_for = []
    monkeypatch.setattr(agent, "_file_flag", lambda address, result: filed_for.append(address))

    filed = agent.run_once()

    assert filed == 1
    assert filed_for == [CANDIDATE_A]

    # Second cycle immediately after: CANDIDATE_A is on cooldown and must not be re-flagged.
    filed_again = agent.run_once()
    assert filed_again == 0
    assert filed_for == [CANDIDATE_A]


def test_run_once_continues_past_a_scoring_failure(monkeypatch):
    settings = Settings(agent_secret_key=AGENT_SECRET, contract_id=CONTRACT_ID)
    agent = monitor.MonitoringAgent(settings)

    monkeypatch.setattr(monitor, "recent_network_accounts", lambda *args, **kwargs: [CANDIDATE_A, CANDIDATE_B])

    def fake_score_account(address, _settings):
        if address == CANDIDATE_A:
            raise RuntimeError("boom")
        return _result(address, threshold_exceeded=True)

    monkeypatch.setattr(monitor, "score_account", fake_score_account)
    filed_for = []
    monkeypatch.setattr(agent, "_file_flag", lambda address, result: filed_for.append(address))

    filed = agent.run_once()

    assert filed == 1
    assert filed_for == [CANDIDATE_B]
