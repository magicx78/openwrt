"""Config flow for OpenWrt topology integration."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    SNAPSHOT_ENDPOINT,
)


class OpenWrtTopologyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle OpenWrt topology config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = str(user_input[CONF_BASE_URL]).rstrip("/")
            await self.async_set_unique_id(base_url.lower())
            self._abort_if_unique_id_configured()

            ok, reason = await self._validate_connection(user_input)
            if ok:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                    data={
                        CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                        CONF_BASE_URL: base_url,
                        CONF_USERNAME: user_input.get(CONF_USERNAME),
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD),
                        CONF_VERIFY_SSL: bool(user_input.get(CONF_VERIFY_SSL, True)),
                        CONF_SCAN_INTERVAL: max(MIN_SCAN_INTERVAL, int(user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))),
                    },
                )
            errors["base"] = reason

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_BASE_URL): str,
                vol.Optional(CONF_USERNAME): str,
                vol.Optional(CONF_PASSWORD): str,
                vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL)),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def _validate_connection(self, user_input: dict[str, Any]) -> tuple[bool, str]:
        base_url = str(user_input[CONF_BASE_URL]).rstrip("/")
        verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, True))
        session = async_get_clientsession(self.hass, verify_ssl=verify_ssl)

        auth = None
        username = user_input.get(CONF_USERNAME)
        password = user_input.get(CONF_PASSWORD)
        if username:
            auth = aiohttp.BasicAuth(str(username), str(password or ""))

        try:
            async with asyncio.timeout(15):
                response = await session.get(
                    f"{base_url}{SNAPSHOT_ENDPOINT}",
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
                if response.status >= 400:
                    return False, "cannot_connect"
                payload = await response.json(content_type=None)
                if not isinstance(payload, dict) or "nodes" not in payload:
                    return False, "invalid_response"
        except TimeoutError:
            return False, "timeout"
        except aiohttp.ClientError:
            return False, "cannot_connect"
        except ValueError:
            return False, "invalid_response"

        return True, ""
