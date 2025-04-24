# General python package imports
import os
import yaml

import traceback

# QGIS package imports
from qgis.core  import Qgis, QgsApplication, QgsMessageLog, QgsSettings, QgsTask
from qgis.gui   import QgsDoubleSpinBox, QgsFileWidget
from qgis.PyQt  import QtCore, QtGui, QtWidgets, uic
from qgis.PyQt.QtCore import QLocale, pyqtSignal, QCoreApplication
from qgis.utils import iface

# imports of SYS4ENCA plugin code

from enca_plugin.help import show_help
from enca_plugin.qgis_tools import load_vector_layer
from enca_plugin.qt_tools import expand_template, writeWidget

from enca_plugin.exe_utils import run_in_enca_env_no_wait, configure_environment

from enca_plugin.cfg import REP_ID, MSG_LOG_TAG, YEARLY, SOFTWARE
from enca_plugin.cfg.error_values import RUN_OK, ERROR_PROCESSING, ERROR_CONFIG, ERROR_OTHER, RUN_WARN, CANCEL
from enca_plugin.cfg import component_names, runtype

import enca_plugin.cfg.carbon as carbon
import enca_plugin.cfg.infra as infra
import enca_plugin.cfg.leac as leac
import enca_plugin.cfg.water as water

import enca_plugin.cfg.carbon.agriculture as carbon_agriculture
import enca_plugin.cfg.carbon.fire as carbon_fire
import enca_plugin.cfg.carbon.fire_vuln as carbon_fire_vuln
import enca_plugin.cfg.carbon.forest as carbon_forest
import enca_plugin.cfg.carbon.livestock as carbon_livestock
import enca_plugin.cfg.carbon.npp as carbon_npp
import enca_plugin.cfg.carbon.soil as carbon_soil
import enca_plugin.cfg.carbon.soil_erosion as carbon_soil_erosion

import enca_plugin.cfg.water.drought_vuln as water_drought_vuln
import enca_plugin.cfg.water.precipitation_evapotranspiration as water_precip_evapo
import enca_plugin.cfg.water.river_length_pixel as water_river_length_px
import enca_plugin.cfg.water.usage as water_usage

import enca_plugin.cfg.total as total
import enca_plugin.cfg.trend as trend

FORM_CLASS, BASE_CLASS = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "enca_plugin_dockwidget_base.ui")
)

# We need to keep a reference to our tasks prevent tasks from "disappearing" when we pass them on to the taskmanager.
_enca_tasks = []  #: global reference to currently launched tasks.

