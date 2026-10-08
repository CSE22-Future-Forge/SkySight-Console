# Building the offline Sri Lanka map

The ground station and the phones use one vector map file, `srilanka.pmtiles`
(OpenMapTiles schema), generated from OpenStreetMap with
[Planetiler](https://github.com/onthegomap/planetiler). Build it once on a machine
with internet, and rebuild it before field trials so roads and buildings are current.

Never bulk-download raster tiles from openstreetmap.org: it breaks the OSM tile usage policy.

## Requirements

- Java 21 or newer (`java -version`)
- About 4 GB of free disk space; the downloads are 1.7 GB
- Git Bash (Windows) or any bash shell

## Build

```bash
bash tools/build_map.sh            # output: %LOCALAPPDATA%/SARManifest/maps/srilanka.pmtiles
bash tools/build_map.sh D:/maps    # or a folder of your choice
```

The script downloads these sources into `%LOCALAPPDATA%/SARManifest/map_build/sources/`:

| File | Size | Source |
| --- | --- | --- |
| `planetiler.jar` | 93 MB | GitHub releases |
| `sri-lanka-latest.osm.pbf` | 144 MB | Geofabrik |
| `water-polygons-split-3857.zip` | 931 MB | osmdata.openstreetmap.de |
| `natural_earth_vector.sqlite.zip` | 434 MB | Natural Earth |
| `lake_centerline.shp.zip` | 81 MB | osm-lakelines |

Every download resumes, so on a weak connection you can simply run the script again.
Once the files are downloaded, Planetiler takes about 3 minutes on a laptop and writes
a file of about 110 MB.

To refresh only the OSM data, delete `sri-lanka-latest.osm.pbf` and run the script again.

## Install

- **Laptop:** the ground station serves `<data dir>/maps/srilanka.pmtiles`. To use a
  different path, change the `map_file` setting.
- **Phones:** the app downloads the file during pairing from `/maps/srilanka.pmtiles`,
  resuming with HTTP Range and checking it against the SHA-256 from `/maps/manifest`.
  To copy it over USB instead, put it in the app's documents folder.

## Verify offline

1. Disable the laptop's network adapters, or switch the phone to airplane mode.
2. Start the ground station and open the map.
3. Pan around Sri Lanka at several zoom levels. Roads, water and place labels must all
   render. If shapes appear but there are no labels, the glyph files are missing.
