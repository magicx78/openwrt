"""
topology_ui.py — HTML/JS content for /ui/topology (v0.7.0)

Topology visualization: vis-network, LR hierarchical layout,
semantic-correct null display, inferred/valid/inactive markers.
Consumed by: server.py ui_topology() -> _page(TOPOLOGY_UI_CONTENT, ...)

Semantics enforced (per ANWEISUNG 2, 2026-04-08):
  - signal/bitrate null  -> "?" (never a fake default like -60 or 0)
  - inactive             -> 40% opacity, no error icon
  - valid: false         -> red border + "!" label marker
  - inferred: true       -> dashed/dotted edge + "*" label marker
  - interface_type unknown -> neutral grey, not an error
"""
from __future__ import annotations

TOPOLOGY_UI_CONTENT = r"""
<style>
html, body {
  margin: 0; padding: 0; height: 100%;
  background: #111; color: #eee;
  font-family: system-ui, -apple-system, sans-serif;
  overflow: hidden;
}
#app {
  display: grid;
  grid-template-columns: 1fr 320px;
  grid-template-rows: auto 1fr auto;
  grid-template-areas: "header header" "graph sidebar" "footer footer";
  height: 100%;
}
header {
  grid-area: header;
  padding: 8px 12px;
  background: #181818;
  border-bottom: 1px solid #333;
  display: flex; align-items: center; justify-content: space-between;
}
header .title { font-size: 14px; font-weight: 600; }
header .controls {
  display: flex; gap: 8px; align-items: center;
  font-size: 12px; flex-wrap: wrap;
}
header select, header button {
  background: #222; color: #eee; border: 1px solid #444;
  padding: 3px 6px; border-radius: 3px; font-size: 12px; cursor: pointer;
}
header label { color: #bbb; }
#graph { grid-area: graph; position: relative; }
#mynetwork { width: 100%; height: 100%; background: #111; }
#error-msg {
  display: none;
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  background: #1a0808; border: 1px solid #ef4444;
  padding: 16px 24px; border-radius: 8px;
  color: #ef4444; font-size: 13px; text-align: center;
  z-index: 100; max-width: 340px; line-height: 1.6;
}
#sidebar {
  grid-area: sidebar;
  border-left: 1px solid #333; background: #141414;
  padding: 8px; font-size: 12px; overflow-y: auto;
}
#sidebar h2 {
  font-size: 13px; margin: 0 0 8px;
  padding-bottom: 4px; border-bottom: 1px solid #333;
}
#sidebar .kv { margin-bottom: 4px; }
#sidebar .kv span.key { display: inline-block; width: 110px; color: #999; }
.legend {
  margin-top: 12px; border-top: 1px solid #2a2a2a; padding-top: 8px;
}
.legend strong {
  display: block; margin-bottom: 4px;
  color: #888; font-size: 10px;
  text-transform: uppercase; letter-spacing: .05em;
}
.legend-item {
  display: flex; align-items: center;
  gap: 6px; margin-bottom: 4px;
  font-size: 11px; color: #bbb;
}
.legend-dot  { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.legend-line { width: 24px; height: 0; flex-shrink: 0; }
#topology-footer {
  grid-area: footer;
  padding: 3px 12px;
  background: #0d0d0d; border-top: 1px solid #222;
  font-size: 11px; color: #4a4a4a;
  display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
}
#topology-footer span { white-space: nowrap; }
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
      <label><input type="checkbox" id="inferredFilter"> highlight inferred</label>
      <button id="refreshBtn">refresh</button>
      <span id="statusText">Status: initialisiere...</span>
    </div>
  </header>

  <div id="graph">
    <div id="mynetwork"></div>
    <div id="error-msg"></div>
  </div>

  <aside id="sidebar">
    <h2>Details</h2>
    <div id="detailsContent">Knoten auswaehlen, um Details zu sehen.</div>

    <div class="legend">
      <strong>Nodes</strong>
      <div class="legend-item">
        <span class="legend-dot" style="background:#4fc3f7"></span> Router / Gateway
      </div>
      <div class="legend-item">
        <span class="legend-dot" style="background:#81c784"></span> Access Point
      </div>
      <div class="legend-item">
        <span class="legend-dot" style="background:#ffd166;border-radius:2px"></span> Interface
      </div>
      <div class="legend-item">
        <span class="legend-dot" style="background:#c084fc;border-radius:0;transform:rotate(45deg);width:8px;height:8px;margin:1px 2px"></span> SSID
      </div>
      <div class="legend-item">
        <span class="legend-dot" style="background:#e0e0e0"></span> Client
      </div>
      <div class="legend-item">
        <span class="legend-dot" style="background:#6b7280;opacity:.4"></span> inactive (40% opacity)
      </div>
      <div class="legend-item">
        <span class="legend-dot" style="background:transparent;outline:2px solid #ef4444;outline-offset:1px"></span>
        valid: false
      </div>
      <div class="legend-item" style="color:#666;font-style:italic">
        * = abgeleitet &nbsp;&nbsp; ! = Datenfehler
      </div>
    </div>

    <div class="legend">
      <strong>Edges</strong>
      <div class="legend-item">
        <span class="legend-line" style="border-top:2px solid #555"></span> Ethernet / LAN
      </div>
      <div class="legend-item">
        <span class="legend-line" style="border-top:2px dashed #f97316"></span> WiFi 2.4 GHz
      </div>
      <div class="legend-item">
        <span class="legend-line" style="border-top:2px dashed #3b82f6"></span> WiFi 5 GHz
      </div>
      <div class="legend-item">
        <span class="legend-line" style="border-top:2px dashed #a78bfa"></span> WiFi (Band unbekannt)
      </div>
      <div class="legend-item">
        <span class="legend-line" style="border-top:2px dotted #94a3b8"></span> abgeleitet (inferred)
      </div>
    </div>
  </aside>

  <div id="topology-footer">
    <span>&#x2139; Config-Pull ohne Live-Daten &#x2014; Snapshot zeigt zuletzt bekannte Werte.</span>
    <span>inactive = 0&thinsp;RX und 0&thinsp;TX</span>
    <span>? = Wert nicht vorhanden (kein Fantasie-Default)</span>
    <span id="snapshotMeta"></span>
    <!-- TODO: WebSocket Live-Update (spaetere Phase) -->
  </div>

</div>

<script>
// =============================================================================
// TopologyApi — fetches the canonical snapshot
// =============================================================================
const TopologyApi = {
  SNAPSHOT_URL: "/api/topology/snapshot?include_wifi=1",

  async getTopologySnapshot() {
    const res = await fetch(this.SNAPSHOT_URL, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.json();
  }
};

// =============================================================================
// DomainMapper — converts raw payload into rich domain objects
// =============================================================================
const DomainMapper = {
  mapSnapshot(payload) {
    const interfaces = Array.isArray(payload.interfaces) ? payload.interfaces : [];
    const clients    = Array.isArray(payload.clients)    ? payload.clients    : [];
    const nodes      = Array.isArray(payload.nodes)      ? payload.nodes      : [];
    const edges      = Array.isArray(payload.edges)      ? payload.edges      : [];

    const nodeById = {};

    for (const n of nodes) {
      const attrs = n.attributes || {};
      nodeById[n.id] = {
        ...n,
        attributes:      attrs,
        signal_display:  this.metricOrUnknown(attrs.signal,   "dBm"),
        bitrate_display: this.metricOrUnknown(attrs.bitrate,  "Mbit/s"),
        rx_display:      this.metricOrUnknown(attrs.rx_bytes, "bytes"),
        tx_display:      this.metricOrUnknown(attrs.tx_bytes, "bytes"),
      };
    }

    const interfaceById = {};
    for (const iface of interfaces) {
      interfaceById[iface.id] = iface;
      if (!nodeById[iface.id]) {
        nodeById[iface.id] = {
          id:    iface.id,
          type:  "interface",
          label: iface.name || iface.id,
          status:           iface.status,
          valid:            iface.valid,
          source:           iface.source,
          inferred:         iface.inferred,
          inference_reason: iface.inference_reason,
          attributes: {
            ap_mac:         iface.ap_mac,
            interface_type: iface.interface_type,
            rx_bytes:       iface.rx_bytes,
            tx_bytes:       iface.tx_bytes,
            warning:        iface.warning,
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
          id:    c.id,
          type:  "client",
          label: c.mac || c.id,
          status:           c.connected === false ? "inactive" : "active",
          source:           c.source,
          inferred:         c.inferred,
          inference_reason: c.inference_reason,
          attributes: {
            ap_mac:    c.ap_mac,
            connected: c.connected,
            last_seen: c.last_seen,
            signal:    c.signal,
            bitrate:   c.bitrate,
          },
          signal_display:  this.metricOrUnknown(c.signal,  "dBm"),
          bitrate_display: this.metricOrUnknown(c.bitrate, "Mbit/s"),
        };
      }
    }

    return {
      generated_at: payload.generated_at,
      meta:         payload.meta || {},
      interfaces, clients, edges,
      nodes:        Object.values(nodeById),
      nodeById, interfaceById, clientById,
    };
  },

  // null / undefined -> "?"  —  never substitute a fake numeric default
  metricOrUnknown(value, unit) {
    if (value === null || value === undefined) return "?";
    return unit ? value + " " + unit : String(value);
  }
};

// =============================================================================
// NodeRenderer — builds vis-network node descriptors
// =============================================================================
const NodeRenderer = {
  // LR hierarchy: Router(0) -> AP(1) -> Interface(2) -> SSID(3) -> Client(4)
  TYPE_LEVEL: { router: 0, ap: 1, interface: 2, ssid: 3, client: 4 },

  toVisNode(node) {
    const style = this.style(node);
    let label = node.label || node.id;
    if (node.inferred)        label += " *"; // abgeleitet, nicht gemessen
    if (node.valid === false) label += " !"; // Datenfehler

    return {
      id:    node.id,
      label,
      level: this.TYPE_LEVEL[node.type] !== undefined ? this.TYPE_LEVEL[node.type] : 2,
      title: this._tooltip(node),  // hover-tooltip as DOM element
      _type: node.type,
      ...style,
    };
  },

  // Returns a DOM element used as vis-network hover tooltip
  _tooltip(node) {
    const d = document.createElement("div");
    d.style.cssText =
      "background:#1c1c2e;color:#e2e8f0;padding:8px 12px;" +
      "border-radius:6px;font-size:11px;line-height:1.8;" +
      "border:1px solid #374151;pointer-events:none;max-width:280px";

    const rows = [];
    const kv = function(k, v) {
      if (v !== null && v !== undefined && v !== "") {
        rows.push("<b>" + k + ":</b> " + v);
      }
    };

    kv("Name",   node.label);
    kv("Type",   node.type);
    kv("Status", node.status);
    if (node.ip) kv("IP", node.ip);

    // Signal — iw station dump caveat when null
    const attrs = node.attributes || {};
    if (Object.prototype.hasOwnProperty.call(attrs, "signal")) {
      if (attrs.signal === null) {
        rows.push("<b>Signal:</b> <span style='color:#9ca3af'>" +
                  "? (aus iw station dump nicht eindeutig)</span>");
      } else {
        rows.push("<b>Signal:</b> " + attrs.signal + " dBm");
      }
    }
    if (Object.prototype.hasOwnProperty.call(attrs, "bitrate")) {
      if (attrs.bitrate === null) {
        rows.push("<b>Bitrate:</b> <span style='color:#9ca3af'>?</span>");
      } else {
        rows.push("<b>Bitrate:</b> " + attrs.bitrate + " Mbit/s");
      }
    }

    // Semantic status notes (no error icons for innocent states)
    if (node.status === "inactive") {
      rows.push("<span style='color:#9ca3af'>inactive: 0 RX und 0 TX</span>");
    }
    if (node.inferred) {
      rows.push("<span style='color:#f59e0b'>* Wert abgeleitet, nicht gemessen</span>");
    }
    if (node.valid === false) {
      rows.push("<span style='color:#ef4444'>! Datenfehler &mdash; Wert nicht verl&auml;sslich</span>");
    }

    d.innerHTML = rows.join("<br>");
    return d;
  },

  style(node) {
    let colorBg = "#888", shape = "dot", size = 14;

    if      (node.type === "router")    { colorBg = "#4fc3f7"; shape = "ellipse"; size = 24; }
    else if (node.type === "ap")        { colorBg = "#81c784"; shape = "dot";     size = 20; }
    else if (node.type === "interface") { colorBg = "#ffd166"; shape = "box";     size = 14; }
    else if (node.type === "ssid")      { colorBg = "#c084fc"; shape = "diamond"; size = 14; }
    else if (node.type === "client")    { colorBg = "#e0e0e0"; shape = "dot";     size = 10; }

    let borderColor = "#555";
    if      (node.valid === false)                                   borderColor = "#ef4444";
    else if (node.status === "inactive")                             borderColor = "#9ca3af";
    else if (node.status === "error" || node.status === "FAILED")    borderColor = "#ef4444";
    else if (node.status === "pending")                              borderColor = "#f59e0b";
    else if (node.status === "provisioned")                          borderColor = "#10b981";

    const inactive = node.status === "inactive";
    if (inactive) colorBg = "#6b7280";

    // unknown interface type -> neutral grey, NOT an error
    if (node.type === "interface" && attrs(node, "interface_type") === "unknown") {
      colorBg = "#94a3b8";
    }

    return {
      color: {
        background: colorBg,
        border:     borderColor,
        highlight:  { background: colorBg, border: "#fff" },
      },
      shape,
      size,
      opacity: inactive ? 0.4 : 1,  // inactive nodes: 40% opacity
    };
  }
};

function attrs(node, key) {
  return node && node.attributes ? node.attributes[key] : undefined;
}

// =============================================================================
// EdgeRenderer — builds vis-network edge descriptors
// =============================================================================
const EdgeRenderer = {
  toVisEdge(edge, nodeById) {
    const from   = edge.from   || edge.source;
    const to     = edge.to     || edge.target;
    const toNode = nodeById ? nodeById[to] : null;

    // Determine medium: relationship type + connected node's interface_type
    const rel         = edge.relationship || "";
    const isWifiRel   = rel === "has_client" || rel === "broadcasts_ssid";
    const toIfaceType = attrs(toNode, "interface_type");
    const isWifi      = isWifiRel || toIfaceType === "wifi";

    let edgeColor = "#555";  // default: Ethernet solid grey
    let dashes    = false;

    if (edge.inferred) {
      // dotted, light — data not confirmed
      dashes    = [2, 6];
      edgeColor = "#94a3b8";
    } else if (isWifi) {
      dashes = [6, 4];
      // Band detection from phy number in node IDs (Cudy WR3000: phy0=2.4GHz, phy1=5GHz)
      const combined = (from || "") + " " + (to || "");
      if (combined.indexOf("phy0") !== -1)      edgeColor = "#f97316"; // 2.4 GHz orange
      else if (combined.indexOf("phy1") !== -1) edgeColor = "#3b82f6"; // 5 GHz blue
      else                                       edgeColor = "#a78bfa"; // band unknown, purple
    }

    return {
      id:     edge.id || (from + "--" + to),
      from, to,
      arrows: "to",
      width:  edge.inferred ? 1 : 2,
      color:  { color: edgeColor, highlight: "#fff", hover: "#aaa" },
      dashes,
    };
  }
};

// =============================================================================
// DetailPanel — sidebar with node attributes on click
// =============================================================================
const DetailPanel = {
  target: document.getElementById("detailsContent"),

  render(node) {
    if (!node) {
      this.target.textContent = "Knoten auswaehlen, um Details zu sehen.";
      return;
    }
    const lines = [];
    const kv = function(key, value) {
      if (value === undefined) return;
      let display;
      if (value === null || value === "") {
        display = "<span style='color:#6b7280'>unbekannt</span>";
      } else {
        display = String(value);
      }
      lines.push("<div class='kv'><span class='key'>" + key + ":</span><span>" + display + "</span></div>");
    };

    kv("ID",     node.id);
    kv("Type",   node.type);
    kv("Label",  node.label);
    kv("Status", node.status);
    kv("Inferred",
       node.inferred === true
         ? "<span style='color:#f59e0b'>ja &mdash; Wert abgeleitet, nicht gemessen</span>"
         : "nein");
    if (node.inference_reason) kv("Reason",  node.inference_reason);
    if (node.source)           kv("Source",  node.source);
    if (node.project)          kv("Project", node.project);
    if (node.role)             kv("Role",    node.role);
    if (node.ip !== undefined) kv("IP",      node.ip);

    if (node.attributes) {
      if (node.attributes.interface_type !== undefined) {
        kv("Interface Type", node.attributes.interface_type);
      }
      kv("Valid",
         node.valid === false
           ? "<span style='color:#ef4444'>false &mdash; Datenfehler</span>"
           : "true");
      kv("RX",      node.rx_display);
      kv("TX",      node.tx_display);
      kv("Signal",  node.signal_display);
      kv("Bitrate", node.bitrate_display);
      if (node.attributes.warning)              kv("Warning",   node.attributes.warning);
      if (node.attributes.ap_mac)               kv("AP",        node.attributes.ap_mac);
      if (node.attributes.connected !== undefined) kv("Connected", node.attributes.connected);
      if (node.attributes.last_seen)            kv("Last Seen", node.attributes.last_seen);
    }

    if (node.inferred) {
      lines.push("<div style='margin-top:6px;color:#f59e0b;font-style:italic;font-size:11px'>" +
                 "* Wert abgeleitet, nicht gemessen</div>");
    }
    if (node.valid === false) {
      lines.push("<div style='margin-top:4px;color:#ef4444;font-style:italic;font-size:11px'>" +
                 "! Datenfehler &mdash; Wert nicht verl&auml;sslich</div>");
    }

    this.target.innerHTML = lines.join("");
  }
};

// =============================================================================
// FilterControls
// =============================================================================
const FilterControls = {
  interfaceType:     document.getElementById("interfaceTypeFilter"),
  clientsOnly:       document.getElementById("clientsOnlyFilter"),
  showInactive:      document.getElementById("showInactiveFilter"),
  inferredHighlight: document.getElementById("inferredFilter"),

  getState() {
    return {
      interfaceType:     this.interfaceType.value,
      clientsOnly:       this.clientsOnly.checked,
      showInactive:      this.showInactive.checked,
      inferredHighlight: this.inferredHighlight.checked,
    };
  },

  matches(node, state) {
    if (!state.showInactive && node.status === "inactive")    return false;
    if (state.clientsOnly   && node.type   !== "client")      return false;
    if (state.interfaceType !== "all" && node.type === "interface") {
      const t = (node.attributes && node.attributes.interface_type) || "unknown";
      if (t !== state.interfaceType) return false;
    }
    return true;
  }
};

// =============================================================================
// GraphView — vis-network wrapper with hierarchical LR layout
// =============================================================================
class GraphView {
  constructor(container) {
    this.nodes   = new vis.DataSet([]);
    this.edges   = new vis.DataSet([]);
    this.network = new vis.Network(
      container,
      { nodes: this.nodes, edges: this.edges },
      {
        autoResize: true,
        physics: { enabled: false },
        layout: {
          hierarchical: {
            enabled:         true,
            direction:       "LR",   // left-to-right: gateway -> clients
            levelSeparation: 200,
            nodeSpacing:     80,
            treeSpacing:     120,
            sortMethod:      "directed",
            shakeTowards:    "roots",
          }
        },
        interaction: {
          hover:        true,
          tooltipDelay: 150,
          multiselect:  false,
          dragNodes:    true,
          zoomView:     true,
          dragView:     true,
        },
        nodes: {
          font:        { color: "#fff", size: 11 },
          borderWidth: 2,
        },
        edges: {
          smooth: {
            enabled:        true,
            type:           "cubicBezier",
            forceDirection: "horizontal",
          }
        }
      }
    );

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
    const state    = FilterControls.getState();
    const visible  = new Set();
    const visNodes = [];

    for (const node of this.domain.nodes) {
      if (!FilterControls.matches(node, state)) continue;
      const vn = NodeRenderer.toVisNode(node);
      // "highlight inferred" checkbox -> golden border
      if (state.inferredHighlight && node.inferred) {
        vn.borderWidth  = 3;
        vn.color.border = "#f59e0b";
      }
      visNodes.push(vn);
      visible.add(node.id);
    }

    const visEdges = [];
    for (const edge of this.domain.edges) {
      const e = EdgeRenderer.toVisEdge(edge, this.domain.nodeById);
      if (!visible.has(e.from) || !visible.has(e.to)) continue;
      visEdges.push(e);
    }

    this.nodes.clear();
    this.nodes.add(visNodes);
    this.edges.clear();
    this.edges.add(visEdges);
  }
}

// =============================================================================
// TopologyPage — page lifecycle: load vis, fetch snapshot, poll
// =============================================================================
const TopologyPage = {
  statusText:   document.getElementById("statusText"),
  refreshBtn:   document.getElementById("refreshBtn"),
  errorMsg:     document.getElementById("error-msg"),
  snapshotMeta: document.getElementById("snapshotMeta"),
  graphView:    null,

  async ensureVisLoaded() {
    if (window.vis && window.vis.Network && window.vis.DataSet) return;
    await new Promise(function(resolve, reject) {
      const script   = document.createElement("script");
      script.src     = "https://cdn.jsdelivr.net/npm/vis-network/dist/vis-network.min.js";
      script.async   = true;
      script.onload  = resolve;
      script.onerror = function() {
        reject(new Error("vis-network konnte nicht geladen werden"));
      };
      document.head.appendChild(script);
    });
  },

  showError(msg) {
    this.errorMsg.textContent   = msg;
    this.errorMsg.style.display = "block";
    this.statusText.textContent = "Status: Fehler";
  },

  hideError() {
    this.errorMsg.style.display = "none";
  },

  async init() {
    try {
      await this.ensureVisLoaded();
    } catch (err) {
      this.showError("vis-network konnte nicht geladen werden.\nBitte Internetverbindung pruefen.");
      return;
    }
    this.graphView = new GraphView(document.getElementById("mynetwork"));
    this._bindEvents();
    await this.reload();
    setInterval(() => this.reload(), 5000);
  },

  _bindEvents() {
    this.refreshBtn.addEventListener("click", () => this.reload());
    FilterControls.interfaceType    .addEventListener("change", () => this.graphView.render());
    FilterControls.clientsOnly      .addEventListener("change", () => this.graphView.render());
    FilterControls.showInactive     .addEventListener("change", () => this.graphView.render());
    FilterControls.inferredHighlight.addEventListener("change", () => this.graphView.render());
  },

  async reload() {
    try {
      this.hideError();
      this.statusText.textContent = "Status: lade...";
      const payload = await TopologyApi.getTopologySnapshot();
      const domain  = DomainMapper.mapSnapshot(payload);
      this.graphView.setDomain(domain);
      this.statusText.textContent = "Status: OK (" + new Date().toLocaleTimeString() + ")";
      const m = payload.meta;
      if (m) {
        this.snapshotMeta.textContent =
          "Nodes: "    + (m.node_count   !== undefined ? m.node_count   : "?") +
          " | Edges: " + (m.edge_count   !== undefined ? m.edge_count   : "?") +
          " | Clients: " + (m.client_count !== undefined ? m.client_count : "?") +
          (m.inference_used ? " | \u26a0 inferred data" : "");
      }
    } catch (err) {
      console.error("[topology]", err);
      this.showError("Snapshot nicht verfuegbar: " + err.message);
    }
  }
};

TopologyPage.init();
</script>
"""
