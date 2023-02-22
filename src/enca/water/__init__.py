"""Water reporting."""

import os

import geopandas as gpd

import enca
from enca.framework.config_check import ConfigRaster, ConfigShape
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
LT_OUTFLOW = 'LTA-river-outflow'

COAST = 'COAST'
COEFF = 'coeff'
INFLOW = 'inf'
OUTFLOW = 'outf'

LT_OUT_M3 = 'LT_out_m3'
NEXT_DOWN = 'NEXT_DOWN'

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
                LT_EVAPO: ConfigRaster(raster_type=RasterType.ABSOLUTE_VOLUME, optional=True),
                LT_OUTFLOW: ConfigShape()
                }
            })

        self.input_rasters = [PRECIPITATION, EVAPO, USE_MUNI, USE_AGRI, DROUGHT_VULN, EVAPO_RAINFED,
                              RIVER_LENGTH, LT_PRECIPITATION, LT_EVAPO]

    def _start(self):
        water_config = self.config[self.component]

        area_stats = self.area_stats()
        for year in self.years:
            stats = self.selu_stats({key: water_config[key] for key in self.input_rasters if water_config[key]})
            stats[enca.AREA_RAST] = area_stats.unstack(self.reporting_shape.index.name, fill_value=0).sum(axis=1)
            stats.to_csv(os.path.join(self.statistics, f'SELU_stats_{year}.csv'))

            flow_results = self.selu_inflow_outflow(stats, year)
            flow_results.to_csv(os.path.join(self.statistics, f'SELU_flow-results_{year}.csv'))

    def selu_inflow_outflow(self, selu_stats, year):
        """Calculate annual in- and outflow per SELU.

        The calculation is based on long-term river outflow per SELU retrieved from the GLORiC dataset, but the ratio
        between the long-term and annual water availability (precipitation - evapotranspiration) is used to shift the
        long-term outflow to an annual river outflow.  Hypothesis: then percentage shift between long-term water
        availability and annual water availability corresponds to the long-term river outflow.

        """  # TODO docstring should say "... corresponds to the annual river outflow."?
        df_flow = gpd.read_file(self.config[self.component][LT_OUTFLOW], ignore_geometry=True).set_index(enca.HYBAS_ID)
        df = selu_stats[[PRECIPITATION, EVAPO, LT_PRECIPITATION, LT_EVAPO]].join(df_flow)
        coeff = (df[PRECIPITATION] - df[EVAPO]) / (df[LT_PRECIPITATION] - df[LT_EVAPO])
        coeff[coeff <= 0] = 1  # some rules - neg. values result in usage of LT_out_m3
        coeff[coeff > 2] = 1  # if coeff > 2, this is strange and we better set to LT_out_m3 (mostly small areas)
        df[OUTFLOW] = df[LT_OUT_M3] * coeff

        # calculate the sum of the outflow into the NEXT_DOWN SELU and call it inflow
        inflow = df.groupby(NEXT_DOWN)[OUTFLOW].sum().rename(INFLOW)
        inflow.index.rename(enca.HYBAS_ID)

        return df[[COAST, OUTFLOW]].join(inflow).fillna(0)
