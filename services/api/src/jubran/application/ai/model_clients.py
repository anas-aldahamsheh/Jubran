"""Provider adapters exposing the same tool-calling turn interface.

The instructions and the tool list never change between turns or guests, and the
turn's facts (basket, orders...) are sent next to the guest's message instead of
inside the instructions. So every request starts with the same long prefix, which
every provider caches: repeated tokens cost a fraction of the price.

Calls to OpenAI and Anthropic share one pool of open connections (per event loop), so a guest's
message doesn't wait for a new connection to be set up.
"""
import asyncio
import json
import logging
import weakref
from types import SimpleNamespace
from typing import Any, Callable, Optional

import httpx

from jubran.application.ai.model_config_service import RuntimeModelConfig
from jubran.application.ai.tools import TOOL_DEFINITIONS
from jubran.application.ai.usage import Usage

logger = logging.getLogger(__name__)
CONTEXT_HEADER = "[Turn context from the restaurant system: authoritative facts, not the guest's words]\n"
MAX_OUTPUT_TOKENS = 4000
# Receives the reply's text while the model writes it (streaming); None means "start over"
# (a retried request writes the reply again from the beginning).
TextStream = Callable[[Optional[str]], None]


# An idle connection stays open this long, so the next message (often a few seconds later) reuses it.
KEEP_ALIVE_SECONDS = 60.0
_SHARED_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = \
    weakref.WeakKeyDictionary()


def shared_http_client() -> httpx.AsyncClient:
    """The connection pool to the model provider for the running event loop (made again if closed)."""
    loop = asyncio.get_running_loop()
    client = _SHARED_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=90.0, limits=httpx.Limits(max_connections=100,
                                                                     max_keepalive_connections=20,
                                                                     keepalive_expiry=KEEP_ALIVE_SECONDS))
        _SHARED_CLIENTS[loop] = client
    return client


async def close_shared_http_client() -> None:
    """At shutdown: close the running event loop's connections to the model provider."""
    client = _SHARED_CLIENTS.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()


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

    async def send_message(self, message: str | list[dict], context: Optional[dict] = None,
                           on_text: Optional[TextStream] = None):
        if isinstance(message, list):
            message = [self._types.Part.from_function_response(name=item["name"], response=item["response"])
                       for item in message]
        elif context:
            message = [context_text(context), message]
        if on_text is None:
            response = await self._chat.send_message(message)
        else:
            response = await self._streamed(message, on_text)
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

    async def _streamed(self, message, on_text: TextStream):
        """The same answer as send_message, with its text passed on while it is written."""
        calls, texts, meta = [], [], None
        async for chunk in await self._chat.send_message_stream(message):
            meta = getattr(chunk, "usage_metadata", None) or meta
            candidates = getattr(chunk, "candidates", None) or []
            content = getattr(candidates[0], "content", None) if candidates else None
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "function_call", None) is not None:
                    calls.append(part.function_call)
                elif getattr(part, "text", None) and not getattr(part, "thought", False):
                    texts.append(part.text)
                    on_text(part.text)
        return SimpleNamespace(function_calls=calls, text="".join(texts), usage_metadata=meta)

    async def send_note(self, note: str):
        """A note from the server (not the guest), e.g. asking to fix the last reply."""
        return await self.send_message(note)

    async def close(self):
        await self._client.aio.aclose()
        self._client.close()


