from app.config import Settings


def test_safe_summary_excludes_secret_values():
    settings = Settings(api_key="x" * 32, agent_secret_key="secret-value")

    summary = settings.safe_summary()
    assert summary["api_key_configured"] is True
    assert summary["agent_key_configured"] is True
    assert "secret-value" not in str(summary)
