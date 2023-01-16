import enca

class CarbonLivestock(enca.ENCARun):

    run_type = enca.PREPROCESS
    component = 'CARBON_LIVESTOCK'

    def _start(self):
        print('Hello from ENCA Carbon Livestock preprocessing.')
