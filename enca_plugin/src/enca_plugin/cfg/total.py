"""Total Ecosystem Capability account."""

CARBON_RESULT = 'carbon_result'
WATER_RESULT  = 'water_result'
INFRA_RESULT  = 'infra_result'
ECUADJ_CARBON = 'ECUadj_Carbon'
ECUADJ_WATER  = 'ECUadj_Water'
ECUADJ_INFRA  = 'ECUadj_Infra'

component = 'TOTAL'

_indices_average = ['CEH', 'CEIUV', 'SCU', 'ECU', 'EIH', 'EIIUV', 'EISUI',
                    'WEH', 'WEIUV', 'SIWU', 'TEC_ha', 'C_EC_ha', 'W_EC_ha', 'EI_EC_ha']

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
