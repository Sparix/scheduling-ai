from collections.abc import Callable
from typing import Literal

from app.agents.model import ChatModel, Message
from app.agents.prompts import ROUTER_PROMPT
from app.queries import Clarification, Handoff
from app.schemas import Actor, Schema
from app.services.navigation import Context, NavigationTarget
from app.services.schedules import ActionOutcome
from app.tools import ToolRegistry, ToolResult

Step = NavigationTarget | ActionOutcome | Handoff


class Answer(Schema):
    type: Literal["answer"] = "answer"
    text: str
    steps: list[Step] = []


class Question(Schema):
    type: Literal["question"] = "question"
    text: str
    options: list[str] = []
    steps: list[Step] = []


class Failure(Schema):
    type: Literal["error"] = "error"
    message: str


Result = Answer | Question | Failure


def steps(results: list[ToolResult]) -> list[Step]:
    return [
        r.result
        for r in results
        if isinstance(r.result, NavigationTarget | ActionOutcome | Handoff)
    ]


class Router:
    def __init__(
        self,
        model: ChatModel,
        registry: ToolRegistry,
        context: Callable[[Actor], Context],
        max_iterations: int,
    ):
        self.model = model
        self.registry = registry
        self.context = context
        self.max_iterations = max_iterations

    async def run(
        self, actor: Actor, history: list[Message], text: str
    ) -> tuple[Result, list[ToolResult], list[Message]]:
        prompt = [
            Message(role="system", content=ROUTER_PROMPT),
            Message(role="system", content=self.context(actor).model_dump_json()),
        ]
        turn = [Message(role="user", content=text)]
        tools = self.registry.visible(actor)
        results: list[ToolResult] = []
        for iteration in range(self.max_iterations):
            last = iteration == self.max_iterations - 1
            reply = await self.model.complete(
                prompt + history + turn, tools, "none" if last else "auto"
            )
            turn.append(reply)
            if not reply.tool_calls:
                answer = Answer(text=reply.content or "", steps=steps(results))
                return answer, results, turn
            for call in reply.tool_calls:
                result = self.registry.execute(
                    call.function.name, call.function.arguments, actor
                )
                results.append(result)
                turn.append(
                    Message(
                        role="tool", tool_call_id=call.id, content=result.for_model()
                    )
                )
            for result in results:
                if isinstance(result.result, Clarification):
                    question = result.result
                    turn.append(Message(role="assistant", content=question.question))
                    asked = Question(
                        text=question.question,
                        options=question.options,
                        steps=steps(results),
                    )
                    return asked, results, turn
                if isinstance(result.result, Handoff):
                    text = f"Handed over to the {result.result.agent} agent."
                    turn.append(Message(role="assistant", content=text))
                    return Answer(text=text, steps=steps(results)), results, turn
        return (
            Failure(message="Too many steps. Please narrow the request."),
            results,
            turn,
        )