#:  names of input widgets per component.
# Note: names must match the name of the corresponding ui widget *and* the config key to which they refer.
component_input_widgets = [
    (
        carbon.component,
        [
            (carbon.FOREST_AGB, [YEARLY]),
            (carbon.FOREST_BGB, [YEARLY]),
            (carbon.FOREST_LITTER, [YEARLY]),
            (carbon.SOIL, [YEARLY]),
            (carbon.LIVESTOCK, [YEARLY]),
            (carbon.NPP, [YEARLY]),
            (carbon.AGRICULTURE_CEREALS, [YEARLY]),
            (carbon.AGRICULTURE_FIBER, [YEARLY]),
            (carbon.AGRICULTURE_FRUIT, [YEARLY]),
            (carbon.AGRICULTURE_OILCROP, [YEARLY]),
            (carbon.AGRICULTURE_PULSES, [YEARLY]),
            (carbon.AGRICULTURE_ROOTS, [YEARLY]),
            (carbon.AGRICULTURE_CAFE, [YEARLY]),
            (carbon.AGRICULTURE_VEGETABLES, [YEARLY]),
            (carbon.AGRICULTURE_SUGAR, [YEARLY]),
            (carbon.WOODREMOVAL, [YEARLY]),
            (carbon.SOIL_EROSION, [YEARLY]),
            (carbon.ILUP, [YEARLY]),
            (carbon.CEH1, [YEARLY]),
            (carbon.CEH4, [YEARLY]),
            (carbon.CEH6, [YEARLY]),
            (carbon.CEH7, [YEARLY]),
            (carbon.COW, [YEARLY]),
            (carbon.FIRE, [YEARLY]),
            (carbon.FIRE_SPLIT, [YEARLY]),
            (carbon.FIRE_INTEN, [YEARLY]),
        ],
    ),
    (
        water.component,
        [
            (water.USE_AGRI, [YEARLY]),
            (water.USE_MUNI, [YEARLY]),
            (water.EVAPO_RAINFED, [YEARLY]),
            (water.PRECIPITATION, [YEARLY]),
            (water.EVAPO, [YEARLY]),
            water.LT_PRECIPITATION,
            water.LT_EVAPO,
            (water.DROUGHT_VULN, [YEARLY]),
            (water.LEAC_RESULT, [YEARLY]),
            water.RIVER_LENGTH,
            water.LT_OUTFLOW,
            water.AQUIFER,
            water.SALINITY,
            water.HYDRO_LAKES,
            water.GLORIC,
            water.LC_LAKES,
        ],
    ),
    (
        water_precip_evapo.component,
        [
            water_precip_evapo.WORLDCLIM,
            water_precip_evapo.CGIAR_AET,
            (water_precip_evapo.COPERNICUS_PRECIPITATION, [YEARLY]),
            water_precip_evapo.LC_RAINFED_AGRI,
        ],
    ),
    (
        water_usage.component,
        [
            (
                water_usage.GHS_POP,
                [
                    water_usage.Y1990,
                    water_usage.Y1995,
                    water_usage.Y2000,
                    water_usage.Y2005,
                    water_usage.Y2010,
                    water_usage.Y2015,
                    water_usage.Y2020,
                    water_usage.Y2025,
                    water_usage.Y2030,
                ],
            ),
            water_usage.MUNICIPAL,
            water_usage.AGRICULTURAL,
            water_usage.LC_AGRI,
        ],
    ),
    (
        water_drought_vuln.component,
        [
            (water_drought_vuln.DROUGHT_CODE, [YEARLY]),
            water_drought_vuln.DROUGHT_CODE_LTA,
        ],
    ),
    (water_river_length_px.component, [water_river_length_px.GLORIC]),
    (
        carbon_npp.component,
        [(carbon_npp.GDMP_DIR, [YEARLY]), carbon_npp.GDMP_2_NPP],
    ),
    (
        carbon_soil.component,
        [
            carbon_soil.SEAL_ADJUST,
            carbon_soil.SOC,
            carbon_soil.SOC_MANGROVES,
            carbon_soil.MANGROVE_CLASSES,
            carbon_soil.NONSOIL_CLASSES,
            carbon_soil.URBAN_CLASSES,
        ],
    ),
    (
        carbon_soil_erosion.component,
        [
            carbon_soil_erosion.R_FACTOR_1,
            carbon_soil_erosion.R_FACTOR_25,
            carbon_soil_erosion.SOIL_CARBON_10,
            carbon_soil_erosion.SOIL_CARBON_20,
            carbon_soil_erosion.SOIL_CARBON_30,
            (carbon_soil_erosion.SOIL_LOSS, [YEARLY]),
        ],
    ),
    (
        carbon_livestock.component,
        [
            (carbon_livestock.LIVESTOCK_CARBON, carbon_livestock.livestock_types),
            (carbon_livestock.LIVESTOCK_DIST, carbon_livestock.livestock_types),
            (carbon_livestock.WEIGHTS, carbon_livestock.livestock_types),
        ],
    ),
    (
        carbon_fire_vuln.component,
        [
            (carbon_fire_vuln.SEVERITY_RATING, [YEARLY]),
            carbon_fire_vuln.SEVERITY_RATING_LTA,
        ],
    ),
    (
        carbon_agriculture.component,
        [
            carbon_agriculture.AGRICULTURE_DISTRIBUTION,
            carbon_agriculture.AGRICULTURE_STATS,
        ],
    ),
    (
        carbon_fire.component,
        [(carbon_fire.BURNT_AREAS, [YEARLY]), carbon_fire.FOREST_BIOMASS],
    ),
    (
        carbon_forest.component,
        [
            carbon_forest.FAOFRA_AGB,
            carbon_forest.FAOFRA_BGB,
            carbon_forest.FAOFRA_LITTER,
            carbon_forest.FAOFRA_WREM,
            carbon_forest.FOREST_LC_CLASSES,
            carbon_forest.LAND_COVER_FRACTION,
            carbon_forest.WOOD_REMOVAL_LIMIT,
        ],
    ),
    (
        infra.component,
        [
            infra.REF_YEAR,
            infra.REF_LANDCOVER,
            (
                infra.PATHS_INDICES,
                [(indices, [YEARLY]) for indices in list(infra.INDICES.keys())],
            ),
            (infra.GENERAL, [infra.LC_URBAN, infra.LC_WATER]),
            infra.LUT_GBLI,
            infra.NATURALIS,
            infra.OSM,
            (infra.CATCHMENTS, [infra.CATCHMENT_6, infra.CATCHMENT_8, infra.CATCHMENT_12]),
            infra.DAMS,
            infra.GLORIC,
            (infra.LEAC_RESULT, [YEARLY]),
            (infra.TREE_COVER, [YEARLY]),
        ],
    ),
    (
        leac.component,
        [
            leac.REF_YEAR,
            leac.REF_LANDCOVER,
            leac.LUT_CT_LC,
            leac.LUT_CT_LCF,
            leac.LUT_LC,
            leac.LUT_LCFLOWS,
        ],
    ),
    (
        total.component,
        [
            total.INFRA_RESULT,
            total.CARBON_RESULT,
            total.WATER_RESULT,
            total.ECUADJ_CARBON,
            total.ECUADJ_WATER,
            total.ECUADJ_INFRA,
        ],
    ),
    (trend.component, [trend.TOTAL_RESULT]),
]

