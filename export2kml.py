# -*- coding: utf-8 -*-
"""
export2kml.py
QGIS plugin: Export 2KML

Batch‐export selected vector and raster layers to a single KML or KMZ.
Vectors become <Placemark> entries; rasters are rendered with their QML style,
saved as opaque PNGs, and embedded as <GroundOverlay> in the KMZ.
"""

import os
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from qgis.PyQt import uic
from qgis.PyQt.QtWidgets import (
    QAction,
    QFileDialog,
    QTableWidgetItem,
    QCheckBox,
    QComboBox,
    QMessageBox
)
from qgis.PyQt.QtGui import QIcon, QImage, QPainter
from qgis.PyQt.QtCore import Qt, QSize
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsRasterLayer,
    QgsMapSettings,
    QgsMapRendererParallelJob
)
from osgeo import ogr, gdal, osr

# Ensure GDAL exceptions are enabled
gdal.UseExceptions()
ogr.UseExceptions()

# List of KML properties (lowercased when writing into props)
KML_PROPS = [
    'Name', 'description', 'timestamp', 'begin', 'end',
    'altitudeMode', 'tessellate', 'extrude', 'visibility',
    'drawOrder', 'icon'
]


def make_kml_root():
    """
    Create the <kml><Document> root.
    """
    kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
    doc = ET.SubElement(kml, 'Document')
    return kml, doc


def add_vector_layer(doc, path, props):
    """
    Add each feature in a vector layer as a <Placemark>.
    props may include:
      - folder_name: string for <Folder><name>
      - name: field name or literal for <Placemark><name>
      - description: field name or literal for <Placemark><description>
      - fields: list of field names to include under <ExtendedData>
    """
    ds = ogr.Open(path)
    if ds is None:
        raise RuntimeError(f'Cannot open vector {path}')
    layer = ds.GetLayer(0)

    # Determine folder label: props.folder_name or filename
    folder_label = props.get('folder_name') or os.path.splitext(os.path.basename(path))[0]
    folder = ET.SubElement(doc, 'Folder')
    ET.SubElement(folder, 'name').text = folder_label

    # Cache available fields from the layer
    ld = layer.GetLayerDefn()
    avail = [ld.GetFieldDefn(i).GetName() for i in range(ld.GetFieldCount())]

    for feat in layer:
        pm = ET.SubElement(folder, 'Placemark')

        # <name>
        if props.get('name'):
            nm = props['name']
            val = feat.GetField(nm) if nm in avail else nm
            ET.SubElement(pm, 'name').text = str(val)

        # <description>
        if props.get('description'):
            d = props['description']
            val = feat.GetField(d) if d in avail else d
            ET.SubElement(pm, 'description').text = str(val)

        # <ExtendedData> with specified fields
        if props.get('fields'):
            ext = ET.SubElement(pm, 'ExtendedData')
            for fld in props['fields']:
                if fld in avail:
                    dt = ET.SubElement(ext, 'Data', name=fld)
                    ET.SubElement(dt, 'value').text = str(feat.GetField(fld))

        # Geometry → KML
        geom = feat.GetGeometryRef()
        if geom:
            k = ogr.CreateGeometryFromWkb(geom.ExportToWkb())
            wrap = ET.fromstring(f'<r>{k.ExportToKML()}</r>')
            for c in wrap:
                pm.append(c)

    ds = None


