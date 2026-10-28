import os
from datetime import datetime

from qgis.PyQt.QtCore import QObject, QStandardPaths, Qt, pyqtSignal # type: ignore
from qgis.PyQt.QtGui import QAction, QColor, QIcon # type: ignore
from qgis.PyQt.QtWidgets import (  # type: ignore
    QDialog,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
    QPushButton,
)
from qgis.core import ( # type: ignore
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)
from qgis.gui import QgsFileWidget, QgsMapTool, QgsRubberBand # type: ignore

from .downloader import DEMDownloadWorker


def _geographic_bounds(rectangle, source_crs):
    # Transform a map rectangle to validated EPSG:4326 bounds.
    geometry = QgsGeometry.fromRect(rectangle)
    transform = QgsCoordinateTransform(
        source_crs,
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance(),
    )
    geometry.transform(transform)
    bounds = geometry.boundingBox()
    if (
        bounds.xMinimum() < -180
        or bounds.xMaximum() > 180
        or bounds.yMinimum() < -90
        or bounds.yMaximum() > 90
    ):
        raise ValueError("The area is outside the geographic coordinate range.")
    return (
        bounds.xMinimum(),
        bounds.yMinimum(),
        bounds.xMaximum(),
        bounds.yMaximum(),
    )


class AreaSelectionDialog(QDialog):
    def __init__(self, default_output_path, parent=None):
        # Build the dialog for choosing an output raster and download area.
        super().__init__(parent)
        self.setWindowTitle("USGS 3DEP DEM Downloader")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Choose an area on the map to download USGS 3DEP 1-meter DEM data. "
                "Use the current canvas extent or draw a rectangle. Available "
                "coverage depends on the selected area."
            )
        )
        layout.addWidget(QLabel("Output DEM raster:"))
        self.output_file_widget = QgsFileWidget()
        self.output_file_widget.setStorageMode(QgsFileWidget.StorageMode.SaveFile)
        self.output_file_widget.setDialogTitle("Select Output DEM Raster")
        self.output_file_widget.setFilter("GeoTIFF (*.tif *.tiff)")
        self.output_file_widget.setDefaultRoot(os.path.dirname(default_output_path))
        self.output_file_widget.setFilePath(default_output_path)
        layout.addWidget(self.output_file_widget)
        self.status_label = QLabel(
            "Use the current canvas extent or draw a rectangle to begin."
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.canvas_extent_button = QPushButton("Download Canvas Extent")
        layout.addWidget(self.canvas_extent_button)

        self.select_button = QPushButton("Select Area on Map")
        self.select_button.setDefault(True)
        layout.addWidget(self.select_button)

        self.close_button = QPushButton("Close")
        layout.addWidget(self.close_button)
        self.close_button.clicked.connect(self.reject)


class RectangleSelectionTool(QgsMapTool):
    areaSelected = pyqtSignal(float, float, float, float)
    areaSelectionFailed = pyqtSignal(str)
    selectionCancelled = pyqtSignal()

    def __init__(self, canvas):
        # Initialize the map tool used to draw and select a rectangular area.
        super().__init__(canvas)
        self.canvas = canvas
        self.rubber_band = QgsRubberBand(canvas, Qgis.GeometryType.Polygon)
        self.rubber_band.setColor(QColor(45, 115, 210))
        self.rubber_band.setFillColor(QColor(45, 115, 210, 45))
        self.rubber_band.setWidth(2)
        self.start_point = None

    def canvasPressEvent(self, event):
        # Record the starting map point when the user presses the left mouse button.
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = self.canvas.getCoordinateTransform().toMapCoordinates(
                event.pos()
            )
            self.rubber_band.reset(Qgis.GeometryType.Polygon)

    def canvasMoveEvent(self, event):
        # Update the rubber-band rectangle as the pointer moves.
        if self.start_point is not None:
            current = self.canvas.getCoordinateTransform().toMapCoordinates(
                event.pos()
            )
            self.rubber_band.setToGeometry(
                QgsGeometry.fromRect(QgsRectangle(self.start_point, current)), None
            )

    def canvasReleaseEvent(self, event):
        # Transform and emit the selected rectangle after the left mouse button is released.
        if event.button() != Qt.MouseButton.LeftButton or self.start_point is None:
            return

        end_point = self.canvas.getCoordinateTransform().toMapCoordinates(event.pos())
        rectangle = QgsRectangle(self.start_point, end_point)
        self.start_point = None
        self.rubber_band.reset(Qgis.GeometryType.Polygon)
        if rectangle.isEmpty():
            return

        try:
            bounds = _geographic_bounds(
                rectangle, self.canvas.mapSettings().destinationCrs()
            )
        except Exception as error:
            self.areaSelectionFailed.emit(str(error))
            return

        self.areaSelected.emit(*bounds)

    def keyPressEvent(self, event):
        # Emit a cancellation signal when the user presses Escape.
        if event.key() == Qt.Key.Key_Escape:
            self.selectionCancelled.emit()

    def deactivate(self):
        # Clear the in-progress selection when the map tool is deactivated.
        self.start_point = None
        self.rubber_band.reset(Qgis.GeometryType.Polygon)
        super().deactivate()


class USGSDEMPlugin(QObject):
    def __init__(self, iface):
        # Initialize plugin state and retain the QGIS interface.
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.action = None
        self.dialog = None
        self.selection_tool = None
        self.previous_map_tool = None
        self.worker = None
        self.progress_dialog = None
        self._output_timestamp = None

    def initGui(self):
        # Add the plugin action to the QGIS toolbar and menu.
        icon_path = os.path.join(os.path.dirname(__file__), "logo.png")
        self.action = QAction(
            QIcon(icon_path), "Download USGS 3DEP DEM", self.iface.mainWindow()
        )
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("USGS 3DEP Downloader", self.action)

    def unload(self):
        # Restore the map tool, stop active work, and remove plugin actions.
        if self.selection_tool is not None:
            self._restore_map_tool()
            self.selection_tool = None
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait()
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("USGS 3DEP Downloader", self.action)

    def run(self):
        # Create and display the DEM download dialog.
        if self.dialog is None:
            self.dialog = AreaSelectionDialog(
                self._default_output_path(), self.iface.mainWindow()
            )
            self.dialog.select_button.clicked.connect(self.start_area_selection)
            self.dialog.canvas_extent_button.clicked.connect(
                self.download_canvas_extent
            )
            self.dialog.finished.connect(self._dialog_closed)
        self.dialog.status_label.setText(
            "Use the current canvas extent or draw a rectangle to begin."
        )
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def download_canvas_extent(self):
        # Download DEM data for the current map canvas extent.
        if self.worker is not None and self.worker.isRunning():
            return
        self._output_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        canvas = self.iface.mapCanvas()
        try:
            bounds = _geographic_bounds(
                canvas.extent(), canvas.mapSettings().destinationCrs()
            )
        except Exception as error:
            message = "Could not use the canvas extent: {}".format(error)
            self.dialog.status_label.setText(message)
            QMessageBox.warning(
                self.iface.mainWindow(), "USGS 3DEP Downloader", message
            )
            return
        self._area_selected(*bounds)

    def start_area_selection(self):
        # Activate the map tool so the user can draw a custom download area.
        if self.worker is not None and self.worker.isRunning():
            return
        self._output_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.selection_tool is not None:
            self._restore_map_tool()

        self.previous_map_tool = self.iface.mapCanvas().mapTool()
        self.selection_tool = RectangleSelectionTool(self.iface.mapCanvas())
        self.selection_tool.areaSelected.connect(self._area_selected)
        self.selection_tool.areaSelectionFailed.connect(self._selection_failed)
        self.selection_tool.selectionCancelled.connect(self._selection_cancelled)
        self.iface.mapCanvas().setMapTool(self.selection_tool)
        self.dialog.status_label.setText(
            "Drag a rectangle on the map. Press Esc to cancel."
        )

    def _area_selected(self, min_x, min_y, max_x, max_y):
        # Start the DEM worker for geographic bounds and show its progress dialog.
        self._restore_map_tool()
        self.dialog.status_label.setText(
            "Area selected. Searching USGS 3DEP and downloading available tiles..."
        )
        try:
            output_path = self._new_output_path()
        except OSError as error:
            self._download_failed(
                "Could not create the DEM download folder: {}".format(error)
            )
            return
        self.worker = DEMDownloadWorker(
            min_x, min_y, max_x, max_y, output_path, self
        )
        self.worker.progressChanged.connect(self._update_progress)
        self.worker.completed.connect(self._download_completed)
        self.worker.failed.connect(self._download_failed)
        self.progress_dialog = QProgressDialog(
            "Searching for DEM tiles...", "", 0, 0, self.iface.mainWindow()
        )
        self.progress_dialog.setWindowTitle("Downloading USGS 3DEP DEM")
        self.progress_dialog.setCancelButton(None)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        self.progress_dialog.show()
        self.worker.start()

    def _new_output_path(self):
        # Use the configured folder and create a timestamped GeoTIFF for each download.
        configured_path = self.dialog.output_file_widget.filePath().strip()
        if not configured_path:
            configured_path = self._default_output_path()
        extension = os.path.splitext(configured_path)[1]
        if extension.lower() not in (".tif", ".tiff"):
            extension = ".tif"

        output_folder = os.path.dirname(os.path.abspath(configured_path))
        os.makedirs(output_folder, exist_ok=True)
        output_path = os.path.join(
            output_folder,
            "USGS_3DEP_DEM_{}{}".format(self._output_timestamp, extension),
        )
        self.dialog.output_file_widget.setFilePath(output_path)
        return output_path

    def _default_output_path(self):
        # Generate a timestamped GeoTIFF path in the plugin's download folder.
        output_folder = os.path.join(self._downloads_folder(), "USGS_3DEP")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return os.path.join(
            output_folder, "USGS_3DEP_DEM_{}.tif".format(timestamp)
        )

    @staticmethod
    def _downloads_folder():
        # Return the platform's Downloads folder or the user's home fallback.
        try:
            folder = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DownloadLocation
            )
        except AttributeError:
            folder = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        return folder or os.path.join(os.path.expanduser("~"), "Downloads")

    def _update_progress(self, value, total, message):
        # Update the visible progress dialog from worker progress signals.
        if self.progress_dialog is None:
            return
        self.progress_dialog.setLabelText(message)
        self.progress_dialog.setRange(0, total)
        self.progress_dialog.setValue(value)

    def _download_completed(self, output_path):
        # Load a completed GeoTIFF into the current QGIS project.
        self._close_progress()
        layer = QgsRasterLayer(output_path, os.path.basename(output_path))
        if not layer.isValid():
            QMessageBox.critical(
                self.iface.mainWindow(),
                "USGS 3DEP Downloader",
                "The downloaded raster could not be opened: {}".format(output_path),
            )
            self.dialog.status_label.setText("The downloaded raster could not be opened.")
            return

        QgsProject.instance().addMapLayer(layer)
        self.dialog.status_label.setText(
            "DEM downloaded and added to the map: {}".format(output_path)
        )
        self.iface.messageBar().pushMessage(
            "USGS 3DEP",
            "DEM downloaded and added to the map.",
            level=Qgis.MessageLevel.Success,
            duration=8,
        )

    def _download_failed(self, message):
        # Close progress UI and report the download failure to the user.
        self._close_progress()
        self.dialog.status_label.setText("Download failed: {}".format(message))
        QMessageBox.critical(
            self.iface.mainWindow(), "USGS 3DEP Downloader", message
        )

    def _close_progress(self):
        # Close the progress dialog and release the finished worker.
        if self.progress_dialog is not None:
            self.progress_dialog.close()
            self.progress_dialog.deleteLater()
            self.progress_dialog = None
        if self.worker is not None:
            self.worker.wait()
            self.worker = None

    def _selection_failed(self, message):
        # Restore the previous map tool and report a selection error.
        self._restore_map_tool()
        self.dialog.status_label.setText("Area selection failed: {}".format(message))
        QMessageBox.warning(
            self.iface.mainWindow(), "USGS 3DEP Downloader", message
        )

    def _selection_cancelled(self):
        # Restore the previous map tool and report that selection was cancelled.
        self._restore_map_tool()
        self.dialog.status_label.setText("Area selection cancelled.")

    def _restore_map_tool(self):
        # Restore the previous map tool and dispose of the rectangle selector.
        canvas = self.iface.mapCanvas()
        tool = self.selection_tool
        if tool is not None and canvas.mapTool() is tool:
            if self.previous_map_tool is not None:
                canvas.setMapTool(self.previous_map_tool)
            else:
                canvas.unsetMapTool(tool)
        if tool is not None:
            tool.deleteLater()
        self.selection_tool = None
        self.previous_map_tool = None

    def _dialog_closed(self, result):
        # Restore the previous map tool if the dialog closes during area selection.
        if self.selection_tool is not None:
            self._restore_map_tool()
