import gc
import json
import os
import shutil
import tempfile
import time
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from qgis.PyQt.QtCore import QThread, pyqtSignal # type: ignore


class DEMDownloadWorker(QThread):
    progressChanged = pyqtSignal(int, int, str)
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str)
    cleanupWarning = pyqtSignal(str)

    API_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
    DATASET = "Digital Elevation Model (DEM) 1 meter"
    LARGE_DOWNLOAD_WARNING_THRESHOLD = 1_000_000_000

    def __init__(
        self, min_x, min_y, max_x, max_y, output_path, parent=None, cutline_path=None
    ):
        # Initialize a worker for downloading DEM tiles within geographic bounds.
        super().__init__(parent)
        self.bounds = (min_x, min_y, max_x, max_y)
        self.output_path = output_path
        self.cutline_path = cutline_path

    def run(self):
        # Fetch matching tiles, mosaic them, and emit completion or failure signals.
        work_dir = None
        try:
            products = self._find_products()
            products = [product for product in products if product.get("downloadURL")]
            if not products:
                raise RuntimeError(
                    "No USGS 3DEP 1-meter DEM tiles are available for this area."
                )

            work_dir = tempfile.mkdtemp(prefix="usgs_3dep_")
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
            if self.isInterruptionRequested():
                raise RuntimeError("The download was interrupted.")
            output_size = os.path.getsize(self.output_path)
        except Exception as error:
            cleanup_errors = []
            if work_dir is not None:
                cleanup_error = self._cleanup_work_dir(work_dir)
                if cleanup_error is not None:
                    cleanup_errors.append(
                        "Could not remove temporary download files: {}".format(
                            cleanup_error
                        )
                    )
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except OSError as cleanup_error:
                    error = RuntimeError(
                        "{}; could not remove the partial output: {}".format(
                            error, cleanup_error
                        )
                    )
            if cleanup_errors:
                error = RuntimeError(
                    "{}; {}".format(error, "; ".join(cleanup_errors))
                )
            self.failed.emit(str(error))
            return

        if work_dir is not None:
            cleanup_error = self._cleanup_work_dir(work_dir)
            if cleanup_error is not None:
                self.cleanupWarning.emit(
                    "DEM was created successfully, but temporary files could not "
                    "be removed from '{}': {}".format(work_dir, cleanup_error)
                )

        self.progressChanged.emit(
            total, total, "Finalizing the completed DEM..."
        )
        self.completed.emit(self.output_path, output_size)

    @staticmethod
    def _cleanup_work_dir(work_dir):
        # Retry transient Windows file locks without treating cleanup as download failure.
        gc.collect()
        for attempt in range(5):
            try:
                shutil.rmtree(work_dir)
                return None
            except OSError as error:
                if attempt == 4:
                    return error
                time.sleep(0.2)

    def _find_products(self):
        # Query the USGS API for every DEM product intersecting the requested bounds.
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
        # Download one DEM tile into the temporary working directory.
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
        # Mosaic downloaded tiles into the output GeoTIFF and crop to the bounds.
        from osgeo import gdal # type: ignore

        gdal.UseExceptions()
        source_datasets = []
        result = None
        try:
            for tile_path in tile_paths:
                dataset = gdal.Open(tile_path, gdal.GA_ReadOnly)
                if dataset is None:
                    raise RuntimeError(
                        "Could not open downloaded DEM tile: {}".format(tile_path)
                    )
                source_datasets.append(dataset)

            first_tile = source_datasets[0]
            projection = first_tile.GetProjection()
            if not projection:
                raise RuntimeError(
                    "The downloaded DEM has no usable coordinate system."
                )

            options = gdal.WarpOptions(
                format="GTiff",
                dstSRS=projection,
                outputBounds=self.bounds,
                outputBoundsSRS="EPSG:4326",
                creationOptions=["TILED=YES", "COMPRESS=LZW"],
                cutlineDSName=self.cutline_path,
                cropToCutline=self.cutline_path is not None,
                dstNodata=-9999 if self.cutline_path is not None else None,
            )
            result = gdal.Warp(
                self.output_path, source_datasets, options=options
            )
            if result is None:
                raise RuntimeError("GDAL could not mosaic the downloaded DEM tiles.")
            result.FlushCache()
        finally:
            result = None
            dataset = None
            source_datasets.clear()
            gc.collect()
