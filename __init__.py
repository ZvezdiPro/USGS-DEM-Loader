def classFactory(iface):
    # Create and return the QGIS plugin instance.
    from .usgs_dem_loader import USGSDEMPlugin

    return USGSDEMPlugin(iface)
