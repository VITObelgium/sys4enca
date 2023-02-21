"""Water reporting."""

import os

import enca
from enca.framework.config_check import ConfigRaster
from enca.framework.geoprocessing import RasterType

PRECIPITATION = 'precipitation'
EVAPO = 'evapotranspiration'
USE_MUNI = 'MUNIusage'
USE_AGRI = 'AGRIusage'
DROUGHT_VULN = 'drought-vulnerability'
EVAPO_RAINFED = 'ET-rainfed-agriculture'
RIVER_LENGTH = 'river-length'
LT_PRECIPITATION = 'LTA-precipitation'
LT_EVAPO = 'LTA-evapotranspiration'


class Water(enca.ENCARun):
    """Water accounting class."""

    run_type = enca.ENCA
    component = 'WATER'

    def __init__(self, config):
        """Initialize config template and default water run parameters."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                PRECIPITATION: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                EVAPO: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                USE_MUNI: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                USE_AGRI: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                DROUGHT_VULN: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                EVAPO_RAINFED: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                RIVER_LENGTH: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                LT_PRECIPITATION: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                LT_EVAPO: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True)
                }
            })

        self.input_rasters = [PRECIPITATION, EVAPO, USE_MUNI, USE_AGRI, DROUGHT_VULN, EVAPO_RAINFED,
                              RIVER_LENGTH, LT_PRECIPITATION, LT_EVAPO]

    def _start(self):
        water_config = self.config[self.component]
        for year in self.years:
            stats = self.selu_stats({key: water_config[key] for key in self.input_rasters if water_config[key]})
            stats.to_csv(os.path.join(self.statistics, f'SELU_stats_{year}.csv'))
