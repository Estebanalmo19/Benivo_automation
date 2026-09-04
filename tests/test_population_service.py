from app.services import population_service


# ---------------------------------------------------------------------------
# is_dealer_shuffler_catalog_value() -- confirmed 2026-09-04 (second round):
# dealer_shuffler is a CONTROLLED SELECTOR -- ANY real catalog value
# qualifies, not just a named allowlist. Real catalog inspected against
# ALL 372,747 Jobvite applications (job level, no LIMIT): "Presenter",
# "Dealer", "Shuffler", "Gameshow host", "Prive Specialist Dealer", plus
# the field's own "n/a" placeholder (the only value excluded).
# ---------------------------------------------------------------------------

def test_catalog_value_presenter():
    assert population_service.is_dealer_shuffler_catalog_value("Presenter") is True


def test_catalog_value_shuffler():
    assert population_service.is_dealer_shuffler_catalog_value("Shuffler") is True


def test_catalog_value_gameshow_host():
    assert population_service.is_dealer_shuffler_catalog_value("Gameshow host") is True


def test_catalog_value_prive_specialist_dealer():
    assert population_service.is_dealer_shuffler_catalog_value("Prive Specialist Dealer") is True


def test_catalog_value_premium_game_host():
    # Named in the business rule as a possible catalog value -- not itself
    # observed as a real dealer_shuffler value as of 2026-09-04 (it turned
    # out to be a job TITLE whose own dealer_shuffler value is "Presenter"),
    # but the rule is inclusive-by-default, so it must still match if it
    # is ever actually used as a dealer_shuffler selection.
    assert population_service.is_dealer_shuffler_catalog_value("Premium Game Host") is True


def test_catalog_value_dealer_now_included():
    # Confirmed 2026-09-04 (second round): "Dealer" (172 of 527 current
    # Benivo candidates) IS a real catalog value and now counts -- the
    # narrower "Presenter/Shuffler only" allowlist from the first round is
    # retired; the field itself is authoritative, not a named subset of it.
    assert population_service.is_dealer_shuffler_catalog_value("Dealer") is True


def test_catalog_value_case_insensitive_and_trimmed():
    assert population_service.is_dealer_shuffler_catalog_value("  PRESENTER  ") is True
    assert population_service.is_dealer_shuffler_catalog_value("dealer") is True


def test_catalog_value_na_is_excluded():
    # The field's own "not applicable" placeholder (105 real rows) --
    # confirmed to mean "this job posting is not a dealer/presenter/
    # shuffler role at all" (the field is asked on every job posting, not
    # just Presenter/Shuffler/Dealer ones), functionally equivalent to no
    # selection, not a 7th real role.
    assert population_service.is_dealer_shuffler_catalog_value("n/a") is False
    assert population_service.is_dealer_shuffler_catalog_value("N/A") is False


def test_catalog_value_missing_is_excluded():
    # "missing dealer_shuffler" case: no job.customField[fieldCode=
    # 'dealer__shuffler'] entry for this application_eid --
    # DEALER_SHUFFLER_SUBQUERY returns NULL, candidate.get("dealer_shuffler")
    # is None.
    assert population_service.is_dealer_shuffler_catalog_value(None) is False
    assert population_service.is_dealer_shuffler_catalog_value("") is False
    assert population_service.is_dealer_shuffler_catalog_value("   ") is False


def test_catalog_value_unexpected_value_still_counts():
    # dealer_shuffler is a controlled selector (closed dropdown) -- a value
    # this codebase has never seen before is still trusted as a real
    # catalog selection (not guessed away), per "the controlled Jobvite
    # field itself is authoritative" and "do not special-case only
    # Presenter/Shuffler". Only the field's own "n/a"/blank sentinel is
    # excluded.
    assert population_service.is_dealer_shuffler_catalog_value("Some Future Catalog Option") is True


# ---------------------------------------------------------------------------
# resolve_population() -- the confirmed business scenarios
# ---------------------------------------------------------------------------

def test_resolve_population_presenter():
    assert population_service.resolve_population("Presenter", is_vip=False) == "Game Presenters and Shufflers"


def test_resolve_population_shuffler():
    assert population_service.resolve_population("Shuffler", is_vip=False) == "Game Presenters and Shufflers"


def test_resolve_population_gameshow_host():
    assert population_service.resolve_population("Gameshow host", is_vip=False) == "Game Presenters and Shufflers"


def test_resolve_population_prive_specialist_dealer():
    assert population_service.resolve_population("Prive Specialist Dealer", is_vip=False) == "Game Presenters and Shufflers"


def test_resolve_population_premium_game_host():
    assert population_service.resolve_population("Premium Game Host", is_vip=False) == "Game Presenters and Shufflers"


def test_resolve_population_dealer():
    assert population_service.resolve_population("Dealer", is_vip=False) == "Game Presenters and Shufflers"


def test_resolve_population_catalog_value_overrides_vip():
    # A real catalog value wins regardless of is_vip -- checked first.
    assert population_service.resolve_population("Presenter", is_vip=True) == "Game Presenters and Shufflers"


def test_resolve_population_missing_dealer_shuffler_vip_yes_is_tier_1():
    assert population_service.resolve_population(None, is_vip=True) == "Tier 1"


def test_resolve_population_missing_dealer_shuffler_vip_no_is_tier_3():
    assert population_service.resolve_population(None, is_vip=False) == "Tier 3"


def test_resolve_population_na_dealer_shuffler_falls_back_to_is_vip_rule():
    assert population_service.resolve_population("n/a", is_vip=True) == "Tier 1"
    assert population_service.resolve_population("n/a", is_vip=False) == "Tier 3"


def test_resolve_population_null_is_vip_treated_as_not_vip():
    assert population_service.resolve_population(None, is_vip=None) == "Tier 3"


def test_resolve_population_unexpected_value_still_counts_as_catalog():
    assert population_service.resolve_population("Some Future Catalog Option", is_vip=False) == "Game Presenters and Shufflers"


# ---------------------------------------------------------------------------
# resolve_population_api_value() / resolve_population_values()
# ---------------------------------------------------------------------------

def test_resolve_population_api_value_confirmed_values_pass_through():
    assert population_service.resolve_population_api_value("Tier 1") == "Tier 1"
    assert population_service.resolve_population_api_value("Tier 3") == "Tier 3"
    assert population_service.resolve_population_api_value("Game Presenters and Shufflers") == "Game Presenters and Shufflers"


def test_resolve_population_api_value_unknown_value_blocks():
    assert population_service.resolve_population_api_value("Tier 2") is None
    assert population_service.resolve_population_api_value("SomeUnknownPopulation") is None


def test_resolve_population_values_catalog_value():
    population_name, population_api_value = population_service.resolve_population_values("Shuffler", is_vip=False)
    assert population_name == "Game Presenters and Shufflers"
    assert population_api_value == "Game Presenters and Shufflers"


def test_resolve_population_values_tier_1():
    population_name, population_api_value = population_service.resolve_population_values(None, is_vip=True)
    assert population_name == "Tier 1"
    assert population_api_value == "Tier 1"


def test_resolve_population_values_tier_3():
    population_name, population_api_value = population_service.resolve_population_values(None, is_vip=False)
    assert population_name == "Tier 3"
    assert population_api_value == "Tier 3"
