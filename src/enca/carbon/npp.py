import logging
import os
import re
from contextlib import ExitStack

import pandas as pd
import rasterio

import enca
from enca.framework.config_check import ConfigRasterDir, ConfigItem, YEARLY
from enca.framework.geoprocessing import RasterType, block_window_generator

logger = logging.getLogger(__name__)


GDMP_DIR = 'GDMP_DIR'
GDMP_2_NPP = 'GDMP_2_NPP'

_GDMP_scaling = 0.02
_GDMP_unit = 'kg/ha/day'
_GDMP_nodata = -1

class CarbonNPP(enca.ENCARun):
    """Cabon NPP preprocessing run."""

    run_type = enca.PREPROCESS
    component = 'CARBON_NPP'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                GDMP_DIR: {YEARLY: ConfigRasterDir(raster_type=RasterType.RELATIVE)},
                GDMP_2_NPP: ConfigItem()
            }})

    def _start(self):
        print('Hello from Carbon NPP preprocessing.')
        for year in self.years:
            self.write_npp(year)

    def write_npp(self, year):
        """Add up GDMP values for the year, and convert to NPP.""" 
        gdmp_files = self.config[self.component][GDMP_DIR][year]
        gdmp_2_npp_factor = self.config[self.component][GDMP_2_NPP]

        block_shape = (1024, 1024)
        profile = dict(self.accord.ref_profile, dtype=rasterio.float32, nodata=-9999.)

        with rasterio.open(os.path.join(self.maps, f'{self.component}_tons_{year}.tif'), 'w', **profile) as ds_out, \
             ExitStack() as stack:

            gdmp_datasets = [stack.enter_context(rasterio.open(f)) for f in gdmp_files]
            for _, window in block_window_generator(block_shape, ds_out.profile['height'], ds_out.profile['width']):
                gdmp_year = 0

                for num_days, ds_gdmp in zip(self.get_dmp_days(gdmp_files), gdmp_datasets):
                    gdmp = ds_gdmp.read(1, window=window, masked=True)
                    gdmp_year += (gdmp * _GDMP_scaling * num_days).filled(0)

                # convert to NPP [tonne / ha] (GDMP input is [kg / day / ha])
                npp_year = (gdmp_year / 1000.) * gdmp_2_npp_factor**2

                ds_out.write(npp_year, 1, window=window)
                # TODO metadata

    def get_dmp_days(self, files):
        """Extract the date from DMP filenames, and convert dates to 'numer of days since previous file'."""
        # regular expression to match a date in %Y%m%d%H%M format,
        # i.e. 12 integers, surrounded by underscores or the start or end of the string
        regex_date = re.compile('(?:^|.*_)(\d{12})(?:$|_)')
        dates = pd.to_datetime(pd.Series(regex_date.match(os.path.basename(f))[1] for f in files))
        delta_t = dates.diff()
        delta_t[0] = pd.Timedelta(days=dates[0].dayofyear)  # first diff() result will be NA

        return delta_t.dt.days.to_list()
