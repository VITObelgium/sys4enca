import enca

class Leac(enca.ENCARun):

    run_type = enca.ENCA
    component = 'LEAC'

    def _start(self):
        print('Hello from ENCA Leac')
