"""Utilities to validate configurations for all service modules in a generic way.

Each :class:`.run.Run` object has an attribute :attr:`.run.Run.config_template`.  This dictionary describes the
structure and expected content of the configuration for that Run.  The :class:`.run.Run` base class contains a
description of the required configuration common to all services.  Each ecosystem service can extend this dictionary
with the description of its specific configuration.  For example, a (fictional) ``FisheryService`` might extend
``config_template`` as follows::

   self.config_template.update({
        'fish_prices': ConfigItem(check_csv),
        'fish_maps': {
            'bass': ConfigRaster(),
            'salmon': ConfigRaster()
        }
    })

This way, the :meth:`ConfigCheck.validate` method will check for the presence of a configuration key ``fish_prices``,
which passes the tests from the :func:`check_csv` function, and a subsection ``fish_maps`` which
contains two values that should pass the checks defined in :obj:`ConfigRaster`."""

import copy
import glob
import logging
import os
from functools import reduce

import fiona
import pandas as pd
import rasterio
from fiona.errors import FionaError
from pandas.errors import ParserError
from rasterio.errors import RasterioError, RasterioIOError

from .errors import Error
from .geoprocessing import RasterType

logger = logging.getLogger(__name__)


class ConfigError(Error):
    """Subclass to signal errors in the configuration or input files provided by the user.

    :obj:`ConfigError` contains a reference to the config section where the problem is found.  This can be used to
    tell the user which section of the configuration should be changed, or, when using a GUI, which input widget they
    should look at.  For example if there is a problem with the configuration value ``config['input']['maps'][
    'land_use']``, :attr:`ConfigError.path` should have the value ``['input', 'maps', 'land_use']``.

    :param message: Error message.
    :param path: List of keys pointing to the config
    """

    def __init__(self, message, path):
        super().__init__(message)
        self.path = path  #: List of configuration keys pointing to the config value which caused the error.


class ConfigItem:
    """A ConfigItem describes what kind of value is expected at a given position in the config dictionary.

    :param check_function: A function ``check(value, **kwargs)`` by which to check the parameter's value. This
    function should raise :obj:`.errors.Error` if the check fails.
    :param description: Description of the parameter's meaning.
    :param optional: Whether this config parameter may be omitted.
    :param default: Default value.
    :param kwargs: Extra keyword arguments to pass on to ``check_function``.
    """

    def __init__(self, check_function=None, description=None, optional=False, default=None, **kwargs):
        self.description = description
        self._optional = optional
        self._check_function = check_function  #: Function to check the configured value with.
        self._check_kwargs = kwargs  #: Keywoard arguments for :attr:`_check_function`.
        self._default = default
        self._path = None
        self._config = None
        self._configcheck = None
        self._years = None  # : for yearly config items: single year, otherwise: all years which are processed.
        self.value = None  #: Value found after a successful call to :meth:`check`.

    def check(self):
        """Check if :attr:`_config` contains a value for this :class:`ConfigItem`, and check that value."""
        if self._path is None or self._config is None:
            raise RuntimeError('ConfigItem.check() called but _path and _config are not set.  This is a bug. '
                               f'{self._path}, {self._config}')
        # Users may omit ConfigItems with a default, or ConfigItems which are optional.  To avoid having to check for
        # presence of optional config keys or sections in the rest of our code, we complete the input config dict,
        # filling in missing sections with default values.
        # 1. for nested config sections, first look up the parent section (creating empty subsections if needed)
        if len(self._path) > 1:
            parent_section = reduce(lambda section, key: section.setdefault(key, {}), self._path[:-1], self._config)
        else:
            parent_section = self._config
        # 2. Look up the config value, replacing the default if no value is there.
        try:
            value = dict.setdefault(parent_section, self._path[-1], self._default)
        except TypeError:
            # We get a TypeError when 'parent_section' is not a dict.  This happens when we expect to find a dict for a
            # subsection (e.g. {2000: path_to_2000_data, 2006: ....}, but find a single value instead.  This means the
            # input configuration has the wrong format.
            raise ConfigError('Incorrect input configuration, '
                              f'could not look up {": ".join(str(x) for x in self._path)}.', self._path)
        if not value:  # value is None or empty string
            if self._optional:
                return  # Optional items may be left empty/None.  Return here because we can't check these values.
            else:
                raise ConfigError(f'No value for configuration key {": ".join(str(x) for x in self._path)}.',
                                  self._path)

        # If we get here, we have a value:
        try:
            self.check_value(value, **self._check_kwargs)
        except ConfigError:
            # If a ConfigItem we depend on gets checked, we may get a ConfigError here -> pass it on without change.
            raise
        except Exception as e:
            raise ConfigError(str(e), self._path)
        self.value = value

    def check_value(self, value, **kwargs):
        if self._check_function is not None:
            self._check_function(value, **kwargs)
        pass

    def set_config_refs(self, configcheck: 'ConfigCheck', config, path, years):
        """Link the ConfigItem to a config dict that we want to check.

        :param configcheck: :obj:`ConfigCheck` which this ConfigItem belongs to.
        :param config: Dictionary which contains a configuration.
        :param path: List of keys by which to look up the value in ``config`` and nested sub-dictionaries.
        :param years: List of years for which this ConfigItem is valid (either single year or all years).
        """
        self._configcheck = configcheck
        self._config = config
        self._path = path
        self._years = years


