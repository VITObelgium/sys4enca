import ctypes
import operator
import os
import threading
from functools import reduce

import yaml
from qgis.PyQt import QtCore, QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsTask
from qgis.gui import QgsFileWidget, QgsDoubleSpinBox
from qgis.utils import iface

import enca
import enca.carbon as carbon
import enca.carbon.npp as carbon_npp
import enca.carbon.soil as carbon_soil
import enca.carbon.soil_erosion as carbon_soil_erosion
import enca.carbon.livestock as carbon_livestock
import enca.carbon.fire_vuln as carbon_fire_vuln
import enca.carbon.agriculture as carbon_agriculture
import enca.carbon.fire as carbon_fire
import enca.carbon.forest as carbon_forest
import enca.components
import enca.framework
import enca.water as water
import enca.water.precipitation_evapotranspiration as water_precip_evapo
import enca.water.usage as water_usage
import enca.water.drought_vuln as water_drought_vuln
import enca.water.river_length_pixel as water_river_length_px
import enca.infra as infra
import enca.leac as leac
import enca.total as total
import enca.trend as trend
from enca.components import get_component_long_name
from enca.framework.errors import Error
from enca.framework.config_check import ConfigError, YEARLY
from enca.framework.run import Cancelled

from .help import show_help
from .qt_tools import writeWidget, expand_template
from .qgis_tools import load_vector_layer

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'enca_plugin_dockwidget_base.ui'))


# We need to keep a reference to our tasks prevent tasks from "disappearing" when we pass them on to the taskmanager.
_tasks = []  #: global reference to currently launched tasks.

