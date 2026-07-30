"""Centralized policy resolution: is_vip -> policy_name -> policy_api_value. is_vip is the only input."""

from typing import Optional, Tuple

from app.models.domain import POLICY_BASIC, POLICY_VIP


def resolve_policy(is_vip: Optional[bool]) -> str:
    """
    The one centralized policy-resolution rule:
      is_vip IS TRUE       -> 'VIP'
      is_vip IS FALSE/NULL -> 'Basic'

    Deliberately does not consider job_title, department, salary, office, or
    the old unconfirmed candidates.vip text field -- is_vip is the only
    input. VIP has no confirmed Jobvite source yet (see
    synchronization_service), so is_vip is business/integration-owned and
    defaults to FALSE.
    """
    return POLICY_VIP if is_vip is True else POLICY_BASIC


# policy_name (business label) and policy_api_value (exact string Benivo's
# API accepts) are deliberately separate concepts. The first real UAT
# attempt (application_eid=pCu0IxwQ, 2026-07-29) sent policy="Basic" and
# Benivo rejected it: "Policy is misspelled". Confirmed via two independent
# sources that "Tier 1" is the real API value for the general/non-VIP case:
#   1. Live refdata['policies'] (read-only, no create-user call) returns
#      exactly: "Tier 1", "Tier 2", "Tier 3", "Game Presenters and
#      Shufflers" -- "Basic"/"VIP" appear nowhere in it.
#   2. legacy/create_user_benivo.py's original PREFERRED_POLICY was already
#      "Tier 1", and a historical request using it was accepted by policy
#      validation (it failed later, on LastName format -- not on policy).
# Confirmed live in UAT on 2026-07-30: candidate pCu0IxwQ retried with
# policy="Tier 1" returned SUCCESS (benivo_user_id=605070).
#
# No confirmed Benivo API value exists for VIP anywhere (not in refdata, not
# in any historical payload) -- deliberately left unmapped so VIP candidates
# are blocked from posting (via payload validation) rather than guessed.
POLICY_NAME_TO_API_VALUE = {
    POLICY_BASIC: "Tier 1",
}


def resolve_policy_api_value(policy_name: str) -> Optional[str]:
    """Exact Benivo API value for a business policy_name, or None if unconfirmed (blocks posting)."""
    return POLICY_NAME_TO_API_VALUE.get(policy_name)


def resolve_policy_values(is_vip: Optional[bool]) -> Tuple[str, Optional[str]]:
    """(policy_name, policy_api_value) -- policy_name is never overwritten by the API-specific label."""
    policy_name = resolve_policy(is_vip)
    return policy_name, resolve_policy_api_value(policy_name)
