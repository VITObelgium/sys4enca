"""Total Ecosystem Capability account."""

import logging
import os

import pandas as pd

import enca
from enca.carbon import Carbon
from enca.framework.config_check import ConfigItem
from enca.framework.errors import Error
from enca.infra import Infra
from enca.water import Water

logger = logging.getLogger(__name__)

_input_codes = {'HYBAS_ID': 'selu_ID',
                'Area_rast': 'Area_ha',
#                'Area_poly': 'Area_ha',
#                'delta_ha': 'Area_delta_ha',
                'C11': 'Net Ecosystem Carbon Potential',
                'SCU': 'Sustainable Intensity of Carbon Use Index',
                'CEH': 'Carbon Ecosystem Health Index',
                'CIUV': 'Carbon Internal Unit Value',
                'W8': 'Net Ecosystem Accessible Water Potential',
                'W13': 'Sustainable Intensity of Water Use Index',
                'W14': 'Water ecosystem Health Index',
                'W15': 'Water Internal Unit Value',
                'EIP4': 'Total Ecosystem Infrastructure Potential',
                'EISUI': 'Ecosystem Infrastructure Sustainable Use Index',
                'EIH': 'Ecosystem Infrastructure Health Index',
                'EIIUV': 'Ecosystem Infrastructure Internal Unit Value'}

_output_codes = {'ECU': 'Ecosystem Capability Unit',
                 'C_EC': 'Carbon Ecosystem Capability',
                 'C_EC_ha': 'Carbon Ecosystem Capability par ha',
                 'W_EC': 'Water Ecosystem Capability',
                 'W_EC_ha': 'Water Ecosystem Capability par ha',
                 'EI_EC': 'Ecosystem Infrastructure Capability',
                 'EI_EC_ha': 'Ecosystem Infrastructure Capability par ha',
                 'TEC': 'Total Ecosystem Capability',
                 'TEC_ha': 'Total Ecosystem Capability par ha'}

class Total(enca.ENCARun):
    """TEC account Run class."""

    component = 'TOTAL'

    _indices_average = ['CEH', 'CEIUV', 'SCU', 'ECU', 'EIH', 'EIIUV', 'EISUI',
                        'WEH', 'WEIUV', 'SIWU', 'TEC_ha', 'C_EC_ha', 'W_EC_ha', 'EI_EC_ha']

    def __init__(self, config):
        super().__init__(config)

        self.config_template.update({
            self.component: {
                'carbon_result': ConfigItem(),
                'water_result': ConfigItem(),
                'infra_result': ConfigItem(),
                'ECUadj_Carbon': ConfigItem(default=10),# normalize to 10 tC/Yr/ha
                'ECUadj_Water': ConfigItem(default=10000),# rc7*100, #normalize to 10000m3
                'ECUadj_Infra': ConfigItem(default=1)
            }
        })

    def _start(self):

        area_stats = self.area_stats()

        for year in self.years:
            indices = self.indices(year)
            indices.to_csv(os.path.join(self.statistics, f'SELU_stats_{year}.csv'))
            stats_shape_selu = self.statistics_shape.join(indices)
            stats_shape_selu.to_file(
                os.path.join(self.maps, f'{self.component}_Indices_SELU_{year}.gpkg'))

            self.write_reports(indices, area_stats, year)

    def indices(self, year, base=False):
        config = self.config[self.component]
        carbon_indices = os.path.join(config['carbon_result'], 'statistics', f'{Carbon.component}_indices_{year}.csv')
        water_indices = os.path.join(config['water_result'], 'statistics', f'{Water.component}_indices_{year}.csv')
        #temporary patch MDG
        #if year == 2015:
        #    water_indices = os.path.join(config['water_result'], 'statistics', f'{Water.component}_indices_2021.csv')
        infra_indices = os.path.join(config['infra_result'], 'temp', f'NCA_INFRA_SELU_{year}.csv')

        df_carbon = pd.read_csv(carbon_indices)
        df_water = pd.read_csv(water_indices)
        df_infra = pd.read_csv(infra_indices)

        # Merge component dataframes, and keep only columns from _input_codes
        df = df_carbon.merge(df_water, on=enca.HYBAS_ID).merge(df_infra, on=enca.HYBAS_ID)[_input_codes.keys()]
        if not (df_carbon[enca.HYBAS_ID].isin(df[enca.HYBAS_ID]).all() and
                df_water[enca.HYBAS_ID].isin(df[enca.HYBAS_ID]).all()):
            logger.warning('The number of SELU is not identical across the three ENCA components, '
                           'INFRA wil be leading.')
        if not df_infra[enca.HYBAS_ID].isin(df[enca.HYBAS_ID]).all():
            raise Error('Not all SELU from INFRA are present in the output of the other components.'
                        f'#Infra: {len(df_infra)}, #Carbon: {len(df_carbon)}, '
                        f'#Water: {len(df_water)}, # merged: {len(df)}')
        df = df.rename(columns={'C11': 'NECP',
                                'CIUV': 'CEIUV',
                                'W8': 'NEWP',
                                'EIP4': 'TEIP',
                                'EIIUV': 'EIIUV',
                                'W13': 'SIWU',
                                'W14': 'WEH',
                                'W15': 'WEIUV'}).set_index(enca.HYBAS_ID)
        if base:
            df['CEH'] = 1.0
            df['CEIUV'] = df['CEH'] * df['SICU']
            df['EIH'] = 1.0
            df['EIIUV'] = df['EIH'] * df['EISUI']
            df['EWH'] = 1.0
            df['WEIUV'] = df['WEH'] * df['SIWU']

        # normalize
        df['NECP_n'] = df['NECP'] / float(config['ECUadj_Carbon'])  # (Scaled) gross capability
        df['NEWP_n'] = df['NEWP'] / float(config['ECUadj_Water'])
        df['TEIP_n'] = df['TEIP'] / float(config['ECUadj_Infra'])

        # calculate ECU
        df['ECU'] = (df['CEIUV'] + df['WEIUV'] + df['EIIUV']) / 3  # unit-less
        df['C_EC'] = df['NECP_n']*df['ECU']
        df['W_EC'] = df['NEWP_n']*df['ECU']
        df['EI_EC'] = df['TEIP']*df['ECU']
        df['TEC'] = df['C_EC']+df['W_EC']+df['EI_EC']

        # calculate per ha
        df['C_EC_ha'] = df['C_EC']/df['Area_rast']
        df['W_EC_ha'] = df['W_EC'] / df['Area_rast']
        df['EI_EC_ha'] = df['EI_EC'] / df['Area_rast']
        df['TEC_ha'] = df['TEC'] / df['Area_rast']

        return df