class ConfigRef(list):
    """Describes a list of keys identifying an item in a nested config dict.

    `ConfigRef('land_cover', 2000)` indicates a reference to config['land_cover'][2000].  This just a simple wrapper
    for the builtin :obj:`list` class, meant to make the purpose more clear.
    """

    def __init__(self, *path):
        super().__init__()
        for key in path:
            self.append(key)


class ConfigChoice(ConfigItem):
    """Checks a config setting where the user must pick a value from a fixed set of choices."""

    def __init__(self, *choices, **kwargs):
        super().__init__(**kwargs)
        self._choices = set(choices)

    def check_value(self, value):
        if value not in self._choices:
            raise Error(f'Incorrect value "{value}", please choose from {{'
                        + ', '.join(str(x) for x in self._choices)
                        + '}.')


class ConfigShape(ConfigItem):

    def check_value(self, shape: str) -> None:
        """Check if ``shapes`` can be opened using :func:`fiona.open`.

        :param shape: Path to a shapefile.
        """
        check_exists(shape)
        try:
            with fiona.open(shape):
                pass
        except FionaError as e:
            raise Error(f'Failed to open shape file {shape}: {e}.')


class RasterMixin:

    def check_raster(self, file):
        """Check if ``file`` can be opened using :func:`rasterio.open`, and has the required minimum extent.

        The input statistics shapes for the current run should be contained in the raster extent.

        :param file: Name of the file.
        """
        try:
            with rasterio.open(file, 'r'):
                pass
        except (RasterioError, RasterioIOError) as e:
            raise Error(f'Failed to open raster file {file}: {e}')

        # Check if raster extent contains all regions we need.
        self._configcheck.accord.check_raster_contains_ref_extent(file)


class ConfigRaster(ConfigItem, RasterMixin):

    def __init__(self, check_projected=False, check_unit=False, raster_type=RasterType.CATEGORICAL, **kwargs):
        super().__init__(**kwargs)
        self.check_projected = check_projected
        self.check_unit = check_unit
        self.type = raster_type
        if raster_type == RasterType.ABSOLUTE_VOLUME:
            # Absolute volume data sets must always be provided in a projected coordinate system in the right units.
            self.check_projected = True
            self.check_unit = True

    def check_value(self, file: str) -> None:
        """Check if ``file`` can be opened using :func:`rasterio.open`, and has the required extent.

        :param file: Name of the file.
        """
        check_exists(file)
        self.check_raster(file)


class ConfigRasterDir(ConfigItem, RasterMixin):
    """Checks a config item that represents a directory where every .tif file is a raster to be read as input."""

    def __init__(self, check_projected=False, check_unit=False, raster_type=RasterType.CATEGORICAL, **kwargs):
        super().__init__(check_projected, check_unit, **kwargs)
        self.check_projected = check_projected
        self.check_unit = check_unit
        self.type = raster_type

    def check_value(self, dir: str) -> None:
        r"""Check if ``dir`` is a directory, and if all files ending in ``\*.tif`` are valid raster files."""
        if not os.path.isdir(dir):
            raise Error(f'"{dir}" is not a directory.')

        raster_files = glob.glob(os.path.join(dir, '*.tif')) + glob.glob(os.path.join(dir, '*.tiff'))

        if not raster_files:
            raise Error(f'"{dir}" does not contain any with extension ".tif" or ".tiff".')

        for file in raster_files:
            self.check_raster(file)


class ConfigKeyValue(ConfigItem):
    """Key-value dictionary where the values are a set of ConfigItems.

    For example: a dictionary of labels and corresponding rasters.
    """

    def __init__(self, value_check: ConfigItem):
        super().__init__()
        self._value_check = value_check
        self._configitems = None

    def check_value(self, key_value_pairs):
        assert isinstance(key_value_pairs, dict)
        self._configitems = {}
        for config_key, config_val in key_value_pairs.items():
            checker = copy.copy(self._value_check)
            checker.set_config_refs(self._configcheck, self._config, self._path + [config_key], self._years)
            checker.check()
            self._configitems[config_key] = checker

    def set_config_refs(self, configcheck: 'ConfigCheck', config, path, years):
        super().set_config_refs(configcheck, config, path, years)

    def items(self):
        assert self._configitems is not None, 'Should be called only after config validation()'
        return self._configitems.items()


