"""Coordinator for OpenWrt topology data."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SNAPSHOT_ENDPOINT,
)


class OpenWrtTopologyCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage OpenWrt topology polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        entry: ConfigEntry,
    ) -> None:
        self.entry = entry
        self.session = session
        self.base_url: str = str(entry.data[CONF_BASE_URL]).rstrip("/")

        update_interval = timedelta(seconds=int(entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))))

        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest snapshot."""
        url = f"{self.base_url}{SNAPSHOT_ENDPOINT}"
        auth = None
        username = self.entry.data.get("username")
        password = self.entry.data.get("password")
        if username:
            auth = aiohttp.BasicAuth(str(username), str(password or ""))

        try:
            async with asyncio.timeout(20):
                response = await self.session.get(url, auth=auth, headers={"Accept": "application/json"})
                if response.status >= 400:
                    detail = await response.text()
                    raise UpdateFailed(f"HTTP {response.status}: {detail[:200]}")
                data = await response.json(content_type=None)
        except TimeoutError as err:
            raise UpdateFailed("Timeout while fetching topology snapshot") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Connection error: {err}") from err
        except ValueError as err:
            raise UpdateFailed(f"Invalid JSON response: {err}") from err

        if not isinstance(data, dict):
            raise UpdateFailed("Snapshot response is not an object")

        for field in ("generated_at", "nodes", "edges", "interfaces", "clients", "meta"):
            if field not in data:
                raise UpdateFailed(f"Missing snapshot field: {field}")

        return data