def add_raster_layer(doc, path, props, rasters_dir):
    """
    Render the styled raster layer through QGIS (so its QML style applies),
    save as an opaque PNG (no alpha channel), compute its geographic bounds,
    and add it as a <GroundOverlay> in the KML Document.

    Returns the path to the saved PNG (for inclusion in KMZ).
    """
    # 1) Ensure output folder exists
    os.makedirs(rasters_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    img_name = f'{base}.png'
    img_path = os.path.join(rasters_dir, img_name)

    # 2) Load the raster layer (with QML styling)
    rlayer = QgsRasterLayer(path, base)
    if not rlayer.isValid():
        raise RuntimeError(f'Cannot load raster {path}')

    # 3) Set up map settings (extent & size)
    extent = rlayer.extent()
    width, height = rlayer.width(), rlayer.height()
    ms = QgsMapSettings()
    ms.setLayers([rlayer])
    ms.setExtent(extent)
    ms.setOutputSize(QSize(width, height))

    # 4) Start a parallel render job
    job = QgsMapRendererParallelJob(ms)
    job.start()
    job.waitForFinished()

    # 5) Pull the rendered image (this has an alpha channel!)
    img: QImage = job.renderedImage()

    # 6) Create an opaque RGB image (drop alpha) on white background
    rgb = QImage(width, height, QImage.Format_RGB32)
    rgb.fill(Qt.white)
    painter = QPainter(rgb)
    painter.drawImage(0, 0, img)
    painter.end()

    # 7) Save as PNG
    rgb.save(img_path, 'PNG')

    # 8) Compute geographic bounds in EPSG:4326
    ds = gdal.Open(path)
    if ds is None:
        raise RuntimeError(f'Cannot open raster {path}')
    src = osr.SpatialReference(wkt=ds.GetProjection())
    tgt = osr.SpatialReference(); tgt.ImportFromEPSG(4326)
    if not src.IsSame(tgt):
        # Warp on the fly to geographic (VRT)
        ds = gdal.Warp('', ds, format='VRT', dstSRS='EPSG:4326')
    gt = ds.GetGeoTransform()
    xs, ys = ds.RasterXSize, ds.RasterYSize
    minx, maxy = gt[0], gt[3]
    maxx = minx + gt[1] * xs
    miny = maxy + gt[5] * ys
    ds = None

    # 9) Create <GroundOverlay> in the KML
    go = ET.SubElement(doc, 'GroundOverlay')
    ET.SubElement(go, 'visibility').text = '1'
    label = props.get('folder_name') or props.get('name') or base
    ET.SubElement(go, 'name').text = label
    icon = ET.SubElement(go, 'Icon')
    ET.SubElement(icon, 'href').text = f'rasters/{img_name}'
    ll = ET.SubElement(go, 'LatLonBox')
    ET.SubElement(ll, 'north').text = str(maxy)
    ET.SubElement(ll, 'south').text = str(miny)
    ET.SubElement(ll, 'east').text = str(maxx)
    ET.SubElement(ll, 'west').text = str(minx)

    return img_path


class Export2KML:
    def __init__(self, iface):
        """
        iface: a reference to the QGIS application (QgisInterface).
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.added_file_layers = []  # user‐added raster/vector files

    def initGui(self):
        """
        Called by QGIS when loading the plugin. We:
        1) load our .ui
        2) create a QAction (menu + toolbar)
        3) hook up button signals
        """
        ui_path = os.path.join(self.plugin_dir, 'form.ui')
        # Load the dialog (no base instance)
        self.dlg = uic.loadUi(ui_path)

        # Create the QAction with our plugin icon
        icon = QIcon(os.path.join(self.plugin_dir, 'export2kml.png'))
        self.action = QAction(icon, 'Export 2KML…', self.iface.mainWindow())
        self.action.triggered.connect(self.show_dialog)

        # Add to the Plugins menu and toolbar
        self.iface.addPluginToMenu('&Export 2KML', self.action)
        self.iface.addToolBarIcon(self.action)

        # Hook up the UI buttons
        self.dlg.btnBrowseFiles.clicked.connect(self.add_layer)
        self.dlg.btnSelectOutput.clicked.connect(self.select_output)
        self.dlg.btnRun.clicked.connect(self.run_export)
        self.dlg.btnCancel.clicked.connect(self.dlg.close)

    def unload(self):
        """
        Called by QGIS when unloading the plugin. We remove our menu item + toolbar icon.
        """
        self.iface.removePluginMenu('&Export 2KML', self.action)
        self.iface.removeToolBarIcon(self.action)

    def show_dialog(self):
        """
        Re‐build the layer table each time, adjust KML/KMZ radios, and show the dialog.
        We also set the dialog as a Qt.Tool so it stays on top of QGIS (not on top of all apps).
        """
        self.populate_table()
        self.update_format_options()
        self.dlg.setParent(self.iface.mainWindow(), Qt.Tool)
        self.dlg.setWindowFlags(self.dlg.windowFlags() | Qt.Tool)
        self.dlg.show()

    def populate_table(self):
        """
        Populate the QTableWidget (`self.dlg.tableLayers`) with:
         - a checkbox for each layer
         - layer name
         - layer type (Raster/Vector)
         - one editable QComboBox per KML property
        """
        project_layers = list(QgsProject.instance().mapLayers().values())
        all_layers = project_layers + self.added_file_layers
        tbl = self.dlg.tableLayers

        # Configure columns: [Export?, Layer Name, Type] + KML_PROPS
        tbl.setColumnCount(3 + len(KML_PROPS))
        tbl.setHorizontalHeaderLabels(['Export?', 'Layer Name', 'Type'] + KML_PROPS)
        tbl.setRowCount(len(all_layers))

        for i, lyr in enumerate(all_layers):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(True)
            cb.stateChanged.connect(self.update_format_options)
            tbl.setCellWidget(i, 0, cb)

            # Layer name (non‐editable)
            item_name = QTableWidgetItem(lyr.name())
            item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
            tbl.setItem(i, 1, item_name)

            # Type (Raster/Vector)
            typ = 'Raster' if isinstance(lyr, QgsRasterLayer) else 'Vector'
            item_type = QTableWidgetItem(typ)
            item_type.setFlags(item_type.flags() & ~Qt.ItemIsEditable)
            tbl.setItem(i, 2, item_type)

            # One editable combo per KML property
            for j in range(len(KML_PROPS)):
                combo = QComboBox()
                combo.addItem('<None>')
                if isinstance(lyr, QgsVectorLayer):
                    # Populate with all field names
                    combo.addItems([f.name() for f in lyr.fields()])
                combo.setEditable(True)
                tbl.setCellWidget(i, j + 3, combo)

    def update_format_options(self):
        """
        If any raster is checked, we disable the “KML” radio and force “KMZ.”
        Otherwise, re‐enable “KML.”
        """
        tbl = self.dlg.tableLayers
        any_raster = False
        project_layers = list(QgsProject.instance().mapLayers().values())
        all_layers = project_layers + self.added_file_layers

        for i, lyr in enumerate(all_layers):
            cb = tbl.cellWidget(i, 0)
            if isinstance(lyr, QgsRasterLayer) and cb.isChecked():
                any_raster = True
                break

        # Disable/enable radios accordingly
        self.dlg.radioKML.setEnabled(not any_raster)
        if any_raster:
            self.dlg.radioKMZ.setChecked(True)

    def add_layer(self):
        """
        Show a file dialog so the user can pick a shapefile/GeoPackage or TIFF. 
        Loaded files appear in the same table alongside project layers.
        """
        path, _ = QFileDialog.getOpenFileName(
            self.dlg, 'Browse Input Files', '',
            'Vector (*.shp *.gpkg);;Raster (*.tif *.tiff);;All (*.*)'
        )
        if not path:
            return

        # Create an in‐memory QgsRasterLayer or QgsVectorLayer
        if path.lower().endswith(('.tif', '.tiff')):
            lyr = QgsRasterLayer(path, os.path.basename(path))
        else:
            lyr = QgsVectorLayer(path, os.path.basename(path), 'ogr')

        if not lyr.isValid():
            QMessageBox.critical(self.iface.mainWindow(), 'Error', 'Cannot load layer')
            return

        self.added_file_layers.append(lyr)
        self.populate_table()
        self.update_format_options()

    def select_output(self):
        """
        Let the user choose where to save KML/KMZ. 
        If a raster is selected, .kmz is forced.
        """
        fmt = 'kmz' if self.dlg.radioKMZ.isChecked() else 'kml'
        path, _ = QFileDialog.getSaveFileName(self.dlg, 'Output File', '', f'*.{fmt}')
        if not path:
            return
        if not path.lower().endswith(f'.{fmt}'):
            path += f'.{fmt}'
        self.output_path = path
        self.dlg.editOutput.setText(path)

    def run_export(self):
        """
        Gather all checked layers, read each layer’s KML properties from the table,
        write a combined doc.kml in a temp folder, then either save as .kml or package
        into .kmz (including any rendered PNGs for rasters). 
        Shows and updates a progress bar if present.
        """
        try:
            out = getattr(self, 'output_path', None)
            if not out:
                raise ValueError('Please select an output file')

            # 1) Create KML root
            kml, doc = make_kml_root()
            tmp = tempfile.mkdtemp()
            rasters_dir = os.path.join(tmp, 'rasters')
            os.makedirs(rasters_dir, exist_ok=True)
            collected = []

            # 2) Progress bar (optional)
            progress = getattr(self.dlg, 'progressBar', None)
            if progress:
                progress.setValue(0)
                progress.setVisible(True)

            project_layers = list(QgsProject.instance().mapLayers().values())
            all_layers = project_layers + self.added_file_layers
            tbl = self.dlg.tableLayers

            # Count rasters to update progress appropriately
            total_rasters = sum(
                1 for i, lyr in enumerate(all_layers)
                if isinstance(lyr, QgsRasterLayer) and tbl.cellWidget(i, 0).isChecked()
            )
            raster_count = 0

            # 3) Loop through each layer in the combined list
            for i, lyr in enumerate(all_layers):
                if not tbl.cellWidget(i, 0).isChecked():
                    continue  # skip unchecked

                path = lyr.source()
                props = {}
                # Read each KML property from the combo boxes
                for j, prop in enumerate(KML_PROPS, start=3):
                    text = tbl.cellWidget(i, j).currentText().strip()
                    if text and text != '<None>':
                        props[prop.lower()] = text

                if isinstance(lyr, QgsRasterLayer):
                    # Render+embed as PNG
                    png_path = add_raster_layer(doc, path, props, rasters_dir)
                    collected.append(png_path)

                    raster_count += 1
                    if progress and total_rasters:
                        pct = int(raster_count / total_rasters * 100)
                        progress.setValue(pct)

                else:
                    # Add vector features as Placemarks
                    add_vector_layer(doc, path, props)

            # 4) Write out the KML file
            kml_file = os.path.join(tmp, 'doc.kml')
            ET.ElementTree(kml).write(kml_file, encoding='utf-8', xml_declaration=True)

            # 5) Decide KML vs KMZ
            if collected:
                # KMZ: add doc.kml + all PNGs under rasters/
                with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(kml_file, 'doc.kml')
                    for f in collected:
                        zf.write(f, f'rasters/{os.path.basename(f)}')
            else:
                # Only vectors: save as .kml directly
                os.replace(kml_file, out)

            if progress:
                progress.setVisible(False)

            self.iface.messageBar().pushMessage('Export 2KML',
                                                f'Exported to {out}',
                                                level=Qgis.Info)
            self.dlg.close()

        except Exception as e:
            QMessageBox.critical(self.iface.mainWindow(), 'Error', str(e))
