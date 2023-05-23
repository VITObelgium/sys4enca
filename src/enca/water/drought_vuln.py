import bisect
import datetime
import logging
import os

import numpy as np
import rasterio

import enca
from enca.framework.config_check import ConfigItem
from enca.framework.geoprocessing import RasterType

_DROUGHT_VULNERABILITY_INDICATOR = 'drought_vulnerability_indicator'

logger = logging.getLogger(__name__)

class DroughtVuln(enca.ENCARun):

    run_type = enca.RunType.PREPROCESS
    component = 'WATER_DROUGHT_VULNERABILITY'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                # use ConfigItem, not ConfigRaster, becase we don't want automatic warping
                _DROUGHT_VULNERABILITY_INDICATOR: ConfigItem()}})

        # TODO dates should match the dates of bands in the drought vulnerability indicator input raster?
        d1 = datetime.date(1980, 1, 1)
        d2 = datetime.date(2022, 1, 1)
        self.dates = [d1 + datetime.timedelta(days=x) for x in range((d2-d1).days + 1)]

    def _start(self):
        drought_lta = os.path.join(self.temp_dir(), 'drought_code_long-term_average.tif')
        # Calculate 40-year average of drought vulnearability indicator:
        with rasterio.open(self.config[self.component][_DROUGHT_VULNERABILITY_INDICATOR]) as src, \
             rasterio.open(drought_lta, 'w',
                           **dict(src.profile,
                                  count=1,
                                  dtype=np.float64,
                                  interleave='band')) as dst:
            sum = 0
            count = 0
            index_start = src.count - int(40 * 365.24)
            if index_start < 0:
                logger.warning('Drought vulnerability file does not contain 40 years of data.  '
                               'Using all %s available bands for the long-term average', src.count)
                index_start = 0
            for i in range(index_start, src.count):
                data = src.read(i + 1)
                valid = data != src.nodata
                count += valid
                sum += np.where(valid, data, 0)

            lta = np.divide(sum, count, where=count != 0, out=sum)
            dst.update_tags(creator='sys4enca', info='long-term average of the drought code')
            dst.write(lta, 1)

        # Next, calculate annual average for all years:
        with rasterio.open(self.config[self.component][_DROUGHT_VULNERABILITY_INDICATOR]) as src:
            # small test:
            if len(self.dates) == src.profile['count']:
                logger.info('Number of drought vulnerability bands matches number of dates.')
            elif len(self.dates) < src.profile['count']:
                logger.warning('Number of drought vulnerability bands exceeds number of dates.  Expected mismatch.')
            else:
                logger.error('Number of drought vulnerability bands smaller than number of dates.  Unexpected mismatch.')

            for year in self.years:
                sum = 0
                count = 0

                i_start = self.get_date_index(datetime.date(year, 1, 1))
                i_end = self.get_date_index(datetime.date(year, 12, 31))
                for idx in range(i_start, 1 + i_end):
                    data = src.read(1 + idx)
                    valid = data != src.nodata
                    count += valid
                    sum += np.where(valid, data, 0)

                annual = np.divide(sum, count, where=count != 0, out=sum)
                path_out = os.path.join(self.temp_dir(), f'drought_code_annual-average_{year}.tif')
                with rasterio.open(path_out, 'w',
                                   **dict(src.profile,
                                          count=1,
                                          dtype=np.float64,
                                          interleave='band')) as dst:
                    dst.update_tags(creator='sys4enca', info=f'annual average of the drought code for year {year}')
                    dst.write(annual, 1)

                # Now calculate annual drought vulnerability as ratio between annual and LTA
                # ratio < 1 means lower vulnerability than LTA; > 1 means ihgher vulnerability / decrease in health
                dvi = annual / lta
                path_out = os.path.join(self.temp_dir(), f'drought_vulnerability_ratio_{year}.tif')
                with rasterio.open(path_out, 'w',
                                   **dict(src.profile,
                                          count=1, dtype=np.float64, interleave='band')) as dst:
                    dst.update_tags(creator='sys4enca',
                                    info='drought vulnerability as ratio between annual average and long-term average, '
                                    f'i.e % of normal drought level, for year {year}')
                    dst.write(dvi, 1)

                # Now we have to calculate a meaningful health indicator for the table work from the vulnerability idea
                # is that the vulnerability against drought is mainly the change against the normal state. meaning: the
                # vegetation is adapted to the normal state of the water availability (plants are adapted to their area)
                # which is represented by our 40year average in drought code the higher the % above normal state the
                # higher is the vulnerability and the lower the health status

                # now we have to take into account the annual % of normal  which we have to categorize
                aAnnualDVHI = np.full_like(dvi, 1, dtype=np.float32)
                # >1.05 - 1.5
                aAnnualDVHI[(dvi > 1.05) & (dvi < 1.25)] -= 0.05
                aAnnualDVHI[(dvi >= 1.25) & (dvi < 1.5)] -= 0.1
                # >1.5 - 2
                aAnnualDVHI[(dvi >= 1.5) & (dvi < 1.75)] -= 0.15
                aAnnualDVHI[(dvi >= 1.75) & (dvi < 2)] -= 0.2
                # >2 - 2.5
                aAnnualDVHI[(dvi >= 2) & (dvi < 2.25)] -= 0.25
                aAnnualDVHI[(dvi >= 2.25) & (dvi < 2.5)] -= 0.3
                # >2.5 - 3
                aAnnualDVHI[(dvi >= 2.5) & (dvi < 2.75)] -= 0.35
                aAnnualDVHI[(dvi >= 2.75) & (dvi < 3)] -= 0.4
                # >3
                aAnnualDVHI[(dvi >= 3) & (~np.isnan(dvi))] -= 0.5

                path_out = os.path.join(self.temp_dir(), f'drought-vulnerability-health-index_{year}.tif')
                with rasterio.open(path_out, 'w',
                                   **dict(src.profile,
                                          count=1, dtype=np.float32, interleave='band')) as dst:
                    dst.update_tags(creator='sys4enca', info=f'drought vulnerability health indicator for year {year}, '
                                    'generated out of the drought code ratio.')
                    dst.write(aAnnualDVHI, 1)

                # warp to AOI
                # TODO check if we still need original multi-stage warp
                path_out_aoi = os.path.join(self.maps, f'NCA_WATER_drought-vulnerability_factor_{year}.tif')
                self.accord.AutomaticBring2AOI(path_out, RasterType.ABSOLUTE_POINT,
                                               secure_run=True, path_out=path_out_aoi)

    def get_date_index(self, date):
        """Get the index of a date in the list self.dates."""
        i = bisect.bisect_right(self.dates, date)
        if i and (self.dates[0] <= date <= self.dates[-1]):
            return i-1
        else:
            raise ValueError(f'Date {date} outside of range [{self.dates[0]}, {self.dates[-1]}].')