#: vector output files to be loaded after a run, with visualization parameters (keyword arguments for
component_vector_layers = {
    carbon.component: [
        (
            os.path.join("temp", "CARBON_Indices_SELU_{year}.gpkg"),
            dict(layer_name="NEACS [ha]", attribute_name="C10_ha"),
        )
    ],
    water.component: [
        (
            os.path.join("temp", "WATER_Indices_SELU_{year}.gpkg"),
            dict(layer_name="TOTuseEW", attribute_name="W9_ha", color_ramp="Blues"),
        )
    ],
    carbon_livestock.component: [],
}

def findChild(widget: QtWidgets.QWidget, name: str):
    """Helper function to deal with the fact that .ui compilation does not allow duplicate widget names.

    When the .ui file gets compiled, widgets with duplicate names get a numerical suffix (e.g. 'run_name'
    -> 'run_name2').  Here, we search using a regular expressions, and return the first and widget.  Raise an error
    if not exactly one matching widget is found.
    """
    try:
        (result,) = widget.findChildren(
            QtWidgets.QWidget,
            QtCore.QRegularExpression(
                f"^{QtCore.QRegularExpression.escape(name)}_\\d*$"
            ),
        )
    except ValueError:
        QgsMessageLog.logMessage(
            f'findChildren() did not find a unique result for widget name "{name}"',
            tag=MSG_LOG_TAG,
            level=Qgis.Critical,
        )
        raise
    return result

