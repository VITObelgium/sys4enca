"""Default parameters and helper functions for user customization."""

import pandas as pd


defaults = dict(
    C1_2=0.12,
    C1_3_1=0.25,
    C2_752=0.04,
    C3_2=0.4,
    C3_21=0.5,
    C3_3=25,
    C3_51=0.2,
    C3_52=0.8,
    C3_5=1.5,
    C4_4=0.05,
    C6_41=0.15,
    C6_42=0.02,
    C6_43=0.2,
    C6_5=0.9,
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
    W8_5=0.5,
    ECUadj_Carbon=10,  # normalize to 10 tC/Yr/ha
    ECUadj_Water=10000,  # rc7*100, #normalize to 10000m3
    ECUadj_Infra=1
)  #: Default parameters.


def read(file):
    """Read parameters from a CSV file with columns 'code' and 'value' and return as dict."""
    return dict(pd.read_csv(file, index_col='code', usecols=['code', 'value'])['value'])
