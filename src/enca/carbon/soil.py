import enca

class CarbonSoil(enca.ENCARun):

    run_type = enca.PREPROCESS
    component = 'CARBON_SOIL'

    def _start(self):
        print('Hello from ENCA Carbon Soil preprocessing.')
