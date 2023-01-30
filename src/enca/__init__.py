import logging
import os
import sys

import pyproj

from .run import Run
from .config_check import ConfigError

if sys.version_info[:2] >= (3, 8):
    # TODO: Import directly (no need for conditional) when `python_requires = >= 3.8`
    from importlib.metadata import PackageNotFoundError, version  # pragma: no cover
else:
    from importlib_metadata import PackageNotFoundError, version  # pragma: no cover

try:
    dist_name = 'sys4enca'
    __version__ = version(dist_name)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
finally:
    del PackageNotFoundError

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

ENCA = 'ENCA'
ACCOUNT = 'ENCA_ACCOUNT'
PREPROCESS = 'ENCA_PREPROCESS'

HYBAS_ID = 'HYBAS_ID'
GID_0 = 'GID_0'

class ENCARun(Run):

    component = None  #: ENCA component, to be set in each subclass.
    run_type = None  #: One of ENCA, ACCOUNT, or PREPROCESS
    id_col_statistics = HYBAS_ID
    id_col_reporting = GID_0

    epsg = 3857

    def __init__(self, config):
        super().__init__(config)
        self.root_logger = logger
        try:
            self.aoi_name = config['aoi_name']
            self.tier = config['tier']
        except KeyError as e:
            raise ConfigError(f'Missing config key {str(e)}', [str(e)])

        self.run_dir = os.path.join(self.output_dir, self.aoi_name, str(self.tier), self.run_type, self.component,
                                    self.run_name)

    def version_info(self):
        return f'ENCA version {__version__} using '\
               f'GDAL (osgeo) {version("GDAL")}, rasterio {version("rasterio")}, geopandas {version("geopandas")} '\
               f'numpy {version("numpy")}, pandas {version("pandas")}, '\
               f'pyproj {pyproj.__version__}., PROJ {pyproj.proj_version_str}'
