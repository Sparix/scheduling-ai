from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date as Date
from datetime import datetime, time, timedelta

from app.repository import Repository
from app.schemas import (
    Assignment,
    AutoSchedule,
    Blocker,
    Constraint,
    ConstraintDecision,
    ConstraintType,
    Decision,
    Employee,
    EmployeeRole,
    Group,
    Priority,
    ScheduleCheck,
    Shift,
    SoftScore,
    Source,
    Status,
    Unfilled,
    Violation,
    week_days,
    week_start_of,
)

MINOR_AGE = 18
SENIOR_AGE = 55
ELDER_AGE = 60
NIGHT_START = time(22)
NIGHT_END = time(6)
FAIRNESS_WEEKS = 4
WEIGHT = {Priority.HIGH: 3, Priority.MEDIUM: 2, Priority.LOW: 1}
WEEKLY_RULES = {"2", "4", "13", "A3", "A7"}


def hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 2)


def violation(item: Assignment, rule: str, message: str) -> Violation:
    return Violation(
        rule=rule,
        message=message,
        employee_id=item.employee_id,
        shift_id=item.shift_id,
        date=item.date,
    )


def in_night_window(start: datetime, end: datetime) -> bool:
    day = start.date() - timedelta(days=1)
    while day <= end.date():
        night_start = datetime.combine(day, NIGHT_START)
        night_end = datetime.combine(day + timedelta(days=1), NIGHT_END)
        if start < night_end and night_start < end:
            return True
        day += timedelta(days=1)
    return False


def longest_rest(week: Date, intervals: list[tuple[datetime, datetime]]) -> float:
    cursor = datetime.combine(week, time())
    week_end = cursor + timedelta(days=7)
    longest = timedelta()
    for start, end in sorted(intervals):
        if end <= cursor or start >= week_end:
            continue
        longest = max(longest, start - cursor)
        cursor = max(cursor, end)
    return hours(max(longest, week_end - cursor))


def streak(days: set[Date], day: Date, step: int) -> int:
    count = 0
    while day + timedelta(days=step * (count + 1)) in days:
        count += 1
    return count


