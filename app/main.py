from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents.model import ChatModel, Message, OpenAIModel
from app.agents.router import Failure, Result, Router
from app.config import Clock, Settings
from app.errors import AppError, NotFound, PermissionDenied
from app.queries import Navigate
from app.repository import Repository
from app.rule_engine import RuleEngine
from app.schemas import Actor, Schema
from app.services.access import Access, demo_accounts
from app.services.constraints import ConstraintService
from app.services.navigation import NavigationService
from app.services.reads import ReadService
from app.services.schedules import ScheduleService
from app.services.views import View, ViewService
from app.tools import ToolRegistry, ToolResult, build_tools

APP_DIR = Path(__file__).parent


@dataclass
class Conversation:
    owner: str
    messages: list[Message] = field(default_factory=list)


class ChatRequest(Schema):
    message: str
    session_id: UUID | None = None


class ChatResponse(Schema):
    session_id: UUID
    result: Result
    tool_calls: list[ToolResult]


class ContextResponse(Schema):
    user: Actor
    accounts: list[Actor]
    now: datetime


def build(
    repo: Repository, engine: RuleEngine, clock: Clock
) -> tuple[ToolRegistry, NavigationService, ViewService]:
    access = Access(repo)
    reads = ReadService(repo, access, clock)
    schedules = ScheduleService(repo, access, engine)
    navigation = NavigationService(repo, access, clock)
    tools = build_tools(
        reads, schedules, ConstraintService(repo, access, engine, clock), navigation
    )
    views = ViewService(repo, reads, schedules, navigation)
    return ToolRegistry(tools), navigation, views


def create_app(
    settings: Settings | None = None, model: ChatModel | None = None
) -> FastAPI:
    settings = settings or Settings()
    repo = Repository(settings.data_dir)
    clock = Clock(settings.demo_now)
    registry, navigation, views = build(repo, RuleEngine(repo), clock)
    accounts = demo_accounts(repo)
    conversations: dict[UUID, Conversation] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient() as client:
            chat_model = model or OpenAIModel(client, settings)
            app.state.router = Router(
                chat_model, registry, navigation.context, settings.max_iterations
            )
            yield

    app = FastAPI(title="Scheduling assistant", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    @app.exception_handler(AppError)
    async def app_error(request: Request, error: AppError) -> JSONResponse:
        failure = Failure(message=error.message)
        return JSONResponse({"result": failure.model_dump()}, status_code=error.status)

    def current_actor(request: Request) -> Actor:
        user_id = request.headers.get("x-demo-user", settings.demo_user_id)
        if user_id not in accounts:
            raise PermissionDenied("Unknown demo account.")
        return accounts[user_id]

    CurrentActor = Annotated[Actor, Depends(current_actor)]

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(APP_DIR / "templates" / "chat.html")

    @app.get("/api/context")
    async def context(actor: CurrentActor) -> ContextResponse:
        return ContextResponse(
            user=actor, accounts=list(accounts.values()), now=clock.now()
        )

    @app.post("/api/view")
    async def view(query: Navigate, actor: CurrentActor) -> View:
        return views.open(actor, query)

    @app.post("/api/chat", response_model_exclude_none=True)
    async def chat(
        payload: ChatRequest, request: Request, actor: CurrentActor
    ) -> ChatResponse:
        session_id = payload.session_id or uuid4()
        if payload.session_id is None:
            conversations[session_id] = Conversation(owner=actor.user_id)
        conversation = conversations.get(session_id)
        if conversation is None or conversation.owner != actor.user_id:
            raise NotFound("Conversation not found.")
        result, tool_calls, turn = await request.app.state.router.run(
            actor, conversation.messages, payload.message
        )
        conversation.messages += turn
        return ChatResponse(session_id=session_id, result=result, tool_calls=tool_calls)

    return app


app = create_app()
