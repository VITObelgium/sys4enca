from PyQt5 import QtWidgets

from qgis.core import Qgis, QgsApplication, QgsMessageLog
from qgis.gui import QgsFileWidget, QgsCheckableComboBox

import geopandas as gpd


class RegionPicker(QtWidgets.QGroupBox):

    def __init__(self, *args, **kwargs):
        super(RegionPicker, self).__init__(*args, **kwargs)

        layout = QtWidgets.QFormLayout()

        self._filewidget = QgsFileWidget(self)
        self._selected_regions = QgsCheckableComboBox(self)
        layout.insertRow(0, 'Shape file', self._filewidget)
        layout.insertRow(1, 'Selected regions', self._selected_regions)
        self._id_label = None

        self.setLayout(layout)

        self._current_shapefile = self._filewidget.filePath()

        # State to keep track of filewidget button clicks.
        self._filewidget_clicked_flag = False

        # connect signals
        self.connect_widgets()

    def connect_widgets(self):
        """Signals and slots to update the region selection combobox when a new reporting shapefile is selected."""
        # We want to update the list of regions whenever a new shape file was set by the user, but only when the
        # user has finished editing (i.e. don't attempt to load a new shape while the user is still typing the
        # filename).  The file can be changed in the following ways:
        # - user edits the text using the widget's LineEdit -> use the editingFinished signal for this
        self._filewidget.lineEdit().editingFinished.connect(self.updateRegions)
        # - user clicks the button and selects a file using the menu -> there's no simple method (e.g. a signal) to
        #   detect this, so  we use a slightly hacky workaround:
        # 1. find the QgsFileWidget button using findChildren(QtWidgets.QAbstractButton) -- findChildren returns a list,
        #    but there will be exactly one child object of type QAbstractButton -- and listen for the 'pressed' signal
        #    to set a flag when the user has clicked the button.
        for x in self._filewidget.findChildren(QtWidgets.QAbstractButton):
            x.pressed.connect(self._filewidget_buttonclicked)
        # 2. listen for the LineEdit's textChanged signal to detect updates to the filename, but only update the menu
        #    when the button clicked flag is set. (The LineEdit also sends a textChanged signal whenever the user types
        #    in the lineEdit, but we don't want to update the list of regions while the user is still typing.)
        self._filewidget.lineEdit().textChanged.connect(self._filewidget_textchanged)
        # 3. handle special case when the user clicks the button but then clicks cancel, so no textChanged signal is
        #    sent, and then edits the filename by typing.
        QgsApplication.instance().focusChanged.connect(self._focuschanged)

    def _filewidget_textchanged(self):
        """Update region list after the user has selected a file using the QgsFileWidget button."""
        # We *only* want to handle textChanged if it's the consequence of clicking the QgsFileWidget button.  If the
        # button wasn't clicked, this signal means the user is busy typing a file name in the line edit, and we do not
        # want to update the available regions on every keypress (instead, we listen for editingFinished).
        if self._filewidget_clicked_flag:
            self._filewidget_clicked_flag = True
            self.updateRegions()

    def _filewidget_buttonclicked(self):
        """Detect when the user opens the shapefile selection dialog."""
        self._filewidget_clicked_flag = True

    def set_id_label(self, label):
        """Set the column name which should contain the id's of the selected regions"""
        self._id_label = label

    def setShapefile(self, shapefile):
        self._filewidget.lineEdit().setValue(shapefile)
        self.updateRegions()

    def updateRegions(self):
        new_shapefile = self._filewidget.filePath()
        if self._current_shapefile == new_shapefile:
            # Do nothing if the filename after editing is the same as the one before.
            return

        if new_shapefile:  # not None or empty
            try:
                regions = gpd.read_file(new_shapefile, ignore_geometry=True)[self._id_label]
            except Exception:
                QgsMessageLog.logMessage(f'Failed to read ID\'s with label {self._id_label} '
                                         f'from file "{new_shapefile}"',
                                         level=Qgis.Critical)
                self._filewidget.lineEdit().setStyleSheet('border: 2px solid red')
                return
        else:
            regions = []
        self._selected_regions.clear()
        for id in sorted(regions):
            self._selected_regions.addItem(id)
        self._current_shapefile = new_shapefile
        self._selected_regions.selectAllOptions()
        self._selected_regions.repaint()

    def _focuschanged(self, old, new):
        # We handle the following special case:
        # 1) user clicks the file button for the shape file
        # 2) user clicks cancel (thus, the file remains the same, but our _filewidget_clicked_flag will be set to True)
        # 3) [... arbitrary number of other user interactions in between...]
        # 4) at a later stage, the user clicks the line edit and changes the text.
        #
        # -> if our _filewidgt_clicked_flag is still True, editing the text will trigger an update of the NUTS region
        # list after the first keypress.  Therefore, set the flag to False when the NUTS file lineEdit receives focus.
        if new == self._filewidget.lineEdit():
            self._filewidget_clicked_flag = False
