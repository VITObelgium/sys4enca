from qgis.PyQt import QtCore, QtWidgets, QtGui

_validator = QtGui.QRegExpValidator(QtCore.QRegExp('\\d+(, *\\d+)*'))


class IntList(QtWidgets.QLineEdit):

    def __init__(self, *args, **kwargs):
        super(IntList, self).__init__(*args, **kwargs)

        self.setValidator(_validator)

    def value(self):
        """Return the widget contents as a list of integers."""
        if not self.text():  # Empty string
            return []
        # If widget contents are not empty, the validator ensures that the widget holds a list of integers split by
        # commas, with possible trailing comma and spaces, which we can remove with rstrip():
        return [int(s) for s in self.text().rstrip(', ').split(',')]

    def setValue(self, value):
        if value is not None:
            self.setText(', '.join(str(i) for i in value))
        else:  # setValue(None) is used to clear the widget
            self.clear()
