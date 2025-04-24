import logging
import os
import shutil

import pandas as pd

import enca
from enca.framework.config_check import ConfigItem, ConfigRaster, check_csv
from enca.framework.geoprocessing import SHAPE_ID, RasterType, sum_rasters

logger = logging.getLogger(__name__)

CATTLE = 'cattle'
CHICKEN = 'chicken'
SHEEP = 'sheep'
GOAT = 'goats'
PIG = 'pigs'

LIVESTOCK_DIST = 'livestock_distribution'
LIVESTOCK_CARBON = 'livestock_carbon'
WEIGHTS = 'weights'

DWF = 'DWF'


# Conversion factor to unit tonne / ha, asssuming
#
# - livestock distribution unit: head / km2
# - statistic unit: total head
# - weight unit: kg
_weight_2_carbon = 0.15
_kg_2_tons = 0.001
_tKm2_2_tHa = 0.01
_conversion_factor = _weight_2_carbon * _kg_2_tons * _tKm2_2_tHa

_livestock_types = [CATTLE, CHICKEN, SHEEP, GOAT, PIG]

_livestock_long_names = {
    CATTLE: {
        "en": "Cattle",
        "fr": "Bovins",
    },
    CHICKEN: {
        "en": "Chicken",
        "fr": "Poulet",
    },
    SHEEP: {
        "en": "Sheep",
        "fr": "Mouton",
    },
    GOAT: {
        "en": "Goat",
        "fr": "Chèvre",
    },
    PIG: {
        "en": "Pig",
        "fr": "Porc",
    },
}

class CarbonLivestock(enca.ENCARunAdminAOI):

    component = 'CARBON_LIVESTOCK'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                LIVESTOCK_DIST: {
                    stock_type: ConfigRaster(raster_type=RasterType.RELATIVE) for stock_type in _livestock_types},
                LIVESTOCK_CARBON: {
                    stock_type: ConfigItem(check_csv) for stock_type in _livestock_types},
                WEIGHTS: {
                    stock_type: ConfigItem() for stock_type in _livestock_types}
            }})

        self.livestock_carbon_rasters = {stock_type:
                                         os.path.join(self.temp_dir(),
                                                      f'NCA_{self.component}_{stock_type}_tonsha_{{year}}.tif')
                                         for stock_type in _livestock_types}

    def _start(self):
        print('Hello from ENCA Carbon Livestock preprocessing.')

        for year in self.years:
            self.livestock_carbon(year)
            # Total livestock carbon:
            sum_rasters(os.path.join(self.maps, f'NCA_{self.component}_tons_{year}.tif'),
                        *[raster.format(year=year) for raster in self.livestock_carbon_rasters.values()])
            # Move 'cattle' carbon raster to maps dir:   # TODO rename 'cattle' -> 'Cow'?
            shutil.move(self.livestock_carbon_rasters[CATTLE].format(year=year), self.maps)

    def livestock_carbon(self, year):
        """Calculate livestock carbon per pixel using livestock distribution raster and FAO stats."""
        config = self.config[self.component]
        for stock_type in _livestock_types:
            raster_dist = config[LIVESTOCK_DIST][stock_type]
            out_file = self.livestock_carbon_rasters[stock_type].format(year=year)
            df_stats = pd.read_csv(config[LIVESTOCK_CARBON][stock_type], sep=';', index_col=enca.ADMIN_ID)

            data = df_stats[f'heads_{year}'] * config[WEIGHTS][stock_type] * _conversion_factor

            self.accord.spatial_disaggregation_byArea(raster_dist, data,
                                                      self.admin_raster, self.admin_shape[SHAPE_ID],
                                                      out_file)


def get_livestock_long_name(livestock_key, locale="en"):
    """
    Fetches the component name in the specified language.

    :param livestock_key: The key identifier for the component.
    :param lang: The language code ('en' for English, 'fr' for French).
    :return: The name of the component in the specified language.
    """
    # The locale is usually in the format 'en_US', 'fr_FR', etc.
    # If you only need the first two characters (e.g., 'en', 'fr')
    language_code = locale[0:2]
    return _livestock_long_names.get(livestock_key, {}).get(language_code, "Unknown livestock type")
