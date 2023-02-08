import enca

class Infra(enca.ENCARun):

    run_type = enca.ENCA
    component = 'INFRA'

    def _start(self):
        print('Hello from ENCA Infra')
