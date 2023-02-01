import logging
import re

import enca
import pandas as pd

from enca.config_check import ConfigRasterDir
from enca.geoprocessing import RasterType, statistics_byArea, statistics_area, SHAPE_ID
from .forest import CarbonForest
from .soil import CarbonSoil

FOREST_AGB = 'ForestAGB'
FOREST_BGB = 'ForestBGB'
FOREST_LITTER = 'ForestLitter'

AREA_RAST = 'Area_rast'

SOIL = 'Soil'

logger = logging.getLogger(__name__)


class Carbon(enca.ENCARun):

    run_type = enca.ENCA
    component = 'CARBON'


    def __init__(self, config):
        super().__init__(config)

        self.config_template.update({
          self.component: {
              CarbonForest.component: ConfigRasterDir(raster_type=RasterType.ABSOLUTE_VOLUME),
              CarbonSoil.component: ConfigRasterDir(raster_type=RasterType.ABSOLUTE_VOLUME)
          }

        })

    def _start(self):
        print('Hello from ENCA Carbon')
        for year in self.years:
            self.selu_statistics(year)

    def selu_statistics(self, year):

        # look up required input files with correct column label
        input_files = {CarbonForest.component: [FOREST_AGB, FOREST_BGB, FOREST_LITTER],
                       CarbonSoil.component: [SOIL]}

        labeled_files = {}

        carbon_config = self.config[self.component]
        for rasterdir, labels in input_files.items():
            for label in labels:
                # Following will extract exactly one match, or raise ValueError if 0 or more than one match found:
                file, = [x for x in carbon_config[rasterdir] if re.match(f'.*{label}_tons_{year}.tif', x)]
                labeled_files[label] = file
        logger.debug('Found following files:\n%s', labeled_files)

        result = pd.DataFrame(index=self.statistics_shape.index)
        for label, file in labeled_files.items():
            stats = statistics_byArea(file, self.statistics_raster, self.statistics_shape[SHAPE_ID])
            result[label] = stats['sum']

        result[AREA_RAST] = statistics_area(self.statistics_raster, self.statistics_shape[SHAPE_ID])
        # TODO add polygon area?

        logger.debug('SELU statistics for %s:\n%s', year, result)
