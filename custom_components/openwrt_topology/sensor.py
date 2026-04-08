"""Sensor platform for OpenWrt topology."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_BASE_URL, DOMAIN
from .coordinator import OpenWrtTopologyCoordinator


@dataclass(frozen=True)
class TopologySensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any] = lambda _: None


SENSORS: tuple[TopologySensorDescription, ...] = (
    TopologySensorDescription(
        key="generated_at",
        name="Generated At",
        icon="mdi:clock-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("generated_at"),
    ),
    TopologySensorDescription(
        key="nodes",
        name="Node Count",
        icon="mdi:graph-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="nodes",
        value_fn=lambda data: len(data.get("nodes", [])),
    ),
    TopologySensorDescription(
        key="edges",
        name="Edge Count",
        icon="mdi:vector-line",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="edges",
        value_fn=lambda data: len(data.get("edges", [])),
    ),
    TopologySensorDescription(
        key="interfaces",
        name="Interface Count",
        icon="mdi:ethernet",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="ifaces",
        value_fn=lambda data: len(data.get("interfaces", [])),
    ),
    TopologySensorDescription(
        key="clients",
        name="Client Count",
        icon="mdi:devices",
        entity_category=EntityCategory.DIAGNOSTIC,
        native_unit_of_measurement="clients",
        value_fn=lambda data: len(data.get("clients", [])),
    ),
    TopologySensorDescription(
        key="inference_used",
        name="Inference Used",
        icon="mdi:lightbulb-question-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("meta", {}).get("inference_used"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up topology sensors from a config entry."""
    coordinator: OpenWrtTopologyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(OpenWrtTopologySensor(coordinator, entry, description) for description in SENSORS)


class OpenWrtTopologySensor(CoordinatorEntity[OpenWrtTopologyCoordinator], SensorEntity):
    """Representation of a topology sensor."""

    entity_description: TopologySensorDescription

    def __init__(
        self,
        coordinator: OpenWrtTopologyCoordinator,
        entry: ConfigEntry,
        description: TopologySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.data.get(CONF_NAME, "OpenWrt Topology"),
            "manufacturer": "OpenWrt",
            "model": "Topology Snapshot",
            "configuration_url": entry.data.get(CONF_BASE_URL),
        }

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        meta = data.get("meta", {})
        attrs: dict[str, Any] = {
            "source": meta.get("source"),
            "schema_version": meta.get("schema_version"),
            "inference_used": meta.get("inference_used"),
            "generated_at": data.get("generated_at"),
        }
        if self.entity_description.key == "generated_at":
            attrs.update(
                {
                    "node_count": len(data.get("nodes", [])),
                    "edge_count": len(data.get("edges", [])),
                    "interface_count": len(data.get("interfaces", [])),
                    "client_count": len(data.get("clients", [])),
                }
            )
        return attrs