class EncaPluginDockWidget(QtWidgets.QDockWidget, FORM_CLASS):
    """ Class for main plugin widget logic

    """

    closingPlugin = pyqtSignal()

    def __init__(self, parent=None):
        """ Initializes the SYS4ENCA tool widget

        :param parent: parent widget
        """

        super(EncaPluginDockWidget, self).__init__(parent)

        # Get the current locale from QGIS settings
        settings = QgsSettings()
        locale = settings.value("locale/userLocale", QLocale.system().name())       

        # Set up the user interface from Designer.
        # After setupUI you can access any designer object by doing
        # self.<objectname>, and you can use autoconnect slots - see
        # https://doc.qt.io/qt-5/designer-using-a-ui-file-python.html
        # #widgets-and-dialogs-with-auto-connect
        self.setupUi(self)
        
        self.set_up_component_dropdowns(locale)
        self.set_up_carbon_livestock(locale)
        self.set_up_infra()

        self.toolbar = QtWidgets.QToolBar()
        self.toolbar.setIconSize(iface.iconSize(dockedToolbar=True))

        self.loadact = QtWidgets.QAction(
            QtGui.QIcon(":/plugins/enca_plugin/mActionFileOpen.svg"),
            self.tr("Load Configuration"),
            self,
        )
        self.loadact.triggered.connect(self.loadConfig)
        self.saveact = QtWidgets.QAction(
            QtGui.QIcon(":/plugins/enca_plugin/mActionFileSaveAs.svg"),
            self.tr("Save Configuration"),
            self,
        )
        self.saveact.triggered.connect(self.saveConfig)
        self.toolbar.addAction(self.loadact)
        self.toolbar.addAction(self.saveact)
        self.toolbar.addSeparator()

        self.runact = QtWidgets.QAction(
            QtGui.QIcon(":/plugins/enca_plugin/play-button-svgrepo-com.svg"),
            self.tr("Run"),
            self,
        )
        self.runact.triggered.connect(self.run)
        self.toolbar.addAction(self.runact)

        self.continue_run = QtWidgets.QCheckBox(
            self.tr("Continue existing run")
        )
        self.continue_run.setObjectName("continue_run")
        self.toolbar.addWidget(self.continue_run)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred
        )
        self.toolbar.addWidget(spacer)

        self.helpact = QtWidgets.QAction(self.tr("Help"), self)
        self.helpact.triggered.connect(show_help)
        self.toolbar.addAction(self.helpact)

        self.toolbarLayout.addWidget(self.toolbar)

        # Reporting area shapefiles should have 'REP_ID' id attribute
        self.reporting_areas.set_id_label(REP_ID)

        # Initialize Tier level combobox
        self.tier.addItem("1", 1)
        self.tier.addItem("2", 2)
        self.tier.addItem("3", 3)

        self.config_template = {
            "years": [self.year],
            "output_dir": self.output_dir,
            "aoi_name": self.aoi_name,
            "statistics_shape": self.data_areas,
            "reporting_shape": self.reporting_areas._filewidget,
            "selected_regions": self.reporting_areas._selected_regions,
            "admin_boundaries": self.admin_boundaries,
            "land_cover": {self.year: self.land_cover},
            "continue": self.continue_run,
            "tier": self.tier,
        }
        self.component_templates = self.build_template_tree(
            component_input_widgets, self.run_types
        )

        configure_environment()

    def set_up_component_dropdowns(self, locale: str):
        """Fill the dropdown menu for preprocessing/run/ accounts tabs, and connect signals."""
        for i in range(self.run_types.count()):
            tab = self.run_types.widget(i)
            dropdown = findChild(tab, "component")
            components_stack = findChild(tab, "components_stack")
            for j in range(components_stack.count()):
                name = components_stack.widget(j).objectName()[
                    :-1
                ]  # cut off trailing '_'
                dropdown.addItem(component_names.get_component_long_name(name, locale), name)
            dropdown.currentIndexChanged.connect(components_stack.setCurrentIndex)

    def set_up_carbon_livestock(self, locale: str):
        """Set up Carbon livestock key-value widgets."""
        self.livestock_carbon_.setLayout(QtWidgets.QFormLayout())
        self.livestock_distribution_.setLayout(QtWidgets.QFormLayout())
        self.weights_.setLayout(QtWidgets.QFormLayout())
        for key in carbon_livestock.livestock_types:
            carbon_widget = QgsFileWidget(self, objectName=key + "_")
            carbon_widget.setFilter(self.tr("CSV (*.csv);; All Files (*.*)"))
            self.livestock_carbon_.layout().addRow(
                carbon_livestock.get_livestock_long_name(key, locale), carbon_widget
            )
            distribution_widget = QgsFileWidget(self, objectName=key + "_")
            distribution_widget.setFilter(self.tr("Geotiff (*.tiff *.tif);; All Files (*.*)"))
            self.livestock_distribution_.layout().addRow(
                carbon_livestock.get_livestock_long_name(key, locale), distribution_widget
            )
            widget_weight = QgsDoubleSpinBox(self, objectName=key + "_")
            widget_weight.setRange(0.0, 1000.0)
            self.weights_.layout().addRow(
                carbon_livestock.get_livestock_long_name(key, locale), widget_weight
            )

    def set_up_infra(self):
        """Set up Infra indices input widgets."""
        widget_infra = self.ENCA_.findChild(
            QtWidgets.QWidget, infra.component + "_"
        )
        widget_infra_indices = widget_infra.findChild(
            QtWidgets.QGroupBox, "paths_indices_"
        )
        widget_infra_indices.setLayout(QtWidgets.QFormLayout())
        for idx, label in infra.INDICES.items():
            # Add suffix '_' to index widget object names because they end in an integer
            widget_infra_indices.layout().addRow(
                f"{idx}. {label}", QgsFileWidget(self, objectName=f"{idx}_")
            )

    def saveConfig(self):
        """Handle 'save' button action: let user pick the yaml config file in a dialog window, then
        save current ui state to the selected file."""
        try:
            cfg=self.make_config()
            default_cfg_file = "config.yaml"
            if 'run_name' in cfg and len(cfg['run_name'].strip())>0:
                default_cfg_file = cfg['run_name'] + '.yaml'
            filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, self.tr("Save Config File"), default_cfg_file, "*.yaml")
            if not filename:
                return
            self._write_config_to_file(cfg, filename)
        except Exception as e:
            msg = [self.tr('An unexpected error occurred while saving config.'), self.tr('Please contact {0} tool support with following message:').format(SOFTWARE),'']
            msg.extend(traceback.format_exception(e, limit=1, chain=False))
            QtWidgets.QMessageBox.critical(self, self.tr('{0} plugin error').format(SOFTWARE), os.linesep.join(msg))

    @staticmethod
    def _write_config_to_file(config: dict, filename: str):
        """Write config dictionary to a file on disk.

        :param config: configuration dictionary
        :param filename: yaml file to be written
        """
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, 'wt') as f:
            f.write(yaml.dump(config))

    def loadConfig(self):
        """Load a yaml config file."""
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            self.tr("Load Config File"),
            "",
            self.tr("Config files (*.yaml);; All files (*.*)"),
        )
        if not filename:
            return

        try:
            with open(filename, "rt") as f:
                config = yaml.safe_load(f)

            # Clear existing configuration:
            for widget in self.list_config_widgets():
                writeWidget(widget, None)

            # Handle year selection and reporting regions manually:
            self.year.setValue(config["years"][0])
            self.reporting_areas.setShapefile(config.get("reporting_shape"))

            # remaining config values can be read automatically using the config templates
            main_template = {
                key: value
                for key, value in self.config_template.items()
                if key not in ("years", "reporting_regions")
            }
            self.load_template(config, main_template)

            # select the right tab (preprocessing/components/accounts), and the right component within that tab:
            component_name = config["component"]
            run_type = runtype.component_run_types[component_name]
            # select tab:
            run_type_tab = findChild(self.run_types, run_type.name)
            self.run_types.setCurrentWidget(run_type_tab)
            # select component:
            component_page = findChild(run_type_tab, component_name)
            component_combo = findChild(run_type_tab, "component")
            idx = component_combo.findData(component_name, QtCore.Qt.UserRole)
            component_combo.setCurrentIndex(idx)

            # handle run name:
            try:
                findChild(component_page, "run_name").setText(config["run_name"])
            except ValueError:
                pass  # Not all input pages have a run_name widget yet
            # read other config values using the template
            self.load_template(
                config, {component_name: self.component_templates[component_name]}
            )
        except BaseException as e:
            msg = [self.tr('An unexpected error occurred while loading config from'), filename, self.tr('Please contact {0} tool support with following message:').format(SOFTWARE),'']
            msg.extend(traceback.format_exception(e, limit=1, chain=False))
            QtWidgets.QMessageBox.critical(self, self.tr('{0} plugin error').format(SOFTWARE), os.linesep.join(msg))

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
        result = self.list_widgets(self.config_template) + self.list_widgets(
            self.component_templates
        )
        return result

    def load_template(self, config, template):
        """Update the widgets listed in the template with values from config.

        We use recursion to process te nested dictionary structure of the template and config.  This is more or less
        the inverse of expand_template().

        NOTE: Assumes widget self.year was already set to the correct value.

        """
        if type(template) == dict:
            for key, template_value in template.items():
                # special case for yearly datasets ...
                if key == self.year:
                    key = self.year.value()
                try:
                    self.load_template(config[key], template_value)
                except KeyError as e:
                    QgsMessageLog.logMessage(
                        self.tr("Item missing in the loaded configuration: ") + str(e),
                        tag=MSG_LOG_TAG,
                        level=Qgis.Warning,
                    )
        elif type(template) in (int, bool, str):
            # We're only interested in the widgets referenced from the template.
            pass
        elif isinstance(template, QtCore.QObject):
            writeWidget(template, config)
        else:
            raise RuntimeError(self.tr("Failed to process template item {0}").format(str(template)))

    def run(self):
        """Handle the 'Run' button action: set up a processing task that runs one of the SYS4ENCA components and submit
        it to the QGIS task manager.

        """
        try:
            config = self.make_config()
            widget_dict = self.expand_year(self.make_template)

            component_name = config["component"]
               
            cfg_file = "config.yaml"
            if 'run_name' in config and len(config['run_name'].strip())>0:
                run_name = config['run_name'].strip()
                cfg_file = run_name + '.yaml'
        
            cfg_path = os.path.join(config['output_dir'], cfg_file)    
            self._write_config_to_file(config, cfg_path)

        except Exception as e:
            msg = [self.tr('An unexpected error occurred while preparing to launch task.'), self.tr('Please contact {0} tool support with following message:').format(SOFTWARE),'']
            msg.extend(traceback.format_exception(e, limit=1, chain=False))
            QtWidgets.QMessageBox.critical(self, self.tr('{0} plugin error').format(SOFTWARE), os.linesep.join(msg))

        try:
            taskname = f'{component_name} {self.tr('run')} {config["run_name"]}'
            task = EncaTaskCLI(
                taskname,
                component_name,
                config,
                cfg_path,
                widget_dict,
                output_rasters=None,
                output_vectors=component_vector_layers.get(component_name, []),
            )
            _enca_tasks.append(task)
            QgsMessageLog.logMessage(self.tr("Submitting task {0}").format(taskname), tag=MSG_LOG_TAG, level=Qgis.Info)
            QgsApplication.taskManager().addTask(task)
        except Exception as e:
            msg = [self.tr('An unexpected error occurred while launching SYS4ENCA task.'), self.tr('Please contact {0} tool support with following message:').format(SOFTWARE),'']
            msg.extend(traceback.format_exception(e, limit=1, chain=False))
            QtWidgets.QMessageBox.critical(self, self.tr('{0} plugin error').format(SOFTWARE), os.linesep.join(msg))
            raise(e)

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
                result[name] = self.build_template_tree(
                    children, findChild(root_widget, name)
                )
            else:
                assert isinstance(entry, str)  # entry is the name of a widget
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
        component_dropdown = findChild(tab_runtype, "component")
        component_name = component_dropdown.currentData(QtCore.Qt.UserRole)
        template = {
            **self.config_template,
            "component": component_dropdown,
            component_name: self.component_templates[component_name],
        }
        component_widget = findChild(tab_runtype, component_name)
        template["run_type_name"] = runtype.component_run_types[component_name].name
        try:
            template["run_name"] = findChild(component_widget, "run_name")
        except ValueError:  # Some pages are currently incomplete
            pass
        return template

    def expand_year(self, template):
        """Recursively replace all occurrences of the year widget as a dict key by the year value.

        This is needed so we can look up widgets by their config path when we catch a
        ConfigError."""
        # FIXME: does this match non-year QSpinBox'es?
        # note: expand_year is only used for widget_dict for Task class, which does not use it (yet)
        if type(template) == dict:  # nested dictionary
            result = {}
            for key, value in template.items():
                result_val = self.expand_year(value)
                if isinstance(key, QtWidgets.QSpinBox):  # FIXME: does this match non-year QSpinBox'es?
                    result[key.value()] = result_val
                else:
                    result[key] = result_val
            return result
        else:
            return template 

