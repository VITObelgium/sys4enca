"""Calculate a long-term average of ECMWF drought code or fire severity rating netCDF files."""

import argparse
import glob
import os

import numpy as np
import rasterio


def parse_args():
    parser = argparse.ArgumentParser(prog='average_rasters')
    parser.add_argument('base_dir', help='directory where we can find yearly subdirectories from 1981 until 2020')
    return parser.parse_args()

def main():
    args = parse_args()

    profile = None
    sum = 0
    count = 0
    for year in range(1981, 2021):  # 40 years
        print('Adding rasters for year', year)
        nc_files = glob.glob(os.path.join(args.base_dir, str(year), '*.nc'))
        for f in nc_files:
            with rasterio.open(f) as src:
                data = src.read(1)
                valid = data != src.nodata
                count += valid
                sum += np.where(valid, data, 0)
                if profile is None:
                    profile = src.profile
    lta = np.divide(sum, count, where=count != 0, out=sum)

    with rasterio.open(os.path.join(args.base_dir, 'lta.tif'), 'w', **dict(profile,
                                                                           driver='Gtiff',
                                                                           crs='EPSG:4326',
                                                                           dtype=np.float32)) as ds_lta:
        ds_lta.update_tags(creator='sys4enca', info='long-term average')
        ds_lta.write(lta, 1)


if __name__ == '__main__':
    main()