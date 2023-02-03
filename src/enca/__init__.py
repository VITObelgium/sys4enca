import logging
import os
import sys

import pandas as pd
import pyproj
import rasterio

from .config_check import ConfigError
from .run import Run
from .geoprocessing import SHAPE_ID, number_blocks, block_window_generator

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
C_CODE = 'C_CODE'

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
        self.maps = os.path.join(self.run_dir, 'maps')
        self.reports = os.path.join(self.run_dir, 'reports')
        self.statistics = os.path.join(self.run_dir, 'statistics')

        logger.debug('Running with config:\n%s', config)

    def _create_dirs(self):
        super()._create_dirs()

        os.makedirs(self.maps, exist_ok=True)
        os.makedirs(self.reports, exist_ok=True)
        os.makedirs(self.statistics, exist_ok=True)

    def version_info(self):
        return f'ENCA version {__version__} using ' \
               f'GDAL (osgeo) {version("GDAL")}, rasterio {version("rasterio")}, geopandas {version("geopandas")} ' \
               f'numpy {version("numpy")}, pandas {version("pandas")}, ' \
               f'pyproj {pyproj.__version__}., PROJ {pyproj.proj_version_str}'

    def area_stats(self, block_shape=(2048, 2048), add_progress=lambda p: None):
        """Count number of pixels per statistics region, and number of overlapping pixels with each reporting region.

        :return: Dataframe with a multiindex from reporting shape and statistics shape indices, and the number of
         pixels in the intersection of each pair (reporting_shape, statistics_shape).
        """
        # counts of overlapping pixels per window
        REPORT_NUM = 'REPORT_NUM'
        STATS_NUM = 'STATS_NUM'
        wdw_blocks = []
        with rasterio.open(self.statistics_raster) as ds_stats, \
                rasterio.open(self.reporting_raster) as ds_reporting:
            nblocks = number_blocks(ds_stats.profile, block_shape)

            for _, window in block_window_generator(block_shape, ds_stats.profile['height'], ds_stats.profile['width']):
                stats = ds_stats.read(1, window=window).flatten()
                reporting = ds_reporting.read(1, window=window).flatten()
                valid = stats != ds_stats.nodata
                df_window = pd.DataFrame({
                    REPORT_NUM: reporting[valid],
                    STATS_NUM: stats[valid]})

                # Remove pixels outside of statistics regions
                wdw_blocks.append(df_window.groupby([REPORT_NUM, STATS_NUM]).size())
                add_progress(100. / nblocks)

        df_overlap = pd.concat(wdw_blocks).groupby([REPORT_NUM, STATS_NUM]).sum().rename('count')

        # Transform REPORT_NUM and STATS_NUM back to statistics and reporting shape id's by joining with corresponding
        # columns of reporting_shape and statistics_shape.
        # ... we need some index resetting and renaming to do this
        df_overlap = df_overlap.to_frame().join(
            self.statistics_shape[SHAPE_ID].rename(STATS_NUM).reset_index().set_index(STATS_NUM)).join(
            self.reporting_shape[SHAPE_ID].rename(REPORT_NUM).reset_index().set_index(REPORT_NUM)).set_index(
            [self.reporting_shape.index.name, self.statistics_shape.index.name]
        )

        return df_overlap
