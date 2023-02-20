"""Transform a raster file to match the  resolution, projection and extent of a reference raster file."""

import argparse
import os
import logging
import sys
import tempfile

from enca.framework.run import set_up_console_logging
from enca.framework.geoprocessing import GeoProcessing, RasterType

logger = logging.getLogger('enca')


def parse_args():
    """Process command line arguments."""
    parser = argparse.ArgumentParser()

    parser.add_argument('input_raster', help='Raster which we want to transform to the reference extent.')
    parser.add_argument('--ref_raster', help='Reference raster defining resolution / projection / extent.')
    parser.add_argument('--type', help='Type of the input raster.', choices=[t.name for t in RasterType],
                        default=RasterType.CATEGORICAL.name)
    parser.add_argument('--suffix', help='Suffix to append to the output filename', type=str, default='')
    parser.add_argument('--verbose', action='store_true')

    return parser.parse_args()


def main():
    """Enter here."""
    args = parse_args()

    set_up_console_logging(logger, args.verbose)

    output_file = args.suffix.join(os.path.splitext(os.path.basename(args.input_raster)))
    logger.debug('Write to output file %s.', output_file)
    if os.path.isfile(output_file):
        print(f'Output file "{output_file}" already exists.  Quitting.')
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp:
        # TODO use bbox instead of ref_raster?
        processor = GeoProcessing('ENCA tool', 'bring2aoi', tmp, args.ref_raster)

        processor.AutomaticBring2AOI(args.input_raster, raster_type=RasterType[args.type], path_out=output_file)


if __name__ == '__main__':
    main()
