import json
import os
import tempfile
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from qgis.PyQt.QtCore import QThread, pyqtSignal


class DEMDownloadWorker(QThread):
    progressChanged = pyqtSignal(int, int, str)
    completed = pyqtSignal(str)
    failed = pyqtSignal(str)

    API_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
    DATASET = "Digital Elevation Model (DEM) 1 meter"

    def __init__(self, min_x, min_y, max_x, max_y, output_path, parent=None):
        super().__init__(parent)
        self.bounds = (min_x, min_y, max_x, max_y)
        self.output_path = output_path

    def run(self):
        try:
            products = self._find_products()
            products = [product for product in products if product.get("downloadURL")]
            if not products:
                raise RuntimeError(
                    "No USGS 3DEP 1-meter DEM tiles are available for this area."
                )

            with tempfile.TemporaryDirectory(prefix="usgs_3dep_") as work_dir:
                tile_paths = []
                total = len(products)
                for index, product in enumerate(products, start=1):
                    if self.isInterruptionRequested():
                        raise RuntimeError("The download was interrupted.")
                    url = product.get("downloadURL")
                    tile_path = self._download_tile(url, work_dir, index)
                    tile_paths.append(tile_path)
                    self.progressChanged.emit(
                        index,
                        total,
                        "Downloaded tile {} of {}.".format(index, total),
                    )

                if not tile_paths:
                    raise RuntimeError(
                        "USGS returned DEM records without downloadable GeoTIFFs."
                    )
                if self.isInterruptionRequested():
                    raise RuntimeError("The download was interrupted.")
                self._mosaic(tile_paths)

            self.progressChanged.emit(
                total, total, "Adding the completed DEM to the map..."
            )
            self.completed.emit(self.output_path)
        except Exception as error:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except OSError as cleanup_error:
                    error = RuntimeError(
                        "{}; could not remove the partial output: {}".format(
                            error, cleanup_error
                        )
                    )
            self.failed.emit(str(error))

    def _find_products(self):
        products = []
        offset = 0
        while True:
            if self.isInterruptionRequested():
                raise RuntimeError("The download was interrupted.")
            query = urlencode(
                {
                    "datasets": self.DATASET,
                    "bbox": ",".join(str(value) for value in self.bounds),
                    "prodFormats": "GeoTIFF",
                    "max": 1000,
                    "offset": offset,
                }
            )
            request = Request(
                "{}?{}".format(self.API_URL, query),
                headers={"User-Agent": "USGS-3DEP-QGIS-Plugin"},
            )
            with urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))

            page = data.get("items", [])
            products.extend(page)
            total = int(data.get("total", len(products)))
            if offset + len(page) >= total:
                return products
            if not page:
                raise RuntimeError("The USGS API returned an incomplete result page.")
            offset += len(page)

    def _download_tile(self, url, work_dir, index):
        filename = os.path.basename(urlparse(url).path)
        if not filename:
            filename = "tile_{}.tif".format(index)
        destination = os.path.join(work_dir, "{}_{}".format(index, filename))
        request = Request(url, headers={"User-Agent": "USGS-3DEP-QGIS-Plugin"})
        with urlopen(request, timeout=60) as response, open(destination, "wb") as target:
            while True:
                if self.isInterruptionRequested():
                    raise RuntimeError("The download was interrupted.")
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
        return destination

    def _mosaic(self, tile_paths):
        from osgeo import gdal

        gdal.UseExceptions()
        first_tile = gdal.Open(tile_paths[0], gdal.GA_ReadOnly)
        if first_tile is None or not first_tile.GetProjection():
            raise RuntimeError("The downloaded DEM has no usable coordinate system.")

        options = gdal.WarpOptions(
            format="GTiff",
            dstSRS=first_tile.GetProjection(),
            outputBounds=self.bounds,
            outputBoundsSRS="EPSG:4326",
            creationOptions=["TILED=YES", "COMPRESS=LZW"],
        )
        result = gdal.Warp(self.output_path, tile_paths, options=options)
        first_tile = None
        if result is None:
            raise RuntimeError("GDAL could not mosaic the downloaded DEM tiles.")
        result = None
