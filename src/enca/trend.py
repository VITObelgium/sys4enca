"""Ecosystem Cabability Trend account."""

import glob
import os
import logging

import numpy as np
import pandas as pd
import scipy.stats as stats

import enca
from enca.framework.config_check import ConfigItem
from enca.framework.errors import Error

logger = logging.getLogger(__name__)

_keys_trend = ['C_EC', 'W_EC', 'EI_EC', 'ECU', 'TEC']


class Trend(enca.ENCARun):
    """Trend Run class."""

    run_type = enca.RunType.ACCOUNT
    component = 'TREND'

    def __init__(self, config):
        """Update config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                'total_result': ConfigItem()}})

    def _start(self):
        # Get all SELU_stats_{year}.csv files:
        stats_files = glob.glob(os.path.join(self.config[self.component]['total_result'],
                                             'statistics', 'SELU_stats_*.csv'))
        logger.debug('Found following total SELU stats files: %s', stats_files)
        if not stats_files:
            logger.error('Found no SELU stats files (SELU_stats_yyyy.csv) in folder %s', os.path.join(self.config[self.component]['total_result'], 'statistics'))
            raise Error(f"Found no SELU stats files (SELU_stats_yyyy.csv) in folder {os.path.join(self.config[self.component]['total_result'], 'statistics')}")
        df_merged = None
        years = []
        year_suffixes = []
        for filename in stats_files:
            df_in = pd.read_csv(filename, index_col=enca.HYBAS_ID)
            # Extract year suffix from filename 'SELU_stats_yyyy.csv'
            years.append(int(filename[-8:-4]))
            suffix = '_' + filename[-6:-4]
            year_suffixes.append(suffix)

            if df_merged is None:
                df_merged = df_in[['Area_rast']].copy()

            df_in = df_in[[col for col in df_in.columns
                           if col not in ('Area_rast', 'Area_poly')]].add_suffix(suffix)
            df_merged = df_merged.merge(df_in, on=enca.HYBAS_ID)

        df_trend = df_merged[['Area_rast']].copy()
        for key in _keys_trend:
            logger.debug('Calculate trend for %s', key)
            input_cols = [key + suffix for suffix in year_suffixes]
            df_temp = df_merged[input_cols].copy()
            # calculate regression:
            df_trend[key + '_slope'] = df_temp.apply(lambda row: calc_slope_wrapper(row, years), axis=1)
            # calculate coefficient of correlation / determination:
            df_trend[key + '_CV'] = df_temp.apply(lambda row: calc_variation(row, years), axis=1)

        df_temp = pd.DataFrame(index=df_merged.index)
        # normalize TEC based on correlation coefficient
        # total of 3 subcomponents needs to be 3*1, so calculate factor
        f = 3./(df_trend['C_EC_CV'] + df_trend['W_EC_CV'] + df_trend['EI_EC_CV'])
        for suffix in year_suffixes:
            df_temp['TEC_n' + suffix] = df_merged['C_EC' + suffix] * df_trend['C_EC_CV'] * f \
                + df_merged['W_EC' + suffix] * df_trend['W_EC_CV'] * f \
                + df_merged['EI_EC' + suffix] * df_trend['EI_EC_CV'] * f
        df_trend['TEC_n'+'_slope'] = df_temp.apply(lambda row: calc_slope_wrapper(row, years), axis=1)
        df_trend['TEC_n'+'_CV'] = df_temp.apply(lambda row: calc_variation(row, years), axis=1)

        # calculate disturbance trend
        df_trend['trend'] = df_trend.apply(calc_disturbance, axis=1)
        df_trend['cause'] = df_trend.apply(calc_disturbance_cause, axis=1)

        # push small hybas out of reference calculation
        area_Hybas = 1500  # minimal size of HYBAS to get reliable results
        df_trend.loc[df_trend['Area_rast'] < area_Hybas, 'ECU_slope'] = np.nan

        output_file = os.path.join(self.statistics, f'NCA_TEC-trend_Indices_SELU_{years[0]}-{years[-1]}')
        df_trend.to_csv(output_file + '.csv')
        stats_shape_selu = self.statistics_shape.join(df_trend)
        stats_shape_selu.to_file(output_file + '.gpkg')


def calc_slope_wrapper(row, years):
    if np.inf in row.values:
        # could happen if no information was available for ref. year 2000
        return np.nan
    slope_prct, delta = calc_slope(row.values, years)
    return slope_prct.values[0]


def calc_slope(row, years):
    """Calculate a linear least-squares regression for two sets of measurements (x=values, y=years)."""
    # row_n = row/row[0]      #normalize
    # row2= [470561.71054194274,479377.67820375453,526848.138581338,545165.6345489196,507730.43297329283]
    a = stats.linregress(years, y=row)  # TODO need to get the dates from years
    # returns slope, intercept, rvalue, pvalue, stderr  #a._asdict()

    # calculate trend_start (1st year) & trend_end (last year)
    t_start = years[0] * a.slope + a.intercept
    t_end = years[-1] * a.slope + a.intercept
    slope = (t_end - t_start)/t_start
    delta = (t_end - t_start)

    # print(stats.pearsonr(years, y=row))
    # r-value is correlation coefficient, p-value is two-sided p-value for a hypothesis test whose null hypothesis is
    #   that the slope is zero
    # angle = math.atan(a.slope)
    # angle_degree = math.degrees(angle)
    # angle_prct = 100/45*angle_degree  #45° is 100%
    return pd.Series(slope*100), pd.Series(delta)  # returns yearly trend change in prct & absolute trend-delta


def calc_variation(row, years):
    """Compute the coefficient of variation = ratio of the biased standard deviation to the mean in prct."""
    # return stats.variation(row)

    # Computes the correlation coefficient to determine variation related to slope
    # Correlation coefficient is between -1 & +1 with 1 corresponding to a full correlation & 0 a no-correlation
    a = stats.linregress(years, y=row)
    return np.abs(a.rvalue)


def calc_disturbance(row, ID_FIELD='TEC'):
    """Determine the magnitude of the disturbance."""
    # slope_cat defines the degree of change (how fast was the change), note TEC_slope is expressed in % per year
    # and has positive or negative slope
    LOW = 2  # slow speed %
    MED = 5  # medium speed %
    HIG = 10  # high speed %
    # slope_cat = (row.TEC_slope > LOW)*1 + (row.TEC_slope > MED)*1 + (row.TEC_slope > HIG)*1 + \
    #             (row.TEC_slope < -LOW)*-1 + (row.TEC_slope < -MED)*-1 + (row.TEC_slope < -HIG)*-1
    # use normalized TEC_slope value, normalization done based on correlation coefficients per component
    slope_cat = (row[ID_FIELD+'_slope'] > LOW) * 1 \
        + (row[ID_FIELD+'_slope'] > MED) * 1 \
        + (row[ID_FIELD+'_slope'] > HIG) * 1 \
        + (row[ID_FIELD+'_slope'] < -LOW) * -1 \
        + (row[ID_FIELD+'_slope'] < -MED) * -1 \
        + (row[ID_FIELD+'_slope'] < -HIG) * -1
    # note slope_cat can also be 0 if between -0.1 and + 0.1

    # var_cat defines the consistency of the change
    VGOOD = 0.85  # very good determination 0.10
    GOOD = 0.70  # good      determination 0.20
    ACC = 0.50  # acceptable coefficient of variation  determination 0.30
    var_cat = (row[ID_FIELD+'_CV'] < ACC) * 1 + (row[ID_FIELD+'_CV'] < GOOD) * 1 + (row[ID_FIELD+'_CV'] < VGOOD)*1
    # note var_cat can also be 0 if not acceptable

    dict_disturbance = {(0, 0): 0, (0, 1): 1, (0, 2): 2, (0, 3): 3,  # neutral slope
                        # slow positive change no_acc -> VG consistency
                        (1, 0): 4,   (1, 1): 5,  (1, 2): 6,   (1, 3): 7,
                        (2, 0): 8,   (2, 1): 9,  (2, 2): 10,  (2, 3): 11,  # medium positive change
                        (3, 0): 12,  (3, 1): 13, (3, 2): 14,  (3, 3): 15,  # steep positive change
                        (-1, 0): -4, (-1, 1): -5, (-1, 2): -6, (-1, 3): -7,  # slow degradation
                        (-2, 0): -8, (-2, 1): -9, (-2, 2): -10, (-2, 3): -11,  # medium degradation
                        (-3, 0): -12, (-3, 1): -13, (-3, 2): -14, (-3, 3): -15  # steep degradation
                        }

    # TODO reset index if r2 is too low <0.8 ?
    # TODO how to deal with 'low TEC and what is value of low TEC that remains stable'
    # = low capability but perhaps high potential ?

    return (dict_disturbance[(slope_cat, var_cat)])


def calc_disturbance_cause(row):
    """Determine the cause of disturbance."""
    LOW = 2  # slow speed
    MED = 5  # medium speed
    HIG = 10  # high speed
    OFFSET_WATER = 3  # 25 offset to push water down ?
    slope_cat_carbon = (row.C_EC_slope > LOW) * 1 + (row.C_EC_slope > MED) * 1 + (row.C_EC_slope > HIG) * 1 \
        + (row.C_EC_slope < -LOW) * 1 + (row.C_EC_slope < -MED) * 1 + (row.C_EC_slope < -HIG) * 1
    slope_cat_water = (row.W_EC_slope > (LOW+OFFSET_WATER)) * 1 \
        + (row.W_EC_slope > (MED+OFFSET_WATER)) * 1 + (row.W_EC_slope > (HIG+OFFSET_WATER)) * 1 \
        + (row.W_EC_slope < (-LOW-+OFFSET_WATER)) * 1 \
        + (row.W_EC_slope < (-MED-+OFFSET_WATER)) * 1 + (row.W_EC_slope < (-HIG-+OFFSET_WATER)) * 1
    slope_cat_infra = (row.EI_EC_slope > LOW) * 1 + (row.EI_EC_slope > MED) * 1 + (row.EI_EC_slope > HIG) * 1 \
        + (row.EI_EC_slope < -LOW) * 1 + (row.EI_EC_slope < -MED) * 1 + (row.EI_EC_slope < -HIG) * 1

    disturb_cause = (slope_cat_carbon >= 2)*1 + (slope_cat_water >= 2)*2 + (slope_cat_infra >= 2)*4
    return (disturb_cause)
