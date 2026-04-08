const PANEL_TAG = "openwrt-topology-panel";

function esc(v) {
  return String(v)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function metric(v, unit = "") {
  if (v === null || v === undefined) return "?";
  return unit ? `${v} ${unit}` : String(v);
}

class OpenWrtTopologyPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._snapshot = null;
    this._selected = null;
    this._error = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this.render();
      this.load();
    }
  }

  static get properties() {
    return { hass: {} };
  }

  async load() {
    this._error = null;
    this.render();
    try {
      const resp = await fetch("/api/openwrt_topology/snapshot", { headers: { Accept: "application/json" } });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      this._snapshot = await resp.json();
      if (!this._selected && Array.isArray(this._snapshot.nodes) && this._snapshot.nodes.length) {
        this._selected = this._snapshot.nodes[0].id;
      }
    } catch (err) {
      this._error = err.message || String(err);
    }
    this.render();
  }

  laneFor(node) {
    const t = String(node.type || "unknown").toLowerCase();
    if (t === "router" || t === "gateway") return 0;
    if (t === "ap" || t === "access_point" || t === "switch" || t === "interface" || t === "ssid" || t === "unknown") return 1;
    return 2;
  }

  colorFor(node) {
    const t = String(node.type || "unknown").toLowerCase();
    if (node.status === "inactive") return "#6b7280";
    if (t === "router" || t === "gateway") return "#2563eb";
    if (t === "ap" || t === "access_point") return "#16a34a";
    if (t === "switch" || t === "interface") return "#64748b";
    if (t === "ssid") return "#7c3aed";
    return "#9ca3af";
  }

  shapeClassFor(node) {
    const t = String(node.type || "unknown").toLowerCase();
    if (t === "router" || t === "gateway" || t === "switch") return "shape-square";
    if (t === "ssid") return "shape-diamond";
    return "shape-circle";
  }

  computeLayout(nodes) {
    const byLane = [[], [], []];
    for (const node of nodes) byLane[this.laneFor(node)].push(node);

    const pos = {};
    const laneX = [100, 420, 760];
    for (let lane = 0; lane < byLane.length; lane += 1) {
      const list = byLane[lane];
      const step = Math.max(90, Math.floor(620 / Math.max(1, list.length)));
      for (let i = 0; i < list.length; i += 1) {
        pos[list[i].id] = { x: laneX[lane], y: 70 + i * step };
      }
    }
    return pos;
  }

  detailsHtml(node) {
    if (!node) return "<div>Node waehlen.</div>";
    const a = node.attributes || {};
    const rows = [
      ["Name", node.label || node.id],
      ["Typ", node.type],
      ["ID", node.id],
      ["IP", node.ip ?? "unbekannt"],
      ["Status", node.status ?? "unbekannt"],
      ["Signal", metric(a.signal, "dBm")],
      ["Bitrate", metric(a.bitrate, "Mbit/s")],
      ["Inferred", node.inferred ? "ja" : "nein"],
      ["Valid", node.valid === false ? "false" : "true"],
      ["Source", node.source ?? "unbekannt"],
      ["Interface Type", a.interface_type ?? "unbekannt"],
    ];
    const body = rows
      .map(([k, v]) => `<div class="row"><span class="k">${esc(k)}:</span><span>${esc(v)}</span></div>`)
      .join("");
    const hints = [
      node.inferred ? '<div class="hint">Wert abgeleitet, nicht gemessen.</div>' : "",
      node.valid === false ? '<div class="hint warn">Datenfehler - Wert nicht verlässlich.</div>' : "",
    ].join("");
    return `${body}${hints}`;
  }

  wireHandlers() {
    const nodeEls = this.shadowRoot.querySelectorAll(".node");
    nodeEls.forEach((el) => {
      el.addEventListener("click", () => {
        this._selected = el.getAttribute("data-id");
        this.render();
      });
    });

    const reload = this.shadowRoot.getElementById("reload");
    if (reload) reload.addEventListener("click", () => this.load());
  }

  render() {
    const snap = this._snapshot;
    const nodes = Array.isArray(snap?.nodes) ? snap.nodes : [];
    const edges = Array.isArray(snap?.edges) ? snap.edges : [];
    const layout = this.computeLayout(nodes);
    const selected = nodes.find((n) => n.id === this._selected) || null;

    const edgeSvg = edges
      .map((e) => {
        const from = e.from || e.source;
        const to = e.to || e.target;
        const p1 = layout[from];
        const p2 = layout[to];
        if (!p1 || !p2) return "";
        const dashed = e.inferred ? "4,5" : (String(e.relationship || "").includes("client") ? "8,6" : "");
        const color = e.inferred ? "#9ca3af" : "#64748b";
        return `<line x1="${p1.x}" y1="${p1.y}" x2="${p2.x}" y2="${p2.y}" stroke="${color}" stroke-width="2" ${dashed ? `stroke-dasharray="${dashed}"` : ""} />`;
      })
      .join("");

    const nodeHtml = nodes
      .map((n) => {
        const p = layout[n.id] || { x: 0, y: 0 };
        const inactive = n.status === "inactive" ? "inactive" : "";
        const invalid = n.valid === false ? "invalid" : "";
        const inferred = n.inferred ? "inferred" : "";
        const selectedCls = this._selected === n.id ? "selected" : "";
        const cls = ["node", this.shapeClassFor(n), inactive, invalid, inferred, selectedCls].filter(Boolean).join(" ");
        const tip = `Signal: ${metric((n.attributes || {}).signal, "dBm")} | Bitrate: ${metric((n.attributes || {}).bitrate, "Mbit/s")}`;
        return `<button class="${cls}" data-id="${esc(n.id)}" style="left:${p.x}px;top:${p.y}px;background:${this.colorFor(n)}" title="${esc(tip)}">
            <span>${esc(n.label || n.id)}</span>
          </button>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; height:100%; }
        .page { height:100%; display:grid; grid-template-columns: 1fr 360px; grid-template-rows:auto 1fr; background: var(--card-background-color, #111827); color: var(--primary-text-color, #e5e7eb); }
        .head { grid-column:1/3; padding:10px 14px; border-bottom:1px solid var(--divider-color, #334155); display:flex; justify-content:space-between; align-items:center; }
        .canvas { position:relative; overflow:auto; min-height:620px; }
        .canvas-inner { position:relative; width:900px; height:700px; margin:10px; }
        svg { position:absolute; inset:0; }
        .lane-label { position:absolute; top:8px; font-size:12px; color:#94a3b8; }
        .lane-0 { left:70px; } .lane-1 { left:390px; } .lane-2 { left:740px; }
        .node { position:absolute; transform:translate(-50%,-50%); border:2px solid #1f2937; color:#f8fafc; min-width:74px; min-height:34px; padding:6px 8px; cursor:pointer; box-shadow:none; opacity:1; }
        .shape-circle { border-radius:20px; }
        .shape-square { border-radius:8px; }
        .shape-diamond { transform:translate(-50%,-50%) rotate(45deg); border-radius:6px; }
        .shape-diamond span { display:inline-block; transform:rotate(-45deg); }
        .inactive { opacity:0.4; }
        .invalid { border-color:#ef4444; }
        .inferred { outline:2px dashed #93c5fd; outline-offset:2px; }
        .selected { box-shadow:0 0 0 2px #e5e7eb inset; }
        .side { border-left:1px solid var(--divider-color, #334155); padding:12px; overflow:auto; }
        .row { margin-bottom:6px; }
        .k { display:inline-block; width:120px; color:#94a3b8; }
        .hint { margin-top:10px; color:#93c5fd; }
        .hint.warn { color:#fca5a5; }
        .error { color:#fca5a5; }
      </style>
      <div class="page">
        <div class="head">
          <div>OpenWrt Topology (Native HA Panel)</div>
          <div>
            <button id="reload">Neu laden</button>
          </div>
        </div>
        <div class="canvas">
          ${this._error ? `<div class="error">Fehler: ${esc(this._error)}</div>` : ""}
          <div class="canvas-inner">
            <div class="lane-label lane-0">Gateway / Router</div>
            <div class="lane-label lane-1">AP / Switch / Interfaces</div>
            <div class="lane-label lane-2">Clients</div>
            <svg viewBox="0 0 900 700" preserveAspectRatio="none">${edgeSvg}</svg>
            ${nodeHtml}
          </div>
        </div>
        <div class="side">
          <h3>Details</h3>
          ${this.detailsHtml(selected)}
        </div>
      </div>
    `;

    this.wireHandlers();
  }
}

if (!customElements.get(PANEL_TAG)) {
  customElements.define(PANEL_TAG, OpenWrtTopologyPanel);
}
