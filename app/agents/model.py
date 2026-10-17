from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.errors import ModelError
from app.tools import Tool


class FunctionCall(BaseModel):
    name: str
    arguments: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


ToolChoice = Literal["auto", "none"]


class ChatModel(Protocol):
    async def complete(
        self, messages: list[Message], tools: list[Tool], tool_choice: ToolChoice
    ) -> Message: ...


class OpenAIModel:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client = client
        self.settings = settings

    async def complete(
        self, messages: list[Message], tools: list[Tool], tool_choice: ToolChoice
    ) -> Message:
        if not self.settings.model_name:
            raise ModelError("Set MODEL_NAME")
        payload = {
            "model": self.settings.model_name,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "tools": [tool.definition() for tool in tools],
            "tool_choice": tool_choice,
        }
        if self.settings.reasoning_effort:
            payload["reasoning_effort"] = self.settings.reasoning_effort
        token = self.settings.openai_api_key.get_secret_value()
        try:
            response = await self.client.post(
                f"{self.settings.openai_base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.settings.model_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ModelError(f"Model API request failed: {error}") from None
        return Message.model_validate(response.json()["choices"][0]["message"])
