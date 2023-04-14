from PyQt5 import QtWidgets

from qgis.core import Qgis, QgsApplication, QgsMessageLog
from qgis.gui import QgsFileWidget, QgsCheckableComboBox

class KeyValueFiles(QtWidgets.QGroupBox):

    def __init__(self, keys, *args, **kwargs):
        super(KeyValueFiles, self).__init__(*args, **kwargs)

        self._filewidgets = None
        self.setLayout(QtWidgets.QFormLayout())

    def setKeys(self, keys):
        self._filewidgets = {key: QgsFileWidget(self) for key in keys}

        # Remove existing items from the layout
        while self.layout().takeAt(0):
            continue

        for key, widget in self._filewidgets.items():
            self.layout().addRow(key, widget)

    def value(self):
        return {key: widget.lineEdit().value() for key, widget in self._filewidgets.items()}

    def setValue(self, values: dict):
        for key, value in values.items():
            self._filewidgets[key].lineEdit().setValue(value)
