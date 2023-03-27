import bisect
import datetime
import os

import numpy as np
import rasterio

import enca
from enca.framework.config_check import ConfigItem
from enca.framework.geoprocessing import RasterType

DAILY_SEVERITY_RATING = 'dsr'

class CarbonFireVulnerability(enca.ENCARun):

    run_type = enca.PREPROCESS
    component = 'CARBON_FIRE_VULNERABILITY'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                DAILY_SEVERITY_RATING: ConfigItem()
                }})

        # TODO following dates should correspond to dates of bands in dsr raster?
        d1 = datetime.date(1980, 1, 1)
        d2 = datetime.date(2022, 1, 1)
        self.dates = [d1 + datetime.timedelta(days=x) for x in range((d2 - d1).days + 1)]

    def get_date_index(self, date):
        """Get the index of a date in the list self.dates."""
        i = bisect.bisect_right(self.dates, date)
        if i and (self.dates[0] <= date <= self.dates[-1]):
            return i-1
        else:
            raise ValueError(f'Date {date} outside of range [{self.dates[0]}, {self.dates[-1]}].')

    def _start(self):
        # Calculate 40 year average severity rating:
        path_lta = os.path.join(self.temp_dir(), 'fire-severity_long-term-average.tif')
        with rasterio.open(self.config[self.component][DAILY_SEVERITY_RATING]) as ds_dsr:
            total = np.zeros((ds_dsr.height, ds_dsr.width), dtype=np.float64)
            count = np.zeros((ds_dsr.height, ds_dsr.width), dtype=int)
            for band in range(1 + ds_dsr.count - int(40 * 365.24), 1 + ds_dsr.count):
                data = ds_dsr.read(band)
                valid = data != ds_dsr.nodata
                total[valid] += data[valid]
                count[valid] += 1
                del valid, data

            lta = np.full_like(total, np.nan)
            valid = count != 0
            lta[valid] = total[valid] / count[valid]
            del valid, total, count

            with rasterio.open(path_lta, 'w', **dict(ds_dsr.profile, crs='EPSG:4326', count=1)) as ds_out:
                ds_out.write(lta, 1)

            # Calculate annual severity rating, and ratio with:
            for year in self.years:
                i_start = self.get_date_index(datetime.date(year, 1, 1))
                i_end = self.get_date_index(datetime.date(year, 12, 31))
                total = np.zeros((ds_dsr.height, ds_dsr.width), dtype=np.float64)
                count = np.zeros((ds_dsr.height, ds_dsr.width), dtype=int)
                for idx in range(i_start, 1 + i_end):
                    data = ds_dsr.read(1 + idx)
                    valid = data != ds_dsr.nodata

                    total[valid] += data[valid]
                    count[valid] += 1

                    del valid, data

                annual = np.full_like(total, np.nan)
                valid = count != 0
                annual[valid] = total[valid] / count[valid]
                del valid, total, count

                with rasterio.open(os.path.join(self.temp_dir(), f'fire-severity_annual-average_{year}.tif'), 'w',
                                   **dict(ds_dsr.profile, crs='EPSG:4326', count=1)) as ds_annual:
                    ds_annual.write(annual, 1)

                # ratio between annual and long term average fire vulnerability:
                ratio = annual / lta
                with rasterio.open(os.path.join(self.temp_dir(), f'fire_vulnerability_ratio_{year}.tif'), 'w',
                                   **dict(ds_dsr.profile, crs='EPSG:4326')) as ds_ratio:
                    ds_ratio.write(ratio, 1)

                # now we have to calculate a meaningful health indicator for the table work from the vulnerability.
                #
                # The idea is that the vulnerability against drought is mainly the change against the normal
                # state. meaning: the vegetation is adapted to the normal state of the water availablity (plants are
                # adapted to their area) which is represented by our 40year average in drought code.  The higher the %
                # above normal state, the higher is the vulnerabilty and the lower the health status.
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

                path_vuln_4326 = os.path.join(self.temp_dir(), f'fire-vulnerability-health-index_{year}.tif')
                with rasterio.open(path_vuln_4326, 'w',
                                   **dict(ds_dsr.profile,
                                          crs='EPSG:4326', count=1,
                                          dtype=rasterio.float32, interleave='band')) as ds_fvhi:
                    ds_fvhi.write(annual_fvhi, 1)

                # TODO original preprocesing uses 2-step resampling:
                #  1) bilinear to 1km²
                #  2) neighbour to 100x100m²
                # -> still needed?
                path_out = os.path.join(self.maps, f'NCA_{self.component}_CEH4_factor_{year}.tif')
                self.accord.AutomaticBring2AOI(path_vuln_4326, raster_type=RasterType.ABSOLUTE_POINT,
                                               path_out=path_out)
