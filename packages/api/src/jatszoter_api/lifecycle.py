from enum import StrEnum

from statemachine import StateMachine
from statemachine.exceptions import TransitionNotAllowed
from statemachine.states import States

from jatszoter_api.errors import InvalidTransitionError


class DeploymentStatus(StrEnum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    BOOTSTRAPPING = "BOOTSTRAPPING"
    READY = "READY"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Actor(StrEnum):
    AGENT = "agent"
    OPERATOR = "operator"
    API = "api"


S = DeploymentStatus


class Lifecycle(StateMachine):
    s = States.from_enum(S, initial=S.PENDING, final=[S.COMPLETED, S.FAILED, S.CANCELLED])
    live = (s.PENDING, s.STARTING, s.BOOTSTRAPPING, s.READY, s.STOPPING)

    assign = s.PENDING.to(s.STARTING, cond="by_agent")
    cancel = s.PENDING.to(s.CANCELLED, cond="by_operator")
    boot = s.STARTING.to(s.BOOTSTRAPPING, cond="by_agent")
    bootstrap = s.BOOTSTRAPPING.to(s.READY, cond="by_agent")
    stop = s.STOPPING.from_(s.STARTING, s.BOOTSTRAPPING, s.READY, cond="by_operator")
    expire = s.READY.to(s.STOPPING, cond="by_api")
    destroy = s.STOPPING.to(s.COMPLETED, cond="by_agent")
    fail = s.FAILED.from_(*live, cond="by_agent")
    abort = s.FAILED.from_(*live, cond="by_api")

    def by_agent(self, actor: Actor) -> bool:
        return actor is Actor.AGENT

    def by_operator(self, actor: Actor) -> bool:
        return actor is Actor.OPERATOR

    def by_api(self, actor: Actor) -> bool:
        return actor is Actor.API


TERMINAL = frozenset(DeploymentStatus(state.value) for state in Lifecycle.final_states)


def advance(current: DeploymentStatus, event: str, actor: Actor) -> DeploymentStatus:
    """The status `event` by `actor` leads to from `current`, or InvalidTransitionError."""
    machine = Lifecycle(start_value=current)
    try:
        machine.send(event, actor=actor)
    except TransitionNotAllowed as error:
        raise InvalidTransitionError(f"{actor} cannot {event} a {current} deployment") from error
    return DeploymentStatus(machine.current_state_value)
