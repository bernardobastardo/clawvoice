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
    resp: aiohttp.ClientResponse,
) -> AsyncGenerator[conversation.AssistantContentDeltaDict]:
    """Parse SSE from an already-open response and yield HA delta dicts.

    The HTTP connection is already established before this generator starts,
    so we only pay the parsing cost here — zero network setup overhead.
    """
    # First delta must declare the role
    yield {"role": "assistant"}

    # Read raw bytes and parse SSE inline — avoids buffering full lines
    buffer = ""
    async for chunk in resp.content.iter_any():
        buffer += chunk.decode("utf-8")

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
                continue

            choices = data.get("choices")
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta")
            if delta:
                content = delta.get("content")
                if content:
                    yield {"content": content}

            if choice.get("finish_reason") == "stop":
                return


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
        # Cache auth headers (token doesn't change). Agent ID is added per-request
        # since it can be changed via options flow without reload.
        self._auth_headers: dict[str, str] = {"Content-Type": "application/json"}
        token = entry.data.get(CONF_API_TOKEN)
        if token:
            self._auth_headers["Authorization"] = f"Bearer {token}"

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
        """Return HTTP headers with the current agent ID."""
        return {
            **self._auth_headers,
            "x-openclaw-agent-id": self._agent_id,
        }

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Process the user input and call the OpenClaw API with streaming."""
        session = async_get_clientsession(self.hass)

        # Only send the latest user message. OpenClaw maintains conversation
        # context via native sessions, linked by the `user` field in the payload.
        # IMPORTANT: do NOT send the x-openclaw-session-key header — it breaks
        # agent routing for non-default agents. The `user` field alone is enough
        # for OpenClaw to derive a stable session key.
        agent_id = self._agent_id
        messages: list[dict] = []

        prompt = self.entry.options.get(CONF_PROMPT)
        if prompt:
            messages.append({"role": "system", "content": prompt})

        messages.append({"role": "user", "content": user_input.text})

        # Use conversation_id as the user field for session continuity.
        # Prefix with agent_id so different agents don't share sessions.
        conversation_id = user_input.conversation_id
        user_key = f"ha:{agent_id}:{conversation_id}" if conversation_id else None

        payload: dict = {
            "model": f"openclaw:{agent_id}",
            "messages": messages,
            "stream": True,
        }
        if user_key:
            payload["user"] = user_key

        headers = self._headers.copy()

        url = f"{self._base_url}/v1/chat/completions"

        LOGGER.debug(
            "Sending to OpenClaw: url=%s agent_id=%s model=%s user=%s",
            url,
            headers.get("x-openclaw-agent-id"),
            payload["model"],
            user_key,
        )

        # Open the HTTP connection and validate status BEFORE entering
        # the streaming generator. This separates connection errors from
        # parsing and lets us fail fast on auth/server issues.
        try:
            resp = await session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=120,
                    sock_connect=5,  # fail fast if gateway unreachable
                    sock_read=60,
                ),
            )
        except aiohttp.ClientError as err:
            LOGGER.error("Connection error to OpenClaw: %s", err)
            raise HomeAssistantError(f"Connection error to OpenClaw: {err}") from err

        try:
            if resp.status == 401:
                raise HomeAssistantError("Authentication failed with OpenClaw")
            if resp.status == 429:
                raise HomeAssistantError("Rate limited by OpenClaw")
            if resp.status >= 400:
                text = await resp.text()
                LOGGER.error("OpenClaw error %s: %s", resp.status, text)
                raise HomeAssistantError(f"OpenClaw returned error {resp.status}")

            # Stream deltas into HA's pipeline — this is what makes TTS
            # generate audio progressively as text arrives from OpenClaw.
            async for _content in chat_log.async_add_delta_content_stream(
                user_input.agent_id, _transform_stream(resp)
            ):
                pass

        except HomeAssistantError:
            raise
        except Exception as err:
            LOGGER.error("Error communicating with OpenClaw: %s", err)
            raise HomeAssistantError(
                f"Error communicating with OpenClaw: {err}"
            ) from err
        finally:
            resp.release()

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
