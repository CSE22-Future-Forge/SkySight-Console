"""Generate ground_station/ui/web/style.json: an offline OpenMapTiles style.

Light, low-contrast base so victim/team markers stand out. No sprite sheet (POIs
are circles), and only the bundled Noto Sans glyphs. Labels prefer English/Latin
names: MapLibre cannot shape Sinhala/Tamil script correctly.

``{{PMTILES_URL}}`` and ``{{BASE_URL}}`` are filled in by the ground station when
it serves /maps/style.json; the phone app substitutes its own local paths.

    python tools/make_style.py
"""

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "ground_station" / "ui" / "web" / "style.json"
NAME = ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name:latin"], ["get", "name"]]
REG, MED, ITA = ["Noto Sans Regular"], ["Noto Sans Medium"], ["Noto Sans Italic"]
SRC = "openmaptiles"


def fill(id_, layer, color, flt=None, minzoom=None, opacity=1.0):
    lyr = {"id": id_, "type": "fill", "source": SRC, "source-layer": layer,
           "paint": {"fill-color": color, "fill-opacity": opacity}}
    if flt:
        lyr["filter"] = flt
    if minzoom is not None:
        lyr["minzoom"] = minzoom
    return lyr


def line(id_, layer, color, width, flt=None, minzoom=None, dash=None):
    lyr = {"id": id_, "type": "line", "source": SRC, "source-layer": layer,
           "layout": {"line-cap": "round", "line-join": "round"},
           "paint": {"line-color": color, "line-width": width}}
    if flt:
        lyr["filter"] = flt
    if minzoom is not None:
        lyr["minzoom"] = minzoom
    if dash:
        lyr["paint"]["line-dasharray"] = dash
    return lyr


def ramp(*stops):
    return ["interpolate", ["exponential", 1.5], ["zoom"], *stops]


def label(id_, layer, size, color, font=REG, flt=None, minzoom=None, layout=None):
    lyr = {"id": id_, "type": "symbol", "source": SRC, "source-layer": layer,
           "layout": {"text-field": NAME, "text-font": font, "text-size": size, "text-max-width": 8},
           "paint": {"text-color": color, "text-halo-color": "#ffffff", "text-halo-width": 1.4}}
    if flt:
        lyr["filter"] = flt
    if minzoom is not None:
        lyr["minzoom"] = minzoom
    if layout:
        lyr["layout"].update(layout)
    return lyr


def is_class(*classes):
    return ["in", ["get", "class"], ["literal", list(classes)]]


# class, fill, casing, (zoom, width, ...) stops
ROADS = [
    ("motorway", "#fcd6a4", "#e9ac77", (5, 1.2, 10, 3, 14, 9, 18, 30)),
    ("trunk", "#fde9b5", "#e5c48a", (5, 1, 10, 2.6, 14, 8, 18, 28)),
    ("primary", "#fff4c6", "#e0c992", (7, 0.8, 10, 2, 14, 7, 18, 26)),
    ("secondary", "#ffffff", "#cfc6b8", (9, 0.6, 12, 2, 14, 5.5, 18, 22)),
    ("tertiary", "#ffffff", "#cfc6b8", (10, 0.5, 12, 1.4, 14, 4.5, 18, 20)),
    ("minor", "#ffffff", "#d9d2c6", (12, 0.5, 14, 3, 18, 16)),
    ("service", "#ffffff", "#ddd6ca", (13, 0.5, 14, 1.5, 18, 9)),
]
EMERGENCY = ("hospital", "police", "fire_station", "doctors", "clinic")


