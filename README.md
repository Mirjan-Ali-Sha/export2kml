# Export 2KML

**Version:** 0.1  
**Author:** Mirjan Ali Sha  
**License:** MIT  

Export 2KML is a QGIS 3.x plugin that allows you to batch‐export any combination of vector and raster layers into a single KML (for vector‐only exports) or KMZ (when rasters are included). Raster layers are rendered using their current QGIS styling (QML), saved as opaque PNG overlays, and embedded in the KMZ so that Google Earth (or any KML‐aware client) will display them correctly. Vector layers become `<Placemark>` entries with customizable KML properties (Name, description, timestamp, etc.).

---

## Features

- **Batch Export**: Select multiple vector and/or raster layers in one dialog.
- **Styled Rasters**: Renders each raster layer with its QGIS‐applied symbology into an opaque PNG.  **(*Not working Properly, try to resolve all raster related issues)**
- **KML Properties**: For each layer, assign KML fields (Name, description, timestamp, begin, end, altitudeMode, tessellate, extrude, visibility, drawOrder, icon) via editable dropdowns.
- **Auto‐Switch to KMZ**: If any raster is checked, “KMZ” format is automatically enforced; otherwise you can export to plain KML.
- **Progress Bar**: Displays rendering progress when processing raster layers.
- **Always-On-Top Dialog**: The plugin window stays on top of QGIS (but not other applications).
- **Free & Open Source**: Released under the MIT License; no warranties, use at your own risk.

---

## Requirements 

- QGIS 3.x (3.0 or later)
- GDAL with libkml support (optional, but recommended for native KML/KMZ writing).  **[Not Required for this version]**
  To verify on macOS/Linux/Windows, run:
  ```bash
  ogrinfo --formats | grep -i libkml
