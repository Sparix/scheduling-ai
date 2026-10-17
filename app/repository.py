from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from app.config import DATA_DIR
from app.errors import NotFound
from app.schemas import (
    Absence,
    Assignment,
    AutoSchedule,
    Constraint,
    Department,
    Employee,
    Schedule,
    Shift,
    ShiftGroup,
    WorkRule,
)


def load[T: BaseModel](path: Path, model: type[T]) -> list[T]:
    return TypeAdapter(list[model]).validate_json(path.read_text())


class Repository:
    def __init__(self, folder: Path = DATA_DIR):
        self.department = Department.model_validate_json(
            (folder / "department.json").read_text()
        )
        self.employees = {e.id: e for e in load(folder / "employees.json", Employee)}
        self.groups = {g.id: g for g in load(folder / "shift_groups.json", ShiftGroup)}
        self.shifts = {s.id: s for s in load(folder / "shifts.json", Shift)}
        self.rules = {r.id: r for r in load(folder / "rules.json", WorkRule)}
        self.constraints = {
            c.id: c for c in load(folder / "constraints.json", Constraint)
        }
        self.absences = load(folder / "absences.json", Absence)
        self.schedules = {
            s.week_start: s for s in load(folder / "schedules.json", Schedule)
        }
        self.proposals: dict[str, AutoSchedule] = {}

    def assignments(self) -> list[Assignment]:
        return [a for schedule in self.schedules.values() for a in schedule.assignments]

    def get_employee(self, employee_id: str) -> Employee:
        if employee_id not in self.employees:
            raise NotFound(f"Employee {employee_id} not found.")
        return self.employees[employee_id]

    def get_shift(self, shift_id: str) -> Shift:
        if shift_id not in self.shifts:
            raise NotFound(f"Shift {shift_id} not found.")
        return self.shifts[shift_id]
