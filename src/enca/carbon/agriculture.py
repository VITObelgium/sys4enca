import glob
import logging
import os
from contextlib import ExitStack

import numpy as np
import pandas as pd
import rasterio
import rasterio.warp as warp

import enca
from enca.framework.errors import Error
from enca.framework.config_check import ConfigItem
from enca.framework.geoprocessing import RasterType, SHAPE_ID

logger = logging.getLogger(__name__)

CAFE = 'cafe'
CEREALS = 'cereals'
FIBER = 'fiber'
FRUIT = 'fruit'
OILCROP = 'oilcrop'
PULSES = 'pulses'
ROOTS = 'roots'
SUGAR = 'sugar'
VEGETABLES = 'vegetables'

FOOD = 'food'
NONFOOD = 'non-food'

AGRICULTURE_DISTRIBUTION = 'agriculture_distribution'
AGRICULTURE_STATS = 'agriculture_stats'

EARTH_RADIUS = 6356752.3

_agri_types = [CAFE, CEREALS, FIBER, FRUIT, OILCROP, PULSES, ROOTS, SUGAR, VEGETABLES]

_carbon = {
    CAFE: 0.4,
    CEREALS: 0.4,
    FIBER: 0.4,
    FRUIT: 0.2,
    OILCROP: 0.4,
    PULSES: 0.4,
    ROOTS: 0.3,
    SUGAR: 0.3,
    VEGETABLES: 0.1,
}

class CarbonAgriculture(enca.ENCARunAdminAOI):

    component = 'CARBON_AGRICULTURE'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                # SPAM agriculture distribution files are not in projected coordinate system, so we can't use our
                # standard raster check functions.
                AGRICULTURE_DISTRIBUTION: ConfigItem(),  # Directory of SPAM agriculture production rasters
                AGRICULTURE_STATS: ConfigItem(),  # Directory of FAO agriculture stats per agri_type
            }})

        self.spam_files = None
        self.spam_files_aoi = {agri: os.path.join(self.temp_dir(), f'SPAM_{agri}_ENCA.tif')
                               for agri in _agri_types}

    def _start(self):
        print("Hello from carbon agriculture preprocessing.")

        self.preprocess_SPAM()
        for year in self.years:
            self.agriculture_carbon(year)

    def preprocess_SPAM(self):
        """Calculate total agriculture distribution per agri type in tonne/ha."""
        spam_files = glob.glob(os.path.join(self.config[self.component][AGRICULTURE_DISTRIBUTION], '*.tif'))

        # Get a window on the SPAM dataset that matches our reporting AOI bounds.
        # We use the first SPAM raster, assuming all have identical projection and transform
        with rasterio.open(self.reporting_raster) as src_aoi, rasterio.open(spam_files[0]) as src_spam:
            aoi_bounds = warp.transform_bounds(src_aoi.crs, src_spam.crs, *src_aoi.bounds)
            aoi_window = src_spam.window(*aoi_bounds).round_offsets().round_lengths(op='ceil')

            # We need to transform the SPAM data (tonnes per pixel with pixels in lat/lon coordinates) to tonnes per
            # hectare in our reporting CRS.  In order to do that, we need to calculate the pixel area of every pixel in
            # our aoi_window:
            # 1. get pixel ul coordinates:
            # TODO simplify, as we only really need the latitudes and the resolution to calculate pixel surface area for
            # an equally spaced lon/lat grid.
            lons, lats = rasterio.transform.xy(src_spam.transform, *np.meshgrid(
                np.arange(aoi_window.row_off, aoi_window.row_off + aoi_window.height),
                np.arange(aoi_window.col_off, aoi_window.col_off + aoi_window.width), indexing='ij'), offset='ul')
            # 2. get pixel surface area in hectare
            areas_ha = latlon_pixel_area(np.array(lats), *src_spam.res) / 10000.

        # 3. Sum agriculture distribution [tonne / pixel] per agri type in aoi_window, and warp to reference AOI.
        for agri in _agri_types:
            # Get list of SPAM files for this type:
            crop_names = lut_crops[lut_crops['group'] == agri].name.to_list()
            agri_spam_files = [f for f in spam_files
                               if os.path.basename(f).split('_')[3].lower() in crop_names]
            out_file = os.path.join(self.temp_dir(), f'SPAM_{agri}.tif')
            logger.debug('SPAM files for agri type %s:\n%s', agri, [os.path.basename(f) for f in agri_spam_files])
            with ExitStack() as stack:
                agri_ds = [stack.enter_context(rasterio.open(f)) for f in agri_spam_files]
                total = sum(ds.read(1, window=aoi_window, masked=True) for ds in agri_ds) / areas_ha
                ds0 = agri_ds[0]
                transform = ds0.window_transform(aoi_window)
                with rasterio.open(out_file, 'w',
                                   **dict(ds0.profile, transform=transform,
                                          width=total.shape[1], height=total.shape[0])) as ds_out:
                    ds_out.write(total.filled(ds_out.nodata), 1)
            self.accord.AutomaticBring2AOI(out_file, raster_type=RasterType.RELATIVE,
                                           path_out=self.spam_files_aoi[agri])

    def agriculture_carbon(self, year):
        """Adjust agriculture distribution for this year, and convert to tonnes of carbon."""
        stats_files = glob.glob(os.path.join(self.config[self.component][AGRICULTURE_STATS], '*.csv'))
        for agri in _agri_types:
            logger.debug('Spatial disaggregation of statistics for agriculture type %s.', agri)
            # Look for the single file matching the pattern 'FAOSTATSy_{agri}.csv'
            try:
                csv_file, = (x for x in stats_files if x.endswith(f'_{agri}.csv'))
            except ValueError as e:
                raise Error(f'Failed to find unique statistics file for "{agri}": {e}.')
            df_stats = pd.read_csv(csv_file, index_col=enca.ADMIN_ID, sep=';')
            data = df_stats[f't_{year}'] * _carbon[agri]
            out_file = os.path.join(self.maps, f'NCA_{self.component}_{agri}_tonsha_{year}.tif')
            self.accord.spatial_disaggregation_byArea(self.spam_files_aoi[agri], data,
                                                      self.admin_raster, self.admin_shape[SHAPE_ID],
                                                      out_file)


