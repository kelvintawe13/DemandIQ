"""Match a name written on a logbook sheet to a workspace location.

A photo of a notebook, a spreadsheet column and a till export all name the same
site by hand, and none of them spells it the way the database does: ``Remer
Grills`` arrives as ``remera grills``, ``Remer Grill's``, ``REMER  GRILLS`` or
``Remera Grill``. An exact (or case-insensitive) lookup rejected every one of
those, so the row failed with "Location ... not found" and the operator had to
correct the file before importing a single day.

The matching is deliberately conservative, because a wrong match files a day
against the wrong site — worse than an error the operator can see:

* case, accents, punctuation and repeated spaces are normalised away;
* a typo is tolerated up to a similarity threshold;
* a shorter name that is wholly contained in a longer one needs **two** shared
  words ("Kigali Heights" in "Kigali Heights Bistro"), never one;
* if the best and second-best candidates are close, nothing is returned. The
  caller shows the ambiguity instead of guessing.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
import uuid
from collections.abc import Iterable

# Similarity needed to accept a candidate at all. Below this the two names are
# different places, not different spellings of one place.
FUZZY_MIN_SCORE = 0.82

# How much the best candidate must beat the runner-up. Two sites whose names are
# equally plausible ("Remer Grills" / "Remer Grill House") are a question for the
# operator, not a coin toss for the importer.
FUZZY_MIN_MARGIN = 0.05

# Containment is strong evidence, but only for names made of several words.
_MIN_TOKENS_FOR_CONTAINMENT = 2


def normalise_location_name(value: object) -> str:
    """Fold a written site name to the words that identify it.

    ``"Remer Grill's "`` -> ``"remer grills"``, ``"Café  Kigali"`` ->
    ``"cafe kigali"``, ``"A&B"`` -> ``"a and b"``.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['\u2019`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score(target: str, candidate: str) -> float:
    """Similarity of two normalised names, with a cautious containment bonus."""
    ratio = difflib.SequenceMatcher(None, target, candidate).ratio()
    target_tokens, candidate_tokens = set(target.split()), set(candidate.split())
    shared = target_tokens & candidate_tokens
    if len(shared) < _MIN_TOKENS_FOR_CONTAINMENT:
        return ratio
    # One name is a strict subset of the other ("Kigali Heights" vs "Kigali
    # Heights Bistro"): the same site with an extra qualifier.
    if shared == target_tokens or shared == candidate_tokens:
        return max(ratio, 0.95)
    # Two shared words with a typo elsewhere ("Remera Grills Kigali").
    return max(ratio, 0.85)


def match_location_id(
    raw: object,
    candidates: Iterable[tuple[uuid.UUID, str]],
) -> uuid.UUID | None:
    """The location ``raw`` most likely names, or ``None`` when it is unclear.

    Args:
        raw: The value as written in the file/photo (a name, rarely an id).
        candidates: ``(id, name)`` for every active location in the workspace.

    Returns:
        The matched id, or ``None`` when nothing is close enough or two
        candidates are too close to call.
    """
    target = normalise_location_name(raw)
    if not target:
        return None

    scored: list[tuple[float, uuid.UUID]] = []
    exact: list[uuid.UUID] = []
    for location_id, name in candidates:
        normalised = normalise_location_name(name)
        if not normalised:
            continue
        if normalised == target:
            exact.append(location_id)
            continue
        scored.append((_score(target, normalised), location_id))

    # A spelling the database also holds, punctuation aside, is not a guess.
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None  # two sites with the same name: cannot choose

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_id = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < FUZZY_MIN_SCORE:
        return None
    if best_score - second_score < FUZZY_MIN_MARGIN:
        return None
    return best_id
