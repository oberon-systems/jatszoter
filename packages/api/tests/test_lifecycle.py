import itertools

import pytest

from jatszoter_api.errors import InvalidTransitionError
from jatszoter_api.lifecycle import TERMINAL, Actor, DeploymentStatus, Lifecycle, advance

S = DeploymentStatus
A = Actor
LIVE = [S.PENDING, S.STARTING, S.BOOTSTRAPPING, S.READY, S.STOPPING]
EVENTS = [event.id for event in Lifecycle().events]
# The "Who moves what" table of docs/project/02-domain-model.md.
MOVES = {
    (S.PENDING, S.STARTING, A.AGENT),
    (S.PENDING, S.CANCELLED, A.OPERATOR),
    (S.STARTING, S.BOOTSTRAPPING, A.AGENT),
    (S.BOOTSTRAPPING, S.READY, A.AGENT),
    (S.READY, S.STOPPING, A.OPERATOR),
    (S.READY, S.STOPPING, A.API),
    (S.STOPPING, S.COMPLETED, A.AGENT),
    *((status, S.FAILED, A.AGENT) for status in LIVE),
    *((status, S.FAILED, A.API) for status in LIVE),
}


def reachable() -> set[tuple[S, S, A]]:
    moves = set()
    for current, event, actor in itertools.product(DeploymentStatus, EVENTS, Actor):
        try:
            moves.add((current, advance(current, event, actor), actor))
        except InvalidTransitionError:
            continue
    return moves


def test_machine_matches_lifecycle() -> None:
    assert reachable() == MOVES


@pytest.mark.parametrize("actor", list(Actor))
@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("current", sorted(TERMINAL))
def test_terminal_states_have_no_exits(current: S, event: str, actor: A) -> None:
    with pytest.raises(InvalidTransitionError):
        advance(current, event, actor)


def test_an_unknown_event_is_refused() -> None:
    with pytest.raises(InvalidTransitionError):
        advance(S.PENDING, "teleport", A.OPERATOR)


def test_terminal_set() -> None:
    assert frozenset({S.COMPLETED, S.FAILED, S.CANCELLED}) == TERMINAL
