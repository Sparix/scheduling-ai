from datetime import date as Date
from typing import Protocol

from app.schemas import (
    Assignment,
    AutoSchedule,
    Constraint,
    ConstraintDecision,
    Decision,
    ScheduleCheck,
)


class RuleEngine(Protocol):
    def check_assignment(
        self, assignment: Assignment, assignments: list[Assignment]
    ) -> Decision: ...

    def check_schedule(
        self, week: Date, assignments: list[Assignment]
    ) -> ScheduleCheck: ...

    def check_constraint(
        self, constraint: Constraint, constraints: list[Constraint]
    ) -> ConstraintDecision: ...

    def auto_schedule(
        self, week: Date, shift_ids: list[str], assignments: list[Assignment]
    ) -> AutoSchedule: ...
