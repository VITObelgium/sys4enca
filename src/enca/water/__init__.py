"""Water reporting."""

import logging
import os

import geopandas as gpd

import enca
from enca.framework.config_check import ConfigRaster, ConfigShape
from enca.framework.geoprocessing import RasterType

logger = logging.getLogger(__name__)

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
AQUIFER = 'aquifer'
SALINITY = 'salinity'
HYDRO_LAKES = 'HYDROlakes'
GLORIC_ADAPTED = 'GLORIC_adapted'

COAST = 'COAST'
COEFF = 'coeff'
INFLOW = 'inf'
OUTFLOW = 'outf'

LT_OUT_M3 = 'LT_out_m3'
NEXT_DOWN = 'NEXT_DOWN'
LOG_Q_AVG = 'Log_Q_avg'
SRMU = 'SRMU'
HYGEO2 = 'HYGEO2'
AREA_HA = 'area_ha'
LAKE_AREA = 'Lake_area'
TOTAL_LAKE_AREA = 'total_lake_area'
TOTAL_LAKE_RUNOFF = 'total_lake_runoff'
VOL_TOTAL = 'Vol_total'
DIS_AVG = 'Dis_avg'
HYLAK_ID = 'Hylak_id'
OWN_TOTAL = 'own_total'
HYBAS_LAKE_AREA = 'hybas_lake_area'
HYBAS_LAKE_VOL = 'hybas_lake_vol'

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
                LT_OUTFLOW: ConfigShape(),
                AQUIFER: ConfigShape(),
                SALINITY: ConfigShape(),
                HYDRO_LAKES: ConfigShape(),
                GLORIC_ADAPTED: ConfigShape(),
                }
            })

        self.input_rasters = [PRECIPITATION, EVAPO, USE_MUNI, USE_AGRI, DROUGHT_VULN, EVAPO_RAINFED,
                              RIVER_LENGTH, LT_PRECIPITATION, LT_EVAPO]

    def _start(self):
        water_config = self.config[self.component]

        water_stats = self.additional_water_stats()
        water_stats.to_csv(os.path.join(self.statistics, 'SELU_additional-water-stats.csv'))

        area_stats = self.area_stats()
        for year in self.years:
            stats = self.selu_stats({key: water_config[key] for key in self.input_rasters if water_config[key]})
            stats[enca.AREA_RAST] = area_stats.unstack(self.reporting_shape.index.name, fill_value=0).sum(axis=1)
            stats.to_csv(os.path.join(self.statistics, f'SELU_stats_{year}.csv'))

            flow_results = self.selu_inflow_outflow(stats, year)
            flow_results.to_csv(os.path.join(self.statistics, f'SELU_flow-results_{year}.csv'))

    def additional_water_stats(self):
        """Calculate additional water statistics per SELU.

        The following statistics are generated:
        - lake/reservoir volume in m3 of Hybas --> W1_11
        - lake/reservoir area in ha of Hybas --> W1_12 == i1
        - total lake/reservoir area in ha (each touching Hybas get the same overall lake/reservoir area) --> i12
        - annual average discharge of lake /reservoir in m3 ((each touching Hybas get the same overall lake/reservoir
          runoff) --> i2
        - SRMU --> W1_21 == i6
        - overall area of major aquifiers per SELU --> W1_41
        - overall area of local aquifiers per SELU  --> W1_42
        - overall area of salinity areas per SELU  --> i10
        """
        water_config = self.config[self.component]

        # Use reset_index to get a GeoDataFrame with HYBAS_ID column we can use for overlays
        hybas_geom = self.statistics_shape[['geometry']].reset_index()

        logger.debug('Calculate SRMU.')
        gdf_SRMU = gpd.read_file(water_config[GLORIC_ADAPTED], include_fields=[LOG_Q_AVG]).overlay(
            hybas_geom, how='intersection')

        length = gdf_SRMU.geometry.length / 1000.
        q_avg = 10 ** gdf_SRMU[LOG_Q_AVG]
        gdf_SRMU[SRMU] = length * q_avg
        result = gdf_SRMU.groupby(enca.HYBAS_ID)[[SRMU]].sum().reindex(self.statistics_shape.index)
        del gdf_SRMU

        shps = {key: os.path.join(self.temp_dir(), f'{key}.shp') for key in (AQUIFER, SALINITY, HYDRO_LAKES)}
        for key, outfile in shps.items():
            logger.debug('Reproject shapefile for %s', key)
            self.accord.vector_2_AOI(water_config[key], outfile)

        logger.debug('Calculate aquifer areas.')
        gdf_aqua = gpd.read_file(shps[AQUIFER], include_fields=[HYGEO2]).overlay(hybas_geom, how='intersection')
        gdf_aqua[AREA_HA] = gdf_aqua.area / 10000.

        major = gdf_aqua[HYGEO2].isin([11, 12, 13, 14, 15])
        local = gdf_aqua[HYGEO2].isin([33, 34])
        result = result.join(gdf_aqua[major].groupby(enca.HYBAS_ID)[AREA_HA].sum().rename('Major-aquifer'))
        result = result.join(gdf_aqua[local].groupby(enca.HYBAS_ID)[AREA_HA].sum().rename('Local-aquifer'))
        del gdf_aqua

        logger.debug('Extract area of salinity.')
        gdf_sal = gpd.read_file(shps[SALINITY]).overlay(hybas_geom, how='intersection')
        gdf_sal[AREA_HA] = gdf_sal.area / 10000.
        result = result.join(gdf_sal.groupby(enca.HYBAS_ID)[AREA_HA].sum().rename(SALINITY))
        del gdf_sal

        logger.debug('Extract lake & reservoir statistics.')
        gdf_lake = gpd.read_file(shps[HYDRO_LAKES], include_fields=[LAKE_AREA, VOL_TOTAL, DIS_AVG, HYLAK_ID])
        gdf_lake[TOTAL_LAKE_AREA] = 100 * gdf_lake[LAKE_AREA]  # Convert km² to ha
        gdf_lake[DIS_AVG].clip(lower=0., inplace=True)  # Set -9999 nodata values to 0
        # Convert yearly discharge: multiply by number of seconds in astronomical year.
        gdf_lake[TOTAL_LAKE_RUNOFF] = 31556700. * gdf_lake[DIS_AVG]

        # Set up help for later calculation of hybas fraction (changes in EPSG can alter area estimation if not an equal
        # area projection).
        gdf_lake[OWN_TOTAL] = gdf_lake.area

        gdf_lake = gdf_lake.overlay(hybas_geom, how='intersection')
        fraction = gdf_lake.area / gdf_lake[OWN_TOTAL]
        gdf_lake[HYBAS_LAKE_AREA] = fraction * gdf_lake[TOTAL_LAKE_AREA]
        # lake volume in m³ in the hybas --> Vol_total is in million m³
        gdf_lake[HYBAS_LAKE_VOL] = fraction * gdf_lake[VOL_TOTAL] * 1000000.
        result = result.join(gdf_lake.groupby(enca.HYBAS_ID)[[TOTAL_LAKE_AREA,
                                                              TOTAL_LAKE_RUNOFF,
                                                              HYBAS_LAKE_AREA,
                                                              HYBAS_LAKE_VOL]].sum())
        del gdf_lake

        return result.fillna(0)

    def selu_inflow_outflow(self, selu_stats, year):
        """Calculate annual in- and outflow per SELU.

        The calculation is based on long-term river outflow per SELU retrieved from the GLORiC dataset, but the ratio
        between the long-term and annual water availability (precipitation - evapotranspiration) is used to shift the
        long-term outflow to an annual river outflow.  Hypothesis: then percentage shift between long-term water
        availability and annual water availability corresponds to the long-term river outflow.

        """  # TODO docstring should say "... corresponds to the annual river outflow."?
        logger.debug('Calculate SELU in- and outflow.')
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
