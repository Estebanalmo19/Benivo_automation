from app.services import policy_service


def test_resolve_policy_true_is_vip():
    assert policy_service.resolve_policy(True) == "VIP"


def test_resolve_policy_false_is_basic():
    assert policy_service.resolve_policy(False) == "Basic"


def test_resolve_policy_null_is_basic():
    assert policy_service.resolve_policy(None) == "Basic"


def test_resolve_policy_api_value_basic_confirmed():
    assert policy_service.resolve_policy_api_value("Basic") == "Tier 1"


def test_resolve_policy_api_value_vip_unconfirmed_returns_none():
    assert policy_service.resolve_policy_api_value("VIP") is None


def test_resolve_policy_api_value_unknown_policy_name_blocks():
    assert policy_service.resolve_policy_api_value("SomeUnknownPolicy") is None


def test_resolve_policy_values_basic():
    policy_name, policy_api_value = policy_service.resolve_policy_values(False)
    assert policy_name == "Basic"
    assert policy_api_value == "Tier 1"


def test_resolve_policy_values_vip_blocks():
    policy_name, policy_api_value = policy_service.resolve_policy_values(True)
    assert policy_name == "VIP"
    assert policy_api_value is None
