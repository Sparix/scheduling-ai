from app.config import Clock
from app.errors import AppError, NotFound
from app.queries import ConstraintInput, Review
from app.repository import Repository
from app.rule_engine import RuleEngine
from app.schemas import Actor, Constraint, ConstraintDecision, Status, weekday_of
from app.services.access import Access
from app.services.schedules import ActionOutcome


class ConstraintService:
    def __init__(
        self, repo: Repository, access: Access, engine: RuleEngine, clock: Clock
    ):
        self.repo = repo
        self.access = access
        self.engine = engine
        self.clock = clock

    def draft(self, actor: Actor, query: ConstraintInput) -> Constraint:
        employee_id = query.employee_id or actor.employee_id
        if employee_id is None:
            raise AppError("Say whose constraint this is.")
        employee = self.access.employee(actor, employee_id)
        group = self.repo.groups[query.group]
        if weekday_of(query.date) not in group.active_days:
            raise AppError(
                f"The {group.name} group does not run on {weekday_of(query.date)}."
            )
        return Constraint(
            employee_id=employee.id,
            group=query.group,
            date=query.date,
            type=query.type,
            priority=query.priority,
            reason=query.reason,
            submitted_at=self.clock.now(),
        )

    def check_constraint(
        self, actor: Actor, query: ConstraintInput
    ) -> ConstraintDecision:
        draft = self.draft(actor, query)
        return self.engine.check_constraint(draft, list(self.repo.constraints.values()))

    def submit_constraint(self, actor: Actor, query: ConstraintInput) -> ActionOutcome:
        draft = self.draft(actor, query)
        decision = self.engine.check_constraint(
            draft, list(self.repo.constraints.values())
        )
        if decision.status == Status.REJECTED:
            return ActionOutcome(
                action="submit_constraint",
                applied=False,
                message="Rejected by the rule engine.",
                violations=decision.violations,
            )
        draft.id = f"C{len(self.repo.constraints) + 1:03d}"
        draft.status = decision.status
        self.repo.constraints[draft.id] = draft
        return ActionOutcome(
            action="submit_constraint",
            applied=True,
            message=f"Saved {draft.id} as {draft.status}.",
            warnings=decision.warnings,
        )

    def review_constraint(self, actor: Actor, query: Review) -> ActionOutcome:
        self.access.manager(actor)
        constraint = self.repo.constraints.get(query.constraint_id.upper())
        if constraint is None:
            raise NotFound(f"Constraint {query.constraint_id} not found.")
        decision = self.engine.check_constraint(
            constraint, list(self.repo.constraints.values())
        )
        if query.approve and decision.violations:
            return ActionOutcome(
                action="review_constraint",
                applied=False,
                message="Rule violations cannot be approved.",
                violations=decision.violations,
            )
        constraint.status = Status.APPROVED if query.approve else Status.REJECTED
        return ActionOutcome(
            action="review_constraint",
            applied=True,
            message=f"{constraint.id} is {constraint.status}.",
        )
