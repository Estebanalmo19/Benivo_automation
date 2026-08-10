from app.services import home_country_service


def test_resolve_effective_home_country_uses_primary_when_present():
    candidate = {"home_country": "Serbia", "current_country": "Kazakhstan"}

    value, source = home_country_service.resolve_effective_home_country(candidate)

    assert value == "Serbia"
    assert source == home_country_service.SOURCE_CANDIDATE_HOME_COUNTRY


def test_resolve_effective_home_country_falls_back_to_current_country():
    candidate = {"home_country": None, "current_country": "Belarus"}

    value, source = home_country_service.resolve_effective_home_country(candidate)

    assert value == "Belarus"
    assert source == home_country_service.SOURCE_CURRENT_LOCATION


def test_resolve_effective_home_country_falls_back_when_primary_is_blank_string():
    candidate = {"home_country": "", "current_country": "United Arab Emirates"}

    value, source = home_country_service.resolve_effective_home_country(candidate)

    assert value == "United Arab Emirates"
    assert source == home_country_service.SOURCE_CURRENT_LOCATION


def test_resolve_effective_home_country_missing_when_both_absent():
    candidate = {"home_country": None, "current_country": None}

    value, source = home_country_service.resolve_effective_home_country(candidate)

    assert value is None
    assert source == home_country_service.SOURCE_MISSING


def test_resolve_effective_home_country_missing_when_keys_absent_entirely():
    value, source = home_country_service.resolve_effective_home_country({})

    assert value is None
    assert source == home_country_service.SOURCE_MISSING


def test_resolve_effective_home_country_never_prefers_fallback_over_primary():
    # Even when current_country looks more "complete"/different, primary
    # always wins when present -- this is a controlled fallback, not a
    # best-of-both-fields merge.
    candidate = {"home_country": "Georgia", "current_country": "Serbia"}

    value, source = home_country_service.resolve_effective_home_country(candidate)

    assert value == "Georgia"
    assert source == home_country_service.SOURCE_CANDIDATE_HOME_COUNTRY
