(function () {
  const SNAPSHOT_URL = "/api/topology/snapshot";

  const statusEl = document.getElementById("status");
  const loadingEl = document.getElementById("loading");
  const errorEl = document.getElementById("error");
  const detailEl = document.getElementById("detailContent");

  const filterHideInactive = document.getElementById("filterHideInactive");
  const filterHighlightInferred = document.getElementById("filterHighlightInferred");
  const filterClientsWithSignal = document.getElementById("filterClientsWithSignal");

  const visNodes = new vis.DataSet([]);
  const visEdges = new vis.DataSet([]);
  const graph = new vis.Network(
    document.getElementById("graph"),
    { nodes: visNodes, edges: visEdges },
    {
      physics: false,
      interaction: { hover: true, dragNodes: false, dragView: true, zoomView: true },
      layout: {
        hierarchical: {
          enabled: true,
          direction: "LR",
          sortMethod: "directed",
          levelSeparation: 250,
          nodeSpacing: 140,
          treeSpacing: 180,
        },
      },
      nodes: {
        borderWidth: 2,
        font: { color: "#dbe3ea", size: 12, face: "Segoe UI" },
      },
      edges: {
        arrows: "to",
        smooth: { enabled: true, type: "cubicBezier", roundness: 0.28 },
        font: { color: "#9fb0c3", size: 10, align: "middle" },
      },
    }
  );

  let domain = { nodes: [], edges: [], nodeById: {} };

  function valueOrUnknown(value) {
    if (value === null || value === undefined || value === "") return "unbekannt";
    return String(value);
  }

  function metricDisplay(value, unit) {
    if (value === null || value === undefined) return "?";
    return unit ? `${value} ${unit}` : String(value);
  }

  function escapeHtml(v) {
    return String(v)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function nodeLevel(type) {
    const t = (type || "").toLowerCase();
    if (t === "gateway" || t === "router") return 0;
    if (t === "access_point" || t === "ap" || t === "switch" || t === "interface" || t === "ssid" || t === "unknown") return 1;
    return 2;
  }

  function nodeVisual(node, highlightInferred) {
    const attrs = node.attributes || {};
    const t = (node.type || "unknown").toLowerCase();

    let shape = "dot";
    let bg = "#9ca3af";
    let size = 16;

    if (t === "gateway" || t === "router") {
      shape = "box";
      bg = "#2563eb";
      size = 24;
    } else if (t === "access_point" || t === "ap") {
      shape = "triangle";
      bg = "#16a34a";
      size = 20;
    } else if (t === "switch") {
      shape = "box";
      bg = "#64748b";
      size = 18;
    } else if (t === "interface") {
      shape = "ellipse";
      bg = "#64748b";
      size = 15;
      if ((attrs.interface_type || "").toLowerCase() === "unknown") {
        bg = "#94a3b8";
      }
    } else if (t === "ssid") {
      shape = "diamond";
      bg = "#7c3aed";
      size = 14;
    } else if (t === "client") {
      shape = "dot";
      bg = "#9ca3af";
      size = 12;
    }

    let border = "#374151";
    if (node.valid === false) border = "#ef4444";

    let opacity = 1.0;
    if ((node.status || "").toLowerCase() === "inactive") {
      bg = "#6b7280";
      opacity = 0.4;
    }

    let label = node.label || node.id;
    if (node.valid === false) label = `${label} !`;
    if (highlightInferred && node.inferred === true) label = `${label} *`;

    const title = [
      `Name: ${valueOrUnknown(node.label || node.id)}`,
      `IP: ${valueOrUnknown(node.ip)}`,
      `MAC: ${valueOrUnknown(node.mac || attrs.mac || node.id)}`,
      `Signal: ${metricDisplay(attrs.signal, "dBm")}`,
      `Bitrate: ${metricDisplay(attrs.bitrate, "Mbit/s")}`,
      `Status: ${valueOrUnknown(node.status)}`,
      `Inferred: ${node.inferred === true ? "ja" : "nein"}`,
    ].join("\n");

    return {
      id: node.id,
      label,
      title,
      shape,
      size,
      level: nodeLevel(t),
      color: {
        background: bg,
        border,
        highlight: { background: bg, border: "#e5e7eb" },
      },
      opacity,
      type: node.type,
    };
  }

  function edgeVisual(edge, nodeById, highlightInferred) {
    const from = edge.from || edge.source;
    const to = edge.to || edge.target;
    const rel = (edge.relationship || "").toLowerCase();

    let color = "#6b7280";
    let dashes = false;

    if (rel === "has_interface") {
      color = "#6b7280";
      dashes = false;
    } else if (rel === "broadcasts_ssid" || rel === "has_client") {
      color = "#3b82f6";
      dashes = [8, 6];

      const fromNode = nodeById[from] || {};
      const toNode = nodeById[to] || {};
      const combo = `${(fromNode.label || "").toLowerCase()} ${(toNode.label || "").toLowerCase()}`;
      if (combo.includes("2.4") || combo.includes("2g")) color = "#f59e0b";
      if (combo.includes("5g")) color = "#3b82f6";
    }

    if (edge.inferred === true) {
      color = "#94a3b8";
      dashes = [2, 6];
    }

    const toNode = nodeById[to] || {};
    const signal = toNode.attributes ? toNode.attributes.signal : null;
    const label = (rel === "has_client") ? metricDisplay(signal, "dBm") : undefined;

    return {
      id: edge.id || `${from}--${to}`,
      from,
      to,
      label,
      color,
      dashes,
      width: edge.inferred ? 1 : 2,
      title: `Inferred: ${edge.inferred === true ? "ja" : "nein"}`,
    };
  }

  function mergedDomain(snapshot) {
    const interfaces = Array.isArray(snapshot.interfaces) ? snapshot.interfaces : [];
    const clients = Array.isArray(snapshot.clients) ? snapshot.clients : [];
    const nodes = Array.isArray(snapshot.nodes) ? snapshot.nodes : [];
    const edges = Array.isArray(snapshot.edges) ? snapshot.edges : [];

    const nodeById = {};
    for (const n of nodes) {
      nodeById[n.id] = {
        ...n,
        attributes: n.attributes || {},
      };
    }

    for (const iface of interfaces) {
      if (!nodeById[iface.id]) {
        nodeById[iface.id] = {
          id: iface.id,
          type: "interface",
          label: iface.name || iface.id,
          status: iface.status,
          valid: iface.valid,
          inferred: iface.inferred,
          inference_reason: iface.inference_reason,
          source: iface.source,
          attributes: {
            ap_mac: iface.ap_mac,
            interface_type: iface.interface_type,
            rx_bytes: iface.rx_bytes,
            tx_bytes: iface.tx_bytes,
            warning: iface.warning,
          },
        };
      }
    }

    for (const client of clients) {
      if (!nodeById[client.id]) {
        nodeById[client.id] = {
          id: client.id,
          type: "client",
          label: client.mac || client.id,
          status: client.connected === false ? "inactive" : "active",
          inferred: client.inferred,
          inference_reason: client.inference_reason,
          source: client.source,
          attributes: {
            ap_mac: client.ap_mac,
            signal: client.signal,
            bitrate: client.bitrate,
            connected: client.connected,
            last_seen: client.last_seen,
          },
        };
      }
    }

    return {
      generated_at: snapshot.generated_at,
      meta: snapshot.meta || {},
      interfaces,
      clients,
      nodes: Object.values(nodeById),
      edges,
      nodeById,
    };
  }

  function applyFilters(input) {
    const hideInactive = filterHideInactive.checked;
    const clientsWithSignal = filterClientsWithSignal.checked;

    const visible = [];
    const keep = new Set();
    for (const n of input.nodes) {
      const attrs = n.attributes || {};
      if (hideInactive && String(n.status || "").toLowerCase() === "inactive") continue;
      if (clientsWithSignal && String(n.type || "").toLowerCase() === "client" && (attrs.signal === null || attrs.signal === undefined)) continue;
      visible.push(n);
      keep.add(n.id);
    }

    const filteredEdges = [];
    for (const e of input.edges) {
      const from = e.from || e.source;
      const to = e.to || e.target;
      if (!keep.has(from) || !keep.has(to)) continue;
      filteredEdges.push(e);
    }

    return { ...input, nodes: visible, edges: filteredEdges };
  }

  function renderDetails(node) {
    if (!node) {
      detailEl.textContent = "Knoten anklicken, um Details zu sehen.";
      return;
    }

    const attrs = node.attributes || {};
    const details = [
      ["Name", node.label || node.id],
      ["Typ", node.type],
      ["ID", node.id],
      ["IP", node.ip],
      ["MAC", node.mac || attrs.mac],
      ["Signal", metricDisplay(attrs.signal, "dBm")],
      ["Bitrate", metricDisplay(attrs.bitrate, "Mbit/s")],
      ["Status", node.status],
      ["Quelle", node.source],
      ["Inferred", node.inferred === true ? "ja" : "nein"],
      ["Inference Reason", node.inference_reason],
      ["Valid", node.valid === false ? "false" : "true"],
      ["Interface Type", attrs.interface_type],
      ["RX Bytes", attrs.rx_bytes],
      ["TX Bytes", attrs.tx_bytes],
      ["AP MAC", attrs.ap_mac],
      ["Connected", attrs.connected],
      ["Last Seen", attrs.last_seen],
      ["Warning", attrs.warning],
    ];

    const rows = [];
    for (const [k, v] of details) {
      const isUnknown = v === null || v === undefined || v === "";
      rows.push(`<div class="kv"><span class="k">${escapeHtml(k)}:</span><span class="${isUnknown ? "unknown" : ""}">${escapeHtml(isUnknown ? "unbekannt" : String(v))}</span></div>`);
    }

    if (node.inferred === true) {
      rows.push('<div class="kv unknown">Hinweis: Wert abgeleitet, nicht gemessen.</div>');
    }
    if (node.valid === false) {
      rows.push('<div class="kv warn">Hinweis: Datenfehler - Wert nicht verlaesslich.</div>');
    }

    const attrsSignalNull = attrs.signal === null || attrs.signal === undefined;
    if (attrsSignalNull) {
      rows.push('<div class="kv unknown">Signal: ? (aus iw station dump nicht eindeutig)</div>');
    }

    detailEl.innerHTML = rows.join("");
  }

  function renderGraph() {
    const highlightInferred = filterHighlightInferred.checked;
    const filtered = applyFilters(domain);

    const nodes = filtered.nodes.map((n) => nodeVisual(n, highlightInferred));
    const edges = filtered.edges.map((e) => edgeVisual(e, domain.nodeById, highlightInferred));

    visNodes.clear();
    visNodes.add(nodes);
    visEdges.clear();
    visEdges.add(edges);
  }

  async function loadSnapshot() {
    try {
      statusEl.textContent = "Lade Snapshot...";
      loadingEl.style.display = "block";
      errorEl.style.display = "none";

      const response = await fetch(SNAPSHOT_URL, { cache: "no-store" });
      if (!response.ok) throw new Error(`API Fehler ${response.status}`);
      const snapshot = await response.json();

      domain = mergedDomain(snapshot);
      renderGraph();
      statusEl.textContent = `OK - ${new Date().toLocaleTimeString()}`;
      loadingEl.style.display = "none";
    } catch (err) {
      loadingEl.style.display = "none";
      errorEl.style.display = "block";
      errorEl.textContent = `Topology konnte nicht geladen werden: ${err.message}`;
      statusEl.textContent = "Fehler";
    }
  }

  graph.on("click", (params) => {
    const nodeId = params.nodes && params.nodes.length ? params.nodes[0] : null;
    renderDetails(nodeId ? domain.nodeById[nodeId] : null);
  });

  filterHideInactive.addEventListener("change", renderGraph);
  filterHighlightInferred.addEventListener("change", renderGraph);
  filterClientsWithSignal.addEventListener("change", renderGraph);

  // TODO: Replace interval polling with optional websocket updates in a later phase.
  loadSnapshot();
})();
