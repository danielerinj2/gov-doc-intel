from __future__ import annotations

from app.domain.states import ALLOWED_TRANSITIONS, DocumentState


class InvalidTransitionError(ValueError):
    pass


class StateMachine:
    def transition(self, current: DocumentState, target: DocumentState) -> DocumentState:
        # Any operational state can fail due to pipeline exceptions.
        if target == DocumentState.FAILED and current != DocumentState.ARCHIVED:
            return target

        allowed = ALLOWED_TRANSITIONS.get(current, set())
        if target not in allowed:
            raise InvalidTransitionError(f"Invalid transition {current.value} -> {target.value}")
        return target
