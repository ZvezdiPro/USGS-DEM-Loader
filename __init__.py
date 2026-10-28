def classFactory(iface):
    from .usgs_dem_loader import USGSDEMPlugin

    return USGSDEMPlugin(iface)
