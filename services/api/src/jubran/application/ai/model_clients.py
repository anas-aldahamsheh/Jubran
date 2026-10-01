"""Provider adapters exposing the same tool-calling turn interface.

The instructions and the tool list never change between turns or guests, and the
turn's facts (basket, orders...) are sent next to the guest's message instead of
inside the instructions. So every request starts with the same long prefix, which
both providers cache: repeated tokens cost a fraction of the price.
"""
import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any, Optional

import httpx

from jubran.application.ai.model_config_service import RuntimeModelConfig
from jubran.application.ai.tools import TOOL_DEFINITIONS
from jubran.application.ai.usage import Usage

logger = logging.getLogger(__name__)
CONTEXT_HEADER = "[Turn context from the restaurant system: authoritative facts, not the guest's words]\n"
MAX_OUTPUT_TOKENS = 4000


def context_text(context: Optional[dict]) -> Optional[str]:
    if not context:
        return None
    return CONTEXT_HEADER + json.dumps(context, ensure_ascii=False, separators=(",", ":"))


class GeminiAgentChat:
    def __init__(self, config: RuntimeModelConfig, history: list[dict], system_instruction: str):
        from google import genai
        from google.genai import types

        self._types = types
        self.usage = Usage()
        self._client = genai.Client(api_key=config.api_key)
        contents = [types.Content(role="model" if msg["role"] == "assistant" else "user",
                                  parts=[types.Part.from_text(text=msg["content"])])
                    for msg in history if msg.get("content")]
        thinking = None
        if config.thinking_level is not None:
            thinking = types.ThinkingConfig(thinking_level=config.thinking_level.upper())
        elif config.thinking_budget is not None:
            thinking = types.ThinkingConfig(thinking_budget=config.thinking_budget)
        self._chat = self._client.aio.chats.create(
            model=config.model_id,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.2,
                tools=[types.Tool(function_declarations=TOOL_DEFINITIONS)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=thinking,
            ),
            history=contents or None,
        )

    async def send_message(self, message: str | list[dict], context: Optional[dict] = None):
        if isinstance(message, list):
            message = [self._types.Part.from_function_response(name=item["name"], response=item["response"])
                       for item in message]
        elif context:
            message = [context_text(context), message]
        response = await self._chat.send_message(message)
        meta = getattr(response, "usage_metadata", None)
        if meta is not None:
            self.usage.add(input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
                           cached_input_tokens=getattr(meta, "cached_content_token_count", 0) or 0,
                           output_tokens=(getattr(meta, "candidates_token_count", 0) or 0)
                           + (getattr(meta, "thoughts_token_count", 0) or 0),
                           reasoning_tokens=getattr(meta, "thoughts_token_count", 0) or 0)
        else:
            self.usage.add()
        return response

    async def send_note(self, note: str):
        """A note from the server (not the guest), e.g. asking to fix the last reply."""
        return await self.send_message(note)

    async def close(self):
        await self._client.aio.aclose()
        self._client.close()


class OpenAIAgentChat:
    # Request fields some models refuse; dropped (and remembered) when OpenAI says so.
    OPTIONAL_FIELDS = ("include", "prompt_cache_key")

    def __init__(self, config: RuntimeModelConfig, history: list[dict], system_instruction: str):
        self._config = config
        self._instruction = system_instruction
        self.usage = Usage()
        self._client = httpx.AsyncClient(timeout=90.0)
        self._dropped: set[str] = set()
        self._input: list[dict[str, Any]] = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in history if msg.get("content")
        ]
        self._tools = [
            {"type": "function", "name": tool["name"], "description": tool["description"],
             "parameters": tool["parameters"], "strict": False}
            for tool in TOOL_DEFINITIONS
        ]

    def _payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model_id,
            "instructions": self._instruction,
            "input": self._input,
            "tools": self._tools,
            "tool_choice": "auto",
            "store": False,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            # Nothing is stored at OpenAI, so reasoning between tool calls travels encrypted with the request.
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": "jubran-assistant",
        }
        reasoning = {}
        if self._config.reasoning_effort:
            reasoning["effort"] = self._config.reasoning_effort
        if self._config.reasoning_mode and self._config.reasoning_mode != "standard":
            reasoning["mode"] = self._config.reasoning_mode
        if reasoning:
            payload["reasoning"] = reasoning
        for name in self._dropped:
            payload.pop(name, None)
        return payload

    # A brief provider hiccup (overloaded, a dropped connection) is retried instead of failing the guest's turn.
    RETRY_DELAYS = (0.5, 1.5)

    @staticmethod
    def _transient(response: httpx.Response) -> bool:
        if response.status_code in (500, 502, 503, 504):
            return True
        return response.status_code == 429 and "insufficient_quota" not in response.text

    async def _post(self) -> dict[str, Any]:
        retries = 0
        for _ in range(len(self.OPTIONAL_FIELDS) + len(self.RETRY_DELAYS) + 1):
            try:
                response = await self._client.post("https://api.openai.com/v1/responses", json=self._payload(),
                                                   headers={"Authorization": f"Bearer {self._config.api_key}"})
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                if retries >= len(self.RETRY_DELAYS):
                    raise
                await asyncio.sleep(self.RETRY_DELAYS[retries])
                retries += 1
                continue
            if self._transient(response) and retries < len(self.RETRY_DELAYS):
                logger.warning("OpenAI answered %s; retrying", response.status_code)
                await asyncio.sleep(self.RETRY_DELAYS[retries])
                retries += 1
                continue
            if response.status_code == 400:
                try:
                    error = response.json().get("error") or {}
                except ValueError:
                    error = {}
                text = f"{error.get('param') or ''} {error.get('message') or ''}"
                refused = next((name for name in self.OPTIONAL_FIELDS
                                if name not in self._dropped and name in text), None)
                if refused:
                    logger.info("OpenAI model %s does not accept %s; continuing without it",
                                self._config.model_id, refused)
                    self._dropped.add(refused)
                    if refused == "include":  # reasoning items without their content cannot be sent back
                        self._input = [item for item in self._input if item.get("type") != "reasoning"]
                    continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()
        return response.json()

    async def send_message(self, message: str | list[dict], context: Optional[dict] = None):
        if isinstance(message, str):
            if context:
                self._input.append({"role": "developer", "content": context_text(context)})
            self._input.append({"role": "user", "content": message})
        else:
            self._input.extend({"type": "function_call_output", "call_id": item["call_id"],
                                "output": json.dumps(item["response"], ensure_ascii=False, separators=(",", ":"))}
                               for item in message)
        return await self._exchange()

    async def send_note(self, note: str):
        """A note from the server (not the guest), e.g. asking to fix the last reply."""
        self._input.append({"role": "developer", "content": note})
        return await self._exchange()

    async def _exchange(self):
        data = await self._post()
        usage = data.get("usage") or {}
        self.usage.add(input_tokens=usage.get("input_tokens", 0),
                       cached_input_tokens=(usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
                       output_tokens=usage.get("output_tokens", 0),
                       reasoning_tokens=(usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0))
        output = data.get("output") or []
        self._input.extend(item for item in output
                           if not (item.get("type") == "reasoning" and "include" in self._dropped))
        calls = []
        texts = []
        for item in output:
            if item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append(SimpleNamespace(name=item["name"], args=args, call_id=item["call_id"]))
            elif item.get("type") == "message":
                texts.extend(part.get("text", "") for part in item.get("content", [])
                             if part.get("type") == "output_text")
        return SimpleNamespace(function_calls=calls, text="\n".join(filter(None, texts)))

    async def close(self):
        await self._client.aclose()
