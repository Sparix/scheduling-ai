from collections import Counter
from collections.abc import Callable
from datetime import date as Date
from datetime import datetime, timedelta

from app.config import Clock
from app.errors import AppError, NotFound, PermissionDenied
from app.queries import Navigate, NavigationParams
from app.repository import Repository
from app.schemas import (
    Actor,
    Group,
    Schema,
    Shift,
    Target,
    TargetKind,
    TargetName,
    UserRole,
    week_days,
    week_start_of,
)
from app.services.access import Access

MANAGER = {UserRole.MANAGER}
EMPLOYEE = {UserRole.EMPLOYEE}
EVERYONE = {UserRole.MANAGER, UserRole.EMPLOYEE}
SCREEN = TargetKind.SCREEN
REPORT = TargetKind.REPORT

TARGETS = [
    Target(
        name=TargetName.SCHEDULE_BOARD,
        kind=SCREEN,
        description="Department weekly schedule with filters.",
        roles=MANAGER,
        params={
            "week",
            "date",
            "group",
            "shift_id",
            "employee_id",
            "role",
            "night_only",
        },
        required={"week"},
    ),
    Target(
        name=TargetName.MY_SCHEDULE,
        kind=SCREEN,
        description="Your own shifts.",
        roles=EMPLOYEE,
        params={"week"},
        required={"week"},
    ),
    Target(
        name=TargetName.EMPLOYEE_PROFILE,
        kind=SCREEN,
        description="One employee's profile.",
        roles=EVERYONE,
        params={"employee_id"},
        required={"employee_id"},
    ),
    Target(
        name=TargetName.CONSTRAINTS_REVIEW,
        kind=SCREEN,
        description="Submitted constraints and pending approvals.",
        roles=MANAGER,
        params={"week", "group", "employee_id"},
    ),
    Target(
        name=TargetName.MY_CONSTRAINTS,
        kind=SCREEN,
        description="Your own requests and blocks.",
        roles=EMPLOYEE,
        params={"week"},
    ),
    Target(
        name=TargetName.ABSENCES,
        kind=SCREEN,
        description="Vacations, sick leave and reserve duty.",
        roles=MANAGER,
        params={"from_date", "to_date", "employee_id"},
    ),
    Target(
        name=TargetName.AUTO_SCHEDULE_REVIEW,
        kind=SCREEN,
        description="Review an automatic scheduling proposal.",
        roles=MANAGER,
        params={"week", "group", "proposal_id"},
        required={"week"},
    ),
    Target(
        name=TargetName.SHIFTS,
        kind=SCREEN,
        description="Shifts and shift groups.",
        roles=EVERYONE,
        params={"group", "shift_id"},
    ),
    Target(
        name=TargetName.RULES,
        kind=SCREEN,
        description="Work rules.",
        roles=EVERYONE,
    ),
    Target(
        name=TargetName.WEEKEND_FAIRNESS,
        kind=REPORT,
        description="Weekend shifts per employee.",
        roles=MANAGER,
        params={"from_date", "to_date"},
        required={"from_date", "to_date"},
    ),
    Target(
        name=TargetName.NIGHT_FAIRNESS,
        kind=REPORT,
        description="Night shifts per employee.",
        roles=MANAGER,
        params={"from_date", "to_date"},
        required={"from_date", "to_date"},
    ),
    Target(
        name=TargetName.HOURS,
        kind=REPORT,
        description="Weekly hours per employee.",
        roles=EVERYONE,
        params={"week"},
        required={"week"},
    ),
    Target(
        name=TargetName.COVERAGE,
        kind=REPORT,
        description="Missing people per shift.",
        roles=MANAGER,
        params={"week", "group"},
        required={"week"},
    ),
]


class NavigationTarget(Schema):
    target: TargetName
    kind: TargetKind
    params: NavigationParams


class ReportRow(Schema):
    label: str
    value: float
    employee_id: str | None = None
    detail: str = ""


class Context(Schema):
    user: Actor
    now: datetime
    this_week: Date
    next_week: Date
    active_week: Date
    last_four_weeks: str
    targets: list[str]


