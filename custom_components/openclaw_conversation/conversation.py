"""Conversation support for OpenClaw."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    ChatLog,
    ConversationEntity,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_AGENT_ID,
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_PROMPT,
    DEFAULT_AGENT_ID,
    DEFAULT_NAME,
    DOMAIN,
    LOGGER,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up conversation entities."""
    async_add_entities(
        [OpenClawConversationEntity(config_entry)],
    )


async def _transform_stream(
    chat_log: ChatLog,
    session: aiohttp.ClientSession,
    url: str,
    payload: dict,
    headers: dict[str, str],
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Stream OpenClaw SSE response and yield HA delta dicts.

    Yields:
        AssistantContentDeltaDict: role and content deltas that HA's
        async_add_delta_content_stream understands.
    """
    # First delta must declare the role
    yield {"role": "assistant"}

    try:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120, sock_read=60),
        ) as resp:
            if resp.status == 401:
                raise HomeAssistantError("Authentication failed with OpenClaw")
            if resp.status == 429:
                raise HomeAssistantError("Rate limited by OpenClaw")
            if resp.status >= 400:
                text = await resp.text()
                LOGGER.error("OpenClaw error %s: %s", resp.status, text)
                raise HomeAssistantError(f"OpenClaw returned error {resp.status}")

            # Parse SSE stream line by line
            buffer = ""
            async for raw_bytes in resp.content:
                buffer += raw_bytes.decode("utf-8")

                # SSE events are separated by double newlines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()

                    if not line or not line.startswith("data:"):
                        continue

                    data_str = line[5:].strip()

                    if data_str == "[DONE]":
                        return

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        LOGGER.debug("Skipping non-JSON SSE line: %s", data_str)
                        continue

                    # Extract delta content from the chat completions chunk
                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield {"content": content}

                    # Check for finish_reason
                    finish_reason = choices[0].get("finish_reason")
                    if finish_reason == "stop":
                        return

    except aiohttp.ClientError as err:
        LOGGER.error("Connection error to OpenClaw: %s", err)
        raise HomeAssistantError(f"Connection error to OpenClaw: {err}") from err


class OpenClawConversationEntity(ConversationEntity):
    """OpenClaw conversation agent."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = True

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the agent."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title or DEFAULT_NAME,
            manufacturer="OpenClaw",
            model="Gateway",
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @property
    def _base_url(self) -> str:
        """Return the base URL for the OpenClaw gateway."""
        return self.entry.data[CONF_BASE_URL].rstrip("/")

    @property
    def _agent_id(self) -> str:
        """Return the configured agent ID."""
        return self.entry.options.get(
            CONF_AGENT_ID,
            self.entry.data.get(CONF_AGENT_ID, DEFAULT_AGENT_ID),
        )

    @property
    def _headers(self) -> dict[str, str]:
        """Return the HTTP headers for the OpenClaw API."""
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "x-openclaw-agent-id": self._agent_id,
        }
        token = self.entry.data.get(CONF_API_TOKEN)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    def _build_messages(
        self,
        chat_log: ChatLog,
    ) -> list[dict]:
        """Build the messages payload from the chat log."""
        messages: list[dict] = []

        # Add system prompt if configured
        prompt = self.entry.options.get(CONF_PROMPT)
        if prompt:
            messages.append({"role": "system", "content": prompt})

        # Convert chat log content to OpenAI-compatible messages
        for content in chat_log.content:
            if isinstance(content, conversation.UserContent):
                messages.append({"role": "user", "content": content.content})
            elif isinstance(content, conversation.AssistantContent):
                if content.content:
                    messages.append({"role": "assistant", "content": content.content})
            elif isinstance(content, conversation.SystemContent):
                messages.append({"role": "system", "content": content.content})

        return messages

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Process the user input and call the OpenClaw API with streaming."""
        session = async_get_clientsession(self.hass)
        messages = self._build_messages(chat_log)

        # Use conversation_id as session key for OpenClaw
        # This allows OpenClaw to maintain its own conversation context
        user_key = user_input.conversation_id or None

        payload: dict = {
            "model": f"openclaw:{self._agent_id}",
            "messages": messages,
            "stream": True,
        }
        if user_key:
            payload["user"] = user_key

        headers = self._headers.copy()
        if user_key:
            headers["x-openclaw-session-key"] = user_key

        url = f"{self._base_url}/v1/chat/completions"

        # Use HA's delta streaming API - this is what makes the voice pipeline
        # send audio chunks to the satellite progressively as text arrives.
        # Each delta is forwarded to TTS which converts text->audio in chunks.
        try:
            delta_stream = _transform_stream(chat_log, session, url, payload, headers)

            # async_add_delta_content_stream consumes the generator,
            # builds up the AssistantContent, and returns the collected
            # content objects. HA internally forwards each delta to TTS
            # for streaming audio generation.
            async for _content in chat_log.async_add_delta_content_stream(
                user_input.agent_id, delta_stream
            ):
                pass  # HA handles the streaming internally

        except HomeAssistantError:
            raise
        except Exception as err:
            LOGGER.error("Error communicating with OpenClaw: %s", err)
            raise HomeAssistantError(
                f"Error communicating with OpenClaw: {err}"
            ) from err

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
