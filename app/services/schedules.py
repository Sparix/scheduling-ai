from datetime import date as Date
from datetime import time

from app.errors import AppError, NotFound
from app.queries import (
    AssignmentQuery,
    AutoScheduleQuery,
    CandidateQuery,
    ProposalQuery,
    ScheduleChange,
    ScheduleQuery,
    WeekQuery,
)
from app.repository import Repository
from app.rule_engine import RuleEngine
from app.schemas import (
    Actor,
    Assignment,
    Decision,
    Schedule,
    ScheduleCheck,
    ScheduleStatus,
    Schema,
    Source,
    Unfilled,
    Violation,
    week_start_of,
)
from app.services.access import Access


class ScheduleRow(Schema):
    date: Date
    shift_id: str
    shift: str
    start: time
    end: time
    employee_id: str
    employee: str
    source: Source


class Candidate(Schema):
    employee_id: str
    employee: str
    violations: list[str]


class ActionOutcome(Schema):
    action: str
    applied: bool
    message: str
    violations: list[Violation] = []
    warnings: list[Violation] = []
    score_before: float | None = None
    score_after: float | None = None


class Proposal(Schema):
    proposal_id: str
    proposed: list[ScheduleRow]
    unfilled: list[Unfilled]
    score_before: float
    score_after: float


