"""Billing provisioning: the first visit must not 500 when requests overlap.

The Billing screen loads three endpoints at once — ``/subscription``,
``/invoices`` and ``/usage`` — and each of them provisions the workspace's
subscription when it is missing. A workspace that has never opened the screen
therefore has several requests doing "select, find nothing, insert" inside the
same few milliseconds, and the unique index on ``billing_subscriptions.
workspace_id`` lets exactly one of them win. The losers used to surface the
duplicate-key error as a 500, which took the whole screen down to the error
boundary on the very first visit.

These are hermetic: the session is a stub, so no database is needed to prove the
losing request recovers instead of raising.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.models.billing import BillingSubscription
from app.routers.billing import _get_or_create_subscription

WORKSPACE = uuid.UUID("c6c30409-c198-4f43-af52-7cebd5b31538")


class _RacedSession:
    """A session which lost the insert race to another request.

    The first read finds nothing (that is why it tried to insert), and the
    insert trips the unique index. Afterwards the read must return the row the
    winning request committed.
    """

    def __init__(self) -> None:
        self.reads = 0
        self.rolled_back = False
        self.winner = BillingSubscription(
            id=uuid.uuid4(),
            workspace_id=WORKSPACE,
            plan_id="growth",
            status="active",
        )

    def scalar(self, statement):  # noqa: ANN001 - mirrors Session.scalar
        self.reads += 1
        return None if self.reads == 1 else self.winner

    def add(self, obj) -> None:  # noqa: ANN001 - mirrors Session.add
        self.added = obj

    def flush(self) -> None:
        raise IntegrityError("INSERT INTO billing_subscriptions", {}, Exception("duplicate key"))

    def rollback(self) -> None:
        self.rolled_back = True

    def get(self, *args, **kwargs):  # noqa: ANN002, ANN003 - mirrors Session.get
        return None


def test_a_losing_request_returns_the_provisioned_subscription_instead_of_500ing() -> None:
    db = _RacedSession()

    sub = _get_or_create_subscription(db, WORKSPACE)  # type: ignore[arg-type]

    assert sub is db.winner
    assert db.rolled_back, "the failed insert left the transaction dirty"
    assert db.reads == 2, "the row committed by the winner was never re-read"
