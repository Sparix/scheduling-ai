from datetime import date as Date
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator

from app.schemas import (
    ConstraintType,
    EmployeeRole,
    Group,
    Priority,
    RuleType,
    Schema,
    Skill,
    Status,
    TargetName,
    week_start_of,
)

Week = Annotated[Date, AfterValidator(week_start_of)]
ShiftId = Annotated[str, AfterValidator(str.upper)]


class Agent(StrEnum):
    EXPLANATION = "explanation"
    CONSTRAINTS = "constraints"


class Empty(Schema):
    pass


class EmployeeQuery(Schema):
    employee_id: str | None = None
    name: str | None = None
    skill: Skill | None = None
    min_level: int = 1
    role: EmployeeRole | None = None
    onboarding: bool | None = None
    min_age: int | None = None
    max_age: int | None = None


class ShiftQuery(Schema):
    shift_id: ShiftId | None = None
    group: Group | None = None
    date: Date | None = None
    lead_only: bool = False
    night_only: bool = False


class RuleQuery(Schema):
    rule_id: str | None = None
    type: RuleType | None = None


class AbsenceQuery(Schema):
    employee_id: str | None = None
    from_date: Date | None = None
    to_date: Date | None = None


class ConstraintQuery(Schema):
    employee_id: str | None = None
    week: Week | None = None
    group: Group | None = None
    status: Status | None = None


class ScheduleQuery(Schema):
    week: Week
    employee_id: str | None = None
    group: Group | None = None
    date: Date | None = None
    shift_id: ShiftId | None = None
    role: EmployeeRole | None = None
    night_only: bool = False


class Slot(Schema):
    employee_id: str
    shift_id: ShiftId
    date: Date

    @property
    def key(self) -> tuple[str, str, Date]:
        return self.employee_id, self.shift_id, self.date


class AssignmentQuery(Slot):
    instead_of: list[Slot] = []


class WeekQuery(Schema):
    week: Week


class ScheduleChange(Schema):
    add: list[Slot] = []
    remove: list[Slot] = []


class AutoScheduleQuery(Schema):
    week: Week
    group: Group | None = None


class ProposalQuery(Schema):
    proposal_id: str


class ConstraintInput(Schema):
    group: Group
    date: Date
    type: ConstraintType
    priority: Priority = Priority.MEDIUM
    reason: str = ""
    employee_id: str | None = None


class Review(Schema):
    constraint_id: str
    approve: bool


class NavigationParams(Schema):
    week: Week | None = None
    date: Date | None = None
    from_date: Date | None = None
    to_date: Date | None = None
    group: Group | None = None
    shift_id: ShiftId | None = None
    employee_id: str | None = None
    role: EmployeeRole | None = None
    night_only: bool | None = None
    proposal_id: str | None = None


class Navigate(NavigationParams):
    target: TargetName


class Clarification(Schema):
    question: str
    options: list[str] = []


class Handoff(Schema):
    agent: Agent
    request: str
    employee_id: str | None = None
    date: Date | None = None
    week: Week | None = None
