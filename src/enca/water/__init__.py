"""Water reporting."""

import logging
import os

import geopandas as gpd
import numpy as np

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
MAJOR_AQUIFER = 'Major-aquifer'
LOCAL_AQUIFER = 'Local-aquifer'


input_codes = dict(
    CoastID=COAST,
    W1_11=HYBAS_LAKE_VOL,
    W1_12=HYBAS_LAKE_AREA,
    W1_21=SRMU,
    W1_22=RIVER_LENGTH,
    W1_41=MAJOR_AQUIFER,
    W1_42=LOCAL_AQUIFER,
    W2_1=PRECIPITATION,
    W2_31=INFLOW,
    W2_51a=0,
    W3_1=EVAPO,
    W3_3=OUTFLOW,
    W3_43=0,
    W3_44=0,
    W3_4a=USE_AGRI,
    W3_4b=USE_MUNI,
    W3_81a=0,
    W3_82=0,
    W9_21=EVAPO_RAINFED,
    i2=TOTAL_LAKE_RUNOFF,
    i3=0.8,
    i5=0.8,
    i9=1.0,
    i10=SALINITY,
    i11=0.8,
    i12=TOTAL_LAKE_AREA,
    W13_23=DROUGHT_VULN)


parameters = dict(
    W2_41=0.5,
    W2_51a=0.8,
    W2_51b=0.8,
    W2_61=1.3,
    W3_41a=0.8,
    W3_41b=0.8,
    W3_41c=1.5,
    W3_42=0.2,
    W3_46=0.2,
    W3_6=0.7,
    W3_7=0.7,
    W3_81=0.8,
    W8_1=10.,
    W8_5=0.5)


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

        self.parameters = parameters.copy()

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

            indices = self.indices(water_stats.join(stats).join(flow_results))
            indices.to_csv(os.path.join(self.statistics, f'WATER_indices_{year}.csv'))

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
        result = result.join(gdf_aqua[major].groupby(enca.HYBAS_ID)[AREA_HA].sum().rename(MAJOR_AQUIFER))
        result = result.join(gdf_aqua[local].groupby(enca.HYBAS_ID)[AREA_HA].sum().rename(LOCAL_AQUIFER))
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

    def indices(self, selu_stats):
        logger.debug('*** assign data to input columns')
        df = selu_stats.copy()

        parameters = self.parameters
        for code, value in input_codes.items():
            if isinstance(value, tuple):
                value, default = value  # input_codes may contain (value, default) pair, or plain value
            else:
                default = 0

            if (isinstance(value, str)):  # Value is a column name -> use data from that column
                if value in df.columns:
                    df.rename({value: code}, axis='columns', inplace=True)
                else:  # missing input data -> assign default
                    logger.warning('No input data for %s, assigning default value %s.', value, default)
                    df[code] = float(default)
            else:  # Assign a fixed value.
                df[code] = float(value)

        # calculate correct value for  W13_23 - soil and vegetation vulnerability to natural water stress index
        # zonal statistic gave sum
        df['W13_23'] = np.where((df.W13_23 / df.Area_rast) > 1.0, 1.0, df.W13_23 / df.Area_rast)
        # df['W13_23'] = np.where(df.W13_23 > 1.0, 1.0, df.W13_23)

        # re-assign some input columns
        df['a'] = df.Area_rast

        logger.debug('*** run Ecosystem Water Basic Balance...')
        logger.debug('**** Opening Stock descriptors')
        # indicators
        df['i0'] = df.a
        df['i1'] = df.W1_12
        df['i4'] = df.W1_22
        df['i6'] = df.W1_21

        # cal Glacier, Ice and snow (1000 m3)
        df['W1_3'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # cal Soil and Veg water (net AET)
        # for that we need the Soil and vegetation potential which is half the AET
        df['W8_5'] = df.W3_1 * parameters['W8_5']
        df['W1_5'] = df.W8_5

        logger.debug('**** Total inflows of Water')
        # internal spontaneous water transfer received
        # surface runoff to rivers
        df['W2_21'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # infiltration to soil
        df['W2_22'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # Groundwater drainage into rivers
        df['W2_23'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # other transfers received
        df['W2_24'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # sum up
        df['W2_2'] = df.W2_21 + df.W2_22 + df.W2_23 + df.W2_24
        # a spin off - Groundwater drainage to river minus percolation
        # first we need available effictive rainfall
        df['W4a'] = df.W2_1 - df.W3_1
        # now calculate groundwater drainage to rivers
        df['W2_2a'] = df.W3_3 - df.W2_31 - df.W4a

        # Natural inflows from upstream terrotories
        # cal natural inflow of groundwater from upstream terrotories
        df['W2_32'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # now sum up
        df['W2_3'] = df.W2_31 + df.W2_32

        # Artificial inflows of water from other territories and the sea
        # Articitial inflow of water from other territories
        # -> first abstraction of water for disturbanse
        df['W3_41'] = ((df.W3_4a * parameters['W3_41a']) + (df.W3_4b * parameters['W3_41b'])) * parameters['W3_41c']
        df['W2_41'] = df.W3_41 * parameters['W2_41']
        # abstraction of water from sea
        df['W2_42'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # sum up
        df['W2_4'] = df.W2_41 + df.W2_42

        # Waste water returns/discharge to inland water assets
        # returns/discharge of untreated urban waste water
        df['W2_51'] = (df.W3_4b * parameters['W2_51a']) - (df.W3_4b * parameters['W2_51b'] * df.CoastID)
        # split in treated waste water (W2_51a) and untreated (W2_51b)
        df['W2_51b'] = df.W2_51 - df.W2_51a
        # returns/discharge water_urban runoff
        df['W2_52'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # sum up
        df['W2_5'] = df.W2_51 + df.W2_52

        # other returns of abstracted water to inland water bodies
        # losses of water in transport and storage
        df['W2_61'] = df.W3_41 * parameters['W2_61']
        # irrigation water
        df['W2_62'] = df.W3_4a
        # return of water from hydroelectric production
        df['W2_63'] = df.W3_43
        # return of cooling water
        df['W2_64'] = df.W3_44
        # return of mine water
        df['W2_65'] = df['W3_45'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # return of water from other productions
        df['W2_66'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # other return of water
        df['W2_67'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # sum up
        df['W2_6'] = df.W2_61 + df.W2_62 + df.W2_63 + df.W2_64 + df.W2_65 + df.W2_66 + df.W2_67

        # now sum up the inflow
        df['W2'] = df.W2_1 + df.W2_2 + df.W2_3 + df.W2_4 + df.W2_5 + df.W2_6

        logger.debug('**** Total outflows of Water')
        # the W3_2 (internal sponteaneous water transfer supplied) is the same as the received ones (W2_2)
        # surface runoff to rivers
        df['W3_21'] = df.W2_21
        # infiltration from soil
        df['W3_22'] = df.W2_22
        # Groundwater drainage to rivers
        df['W3_23'] = df.W2_23
        # other transfers supplied
        df['W3_24'] = df.W2_24
        # sum up
        df['W3_2'] = df.W3_21 + df.W3_22 + df.W3_23 + df.W3_24

        # split up of Natural outflows to downstream territories and the sea
        # to sea
        df['W3_32'] = df.W3_3 * df.CoastID
        # of surface waters to downstream territories
        df['W3_31'] = df.W3_3 - df.W3_32
        # natural outflow of groundwater to downstream territories
        df['W3_33'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        # Abstraction from inland water bodies
        # own-account abstraction by agriculture (incl. irrigation)
        df['W3_42'] = df.W3_4a * parameters['W3_42']
        # own-account abstraction for municipal and household use
        df['W3_46'] = df.W3_4b * parameters['W3_46']
        # sum up
        df['W3_4'] = df.W3_41 + df.W3_42 + df.W3_43 + df.W3_44 + df.W3_45 + df.W3_46
        # adding some split-ups of W3_4
        # of which water abstraction from surface water
        df['W3_4c'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # of which water abstraction from groundwater
        df['W3_4d'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        # Collection of precipittion water and urban runoff
        df['W3_5'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        # Actual evapo-transpiration induced by irrigation
        df['W3_6'] = df.W3_4a * parameters['W3_6']

        # Evaporation from industry and other uses
        df['W3_7'] = df.W3_44 * parameters['W3_7']

        # artificial outflow of water to other territories and the sea
        # direct discharche of wastewater to the sea
        df['W3_81'] = (df.W3_4b * parameters['W3_81'] * df.CoastID) - df.W3_82
        # of which untreated waste water
        df['W3_81b'] = df.W3_81 - df.W3_81a
        # sum up
        df['W3_8'] = df.W3_81 + df.W3_82

        # other chnage in volume of stocks and adjustments
        df['W3_9'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        # now we sum up the total outflow
        df['W3'] = df.W3_1 + df.W3_2 + df.W3_3 + df.W3_4 + df.W3_5 + df.W3_6 + df.W3_7 + df.W3_8 + df.W3_9

        logger.debug('**** calculate Net Ecosystem Water Balance')
        # groundwater net surface recharge
        df['W4b'] = -(df.W2_2a + df.W3_4d)
        # now full balance
        df['W4'] = df.W2 - df.W3

        logger.debug('**** Closing Stocks descriptors')
        df['W5_11'] = df.W1_11
        df['W5_12'] = df.i1
        df['W5_21'] = df.W1_21
        df['W5_22'] = df.i4
        df['W5_3'] = df.W1_3
        df['W5_41'] = df.W1_41
        df['W5_42'] = df.W1_42
        df['W5_5'] = df.W8_5

        logger.debug('*** run Accessible Water Resource estimations...')
        logger.debug('**** Net primary and secondary water resources')
        # first some other split-ups of W2
        df['W2a'] = df.W2_1 + df.W2_2 + df.W2_3
        df['W2b'] = df.W2_4 + df.W2_5 + df.W2_6
        # now cal the net
        df['W6'] = df.W2a + df.W2b - df.W3_2 - df.W3_3

        logger.debug('**** Net ecosystem Water surplus')
        # first total adjustment of natural renewable water resources
        # regular renewable water resources
        df['W7_11'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # legally reserved runoff
        df['W7_12'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # inflow not garantueed trough law
        df['W7_13'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # outflow garantueed by law
        df['W7_14'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # water natural resources unuseable due to quality
        df['W7_15'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # remote inaccessable water resources
        df['W7_16'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # Exploitable irrugular renewable water resources
        df['W7_17'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # previous net accumulation in water stocks
        df['W7_18'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # other accessable adjustments of natural water
        df['W7_19'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # summ up
        df['W7_1'] = df.W7_11 + df.W7_12 + df.W7_13 + df.W7_14 + df.W7_15 + df.W7_16 + df.W7_17 + df.W7_18 + df.W7_19

        # Total adjustment of secondary renewable water resources
        # secondary water resources unusable due to quality
        df['W7_21'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # other adjustments of secondary resources
        df['W7_22'] = -df.W2_63
        df['W7_2'] = df.W7_21 + df.W7_22

        # cal exploitable natural water resources ENWR
        df['W7a'] = df.W2a + df.W7_1 + df.W3_9
        # exploitable secondary water resources (ESWR)
        df['W7b'] = df.W2b + df.W7_2
        # sum up
        df['W7'] = df.W7a + df.W7b

        logger.debug('**** Net Ecosystem Accessible Water Potential')
        # lakes and reservoirs runoff potential
        df['W8_1'] = (df.i2 * df.i1 / (df.i12 + 1.)) + (df.W1_11 / parameters['W8_1'])
        # river runoff land potential
        df['W8_2'] = df.W3_3 * df.i4 / df.a
        # snow and ice discharge potential
        df['W8_3'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # groundwater accessible recharge potential
        # -first we need i8 = aquifer accessible area
        df['i8'] = np.where((df.W1_41 + df.W1_42) > df.a, 1, (df.W1_41 + df.W1_42)/df.a)
        df['W8_4'] = np.where((df.W4b * df.i8) > 0, df.W4b * df.i8, 0)

        # sum up
        df['W8'] = np.where((df.W8_1 + df.W8_2 + df.W8_3 + df.W8_4 + df.W8_5) >=
                            0,  df.W8_1 + df.W8_2 + df.W8_3 + df.W8_4 + df.W8_5, 0)

        logger.debug('*** run Total Water Uses...')
        logger.debug('**** Total use of ecosystem water')
        # abstraction of inland water
        df['W9_1'] = df.W3_4
        # green water use
        # first spontaneous actual evao-transpiration from managed forest
        df['W9_22'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        df['W9_2'] = df.W9_21 + df.W9_22

        # collection of precipitation water and urban run off
        df['W9_3'] = df.W3_5
        # split up in
        # collection of precipitation water
        df['W9_32'] = df['W3_52'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # collection of urban run off
        df['W9_31'] = df['W3_51'] = df.W9_3 - df.W9_32

        # sum up
        df['W9'] = df.W9_1 + df.W9_2 + df.W9_3

        logger.debug('**** Direct use of water and domestic consumption')
        # imports of water from other territories (part of W2_4)
        df['W10_1'] = df.W2_4
        # export of water to other territories
        df['W10_2'] = df.W3_8
        # withdrawal of water from the sea
        df['W10_3'] = df.W2_42
        # re-use of water within economic units
        df['W10_4'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # imports of water/commodities & residuals content
        df['W10_5'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # Exports of water/commodities & residuals content
        df['W10_6'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # sum for direct use
        df['W10a'] = df.W9 + df.W10_1 + df.W10_3 + df.W10_4 + df.W10_5
        # cal dometic consumption
        df['W10b'] = df.W10a - df.W10_2 - df.W10_4 - df.W10_6

        logger.debug('**** Virtual Water embedded into imported commodities')
        df['W11'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        logger.debug('**** Total Water Requirement')
        df['W12'] = df.W10a + df.W11

        logger.debug('*** Table of indices of intensity of use and ecosystem health...')
        logger.debug('**** Sustainable Intensity of water use overall index (SIWU)')
        # intensity of water use
        df['W13_1'] = np.where(((df.W7 + 1) / (df.W9 + 1)) <= 1, (df.W7 + 1) / (df.W9 + 1), 1)

        # Water bodies quantitative status
        # quantitative state of lanke&reservoir index
        df['W13_21'] = 1  # TODO: why is that one - Excel table says 'per memory'
        # quantitative state accessible goundwater index
        df['W13_22'] = 1. - ((1. - df.i9) * df.i8)
        # dependency from artificial inflows from other territories and the sea
        df['W13_24'] = np.where(((df.W9 + 1) / ((df.W9 + df.W10_1) + 1)) >= 1, 1, df.W9 / ((df.W9 + df.W10_1) + 1))
        # now do the geometric average
        df['W13_2'] = np.power(df.W13_21 * df.W13_22 * df.W13_23 * df.W13_24, 1./4)
        # now the geometric mean of W13_1 and W13_2 for the SIWU
        df['W13'] = np.power(df.W13_1 * df.W13_2, 1./2)

        logger.debug('**** composite index of ecosystem water health (EWH)')
        # water assests bio-chmical diagnosis / SELU composite index
        # first lakes & reservoir index
        # df['W14_11'] = 1. - ((1. - df.i3) * (df.i1 / df.i0) )
        df['W14_11'] = np.where(1. - ((1. - df.i3) * (df.i1 / df.i0)) >= 0, 1. - ((1. - df.i3) * (df.i1 / df.i0)), 0)

        # rivers and other streams
        # for that we also need i7 = SELU quality weighted SRMUs
        df['i7'] = df.i6 * df.i5
        df['W14_12'] = df.i7 / df.W1_21
        # glacier, snow & ice
        df['W14_13'] = 1  # TODO: why is that one - Excel table says 'per memory'
        # accessible ground water
        df['W14_14'] = 1. - ((1. - df.i11) * (df.i10 / df.i0))
        # cal geometric mean
        df['W14_1'] = df.W14_11 * df.W14_12 * df.W14_13 * df.W14_14

        # index based on indirect water quality indicators
        # vulnerability to urban, industrail & agriculture polution
        df['W14_21'] = 1  # TODO: why is that one - Excel table says 'per memory'
        # water born diseases to humans
        df['W14_22'] = 1  # TODO: why is that one - Excel table says 'per memory'
        # water borne diseases to flora and fauna
        df['W14_23'] = 1  # TODO: why is that one - Excel table says 'per memory'
        # other indirect indicators of water polution
        df['W14_24'] = 1  # TODO: why is that one - Excel table says 'per memory'
        # now calculate the product (TODo: why... why not the geometric mean)
        df['W14_2'] = df.W14_21 * df.W14_22 * df.W14_23 * df.W14_24

        # cal the composite index
        df['W14'] = df.W14_1 * df.W14_2

        logger.debug('**** Water ecological internal unit value (WEIUV)')
        df['W15'] = (df.W13 + df.W14) / 2.

        logger.debug('*** calculate area-specific values')
        df['W8_ha'] = df.W8 / df.a
        df['W7_ha'] = df.W7 / df.a
        df['W9_ha'] = df.W9 / df.a

        return df.drop('a', axis=1)
