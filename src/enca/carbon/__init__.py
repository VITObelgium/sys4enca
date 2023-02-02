import logging
import re

import numpy as np
import pandas as pd

import enca
from enca.config_check import ConfigRasterDir
from enca.errors import Error
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
          self.component: {  # TODO: choice of input components is configurable (e.g. FireIntensity depending on availability)
              CarbonForest.component: ConfigRasterDir(raster_type=RasterType.ABSOLUTE_VOLUME),
              CarbonSoil.component: ConfigRasterDir(raster_type=RasterType.ABSOLUTE_VOLUME)
          }

        })

    def _start(self):
        logger.debug('Hello from ENCA Carbon')
        for year in self.years:
            selu_stats = self.selu_statistics(year)
            self.indices(selu_stats, year)

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
        return result

    def indices(self, selu_stats, year):
        # loop over the InputCodes and assign either SELU results or fixed numbers when we do not have the calculations
        logger.debug('*** assign data to input columns')
        df = selu_stats
        area = df.Area_rast
        input_codes = self.config[self.component]['input_codes']
        parameters = self.config[self.component]['parameters']
        for code, value in input_codes.items():
            # run check if column exits and has to be renamed or if we have to create it with set value
            if (isinstance(value, str)):
                if value in df.columns:  # rename the column to the code
                    df.rename({value: code}, axis='columns', inplace=True)
                else:  # missing input data
                    raise Error(f'Missing SELU raster column "{value}", to be assigned to code "{code}".')
            else:
                # we have a factor in the SELU raster result assignment, just generate the column new
                df[code] = float(value)

        # convert Cow carbon to Cow_in_Liv ratio
        df['Cow_in_Liv'] = np.where(df.C1_43 == 0, 0, df.Cow_in_Liv / df.C1_43)

        # calculate average ILUP per SELU
        df['C10_2ILUP'] = np.where((df.C10_2ILUP / area) > 1.0, 1.0, df.C10_2ILUP / area)
        # calulate average CEH7 health indicator per SELU
        df['CEH7'] = np.where((df.CEH7 / area) > 1.0, 1.0, df.CEH7 / area)
        # calculate fire split ratio (hman versus natural firwe)
        df['fire_ratio'] = np.where((df.fire_ratio / area) > 1.0, 1.0, df.fire_ratio / area)
        # calculate average CEH4 indicator (vulnerability to fire)
        df['CEH4'] = np.where((df.CEH4 / area) > 1.0, 1.0, df.CEH4 / area)

        # setup fire correctly fire * fire-intensity
        df['fire_inten'] = np.where(df.fire_inten == 1.0, 1.0,
                                    np.where((df.fire_inten / area) > 5.0, 5.0, df.fire_inten / area))
        df['fire'] = df.fire * df.fire_inten

        # generae the carbon input for man-made and natural fires
        ##first man-made fires with parameter
        df['C4_31'] = df.fire * df.fire_ratio
        ##now natural
        df['C6_3'] = df.fire - df.C4_31
        # drop the un-needed input column
        df.drop('fire', axis=1, inplace=True)

        # now we run a check for litter and root_carbon since we have data and do not want to use factors (only when there is no data available)
        # check if we have a a litter value where we have a AGB value and if this value is OK ELSE take formula to generate
        df['C1_2'] = np.where(((df['C1_2'] == 0) & (df['C1_1'] != 0)) | (df['C1_2'] < (df['C1_1'] * 0.07)),
                              df['C1_1'] * parameters['C1_2'], df['C1_2'])
        # check if our read out BGB makes sense
        df['C1_3_1'] = np.where(((df['C1_3_1'] == 0) & (df['C1_1'] != 0)) | (df['C1_3_1'] < (df['C1_1'] * 0.2)),
                                df['C1_1'] * parameters['C1_3_1'], df['C1_3_1'])

        # now we run calculations
        logger.debug('*** run Ecosystem Carbon Basic Balance...')
        logger.debug('**** Opening Stock Total')
        # open stock is just the sum of all input STOCK raster values
        df['C1'] = df.C1_1 + df.C1_2 + df.C1_3_1 + df.C1_3_2 + df.C1_43

        logger.debug('**** Total inflow of biocarbon (inflow)')
        ##calculation of net increase of secondary biocarbon
        # cal formation of dead organic matter (DOM) = C6_5
        df['C6_5'] = df['C2_52'] = df.C2_3 * parameters['C6_5']
        # cal net increase of livestock
        df['C2_53'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # cal C2_54 which is C6_43 = decomposing of litter to soil
        df['C6_43'] = df['C2_54'] = df.C1_2 * parameters['C6_43']
        # cal net increase in secondary carbon
        df['C2_5'] = df.C2_52 + df.C2_53 + df.C2_54

        # cal inflow of carbon from other countries
        df['C2_6'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        ##cal Production residuals and transfer
        # first calculate the total agriculture crop net removals
        # cal the total of agriculture crop net removals
        df['C3_1'] = df.C3_11 + df.C3_12 + df.C3_13 + df.C3_14 + df.C3_15 + df.C3_16 + df.C3_17 + df.C3_18 + df.C3_19

        # cal agriculture crop residuals (incl. removals and returns) which is also C3_2
        df['C2_71'] = df['C3_2'] = df.C3_1 * parameters['C3_2']
        ##cal C2_72 (manure return and application) is longer and circle ((C3_3 + C2_752)/2)
        # carbon of biomas used by grazing livestock

        df['C3_3'] = df.C2_3 / area * (df.C1_43 / 24.)
        # cal supply of livestock feed (minus the grazing)
        df['C2_752'] = np.where(((df.C1_43 * parameters['C2_752']) - df.C3_3) > 0,
                                (df.C1_43 * parameters['C2_752']) - df.C3_3, 0)
        df['C2_72'] = (df.C3_3 + df.C2_752) / 2.

        # cal forest residuals = C3_5
        df['C2_73'] = df['C3_5'] = df.C3_4 * parameters['C3_5']
        # cal fishery discharge
        df['C2_74'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        ##cal C2_751(supply of biofuel)
        # first calculate combustion of other biogenic fuel - which is also C3_51 (removals of forestry leftovers and byproducts)
        df['C4_34'] = df['C3_51'] = df.C3_5 * parameters['C3_51']
        df['C2_751'] = df.C4_33 + df.C4_34

        # cal other transfers recived from the supply and use system
        df['C2_753'] = 0  # TODO: why is that Zero - Excel table says 'per memory'
        # sum up all transfers
        df['C2_75'] = df.C2_751 + df.C2_752 + df.C2_753

        # finally - now really calculate the carbon in production residuals and transfer
        df['C2_7'] = df.C2_71 + df.C2_72 + df.C2_73 + df.C2_74 + df.C2_75

        # cal consumption residuals
        df['C2_8'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        ## here we can now calculate the total of carbon inflow
        df['C2'] = df.C2_3 + df.C2_5 + df.C2_6 + df.C2_7 + df.C2_8

        logger.debug('**** Total withdrawla of biocarbon')

        ##cal some split ups of agriculture crop residuals
        # removals of agriculture leftovers and byproducts
        df['C3_21'] = df.C3_2 * parameters['C3_21']
        # returns of agriculture leftovers
        df['C3_22'] = df.C3_2 - df.C3_21
        # cal an other split up for forest residuals - here: returns of forestry left overs
        df['C3_52'] = df.C3_5 * parameters['C3_52']
        # sum up the total harvest of crops, forest etc.
        df['C3_a'] = df.C3_1 + df.C3_2 + df.C3_3 + df.C3_4 + df.C3_5
        # withdrawals of secondary carbon
        df['C3_b'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        ## here we have now the total withdrawal of bio-carbon
        df['C3'] = df.C3_a + df.C3_b

        logger.debug('**** Net indirect anthropogenic losses of biocarbon & biomass combustion')
        # net loss due to land use change
        df['C4_11a'] = df.C4_111 + df.C4_112
        # TODO: cal of some net losses not used up to now
        # artificial development urban and road
        df['C4_113'] = (df.C4_11b / (df.C4_11a + 1)) * df.C4_111
        # artificial development mining
        df['C4_114'] = df.C4_11b - df.C4_113
        # cal the net loss of carbon due to land use change
        df['C4_1'] = df.C4_11a + df.C4_11b

        # dumping and leaking of biocarbon to water bodies
        df['C4_2'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        ##combusion of ecosystem biocarbon
        # other biomass fires induced by humans
        df['C4_32'] = 0  # TODO: why is that Zero - Excel table says 'per memory'

        # sum up all combustion
        df['C4_3'] = df.C4_31 + df.C4_32 + df.C4_33 + df.C4_34

        # other emmission to atmosphere of antropogeneic origin (in this case farthing of cows )
        df['C4_4'] = (df.C1_43 / 24.) * parameters['C4_4'] * (12. / 16.) * df.Cow_in_Liv

        ##sum up the net indirect losses of bio carbon and biomass combution
        df['C4'] = df.C4_1 + df.C4_2 + df.C4_3 + df.C4_4

        logger.debug('**** Total use and induced loss of ecosystem carbon')
        # cal total losses
        df['C5'] = df.C3 + df.C4

        logger.debug('**** Losses of biocarbon due to natural and multiple causes')
        ##total composing of biomass
        # first we need second ecosystem respiration_AGB
        df['C6_41'] = (df.C1_2 + df.C6_5) * parameters['C6_41']
        # secondary ecosystem respiration_BGB
        df['C6_42'] = (df.C1_3_2 + df.C6_43) * parameters['C6_42']

        # add to that the decomposing of litter to soil
        df['C6_4'] = df.C6_41 + df.C6_42 + df.C6_43

        # sum up to the losses of biocarbon due to natural and multiple causes
        df['C6'] = df.C6_2 + df.C6_3 + df.C6_4 + df.C6_5

        logger.debug('**** Total outflow of biocarbon (total losses)')
        df['C7'] = df.C5 + df.C6

        logger.debug('**** Closing stock')
        # above living biomas
        df['C9_1'] = df.C1_1 + df.C2_3 - df.C3_a - df.C4_11a - df.C4_31 - df.C4_32 - df.C6_3 - df.C6_5
        # litter and deadwood carbon
        df['C9_2'] = df.C1_2 + df.C2_71 + df.C2_73 - df.C3_21 - df.C3_51 - df.C4_34 - df.C6_41 - df.C6_43 + df.C6_5
        # roots carbon
        df['C9_3_1'] = df.C9_1 * parameters['C1_3_1']
        # soil organic carbon
        df['C9_3_2'] = df.C1_3_2 + df.C2_54 + (df.C2_72 / 2.) - df.C4_11b - df.C6_2 - df.C6_42
        # livestock carbon
        df['C9_43'] = df.C1_43 + df.C2_53 - df.C3_b

        ##sum it up to closing stock
        df['C9'] = df.C9_1 + df.C9_2 + df.C9_3_1 + df.C9_3_2 + df.C9_43

        logger.debug('**** calculate balance')
        # net ecosystem balance 1 (flows)
        df['NECB1'] = df.C2 - df.C7
        # net ecosystem carbon balance 2 (stocks)
        df['NECB2'] = df.C9 - df.C1
        # adjustment
        df['ADJ'] = df.NECB2 - df.NECB1

        logger.debug('*** run Accessible Resource Surplus calculations...')
        logger.debug('**** Net Ecosystem Accessible Carbon surplus...')
        # cal net inflow of biomass carbon
        df['C10_1'] = df.C2 - df.C6_3 - df.C6_41 - df.C6_43 - df.C6_5
        # we can generate the NEACS indicator - net ecosystem accessible carbon surplus
        df['C10'] = np.where(df.C10_1 > 0, df.C10_1 * df.C10_2ILUP, 1)

        logger.debug('**** Net Ecosystem Carbon Potential calculation...')
        # the overall net potential is NPP + second biocarbon + forest mobilisation of stock MINUS the fire destruction
        df['C11'] = (df.C2_3 + df.C2_53 + df.C2_54) - (df.C6_2 / 3.) - df.C6_3

        logger.debug('*** run Total Uses of Ecosystem Bio and Geo-Carbon calculation')
        # lol... all already done
        # C3, C4, C5

        logger.debug('*** Calculation of Indices of intensity of use and ecosystem health')
        # sustainable intensity of carbon use index
        df['SCU'] = np.where((df.C10 > df.C5), 1, df.C10 / df.C5)
        # Ecosystem carbon health index
        df['CEH'] = df.CEH1 * df.CEH6 * df.CEH7 * df.CEH4
        # ecosystem carbon internal unit value
        df['CIUV'] = (df.SCU + df.CEH) / 2.

        logger.debug('*** calculation of additional indicators')
        # emmision in CO2eq of combustion
        # NOTE: TODO: I changed the formula since jean-Louis had twise the fire in then!
        df['CO2eq_COMB'] = (df.C4_33 + df.C4_34) / 12. * 44.
        # emmission in CO2eq from uman induced fires
        df['CO2eq_FIRE'] = (df.C4_31 + df.C4_32) / 12. * 44.
        # emmission in CO2eq of livestock
        df['CO2eq_ANIM'] = (df.C4_4 / 12. * 16.) * 23.
        # sum of all man-made emmissions
        df['CO2eq_Man'] = df.CO2eq_COMB + df.CO2eq_FIRE + df.CO2eq_ANIM
        # emmissions in CO2eq of nautral caoses (fire, etc)
        df['CO2eq_Nat'] = df.C6_3 / 12. * 44.
        # total emmission
        df['CO2eq_Tot'] = df.CO2eq_Man + df.CO2eq_Nat

        logger.debug('*** calculate area-specific values')
        df['C11_ha'] = df.C11 / area
        df['C10_ha'] = df.C10 / area
        df['C5_ha'] = df.C5 / area
        df['C10_1_ha'] = df.C10_1 / area
        df['C2_3_ha'] = df.C2_3 / area

        logger.debug('Indices for %s:\n%s', year, df)
