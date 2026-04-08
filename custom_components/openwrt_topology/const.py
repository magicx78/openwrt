"""Constants for the OpenWrt topology integration."""

from __future__ import annotations

DOMAIN = "openwrt_topology"
PLATFORMS = ["sensor"]

CONF_BASE_URL = "base_url"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_NAME = "OpenWrt Topology"
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10

SNAPSHOT_ENDPOINT = "/api/topology/snapshot"
