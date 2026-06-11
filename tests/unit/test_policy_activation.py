from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from agent_app.governance.policy_activation import PolicyActivation, PolicyActivationStatus


def test_creates_valid_activation():
    a = PolicyActivation(
        activation_id="pa_test123", environment="prod", bundle_id="pb_001",
        config_hash="abc123", promotion_id="pr_001", activated_by="admin",
    )
    assert a.activation_id == "pa_test123"
    assert a.environment == "prod"
    assert a.status == PolicyActivationStatus.ACTIVE
    assert a.superseded_at is None


def test_requires_environment():
    with pytest.raises(ValidationError, match="environment"):
        PolicyActivation(activation_id="pa_1", bundle_id="pb_1", config_hash="h1", activated_by="admin")


def test_requires_bundle_id():
    with pytest.raises(ValidationError, match="bundle_id"):
        PolicyActivation(activation_id="pa_1", environment="prod", config_hash="h1", activated_by="admin")


def test_defaults_to_active():
    a = PolicyActivation(activation_id="pa_1", environment="dev", bundle_id="pb_1", config_hash="h1", activated_by="admin")
    assert a.status == PolicyActivationStatus.ACTIVE


def test_all_statuses_valid():
    for status in PolicyActivationStatus:
        a = PolicyActivation(activation_id="pa_1", environment="dev", bundle_id="pb_1", config_hash="h1", activated_by="admin", status=status)
        assert a.status == status


def test_created_at_has_tzinfo():
    a = PolicyActivation(activation_id="pa_1", environment="dev", bundle_id="pb_1", config_hash="h1", activated_by="admin")
    assert a.created_at.tzinfo is not None


def test_superseded_fields_optional():
    a = PolicyActivation(
        activation_id="pa_1", environment="dev", bundle_id="pb_1", config_hash="h1",
        activated_by="admin", superseded_at=datetime.now(timezone.utc),
        superseded_by_activation_id="pa_old",
    )
    assert a.superseded_at is not None
    assert a.superseded_by_activation_id == "pa_old"
