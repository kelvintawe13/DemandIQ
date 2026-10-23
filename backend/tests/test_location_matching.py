"""A site name written by hand is not spelled the way the database spells it.

``Remer Grills`` arrives as ``remera grills``, ``Remer Grill's``, ``REMER
GRILLS`` or ``Remera Grill``. Rejecting those rows over a typo is why a
photographed page imported nothing; matching them loosely enough to accept a
typo, but strictly enough never to pick the wrong site, is the balance pinned
here.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.routers.ingest import _resolve_location_id
from app.services.ingestion.location_match import match_location_id, normalise_location_name

REMER = uuid.uuid4()
BISTRO = uuid.uuid4()


def candidates(*rows: tuple[uuid.UUID, str]) -> list[tuple[uuid.UUID, str]]:
    return list(rows)


class TestNormalisation:
    def test_folds_case_accents_and_punctuation(self) -> None:
        assert normalise_location_name("  Remer Grill's ") == "remer grills"
        assert normalise_location_name("Café  Kigali") == "cafe kigali"
        assert normalise_location_name("A&B") == "a and b"
        assert normalise_location_name("REMER—GRILLS") == "remer grills"

    def test_an_empty_value_names_nothing(self) -> None:
        assert normalise_location_name(None) == ""
        assert normalise_location_name("   ") == ""


class TestMatching:
    def test_matches_a_spelling_the_database_also_holds(self) -> None:
        pool = candidates((REMER, "Remer Grills"))
        assert match_location_id("remer  grills", pool) == REMER
        assert match_location_id("Remer-Grills", pool) == REMER
        assert match_location_id("Remer Grill's", pool) == REMER

    def test_accepts_a_typo_in_one_word(self) -> None:
        # The name of the workspace's own location, as a logbook spells it.
        pool = candidates((REMER, "Remer Grills"))
        assert match_location_id("Remera Grills", pool) == REMER
        assert match_location_id("Remera Grill", pool) == REMER

    def test_accepts_a_shorter_multi_word_name(self) -> None:
        pool = candidates((REMER, "Remer Grills"), (BISTRO, "Kigali Heights Bistro"))
        assert match_location_id("Kigali Heights", pool) == BISTRO

    def test_one_shared_word_is_not_evidence(self) -> None:
        # "Grills" appears in both sites; choosing either would file a day
        # against a coin toss.
        pool = candidates((REMER, "Remer Grills"), (BISTRO, "Kigali Grills"))
        assert match_location_id("Grills", pool) is None

    def test_refuses_a_different_site(self) -> None:
        pool = candidates((REMER, "Remer Grills"))
        assert match_location_id("Nyamirambo Cafe", pool) is None

    def test_refuses_to_choose_between_two_plausible_sites(self) -> None:
        pool = candidates((REMER, "Remer Grils A"), (BISTRO, "Remer Grils B"))
        assert match_location_id("Remer Grils", pool) is None

    def test_refuses_two_locations_with_the_same_name(self) -> None:
        pool = candidates((REMER, "Remer Grills"), (BISTRO, "remer  grills"))
        assert match_location_id("Remer Grills", pool) is None

    def test_no_candidates_is_no_match(self) -> None:
        assert match_location_id("Remer Grills", []) is None


class TestRouterResolution:
    def test_exact_name_wins_before_any_fuzzy_work(self) -> None:
        location = SimpleNamespace(id=REMER, name="Remer Grills")
        db = MagicMock()
        db.scalar.return_value = location

        assert _resolve_location_id(db, uuid.uuid4(), "Remer Grills") == REMER
        db.scalars.assert_not_called()

    def test_a_typo_still_resolves_to_the_location(self) -> None:
        location = SimpleNamespace(id=REMER, name="Remer Grills")
        db = MagicMock()
        db.scalar.return_value = None  # no exact name in the workspace
        db.scalars.return_value = [location]

        assert _resolve_location_id(db, uuid.uuid4(), "Remera Grills") == REMER

    def test_an_unknown_site_is_still_an_error(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None
        db.scalars.return_value = [SimpleNamespace(id=REMER, name="Remer Grills")]

        assert _resolve_location_id(db, uuid.uuid4(), "Nyamirambo Cafe") is None
