/* Coordinator map: MapLibre GL JS + PMTiles, driven from Python over QWebChannel.
 *
 * Python -> JS (bridge signals, JSON strings):
 *   victimsChanged({points, errors})   teamsChanged({points, trails})   uavChanged(feature|null)
 *   flyToRequested(lat, lon, zoom)     selectionChanged(id)             fitRequested([[w,s],[e,n]])
 * JS -> Python (bridge slots):
 *   mapReady()  onVictimClicked(id)  onAddVictimAt(lat, lon)  snapshot() -> JSON of the latest payloads
 *
 * Also works in a plain browser (no bridge) for debugging: window.sar.* takes the same payloads.
 */
(function () {
  "use strict";

  var STATUS_COLOR = ["match", ["get", "status"],
    "open", "#e53935", "ongoing", "#fb8c00", "completed", "#43a047",
    "dismissed", "#9e9e9e", "pending", "#8e24aa", "#607d8b"];
  var EMPTY = { type: "FeatureCollection", features: [] };

  var bridge = null;
  var ready = false;
  var pending = {};
  var selectedId = "";
  var ctxLngLat = null;

  maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);

  var map = new maplibregl.Map({
    container: "map",
    style: "/maps/style.json",
    center: [80.7, 7.85],
    zoom: 7,
    minZoom: 5,
    maxZoom: 18,
    maxBounds: [[77.5, 4.0], [84.5, 11.5]],
    attributionControl: { compact: true },
    dragRotate: false,
    pitchWithRotate: false,
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 140 }), "bottom-left");

  function showBanner(text) {
    var el = document.getElementById("banner");
    el.textContent = text;
    el.hidden = !text;
  }

  // Inside the ground station a Qt strip above the map offers Import / Download instead.
  var inGui = /[?&]gui=1/.test(location.search);
  fetch("/maps/manifest").then(function (r) {
    if (!r.ok && !inGui) showBanner("No offline map installed (see tools/build_map.md). Markers still work.");
  }).catch(function () { showBanner("Map server not reachable."); });

  function uavIcon() {
    var s = 56, c = document.createElement("canvas");
    c.width = c.height = s;
    var g = c.getContext("2d");
    g.translate(s / 2, s / 2);
    g.beginPath();
    g.moveTo(0, -24); g.lineTo(17, 20); g.lineTo(0, 11); g.lineTo(-17, 20); g.closePath();
    g.fillStyle = "#1a3d7c"; g.strokeStyle = "#ffffff"; g.lineWidth = 4; g.lineJoin = "round";
    g.stroke(); g.fill();
    return g.getImageData(0, 0, s, s);
  }

  map.on("load", function () {
    map.addImage("uav", uavIcon(), { pixelRatio: 2 });
    map.addSource("trails", { type: "geojson", data: EMPTY });
    map.addSource("victim-errors", { type: "geojson", data: EMPTY });
    map.addSource("victims", { type: "geojson", data: EMPTY });
    map.addSource("teams", { type: "geojson", data: EMPTY });
    map.addSource("uav", { type: "geojson", data: EMPTY });

    map.addLayer({ id: "trails", type: "line", source: "trails",
      layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": 2.5, "line-opacity": 0.75 } });
    map.addLayer({ id: "victim-errors", type: "fill", source: "victim-errors",
      paint: { "fill-color": STATUS_COLOR, "fill-opacity": 0.13 } });
    map.addLayer({ id: "victim-errors-outline", type: "line", source: "victim-errors",
      paint: { "line-color": STATUS_COLOR, "line-width": 1, "line-opacity": 0.55 } });
    map.addLayer({ id: "victim-selected", type: "circle", source: "victims",
      filter: ["==", ["get", "id"], ""],
      paint: { "circle-radius": 13, "circle-color": "rgba(0,0,0,0)",
               "circle-stroke-color": "#1565c0", "circle-stroke-width": 3 } });
    map.addLayer({ id: "victims", type: "circle", source: "victims",
      paint: {
        "circle-radius": 7.5,
        "circle-color": ["case", ["==", ["get", "status"], "pending"], "#ffffff", STATUS_COLOR],
        "circle-stroke-color": ["case", ["==", ["get", "status"], "pending"], "#8e24aa", "#ffffff"],
        "circle-stroke-width": ["case", ["==", ["get", "status"], "pending"], 3, 2],
      } });
    map.addLayer({ id: "victim-labels", type: "symbol", source: "victims",
      layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Medium"], "text-size": 11,
                "text-offset": [0, 1.25], "text-anchor": "top", "text-optional": true },
      paint: { "text-color": "#222", "text-halo-color": "#fff", "text-halo-width": 1.6 } });
    map.addLayer({ id: "teams", type: "circle", source: "teams",
      paint: { "circle-radius": 6.5, "circle-color": ["get", "color"],
               "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
    map.addLayer({ id: "team-labels", type: "symbol", source: "teams",
      layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Medium"], "text-size": 11,
                "text-offset": [0, -1.3], "text-anchor": "bottom", "text-optional": true },
      paint: { "text-color": ["get", "color"], "text-halo-color": "#fff", "text-halo-width": 1.6 } });
    map.addLayer({ id: "uav", type: "symbol", source: "uav",
      layout: { "icon-image": "uav", "icon-rotate": ["get", "heading"], "icon-rotation-alignment": "map",
                "icon-allow-overlap": true, "icon-ignore-placement": true,
                "text-field": ["get", "label"], "text-font": ["Noto Sans Medium"], "text-size": 10,
                "text-offset": [0, 1.9], "text-anchor": "top", "text-optional": true },
      paint: { "text-color": "#1a3d7c", "text-halo-color": "#fff", "text-halo-width": 1.6 } });

    ready = true;
    Object.keys(pending).forEach(function (k) { apply(k, pending[k]); });
    pending = {};
    if (bridge) bridge.mapReady();
  });

  function setData(source, data) {
    var src = map.getSource(source);
    if (src) src.setData(data || EMPTY);
  }

  function apply(kind, data) {
    if (!ready) { pending[kind] = data; return; }
    if (kind === "victims") {
      setData("victims", data && data.points);
      setData("victim-errors", data && data.errors);
    } else if (kind === "teams") {
      setData("teams", data && data.points);
      setData("trails", data && data.trails);
    } else if (kind === "uav") {
      setData("uav", data ? { type: "FeatureCollection", features: [data] } : EMPTY);
    } else if (kind === "selection") {
      select(data);
    }
  }

  function select(id) {
    selectedId = id || "";
    if (ready) map.setFilter("victim-selected", ["==", ["get", "id"], selectedId]);
  }

  function flyTo(lat, lon, zoom) {
    map.flyTo({ center: [lon, lat], zoom: Math.max(map.getZoom(), zoom || 15), speed: 1.6 });
  }

  // ---------------------------------------------------------------- input ---
  map.on("click", "victims", function (e) {
    var id = e.features[0].properties.id;
    select(id);
    if (bridge) bridge.onVictimClicked(id);
  });
  ["victims", "teams"].forEach(function (layer) {
    map.on("mouseenter", layer, function () { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", layer, function () { map.getCanvas().style.cursor = ""; });
  });
  map.on("click", "teams", function (e) {
    var p = e.features[0].properties;
    new maplibregl.Popup({ closeButton: false })
      .setLngLat(e.lngLat).setHTML("<b>" + p.name + "</b><br>" + p.detail).addTo(map);
  });

  var coords = document.getElementById("coords");
  map.on("mousemove", function (e) {
    coords.textContent = e.lngLat.lat.toFixed(5) + ", " + e.lngLat.lng.toFixed(5);
  });

  var ctx = document.getElementById("ctx");
  map.on("contextmenu", function (e) {
    ctxLngLat = e.lngLat;
    ctx.style.left = e.point.x + "px";
    ctx.style.top = e.point.y + "px";
    ctx.hidden = false;
  });
  function hideCtx() { ctx.hidden = true; }
  map.on("movestart", hideCtx);
  map.on("click", hideCtx);
  document.getElementById("ctx-add").addEventListener("click", function () {
    hideCtx();
    if (bridge && ctxLngLat) bridge.onAddVictimAt(ctxLngLat.lat, ctxLngLat.lng);
  });
  document.getElementById("ctx-copy").addEventListener("click", function () {
    hideCtx();
    if (ctxLngLat && navigator.clipboard) {
      navigator.clipboard.writeText(ctxLngLat.lat.toFixed(6) + ", " + ctxLngLat.lng.toFixed(6));
    }
  });

  // ------------------------------------------------------------ public API ---
  window.sar = {
    victims: function (d) { apply("victims", d); },
    teams: function (d) { apply("teams", d); },
    uav: function (d) { apply("uav", d); },
    select: select,
    flyTo: flyTo,
    fit: function (b) { map.fitBounds(b, { padding: 60, maxZoom: 16 }); },
  };

  if (typeof QWebChannel !== "undefined" && typeof qt !== "undefined") {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.bridge;
      bridge.victimsChanged.connect(function (s) { apply("victims", JSON.parse(s)); });
      bridge.teamsChanged.connect(function (s) { apply("teams", JSON.parse(s)); });
      bridge.uavChanged.connect(function (s) { apply("uav", JSON.parse(s)); });
      bridge.selectionChanged.connect(function (id) { select(id); });
      bridge.flyToRequested.connect(function (lat, lon, zoom) { flyTo(lat, lon, zoom); });
      bridge.fitRequested.connect(function (s) { window.sar.fit(JSON.parse(s)); });
      bridge.snapshot(function (s) {
        var snap = JSON.parse(s || "{}");
        Object.keys(snap).forEach(function (k) { apply(k, snap[k]); });
      });
      if (ready) bridge.mapReady();
    });
  }
})();