class ConfigCheck:

    def __init__(self, config_template, config, accord, add_progress=lambda p: None):
        # Presence of 'years' should have been checked in Run init code.  If it is not available here, that's a bug:
        assert 'years' in config, 'Config["years"] is missing.  This is a bug.'
        self.config = config
        self.accord = accord
        self.years = config['years']  #: List of years processed in this run.
        self.config_items = self._compile(config_template)  #: Dictionary of :obj:`ConfigItem`.
        self._validated = False

    def _compile(self, checked_section, path=[], this_year=None):
        """Expand YEARLY config keys and link ConfigItems with their value inside the config dict."""
        if isinstance(checked_section, dict):
            result = {}
            for key, value in checked_section.items():
                logger.debug('Compiling %s %s', '.'.join(str(x) for x in path), key)
                if key is YEARLY:
                    if this_year is not None:  # We have already expanded YEARLY once before reaching this point -> no
                        # further YEARLY keys are allowed.
                        raise RuntimeError('Config template contains nested YEARLY items.')
                    # Expand YEARLY into a subsection for each year:
                    for year in self.years:
                        result[year] = self._compile(copy.deepcopy(value), path + [year], this_year=year)
                else:
                    sub_path = path + [key]
                    result[key] = self._compile(value, sub_path, this_year)
        else:
            logger.debug('Set config refs on %s', '.'.join([str(x) for x in path]))
            result = checked_section
            if this_year is not None:  # we have config item for specific year:
                years = [this_year]
            else:
                years = self.years
            result.set_config_refs(self, self.config, path, years)
        return result

    def validate(self, validate_section=None):
        """Validate if :attr:`config` satisfies the requirements of :attr:`config_items`.

        Raises an exception if a required configuration parameter is missing, or if its value doesn't pass a check.
        """
        if validate_section is None:
            self.validate(self.config_items)
            self._validated = True
        else:
            for key, item in validate_section.items():
                if isinstance(item, dict):  # sub-section -> recurse
                    self.validate(item)
                elif isinstance(item, ConfigItem):  # ConfigItem -> check
                    item.check()
                else:
                    raise RuntimeError(f'Unexpected entry in configuration: "{key}: {item}".  This is a bug.')

    def look_up_item(self, path, year):
        """Look up a ConfigItem from a compiled configuration, and return the list of its values.

        :param path: List of keys, either strings or 'YEARLY'.
        :param year: Year for which to lookup 'YEARLY' items.
        :return: The checked value of the ConfigItem
        """
        item = self.config_items
        for key in path:
            if key is YEARLY:
                item = item[year]
            else:
                item = item[key]
        if item.value is None:
            item.check()
        return item.value

    def get_configitems(self, item_type, config_items=None, path=[]):
        """Recursively process all config_items, and return a list of all items of the requested type."""
        if config_items is None:
            config_items = self.config_items
        result = []
        for key, val in config_items.items():
            sub_path = path + [key]
            if isinstance(val, dict) or isinstance(val, ConfigKeyValue):
                result = result + self.get_configitems(item_type, val, sub_path)
            elif isinstance(val, item_type):
                result.append(val)
        return result


def YEARLY():
    """Dummy function, only used as a unique symbol to label yearly config items."""
    pass


def check_exists(file):
    """Check if ``file`` exists, raise :obj:`.errors.Error` otherwise."""
    if not os.path.exists(file):
        raise Error(f'File "{file}" does not exist.')


def check_csv(file, required_columns=[], unique_columns=[], allow_missing=True, dtypes=None, delimiter=None):
    """Check if ``file`` can be read using :func:`pandas.read_csv`.

    Use the optional arguments to perform additional checks.

    :param file:
    :param required_columns: List of columns which must be present in the CSV file.
    :param unique_columns: List of columns (or combinations of columns) which may not hold duplicate values.
    :param allow_missing: If `False`, the table may not contain any missing values.
    """
    check_exists(file)
    try:
        # TODO more elaborate checks for valid separator etc?
        # TODO inspect file size first, and skip the check (with a warning) for very large files?
        data = pd.read_csv(file, comment='#', delimiter=delimiter)
    except ParserError as e:
        raise Error(f'{file} is not a valid CSV file: {e}.')

    for field in required_columns:
        if isinstance(field, tuple):
            # can use tuple to specify a list of alternatives
            if not any(name in data for name in field):
                raise Error(f'{file} must contain at least one the following columns: {", ".join(field)}.')
        elif field not in data:
            raise Error(f'{file} does not contain required column {field}.')

    if dtypes:
        data_convert = data.astype(dtypes)
        # Check if conversion to dtypes succeeded:
        for col in dtypes:
            if col not in data_convert:
                continue  # a column listed in dtypes may be optional
            missing = data_convert[col].isna()
            if missing.any():
                first_missing = data[col][missing].iloc[0]  # Show the first bad value in the error message.
                raise Error(f'CSV file {file} has unexpected value in column {col}, item {missing.idxmax()}: '
                            f'"{first_missing}".')
        data = data_convert

    for field in unique_columns:
        if field in data:  # a unique column is not necessarily a required column ;-)
            if data.duplicated(subset=field).any():
                raise Error(f'Table {file} contains duplicate entries for {field}.')

    if not allow_missing and data.isna().any(axis=None):
        raise Error(f'Table {file} has missing data.')
