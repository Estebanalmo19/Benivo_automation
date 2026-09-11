from app.services.gender_service import resolve_gender


def test_resolve_gender_male():
    assert resolve_gender("Male") == "Male"


def test_resolve_gender_female():
    assert resolve_gender("Female") == "Female"


def test_resolve_gender_undefined_is_omitted():
    # "Undefined" is Jobvite's own not-answered value -- never mapped to
    # Benivo's real "Other" value.
    assert resolve_gender("Undefined") is None


def test_resolve_gender_none_is_omitted():
    assert resolve_gender(None) is None


def test_resolve_gender_blank_is_omitted():
    assert resolve_gender("") is None
    assert resolve_gender("   ") is None


def test_resolve_gender_unknown_value_is_omitted_safely():
    # A genuinely new/unexpected Jobvite value must never be guessed into
    # "Other" or any other Benivo value -- fails safe to None.
    assert resolve_gender("Non-binary") is None
    assert resolve_gender("Prefer not to say") is None


def test_resolve_gender_never_returns_other():
    # "Other" is a real, distinct documented Benivo value -- must never be
    # produced as a substitute for missing/undefined/unrecognized Jobvite data.
    for raw in (None, "", "Undefined", "Non-binary", "Other"):
        assert resolve_gender(raw) != "Other"


def test_resolve_gender_case_insensitive_match():
    assert resolve_gender("male") == "Male"
    assert resolve_gender("FEMALE") == "Female"