class OpenAIAgentChat:
    # Request fields some models refuse; dropped (and remembered) when OpenAI says so.
    OPTIONAL_FIELDS = ("include", "prompt_cache_key")

    def __init__(self, config: RuntimeModelConfig, history: list[dict], system_instruction: str,
                 client: Optional[httpx.AsyncClient] = None):
        self._config = config
        self._instruction = system_instruction
        self.usage = Usage()
        # A client given here belongs to this chat (tests); otherwise the shared pool is used.
        self._own_client = client
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

    @property
    def _client(self) -> httpx.AsyncClient:
        return self._own_client or shared_http_client()

    async def _request(self, on_text: Optional[TextStream]) -> tuple[httpx.Response, Optional[dict[str, Any]]]:
        """One attempt: the HTTP response, and the finished model response when it succeeded."""
        url, headers = "https://api.openai.com/v1/responses", {"Authorization": f"Bearer {self._config.api_key}"}
        if on_text is None:
            response = await self._client.post(url, json=self._payload(), headers=headers)
            return response, (response.json() if response.is_success else None)
        async with self._client.stream("POST", url, json={**self._payload(), "stream": True},
                                       headers=headers) as response:
            if not response.is_success:
                await response.aread()
                return response, None
            on_text(None)
            return response, await self._read_events(response, on_text)

    @staticmethod
    async def _read_events(response: httpx.Response, on_text: TextStream) -> dict[str, Any]:
        """Server-sent events of a streamed response: text as it comes, then the finished response."""
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "response.output_text.delta" and event.get("delta"):
                on_text(event["delta"])
            elif kind in ("response.completed", "response.incomplete"):
                return event.get("response") or {}
            elif kind in ("response.failed", "error"):
                error = (event.get("response") or {}).get("error") or event.get("error") or event
                raise httpx.HTTPStatusError(f"OpenAI stream failed: {error.get('message') or kind}",
                                            request=response.request, response=response)
        raise httpx.RemoteProtocolError("OpenAI stream ended before the response was complete",
                                        request=response.request)

    async def _post(self, on_text: Optional[TextStream] = None) -> dict[str, Any]:
        retries = 0
        for _ in range(len(self.OPTIONAL_FIELDS) + len(self.RETRY_DELAYS) + 1):
            try:
                response, data = await self._request(on_text)
                if data is not None:
                    return data
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

    async def send_message(self, message: str | list[dict], context: Optional[dict] = None,
                           on_text: Optional[TextStream] = None):
        if isinstance(message, str):
            if context:
                self._input.append({"role": "developer", "content": context_text(context)})
            self._input.append({"role": "user", "content": message})
        else:
            self._input.extend({"type": "function_call_output", "call_id": item["call_id"],
                                "output": json.dumps(item["response"], ensure_ascii=False, separators=(",", ":"))}
                               for item in message)
        return await self._exchange(on_text)

    async def send_note(self, note: str):
        """A note from the server (not the guest), e.g. asking to fix the last reply."""
        self._input.append({"role": "developer", "content": note})
        return await self._exchange()

    async def _exchange(self, on_text: Optional[TextStream] = None):
        data = await self._post(on_text)
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
        # The shared pool stays open for the next messages.
        if self._own_client is not None:
            await self._own_client.aclose()