#:  names of input widgets per component.
# Note: names must match the name of the corresponding ui widget *and* the config key to which they refer.
component_input_widgets = [
    (carbon.Carbon.component, [
        carbon.FOREST_AGB,
        carbon.FOREST_BGB,
        carbon.FOREST_LITTER,
        carbon.SOIL,
        carbon.LIVESTOCK,
        carbon.NPP,
        carbon.AGRICULTURE_CEREALS,
        carbon.AGRICULTURE_FIBER,
        carbon.AGRICULTURE_FRUIT,
        carbon.AGRICULTURE_OILCROP,
        carbon.AGRICULTURE_PULSES,
        carbon.AGRICULTURE_ROOTS,
        carbon.AGRICULTURE_CAFE,
        carbon.AGRICULTURE_VEGETABLES,
        carbon.AGRICULTURE_SUGAR,
        carbon.WOODREMOVAL,
        carbon.SOIL_EROSION,
        carbon.ILUP,
        carbon.CEH1,
        carbon.CEH4,
        carbon.CEH6,
        carbon.CEH7,
        carbon.COW,
        carbon.FIRE,
        carbon.FIRE_SPLIT,
        carbon.FIRE_INTEN]),
    (water.Water.component, [
        water.USE_AGRI,
        water.USE_MUNI,
        water.EVAPO_RAINFED,
        water.PRECIPITATION,
        water.EVAPO,
        water.LT_PRECIPITATION,
        water.LT_EVAPO,
        water.DROUGHT_VULN,
        water.RIVER_LENGTH,
        water.LT_OUTFLOW,
        water.AQUIFER,
        water.SALINITY,
        water.HYDRO_LAKES,
        water.GLORIC]),
    (water_precip_evapo.WaterPrecipEvapo.component, [
        water_precip_evapo._WORLDCLIM,
        water_precip_evapo._CGIAR_AET,
        (water_precip_evapo._COPERNICUS_PRECIPITATION, [YEARLY]),
        water_precip_evapo._LC_RAINFED_AGRI]),
    (water_usage.Usage.component, [
        (water_usage._GHS_POP, ['y1990',
                                'y1995',
                                'y2000',
                                'y2005',
                                'y2010',
                                'y2015',
                                'y2020',
                                'y2025',
                                'y2030']),
        water_usage._MUNICIPAL,
        water_usage._AGRICULTURAL,
        water_usage._LC_AGRI]),
    (water_drought_vuln.DroughtVuln.component, [
        (water_drought_vuln.DROUGHT_CODE, [YEARLY]),
        water_drought_vuln.DROUGHT_CODE_LTA]),
    (water_river_length_px.RiverLength.component, [
        water_river_length_px._GLORIC]),
    (carbon_npp.CarbonNPP.component, [
        (carbon_npp.GDMP_DIR, [YEARLY]),
        carbon_npp.GDMP_2_NPP]),
    (carbon_soil.CarbonSoil.component, [
        carbon_soil.SEAL_ADJUST,
        carbon_soil.SOC,
        carbon_soil.SOC_MANGROVES,
        carbon_soil.MANGROVE_CLASSES,
        carbon_soil.NONSOIL_CLASSES,
        carbon_soil.URBAN_CLASSES]),
    (carbon_soil_erosion.CarbonErosion.component, [
        carbon_soil_erosion.R_FACTOR_1,
        carbon_soil_erosion.R_FACTOR_25,
        carbon_soil_erosion.SOIL_CARBON_10,
        carbon_soil_erosion.SOIL_CARBON_20,
        carbon_soil_erosion.SOIL_CARBON_30,
        (carbon_soil_erosion.SOIL_LOSS, [YEARLY])]),
    (carbon_livestock.CarbonLivestock.component, [
        (carbon_livestock.LIVESTOCK_CARBON, carbon.livestock._livestock_types),
        (carbon_livestock.LIVESTOCK_DIST, carbon.livestock._livestock_types),
        (carbon_livestock.WEIGHTS, carbon.livestock._livestock_types)]),
    (carbon_fire_vuln.CarbonFireVulnerability.component, [
        (carbon_fire_vuln.SEVERITY_RATING, [YEARLY]),
        carbon_fire_vuln.SEVERITY_RATING_LTA]),
    (carbon_agriculture.CarbonAgriculture.component, [
        carbon_agriculture.AGRICULTURE_DISTRIBUTION,
        carbon_agriculture.AGRICULTURE_STATS]),
    (carbon_fire.CarbonFire.component, [
        (carbon_fire.BURNT_AREAS, [YEARLY]),
        carbon_fire.FOREST_BIOMASS]),
    (carbon_forest.CarbonForest.component, [
        carbon_forest.FAOFRA_AGB,
        carbon_forest.FAOFRA_BGB,
        carbon_forest.FAOFRA_LITTER,
        carbon_forest.FAOFRA_WREM,
        carbon_forest.FOREST_LC_CLASSES,
        carbon_forest.LAND_COVER_FRACTION,
        carbon_forest.WOOD_REMOVAL_LIMIT
    ]),
    (infra.Infra.component, [
        infra.REF_YEAR,
        infra.REF_LANDCOVER,
        ('paths_indices', list(infra.INDICES.keys())),
        ('general', [
            'lc_urban',
            'lc_water']),
        'lut_gbli',
        'naturalis',
        'osm',
        ('catchments', [
            'catchment_6',
            'catchment_8',
            'catchment_12'
        ]),
        'dams',
        'gloric',
        ('leac_result', [YEARLY])
    ]),
    (leac.Leac.component, [
        leac.REF_YEAR,
        leac.REF_LANDCOVER,
        'lut_ct_lc',
        'lut_ct_lcf',
        'lut_lc',
        'lut_lc2psclc',
        'lut_lcflow_C',
        'lut_lcflow_F',
        'lut_lcflows',
    ]),
    (total.Total.component, [
        'infra_result',
        'carbon_result',
        'water_result'
    ]),
    (trend.Trend.component, [
        'total_result'
    ])
]

#: vector output files to be loaded after a run, with visualization parameters (keyword arguments for
component_vector_layers = {
    carbon.Carbon.component: [(os.path.join('temp', 'CARBON_Indices_SELU_{year}.gpkg'), dict(
        layer_name='NEACS [ha]',
        attribute_name='C10_ha'))],
    water.Water.component: [(os.path.join('temp', 'WATER_Indices_SELU_{year}.gpkg'), dict(
        layer_name='TOTuseEW',
        attribute_name='W9_ha',
        color_ramp='Blues'))],
    carbon_livestock.CarbonLivestock.component: []
}

def findChild(widget: QtWidgets.QWidget, name: str):
    """Helper function to deal with the fact that .ui compilation does not allow duplicate widget names.

    When the .ui file gets compiled, widgets with duplicate names get a numerical suffix (e.g. 'run_name'
    -> 'run_name2').  Here, we search using a regular expressions, and return the first and widget.  Raise an error
    if not exactly one matching widget is found.
    """
    try:
        result, = widget.findChildren(QtWidgets.QWidget,
                                      QtCore.QRegularExpression(f'^{QtCore.QRegularExpression.escape(name)}_\\d*$'))
    except ValueError:
        QgsMessageLog.logMessage(f'findChildren() did not find a unique result for widget name "{name}"',
                                 level=Qgis.Critical)
        raise
    return result


