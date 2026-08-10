import pytest

from app.services import office_resolution_service as office_resolution

# The 13 real Benivo UAT officeName values (fetched live 2026-07-29), used
# as realistic refdata in these tests.
UAT_OFFICES = [
    {"id": "32efc23b-9a5b-4c9d-a304-747c7844a584", "officeName": "Bulgaria (Live Casino)"},
    {"id": "e908ab8d-0046-43f4-9ead-6a8dfb4b0876", "officeName": "Latvia (Global)"},
    {"id": "68da6b8b-1e07-4742-9333-a882e284c3fb", "officeName": "Serbia (Live Casino)"},
    {"id": "53ebc1b4-3be2-4b95-a193-d3067fe932f0", "officeName": "Serbia (Global)"},
    {"id": "b1863484-fea7-4c71-8261-e38a30f35f34", "officeName": "Gibraltar (Head office)"},
    {"id": "e59774f5-5852-4d83-b655-c7d4410d65d8", "officeName": "Malta (Global)"},
    {"id": "b732127f-8a3a-4d99-bf0e-95c24fa69f55", "officeName": "Romania (Live Casino)"},
    {"id": "73a201c9-409b-45da-874b-1e532281796d", "officeName": "Romania (Global)"},
    {"id": "1f607452-4bf2-4783-a3b5-40a923aced07", "officeName": "UAE (Global)"},
    {"id": "ca081aff-d1f3-407a-afdd-adb836563d31", "officeName": "UAE (Live Casino)"},
    {"id": "1edf820f-72a1-443d-8461-8b6a0d7b1292", "officeName": "Canada (Live Casino)"},
    {"id": "d715fdb6-c93c-4940-a076-918214ca152d", "officeName": "Colombia (Live Casino)"},
    {"id": "ae9b1fdb-fb21-4532-b271-e96b894915b8", "officeName": "Brazil (Live Casino)"},
]


@pytest.mark.parametrize(
    "jobvite_workplace, expected_office_name, expected_office_id, expected_host_country",
    [
        ("RAK Live Casino", "UAE (Live Casino)", "ca081aff-d1f3-407a-afdd-adb836563d31", "UAE"),
        ("Serbia Live Casino", "Serbia (Live Casino)", "68da6b8b-1e07-4742-9333-a882e284c3fb", "Serbia"),
        ("Colombia Live Casino", "Colombia (Live Casino)", "d715fdb6-c93c-4940-a076-918214ca152d", "Colombia"),
        ("Bulgaria Live Casino", "Bulgaria (Live Casino)", "32efc23b-9a5b-4c9d-a304-747c7844a584", "Bulgaria"),
        ("Malta", "Malta (Global)", "e59774f5-5852-4d83-b655-c7d4410d65d8", "Malta"),
        ("Romania Live Casino", "Romania (Live Casino)", "b732127f-8a3a-4d99-bf0e-95c24fa69f55", "Romania"),
        ("Latvia", "Latvia (Global)", "e908ab8d-0046-43f4-9ead-6a8dfb4b0876", "Latvia"),
    ],
)
def test_resolve_office_translates_and_retrieves_real_uuid(
    jobvite_workplace, expected_office_name, expected_office_id, expected_host_country
):
    candidate = {"workplace": jobvite_workplace}
    refdata = {"offices": UAT_OFFICES}

    office = office_resolution.resolve_office(candidate, refdata)

    assert office == {
        "officeId": expected_office_id,
        "officeName": expected_office_name,
        "hostCountry": expected_host_country,
    }


@pytest.mark.parametrize(
    "jobvite_workplace, expected_office_name",
    [
        ("RAK Live Casino", "UAE (Live Casino)"),
        ("rak live casino", "UAE (Live Casino)"),
        ("  RAK   Live   Casino  ", "UAE (Live Casino)"),
        ("Serbia Live Casino", "Serbia (Live Casino)"),
        ("Colombia Live Casino", "Colombia (Live Casino)"),
    ],
)
def test_resolve_office_name_normalizes_whitespace_and_casing_for_lookup(jobvite_workplace, expected_office_name):
    assert office_resolution.resolve_office_name(jobvite_workplace) == expected_office_name


def test_resolve_office_name_never_fuzzy_or_partial_matches():
    # "RAK" alone, or a near-miss with extra text, must NOT resolve --
    # only an exact (normalized) match against the explicit mapping key.
    assert office_resolution.resolve_office_name("RAK") is None
    assert office_resolution.resolve_office_name("RAK Live Casino Extra") is None
    assert office_resolution.resolve_office_name("Live Casino") is None


def test_resolve_office_returns_none_when_workplace_has_no_mapping_entry():
    candidate = {"workplace": "Nonexistent Office"}
    assert office_resolution.resolve_office(candidate, {"offices": UAT_OFFICES}) is None


