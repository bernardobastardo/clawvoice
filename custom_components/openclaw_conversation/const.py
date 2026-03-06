"""Constants for the OpenClaw Conversation integration."""

import logging

DOMAIN = "openclaw_conversation"
LOGGER: logging.Logger = logging.getLogger(__package__)

CONF_BASE_URL = "base_url"
CONF_API_TOKEN = "api_token"
CONF_AGENT_ID = "agent_id"
CONF_SESSION_KEY = "session_key"
CONF_PROMPT = "prompt"

DEFAULT_AGENT_ID = "main"
DEFAULT_NAME = "OpenClaw Conversation"
DEFAULT_PORT = 18789
