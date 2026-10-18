from datetime import date as Date

from app.queries import (
    AbsenceQuery,
    ConstraintQuery,
    EmployeeQuery,
    Navigate,
    RuleQuery,
    ScheduleQuery,
    ShiftQuery,
    WeekQuery,
)
from app.repository import Repository
from app.schemas import Actor, Schema, Shift, Status, TargetName, UserRole, week_days
from app.services.navigation import NavigationService
from app.services.reads import ReadService
from app.services.schedules import ScheduleRow, ScheduleService

ALERT = "⚠"


class Table(Schema):
    title: str = ""
    columns: list[str]
    rows: list[list[str]]


class View(Schema):
    target: TargetName
    title: str
    subtitle: str = ""
    message: str = ""
    tables: list[Table] = []


def day(value: Date) -> str:
    return f"{value:%a %d.%m}"


def hours(shift: Shift | ScheduleRow) -> str:
    return f"{shift.start:%H:%M}–{shift.end:%H:%M}"


def yes(value: bool) -> str:
    return "yes" if value else "no"


class ViewService:
    def __init__(
        self,
        repo: Repository,
        reads: ReadService,
        schedules: ScheduleService,
        navigation: NavigationService,
    ):
        self.repo = repo
        self.reads = reads
        self.schedules = schedules
        self.navigation = navigation

    def open(self, actor: Actor, query: Navigate) -> View:
        self.navigation.navigate(actor, query)
        match query.target:
            case TargetName.SCHEDULE_BOARD | TargetName.MY_SCHEDULE:
                return self.schedule(actor, query)
            case TargetName.EMPLOYEE_PROFILE:
                return self.profile(actor, query)
            case TargetName.CONSTRAINTS_REVIEW | TargetName.MY_CONSTRAINTS:
                return self.constraints(actor, query)
            case TargetName.ABSENCES:
                return self.absences(actor, query)
            case TargetName.AUTO_SCHEDULE_REVIEW:
                return self.proposal(actor, query)
            case TargetName.SHIFTS:
                return self.shifts(actor, query)
            case TargetName.RULES:
                return self.rules(actor, query)
        return self.report(actor, query)

    def title(self, query: Navigate) -> str:
        return query.target.replace("_", " ").capitalize()

    def name(self, employee_id: str) -> str:
        return self.repo.employees[employee_id].name

    def grid(
        self, days: list[Date], shifts: list[Shift], rows: list[ScheduleRow], gaps: bool
    ) -> Table:
        def cell(shift: Shift, date: Date) -> str:
            lines = [
                f"{ALERT} {r.employee} ({', '.join(r.violations)})"
                if r.violations
                else r.employee
                for r in rows
                if r.shift_id == shift.id and r.date == date
            ]
            needed = shift.needed(date)
            if gaps and len(lines) < needed:
                lines.append(f"{ALERT} {len(lines)}/{needed}")
            return "\n".join(lines) or ("" if needed else "–")

        return Table(
            columns=["Shift", *[day(d) for d in days]],
            rows=[
                [f"{s.id} {s.name}\n{hours(s)}", *[cell(s, d) for d in days]]
                for s in shifts
            ],
        )

    def schedule(self, actor: Actor, query: Navigate) -> View:
        schedule = self.schedules.get_schedule(
            actor,
            ScheduleQuery(
                week=query.week,
                employee_id=query.employee_id,
                group=query.group,
                date=query.date,
                shift_id=query.shift_id,
                role=query.role,
                night_only=bool(query.night_only),
            ),
        )
        days = [query.date] if query.date else week_days(schedule.week)
        gaps = actor.role == UserRole.MANAGER and not (query.employee_id or query.role)
        shifts = [
            s
            for s in self.repo.shifts.values()
            if query.group in (None, s.group)
            and query.shift_id in (None, s.id)
            and (not query.night_only or s.is_night)
            and (
                any(r.shift_id == s.id for r in schedule.rows)
                or (gaps and any(s.needed(d) for d in days))
            )
        ]
        subtitle = (
            f"Week of {schedule.week} · {schedule.status} · "
            f"{schedule.assignments} assignments"
        )
        if actor.role == UserRole.MANAGER:
            check = self.schedules.check_schedule(actor, WeekQuery(week=schedule.week))
            if gaps:
                subtitle += f" · {sum(v.is_gap for v in check.violations)} gaps"
            subtitle += f" · score {check.score:g}"
        return View(
            target=query.target,
            title=self.title(query),
            subtitle=subtitle,
            tables=[self.grid(days, shifts, schedule.rows, gaps)],
        )

    def profile(self, actor: Actor, query: Navigate) -> View:
        [employee] = self.reads.search_employees(
            actor, EmployeeQuery(employee_id=query.employee_id)
        )
        skills = ", ".join(
            f"{skill} {level}" for skill, level in employee.skills.items()
        )
        details = Table(
            columns=["Field", "Value"],
            rows=[
                ["ID", employee.id],
                ["Age", str(employee.age)],
                ["Role", employee.role],
                ["Job scope", f"{employee.job_scope:g}"],
                ["Seniority", f"{employee.seniority_years} years"],
                ["Skills", skills],
                ["Onboarding", yes(employee.onboarding)],
                ["Night exemption", yes(employee.night_exemption)],
            ],
        )
        week = self.repo.department.active_week
        schedule = self.schedules.get_schedule(
            actor, ScheduleQuery(week=week, employee_id=employee.id)
        )
        shifts = Table(
            title=f"Shifts in the week of {week}",
            columns=["Date", "Shift", "Hours", "Violations"],
            rows=[
                [
                    day(r.date),
                    f"{r.shift_id} {r.shift}",
                    hours(r),
                    f"{ALERT} {', '.join(r.violations)}" if r.violations else "",
                ]
                for r in schedule.rows
            ],
        )
        return View(
            target=query.target,
            title=employee.name,
            subtitle=self.title(query),
            tables=[details, shifts],
        )

    def constraints(self, actor: Actor, query: Navigate) -> View:
        items = self.reads.search_constraints(
            actor,
            ConstraintQuery(
                employee_id=query.employee_id,
                week=query.week,
                group=query.group,
                status=query.status,
            ),
        )
        table = Table(
            columns=["ID", "Employee", "Group", "Date", "Type", "Priority", "Status"],
            rows=[
                [
                    c.id,
                    self.name(c.employee_id),
                    c.group,
                    day(c.date),
                    c.type,
                    c.priority,
                    c.status if c.status != Status.PENDING else f"{ALERT} {c.status}",
                ]
                for c in sorted(items, key=lambda c: (c.date, c.employee_id))
            ],
        )
        return View(
            target=query.target,
            title=self.title(query),
            subtitle=f"Total: {len(items)}",
            tables=[table],
        )

    def absences(self, actor: Actor, query: Navigate) -> View:
        items = self.reads.search_absences(
            actor,
            AbsenceQuery(
                employee_id=query.employee_id,
                from_date=query.from_date,
                to_date=query.to_date,
            ),
        )
        table = Table(
            columns=["Employee", "Type", "From", "To", "Status"],
            rows=[
                [self.name(a.employee_id), a.type, day(a.start), day(a.end), a.status]
                for a in sorted(items, key=lambda a: a.start)
            ],
        )
        return View(target=query.target, title=self.title(query), tables=[table])

    def proposal(self, actor: Actor, query: Navigate) -> View:
        proposal_id = query.proposal_id or next(
            (
                key
                for key, result in reversed(self.repo.proposals.items())
                if result.week == query.week
            ),
            None,
        )
        if proposal_id is None:
            return View(
                target=query.target,
                title=self.title(query),
                message="No proposal for this week yet.",
            )
        proposal = self.schedules.proposal(actor, proposal_id)
        shifts = [
            s
            for s in self.repo.shifts.values()
            if any(r.shift_id == s.id for r in proposal.proposed)
            or any(u.shift_id == s.id for u in proposal.unfilled)
        ]
        unfilled = Table(
            title="Unfilled",
            columns=["Shift", "Date", "Missing", "Blocked by"],
            rows=[
                [
                    f"{u.shift_id} {self.repo.shifts[u.shift_id].name}",
                    day(u.date),
                    f"{ALERT} {u.missing}",
                    "\n".join(f"Rule {b.rule}: {b.message}" for b in u.blockers),
                ]
                for u in proposal.unfilled
            ],
        )
        return View(
            target=query.target,
            title=self.title(query),
            subtitle=(
                f"{proposal_id} · {len(proposal.proposed)} proposed · "
                f"score {proposal.score_before:g} → {proposal.score_after:g}"
            ),
            tables=[
                self.grid(week_days(proposal.week), shifts, proposal.proposed, False),
                unfilled,
            ],
        )

    def shifts(self, actor: Actor, query: Navigate) -> View:
        items = self.reads.search_shifts(
            actor, ShiftQuery(shift_id=query.shift_id, group=query.group)
        )
        table = Table(
            columns=["Shift", "Group", "Hours", "Skill", "Headcount"],
            rows=[
                [
                    f"{s.id} {s.name}",
                    s.group,
                    hours(s),
                    f"{s.skill} {s.level}" + (", shift lead" if s.lead else ""),
                    " ".join(f"{d}:{n}" for d, n in s.headcount.items() if n),
                ]
                for s in items
            ],
        )
        return View(target=query.target, title=self.title(query), tables=[table])

    def rules(self, actor: Actor, query: Navigate) -> View:
        items = self.reads.list_rules(actor, RuleQuery())
        table = Table(
            columns=["No.", "Type", "Rule", "Limit or weight"],
            rows=[
                [r.id, r.type, r.title, f"{r.limit:g}" if r.limit else r.weight or ""]
                for r in items
            ],
        )
        return View(target=query.target, title=self.title(query), tables=[table])

    def report(self, actor: Actor, query: Navigate) -> View:
        rows = self.navigation.run_report(actor, query)
        period = (
            f"{query.from_date} to {query.to_date}"
            if query.from_date
            else f"Week of {query.week}"
        )
        detail = any(r.detail for r in rows)
        table = Table(
            columns=["Name", "Value", "Detail"] if detail else ["Name", "Value"],
            rows=[
                [r.label, f"{r.value:g}", r.detail][: 3 if detail else 2] for r in rows
            ],
        )
        return View(
            target=query.target,
            title=self.title(query),
            subtitle=period,
            tables=[table],
        )
