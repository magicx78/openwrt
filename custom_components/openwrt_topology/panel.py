"""Native Home Assistant panel + API view for OpenWrt topology."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.http.view import HomeAssistantView
from homeassistant.components.panel_custom import async_register_panel
from homeassistant.core import HomeAssistant

from .const import DOMAIN


class OpenWrtTopologySnapshotView(HomeAssistantView):
    """Return latest topology snapshot from loaded coordinators."""

    url = "/api/openwrt_topology/snapshot"
    name = "api:openwrt_topology:snapshot"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        domain_data = hass.data.get(DOMAIN, {})

        entry_id = request.query.get("entry_id")
        coordinator = None

        if entry_id:
            coordinator = domain_data.get(entry_id)
        if coordinator is None:
            for key, value in domain_data.items():
                if key.startswith("_"):
                    continue
                coordinator = value
                break

        if coordinator is None:
            return self.json_message("No loaded topology entry", status_code=404)

        data: dict[str, Any] = coordinator.data or {
            "generated_at": None,
            "nodes": [],
            "edges": [],
            "interfaces": [],
            "clients": [],
            "meta": {
                "source": "openwrt_topology",
                "schema_version": "1.0",
                "inference_used": False,
            },
        }
        return self.json(data)


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register static frontend assets, API view and sidebar panel once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("_panel_registered"):
        return

    frontend_dir = Path(__file__).resolve().parent / "frontend"
    static_url = "/openwrt_topology"

    if hasattr(hass.http, "async_register_static_paths"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(static_url, str(frontend_dir), False)]
        )
    else:
        hass.http.register_static_path(static_url, str(frontend_dir), cache_headers=False)

    hass.http.register_view(OpenWrtTopologySnapshotView())

    async_register_panel(
        hass,
        frontend_url_path="openwrt-topology",
        webcomponent_name="openwrt-topology-panel",
        sidebar_title="Network Topology",
        sidebar_icon="mdi:graph-outline",
        module_url=f"{static_url}/topology-panel.js",
        require_admin=False,
        config={"apiBase": "/api/openwrt_topology/snapshot"},
    )

    domain_data["_panel_registered"] = True
