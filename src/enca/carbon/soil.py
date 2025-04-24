import os
import time

import numpy as np
import rasterio

import enca
from enca.framework.config_check import ConfigItem, ConfigRaster
from enca.framework.geoprocessing import RasterType, block_window_generator

MANGROVE_CLASSES = 'mangrove_lc_classes'
URBAN_CLASSES = 'urban_lc_classes'
NONSOIL_CLASSES = 'nonsoil_lc_classes'
SEAL_ADJUST = 'SEAL_ADJUST'
SOC = 'SOC'
SOC_MANGROVES = 'SOC_MANGROVES'

class CarbonSoil(enca.ENCARun):

    component = 'CARBON_SOIL'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                MANGROVE_CLASSES: ConfigItem(),
                URBAN_CLASSES: ConfigItem(),
                NONSOIL_CLASSES: ConfigItem(),
                SEAL_ADJUST: ConfigItem(),
                SOC: ConfigRaster(raster_type=RasterType.RELATIVE),
                SOC_MANGROVES: ConfigRaster(raster_type=RasterType.RELATIVE)
            }})

    def _start(self):
        print('Hello from ENCA Carbon Soil preprocessing.')

        for year in self.years:
            self.calculate_soc(year)


    def calculate_soc(self, year, block_shape=(1024, 1024)):
        soil_config = self.config[self.component]

        with rasterio.open(soil_config[SOC]) as ds_SOC, \
             rasterio.open(soil_config[SOC_MANGROVES]) as ds_SOCm, \
             rasterio.open(self.config[enca.LAND_COVER][year]) as ds_LC, \
             rasterio.open(os.path.join(self.maps, f'NCA_{self.component}_tons_{year}.tif'), 'w',
                           **dict(ds_SOC.profile,
                                  compress='lzw',
                                  bigtiff='yes',
                                  tiled=True,
                                  nodata=-9999,
                                  dtype=np.float32,
                                  blockysize=block_shape[0],
                                  blockxsize=block_shape[1])) as ds_out:
            ds_out.update_tags(file_creation=time.asctime(),
                               Info=f'Soil carbon in tons per pixel for year {year}.',
                               NODATA_value=ds_out.nodata,
                               VALUES=f'valid: > 0, nodata: {ds_out.nodata}',
                               PIXEL_UNIT='tons carbon')
            for _, window in block_window_generator(block_shape, ds_SOC.profile['height'], ds_SOC.profile['width']):
                lc = ds_LC.read(1, window=window)
                soc = ds_SOC.read(1, window=window)
                socm = ds_SOCm.read(1, window=window)

                soc[np.isnan(soc)] = 0
                socm[np.isnan(socm)] = 0
                soc[soc == ds_SOC.nodata] = 0
                socm[socm == ds_SOCm.nodata] = 0

                result = soc.astype(np.float32)

                mangrove = np.isin(lc, soil_config[MANGROVE_CLASSES])
                result[mangrove] = (soc[mangrove] + socm[mangrove]) / 2.

                urban = np.isin(lc, soil_config[URBAN_CLASSES])
                result[urban] = soc[urban] * soil_config[SEAL_ADJUST]

                nonsoil = np.isin(lc, soil_config[NONSOIL_CLASSES])
                result[nonsoil] = 0

                # Remove negative values
                result.clip(min=0, out=result)

                ds_out.write(result.astype(rasterio.float32), 1, window=window)
