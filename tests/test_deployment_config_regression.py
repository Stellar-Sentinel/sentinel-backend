import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "deployment_config_cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_deployment_configuration_cases_remain_explicit(case):
    if case["valid"]:
        settings = Settings(**case["values"])
        assert settings.environment == case["values"]["environment"]
        return

    with pytest.raises(ValidationError, match=case["error"]):
        Settings(**case["values"])


def test_fixture_contains_no_signing_material_or_private_keys():
    fixture = (Path(__file__).parent / "fixtures" / "deployment_config_cases.json").read_text()

    assert "BEGIN " not in fixture
    assert "secret" not in fixture.lower()
    assert "PRIVATE KEY" not in fixture
