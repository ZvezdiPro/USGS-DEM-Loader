# USGS 3DEP Downloader

A QGIS plugin that downloads available USGS 3D Elevation Program (3DEP) 1-meter DEM tiles for the current map canvas extent, a rectangle drawn on the map, or a polygon vector layer, mosaics them into a GeoTIFF, and adds the result to the current project.

## Requirements

- QGIS 4.0 through 4.99
- An internet connection to search and download data from the USGS National Map
- USGS 3DEP 1-meter coverage for the selected area (covers most of the mainland US)

The plugin uses QGIS's bundled Python and GDAL libraries; no separate Python environment is required.

## Installation

1. Download the latest plugin ZIP from the project's [Releases](https://github.com/ZvezdiPro/USGS_DEM_Loader/releases) page.
2. In QGIS, open **Plugins → Manage and Install Plugins…** and select **Install from ZIP**.
3. Browse to the downloaded ZIP file and click **Install Plugin**.
4. Once installation is complete, enable **USGS 3DEP Downloader** under **Installed**.

## Usage

1. Click **Download USGS 3DEP DEM** in the toolbar or choose it from the **USGS 3DEP Downloader** menu.
2. Choose the output GeoTIFF path in the dialog, or keep the suggested timestamped path in your Downloads folder. A custom filename is preserved for both the saved file and the layer added to the project.
3. Click **Download Canvas Extent** to use the currently visible map area, or click **Select Area on Map** and drag a rectangle over a custom area. To use a polygon as the download boundary, select a polygon layer from **Polygon layer mask** and click **Download Polygon Layer**. Press **Esc** to cancel rectangle selection.
4. Wait for the available tiles to download and be mosaicked. If the final GeoTIFF is larger than 1 GB, the plugin shows its actual size and lets you keep it and add it to the project, or discard it.

The canvas extent or selected rectangle is transformed to geographic coordinates for the USGS search. Results depend on available 1-meter coverage; if no downloadable tiles cover the area, the plugin reports an error.

## License

This project is licensed under the GNU General Public License, version 3 or (at your option) any later version. See [LICENSE](LICENSE) for details.