class ENCAPluginDockWidget(QtWidgets.QDockWidget, FORM_CLASS):

    closingPlugin = pyqtSignal()

    def __init__(self, parent=None):
        """Constructor."""
        super(ENCAPluginDockWidget, self).__init__(parent)
        # Set up the user interface from Designer.
        # After setupUI you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # http://doc.qt.io/qt-5/designer-using-a-ui-file.html
        # #widgets-and-dialogs-with-auto-connect
        self.setupUi(self)

        self.set_up_component_dropdowns()
        self.set_up_carbon_livestock()
        self.set_up_infra()

        self.toolbar = QtWidgets.QToolBar()
        self.toolbar.setIconSize(iface.iconSize(dockedToolbar=True))

        self.loadact = QtWidgets.QAction(QtGui.QIcon(":/plugins/enca_plugin/mActionFileOpen.svg"),
                                         'Load Configuration', self)
        self.loadact.triggered.connect(self.loadConfig)
        self.saveact = QtWidgets.QAction(QtGui.QIcon(":/plugins/enca_plugin/mActionFileSaveAs.svg"),
                                         'Save Configuration', self)
        self.saveact.triggered.connect(self.saveConfig)
        self.toolbar.addAction(self.loadact)
        self.toolbar.addAction(self.saveact)
        self.toolbar.addSeparator()

        self.runact = QtWidgets.QAction(
            QtGui.QIcon(":/plugins/enca_plugin/play-button-svgrepo-com.svg"),
            'Run', self)
        self.runact.triggered.connect(self.run)
        self.toolbar.addAction(self.runact)

        self.continue_run = QtWidgets.QCheckBox('Continue existing run')
        self.continue_run.setObjectName('continue_run')
        self.toolbar.addWidget(self.continue_run)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)

        self.helpact = QtWidgets.QAction('Help', self)
        self.helpact.triggered.connect(show_help)
        self.toolbar.addAction(self.helpact)

        self.toolbarLayout.addWidget(self.toolbar)

        # Reporting area shapefiles should have 'REP_ID' id attribute
        self.reporting_areas.set_id_label(enca.REP_ID)

        # Initialize Tier level combobox
        self.tier.addItem('1', 1)
        self.tier.addItem('2', 2)
        self.tier.addItem('3', 3)

        self.config_template = {
            'years': [self.year],
            'output_dir': self.output_dir,
            'aoi_name': self.aoi_name,
            'statistics_shape': self.data_areas,
            'reporting_shape': self.reporting_areas._filewidget,
            'selected_regions': self.reporting_areas._selected_regions,
            enca._ADMIN_BOUNDS: self.admin_boundaries,
            'land_cover': {self.year: self.land_cover},
            'continue': self.continue_run,
            'tier': self.tier
        }
        self.component_templates = self.build_template_tree(component_input_widgets, self.run_types)

    def set_up_component_dropdowns(self):
        """Fill the dropdown menu for preprocessing/run/ accounts tabs, and connect signals."""
        for i in range(self.run_types.count()):
            tab = self.run_types.widget(i)
            dropdown = findChild(tab, 'component')
            components_stack = findChild(tab, 'components_stack')
            for j in range(components_stack.count()):
                name = components_stack.widget(j).objectName()[:-1]  # cut off trailing '_'
                dropdown.addItem(get_component_long_name(name), name)
            dropdown.currentIndexChanged.connect(components_stack.setCurrentIndex)

    def set_up_carbon_livestock(self):
        """Set up Carbon livestock key-value widgets."""
        self.livestock_carbon_.setLayout(QtWidgets.QFormLayout())
        self.livestock_distribution_.setLayout(QtWidgets.QFormLayout())
        self.weights_.setLayout(QtWidgets.QFormLayout())
        for key in enca.carbon.livestock._livestock_types:
            carbon_widget = QgsFileWidget(self, objectName=key + '_')
            carbon_widget.setFilter('CSV (*.csv);; All Files (*.*)')
            self.livestock_carbon_.layout().addRow(key, carbon_widget)
            distribution_widget = QgsFileWidget(self, objectName=key + '_')
            distribution_widget.setFilter('Geotiff (*.tiff *.tif);; All Files (*.*)')
            self.livestock_distribution_.layout().addRow(key, distribution_widget)
            widget_weight = QgsDoubleSpinBox(self, objectName=key + '_')
            widget_weight.setRange(0., 1000.)
            self.weights_.layout().addRow(key, widget_weight)

    def set_up_infra(self):
        """Set up Infra indices input widgets."""
        widget_infra = self.ENCA_.findChild(QtWidgets.QWidget, infra.Infra.component + '_')
        widget_infra_indices = widget_infra.findChild(QtWidgets.QGroupBox, 'paths_indices_')
        widget_infra_indices.setLayout(QtWidgets.QFormLayout())
        for idx, label in infra.INDICES.items():
            # Add suffix '_' to index widget object names because they end in an integer
            widget_infra_indices.layout().addRow(f'{idx}. {label}', QgsFileWidget(self, objectName=f'{idx}_'))

    def saveConfig(self):
        """Save current ui state as a yaml config file."""
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Config File", "config.yaml", "*.yaml")
        if not filename:
            return
        with open(filename, 'wt') as f:
            f.write(yaml.dump(self.make_config()))

    def loadConfig(self):
        """Load a yaml config file."""
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(self, self.tr("Load Config File"), "",
                                                            self.tr("Config files (*.yaml);; All files (*.*)"))
        if not filename:
            return

        try:
            with open(filename, 'rt') as f:
                config = yaml.safe_load(f)

            # Clear existing configuration:
            for widget in self.list_config_widgets():
                writeWidget(widget, None)

            # Handle year selection and reporting regions manually:
            self.year.setValue(config['years'][0])
            self.reporting_areas.setShapefile(config.get('reporting_shape'))

            # remaining config values can be read automatically using the config templates
            main_template = {key: value for key, value in self.config_template.items()
                             if key not in ('years', 'reporting_regions')}
            self.load_template(config, main_template)

            # select the right tab (preprocessing/components/accounts), and the right component within that tab:
            component_name = config['component']
            run_class = enca.components.get_component(component_name)
            run_type = run_class.run_type
            # select tab:
            run_type_tab = findChild(self.run_types, run_type.name)
            self.run_types.setCurrentWidget(run_type_tab)
            # select component:
            component_page = findChild(run_type_tab, component_name)
            component_combo = findChild(run_type_tab, 'component')
            idx = component_combo.findData(component_name, QtCore.Qt.UserRole)
            component_combo.setCurrentIndex(idx)

            # handle run name:
            try:
                findChild(component_page, 'run_name').setText(config['run_name'])
            except ValueError:
                pass  # Not all input pages have a run_name widget yet
            # read other config values using the template
            self.load_template(config, {component_name: self.component_templates[component_name]})

        except BaseException as e:
            QtWidgets.QMessageBox.critical(self, 'Error loading config', f'Could not load config {filename}: {e}.')

        if 'metadata' in config:
            self.metadata[config['service']] = config['metadata'][config['years'][0]]

    def list_widgets(self, element):
        result = []
        if isinstance(element, QtWidgets.QWidget):
            result.append(element)
        elif isinstance(element, dict):
            for x in element.values():
                result = result + self.list_widgets(x)
        elif isinstance(element, list):
            for x in element:
                result = result + self.list_widgets(x)
        else:
            pass
        return result

    def list_config_widgets(self):
        result = self.list_widgets(self.config_template) + self.list_widgets(self.component_templates)
        return result

    def load_template(self, config, template):
        """Update the widgets listed in the template with values from config.

        We use recursion to process te nested dictionary structure of the template and config.  This is more or less
        the inverse of expand_template().

        NOTE: Assumes widget self.year was already set to the correct value."""
        if type(template) == dict:
            for key, template_value in template.items():
                # special case for yearly datasets ...
                if key == self.year:
                    key = self.year.value()
                try:
                    self.load_template(config[key], template_value)
                except KeyError as e:
                    QgsMessageLog.logMessage(f'While loading config: missing config key {e}.', level=Qgis.Warning)
        elif type(template) in (int, bool, str):
            # We're only interested in the widgets referenced from the template.
            pass
        elif isinstance(template, QtCore.QObject):
            writeWidget(template, config)
        else:
            raise RuntimeError(f'Failed to process template {template}.')

    def run(self):
        template = self.make_template()
        taskname = f'{template["component"].currentText()} run {template["run_name"].text()}'
        component_name = template["component"].currentData(QtCore.Qt.UserRole)
        task = Task(taskname, template, output_vectors=component_vector_layers.get(component_name, []))
        _tasks.append(task)
        QgsMessageLog.logMessage(f'Submitting task {taskname}', level=Qgis.Info)
        QgsApplication.taskManager().addTask(task)

    def closeEvent(self, event):
        self.closingPlugin.emit()
        event.accept()

    def build_template_tree(self, widget_names: list, root_widget: QtWidgets.QWidget):
        result = {}
        # special case: Yearly inputs
        if widget_names == [YEARLY]:
            return {self.year: root_widget}
        for entry in widget_names:
            if isinstance(entry, tuple):
                name, children = entry
                result[name] = self.build_template_tree(children, findChild(root_widget, name))
            else:
                assert isinstance(entry, str)   # entry is the name of a widget
                result[entry] = findChild(root_widget, entry)
        return result

    def make_config(self):
        """Generate a config from current settings."""
        return expand_template(self.make_template())

    def make_template(self):
        # Get the currently selected component.
        # 1. see which tab we are on (preprocessing / components / accounts)
        tab_runtype = self.run_types.currentWidget()
        # 2. select the component from this tab
        component_dropdown = findChild(tab_runtype, 'component')
        component_name = component_dropdown.currentData(QtCore.Qt.UserRole)
        template = {**self.config_template,
                    'component': component_dropdown,
                    component_name: self.component_templates[component_name]}
        component_widget = findChild(tab_runtype, component_name)
        try:
            template['run_name'] = findChild(component_widget, 'run_name')
        except ValueError:  # Some pages are currently incomplete
            pass
        return template

