import enca

class Carbon(enca.ENCARun):

    run_type = enca.ENCA
    component = 'CARBON'

    def _start(self):
        print('Hello from ENCA Carbon')
