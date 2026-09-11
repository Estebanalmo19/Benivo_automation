from app.services.phone_service import normalize_phone_number


def test_normalize_phone_number_valid_plus_digits_included():
    assert normalize_phone_number("+447700900123") == "+447700900123"


def test_normalize_phone_number_spaces_removed_safely():
    assert normalize_phone_number("+44 7700 900123") == "+447700900123"


def test_normalize_phone_number_hyphens_removed_safely():
    assert normalize_phone_number("+40-721-123-456") == "+40721123456"


def test_normalize_phone_number_parentheses_removed_safely():
    assert normalize_phone_number("+(353) 87 123 4567") == "+353871234567"


def test_normalize_phone_number_local_national_without_plus_is_omitted():
    # No reliable source for a country code -- never guessed.
    assert normalize_phone_number("0721123456") is None
    assert normalize_phone_number("771234567") is None
    assert normalize_phone_number("123456") is None


def test_normalize_phone_number_blank_or_none_is_omitted():
    assert normalize_phone_number(None) is None
    assert normalize_phone_number("") is None
    assert normalize_phone_number("   ") is None


def test_normalize_phone_number_malformed_international_is_omitted():
    # Contains a character this module never strips (a letter, or a "+"
    # not at the very start) -- falls through to omission rather than a
    # partial/guessed cleanup.
    assert normalize_phone_number("+44 abc 900123") is None
    assert normalize_phone_number("44+7700900123") is None
    assert normalize_phone_number("++447700900123") is None


def test_normalize_phone_number_never_invents_country_code():
    # Explicitly forbidden sources for a country code -- normalize_phone_number()
    # takes only the raw phone string, so it structurally cannot consult any
    # of these even if they were somehow passed in.
    import inspect

    signature = inspect.signature(normalize_phone_number)
    assert list(signature.parameters) == ["raw_phone"]