def build() -> dict:
    layers = [
        {"id": "background", "type": "background", "paint": {"background-color": "#f3f0ea"}},
        fill("landcover-wood", "landcover", "#d5e5c5", is_class("wood", "forest"), opacity=0.85),
        fill("landcover-grass", "landcover", "#e3ecd2", is_class("grass", "meadow", "scrub"), opacity=0.8),
        fill("landcover-wetland", "landcover", "#d7e6e1", is_class("wetland")),
        fill("landcover-sand", "landcover", "#f2e8c8", is_class("sand", "beach")),
        fill("landcover-farmland", "landcover", "#eef0d8", is_class("farmland"), opacity=0.7),
        fill("landuse-residential", "landuse", "#ebe6de", is_class("residential", "suburb", "neighbourhood"),
             opacity=0.8),
        fill("landuse-hospital", "landuse", "#d9eeea", is_class("hospital")),
        fill("landuse-school", "landuse", "#efe8d2", is_class("school", "university", "college")),
        fill("park", "park", "#d3e8c3", opacity=0.7),
        fill("water", "water", "#a8cce4"),
        line("waterway-river", "waterway", "#a8cce4", ramp(8, 0.8, 14, 3, 18, 10), is_class("river", "canal")),
        line("waterway-stream", "waterway", "#a8cce4", ramp(12, 0.5, 14, 1.2, 18, 4),
             is_class("stream", "ditch", "drain"), minzoom=12),
        fill("aeroway-area", "aeroway", "#e6e3ef", ["==", ["geometry-type"], "Polygon"], minzoom=11),
        line("aeroway-runway", "aeroway", "#d6d2e3", ramp(11, 2, 14, 10, 18, 60), is_class("runway"), minzoom=11),
        {"id": "building", "type": "fill", "source": SRC, "source-layer": "building", "minzoom": 13,
         "paint": {"fill-color": "#ddd5ca", "fill-outline-color": "#cbbfb0",
                   "fill-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0, 14, 0.9]}},
        line("boundary-state", "boundary", "#9d8fb3", ramp(4, 0.6, 10, 1.2, 14, 2),
             ["all", [">=", ["get", "admin_level"], 3], ["<=", ["get", "admin_level"], 6],
              ["!=", ["get", "maritime"], 1]], dash=[3, 2]),
        line("boundary-country", "boundary", "#8a77a8", ramp(3, 0.8, 10, 2.5),
             ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1]]),
        line("road-path", "transportation", "#b9a88f", ramp(14, 0.8, 18, 2), is_class("path", "track"),
             minzoom=13, dash=[2, 1.5]),
        line("rail", "transportation", "#a39d96", ramp(9, 0.6, 14, 1.6, 18, 3), is_class("rail", "transit"),
             minzoom=9, dash=[3, 2]),
    ]
    # All casings first, then all fills; smallest class first so major roads draw on top.
    for cls, _fill, casing, stops in reversed(ROADS):
        cas = [s if i % 2 == 0 else round(s + 1.2, 2) for i, s in enumerate(stops)]
        layers.append(line(f"road-{cls}-casing", "transportation", casing, ramp(*cas), is_class(cls),
                           minzoom=stops[0]))
    for cls, fill_c, _casing, stops in reversed(ROADS):
        layers.append(line(f"road-{cls}", "transportation", fill_c, ramp(*stops), is_class(cls), minzoom=stops[0]))

    layers += [
        label("water-name", "water_name", 12, "#4f7fa6", ITA, minzoom=9),
        label("waterway-name", "waterway", 11, "#4f7fa6", ITA, minzoom=13, layout={"symbol-placement": "line"}),
        label("road-name", "transportation_name", ["interpolate", ["linear"], ["zoom"], 13, 10, 16, 13], "#5a5148",
              minzoom=13, layout={"symbol-placement": "line", "text-rotation-alignment": "map"}),
        {"id": "poi-emergency", "type": "circle", "source": SRC, "source-layer": "poi", "minzoom": 13,
         "filter": is_class(*EMERGENCY),
         "paint": {"circle-radius": 4, "circle-color": "#00897b", "circle-stroke-color": "#ffffff",
                   "circle-stroke-width": 1.5}},
        label("poi-emergency-label", "poi", 11, "#00695c", MED, is_class(*EMERGENCY), minzoom=13,
              layout={"text-offset": [0, 1.1], "text-anchor": "top"}),
        label("poi-label", "poi", 10.5, "#6f655b", REG,
              is_class("school", "college", "place_of_worship", "town_hall", "bus", "railway", "fuel"), minzoom=15),
        label("peak", "mountain_peak", 11, "#7a5e3a", ITA, minzoom=11),
        label("place-hamlet", "place", 11, "#5d5850", REG,
              is_class("hamlet", "isolated_dwelling", "neighbourhood", "quarter"), minzoom=13),
        label("place-village", "place", ["interpolate", ["linear"], ["zoom"], 11, 11, 15, 14], "#4a443d", REG,
              is_class("village", "suburb"), minzoom=11),
        label("place-town", "place", ["interpolate", ["linear"], ["zoom"], 8, 11, 14, 17], "#38332d", MED,
              is_class("town"), minzoom=8),
        label("place-city", "place", ["interpolate", ["linear"], ["zoom"], 5, 12, 12, 20], "#2b2722", MED,
              is_class("city"), minzoom=5),
        label("place-country", "place", 14, "#5b4d74", MED, is_class("country"),
              layout={"text-transform": "uppercase", "text-letter-spacing": 0.15}),
    ]
    return {
        "version": 8,
        "name": "SAR Manifest offline (OpenMapTiles)",
        "glyphs": "{{BASE_URL}}/maps/glyphs/{fontstack}/{range}.pbf",
        "sources": {SRC: {"type": "vector", "url": "{{PMTILES_URL}}",
                          "attribution": "© OpenStreetMap contributors, © OpenMapTiles"}},
        "layers": layers,
    }


if __name__ == "__main__":
    style = build()
    OUT.write_text(json.dumps(style, indent=1), encoding="utf-8")
    print(f"{OUT} ({len(style['layers'])} layers)")