class EncaTaskCLI(QgsTask):
    """Class for Qgis tasks that start SYS4ENCA component models via their command-line interface (CLI) """
    def __init__(self, description, component, config, config_file, widget_dict, output_rasters=None, output_vectors=None):
        super().__init__(description)

        self.component = component
        self.config = config
        self.widget_dict = widget_dict
        self.output_rasters = output_rasters or []
        self.output_vectors = output_vectors or []
        self.process = None
        self.config_file = config_file
        self.errors = []
        self.warnings = []
        self.return_code = 0
        self.exception = None

    def cancel(self):
        """When the task is cancelled by user, terminate the subprocess."""
        super().cancel()
        if self.process is not None:
            try:
                self.process.terminate()
            except:
                pass
            QgsMessageLog.logMessage(self.tr('Task was cancelled by user'), tag=MSG_LOG_TAG, level=Qgis.Warning)
            self.errors = [self.tr('Run was cancelled by user.')]
            self.return_code = CANCEL

    def run(self) -> bool:
        """Run the SYS4ENCA component in a subprocess.

        :return: boolean indicating if run is successful (True) or not, based on subprocess' return code
        """
        return_code = RUN_OK
        try:
            qgis_version = 'QGIS ' + Qgis.version()
            #continue_run = ''
            #if 'continue' in self.config and self.config['continue']:
            #    continue_run = '--continue'
            #cmd = f'python -m enca --component {self.component} {continue_run} --verbose --started-from "{qgis_version}" "{self.config_file}"'
            # Only specify the cmd options that need to be overruled from / are missing in config
            # e.g. --continue flag is not needed, as it is spefified as continue:true|false in the yaml config file
            cmd = f'python -m enca --component {self.component} --verbose --started-from "{qgis_version}" "{self.config_file}"'
            QgsMessageLog.logMessage(self.tr('Launching {0} component {1} via command').format(SOFTWARE,self.component), tag=MSG_LOG_TAG, level=Qgis.Info)
            QgsMessageLog.logMessage('    '+cmd, tag=MSG_LOG_TAG, level=Qgis.Info)
            with run_in_enca_env_no_wait(command=cmd) as process:
                self.process = process
                QgsMessageLog.logMessage(self.tr('Process ')+ str(process) +self.tr(' running. Awaiting its completion and output.'), tag=MSG_LOG_TAG, level=Qgis.Info)
                (outs,errs) = process.communicate()
                QgsMessageLog.logMessage(self.tr('Task output:'), tag=MSG_LOG_TAG, level=Qgis.Info)

                # Parse the stdout/stderr output per line
                # Repeat messages in Message Log, at corresponding QGIS message level
                # Log level from stdout is removed via partition() to avoid repetition (eg QGIS 'CRITICAL' level vs stdout 'ERROR' level)
                # Allow for multi-line comments (eg stacktrace) that include the loglevel only on first line.
                lvl = Qgis.Info
                for line in outs.splitlines():
                    msg = line[:]
                    if 'CRITICAL' in line:
                        lvl = Qgis.Critical
                        msg = line[:].partition('CRITICAL')[2][1:]
                    elif 'ERROR' in line:
                        lvl = Qgis.Critical
                        msg = line[:].partition('ERROR')[2][1:]
                    elif 'WARNING' in line:
                        lvl = Qgis.Warning
                        msg = line[:].partition('WARNING')[2][1:]
                    elif 'INFO' in line:
                        lvl = Qgis.Info
                        msg = line[:].partition('INFO')[2][1:]
                    else:
                        msg = line[:]                        

                    QgsMessageLog.logMessage(msg, tag=MSG_LOG_TAG, level=lvl)
                    if lvl == Qgis.Critical:
                        self.errors.append(line)
                    elif lvl == Qgis.Warning:
                        self.warnings.append(line)                        

            return_code = process.poll()
            QgsMessageLog.logMessage(self.tr('Process exited with ')+ str(return_code), tag=MSG_LOG_TAG, level=Qgis.Info)
        except Exception as e:
            self.exception = e
            return_code = ERROR_OTHER
        finally:
            self.process = None
        self.return_code = return_code
        return return_code == RUN_OK

    def finished(self, result):
        """When task is completed, either log its successful completion or show a massage to user in case of failure.

        :param result: boolean indicating if the task was completed successfully
        """
        if self.config_file and os.path.exists(self.config_file):
            os.remove(self.config_file)
            self.config_file = None
        if self.return_code == RUN_OK:
            QgsMessageLog.logMessage(self.tr('Task completed'), tag=MSG_LOG_TAG, level=Qgis.Success)

            for filename, kwargs in self.output_vectors:
                path = os.path.join(self.config['output_dir'], self.config['run_name'], filename).format(
                    year=self.config["years"][0]
                )
                load_vector_layer(path, **kwargs)

            for raster in self.output_rasters:
                path = os.path.join(self.config['output_dir'], self.config['run_name'], raster)
                iface.addRasterLayer(path)

        elif self.return_code == CANCEL:
            QgsMessageLog.logMessage(self.tr('Task was cancelled'), tag=MSG_LOG_TAG, level=Qgis.Info)
            #QtWidgets.QMessageBox.information(iface.mainWindow(), self.tr('{0} Task cancelled').format(SOFTWARE), str(self.errors))
        elif self.return_code == RUN_WARN:
            msg = [self.tr('Please refer to the log file at '), self.log_file, self.tr('for more details.')]
            if len(self.warnings)>0:
                msg.append(self.tr('Warning(s):'))
                msg.extend(self.warnings)
            QtWidgets.QMessageBox.warning(iface.mainWindow(), self.tr('{0} Task completed with warnings').format(SOFTWARE), os.linesep.join(msg))
        elif self.return_code in [ERROR_PROCESSING, ERROR_CONFIG, ERROR_OTHER]:
            titles = { ERROR_PROCESSING: self.tr('{0} Task processing error').format(SOFTWARE), 
                       ERROR_CONFIG: self.tr('{0} Task configuration error').format(SOFTWARE),
                       ERROR_OTHER: self.tr('{0} Task encountered an unexpected error').format(SOFTWARE)
                     }
            msg = [self.tr('Please refer to the log file in folder '), os.path.join(self.config['output_dir'],self.config['run_name']), self.tr('for more details.')]
            if len(self.errors) > 0:
                msg.append(self.tr('Error message(s):'))
                msg.extend(self.errors)
            if len(self.warnings) > 0 and len(self.errors) == 0:
                msg.append(self.tr('Warning(s):'))
                msg.extend(self.warnings)
            QtWidgets.QMessageBox.critical(iface.mainWindow(), titles[self.return_code], str(os.linesep.join(msg)))
        else:
            raise ValueError(self.tr('Task returned an unsupported value:'),str(self.return_code))

        _enca_tasks.remove(self)

