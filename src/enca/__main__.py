import argparse
import logging
import sys

from importlib.resources import as_file, files
import yaml

import enca
import enca.components
import enca.framework.geoprocessing
import enca.framework.run
from enca import _
from enca.framework.errors import Error, ConfigError
from enca.framework.cancelled import Cancelled
from enca.framework.error_values import (RUN_OK, ERROR_PROCESSING, ERROR_CONFIG, ERROR_OTHER, RUN_WARN, CANCEL)

# Localization of argparse using gettext.  We *must* set up the gettext domain before importing argparse.
import gettext

with as_file(files(enca).joinpath('locale')) as localedir:
    gettext.bindtextdomain('argparse', localedir)
    gettext.textdomain('argparse')

logger = logging.getLogger('enca')

def parse_args():
    parser = argparse.ArgumentParser(prog=enca.__name__)
    parser.add_argument('--verbose', action='store_const', const=True)
    parser.add_argument('--output-dir', help=_('Default OUTPUT-DIR is current working directory.'), metavar='DIR')
    parser.add_argument('--run-name', help=_('Output is written to OUTPUT-DIR/RUN-NAME.'), metavar='NAME')
    parser.add_argument('--continue', action='store_const', const=True, help=_('Continue from an existing run directory.'))
    parser.add_argument('--component', help=_('ENCA component to run'), choices=enca.components.list_components())
    parser.add_argument('--started-from', help='To log app that launched SYS4ENCA', metavar='APP')
    parser.add_argument('--tier', help=_('Tier level'), type=int)
    parser.add_argument('--aoi-name', help=_('Area of interest name'))
    parser.add_argument('--years', type=int, nargs='+',
                        metavar='YEAR', help=_('Years for which to run the calculation.'))
    parser.add_argument('config', help=_('yaml configuration file for the account calculation.'))

    return parser.parse_args()

def main():
    """Start a run from the command line ."""
    config = dict()
    try:
        args = parse_args()
        if args.config:
            with open(args.config) as f:
                config = yaml.safe_load(f)

        # Override config keys with 'not None' command line arguments:
        config.update({key: val for key, val in vars(args).items() if val is not None})

        # Set default output dir if not specified at command line or in config file.
        if 'output_dir' not in config:
            config['output_dir'] = '.'
    except Exception as e:
        logger.error(f'Configuration error: {e}')
        sys.exit(ERROR_CONFIG)

    try:
        run = enca.components.make_run(config)
        run.start()
    except Cancelled:
        sys.exit(CANCEL)
    except ConfigError as e:
        #print(e)
        sys.exit(ERROR_CONFIG)
    except Error as e:
        #print(e)
        sys.exit(ERROR_PROCESSING)
    except Exception as e:
        #print(e)
        sys.exit(ERROR_OTHER)

    sys.exit(RUN_OK)        

if __name__ == '__main__':
    main()