class RuleEngine:
    def __init__(self, repo: Repository):
        self.repo = repo

    def limit(self, rule: str) -> float:
        return self.repo.rules[rule].limit or 0

    def check_assignment(
        self, assignment: Assignment, assignments: list[Assignment]
    ) -> Decision:
        return Decision(violations=self.violations(assignment, assignments))

    def violations(
        self, item: Assignment, assignments: list[Assignment]
    ) -> list[Violation]:
        employee = self.repo.employees[item.employee_id]
        shift = self.repo.shifts[item.shift_id]
        start, end = shift.interval(item.date)
        own = [
            (self.repo.shifts[other.shift_id], other.date)
            for other in assignments
            if other is not item
            and other.employee_id == employee.id
            and abs((other.date - item.date).days) <= 8
        ]
        found = []
        if self.absent(employee.id, item.date):
            found.append(violation(item, "9", f"{employee.name} is on approved leave."))
        if self.blocked(employee.id, shift, item.date):
            found.append(violation(item, "7", f"{employee.name} blocked this group."))
        if employee.skills.get(shift.skill, 0) < shift.level:
            message = f"{shift.name} needs {shift.skill} level {shift.level}."
            found.append(violation(item, "8", message))
        if shift.lead and employee.role != EmployeeRole.SHIFT_LEAD:
            found.append(violation(item, "8", f"{shift.name} needs a shift lead."))
        requested = self.requested(employee.id, shift.group, item.date)
        found += self.age_violations(item, requested)
        for other, other_day in own:
            other_start, other_end = other.interval(other_day)
            if other_start < end and start < other_end:
                message = f"Overlaps {other.name} on {other_day}."
                found.append(violation(item, "10", message))
        if not shift.on_call:
            work = [(other, day) for other, day in own if not other.on_call]
            found += self.work_violations(item, work)
        return found + self.team_violations(item, assignments)

    def age_violations(self, item: Assignment, requested: bool) -> list[Violation]:
        employee = self.repo.employees[item.employee_id]
        shift = self.repo.shifts[item.shift_id]
        age = employee.age_on(item.date)
        found = []
        if age < MINOR_AGE:
            if shift.on_call:
                message = f"{employee.name} is {age}: no on-call shifts."
                found.append(violation(item, "A6", message))
            elif in_night_window(*shift.interval(item.date)):
                message = f"{employee.name} is {age}: no work between 22:00 and 06:00."
                found.append(violation(item, "A1", message))
            if shift.group == Group.DOUBLES:
                message = f"{employee.name} is {age}: no double shifts."
                found.append(violation(item, "A5", message))
        if shift.is_night and age >= SENIOR_AGE and employee.night_exemption:
            found.append(
                violation(item, "A8", f"{employee.name} is exempt from nights.")
            )
        if shift.is_night and age >= ELDER_AGE and not requested:
            message = f"{employee.name} is {age} and did not request this night."
            found.append(violation(item, "A9", message))
        if age >= ELDER_AGE and not shift.on_call and shift.hours > self.limit("A10"):
            message = f"{employee.name} is {age}; {shift.name} is {shift.hours:g}h."
            found.append(violation(item, "A10", message))
        return found

    def work_violations(
        self, item: Assignment, work: list[tuple[Shift, Date]]
    ) -> list[Violation]:
        employee = self.repo.employees[item.employee_id]
        shift = self.repo.shifts[item.shift_id]
        day = item.date
        start, end = shift.interval(day)
        minor = employee.age_on(day) < MINOR_AGE
        found = []

        def add(rule: str, message: str) -> None:
            found.append(violation(item, rule, message))

        for other, other_day in work:
            other_start, other_end = other.interval(other_day)
            if other_end <= start:
                gap = hours(start - other_end)
            elif end <= other_start:
                gap = hours(other_start - end)
            else:
                continue
            if gap < self.limit("1"):
                add("1", f"Only {gap:g}h of rest next to {other.name} on {other_day}.")
            if minor and gap < self.limit("A4"):
                add("A4", f"Only {gap:g}h of rest; minors need 12h.")
            if (shift.is_morning and other.is_night and other_end.date() == day) or (
                other.is_morning and shift.is_night and end.date() == other_day
            ):
                add("6", f"{other.name} and a morning shift on the same day.")

        week = week_start_of(day)
        this_week = [(s, d) for s, d in work if week_start_of(d) == week]
        this_week.append((shift, day))
        if shift.is_night:
            nights = {d for s, d in work if s.is_night} | {day}
            run = 1 + streak(nights, day, -1) + streak(nights, day, 1)
            if run > self.limit("5"):
                add("5", f"This would be night {run} in a row.")
            week_nights = sum(1 for s, _ in this_week if s.is_night)
            if employee.age_on(day) >= SENIOR_AGE and week_nights > self.limit("A7"):
                add("A7", f"Night {week_nights} this week.")
        day_hours = sum(s.hours for s, d in this_week if d == day)
        if day_hours > self.limit("3"):
            add("3", f"{day_hours:g}h of work on {day}.")
        if minor and day_hours > self.limit("A2"):
            add("A2", f"{day_hours:g}h of work on {day}.")
        work_days = len({d for _, d in this_week})
        if work_days > self.limit("4"):
            add("4", f"{work_days} work days this week.")
        week_hours = sum(s.hours for s, _ in this_week)
        if week_hours > self.limit("13") * employee.job_scope:
            add("13", f"{week_hours:g}h this week at job scope {employee.job_scope}.")
        if minor and week_hours > self.limit("A3"):
            add("A3", f"{week_hours:g}h this week.")
        rest = longest_rest(week, [s.interval(d) for s, d in work] + [(start, end)])
        if rest < self.limit("2"):
            add("2", f"Longest weekly rest would be {rest:g}h.")
        return found

    def team_violations(
        self, item: Assignment, assignments: list[Assignment]
    ) -> list[Violation]:
        employee = self.repo.employees[item.employee_id]
        shift = self.repo.shifts[item.shift_id]
        team = [
            self.repo.employees[other.employee_id]
            for other in assignments
            if other is not item
            and other.shift_id == item.shift_id
            and other.date == item.date
        ]
        found = []
        if len(team) >= shift.needed(item.date):
            message = f"{shift.name} takes {shift.needed(item.date)} on {item.date}."
            found.append(violation(item, "11", message))
        if employee.onboarding and any(mate.onboarding for mate in team):
            message = "Two onboarding employees on the same shift."
            found.append(violation(item, "15", message))
        if employee.onboarding and not self.veteran_alongside(item, assignments):
            message = f"No veteran with {shift.skill} works alongside {employee.name}."
            found.append(violation(item, "14", message))
        return found

    def veteran_alongside(
        self, item: Assignment, assignments: list[Assignment]
    ) -> bool:
        shift = self.repo.shifts[item.shift_id]
        start, end = shift.interval(item.date)
        for other in assignments:
            if other.employee_id == item.employee_id:
                continue
            if abs((other.date - item.date).days) > 1:
                continue
            other_shift = self.repo.shifts[other.shift_id]
            mate = self.repo.employees[other.employee_id]
            other_start, other_end = other_shift.interval(other.date)
            if (
                not other_shift.on_call
                and other_start < end
                and start < other_end
                and not mate.onboarding
                and mate.skills.get(shift.skill, 0)
            ):
                return True
        return False

    def absent(self, employee_id: str, day: Date) -> bool:
        return any(
            absence.employee_id == employee_id and absence.covers(day)
            for absence in self.repo.absences
        )

    def blocked(self, employee_id: str, shift: Shift, day: Date) -> bool:
        groups = {shift.group}
        if shift.group == Group.DOUBLES:
            groups |= {Group.MORNING, Group.EVENING}
        return any(
            c.employee_id == employee_id
            and c.type == ConstraintType.BLOCK
            and c.status == Status.APPROVED
            and c.date == day
            and c.group in groups
            for c in self.repo.constraints.values()
        )

    def requested(self, employee_id: str, group: Group, day: Date) -> bool:
        return any(
            c.employee_id == employee_id
            and c.type == ConstraintType.REQUEST
            and c.status == Status.APPROVED
            and c.date == day
            and c.group == group
            for c in self.repo.constraints.values()
        )

    def check_schedule(
        self, week: Date, assignments: list[Assignment]
    ) -> ScheduleCheck:
        days = week_days(week)
        items = [a for a in assignments if week_start_of(a.date) == week]
        found: dict[tuple, Violation] = {}
        for item in items:
            for found_violation in self.violations(item, assignments):
                if found_violation.rule in WEEKLY_RULES:
                    found_violation.shift_id = None
                    found_violation.date = None
                found.setdefault(found_violation.key, found_violation)
        for shift in self.repo.shifts.values():
            for day in days:
                staffed = sum(
                    1 for a in items if a.shift_id == shift.id and a.date == day
                )
                if staffed < shift.needed(day):
                    message = f"{shift.name} on {day}: {staffed}/{shift.needed(day)}."
                    understaffed = Violation(
                        rule="11", message=message, shift_id=shift.id, date=day
                    )
                    found[understaffed.key] = understaffed
        for day in days:
            nights = [
                s for s in self.repo.shifts.values() if s.is_night and s.needed(day)
            ]
            leads = [
                a
                for a in items
                if a.date == day
                and self.repo.shifts[a.shift_id].is_night
                and self.repo.employees[a.employee_id].role == EmployeeRole.SHIFT_LEAD
            ]
            if nights and not leads:
                missing_lead = Violation(
                    rule="12", message=f"No shift lead on the night of {day}.", date=day
                )
                found[missing_lead.key] = missing_lead
        soft = self.soft_scores(week, items, assignments)
        penalty = sum(WEIGHT[self.repo.rules[s.rule].weight] * s.penalty for s in soft)
        return ScheduleCheck(
            violations=list(found.values()),
            soft=soft,
            score=round(10000 / (100 + penalty), 1),
        )

    def soft_scores(
        self, week: Date, items: list[Assignment], assignments: list[Assignment]
    ) -> list[SoftScore]:
        work = [a for a in items if not self.repo.shifts[a.shift_id].on_call]
        return [
            self.requests_score(week, items),
            self.fairness_score(
                "17", week, assignments, self.counts_as_weekend, self.weekend_capable
            ),
            self.fairness_score(
                "18", week, assignments, self.counts_as_night, self.night_capable
            ),
            self.hours_score(work),
            self.doubles_score(work),
            self.isolated_days_score(work),
            self.stability_score(week, items, assignments),
        ]

    def requests_score(self, week: Date, items: list[Assignment]) -> SoftScore:
        missed = [
            c
            for c in self.repo.constraints.values()
            if c.type == ConstraintType.REQUEST
            and c.status == Status.APPROVED
            and week_start_of(c.date) == week
            and not any(
                a.employee_id == c.employee_id
                and a.date == c.date
                and self.repo.shifts[a.shift_id].group == c.group
                for a in items
            )
        ]
        return SoftScore(
            rule="16",
            penalty=sum(WEIGHT[c.priority] for c in missed),
            details=[
                f"{self.repo.employees[c.employee_id].name}: {c.group} on {c.date}"
                for c in missed
            ][:6],
        )

    def counts_as_weekend(self, shift: Shift) -> bool:
        return shift.group == Group.WEEKEND

    def counts_as_night(self, shift: Shift) -> bool:
        return shift.is_night

    def weekend_capable(self, employee: Employee, week: Date) -> bool:
        return any(
            employee.qualified_for(s)
            for s in self.repo.shifts.values()
            if self.counts_as_weekend(s)
        )

    def night_capable(self, employee: Employee, week: Date) -> bool:
        age = employee.age_on(week)
        return (
            MINOR_AGE <= age < ELDER_AGE
            and not employee.night_exemption
            and any(
                employee.qualified_for(s)
                for s in self.repo.shifts.values()
                if s.is_night
            )
        )

    def fairness_score(
        self,
        rule: str,
        week: Date,
        assignments: list[Assignment],
        counts_as: Callable[[Shift], bool],
        eligible: Callable[[Employee, Date], bool],
    ) -> SoftScore:
        start = week - timedelta(weeks=FAIRNESS_WEEKS - 1)
        counts = Counter(
            a.employee_id
            for a in assignments
            if start <= a.date < week + timedelta(days=7)
            and counts_as(self.repo.shifts[a.shift_id])
        )
        values = [
            counts[e.id] for e in self.repo.employees.values() if eligible(e, week)
        ]
        return SoftScore(
            rule=rule,
            penalty=max(0, max(values) - min(values) - 2),
            details=[
                f"From {min(values)} to {max(values)} over {FAIRNESS_WEEKS} weeks."
            ],
        )

    def hours_score(self, work: list[Assignment]) -> SoftScore:
        worked: defaultdict[str, float] = defaultdict(float)
        for a in work:
            worked[a.employee_id] += self.repo.shifts[a.shift_id].hours
        shortfalls = sorted(
            (
                (self.limit("19") * e.job_scope - worked[e.id], e.name)
                for e in self.repo.employees.values()
                if worked[e.id] < self.limit("19") * e.job_scope
            ),
            reverse=True,
        )
        return SoftScore(
            rule="19",
            penalty=round(sum(short for short, _ in shortfalls) / 8, 2),
            details=[f"{name} is {short:g}h short." for short, name in shortfalls][:6],
        )

    def doubles_score(self, work: list[Assignment]) -> SoftScore:
        doubles = Counter(
            a.employee_id
            for a in work
            if self.repo.shifts[a.shift_id].group == Group.DOUBLES
        )
        excess = [(e, n) for e, n in doubles.items() if n > self.limit("20")]
        return SoftScore(
            rule="20",
            penalty=sum(n - self.limit("20") for _, n in excess),
            details=[
                f"{self.repo.employees[e].name} has {n} doubles." for e, n in excess
            ],
        )

    def isolated_days_score(self, work: list[Assignment]) -> SoftScore:
        days: defaultdict[str, set[Date]] = defaultdict(set)
        for a in work:
            days[a.employee_id].add(a.date)
        isolated = [
            f"{self.repo.employees[e].name} on {day}"
            for e, worked in days.items()
            if len(worked) > 1
            for day in sorted(worked)
            if day - timedelta(days=1) not in worked
            and day + timedelta(days=1) not in worked
        ]
        return SoftScore(rule="21", penalty=len(isolated) * 0.5, details=isolated[:6])

    def stability_score(
        self, week: Date, items: list[Assignment], assignments: list[Assignment]
    ) -> SoftScore:
        last_week = week - timedelta(days=7)
        before: defaultdict[tuple, set[str]] = defaultdict(set)
        now: defaultdict[tuple, set[str]] = defaultdict(set)
        for a in assignments:
            if last_week <= a.date < week:
                before[a.shift_id, (a.date - last_week).days].add(a.employee_id)
        for a in items:
            now[a.shift_id, (a.date - week).days].add(a.employee_id)
        changed = [
            f"{shift_id} on day {offset + 1}"
            for (shift_id, offset), team in now.items()
            if before.get((shift_id, offset)) and not team & before[shift_id, offset]
        ]
        return SoftScore(rule="22", penalty=len(changed) * 0.25, details=changed[:6])

    def check_constraint(
        self, constraint: Constraint, constraints: list[Constraint]
    ) -> ConstraintDecision:
        employee = self.repo.employees[constraint.employee_id]
        group = self.repo.groups[constraint.group]
        week = week_start_of(constraint.date)
        mine = [
            c
            for c in constraints
            if c.id != constraint.id
            and c.employee_id == employee.id
            and c.status != Status.REJECTED
            and week_start_of(c.date) == week
        ]
        same = [c for c in mine if c.group == group.id and c.type == constraint.type]
        violations = []
        warnings = []
        if constraint.type == ConstraintType.REQUEST:
            if self.absent(employee.id, constraint.date):
                message = f"{employee.name} is on approved leave that day."
                violations.append(Violation(rule="9", message=message))
            per_shift = [
                self.age_violations(
                    Assignment(
                        employee_id=employee.id, shift_id=s.id, date=constraint.date
                    ),
                    requested=True,
                )
                for s in self.repo.shifts.values()
                if s.group == group.id and s.needed(constraint.date)
            ]
            if per_shift and all(per_shift):
                unique = {v.rule: v for found in per_shift for v in found}
                violations += unique.values()
            nights = 1 + sum(1 for c in mine if c.group == Group.NIGHT)
            if (
                group.id == Group.NIGHT
                and employee.age_on(constraint.date) >= SENIOR_AGE
                and nights > self.limit("A7")
            ):
                violations.append(
                    Violation(rule="A7", message=f"Night request {nights}.")
                )
        quota = (
            group.request_quota
            if constraint.type == ConstraintType.REQUEST
            else group.block_quota
        )
        if len(same) >= quota:
            message = (
                f"{constraint.type} {len(same) + 1} in {group.name}; quota {quota}."
            )
            warnings.append(Violation(rule="23", message=message))
        deadline = group.deadline(week)
        if constraint.submitted_at > deadline:
            message = f"Submitted after the deadline {deadline:%a %Y-%m-%d %H:%M}."
            warnings.append(Violation(rule="24", message=message))
        if violations:
            status = Status.REJECTED
        elif warnings:
            status = Status.PENDING
        else:
            status = Status.APPROVED
        return ConstraintDecision(
            status=status, violations=violations, warnings=warnings
        )

    def auto_schedule(
        self, week: Date, shift_ids: list[str], assignments: list[Assignment]
    ) -> AutoSchedule:
        current = list(assignments)
        proposed: list[Assignment] = []
        shifts = [self.repo.shifts[shift_id] for shift_id in shift_ids]
        slots = [(s, d) for s in shifts for d in week_days(week) if s.needed(d)]
        qualified = {
            s.id: [e for e in self.repo.employees.values() if e.qualified_for(s)]
            for s in shifts
        }

        def team(shift: Shift, day: Date) -> set[str]:
            return {
                a.employee_id
                for a in current
                if a.shift_id == shift.id and a.date == day
            }

        def add(employee_id: str, shift: Shift, day: Date) -> bool:
            item = Assignment(
                employee_id=employee_id,
                shift_id=shift.id,
                date=day,
                source=Source.AUTOMATIC,
            )
            if employee_id in team(shift, day) or self.violations(item, current):
                return False
            current.append(item)
            proposed.append(item)
            return True

        def fill_one(shift: Shift, day: Date) -> bool:
            ranked = sorted(
                qualified[shift.id],
                key=lambda e: self.rank(e, shift, day, week, current),
            )
            for employee in ranked:
                if add(employee.id, shift, day):
                    return True
            return False

        requests = sorted(
            (
                c
                for c in self.repo.constraints.values()
                if c.type == ConstraintType.REQUEST
                and c.status == Status.APPROVED
                and week_start_of(c.date) == week
            ),
            key=lambda c: (-WEIGHT[c.priority], c.submitted_at),
        )
        for request in requests:
            for shift, day in slots:
                if shift.group == request.group and day == request.date:
                    if add(request.employee_id, shift, day):
                        break

        slots.sort(
            key=lambda s: (not s[0].lead, len(qualified[s[0].id]), s[1], s[0].start)
        )
        unfilled = []
        for shift, day in slots:
            while len(team(shift, day)) < shift.needed(day):
                if not fill_one(shift, day):
                    break
            missing = shift.needed(day) - len(team(shift, day))
            if missing > 0:
                blockers = self.blockers(shift, day, current, qualified[shift.id])
                unfilled.append(
                    Unfilled(
                        shift_id=shift.id, date=day, missing=missing, blockers=blockers
                    )
                )
        unfilled.sort(key=lambda u: (u.date, u.shift_id))
        return AutoSchedule(week=week, proposed=proposed, unfilled=unfilled)

    def rank(
        self,
        employee: Employee,
        shift: Shift,
        day: Date,
        week: Date,
        current: list[Assignment],
    ) -> tuple:
        own = [
            (a.date, self.repo.shifts[a.shift_id])
            for a in current
            if a.employee_id == employee.id
            and a.date >= week - timedelta(weeks=3)
            and not self.repo.shifts[a.shift_id].on_call
        ]
        week_hours = sum(s.hours for d, s in own if d >= week)
        similar = sum(
            1
            for _, s in own
            if (shift.is_night and s.is_night)
            or (shift.group == Group.WEEKEND and s.group == Group.WEEKEND)
        )
        return (
            not self.requested(employee.id, shift.group, day),
            employee.onboarding,
            similar,
            week_hours / employee.job_scope,
            employee.id,
        )

    def blockers(
        self,
        shift: Shift,
        day: Date,
        current: list[Assignment],
        qualified: list[Employee],
    ) -> list[Blocker]:
        if not qualified:
            message = f"Nobody has {shift.skill} level {shift.level}"
            message += " with the shift lead role." if shift.lead else "."
            return [Blocker(rule="8", message=message)]
        on_shift = {
            a.employee_id for a in current if a.shift_id == shift.id and a.date == day
        }
        reasons: defaultdict[str, list[Employee]] = defaultdict(list)
        for employee in qualified:
            if employee.id in on_shift:
                continue
            item = Assignment(employee_id=employee.id, shift_id=shift.id, date=day)
            found = self.violations(item, current)
            if found:
                reasons[found[0].rule].append(employee)
        return [
            Blocker(
                rule=rule,
                message=f"{len(people)} of {len(qualified)} qualified "
                f"({', '.join(p.name for p in people)}): "
                f"{self.repo.rules[rule].title}.",
                employee_ids=[p.id for p in people],
            )
            for rule, people in reasons.items()
        ]
