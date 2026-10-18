from collections import Counter
from datetime import date as Date
from datetime import datetime, time, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, computed_field


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Weekday(StrEnum):
    SUN = "sun"
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"


class UserRole(StrEnum):
    MANAGER = "manager"
    EMPLOYEE = "employee"


class EmployeeRole(StrEnum):
    EMPLOYEE = "employee"
    SHIFT_LEAD = "shift_lead"


class Skill(StrEnum):
    PICKING = "picking"
    PACKING = "packing"
    FORKLIFT = "forklift"
    GOODS_RECEIVING = "goods_receiving"
    QUALITY_CONTROL = "quality_control"
    DISTRIBUTION = "distribution"
    FIRST_AID = "first_aid"
    TRAINING = "training"


class Group(StrEnum):
    MORNING = "morning"
    MIDDAY = "midday"
    EVENING = "evening"
    NIGHT = "night"
    DOUBLES = "doubles"
    WEEKEND = "weekend"
    ON_CALL = "on_call"


class ConstraintType(StrEnum):
    REQUEST = "request"
    BLOCK = "block"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Status(StrEnum):
    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class AbsenceType(StrEnum):
    VACATION = "vacation"
    SICK_LEAVE = "sick_leave"
    RESERVE_DUTY = "reserve_duty"


class RuleType(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    SUBMISSION = "submission"
    AGE = "age"


class ScheduleStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    LOCKED = "locked"


class Source(StrEnum):
    MANUAL = "manual"
    AUTOMATIC = "automatic"


class TargetKind(StrEnum):
    SCREEN = "screen"
    REPORT = "report"


class TargetName(StrEnum):
    SCHEDULE_BOARD = "schedule_board"
    MY_SCHEDULE = "my_schedule"
    EMPLOYEE_PROFILE = "employee_profile"
    CONSTRAINTS_REVIEW = "constraints_review"
    MY_CONSTRAINTS = "my_constraints"
    ABSENCES = "absences"
    AUTO_SCHEDULE_REVIEW = "auto_schedule_review"
    SHIFTS = "shifts"
    RULES = "rules"
    WEEKEND_FAIRNESS = "weekend_fairness"
    NIGHT_FAIRNESS = "night_fairness"
    HOURS = "hours"
    COVERAGE = "coverage"


WEEKDAYS = list(Weekday)


def weekday_of(day: Date) -> Weekday:
    return WEEKDAYS[(day.weekday() + 1) % 7]


def week_start_of(day: Date) -> Date:
    return day - timedelta(days=(day.weekday() + 1) % 7)


def week_days(week: Date) -> list[Date]:
    return [week + timedelta(days=offset) for offset in range(7)]


class Actor(Schema):
    user_id: str
    name: str
    role: UserRole
    employee_id: str | None = None


class Department(Schema):
    id: str
    name: str
    manager_id: str
    manager_name: str
    active_week: Date


class Shift(Schema):
    id: str
    name: str
    group: Group
    start: time
    end: time
    skill: Skill
    level: int
    lead: bool = False
    on_call: bool = False
    headcount: dict[Weekday, int]

    @computed_field
    @property
    def hours(self) -> float:
        start, end = self.interval(Date(2026, 1, 1))
        return (end - start).total_seconds() / 3600

    @computed_field
    @property
    def is_night(self) -> bool:
        return self.end <= self.start and not self.on_call

    @property
    def is_morning(self) -> bool:
        return not self.on_call and self.start < self.end and self.start < time(12)

    def interval(self, day: Date) -> tuple[datetime, datetime]:
        end_day = day + timedelta(days=1) if self.end <= self.start else day
        return datetime.combine(day, self.start), datetime.combine(end_day, self.end)

    def needed(self, day: Date) -> int:
        return self.headcount.get(weekday_of(day), 0)


class Employee(Schema):
    id: str
    name: str
    date_of_birth: Date
    job_scope: float
    seniority_years: int
    skills: dict[Skill, int]
    role: EmployeeRole
    onboarding: bool = False
    night_exemption: bool = False

    def age_on(self, day: Date) -> int:
        born = self.date_of_birth
        return day.year - born.year - ((day.month, day.day) < (born.month, born.day))

    def qualified_for(self, shift: Shift) -> bool:
        if shift.lead and self.role != EmployeeRole.SHIFT_LEAD:
            return False
        return self.skills.get(shift.skill, 0) >= shift.level


class ShiftGroup(Schema):
    id: Group
    name: str
    active_days: list[Weekday]
    block_quota: int
    request_quota: int
    deadline_day: Weekday = Weekday.WED
    deadline_time: time = time(12)

    def deadline(self, week: Date) -> datetime:
        day = week - timedelta(days=7 - WEEKDAYS.index(self.deadline_day))
        return datetime.combine(day, self.deadline_time)


class Constraint(Schema):
    id: str = ""
    employee_id: str
    group: Group
    date: Date
    type: ConstraintType
    priority: Priority = Priority.MEDIUM
    reason: str = ""
    status: Status = Status.PENDING
    submitted_at: datetime


class Absence(Schema):
    id: str
    employee_id: str
    type: AbsenceType
    start: Date
    end: Date
    status: Status

    def covers(self, day: Date) -> bool:
        return self.status == Status.APPROVED and self.start <= day <= self.end


class WorkRule(Schema):
    id: str
    title: str
    type: RuleType
    weight: Priority | None = None
    limit: float | None = None


class Assignment(Schema):
    employee_id: str
    shift_id: str
    date: Date
    source: Source = Source.MANUAL

    @property
    def key(self) -> tuple[str, str, Date]:
        return self.employee_id, self.shift_id, self.date


class Schedule(Schema):
    week_start: Date
    status: ScheduleStatus
    score: float | None = None
    assignments: list[Assignment]


class Target(Schema):
    name: TargetName
    kind: TargetKind
    description: str
    roles: set[UserRole]
    params: set[str] = set()
    required: set[str] = set()


class Violation(Schema):
    rule: str
    message: str
    employee_id: str | None = None
    shift_id: str | None = None
    date: Date | None = None

    @property
    def key(self) -> tuple:
        return self.rule, self.employee_id, self.shift_id, self.date

    @property
    def is_gap(self) -> bool:
        return self.employee_id is None and self.rule in ("11", "12")


class Decision(Schema):
    violations: list[Violation] = []

    @computed_field
    @property
    def legal(self) -> bool:
        return not self.violations


class SoftScore(Schema):
    rule: str
    penalty: float
    details: list[str] = []


class ScheduleCheck(Schema):
    violations: list[Violation]
    soft: list[SoftScore]
    score: float

    @computed_field
    @property
    def per_rule(self) -> dict[str, int]:
        return dict(Counter(v.rule for v in self.violations))


class ConstraintDecision(Schema):
    status: Status
    violations: list[Violation] = []
    warnings: list[Violation] = []


class Blocker(Schema):
    rule: str
    message: str
    employee_ids: list[str] = []


class Unfilled(Schema):
    shift_id: str
    date: Date
    missing: int
    blockers: list[Blocker]


class AutoSchedule(Schema):
    week: Date
    proposed: list[Assignment]
    unfilled: list[Unfilled]
