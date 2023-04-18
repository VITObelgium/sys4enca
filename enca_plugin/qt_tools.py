# Copyright (c) 2022 European Union.
#
# The tool was developed with the contribution of the Joint Research Centre of the European Commission.
#
# This program is free software: you can redistribute it and/or modify it under the terms of the European Union Public
# Licence, either version 1.2 of the License, or (at your option) any later version.
# You may not use this work except in compliance with the Licence.
#
# You may obtain a copy of the Licence at: https://joinup.ec.europa.eu/collection/eupl/eupl-guidelines-faq-infographics
#
# Unless required by applicable law or agreed to in writing, software distributed under the Licence is distributed on
# an "AS IS" basis, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#
# See the Licence for the specific language governing permissions and limitations under the Licence.

"""Helper functions to deal with reading / setting widget contents."""
import ast
import html

from qgis.PyQt import QtWidgets, QtCore
from qgis.core import QgsMessageLog, Qgis
from qgis.gui import QgsFileWidget, QgsCheckableComboBox, QgsFilterLineEdit,  QgsSpinBox

from .integerlist import IntList
from .keyvalue_files import KeyValueFiles

def expand_template(template):
    """Given a template, recursively replace all references to widgets by the value of those widgets."""
    if type(template) == dict:
        return {expand_template(key): expand_template(val) for key, val in template.items()}
    if type(template) == list:
        return [expand_template(val) for val in template]
    if type(template) in (str, int, bool):
        return template
    if isinstance(template, QgsFileWidget):
        return template.lineEdit().value()
    if isinstance(template, QgsCheckableComboBox):
        return template.checkedItems()
    if isinstance(template, QtWidgets.QComboBox):
        # For ComboBox, there are 2 possibilities:
        # either we have Qt.User data and return that:
        user_data = template.currentData(QtCore.Qt.UserRole)
        if user_data is not None:
            return user_data
        # no user data -> return currentText()
        return template.currentText()
    if isinstance(template, IntList):
        return template.value()
    if isinstance(template, QgsFilterLineEdit):
        return template.value()
    if isinstance(template, QtWidgets.QLineEdit):
        return template.text()
    if isinstance(template, QtWidgets.QCheckBox):
        return template.isChecked()
    if isinstance(template, QtWidgets.QAbstractSpinBox):
        return template.value()
    if isinstance(template, QtWidgets.QRadioButton):
        return template.isChecked()
    if isinstance(template, QtWidgets.QPlainTextEdit):
        return template.document().toPlainText()
    if isinstance(template, QtWidgets.QButtonGroup):
        return template.checkedButton().text()
    if isinstance(template, KeyValueFiles):
        return template.value()
    raise RuntimeError(f'Failed to expand config template: {html.escape(str(template))}.')


def writeWidget(widget, value):
    """Set widget state to the given value"""
    if isinstance(widget, QgsFileWidget):
        if value == '':
            value = None
        widget.lineEdit().setValue(value)
    elif isinstance(widget, IntList):
        widget.setValue(value)
    elif isinstance(widget, QgsFilterLineEdit):
        widget.setValue(value)
    elif isinstance(widget, QtWidgets.QLineEdit):
        widget.setText(value)
    elif isinstance(widget, QtWidgets.QCheckBox):
        if value == '1' or value == True:
            widget.setChecked(True)
        else:
            widget.setChecked(False)
    elif isinstance(widget, QgsSpinBox):
        if value:
            widget.setValue(int(value))
    elif isinstance(widget, QtWidgets.QDoubleSpinBox):
        if value:
            widget.setValue(float(value))
    elif isinstance(widget, QgsCheckableComboBox):
        selected_list = []
        if isinstance(value, list):  # Load from yaml file
            selected_list = value
        elif value:  # Load from QGIS projects: list encoded as string.
            selected_list = ast.literal_eval(value)
        widget.deselectAllOptions()
        widget.setCheckedItems(selected_list)
    elif isinstance(widget, QtWidgets.QComboBox):
        # For QComboBox, we have 2 possiblities:
        # Either we are using ItemData with the Qt.UserRole:
        idx = widget.findData(value, QtCore.Qt.UserRole)
        if idx != -1:  # found data -> so use that
            widget.setCurrentIndex(idx)
        else:  # no user data -> use the value directly for the current text
            widget.setCurrentText(value)
    elif isinstance(widget, QtWidgets.QPlainTextEdit):
        widget.setPlainText(value)
    elif isinstance(widget, QtWidgets.QButtonGroup):
        for button in widget.buttons():
            if button.text() == value:
                button.setChecked(True)
                return
    elif isinstance(widget, KeyValueFiles):
        widget.setValue(value)
    else:
        raise RuntimeError(f'Failed to set widget state: {html.escape(str(widget))}')


def readWidget(widget):
    """Return a string representation of the widget state."""
    if isinstance(widget, QgsFileWidget):
        return widget.lineEdit().value()
    elif isinstance(widget, QgsCheckableComboBox):
        QgsMessageLog.logMessage(f'saving checkablecombobox: {str(widget.checkedItems())}', level=Qgis.Info)
        return str(widget.checkedItems())
    elif isinstance(widget, QtWidgets.QComboBox):
        QgsMessageLog.logMessage(f'saving qcombobox: {str(widget.currentText())}', level=Qgis.Info)
        # For ComboBox, there are 2 possibilities:
        # either we have Qt.User data and return that:
        user_data = widget.currentData(QtCore.Qt.UserRole)
        if user_data is not None:
            return user_data
        # no user data -> return currentText()
        return widget.currentText()
    elif isinstance(widget, QtWidgets.QLineEdit):
        return widget.text()
    elif isinstance(widget, QtWidgets.QCheckBox):
        return widget.isChecked()
    elif isinstance(widget, QtWidgets.QAbstractSpinBox):
        return str(widget.value())
 