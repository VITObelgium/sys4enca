from PyQt5 import QtWidgets
from qgis.gui import QgsFileWidget, QgsCheckableComboBox

class RegionPicker(QtWidgets.QWidget):

    def __init__(self, *args, **kwargs):
        super(RegionPicker, self).__init__(*args, **kwargs)

        layout = QtWidgets.QVBoxLayout()

        self._groupbox = QtWidgets.QGroupBox(self)

        self._filewidget = QgsFileWidget(self)
        self._combobox = QgsCheckableComboBox(self)

        layout.addWidget(self._filewidget)
        layout.addWidget(self._combobox)

        self.setLayout(layout)

