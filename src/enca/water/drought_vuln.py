from datetime import datetime, timedelta, date
import glob
import logging
import numpy as np
import os
import rasterio

import enca
from enca.framework.config_check import YEARLY, ConfigItem
from enca.framework.geoprocessing import RasterType, block_window_generator

DROUGHT_CODE_LTA = 'drought_code_lta'
DROUGHT_CODE = 'drought_code'

logger = logging.getLogger(__name__)

_block_shape = (1024, 1024)

class DroughtVuln(enca.ENCARun):

    component = 'WATER_DROUGHT_VULNERABILITY'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                # use ConfigItem, not ConfigRaster, becase we don't want automatic warping
                DROUGHT_CODE_LTA: ConfigItem(),
                DROUGHT_CODE: {YEARLY: ConfigItem()}}})

    def _start(self):
        for year in self.years:
            # calculate annual average:
            sum = 0
            count = 0

            nc_files = self.config[self.component][DROUGHT_CODE][year]
            logger.debug('Calculate drought code average for year %s.  Found %s netCDF input files.',
                         year, len(nc_files))
            with rasterio.open(nc_files) as src:
                profile = src.profile
                tags = src.tags()
                if (float(tags["drtcode#GRIB_longitudeOfLastGridPointInDegrees"]) < 180.5):
                    affine = rasterio.transform.from_origin(float(tags["drtcode#GRIB_longitudeOfFirstGridPointInDegrees"]),
                                                            float(tags["drtcode#GRIB_latitudeOfFirstGridPointInDegrees"]),
                                                            float(tags["drtcode#GRIB_iDirectionIncrementInDegrees"]),
                                                            float(tags["drtcode#GRIB_jDirectionIncrementInDegrees"]))
                    data_roll = False
                else:
                    affine = rasterio.transform.from_origin(float(tags["drtcode#GRIB_longitudeOfFirstGridPointInDegrees"])-180,
                                                            float(tags["drtcode#GRIB_latitudeOfFirstGridPointInDegrees"]),
                                                            float(tags["drtcode#GRIB_iDirectionIncrementInDegrees"]),
                                                            float(tags["drtcode#GRIB_jDirectionIncrementInDegrees"]))
                    data_roll = True




                time_units = tags.get('valid_time#units').split(' since ')[0]
                time_start = tags.get('valid_time#units').split(' since ')[1].split('-')
                refdate  =  date(int(time_start[0]), int(time_start[1]),int(time_start[2]))
                times = tags.get('NETCDF_DIM_valid_time_VALUES')[1:-1].split(',')
                if 'sec' in time_units:
                    time_coverage = [refdate + timedelta(seconds=int(secs)) for secs in times]
                else:
                    logger.error(f"The unit of the time dimension was expected to be in seconds however it is in {time_units}")


                for i in range(profile.get('count')):
                    if time_coverage[i].year != int(year):
                        continue
                    data = src.read(i +1)
                    valid = (data != src.nodata) & (~np.isnan(data))
                    count += valid
                    sum += np.where(valid, data, 0)



            annual = np.divide(sum, count, where=count != 0, out=sum)
            path_out = os.path.join(self.temp_dir(), f'drought_code_annual-average_{year}.tif')
            # do the data_roll if needed to bring data in -180 to +180 longitude format
            if data_roll:
                logger.debug("** do a data roll to get 0deg center meridian.. ")
                annual = np.roll(annual, int(annual.shape[1] / 2), axis=1)
            with rasterio.open(path_out, 'w', **dict(profile,
                                                     count=1,
                                                     driver='Gtiff',
                                                     crs='EPSG:4326',
                                                     dtype=np.float32,
                                                     transform=affine)) as dst:
                dst.update_tags(creator='sys4enca', info=f'annual average of the drought code for year {year}')
                dst.write(annual, 1)

            # Now warp drought code to our AOI
            logger.debug('Warp drought code annual average to AOI.')
            path_out_aoi = os.path.join(self.temp_dir(), f'drought_code_annual-average_{year}_ENCA.tif')
            self.accord.AutomaticBring2AOI(path_out, RasterType.ABSOLUTE_POINT, secure_run=True, path_out=path_out_aoi)

            logger.debug('Warp drought code long-term average to AOI.')
            drought_lta_aoi = os.path.join(self.temp_dir(), 'drought_code_LTA_ENCA.tif')
            self.accord.AutomaticBring2AOI(self.config[self.component][DROUGHT_CODE_LTA],
                                           RasterType.ABSOLUTE_POINT, secure_run=True, path_out=drought_lta_aoi)

            # Now calculate annual drought vulnerability as ratio between annual and LTA
            # ratio < 1 means lower vulnerability than LTA; > 1 means ihgher vulnerability / decrease in health
            with rasterio.open(path_out_aoi) as ds_annual, \
                 rasterio.open(drought_lta_aoi) as ds_lta, \
                 rasterio.open(os.path.join(self.temp_dir(), f'drought_vulnerability_ratio_{year}.tif'), 'w',
                               **ds_annual.profile) as ds_ratio, \
                 rasterio.open(os.path.join(self.maps, f'drought-vulnerability-health-index_{year}.tif'), 'w',
                               **ds_annual.profile) as ds_index:
                ds_ratio.update_tags(
                    creator='sys4enca',
                    info='drought vulnerability as ratio between annual average and long-term average, '
                    f'i.e % of normal drought level, for year {year}')
                ds_index.update_tags(
                    creator='sys4enca',
                    info=f'drought vulnerability health indicator for year {year}, '
                    'generated out of the drought code ratio.')

                for _, window in block_window_generator(_block_shape, ds_lta.profile['height'], ds_lta.profile['width']):
                    dvi = ds_annual.read(1, window=window) / ds_lta.read(1, window=window)

                    ds_ratio.write(dvi, window=window, indexes=1)

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

                    ds_index.write(aAnnualDVHI, window=window, indexes=1)
