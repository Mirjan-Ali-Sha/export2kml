# -*- coding: utf-8 -*-

def classFactory(iface):
    """Load ExportKml class from file ExportKml."""
    from .export2kml import Export2KML
    return Export2KML(iface)