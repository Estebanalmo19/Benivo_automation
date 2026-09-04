from app.services import mobility_scope_service as scope


# ---------------------------------------------------------------------------
# parse_mobility_support_options() -- real-data-confirmed newline separator
# ---------------------------------------------------------------------------

def test_parse_mobility_support_options_single_option():
    assert scope.parse_mobility_support_options("Relocation") == ["Relocation"]


def test_parse_mobility_support_options_all_three():
    raw = "Relocation\nVisa/work permit\nAccommodation"
    assert scope.parse_mobility_support_options(raw) == ["Relocation", "Visa/work permit", "Accommodation"]


def test_parse_mobility_support_options_two_of_three():
    assert scope.parse_mobility_support_options("Visa/work permit\nAccommodation") == ["Visa/work permit", "Accommodation"]


def test_parse_mobility_support_options_na_only():
    assert scope.parse_mobility_support_options("N/A") == ["N/A"]


def test_parse_mobility_support_options_none_is_empty_list():
    assert scope.parse_mobility_support_options(None) == []


def test_parse_mobility_support_options_blank_string_is_empty_list():
    assert scope.parse_mobility_support_options("") == []
    assert scope.parse_mobility_support_options("   ") == []


def test_parse_mobility_support_options_unexpected_value_still_parses():
    # Not a recognized option, but parsing itself must not fail/guess.
    assert scope.parse_mobility_support_options("Something Else") == ["Something Else"]


def test_parse_mobility_support_options_mixed_whitespace_and_newlines():
    # Confirmed real-data variant: trailing/leading blank lines, \r\n, extra spaces.
    raw = "\r\n  Relocation  \r\n\nVisa/work permit\n  \n"
    assert scope.parse_mobility_support_options(raw) == ["Relocation", "Visa/work permit"]


def test_parse_mobility_support_options_mixed_real_option_and_na():
    # Confirmed real combination (8 rows in real data): a real option AND N/A together.
    assert scope.parse_mobility_support_options("Relocation\nVisa/work permit\nAccommodation\nN/A") == [
        "Relocation", "Visa/work permit", "Accommodation", "N/A",
    ]


# ---------------------------------------------------------------------------
# mobility_support_qualifies() -- the confirmed eligibility rule
# ---------------------------------------------------------------------------

def test_mobility_support_qualifies_relocation_only():
    assert scope.mobility_support_qualifies("Relocation") is True


def test_mobility_support_qualifies_visa_work_permit_only():
    assert scope.mobility_support_qualifies("Visa/work permit") is True


def test_mobility_support_qualifies_accommodation_only():
    assert scope.mobility_support_qualifies("Accommodation") is True


def test_mobility_support_qualifies_all_three():
    assert scope.mobility_support_qualifies("Relocation\nVisa/work permit\nAccommodation") is True


def test_mobility_support_qualifies_combination_of_two():
    assert scope.mobility_support_qualifies("Relocation\nAccommodation") is True
    assert scope.mobility_support_qualifies("Visa/work permit\nAccommodation") is True


def test_mobility_support_qualifies_na_only_does_not_qualify():
    assert scope.mobility_support_qualifies("N/A") is False


def test_mobility_support_qualifies_none_does_not_qualify():
    assert scope.mobility_support_qualifies(None) is False


def test_mobility_support_qualifies_blank_does_not_qualify():
    assert scope.mobility_support_qualifies("") is False
    assert scope.mobility_support_qualifies("   ") is False


def test_mobility_support_qualifies_unexpected_value_does_not_qualify():
    assert scope.mobility_support_qualifies("Something Else") is False


def test_mobility_support_qualifies_real_option_mixed_with_na_still_qualifies():
    # Confirmed real combination: N/A co-occurring with a real option must
    # still qualify -- a real option is present.
    assert scope.mobility_support_qualifies("Relocation\nN/A") is True


def test_mobility_support_qualifies_case_insensitive():
    assert scope.mobility_support_qualifies("relocation") is True
    assert scope.mobility_support_qualifies("ACCOMMODATION") is True


def test_mobility_support_qualifies_mixed_whitespace_and_newlines():
    assert scope.mobility_support_qualifies("\r\n  Relocation  \r\n") is True


# ---------------------------------------------------------------------------
# resolve_scope() -- corrected 2026-09-05: mobility_support is the ONLY
# scope rule. Domestic/local relocation (e.g. within Serbia, Romania,
# Bulgaria) does NOT exclude a candidate -- a domestic candidate with a
# qualifying mobility_support selection is in scope exactly like an
# international one, UAE included. The earlier is_domestic_relocation()/
# UAE-only-exception logic has been removed entirely, not disabled.
# ---------------------------------------------------------------------------

def test_resolve_scope_qualifying_mobility_support_is_in_scope():
    in_scope, reason = scope.resolve_scope("Relocation")
    assert in_scope is True
    assert reason is None


def test_resolve_scope_na_only_is_out_of_scope():
    in_scope, reason = scope.resolve_scope("N/A")
    assert in_scope is False
    assert reason == scope.SCOPE_REASON_MOBILITY_SUPPORT


def test_resolve_scope_missing_is_out_of_scope():
    in_scope, reason = scope.resolve_scope(None)
    assert in_scope is False
    assert reason == scope.SCOPE_REASON_MOBILITY_SUPPORT


def test_resolve_scope_domestic_relocation_no_longer_referenced():
    # Domestic-relocation attributes were removed entirely, not just unused.
    assert not hasattr(scope, "is_domestic_relocation")
    assert not hasattr(scope, "UAE_ISO2")
    assert not hasattr(scope, "SCOPE_REASON_DOMESTIC_NON_UAE")


def test_resolve_scope_signature_takes_only_mobility_support():
    import inspect

    params = list(inspect.signature(scope.resolve_scope).parameters)
    assert params == ["mobility_support"]


def test_scope_reason_text_covers_every_reason_code():
    assert scope.SCOPE_REASON_TEXT[scope.SCOPE_REASON_MOBILITY_SUPPORT]
    assert len(scope.SCOPE_REASON_TEXT) == 1
