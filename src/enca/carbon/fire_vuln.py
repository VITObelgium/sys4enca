import glob
import logging
import os

import numpy as np
import rasterio

import enca
from enca.framework.config_check import YEARLY, ConfigItem
from enca.framework.geoprocessing import RasterType, block_window_generator

SEVERITY_RATING = 'fire_severity'
SEVERITY_RATING_LTA = 'fire_severity_lta'


_block_shape = (1024, 1024)


logger = logging.getLogger(__name__)


class CarbonFireVulnerability(enca.ENCARun):

    run_type = enca.RunType.PREPROCESS
    component = 'CARBON_FIRE_VULNERABILITY'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                SEVERITY_RATING: {YEARLY: ConfigItem()},
                SEVERITY_RATING_LTA: ConfigItem()
                }})

    def _start(self):
        for year in self.years:
            # calculate annual average:
            sum = 0
            count = 0

            profile = None

            nc_files = glob.glob(os.path.join(self.config[self.component][SEVERITY_RATING][year], '*.nc'))
            logger.debug('Calculate average fire severity rating for year %s.  Found %s netCDF input files.',
                         year, len(nc_files))
            for f in nc_files:
                with rasterio.open(f) as src:
                    data = src.read(1)
                    valid = data != src.nodata
                    count += valid
                    sum += np.where(valid, data, 0)
                    if profile is None:
                        profile = src.profile

            annual = np.divide(sum, count, where=count != 0, out=sum)
            path_out = os.path.join(self.temp_dir(), f'fire-severity_annual-average_{year}.tif')
            with rasterio.open(path_out, 'w', **dict(profile,
                                                     driver='Gtiff',
                                                     crs='EPSG:4326',
                                                     dtype=np.float32)) as dst:
                dst.update_tags(creator='sys4enca', info=f'annual average of the fire severity rating for year {year}')
                dst.write(annual, 1)

            # Now warp drought code to our AOI
            logger.debug('Warp fire severity annual average to AOI.')
            path_out_aoi = os.path.join(self.temp_dir(), f'fire-severity_annual-average_{year}_ENCA.tif')
            self.accord.AutomaticBring2AOI(path_out, RasterType.ABSOLUTE_POINT, secure_run=True, path_out=path_out_aoi)

            logger.debug('Warp fire severity long-term average to AOI.')
            severity_lta_aoi = os.path.join(self.temp_dir(), 'fire-severity_LTA_ENCA.tif')
            self.accord.AutomaticBring2AOI(self.config[self.component][SEVERITY_RATING_LTA],
                                           RasterType.ABSOLUTE_POINT, secure_run=True, path_out=severity_lta_aoi)

            # Now calculate annual fire vulnerability as ratio between annual and LTA fire severity
            # ratio < 1 means lower vulnerability than LTA; > 1 means ihgher vulnerability / decrease in health
            with rasterio.open(path_out_aoi) as ds_annual, \
                 rasterio.open(severity_lta_aoi) as ds_lta, \
                 rasterio.open(os.path.join(self.temp_dir(), f'fire_vulnerability_ratio_{year}.tif'), 'w',
                               **ds_annual.profile) as ds_ratio, \
                 rasterio.open(os.path.join(self.maps, f'NCA_{self.component}_CEH4_factor_{year}.tif'), 'w',
                               **ds_annual.profile) as ds_index:

                for _, window in block_window_generator(_block_shape, ds_lta.profile['height'], ds_lta.profile['width']):
                    # ratio between annual and long term average fire vulnerability:
                    ratio = ds_annual.read(1, window=window) / ds_lta.read(1, window=window)

                    ds_ratio.write(ratio, window=window, indexes=1)

                    # now we have to calculate a meaningful health indicator for the table work from the vulnerability.
                    #
                    # The idea is that the vulnerability against drought is mainly the change against the normal
                    # state. meaning: the vegetation is adapted to the normal state of the water availablity (plants are
                    # adapted to their area) which is represented by our 40year average in drought code.  The higher the
                    # % above normal state, the higher is the vulnerabilty and the lower the health status.
                    annual_fvhi = np.full_like(ratio, 1, dtype=np.float32)

                    annual_fvhi[(ratio > 1.05) & (ratio < 1.25)] -= 0.05
                    annual_fvhi[(ratio >= 1.25) & (ratio < 1.5)] -= 0.1
                    # >1.5 - 2
                    annual_fvhi[(ratio >= 1.5) & (ratio < 1.75)] -= 0.15
                    annual_fvhi[(ratio >= 1.75) & (ratio < 2)] -= 0.2
                    # >2 - 2.5
                    annual_fvhi[(ratio >= 2) & (ratio < 2.25)] -= 0.25
                    annual_fvhi[(ratio >= 2.25) & (ratio < 2.5)] -= 0.3
                    # >2.5 - 3
                    annual_fvhi[(ratio >= 2.5) & (ratio < 2.75)] -= 0.35
                    annual_fvhi[(ratio >= 2.75) & (ratio < 3)] -= 0.4
                    # >3
                    annual_fvhi[(ratio >= 3) & (~np.isnan(ratio))] -= 0.5

                    ds_index.write(annual_fvhi, window=window, indexes=1)