def test_resolve_office_returns_none_when_translated_name_missing_from_current_refdata():
    # Mapped correctly, but this environment's refdata doesn't have the office
    # (e.g. UAT vs a differently-configured environment).
    candidate = {"workplace": "Serbia Live Casino"}
    refdata_without_serbia = {"offices": [o for o in UAT_OFFICES if "Serbia" not in o["officeName"]]}

    assert office_resolution.resolve_office(candidate, refdata_without_serbia) is None


def test_resolve_office_returns_none_when_refdata_is_none():
    candidate = {"workplace": "Serbia Live Casino"}
    assert office_resolution.resolve_office(candidate, None) is None


# ---------------------------------------------------------------------------
# resolve_host_country() -- explicit officeName -> hostCountry mapping,
# confirmed 2026-08-06 by official email from Benivo (full office catalogue).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "office_name, expected_host_country",
    [
        ("Serbia (Live Casino)", "Serbia"),
        ("Serbia (Global)", "Serbia"),
        ("UAE (Live Casino)", "UAE"),
        ("UAE (Global)", "UAE"),
        ("Bulgaria (Live Casino)", "Bulgaria"),
        ("Malta (Global)", "Malta"),
        ("Romania (Live Casino)", "Romania"),
        ("Romania (Global)", "Romania"),
        ("Colombia (Live Casino)", "Colombia"),
        ("Canada (Live Casino)", "Canada"),
        ("Brazil (Live Casino)", "Brazil"),
        ("Latvia (Global)", "Latvia"),
        ("Gibraltar (Head office)", "Gibraltar"),
    ],
)
def test_resolve_host_country_covers_full_confirmed_office_catalogue(office_name, expected_host_country):
    assert office_resolution.resolve_host_country(office_name) == expected_host_country


def test_resolve_host_country_covers_every_uat_office_in_test_fixture():
    # Every officeName in this test file's own UAT_OFFICES fixture (the 13
    # real offices fetched live) must have a hostCountry -- guards against
    # the catalogue and the mapping silently drifting apart.
    for office in UAT_OFFICES:
        assert office_resolution.resolve_host_country(office["officeName"]) is not None, office["officeName"]


def test_resolve_host_country_returns_none_for_unmapped_office_name():
    assert office_resolution.resolve_host_country("Nonexistent Office (Global)") is None


def test_resolve_host_country_returns_none_for_none():
    assert office_resolution.resolve_host_country(None) is None


def test_resolve_office_includes_host_country_for_every_uat_office():
    # Full round-trip through resolve_office() (not just resolve_host_country
    # directly) for every workplace this codebase currently routes.
    for jobvite_workplace, expected_host_country in [
        ("RAK Live Casino", "UAE"),
        ("Serbia Live Casino", "Serbia"),
        ("Colombia Live Casino", "Colombia"),
        ("Bulgaria Live Casino", "Bulgaria"),
        ("Malta", "Malta"),
        ("Romania Live Casino", "Romania"),
        ("Latvia", "Latvia"),
    ]:
        office = office_resolution.resolve_office({"workplace": jobvite_workplace}, {"offices": UAT_OFFICES})
        assert office["hostCountry"] == expected_host_country


def test_resolve_office_never_infers_host_country_from_candidate_address_fields():
    # hostCountry must come only from the resolved Benivo office, never from
    # any address/location-shaped field on the candidate -- even when such
    # fields are present (and wrong), they must be completely ignored.
    candidate = {
        "workplace": "Serbia Live Casino",
        "home_country": "United States",
        "host_country": "Wrong Country",
        "host_city": "Wrong City",
        "country": "USA",
    }

    office = office_resolution.resolve_office(candidate, {"offices": UAT_OFFICES})

    assert office["hostCountry"] == "Serbia"


def test_resolve_office_uses_whatever_uuid_the_current_refdata_provides():
    # Same officeName, deliberately different UUIDs across two "environments"
    # -- proves the ID always comes from the refdata passed in, never
    # hardcoded, never generated from the name.
    candidate = {"workplace": "Colombia Live Casino"}

    uat_refdata = {"offices": [{"id": "uat-uuid-1234", "officeName": "Colombia (Live Casino)"}]}
    prod_refdata = {"offices": [{"id": "prod-uuid-5678", "officeName": "Colombia (Live Casino)"}]}

    uat_office = office_resolution.resolve_office(candidate, uat_refdata)
    prod_office = office_resolution.resolve_office(candidate, prod_refdata)

    assert uat_office["officeId"] == "uat-uuid-1234"
    assert prod_office["officeId"] == "prod-uuid-5678"
    assert uat_office["officeId"] != prod_office["officeId"]