class ScheduleService:
    def __init__(self, repo: Repository, access: Access, engine: RuleEngine):
        self.repo = repo
        self.access = access
        self.engine = engine

    def schedule(self, week: Date) -> Schedule:
        schedule = self.repo.schedules.get(week_start_of(week))
        if schedule is None:
            raise NotFound(f"No schedule for the week of {week_start_of(week)}.")
        return schedule

    def draft(self, week: Date) -> Schedule:
        schedule = self.schedule(week)
        if schedule.status != ScheduleStatus.DRAFT:
            raise AppError(f"The week of {schedule.week_start} is {schedule.status}.")
        return schedule

    def row(self, item: Assignment) -> ScheduleRow:
        shift = self.repo.shifts[item.shift_id]
        return ScheduleRow(
            date=item.date,
            shift_id=shift.id,
            shift=shift.name,
            start=shift.start,
            end=shift.end,
            employee_id=item.employee_id,
            employee=self.repo.employees[item.employee_id].name,
            source=item.source,
        )

    def get_schedule(self, actor: Actor, query: ScheduleQuery) -> list[ScheduleRow]:
        employee_id = self.access.scope(actor, query.employee_id)
        rows = []
        for item in self.schedule(query.week).assignments:
            shift = self.repo.shifts[item.shift_id]
            employee = self.repo.employees[item.employee_id]
            if (
                (employee_id is None or item.employee_id == employee_id)
                and (query.date is None or item.date == query.date)
                and (query.shift_id is None or item.shift_id == query.shift_id)
                and (query.group is None or shift.group == query.group)
                and (query.role is None or employee.role == query.role)
                and (not query.night_only or shift.is_night)
            ):
                rows.append(self.row(item))
        return sorted(rows, key=lambda row: (row.date, row.start))

    def check_assignment(self, actor: Actor, query: AssignmentQuery) -> Decision:
        for slot in [query, *query.instead_of]:
            self.access.employee(actor, slot.employee_id)
        shift = self.repo.get_shift(query.shift_id)
        ignored = {slot.key for slot in query.instead_of}
        others = [a for a in self.repo.assignments() if a.key not in ignored]
        item = Assignment(
            employee_id=query.employee_id, shift_id=shift.id, date=query.date
        )
        return self.engine.check_assignment(item, others)

    def find_candidates(self, actor: Actor, query: CandidateQuery) -> list[Candidate]:
        self.access.manager(actor)
        shift = self.repo.get_shift(query.shift_id)
        ignored = {slot.key for slot in query.instead_of}
        others = [a for a in self.repo.assignments() if a.key not in ignored]
        on_shift = {
            a.employee_id
            for a in others
            if a.shift_id == shift.id and a.date == query.date
        }
        candidates = []
        for employee in self.repo.employees.values():
            if employee.id in on_shift:
                continue
            item = Assignment(
                employee_id=employee.id, shift_id=shift.id, date=query.date
            )
            decision = self.engine.check_assignment(item, others)
            candidates.append(
                Candidate(
                    employee_id=employee.id,
                    employee=employee.name,
                    violations=sorted({v.rule for v in decision.violations}),
                )
            )
        return sorted(candidates, key=lambda c: len(c.violations))

    def check_schedule(self, actor: Actor, query: WeekQuery) -> ScheduleCheck:
        self.access.manager(actor)
        return self.engine.check_schedule(query.week, self.repo.assignments())

    def update_assignment(self, actor: Actor, query: ScheduleChange) -> ActionOutcome:
        self.access.manager(actor)
        slots = query.add + query.remove
        if not slots:
            raise AppError("Nothing to change.")
        if len({week_start_of(slot.date) for slot in slots}) > 1:
            raise AppError("Change one week at a time.")
        schedule = self.draft(slots[0].date)
        keys = {a.key for a in schedule.assignments}
        for slot in query.remove:
            if slot.key not in keys:
                raise NotFound(
                    f"{slot.employee_id} is not on {slot.shift_id} on {slot.date}."
                )
        removed = {slot.key for slot in query.remove}
        kept = [a for a in schedule.assignments if a.key not in removed]
        added = [
            Assignment(
                employee_id=self.repo.get_employee(slot.employee_id).id,
                shift_id=self.repo.get_shift(slot.shift_id).id,
                date=slot.date,
            )
            for slot in query.add
        ]
        return self.commit("update_assignment", schedule, kept + added, added)

    def auto_schedule(self, actor: Actor, query: AutoScheduleQuery) -> Proposal:
        self.access.manager(actor)
        schedule = self.draft(query.week)
        shift_ids = [
            s.id for s in self.repo.shifts.values() if query.group in (None, s.group)
        ]
        current = self.repo.assignments()
        result = self.engine.auto_schedule(schedule.week_start, shift_ids, current)
        proposal_id = f"P{len(self.repo.proposals) + 1}"
        self.repo.proposals[proposal_id] = result
        before = self.engine.check_schedule(result.week, current)
        after = self.engine.check_schedule(result.week, current + result.proposed)
        return Proposal(
            proposal_id=proposal_id,
            proposed=[self.row(a) for a in result.proposed],
            unfilled=result.unfilled,
            score_before=before.score,
            score_after=after.score,
        )

    def apply_proposal(self, actor: Actor, query: ProposalQuery) -> ActionOutcome:
        self.access.manager(actor)
        result = self.repo.proposals.pop(query.proposal_id, None)
        if result is None:
            raise NotFound(f"Proposal {query.proposal_id} not found.")
        schedule = self.draft(result.week)
        items = schedule.assignments + result.proposed
        return self.commit("apply_proposal", schedule, items, result.proposed)

    def publish_schedule(self, actor: Actor, query: WeekQuery) -> ActionOutcome:
        self.access.manager(actor)
        schedule = self.draft(query.week)
        check = self.engine.check_schedule(schedule.week_start, self.repo.assignments())
        if check.violations:
            return ActionOutcome(
                action="publish_schedule",
                applied=False,
                message="Hard violations must be fixed first.",
                violations=check.violations,
            )
        schedule.status = ScheduleStatus.PUBLISHED
        return ActionOutcome(
            action="publish_schedule", applied=True, message="Published."
        )

    def commit(
        self,
        action: str,
        schedule: Schedule,
        items: list[Assignment],
        added: list[Assignment],
    ) -> ActionOutcome:
        week = schedule.week_start
        current = self.repo.assignments()
        proposed = [a for a in current if week_start_of(a.date) != week] + items
        for item in added:
            decision = self.engine.check_assignment(item, proposed)
            if not decision.legal:
                return ActionOutcome(
                    action=action,
                    applied=False,
                    message="The rule engine refused this change.",
                    violations=decision.violations,
                )
        before = self.engine.check_schedule(week, current)
        after = self.engine.check_schedule(week, proposed)
        known = {v.key for v in before.violations}
        new = [v for v in after.violations if v.key not in known and v.rule != "11"]
        if not new:
            schedule.assignments = items
            schedule.score = after.score
        return ActionOutcome(
            action=action,
            applied=not new,
            message="The change would break other rules." if new else "Applied.",
            violations=new,
            score_before=before.score,
            score_after=after.score,
        )
