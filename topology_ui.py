"""
topology_ui.py — HTML/JS content for /ui/topology (v0.6.2+)

Extracted from server.py without any change.
Used by: server.py ui_topology() → _page(TOPOLOGY_UI_CONTENT, ...)
"""
from __future__ import annotations

TOPOLOGY_UI_CONTENT = r"""
<style>
html, body { margin: 0; padding: 0; height: 100%; background: #111; color: #eee; font-family: system-ui, -apple-system, sans-serif; overflow: hidden; }
#app { display: grid; grid-template-columns: 1fr 320px; grid-template-rows: auto 1fr; grid-template-areas: "header header" "graph sidebar"; height: 100%; }
header { grid-area: header; padding: 8px 12px; background: #181818; border-bottom: 1px solid #333; display: flex; align-items: center; justify-content: space-between; }
header .title { font-size: 14px; font-weight: 600; }
header .controls { display: flex; gap: 8px; align-items: center; font-size: 12px; flex-wrap: wrap; }
header select, header button { background: #222; color: #eee; border: 1px solid #444; padding: 3px 6px; border-radius: 3px; font-size: 12px; cursor: pointer; }
header label { color: #bbb; }
#graph { grid-area: graph; position: relative; }
#mynetwork { width: 100%; height: 100%; background: #111; }
#sidebar { grid-area: sidebar; border-left: 1px solid #333; background: #141414; padding: 8px; font-size: 12px; overflow-y: auto; }
#sidebar h2 { font-size: 13px; margin: 0 0 8px; padding-bottom: 4px; border-bottom: 1px solid #333; }
#sidebar .kv { margin-bottom: 4px; }
#sidebar .kv span.key { display: inline-block; width: 110px; color: #999; }
.legend { margin-top: 12px; border-top: 1px solid #333; padding-top: 8px; }
.legend-item { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.legend-dot { width: 10px; height: 10px; border-radius: 50%; }
.dot-router { background: #4fc3f7; }
.dot-ap { background: #81c784; }
.dot-interface { background: #ffd166; }
.dot-ssid { background: #c084fc; }
.dot-client { background: #e0e0e0; }
</style>

<div id="app">
  <header>
    <div class="title">Network Topology</div>
    <div class="controls">
      <label for="interfaceTypeFilter">Interface:</label>
      <select id="interfaceTypeFilter">
        <option value="all">all</option>
        <option value="wifi">wifi</option>
        <option value="lan">lan</option>
        <option value="uplink">uplink</option>
        <option value="unknown">unknown</option>
      </select>
      <label><input type="checkbox" id="clientsOnlyFilter"> clients only</label>
      <label><input type="checkbox" id="showInactiveFilter" checked> show inactive</label>
      <button id="refreshBtn">refresh</button>
      <span id="statusText">Status: initialisiere...</span>
    </div>
  </header>
  <div id="graph"><div id="mynetwork"></div></div>
  <aside id="sidebar">
    <h2>Details</h2>
    <div id="detailsContent">Knoten auswaehlen, um Details zu sehen.</div>
    <div class="legend">
      <strong>Legend</strong>
      <div class="legend-item"><span class="legend-dot dot-router"></span> Router</div>
      <div class="legend-item"><span class="legend-dot dot-ap"></span> AP</div>
      <div class="legend-item"><span class="legend-dot dot-interface"></span> Interface</div>
      <div class="legend-item"><span class="legend-dot dot-ssid"></span> SSID</div>
      <div class="legend-item"><span class="legend-dot dot-client"></span> Client</div>
    </div>
  </aside>
</div>

<script>
const TopologyApi = {
  SNAPSHOT_URL: "/api/topology/snapshot?include_wifi=1",
  async getTopologySnapshot() {
    const res = await fetch(this.SNAPSHOT_URL, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.json();
  }
};

const DomainMapper = {
  mapSnapshot(payload) {
    const interfaces = Array.isArray(payload.interfaces) ? payload.interfaces : [];
    const clients = Array.isArray(payload.clients) ? payload.clients : [];
    const nodes = Array.isArray(payload.nodes) ? payload.nodes : [];
    const edges = Array.isArray(payload.edges) ? payload.edges : [];
    const nodeById = {};
    for (const n of nodes) {
      const attrs = n.attributes || {};
      nodeById[n.id] = {
        ...n,
        attributes: attrs,
        signal_display: this.metricOrUnknown(attrs.signal, "dBm"),
        bitrate_display: this.metricOrUnknown(attrs.bitrate, "Mbit/s"),
        rx_display: this.metricOrUnknown(attrs.rx_bytes, "bytes"),
        tx_display: this.metricOrUnknown(attrs.tx_bytes, "bytes"),
      };
    }
    const interfaceById = {};
    for (const iface of interfaces) {
      interfaceById[iface.id] = iface;
      if (!nodeById[iface.id]) {
        nodeById[iface.id] = {
          id: iface.id,
          type: "interface",
          label: iface.name || iface.id,
          status: iface.status,
          valid: iface.valid,
          source: iface.source,
          inferred: iface.inferred,
          inference_reason: iface.inference_reason,
          attributes: {
            ap_mac: iface.ap_mac,
            interface_type: iface.interface_type,
            rx_bytes: iface.rx_bytes,
            tx_bytes: iface.tx_bytes,
            warning: iface.warning,
          },
          rx_display: this.metricOrUnknown(iface.rx_bytes, "bytes"),
          tx_display: this.metricOrUnknown(iface.tx_bytes, "bytes"),
        };
      }
    }
    const clientById = {};
    for (const c of clients) {
      clientById[c.id] = c;
      if (!nodeById[c.id]) {
        nodeById[c.id] = {
          id: c.id,
          type: "client",
          label: c.mac || c.id,
          status: c.connected === false ? "inactive" : "active",
          source: c.source,
          inferred: c.inferred,
          inference_reason: c.inference_reason,
          attributes: {
            ap_mac: c.ap_mac,
            connected: c.connected,
            last_seen: c.last_seen,
            signal: c.signal,
            bitrate: c.bitrate,
          },
          signal_display: this.metricOrUnknown(c.signal, "dBm"),
          bitrate_display: this.metricOrUnknown(c.bitrate, "Mbit/s"),
        };
      }
    }

    return {
      generated_at: payload.generated_at,
      meta: payload.meta || {},
      interfaces,
      clients,
      nodes: Object.values(nodeById),
      edges: edges,
      nodeById: nodeById,
      interfaceById,
      clientById,
    };
  },
  metricOrUnknown(value, unit) {
    if (value === null || value === undefined) return "?";
    return unit ? `${value} ${unit}` : String(value);
  }
};

const NodeRenderer = {
  toVisNode(node) {
    const style = this.style(node);
    return {
      id: node.id,
      label: node.label || node.id,
      ...style,
      _type: node.type,
    };
  },
  style(node) {
    let colorBg = "#888", shape = "dot", size = 14;
    if (node.type === "router") { colorBg = "#4fc3f7"; shape = "ellipse"; size = 24; }
    else if (node.type === "ap") { colorBg = "#81c784"; shape = "dot"; size = 20; }
    else if (node.type === "interface") { colorBg = "#ffd166"; shape = "box"; size = 14; }
    else if (node.type === "ssid") { colorBg = "#c084fc"; shape = "diamond"; size = 14; }
    else if (node.type === "client") { colorBg = "#e0e0e0"; shape = "dot"; size = 10; }

    let borderColor = "#555";
    if (node.valid === false) borderColor = "#ef4444";
    else if (node.status === "inactive") borderColor = "#9ca3af";
    else if (node.status === "error" || node.status === "FAILED") borderColor = "#ef4444";
    else if (node.status === "pending") borderColor = "#f59e0b";
    else if (node.status === "provisioned") borderColor = "#10b981";

    if (node.status === "inactive") colorBg = "#6b7280";
    if (node.type === "interface" && node.attributes && node.attributes.interface_type === "unknown") {
      colorBg = "#94a3b8";
    }

    return {
      color: { background: colorBg, border: borderColor, highlight: { background: colorBg, border: "#fff" } },
      shape,
      size,
    };
  }
};

const EdgeRenderer = {
  toVisEdge(edge) {
    const from = edge.from || edge.source;
    const to = edge.to || edge.target;
    return {
      id: edge.id || `${from}--${to}`,
      from,
      to,
      arrows: "to",
      width: edge.inferred ? 1 : 2,
      color: edge.inferred ? "#94a3b8" : "#555",
    };
  }
};

const DetailPanel = {
  target: document.getElementById("detailsContent"),
  render(node) {
    if (!node) {
      this.target.textContent = "Knoten auswaehlen, um Details zu sehen.";
      return;
    }
    const lines = [];
    const kv = (key, value) => {
      if (value === undefined || value === null || value === "") return;
      lines.push(`<div class="kv"><span class="key">${key}:</span><span>${value}</span></div>`);
    };

    kv("ID", node.id);
    kv("Type", node.type);
    kv("Label", node.label);
    kv("Status", node.status);
    kv("Inferred", node.inferred === true ? "yes" : "no");
    kv("Reason", node.inference_reason || "");
    kv("Source", node.source || "");
    kv("Project", node.project || "");
    kv("Role", node.role || "");
    kv("IP", node.ip || "");

    if (node.attributes) {
      kv("Interface Type", node.attributes.interface_type || "");
      kv("Valid", node.valid === false ? "false" : "true");
      kv("RX", node.rx_display);
      kv("TX", node.tx_display);
      kv("Signal", node.signal_display);
      kv("Bitrate", node.bitrate_display);
      kv("Warning", node.attributes.warning || "");
      kv("AP", node.attributes.ap_mac || "");
      kv("Connected", node.attributes.connected);
      kv("Last Seen", node.attributes.last_seen || "");
    }

    this.target.innerHTML = lines.join("");
  }
};

const FilterControls = {
  interfaceType: document.getElementById("interfaceTypeFilter"),
  clientsOnly: document.getElementById("clientsOnlyFilter"),
  showInactive: document.getElementById("showInactiveFilter"),
  getState() {
    return {
      interfaceType: this.interfaceType.value,
      clientsOnly: this.clientsOnly.checked,
      showInactive: this.showInactive.checked,
    };
  },
  matches(node, state) {
    if (!state.showInactive && node.status === "inactive") return false;
    if (state.clientsOnly && node.type !== "client") return false;
    if (state.interfaceType !== "all" && node.type === "interface") {
      const t = (node.attributes && node.attributes.interface_type) || "unknown";
      if (t !== state.interfaceType) return false;
    }
    return true;
  }
};

class GraphView {
  constructor(container) {
    this.nodes = new vis.DataSet([]);
    this.edges = new vis.DataSet([]);
    this.network = new vis.Network(container, { nodes: this.nodes, edges: this.edges }, {
      autoResize: true,
      physics: { enabled: false, stabilization: { iterations: 150 } },
      interaction: { hover: true, multiselect: false, dragNodes: true, zoomView: true, dragView: true },
      nodes: { font: { color: "#fff", size: 11 }, borderWidth: 1 },
      edges: { color: { color: "#555", highlight: "#fff" }, smooth: true }
    });
    this.domain = { nodes: [], edges: [], nodeById: {} };
    this.network.on("click", (params) => {
      const id = params.nodes && params.nodes.length ? params.nodes[0] : null;
      DetailPanel.render(id ? this.domain.nodeById[id] : null);
    });
  }

  setDomain(domain) {
    this.domain = domain;
    this.render();
  }

  render() {
    const state = FilterControls.getState();
    const visible = new Set();
    const visNodes = [];

    for (const node of this.domain.nodes) {
      if (!FilterControls.matches(node, state)) continue;
      visNodes.push(NodeRenderer.toVisNode(node));
      visible.add(node.id);
    }

    const visEdges = [];
    for (const edge of this.domain.edges) {
      const e = EdgeRenderer.toVisEdge(edge);
      if (!visible.has(e.from) || !visible.has(e.to)) continue;
      visEdges.push(e);
    }

    this.nodes.clear();
    this.nodes.add(visNodes);
    this.edges.clear();
    this.edges.add(visEdges);
  }
}

const TopologyPage = {
  statusText: document.getElementById("statusText"),
  refreshBtn: document.getElementById("refreshBtn"),
  graphView: null,

  async ensureVisLoaded() {
    if (window.vis && window.vis.Network && window.vis.DataSet) return;
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/vis-network/dist/vis-network.min.js";
      script.async = true;
      script.onload = resolve;
      script.onerror = () => reject(new Error("vis-network konnte nicht geladen werden"));
      document.head.appendChild(script);
    });
  },

  async init() {
    await this.ensureVisLoaded();
    this.graphView = new GraphView(document.getElementById("mynetwork"));
    this.bindEvents();
    await this.reload();
    setInterval(() => this.reload(), 5000);
  },

  bindEvents() {
    this.refreshBtn.addEventListener("click", () => this.reload());
    FilterControls.interfaceType.addEventListener("change", () => this.graphView.render());
    FilterControls.clientsOnly.addEventListener("change", () => this.graphView.render());
    FilterControls.showInactive.addEventListener("change", () => this.graphView.render());
  },

  async reload() {
    try {
      this.statusText.textContent = "Status: lade...";
      const payload = await TopologyApi.getTopologySnapshot();
      const domain = DomainMapper.mapSnapshot(payload);
      this.graphView.setDomain(domain);
      this.statusText.textContent = "Status: OK (" + new Date().toLocaleTimeString() + ")";
    } catch (err) {
      console.error(err);
      this.statusText.textContent = "Status: Fehler beim Laden";
    }
  }
};

TopologyPage.init();
</script>
"""
