import argparse
import logging
import sys

import yaml

import enca
import enca.components
import enca.geoprocessing
import enca.run
from .config_check import ConfigError
from .errors import Error
logger = logging.getLogger('enca')


def parse_args():
    parser = argparse.ArgumentParser(prog=enca.__name__)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--output-dir', help='Default OUTPUT-DIR is current working directory.', metavar='DIR')
    parser.add_argument('--component', help='ENCA component to run', choices=enca.components.list_components())
    parser.add_argument('--tier', help='Tier level', type=int)
    parser.add_argument('--aoi-name', help='Area of interest name')
    parser.add_argument('--run-name', help='Output is written to OUTPUT-DIR/RUN-NAME.', metavar='NAME')
    parser.add_argument('--continue', action='store_true', help='Continue from an existing run directory.')
    parser.add_argument('--years', type=int, nargs='+',
                               metavar='YEAR', help='Years for which to run the calculation.')
    parser.add_argument('config', nargs='?', help='yaml configuration file for the account calculation.')

    return parser.parse_args()


def main():
    args = parse_args()
    enca.run.set_up_console_logging(logger, args.verbose)
    if args.config:
        with open(args.config) as f:
            config = yaml.safe_load(f)
    else:
        config = dict()

    # Override config keys with 'not None' command line arguments:
    config.update({key: val for key, val in vars(args).items() if val is not None})

    # Set default output dir if not specified at command line or in config file.
    if 'output_dir' not in config:
        config['output_dir'] = '.'

    try:
        run = enca.components.make_run(config)
        run.start()
    except ConfigError as e:
        logger.error('ENCA configuration error: %s Check configuration section %s.',
                     e.message, ': '.join(str(x) for x in e.path))
        sys.exit(1)
    except Error as e:
        logger.error('ENCA error: %s', e.message)
        sys.exit(1)


if __name__ == '__main__':
    main()
