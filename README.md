# USGS 3DEP Downloader

A QGIS plugin that downloads USGS 3D Elevation Program (3DEP) DEM tiles at 1-meter, 1/9 arc-second (about 3-meter), 1/3 arc-second (about 10-meter), or 1 arc-second (about 30-meter) resolution for the current map canvas extent, a rectangle drawn on the map, or a polygon vector layer, mosaics them into a GeoTIFF, and adds the result to the current project.

## Requirements

- QGIS 4.0 through 4.99
- An internet connection to search and download data from the USGS National Map
- USGS 3DEP coverage for the selected product and area

The plugin uses QGIS's bundled Python and GDAL libraries; no separate Python environment is required.

## Installation

1. Download the latest plugin ZIP from the project's [Releases](https://github.com/ZvezdiPro/USGS_DEM_Loader/releases) page.
2. In QGIS, open **Plugins → Manage and Install Plugins…** and select **Install from ZIP**.
3. Browse to the downloaded ZIP file and click **Install Plugin**.
4. Once installation is complete, enable **USGS 3DEP Downloader** under **Installed**.

## Usage

1. Click **Download USGS 3DEP DEM** in the toolbar or choose it from the **USGS 3DEP Downloader** menu.
2. Choose a **DEM Elevation Product** in the dialog. The default is the 1-meter DEM, and you can change the default in **Plugins → USGS 3DEP Downloader → DEM and Large File Settings…**. Choose the output GeoTIFF path, or keep the suggested timestamped path in your Downloads folder. A custom filename is preserved for both the saved file and the layer added to the project.
3. Click **Download Canvas Extent** to use the currently visible map area, or click **Select Area on Map** and drag a rectangle over a custom area. To use a polygon as the download boundary, select a polygon layer from **Polygon layer mask** and click **Download Polygon Layer**. Press **Esc** to cancel rectangle selection.
4. By default, if the estimated total source-tile download is larger than 3 GB, confirm that you want to proceed. Wait for the available tiles to download and be mosaicked. If the final GeoTIFF is larger than 1 GB, the plugin shows its actual size and lets you keep it and add it to the project, or discard it.

The canvas extent or selected rectangle is transformed to geographic coordinates for the USGS search. Coverage varies by product and area; if no downloadable tiles cover the selected area, the plugin reports an error.

The default DEM product and the download or final-raster warning settings are saved in QGIS. In **DEM and Large File Settings…**, you can change the default product, adjust warning thresholds, or disable either warning.

## License

This project is licensed under the GNU General Public License, version 3 or (at your option) any later version. See [LICENSE](LICENSE) for details.