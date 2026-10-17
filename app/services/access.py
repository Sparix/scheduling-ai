from app.errors import PermissionDenied
from app.repository import Repository
from app.schemas import Actor, Employee, UserRole


def demo_accounts(repo: Repository) -> dict[str, Actor]:
    manager = Actor(
        user_id=repo.department.manager_id,
        name=repo.department.manager_name,
        role=UserRole.MANAGER,
    )
    employees = [
        Actor(user_id=e.id, name=e.name, role=UserRole.EMPLOYEE, employee_id=e.id)
        for e in repo.employees.values()
    ]
    return {actor.user_id: actor for actor in [manager, *employees]}


class Access:
    def __init__(self, repo: Repository):
        self.repo = repo

    def manager(self, actor: Actor) -> None:
        if actor.role != UserRole.MANAGER:
            raise PermissionDenied("Only a manager can do this.")

    def employee(self, actor: Actor, employee_id: str) -> Employee:
        if actor.role == UserRole.EMPLOYEE and employee_id != actor.employee_id:
            raise PermissionDenied("You can access only your own data.")
        return self.repo.get_employee(employee_id)

    def scope(self, actor: Actor, employee_id: str | None) -> str | None:
        if employee_id:
            return self.employee(actor, employee_id).id
        return actor.employee_id