def latlon_pixel_area(lats, res_lon, res_lat):
    """Calculate surface area of pixels with (upper left) latitudes lats and resolution.

    Uses a spherical earth approximation.
    """
    return 2 * np.pi * (np.sin(np.deg2rad(lats)) -
                        np.sin(np.deg2rad(lats - res_lat))) * (res_lon / 360.) * EARTH_RADIUS**2


lut_crops = pd.DataFrame(
    columns=['full_name', 'name', 'food/non-food', 'group'],
    data=[['wheat', 'whea', FOOD, CEREALS],
          ['rice', 'rice', FOOD, CEREALS],
          ['maize', 'maiz', FOOD, CEREALS],
          ['barley', 'barl', FOOD, CEREALS],
          ['pearl millet', 'pmil', FOOD, CEREALS],
          ['small millet', 'smil', FOOD, CEREALS],
          ['sorghum', 'sorg', FOOD, CEREALS],
          ['other cereals', 'ocer', FOOD, CEREALS],
          ['potato', 'pota', FOOD, ROOTS],
          ['sweet potato', 'swpo', FOOD, ROOTS],
          ['yams', 'yams', FOOD, ROOTS],
          ['cassava', 'cass', FOOD, ROOTS],
          ['other roots', 'orts', FOOD, ROOTS],
          ['bean', 'bean', FOOD, PULSES],
          ['chickpea', 'chic', FOOD, PULSES],
          ['cowpea', 'cowp', FOOD, PULSES],
          ['pigeonpea', 'pige', FOOD, 'not_used'],
          ['lentil', 'lent', FOOD, 'not_used'],
          ['other pulses', 'opul', FOOD, PULSES],
          ['soybean', 'soyb', FOOD, OILCROP],
          ['groundnut', 'grou', FOOD, OILCROP],
          ['coconut', 'cnut', FOOD, OILCROP],
          ['oilpalm', 'oilp', NONFOOD, OILCROP],
          ['sunflower', 'sunf', NONFOOD, OILCROP],
          ['rapeseed', 'rape', NONFOOD, OILCROP],
          ['sesameseed', 'sesa', NONFOOD, OILCROP],
          ['other oil crops', 'ooil', NONFOOD, OILCROP],
          ['sugarcane', 'sugc', NONFOOD, SUGAR],
          ['sugarbeet', 'sugb', NONFOOD, 'not_used'],
          ['cotton', 'cott', NONFOOD, FIBER],
          ['other fibre crops', 'ofib', NONFOOD, FIBER],
          ['arabica coffee', 'acof', NONFOOD, 'not_used'],
          ['robusta coffee', 'rcof', NONFOOD, CAFE],
          ['cocoa', 'coco', NONFOOD, CAFE],
          ['tea', 'teas', NONFOOD, CAFE],
          ['tobacco', 'toba', NONFOOD, CAFE],
          ['banana', 'bana', FOOD, FRUIT],
          ['plantain', 'plnt', FOOD, FRUIT],
          ['tropical fruit', 'trof', FOOD, FRUIT],
          ['temperate fruit', 'temf', FOOD, FRUIT],
          ['vegetables', 'vege', FOOD, VEGETABLES],
          ['rest of crops', 'rest', NONFOOD, 'not_used']])
