from app.services import country_code_service


def test_resolve_iso2_known_country():
    assert country_code_service.resolve_iso2("Serbia") == "RS"


def test_resolve_iso2_is_case_and_whitespace_insensitive():
    assert country_code_service.resolve_iso2("  serbia  ") == "RS"
    assert country_code_service.resolve_iso2("SERBIA") == "RS"


def test_resolve_iso2_covers_non_standard_jobvite_labels():
    # These exact labels were observed in the candidate population and do
    # not match the plain ISO 3166 English short name.
    assert country_code_service.resolve_iso2("Korea (South)") == "KR"
    assert country_code_service.resolve_iso2("Myanmar (Burma)") == "MM"
    assert country_code_service.resolve_iso2("Russia") == "RU"
    assert country_code_service.resolve_iso2("Russian Federation") == "RU"
    assert country_code_service.resolve_iso2("Viet Nam") == "VN"
    assert country_code_service.resolve_iso2("Vietnam") == "VN"
    assert country_code_service.resolve_iso2("North Macedonia (formerly Macedonia)") == "MK"


def test_resolve_iso2_fails_safely_on_unknown_country():
    assert country_code_service.resolve_iso2("Neverland") is None


def test_resolve_iso2_none_and_empty_string():
    assert country_code_service.resolve_iso2(None) is None
    assert country_code_service.resolve_iso2("") is None