class AnthropicAgentChat:
    """Claude through Anthropic's Messages API. The tools and the instructions are marked for
    caching, and so is the conversation so far, so each tool round re-reads it at the cached price."""
    URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    # Request fields some models refuse; dropped (and remembered) when Anthropic says so.
    OPTIONAL_FIELDS = ("temperature", "output_config")
    # A brief provider hiccup (overloaded, a dropped connection) is retried instead of failing the guest's turn.
    RETRY_DELAYS = (0.5, 1.5)
    # Errors in the middle of a stream that a new attempt usually gets past.
    TRANSIENT_STREAM_ERRORS = ("overloaded_error", "api_error", "rate_limit_error")

    def __init__(self, config: RuntimeModelConfig, history: list[dict], system_instruction: str,
                 client: Optional[httpx.AsyncClient] = None):
        self._config = config
        self.usage = Usage()
        # A client given here belongs to this chat (tests); otherwise the shared pool is used.
        self._own_client = client
        self._dropped: set[str] = set()
        self._system = [{"type": "text", "text": system_instruction, "cache_control": {"type": "ephemeral"}}]
        self._tools = [{"name": tool["name"], "description": tool["description"], "input_schema": tool["parameters"]}
                       for tool in TOOL_DEFINITIONS]
        self._messages: list[dict[str, Any]] = []
        for msg in history:
            text = (msg.get("content") or "").strip()
            # The conversation opens with the guest: an earlier greeting from the assistant is left out.
            if text and (self._messages or msg["role"] == "user"):
                self._add("assistant" if msg["role"] == "assistant" else "user", [{"type": "text", "text": text}])

    def _add(self, role: str, blocks: list[dict[str, Any]]) -> None:
        """Turns alternate between the guest and Claude: blocks of the same side join one turn."""
        if self._messages and self._messages[-1]["role"] == role:
            self._messages[-1] = {"role": role, "content": [*self._messages[-1]["content"], *blocks]}
        else:
            self._messages.append({"role": role, "content": blocks})

    def _payload(self) -> dict[str, Any]:
        messages = list(self._messages)
        if messages:
            # The cache mark goes on the newest block (a copy: the stored conversation stays unmarked).
            last = messages[-1]
            messages[-1] = {**last, "content": [*last["content"][:-1],
                                                {**last["content"][-1], "cache_control": {"type": "ephemeral"}}]}
        payload: dict[str, Any] = {
            "model": self._config.model_id,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": self._system,
            "tools": self._tools,
            "messages": messages,
            "temperature": 0.2,
        }
        if self._config.reasoning_effort:
            # How hard Claude thinks before answering (the admin's thinking level).
            payload["output_config"] = {"effort": self._config.reasoning_effort}
        for name in self._dropped:
            payload.pop(name, None)
        return payload

    @staticmethod
    def _transient(response: httpx.Response) -> bool:
        return response.status_code in (429, 500, 502, 503, 504, 529)

    @property
    def _client(self) -> httpx.AsyncClient:
        return self._own_client or shared_http_client()

    async def _request(self, on_text: Optional[TextStream]) -> tuple[httpx.Response, Optional[dict[str, Any]]]:
        """One attempt: the HTTP response, and the finished message when it succeeded."""
        headers = {"x-api-key": self._config.api_key, "anthropic-version": self.API_VERSION}
        if on_text is None:
            response = await self._client.post(self.URL, json=self._payload(), headers=headers)
            return response, (response.json() if response.is_success else None)
        async with self._client.stream("POST", self.URL, json={**self._payload(), "stream": True},
                                       headers=headers) as response:
            if not response.is_success:
                await response.aread()
                return response, None
            on_text(None)
            return response, await self._read_events(response, on_text)

    @classmethod
    async def _read_events(cls, response: httpx.Response, on_text: TextStream) -> dict[str, Any]:
        """Server-sent events of a streamed message: text as it comes, then the whole message."""
        message: dict[str, Any] = {"content": [], "usage": {}}
        partial_inputs: dict[int, str] = {}
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "message_start":
                message["usage"].update((event.get("message") or {}).get("usage") or {})
            elif kind == "content_block_start":
                index = event.get("index", len(message["content"]))
                while len(message["content"]) <= index:
                    message["content"].append({})
                message["content"][index] = dict(event.get("content_block") or {})
            elif kind == "content_block_delta":
                index, delta = event.get("index", 0), event.get("delta") or {}
                if index >= len(message["content"]):
                    continue
                block = message["content"][index]
                if delta.get("type") == "text_delta":
                    block["text"] = block.get("text", "") + delta.get("text", "")
                    if delta.get("text"):
                        on_text(delta["text"])
                elif delta.get("type") == "input_json_delta":
                    partial_inputs[index] = partial_inputs.get(index, "") + delta.get("partial_json", "")
                elif delta.get("type") == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                elif delta.get("type") == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta.get("signature", "")
            elif kind == "message_delta":
                message["usage"].update((event.get("usage") or {}))
                message["stop_reason"] = (event.get("delta") or {}).get("stop_reason")
            elif kind == "message_stop":
                for index, text in partial_inputs.items():
                    try:
                        message["content"][index]["input"] = json.loads(text) if text.strip() else {}
                    except ValueError:
                        message["content"][index]["input"] = {}
                return message
            elif kind == "error":
                error = event.get("error") or {}
                if error.get("type") in cls.TRANSIENT_STREAM_ERRORS:
                    raise httpx.RemoteProtocolError(f"Anthropic stream failed: {error.get('type')}",
                                                    request=response.request)
                raise httpx.HTTPStatusError(f"Anthropic stream failed: {error.get('message') or error.get('type')}",
                                            request=response.request, response=response)
        raise httpx.RemoteProtocolError("Anthropic stream ended before the message was complete",
                                        request=response.request)

    async def _post(self, on_text: Optional[TextStream] = None) -> dict[str, Any]:
        retries = 0
        for _ in range(len(self.OPTIONAL_FIELDS) + len(self.RETRY_DELAYS) + 1):
            try:
                response, data = await self._request(on_text)
                if data is not None:
                    return data
            except (httpx.ConnectError, httpx.RemoteProtocolError):
                if retries >= len(self.RETRY_DELAYS):
                    raise
                await asyncio.sleep(self.RETRY_DELAYS[retries])
                retries += 1
                continue
            if self._transient(response) and retries < len(self.RETRY_DELAYS):
                logger.warning("Anthropic answered %s; retrying", response.status_code)
                await asyncio.sleep(self.RETRY_DELAYS[retries])
                retries += 1
                continue
            if response.status_code == 400:
                try:
                    message = (response.json().get("error") or {}).get("message") or ""
                except ValueError:
                    message = ""
                refused = next((name for name in self.OPTIONAL_FIELDS if name not in self._dropped
                                and (name in message or (name == "output_config" and "effort" in message))), None)
                if refused:
                    logger.info("Anthropic model %s does not accept %s; continuing without it",
                                self._config.model_id, refused)
                    self._dropped.add(refused)
                    continue
            response.raise_for_status()
            return response.json()
        response.raise_for_status()
        return response.json()

    async def send_message(self, message: str | list[dict], context: Optional[dict] = None,
                           on_text: Optional[TextStream] = None):
        if isinstance(message, str):
            # The turn's facts go first, in their own block, ahead of the guest's words.
            blocks = [{"type": "text", "text": context_text(context)}] if context else []
            self._add("user", [*blocks, {"type": "text", "text": message}])
        else:
            self._add("user", [{"type": "tool_result", "tool_use_id": item["call_id"],
                                "content": json.dumps(item["response"], ensure_ascii=False, separators=(",", ":"))}
                               for item in message])
        return await self._exchange(on_text)

    async def send_note(self, note: str):
        """A note from the server (not the guest), e.g. asking to fix the last reply."""
        self._add("user", [{"type": "text", "text": note}])
        return await self._exchange()

    async def _exchange(self, on_text: Optional[TextStream] = None):
        data = await self._post(on_text)
        usage = data.get("usage") or {}
        cached = usage.get("cache_read_input_tokens") or 0
        self.usage.add(input_tokens=(usage.get("input_tokens") or 0) + cached
                       + (usage.get("cache_creation_input_tokens") or 0),
                       cached_input_tokens=cached, output_tokens=usage.get("output_tokens") or 0)
        # Claude's turn goes back as it came (an empty text block is refused when sent back).
        content = [block for block in data.get("content") or []
                   if block.get("type") and not (block["type"] == "text" and not block.get("text"))]
        if content:
            self._add("assistant", content)
        calls = [SimpleNamespace(name=block["name"], args=block.get("input") or {}, call_id=block["id"])
                 for block in content if block["type"] == "tool_use"]
        text = "".join(block["text"] for block in content if block["type"] == "text")
        return SimpleNamespace(function_calls=calls, text=text)

    async def close(self):
        # The shared pool stays open for the next messages.
        if self._own_client is not None:
            await self._own_client.aclose()


def agent_chat(config: RuntimeModelConfig, history: list[dict], system_instruction: str):
    """The chat client for the provider a section uses."""
    chat_class = {"openai": OpenAIAgentChat, "anthropic": AnthropicAgentChat}.get(config.provider, GeminiAgentChat)
    return chat_class(config, history, system_instruction)
