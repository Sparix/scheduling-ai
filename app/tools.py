from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import BaseModel, ValidationError
from pydantic_core import to_json

from app.errors import AppError
from app.queries import Clarification, Handoff
from app.schemas import Actor, Schema, UserRole
from app.services.constraints import ConstraintService
from app.services.navigation import NavigationService
from app.services.reads import ReadService
from app.services.schedules import ScheduleService


def ask_clarification(actor: Actor, query: Clarification) -> Clarification:
    return query


def handoff(actor: Actor, query: Handoff) -> Handoff:
    return query


@dataclass
class Tool:
    handler: Callable[[Actor, Any], Any]
    description: str
    manager_only: bool = False

    @property
    def name(self) -> str:
        return self.handler.__name__

    @property
    def query_type(self) -> type[BaseModel]:
        return get_type_hints(self.handler)["query"]

    def definition(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.query_type.model_json_schema(),
            },
        }


class ToolError(Schema):
    code: str
    message: str


class ToolResult(Schema):
    tool: str
    arguments: str
    result: Any = None
    error: ToolError | None = None

    def for_model(self) -> str:
        return to_json(self.error or self.result, exclude_none=True).decode()


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools = {tool.name: tool for tool in tools}

    def visible(self, actor: Actor) -> list[Tool]:
        return [
            tool
            for tool in self.tools.values()
            if not tool.manager_only or actor.role == UserRole.MANAGER
        ]

    def execute(self, name: str, arguments: str, actor: Actor) -> ToolResult:
        result = ToolResult(tool=name, arguments=arguments)
        tool = self.tools.get(name)
        if tool is None:
            result.error = ToolError(code="unknown_tool", message=f"No tool {name}.")
            return result
        try:
            query = tool.query_type.model_validate_json(arguments)
            result.result = tool.handler(actor, query)
        except ValidationError as error:
            result.error = ToolError(code="invalid", message=str(error))
        except AppError as error:
            result.error = ToolError(code=error.code, message=error.message)
        return result


def build_tools(
    reads: ReadService,
    schedules: ScheduleService,
    constraints: ConstraintService,
    navigation: NavigationService,
) -> list[Tool]:
    return [
        Tool(reads.get_reference, "Department, skills and shift groups."),
        Tool(reads.search_employees, "Find employees."),
        Tool(reads.search_shifts, "Find shifts."),
        Tool(reads.list_rules, "List work rules."),
        Tool(reads.search_absences, "Find absences."),
        Tool(reads.search_constraints, "Find requests and blocks."),
        Tool(schedules.get_schedule, "Assignments of a week."),
        Tool(
            schedules.check_assignment,
            "Rule engine verdict for an employee on a shift. "
            "replacing_employee_id checks a swap.",
        ),
        Tool(schedules.check_schedule, "Rule engine check of a week.", True),
        Tool(
            schedules.update_assignment,
            "Add, remove or swap an employee on a shift. "
            "Applied only if the rule engine allows it.",
            True,
        ),
        Tool(schedules.auto_schedule, "Propose an automatic schedule.", True),
        Tool(schedules.apply_proposal, "Apply an automatic schedule proposal.", True),
        Tool(schedules.publish_schedule, "Publish a draft week.", True),
        Tool(constraints.check_constraint, "Rule engine verdict for a constraint."),
        Tool(constraints.submit_constraint, "Submit a request or block."),
        Tool(constraints.review_constraint, "Approve or reject a constraint.", True),
        Tool(navigation.navigate, "Open a screen or report."),
        Tool(navigation.run_report, "Get report data."),
        Tool(ask_clarification, "Ask the user a question. Ends the turn."),
        Tool(handoff, "Pass the request to another agent. Ends the turn."),
    ]
