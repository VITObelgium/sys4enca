"""Ecosystem Cabability Trend account."""

_keys_trend = ['C_EC', 'W_EC', 'EI_EC', 'ECU', 'TEC']


component = 'TREND'

TOTAL_RESULT = 'total_result'

LOW = 2  # slow speed %
MED = 5  # medium speed %
HIG = 10  # high speed %

OFFSET_WATER = 3  # 25 offset to push water down ?

VGOOD = 0.85  # very good determination 0.10
GOOD = 0.70  # good      determination 0.20
ACC = 0.50  # acceptable coefficient of variation  determination 0.30