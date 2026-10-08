#!/usr/bin/env bash
# Build the offline Sri Lanka vector map (srilanka.pmtiles) with Planetiler.
#
# Needs internet (once), Java 21+ and ~4 GB of free disk. Every download is
# resumable, so re-running after a dropped connection continues where it left
# off. See tools/build_map.md for the full procedure and how to verify offline.
#
# Usage:  bash tools/build_map.sh [output_dir]
#   output_dir defaults to %LOCALAPPDATA%/SARManifest/maps (the ground station
#   data dir), or ~/.local/share/SARManifest/maps off Windows.
set -euo pipefail

if [[ -n "${LOCALAPPDATA:-}" ]]; then
    DATA_ROOT="$(cygpath -u "$LOCALAPPDATA" 2>/dev/null || echo "$LOCALAPPDATA")/SARManifest"
else
    DATA_ROOT="$HOME/.local/share/SARManifest"
fi
OUT_DIR="${1:-$DATA_ROOT/maps}"
WORK="$DATA_ROOT/map_build"
SRC="$WORK/sources"
mkdir -p "$SRC" "$OUT_DIR" "$WORK/tmp"

# name|url
DOWNLOADS=(
    "planetiler.jar|https://github.com/onthegomap/planetiler/releases/latest/download/planetiler.jar"
    "sri-lanka-latest.osm.pbf|https://download.geofabrik.de/asia/sri-lanka-latest.osm.pbf"
    "lake_centerline.shp.zip|https://github.com/acalcutt/osm-lakelines/releases/download/v12/lake_centerline.shp.zip"
    "natural_earth_vector.sqlite.zip|https://naciscdn.org/naturalearth/packages/natural_earth_vector.sqlite.zip"
    "water-polygons-split-3857.zip|https://osmdata.openstreetmap.de/download/water-polygons-split-3857.zip"
)

fetch() {
    local name="$1" url="$2" dest="$SRC/$1"
    local want have
    want=$(curl -sIL --max-time 60 "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:"{v=$2} END{print v}')
    for attempt in $(seq 1 40); do
        have=$(stat -c %s "$dest" 2>/dev/null || echo 0)
        if [[ -n "$want" && "$have" == "$want" ]]; then
            echo "[ok] $name ($have bytes)"
            return 0
        fi
        echo "[get] $name attempt $attempt ($have/${want:-?} bytes)"
        curl -L --fail --silent --show-error --connect-timeout 30 \
             --speed-limit 1024 --speed-time 60 -C - -o "$dest" "$url" || true
        # Without a known length, one clean exit is the best signal we get.
        if [[ -z "$want" && -s "$dest" ]]; then return 0; fi
    done
    echo "[fail] $name did not finish after 40 attempts" >&2
    return 1
}

for entry in "${DOWNLOADS[@]}"; do
    fetch "${entry%%|*}" "${entry#*|}"
done

echo "[build] running Planetiler"
cd "$WORK"
# --output must be relative: Planetiler parses "C:/..." as a URI with scheme "C".
java -Xmx2g --enable-native-access=ALL-UNNAMED -jar "$SRC/planetiler.jar" \
    --osm-path="$SRC/sri-lanka-latest.osm.pbf" \
    --lake-centerlines-path="$SRC/lake_centerline.shp.zip" \
    --natural-earth-path="$SRC/natural_earth_vector.sqlite.zip" \
    --water-polygons-path="$SRC/water-polygons-split-3857.zip" \
    --tmpdir="$WORK/tmp" \
    --output=srilanka.pmtiles \
    --force

mv -f "$WORK/srilanka.pmtiles" "$OUT_DIR/srilanka.pmtiles"
echo "[done] $OUT_DIR/srilanka.pmtiles ($(stat -c %s "$OUT_DIR/srilanka.pmtiles") bytes)"
