"""The OpenClaw Conversation integration."""

from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import CONF_API_TOKEN, CONF_BASE_URL, DOMAIN, LOGGER

PLATFORMS = (Platform.CONVERSATION,)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type OpenClawConfigEntry = ConfigEntry[aiohttp.ClientSession]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up OpenClaw Conversation."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: OpenClawConfigEntry) -> bool:
    """Set up OpenClaw Conversation from a config entry."""
    session = async_get_clientsession(hass)

    # Validate connection to the OpenClaw gateway
    base_url = entry.data[CONF_BASE_URL].rstrip("/")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    token = entry.data.get(CONF_API_TOKEN)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with session.get(
            f"{base_url}/health",
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 401:
                raise ConfigEntryNotReady("Authentication failed with OpenClaw gateway")
            # Health endpoint might not exist; 404 is acceptable
            if resp.status not in (200, 404):
                raise ConfigEntryNotReady(
                    f"Cannot reach OpenClaw gateway: HTTP {resp.status}"
                )
    except aiohttp.ClientError as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to OpenClaw gateway at {base_url}"
        ) from err

    entry.runtime_data = session

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: OpenClawConfigEntry) -> bool:
    """Unload OpenClaw."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_update_options(hass: HomeAssistant, entry: OpenClawConfigEntry) -> None:
    """Update options."""
    await hass.config_entries.async_reload(entry.entry_id)
