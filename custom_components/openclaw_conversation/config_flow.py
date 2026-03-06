"""Config flow for OpenClaw Conversation integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TemplateSelector,
)

from .const import (
    CONF_AGENT_ID,
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_PROMPT,
    DEFAULT_AGENT_ID,
    DEFAULT_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default="http://192.168.1.100:18789"): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Optional(CONF_API_TOKEN): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_AGENT_ID, default=DEFAULT_AGENT_ID): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows us to connect.

    Returns a dict with info for the entry title.
    """
    session = async_get_clientsession(hass)
    base_url = data[CONF_BASE_URL].rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    token = data.get(CONF_API_TOKEN)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Try a simple chat completions request to verify connectivity
    payload = {
        "model": f"openclaw:{data.get(CONF_AGENT_ID, DEFAULT_AGENT_ID)}",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "stream": False,
    }

    try:
        async with session.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status == 401:
                raise InvalidAuth
            if resp.status == 429:
                # Rate limited but connection works
                return {"title": DEFAULT_NAME}
            if resp.status >= 400:
                text = await resp.text()
                _LOGGER.error("OpenClaw gateway returned %s: %s", resp.status, text)
                raise CannotConnect
    except aiohttp.ClientError as err:
        _LOGGER.error("Cannot connect to OpenClaw gateway: %s", err)
        raise CannotConnect from err

    return {"title": DEFAULT_NAME}


class OpenClawConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OpenClaw Conversation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Use base_url as unique ID to prevent duplicates
            await self.async_set_unique_id(user_input[CONF_BASE_URL])
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OpenClawOptionsFlowHandler:
        """Get the options flow for this handler."""
        return OpenClawOptionsFlowHandler()


class OpenClawOptionsFlowHandler(OptionsFlow):
    """Handle options flow for OpenClaw Conversation."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Optional(CONF_PROMPT): TemplateSelector(),
                        vol.Optional(
                            CONF_AGENT_ID,
                            default=DEFAULT_AGENT_ID,
                        ): str,
                    }
                ),
                {
                    CONF_PROMPT: self.config_entry.options.get(CONF_PROMPT),
                    CONF_AGENT_ID: self.config_entry.options.get(
                        CONF_AGENT_ID,
                        self.config_entry.data.get(CONF_AGENT_ID, DEFAULT_AGENT_ID),
                    ),
                },
            ),
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
