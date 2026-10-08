"""Victim status machine (spec section 5).

The table below is the only place transitions are defined. Team transitions are
additionally guarded in SQL (conditional UPDATE) so concurrent phones cannot both
win; this module answers "is this move legal for this actor at all".
"""

from __future__ import annotations

PENDING = "pending"
OPEN = "open"
ONGOING = "ongoing"
COMPLETED = "completed"
DISMISSED = "dismissed"

STATUSES = (PENDING, OPEN, ONGOING, COMPLETED, DISMISSED)

# Teams only ever see these.
TEAM_VISIBLE = (OPEN, ONGOING, COMPLETED)
# Victims that new detections may still merge into.
DEDUP_ACTIVE = (PENDING, OPEN, ONGOING)

TEAM = "team"
COORDINATOR = "coordinator"
SYSTEM = "system"

# (from, to) -> {actor: action name}
TRANSITIONS: dict[tuple[str | None, str], dict[str, str]] = {
    (None, OPEN): {SYSTEM: "create", COORDINATOR: "add_manual"},
    (None, PENDING): {SYSTEM: "create"},
    (PENDING, OPEN): {COORDINATOR: "verify"},
    (PENDING, DISMISSED): {COORDINATOR: "dismiss"},
    (OPEN, DISMISSED): {COORDINATOR: "dismiss"},
    # Teams claim; the coordinator may also dispatch an open victim directly
    # (design rule 3: the coordinator can override any assignment).
    (OPEN, ONGOING): {TEAM: "accept", COORDINATOR: "assign"},
    (ONGOING, OPEN): {TEAM: "release", COORDINATOR: "release"},
    (ONGOING, ONGOING): {COORDINATOR: "reassign"},
    (ONGOING, COMPLETED): {TEAM: "complete", COORDINATOR: "mark_completed"},
    (COMPLETED, ONGOING): {COORDINATOR: "reopen"},
    (DISMISSED, OPEN): {COORDINATOR: "restore"},
}


class InvalidTransition(Exception):
    def __init__(self, from_status, to_status, actor):
        super().__init__(f"{actor} cannot move a victim from {from_status} to {to_status}")
        self.from_status = from_status
        self.to_status = to_status
        self.actor = actor


def allowed(from_status: str | None, to_status: str, actor: str, actions: set[str] | None = None) -> bool:
    """Is the move legal for this actor? With ``actions``, it must also be one of those named
    actions — (open -> ongoing) is "assign" for the coordinator, never "reopen"."""
    action = TRANSITIONS.get((from_status, to_status), {}).get(actor)
    return action is not None and (actions is None or action in actions)


def check(from_status: str | None, to_status: str, actor: str) -> None:
    if not allowed(from_status, to_status, actor):
        raise InvalidTransition(from_status, to_status, actor)


def coordinator_actions(status: str) -> list[tuple[str, str]]:
    """(action, to_status) pairs the coordinator may apply from this status — drives the GUI buttons."""
    return [
        (actors[COORDINATOR], to)
        for (frm, to), actors in TRANSITIONS.items()
        if frm == status and COORDINATOR in actors
    ]