class NavigationService:
    def __init__(self, repo: Repository, access: Access, clock: Clock):
        self.repo = repo
        self.access = access
        self.clock = clock

    def context(self, actor: Actor) -> Context:
        week = week_start_of(self.clock.today())
        first, last = week - timedelta(weeks=4), week - timedelta(days=1)
        return Context(
            user=actor,
            now=self.clock.now(),
            this_week=week,
            next_week=week + timedelta(weeks=1),
            active_week=self.repo.department.active_week,
            last_four_weeks=f"{first} to {last}",
            targets=[
                f"{t.name}: {t.description} Params {sorted(t.params)}, "
                f"required {sorted(t.required)}."
                for t in TARGETS
                if actor.role in t.roles
            ],
        )

    def navigate(self, actor: Actor, query: Navigate) -> NavigationTarget:
        target = next(t for t in TARGETS if t.name == query.target)
        if actor.role not in target.roles:
            raise PermissionDenied(f"You cannot open {target.name}.")
        given = {
            name
            for name in NavigationParams.model_fields
            if getattr(query, name) is not None
        }
        if given - target.params:
            raise AppError(f"{target.name} accepts only {sorted(target.params)}.")
        if target.required - given:
            raise AppError(f"{target.name} needs {sorted(target.required)}.")
        if query.employee_id:
            self.access.employee(actor, query.employee_id)
        if query.shift_id:
            self.repo.get_shift(query.shift_id)
        if query.proposal_id and query.proposal_id not in self.repo.proposals:
            raise NotFound(f"Proposal {query.proposal_id} not found.")
        return NavigationTarget(target=target.name, kind=target.kind, params=query)

    def run_report(self, actor: Actor, query: Navigate) -> list[ReportRow]:
        target = self.navigate(actor, query)
        match target.target:
            case TargetName.WEEKEND_FAIRNESS:
                return self.fairness(query, lambda s: s.group == Group.WEEKEND)
            case TargetName.NIGHT_FAIRNESS:
                return self.fairness(query, lambda s: s.is_night)
            case TargetName.HOURS:
                return self.hours(actor, query)
            case TargetName.COVERAGE:
                return self.coverage(query)
        raise AppError(f"{target.target} is a screen, not a report.")

    def fairness(
        self, query: Navigate, counts_as: Callable[[Shift], bool]
    ) -> list[ReportRow]:
        shifts = [s for s in self.repo.shifts.values() if counts_as(s)]
        counts = Counter(
            a.employee_id
            for a in self.repo.assignments()
            if query.from_date <= a.date <= query.to_date
            and counts_as(self.repo.shifts[a.shift_id])
        )
        rows = [
            ReportRow(label=e.name, value=counts[e.id], employee_id=e.id)
            for e in self.repo.employees.values()
            if any(e.qualified_for(s) for s in shifts)
        ]
        return sorted(rows, key=lambda row: -row.value)

    def hours(self, actor: Actor, query: Navigate) -> list[ReportRow]:
        employee_id = self.access.scope(actor, None)
        rows = []
        for employee in self.repo.employees.values():
            if employee_id not in (None, employee.id):
                continue
            worked = sum(
                self.repo.shifts[a.shift_id].hours
                for a in self.repo.assignments()
                if a.employee_id == employee.id
                and week_start_of(a.date) == query.week
                and not self.repo.shifts[a.shift_id].on_call
            )
            limit = (self.repo.rules["13"].limit or 0) * employee.job_scope
            rows.append(
                ReportRow(
                    label=employee.name,
                    value=worked,
                    employee_id=employee.id,
                    detail=f"limit {limit:g}h",
                )
            )
        return rows

    def coverage(self, query: Navigate) -> list[ReportRow]:
        assignments = self.repo.assignments()
        rows = []
        for shift in self.repo.shifts.values():
            if query.group not in (None, shift.group):
                continue
            for day in week_days(query.week):
                staffed = sum(
                    1 for a in assignments if a.shift_id == shift.id and a.date == day
                )
                if staffed < shift.needed(day):
                    rows.append(
                        ReportRow(
                            label=f"{shift.id} {shift.name} {day}",
                            value=shift.needed(day) - staffed,
                            detail=f"{staffed}/{shift.needed(day)}",
                        )
                    )
        return rows
