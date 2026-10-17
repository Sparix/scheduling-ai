from app.config import Clock
from app.errors import PermissionDenied
from app.queries import (
    AbsenceQuery,
    ConstraintQuery,
    EmployeeQuery,
    Empty,
    RuleQuery,
    ShiftQuery,
)
from app.repository import Repository
from app.schemas import (
    Absence,
    Actor,
    Constraint,
    Department,
    Employee,
    Schema,
    Shift,
    ShiftGroup,
    Skill,
    UserRole,
    WorkRule,
    week_start_of,
)
from app.services.access import Access


class Reference(Schema):
    department: Department
    skills: list[Skill]
    groups: list[ShiftGroup]


class EmployeeView(Employee):
    age: int


def match_name(name: str, employees: list[Employee]) -> list[Employee]:
    needle = name.casefold().strip()
    words = [e for e in employees if needle in e.name.casefold().split()]
    return words or [e for e in employees if needle in e.name.casefold()]


class ReadService:
    def __init__(self, repo: Repository, access: Access, clock: Clock):
        self.repo = repo
        self.access = access
        self.clock = clock

    def get_reference(self, actor: Actor, query: Empty) -> Reference:
        return Reference(
            department=self.repo.department,
            skills=list(Skill),
            groups=list(self.repo.groups.values()),
        )

    def search_employees(
        self, actor: Actor, query: EmployeeQuery
    ) -> list[EmployeeView]:
        employees = list(self.repo.employees.values())
        if query.employee_id:
            employees = [e for e in employees if e.id == query.employee_id]
        if query.name:
            employees = match_name(query.name, employees)
        if actor.role == UserRole.EMPLOYEE:
            if (query.employee_id or query.name) and actor.employee_id not in {
                e.id for e in employees
            }:
                raise PermissionDenied("You can access only your own data.")
            employees = [e for e in employees if e.id == actor.employee_id]
        today = self.clock.today()
        return [
            EmployeeView(**e.model_dump(), age=e.age_on(today))
            for e in employees
            if (query.skill is None or e.skills.get(query.skill, 0) >= query.min_level)
            and (query.role is None or e.role == query.role)
            and (query.onboarding is None or e.onboarding == query.onboarding)
            and (query.min_age is None or e.age_on(today) >= query.min_age)
            and (query.max_age is None or e.age_on(today) <= query.max_age)
        ]

    def search_shifts(self, actor: Actor, query: ShiftQuery) -> list[Shift]:
        return [
            s
            for s in self.repo.shifts.values()
            if (query.shift_id is None or s.id == query.shift_id)
            and (query.group is None or s.group == query.group)
            and (query.date is None or s.needed(query.date))
            and (not query.lead_only or s.lead)
            and (not query.night_only or s.is_night)
        ]

    def list_rules(self, actor: Actor, query: RuleQuery) -> list[WorkRule]:
        return [
            r
            for r in self.repo.rules.values()
            if (query.rule_id is None or r.id == query.rule_id.upper())
            and (query.type is None or r.type == query.type)
        ]

    def search_absences(self, actor: Actor, query: AbsenceQuery) -> list[Absence]:
        employee_id = self.access.scope(actor, query.employee_id)
        return [
            a
            for a in self.repo.absences
            if (employee_id is None or a.employee_id == employee_id)
            and (query.from_date is None or a.end >= query.from_date)
            and (query.to_date is None or a.start <= query.to_date)
        ]

    def search_constraints(
        self, actor: Actor, query: ConstraintQuery
    ) -> list[Constraint]:
        employee_id = self.access.scope(actor, query.employee_id)
        return [
            c
            for c in self.repo.constraints.values()
            if (employee_id is None or c.employee_id == employee_id)
            and (query.week is None or week_start_of(c.date) == query.week)
            and (query.group is None or c.group == query.group)
            and (query.status is None or c.status == query.status)
        ]