class Task(QgsTask):

    def __init__(self, description, template, output_rasters=None, output_vectors=None):
        super().__init__(description)
        self.config = expand_template(template)
        self.widget_dict = expand_year(template)
        self.output_rasters = output_rasters or []
        self.output_vectors = output_vectors or []
        self.run = None
        self.run_thread = None
        self.exception = None

    def cancel(self):
        """If the task is canceled, raise a Cancelled exception in the run thread to stop it."""
        super().cancel()
        if self.run_thread is not None:
            # Use the Python C API to raise an exception in another thread:
            ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self.run_thread.ident),
                                                             ctypes.py_object(Cancelled))
            # ref: http://docs.python.org/c-api/init.html#PyThreadState_SetAsyncExc
            if ret == 0:
                QgsMessageLog.logMessage('Failed to cancel run thread.', level=Qgis.Critical)

    def run(self):
        try:
            self.run_thread = threading.current_thread()  # save current thread so we can stop it if task is canceled.
            self.run = enca.components.make_run(self.config)
            self.run.start(progress_callback=self.setProgress)
            return True
        except Exception as e:
            self.exception = e
            return False

    def finished(self, result):
        if result:
            QgsMessageLog.logMessage('Task completed', level=Qgis.Info)
            for raster in self.output_rasters:
                path = os.path.join(self.run.run_dir, raster)
                iface.addRasterLayer(path)

            for filename, kwargs in self.output_vectors:
                path = os.path.join(self.run.run_dir, filename).format(year=self.run.config['years'][0])
                load_vector_layer(path, **kwargs)

        else:
            if self.exception is None:
                QgsMessageLog.logMessage('Task failed for unknown reason')
            elif isinstance(self.exception, Cancelled):
                QtWidgets.QMessageBox.information(iface.mainWindow(), 'Cancelled', 'Run was cancelled by user.')
            elif isinstance(self.exception, ConfigError):
                widget = reduce(operator.getitem, self.exception.path, self.widget_dict)
                if isinstance(widget, QgsFileWidget):  # TODO clean up!
                    widget = widget.lineEdit()
                widget.setStyleSheet('border: 1px solid red')
                QtWidgets.QMessageBox.warning(iface.mainWindow(), 'Configuration error', self.exception.message)
                widget.setStyleSheet('')
            elif isinstance(self.exception, Error):
                QtWidgets.QMessageBox.warning(iface.mainWindow(), 'Error', str(self.exception.message))
            else:
                QtWidgets.QMessageBox.critical(iface.mainWindow(), 'Unexpected error',
                                               f'Something went wrong: "{self.exception}".  Please refer to the '
                                               f'log file at {enca.framework.run.get_logfile()} for more details.')
        _tasks.remove(self)


def expand_year(template):
    """Recursively replace all occurrences of the self.year widget as a dict key by the year value.

    This is needed so we can look up widgets by their config path when we catch a
    ConfigError."""
    if type(template) == dict:  # nested dictionary
        result = {}
        for key, value in template.items():
            result_val = expand_year(value)
            if isinstance(key, QtWidgets.QSpinBox):
                result[key.value()] = result_val
            else:
                result[key] = result_val
        return result
    else:
        return template
