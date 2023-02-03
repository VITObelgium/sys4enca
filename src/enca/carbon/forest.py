import enca

class CarbonForest(enca.ENCARun):

    run_type = enca.PREPROCESS
    component = 'CARBON_FOREST'

    def _start(self):
        print('Hello from ENCA Carbon Forest preprocessing.')
