"""Geographic raster and vector data processing utilities."""

import datetime
import logging
import math
import os
import re
import subprocess
from contextlib import ExitStack
from enum import Enum
from importlib.metadata import version
from os.path import splitext, basename, normpath

import affine
import numpy as np
import geopandas as gpd
import pandas as pd
import rasterio
import rasterio.mask
import shapely.geometry
from osgeo import __version__ as GDALversion
from rasterio.windows import Window
from scipy.ndimage import correlate

from .ecosystem import ECOTYPE, ECO_ID
from .errors import Error

logger = logging.getLogger(__name__)

GEO_ID = 'GEO_ID'  #: Column label for unique region identifier in GeoDataFrames
SHAPE_ID = 'SHAPE_ID'  # SHAPE_ID: integer identifier used when rasterizing vector data

LIST_UNITS = ['German legal metre', 'm', 'metre', 'Meter']
DIC_KNOWN_WKT_STRINGS = {'ETRS_1989_LAEA': 3035,
                         'ETRS89-extended / LAEA Europe': 3035,
                         'ETRS89_ETRS_LAEA': 3035,
                         'ETRS89 / ETRS_LAEA': 3035,
                         'GRS_1980_IUGG_1980_Lambert_Azimuthal_Equal_Area': 3035,
                         'ETRS89_extended_LAEA_Europe': 3035,
                         'World_Mollweide': 54009}   # Note: 54009 is an ESRI identifier so add to ESRI id CONSTANT
ESRI_IDENTIFIER = [54009]
MINIMUM_RESOLUTION = 100.
POLY_MIN_SIZE = 0.1  # size in square metre for minimum area for valid statistics vector to be used
EARTH_CIRCUMFERENCE_METRE = 40075000

_GDAL_FILLNODATA = 'gdal_fillnodata.bat' if os.name == 'nt' else 'gdal_fillnodata.py'
_GDAL_EDIT = 'gdal_edit.bat' if os.name == 'nt' else 'gdal_edit.py'
_GDAL_CALC = 'gdal_calc.bat' if os.name == 'nt' else 'gdal_calc.py'
_GDAL_POLY = 'gdal_polygonize.bat' if os.name == 'nt' else 'gdal_polygonize.py'

SUM = 'sum'
COUNT = 'px_count'

# Map rasterio dtypes to GDAL command line dtypes names:
# Note: For signed int8, we use a workaround to make GDAL do the right thing. g
# Plugging in output type 'Byte -co PIXELTYPE-SIGNEDBYTE' in our external GDAL command lines will only work as long as
# we run them from a single string, with shell=True.
_dtype_map = {
    rasterio.int8: 'Byte -co PIXELTYPE=SIGNEDBYTE',  # TODO change this to 'Int8' from GDAL 3.7 onwards...
    rasterio.uint8: 'Byte',
    rasterio.uint16: 'UInt16',
    rasterio.int16: 'Int16',
    rasterio.uint32: 'Uint32',
    rasterio.int32: 'Int32',
    rasterio.float32: 'Float32',
    rasterio.float64: 'Float64',
    # rasterio.complex_: CInt32,
    # rasterio.complex64: 'CFloat32',
    # rasterio.complex128: 'CFloat64',
    # rasterio.complex_int16: 'Cint16'
}


class RasterType(Enum):
    """When rescaling / reprojecting rasters, we need to take into account the type of data contained in a raster."""

    CATEGORICAL = 0  #: Discrete values (map data), to be resampled with nearest neighbour method
    RELATIVE = 1  #: Quantities expressed in a unit relative to an area (e.g tonne / ha.)
    ABSOLUTE_POINT = 2  #: Point data such as height, slope, wind speed
    #: Quantities contained in pixel, e.g. population, precipitation. Must preserve total volume in geographical area.
    ABSOLUTE_VOLUME = 3


class Metadata(object):
    """This class handles all metadata.

    :param creator: string used as tag in raster files for file creator
    :param seaa_model: string used as tag in raster files for ecosystem service name
    """

    def __init__(self, creator, module):
        """Initialize the project's master metadata."""
        self.module = module
        self.master_tags = {"creator": creator,
                            "Module": self.module,
                            "ENCA-version": version('sys4enca'),
                            "software_raster_processing": "rasterio {} (on GDAL {}); "
                                                          "GDAL binary {}".format(rasterio.__version__,
                                                                                  rasterio.__gdal_version__,
                                                                                  GDALversion),
                            "software_vector_processing": "geopandas {}".format(gpd.__version__),
                            "creation_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        self.raster_tags = {}

    def read_raster_tags(self, path_list):
        """Read tags from a list of input rasters and store them in :attr:`raster_tags`.

        The raster file processing history up to the second child is extracted from all input raster files.

        :param path_list: list of absolute file names for which the metadata is extracted
        """
        self.raster_tags = {}
        counter = 1
        # now we refill with all files used to process the current file
        for path in path_list:
            history_counter = 1
            # get tags of raster file
            with rasterio.open(path, 'r') as src:
                src_tags = src.tags()
                src_tags.update(name=src.name)
            # now we create the current file history line
            if "file_creation" not in src_tags.keys():
                src_tags['file_creation'] = 'unknown'
            if "creator" not in src_tags.keys():
                src_tags['creator'] = 'unknown'
            if "processing" not in src_tags.keys():
                src_tags['processing'] = 'unknown'

            self.raster_tags.update({"input-file{}".format(counter):
                                     "file-name: {}, created on: {}, by: {}, info: {}".format(
                                         basename(src_tags['name']),
                                         src_tags['file_creation'],
                                         src_tags['creator'],
                                         src_tags['processing'])})
            # deal with existing 1st child history lines of input files
            for key in src_tags.keys():
                if re.match("^(input-file\\d*)$", key):
                    # now we add this tag line to the history of the specific file
                    self.raster_tags.update({"input-file{}-history{}".format(counter, history_counter): src_tags[key]})
                    history_counter += 1
            counter += 1

    def update_dataset_tags(self, ds, processing_info, unit, *input_rasters):
        """Update metadata tags of a :class:`rasterio.DatasetWriter`.

        :param ds: output Rasterio dataset.
        :param processing_info: Description the raster contents.
        :param unit: Unit of the raster values
        :param input_rasters: List of input raster to include in the processing history.
        """
        if input_rasters:
            self.read_raster_tags(input_rasters)
        ds.update_tags(**self.prepare_raster_tags(processing_info, unit))

    def prepare_raster_tags(self, processing_info, unit_info='N/A'):
        """Prepare the tags to write out a raster via rasterio.

        :param processing_info: Description the raster contents.
        :param unit_info: Unit of the raster values
        :return: dictionary of metadata tags for rasterio
        """
        # first we fill the main tags for the new file
        out_tags = {"file_creation": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "processing": processing_info,
                    "unit": unit_info}
        # now we add the master tags
        out_tags.update(self.master_tags)
        # now we add the raster tags prepared from the input files if exist
        if self.raster_tags:
            out_tags.update(self.raster_tags)
            # reset raster_tags since it is a one-time use
            self.raster_tags = {}
        return out_tags


class GeoProcessing(object):
    """Handles the geoprocessing of raster and vector files towards given profile of reference raster."""

    def __init__(self, creator, module, temp_dir, ProjectExtentTiff=None, GDAL_verbose=False):
        """Initialize the GeoProcessing object with basic metadata and optional reference raster.

        :param creator: Author name to embed in raster metadata.
        :param module: Processing module name to embed in raster metadata.
        :param temp_dir: Temporary directory to use for intermediate files.
        :param ProjectExtentTiff: Reference raster for projection / extent / resolution.
        :param GDAL_verbose: Print GDAL command line output to stdout or not.

        """
        self.metadata = Metadata(creator, module)
        self.ref_profile = None  # typically the profile of the statistical raster processing file
        self.ref_extent = None  # typically the extent of the statistical raster processing file
        # GDAL terminal output
        if GDAL_verbose:
            self.GDAL_print = None
        else:
            self.GDAL_print = subprocess.DEVNULL

        if ProjectExtentTiff is not None:
            # get key parameter of input file
            param = self._load_profile(ProjectExtentTiff)
            # run checks (that we have valid EPSG number, is projected coordinate system, unit is metre
            # Important: otherwise certain class functions will not work
            # note: if really Geographical projection needed then init the ref_profile and ref_extent manually
            try:
                self._epsg_check(param['epsg'], ProjectExtentTiff)
            except Exception:
                raise ValueError('the provided raster file to init the GeoProcessing object has no valid EPSG.')

            if param['epsg'] in ESRI_IDENTIFIER:
                raise RuntimeError('ESRI projections without EPSG conversion are currently not supported as '
                                   'reference file for the GeoProcessing object.')

            if param['projected'] is False:
                raise Error('Currently the GeoProcessing object only supports reference files with projected '
                            'coordinate systems.')
            if param['unit'] not in LIST_UNITS:
                raise Error('Currently the GeoProcessing object only supports reference files with projected '
                            'coordinate systems in the following units: ' + ', '.join(LIST_UNITS))

            if param['overwrite_s_srs'] is True:
                param['profile'].update(crs=rasterio.crs.CRS.from_epsg(param['epsg']))

            self.ref_profile = param['profile']
            self.ref_extent = param['bbox']  # tuple: (lower left x, lower left y, upper right x, upper right y)

        self.reporting_profile = None  # mainly the profile of the reporting raster processing file
        self.reporting_extent = None
        self.temp_dir = temp_dir
        self.src_parameters = {}   # all the parameters of the raster file which has to be geoprocessed

    def _check(self):
        """Check if profile and extent for reference file exist."""
        if self.ref_profile is None:
            raise ValueError("The AccoRD object was not correctly initialized. "
                             "The rasterio profile for the reference file is missing")
        if self.ref_extent is None:
            raise ValueError("The AccoRD object was not correctly initialized. "
                             "The rasterio extent for the reference file is missing")

    def _check2(self):
        """Check if profile and extent of reporting shapefile exist."""
        if self.reporting_profile is None:
            raise ValueError("The AccoRD object was not completely initialized. "
                             "The rasterio profile for the reporting raster file is missing")
        if self.reporting_extent is None:
            raise ValueError("The AccoRD object was not completely initialized. "
                             "The rasterio BoundingBox for the reporting raster file is missing")

    def _check3(self, filename):
        """Check if we have projected raster AND the projection unit is in the list we accept.

        :param filename: file name to raster file to which the status checks are applied
        """
        if self.src_parameters['projected'] is False:
            raise Error(f'Currently the tool only supports raster maps with absolute volume-based data '
                        f' ({filename}) with a projected coordinate system.')
        if self.src_parameters['unit'] not in LIST_UNITS:
            raise Error(f'Currently the tool only supports raster maps with absolute volume-based data'
                        f' ({filename}) with the following projection units: ' + ', '.join(LIST_UNITS))

    def _epsg_check(self, epsg, filename):
        """Check for the main current limitation of the GeoProcessing class - processing of datasets with valid EPSG.

        :param epsg: epsg code of the projection of the raster dataset (either int value, None or string 'no_epsg_code')
        :param filename: file name to raster file to which the status checks are applied
        """
        if (epsg == 'no_epsg_code') or (epsg is None):
            raise Error(f'The raster map {filename} does not have a valid EPSG projection or know WKT-string. '
                        f'Please adapt your raster map or insert a EPSG number in DIC_KNOWN_WKT_STRINGS dictionary.')

    def _full_pre_check(self, path_in, clean_src=False):
        """Execute the _check() function PLUS make sure the key parameter of the to process file are loaded.

        :param path_in: raster path to file for which is checked that all profile info is available
        :param clean_src: boolean to indicate if profile parameters are fresh reloaded or existing are used
        """
        self._check()
        # delete src info if needed
        if clean_src:
            self.src_parameters = {}
        # check if all parameters of input files are existing - when run in stand-alone mode
        if not self.src_parameters:
            self.src_parameters = self._load_profile(path_in)
        # check for main current geoprocessing limitation
        self._epsg_check(self.src_parameters['epsg'], path_in)

    def vectorize(self, raster_path, root_out):
        """Wrap `gdal_polygonize`.

        :param raster_path: absolute path to raster file which is vectorized
        :param root_out: path to base folder in which the vectorized file is saved
        :return: absolute path to vectorized file
        """
        # create output file name
        out_path = os.path.join(root_out, 'vector_{}.shp'.format(splitext(basename(raster_path))[0]))

        # setup cmd command
        cmd = '{} -8 "{}" "{}" vectorized ID'.format(_GDAL_POLY, normpath(raster_path), out_path)

        if not os.path.exists(out_path):
            try:
                subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not polygonize needed raster file: {e}')
            else:
                logger.debug('* Raster file (%s) was successfully polygonized.', raster_path)
        else:
            logger.debug('* Vector file %s already exists, skipping.', out_path)
            pass
        return out_path

    def rasterize_burn(self, path_in: str, path_out: str, nodata_value=0,
                       burn_value=1, dtype='Byte', mode='statistical'):
        """Rasterize a given geopandas dataframe with a specified `column_name`.

        :param path_in: directory for input file.
        :param path_out: directory for output file.
        :param nodata_value: nodata value for the raster.
        :param burn_value: which value to burn
        :param dtype: dtype as a string
        :param mode: if to rasterize to the statistical or reporting extent and resolution (statistical, reporting)
        :return: None
        """
        self._check()

        if mode == 'statistical':
            pextent = self.ref_extent
            res = (float(self.ref_profile['transform'].a), float(abs(self.ref_profile['transform'].e)))
            out_crs = self.ref_profile['crs'].to_epsg()
        elif mode == 'reporting':
            self._check2()
            pextent = self.reporting_extent
            res = (float(self.reporting_profile['transform'].a), float(abs(self.reporting_profile['transform'].e)))
            out_crs = self.reporting_profile['crs'].to_epsg()
        else:
            raise RuntimeError('this mode was not forseen in rasterization function.')

        if burn_value == nodata_value:
            init_value = 1
        else:
            init_value = nodata_value
        cmd = 'gdal_rasterize -l "{}" -init {} -burn {} -at -a_nodata {} -co COMPRESS=DEFLATE -co TILED=YES -co INTERLEAVE=BAND ' \
              '-ot {} -te {} {} {} {} -tr {} {} -a_SRS "EPSG:{}" "{}" "{}"'.format(
                    splitext(basename(path_in))[0],
                    init_value,
                    burn_value,
                    nodata_value,
                    dtype,
                    pextent.left,
                    pextent.bottom,
                    pextent.right,
                    pextent.top,
                    res[0],
                    res[1],
                    out_crs,
                    path_in,
                    path_out)

        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd, shell=True)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not rasterize needed vector file: {e}')
            else:
                tags = self.metadata.prepare_raster_tags(
                    'Vector file was rasterized to reference file extent.', '')
                with rasterio.open(path_out, 'r+') as dst:
                    dst.update_tags(**tags)
                logger.debug('* Vector file was successfully rasterized.')
        else:
            logger.debug('* Rasterized file %s already exists, skipping.', path_out)
            pass

    def rasterize(self, gdb: gpd.GeoDataFrame, gdb_column_name: str, path_out: str, nodata_value=0, dtype='Float32',
                  guess_dtype=False, mode='statistical'):
        """Rasterize a given geopandas dataframe with a specified `column_name`.

        :param gdb: GeoDataFrame to rasterize.
        :param gdb_column_name: column to rasterize.
        :param path_out: directory for output file.
        :param nodata_value: nodata value for the raster.
        :param dtype: dtype as a string
        :param guess_dtype: guess dtype from the data (only for integer data)
        :param mode: if to rasterize to the statistical or reporting extent and resolution (statistical, reporting)
        :return: None
        """
        self._check()
        # check if we have nodata values in the to rasterize vectors (if columnname None this is not relevant)

        if nodata_value in gdb[gdb_column_name].values:
            logger.warning('rasterizing shape: nodata value %s appears in column we want to rasterize.', nodata_value)

        # guess the dtype
        if guess_dtype:
            max_id = gdb[gdb_column_name].max()
            if max_id <= 255:
                dtype = 'Byte'
            else:
                dtype = 'UInt16'

        # write the geodatabase to file
        temp_out = os.path.join(self.temp_dir, 'vector_{}.gpkg'.format(splitext(basename(path_out))[0]))

        if mode == 'statistical':
            pextent = self.ref_extent
            res = (float(self.ref_profile['transform'].a), float(abs(self.ref_profile['transform'].e)))
            out_crs = self.ref_profile['crs'].to_epsg()
        elif mode == 'reporting':
            self._check2()
            pextent = self.reporting_extent
            res = (float(self.reporting_profile['transform'].a), float(abs(self.reporting_profile['transform'].e)))
            out_crs = self.reporting_profile['crs'].to_epsg()
        else:
            raise RuntimeError('this mode was not forseen in rasterization function.')

        cmd = 'gdal_rasterize -a "{}" -l "{}" -a_nodata {} -co COMPRESS=DEFLATE -co TILED=YES -co INTERLEAVE=BAND ' \
              '-ot {} -te {} {} {} {} -tr {} {} -a_SRS "EPSG:{}" ' \
              '"{}" "{}"'.format(gdb_column_name,
                                 splitext(basename(temp_out))[0],
                                 nodata_value,
                                 dtype,
                                 pextent.left,
                                 pextent.bottom,
                                 pextent.right,
                                 pextent.top,
                                 res[0],
                                 res[1],
                                 out_crs,
                                 temp_out,
                                 path_out)

        if not os.path.exists(path_out):
            try:
                gdb[[gdb_column_name, 'geometry']].to_file(temp_out, driver='GPKG')
                subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not rasterize needed vector file: {e}')
            else:
                tags = self.metadata.prepare_raster_tags(
                    'Vector file was rasterized to reference file extent.', '')
                with rasterio.open(path_out, 'r+') as dst:
                    dst.update_tags(**tags)
                logger.debug('* Vector file was successfully rasterized.')
            finally:
                # remove temp files
                if os.path.exists(temp_out):
                    os.remove(temp_out)
        else:
            logger.debug('* Rasterized file %s already exists, skipping.', path_out)
            pass

    def FillHoles(self, path_in, path_out, maxdistance=25, smooth=0):
        """Fill nodata holes in a raster file.

        :param path_in: input filename
        :param path_out: output filename
        :param maxdistance: distance in pixels in which valid pixels are searched
        :param smooth: smoothing iterations
        """
        path_temp = os.path.join(self.temp_dir, 'fillHoles_temp.tif')

        cmd1 = '{} -md {} -si {} "{}" "{}"'.format(_GDAL_FILLNODATA, maxdistance, smooth, path_in, path_temp)

        # now we bring back the nodata value in the file
        with rasterio.open(path_in) as src:
            nodata = src.nodata
        cmd2 = '{} -a_nodata {} "{}"'.format(_GDAL_EDIT, nodata, path_temp)

        # and compress it
        cmd3 = 'gdal_translate -co COMPRESS=DEFLATE -co TILED=YES -co INTERLEAVE=BAND ' \
               '"{}" "{}"'.format(path_temp, path_out)

        # run
        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd1, shell=True, stdout=self.GDAL_print)
                subprocess.check_call(cmd2, shell=True, stdout=self.GDAL_print)
                subprocess.check_call(cmd3, shell=True, stdout=self.GDAL_print)
            except subprocess.CalledProcessError as e:
                raise OSError(f'Could not fill the holes in the input raster: {e}')
            else:
                logger.debug('* Raster file (%s) was successfully filled.', path_in)
            finally:
                # remove temp files
                if os.path.exists(path_temp):
                    os.remove(path_temp)
        else:
            logger.debug('* File with filled data (%s) already exists, skipping.', path_out)
            pass

    def pixel_area_m2(self):
        """Return the area in square meter of one pixel from the AccoRD reference file."""
        self._check()
        if self.ref_profile['crs'].is_projected:
            return pixel_area(self.ref_profile['crs'], self.ref_profile['transform'])
        else:
            return None

    def _load_profile(self, raster_path):
        """Extract key variables of the given raster file.

        :param raster_path: absolute file path to raster file for which to extract the profile
        :return: dictionary containing the key raster profile parameters
        """
        dFile = {}
        dFile['overwrite_s_srs'] = False  # flag that EPSG code of raster file was not valid and extracted from WKT
        try:
            with rasterio.open(raster_path) as src:
                # EPSG
                if src.crs.is_epsg_code:
                    dFile['epsg'] = src.crs.to_epsg()
                else:
                    # we have no EPSG or the VRT problem which shows only the WKT instead of the EPSG code
                    dFile['epsg'] = 'no_epsg_code'
                    # check if we have a know wkt and can overwrite to the right epsg integer number
                    for key, value in DIC_KNOWN_WKT_STRINGS.items():
                        if key in src.crs.wkt:
                            dFile['epsg'] = value
                            # flag that s_srs is overwritten
                            dFile['overwrite_s_srs'] = True
                            break
                # raster extent
                dFile['bbox'] = src.bounds
                # resolution tuple
                dFile['res'] = src.res
                # datatype
                dFile['dtype'] = src.dtypes[0]
                # raster profile
                dFile['profile'] = src.profile
                # raster height and width
                dFile['width'] = src.width
                dFile['height'] = src.height
                # nodata value
                dFile['nodata'] = src.nodata
                # dataset name
                dFile['name'] = src.name
                # dataset file tags (no band tags)
                dFile['tags'] = src.tags()
                # pixel area in square metre
                if src.crs.is_projected:
                    dFile['px_area_m2'] = pixel_area(src.crs, src.transform)
                    _, dFile['unit_factor'] = src.crs.linear_units_factor
                else:
                    dFile['px_area_m2'] = None
                    dFile['unit_factor'] = None
                # projected and units
                dFile['projected'] = src.crs.is_projected
                dFile['geographic'] = src.crs.is_geographic
                dFile['unit'] = src.crs.linear_units
        except rasterio.errors.RasterioIOError as e:
            raise Error(f'Failed to open raster file "{raster_path}": {e}')
        return dFile

    def _check_raster_processing_needed(self):
        """Check if profile of a given raster matches the reference profile and if any processing is needed.

        :return: True if profiles match, False if not.
        """
        if not self.src_parameters:
            raise ValueError('the _check_raster_processing_needed function can be only called when the profile '
                             'of the input raster file is loaded (use _load_profile() function).')

        if (self.src_parameters['epsg'] == self.ref_profile['crs'].to_epsg()) \
                and (self.src_parameters['bbox'] == self.ref_extent) \
                and (self.src_parameters['res'] == (float(self.ref_profile['transform'].a),
                                                    float(abs(self.ref_profile['transform'].e)))):
            return True
        else:
            return False

    def AutomaticBring2AOI(self, path_in, raster_type=RasterType.CATEGORICAL, wOT=None, path_out=None,
                           secure_run=False):
        """Wrap :func:`Bring2AOI` to adjust a raster file if needed. Optimal settings are picked automatically.

        Returns the absolute filename of the adapted raster file, or the original filename if no changes where needed.

        :param path_in: input file path (absolute) of raster file to be processed to reference file specifications
        :param raster_type: type of raster data used to define the geoprocessing method
        :param wOT: overwriting the input raster data type
        :param path_out: output filename (a filename is derived from the input filename if not provided)
        :param secure_run: self.src_parameters is reset before function execution
        :return: the file path of the adapted file if changes were needed, else the input file name
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # test if we even have to process the input raster file
        if self._check_raster_processing_needed():
            logger.debug('* raster file {} OK'.format(os.path.basename(path_in)))
            # reset the src_parameter before close to definitely clean (One Time Use)
            self.src_parameters = {}
            return path_in
        else:
            if path_out is None:
                # now we have to prepare a generic output raster file name
                path_out = os.path.join(self.temp_dir,
                                        '{}_{}m_EPSG{}.tif'.format(splitext(basename(path_in))[0],
                                                                   int(self.ref_profile['transform'].a),
                                                                   self.ref_profile['crs'].to_epsg()))
            # run the standard Bring2AOI function
            self.Bring2AOI(path_in, path_out, raster_type=raster_type, wOT=wOT)
            return path_out

    def Bring2AOI(self, path_in, path_out, raster_type=RasterType.CATEGORICAL, wOT=None, secure_run=False):
        """Automatically transform input file to reference file (extent, resolution, projection).

        Compares input and reference projection parameters to select the optimal transformation method.

        :param path_in: input file path (absolute) of raster file to be processed to reference file specifications
        :param path_out: output file path (absolute) of raster file
        :param raster_type: type of raster data used to define the geoprocessing method
        :param wOT: overwriting the input raster data type
        :param secure_run: self.src_parameters is reset before function execution
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # make sure the output folder is existing
        dir_out = os.path.dirname(path_out)
        if dir_out:  # if path_out is just a filename without directory, dirname() returns ''.
            os.makedirs(dir_out, exist_ok=True)

        # overwrite the output dataformat if not given
        if wOT is None:
            wOT = _dtype_map[self.src_parameters['dtype']]

        # decision tree for geoprocessing method based on:
        # processing case: crop, resample, or warp
        # resolution change: same, up-sampling, or down-sampling
        # data type: discrete data, continuous_relative unit, continuous_absolute_point, continuous_absolute_volume
        res_case, processing_case = self._get_case_parameters()

        # now get the name of correct GeoProcessing function to run and settings
        processing_mode, resampling_mode = self._query_raster_processing_table(res_case, processing_case, raster_type)

        # apply the decision
        logger.warning(f'- raster ({os.path.basename(path_in)}) will be adjusted ({processing_mode}) '
                       f'with {resampling_mode} filter.')
        if processing_mode == 'Crop2AOI':
            self.Crop2AOI(path_in, path_out, wResampling=resampling_mode, wOT=wOT)
        elif processing_mode == 'Translate2AOI':
            self.Translate2AOI(path_in, path_out, wResampling=resampling_mode, wOT=wOT, mode=res_case)
        elif processing_mode == 'Warp2AOI':
            self.Warp2AOI(path_in, path_out, wResampling=resampling_mode, wOT=wOT)
        elif processing_mode == 'VolumeWarp2AOI':
            self.VolumeWarp2AOI(path_in, path_out, wOT=wOT, oversampling_factor=10)
        else:
            raise RuntimeError(f'the processing mode {processing_mode} is currently not implemented. '
                               f'Adapt your input dataset manually to full-fill the tool input data '
                               f'specifications (see manual)')

    def _query_raster_processing_table(self, res_case, processing_case, raster_type):
        """Evaluate which GeoProcessing function and settings have to be used based on given parameters.

        :param res_case: resampling strategy between input raster $ reference raster (same, up-sampling, down-sampling)
        :param processing_case: processing type between input raster & reference raster (crop, resample, warp)
        :param raster_type: type of the raster content (categorical, relative, absolute_point, absolute_volume)
        :return: tuple, given the processing_mode (crop, translate, warp) and resampling_mode
        """
        # possible processing_modes: Crop2AOI, Translate2AOI, Warp2AOI, VolumeWarp2AOI
        # possible resampling_modes: nearest, sum, bilinear, mode, average, 3-step
        decision_tree = {
            'crop': {RasterType.CATEGORICAL: {'same': ('Crop2AOI', 'nearest')},
                     RasterType.RELATIVE: {'same': ('Crop2AOI', 'bilinear')},
                     RasterType.ABSOLUTE_POINT: {'same': ('Crop2AOI', 'nearest')},
                     RasterType.ABSOLUTE_VOLUME: {'same': ('Crop2AOI', 'nearest')}},
            'resample': {RasterType.CATEGORICAL: {'up-sampling': ('Translate2AOI', 'nearest'),
                                                  'down-sampling': ('Translate2AOI', 'mode')},
                         RasterType.RELATIVE: {'up-sampling': ('Translate2AOI', 'bilinear'),
                                               'down-sampling': ('Translate2AOI', 'average')},
                         RasterType.ABSOLUTE_POINT: {'up-sampling': ('Translate2AOI', 'bilinear'),
                                                     'down-sampling': ('Translate2AOI', 'average')},
                         RasterType.ABSOLUTE_VOLUME: {'up-sampling': ('VolumeWarp2AOI', '3-step'),
                                                      'down-sampling': ('VolumeWarp2AOI', '3-step')}},
            'warp': {RasterType.CATEGORICAL: {'same': ('Warp2AOI', 'nearest'),
                                              'up-sampling': ('Warp2AOI', 'nearest'),
                                              'down-sampling': ('Warp2AOI', 'mode')},
                     RasterType.RELATIVE: {'same': ('Warp2AOI', 'near'),
                                           'up-sampling': ('Warp2AOI', 'bilinear'),
                                           'down-sampling': ('Warp2AOI', 'average')},
                     RasterType.ABSOLUTE_POINT: {'same': ('Warp2AOI', 'near'),
                                                 'up-sampling': ('Warp2AOI', 'bilinear'),
                                                 'down-sampling': ('Warp2AOI', 'average')},
                     RasterType.ABSOLUTE_VOLUME: {'same': ('VolumeWarp2AOI', '3-step'),
                                                  'up-sampling': ('VolumeWarp2AOI', '3-step'),
                                                  'down-sampling': ('VolumeWarp2AOI', '3-step')}}}
        try:
            processing_mode, resampling_mode = decision_tree[processing_case][raster_type][res_case]
        except KeyError:
            raise ValueError(f'the given data ({processing_case, raster_type, res_case}) is not in the GeoProcessing '
                             f'decision tree to be evaluated. No geoprocessing mode and resampling mode can be '
                             f'determined.')

        return processing_mode, resampling_mode

    def _get_case_parameters(self):
        """Check src and reference dataset spatial resolution and projection to select correct geoprocessing method.

        :return: tuple, given the resampling strategy and processing type between input & reference raster file
        """
        # First, check resolution change
        if self.src_parameters['projected']:
            if self.src_parameters['unit_factor'] != 1:
                # multiply the resolution with unit factor to get resolution in metre
                src_res = (self.src_parameters['res'][0] * self.src_parameters['unit_factor'],
                           self.src_parameters['res'][1] * self.src_parameters['unit_factor'])
            else:
                src_res = self.src_parameters['res']
        elif self.src_parameters['geographic']:
            # way more complicated to get resolution in metre (we estimate for center of raster)
            center_lat = self.src_parameters['bbox'].top - \
                         (self.src_parameters['height'] / 2 * self.src_parameters['res'][1])
            src_res = (EARTH_CIRCUMFERENCE_METRE * np.cos(np.radians(center_lat)) / 360 * self.src_parameters['res'][0],
                       np.round(EARTH_CIRCUMFERENCE_METRE / 360, 0) * self.src_parameters['res'][1])
        else:
            raise RuntimeError('Input dataset neither projected nor geographic coordinate system')

        if src_res == (float(self.ref_profile['transform'].a),
                       float(abs(self.ref_profile['transform'].e))):
            res_case = 'same'
        elif (src_res[0] * src_res[1]) > (float(self.ref_profile['transform'].a) *
                                          float(abs(self.ref_profile['transform'].e))):
            res_case = 'up-sampling'  # higher resolution of the image is needed to get reference image specs
        else:
            res_case = 'down-sampling'  # lower resolution of the image is needed to get reference image specs

        # Second, check processing method
        if self.src_parameters['epsg'] == self.ref_profile['crs'].to_epsg():
            if res_case == 'same':
                processing_case = 'crop'
            else:
                processing_case = 'resample'
        else:
            processing_case = 'warp'

        return res_case, processing_case

    def _shift_check(self, path_in):
        """Check if we need to regrid due to sub-pixel shift issues in GDAL_translate with nearest neighbor filter.

        :param path_in: path to check if we need regridding
        :return: boolean if we need a shift
        """
        # check if ref_profile is there
        self._check()
        # load file parameters (needed)
        src_parameters = self._load_profile(path_in)
        # now we check if key parameters are OK
        if (src_parameters['epsg'] == self.ref_profile['crs'].to_epsg()) \
                and (src_parameters['bbox'] == self.ref_extent) \
                and (src_parameters['res'] == (float(self.ref_profile['transform'].a),
                                               float(abs(self.ref_profile['transform'].e)))):
            # all worked perfectly - nothing to do
            return False
        else:
            # now we check if we have a shift or something else
            if (src_parameters['epsg'] == self.ref_profile['crs'].to_epsg()) \
                    and (src_parameters['res'] == (float(self.ref_profile['transform'].a),
                                                   float(abs(self.ref_profile['transform'].e))))\
                    and (src_parameters['height'] == self.ref_profile['height']) \
                    and (src_parameters['width'] == self.ref_profile['width']):
                # great something we can easy solve since it is just a sub-pixel shift
                return True
            else:
                raise ValueError(f'Processed dataset ({path_in}) does not match requested specification. Unknown error.'
                                 f' Please check the used geoprocessing function and debug.')

    def _pixel_shift_adjustment(self, path_in):
        """Adjust a sub-pixel shift in a raster file to the proper bounding box needed.

        Note: current implementation does not support rotated datasets.

        :param path_in: path to raster file which is checked.
        """
        # check if we need adjustment
        if self._shift_check(path_in):
            cmd = '{} -a_ullr {} {} {} {} "{}"'.format(_GDAL_EDIT,
                                                       self.ref_extent.left,
                                                       self.ref_extent.top,
                                                       self.ref_extent.right,
                                                       self.ref_extent.bottom,
                                                       path_in)
            try:
                subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
                logger.warning('- sub-pixel shift was detected and resolved.')
            except subprocess.CalledProcessError as e:
                raise OSError(
                    'Could not resolve sub-pixel shift in raster file ({}) : {}'.format(os.path.basename(path_in), e))
        else:
            # nothing to do
            return

    def VolumeWarp2AOI(self, path_in, path_out, wOT='Float64', oversampling_factor=10, secure_run=False):
        """Warp rasters with absolute volume based data.

        Note: still based on gdalwarp but in a 3-step approach which works for up-sampling and down-sampling
        IMPORTANT: since gdal_translate has no "sum" resample method we also use this approach for translate cases

        :param path_in: input file path (absolute) of raster file to be processed to reference file specifications
        :param path_out: output file path (absolute) of raster file
        :param wOT: overwriting the input raster data type
        :param oversampling_factor: set the oversampling rate for the warp
        :param secure_run: self.src_parameters is reset before function execution (mainly used when called stand-alone)
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)
        # yet another check - if we have an absolute_volume datatype then we need a check that we have projected
        # coordinate system and a known unit --> otherwise we got issues with the pixel area needed
        self._check3(path_in)
        # TODO: implement Volume-based processing for raster with geographic coordinate systems
        # create a raster in src file resolution and extent which holds the px area as value for each pixel
        # also warp this file with oversampling --> so we know for each warped and oversampled pixel the
        # original value and original pixel area --> each pixel value can now correctly scaled
        # TODO: implement volume-based processing for projected raster with non-metre units
        # first translate src dataset into raster with metre unit in original resolution and scale the values

        # resolve ESRI / EPSG issue
        if self.src_parameters['epsg'] in ESRI_IDENTIFIER:
            s_srs_string = 'ESRI:{}'.format(self.src_parameters['epsg'])
        else:
            s_srs_string = 'EPSG:{}'.format(self.src_parameters['epsg'])

        # resolve potential issue in wOT
        if wOT in _dtype_map.keys():
            wOT = _dtype_map[wOT]

        # 1. warp to target EPSG with oversampled resolution (nearest neighbor) (crop to ref file with buffer of 10 px)
        buffer_x = 10 * self.ref_profile['transform'].a
        buffer_y = 10 * abs(self.ref_profile['transform'].e)

        path_temp1 = os.path.join(self.temp_dir, 'VolumeData_helper_file1.tif')
        cmd1 = 'gdalwarp --config GDAL_CACHEMAX 256 -s_srs "{}" -t_srs "EPSG:{}" -te {} {} {} {} -tr {} {} ' \
               '-r {} -et 0 -ot {} -ovr None ' \
               '-wo SAMPLE_STEPS=50 -wo SOURCE_EXTRA=5 -wo SAMPLE_GRID=YES ' \
               '-co COMPRESS=DEFLATE -co INTERLEAVE=BAND -co BIGTIFF=YES -multi -co TILED=YES ' \
               '-overwrite "{}" "{}"'.format(s_srs_string,
                                             self.ref_profile['crs'].to_epsg(),
                                             self.ref_extent.left - buffer_x,
                                             self.ref_extent.bottom - buffer_y,
                                             self.ref_extent.right + buffer_x,
                                             self.ref_extent.top + buffer_y,
                                             float(self.ref_profile['transform'].a) / oversampling_factor,
                                             float(abs(self.ref_profile['transform'].e)) / oversampling_factor,
                                             'near',
                                             wOT,
                                             path_in,
                                             path_temp1)

        # 2. adjust raster values to oversampling rate (gdal_calc)
        path_temp2 = os.path.join(self.temp_dir, 'VolumeData_helper_file2.tif')
        cmd2 = '{} -A "{}" --outfile="{}" --calc="A/({}*{})" --overwrite --quiet ' \
               '--co="INTERLEAVE=BAND" --co="COMPRESS=DEFLATE" ' \
               '--co="TILED=YES"'.format(_GDAL_CALC, path_temp1, path_temp2,
                                         self.src_parameters['res'][0] / (float(self.ref_profile['transform'].a) /
                                                                          oversampling_factor),
                                         self.src_parameters['res'][1] / (float(abs(self.ref_profile['transform'].e)) /
                                                                          oversampling_factor))

        # 3. use 'sum' resampling mode in warp method to get final results (gdalwarp)
        cmd3 = 'gdalwarp --config GDAL_CACHEMAX 256 -t_srs "EPSG:{}" -te {} {} {} {} -tr {} {} ' \
               '-r {} -et 0 -ot {} -ovr None ' \
               '-wo SAMPLE_STEPS=50 -wo SOURCE_EXTRA=5 -wo SAMPLE_GRID=YES ' \
               '-co COMPRESS=DEFLATE -co INTERLEAVE=BAND -co BIGTIFF=YES -multi -co TILED=YES ' \
               '-overwrite "{}" "{}"'.format(self.ref_profile['crs'].to_epsg(),
                                             self.ref_extent.left,
                                             self.ref_extent.bottom,
                                             self.ref_extent.right,
                                             self.ref_extent.top,
                                             float(self.ref_profile['transform'].a),
                                             float(abs(self.ref_profile['transform'].e)),
                                             'sum',
                                             wOT,
                                             path_temp2,
                                             path_out)

        log_message = '* file {} was warped to AOI and resampled to target resolution'.format(os.path.basename(path_in))

        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd1, shell=True, stdout=self.GDAL_print)
                subprocess.check_call(cmd2, shell=True, stdout=self.GDAL_print)
                subprocess.check_call(cmd3, shell=True, stdout=self.GDAL_print)
                # now we run a check if all processing was successful or if we have a sub-pixel shift to resolve
                self._pixel_shift_adjustment(path_out)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                # delete file if it is partly processed
                if os.path.exists(path_out):
                    os.remove(path_out)
                raise OSError('Could not warp the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
                if os.path.exists(path_temp1):
                    os.remove(path_temp1)
                if os.path.exists(path_temp2):
                    os.remove(path_temp2)
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def Crop2AOI(self, path_in, path_out, wResampling='nearest', wOT='Float64', secure_run=False):
        """Crop raster to AOI when in same coordinate system and same resolution. No checks are done.

        Note: Nodata value is kept but dtype of output can be adjusted.

        :param path_in: input file path (absolute) of raster file to be processed to reference file specifications
        :param path_out: output file path (absolute) of raster file
        :param wResampling: resampling method
        :param wOT: overwriting the input raster data type
        :param secure_run: self.src_parameters is reset before function execution (mainly used when called stand-alone)
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # first resolve gdal_translate and gdalwarp language issue
        if wResampling == 'near':
            wResampling = 'nearest'
        # resolve potential issue in wOT
        if wOT in _dtype_map.keys():
            wOT = _dtype_map[wOT]

        # Note: the resampling method was added to allow switches to different projection units even when theoretically
        #       no resampling is done (e.g. image in projection with kilometer unit and resolution 1 is same as
        #       reference in metre projection and resolution of 1000)
        if self.src_parameters['overwrite_s_srs']:
            # that case is needed since when input raster had no valid EPSG then gdal command gives wired results
            cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                  '-co INTERLEAVE=BAND -ot {} -projwin {} {} {} {} -projwin_srs "EPSG:{}" -a_srs "EPSG:{}" ' \
                  '-r {} -tr {} {} "{}" "{}"'.format(wOT,
                                                     self.ref_extent.left,
                                                     self.ref_extent.top,
                                                     self.ref_extent.right,
                                                     self.ref_extent.bottom,
                                                     self.ref_profile['crs'].to_epsg(),
                                                     self.ref_profile['crs'].to_epsg(),
                                                     wResampling,
                                                     float(self.ref_profile['transform'].a),
                                                     float(abs(self.ref_profile['transform'].e)),
                                                     path_in,
                                                     path_out)
        else:
            cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                  '-co INTERLEAVE=BAND -ot {} -projwin {} {} {} {} -r {} -tr {} {} ' \
                  '"{}" "{}"'.format(wOT,
                                     self.ref_extent.left,
                                     self.ref_extent.top,
                                     self.ref_extent.right,
                                     self.ref_extent.bottom,
                                     wResampling,
                                     float(self.ref_profile['transform'].a),
                                     float(abs(self.ref_profile['transform'].e)),
                                     path_in,
                                     path_out)
        log_message = '* file {} was successfully cropped to AOI'.format(os.path.basename(path_in))

        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
                # now we run a check if all processing was successful or if we have a sub-pixel shift to resolve
                self._pixel_shift_adjustment(path_out)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                # delete file if it is partly processed
                if os.path.exists(path_out):
                    os.remove(path_out)
                raise OSError(
                    'Could not crop the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def Translate2AOI(self, path_in, path_out, wResampling='nearest', wOT='Float64', secure_run=False,
                      mode=None):
        """Translate raster (resampling and cropping) to AOI when in same coordinate system.

        Note: Nodata value is kept but dtype of output can be adjusted.

        :param path_in: input file path (absolute) of raster file to be processed to reference file specifications
        :param path_out: output file path (absolute) of raster file
        :param wResampling: resampling method
        :param wOT: overwriting the input raster data type
        :param secure_run: self.src_parameters is reset before function execution (mainly used when called stand-alone)
        :param mode: resampling strategy between input & reference raster (due to gdal_translate sub-pixel shifts we
                     have to distinguish between up & down sampling) (default: None --> automatic detection)
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # first resolve gdal_translate and gdalwarp language issue
        if wResampling == 'near':
            wResampling = 'nearest'
        # resolve potential issue in wOT
        if wOT in _dtype_map.keys():
            wOT = _dtype_map[wOT]

        # due to gdal_translate sub-pixel shifts we have to distinguish between up & down sampling
        if mode is None:
            mode, _ = self._get_case_parameters()
        if mode == 'same':
            # that is a Crop case
            self.Crop2AOI(path_in, path_out, wOT=wOT, secure_run=False)
            return

        # now the different approaches
        if mode == 'up-sampling':
            # use 2-step approach since gdal_translate first crop and then resample which leds to shifts
            # get the index of UL and LR corner for reference file in existing input file
            UL_row, UL_column = coord_2_index(self.ref_extent.left, self.ref_extent.top,
                                              self.src_parameters['bbox'].left, self.src_parameters['bbox'].top,
                                              self.src_parameters['res'])
            LR_row, LR_column = coord_2_index(self.ref_extent.right, self.ref_extent.bottom,
                                              self.src_parameters['bbox'].left, self.src_parameters['bbox'].top,
                                              self.src_parameters['res'])
            # add buffer of 1 pixel in input file resolution
            if UL_column >= 1:
                UL_column -= 1
            if UL_row >= 1:
                UL_row -= 1
            if LR_column < self.src_parameters['width']:
                LR_column += 1
            if LR_row < self.src_parameters['height']:
                LR_row += 1

            # create CMD for 1. step - resampling a bigger area as needed
            path_temp = os.path.join(self.temp_dir, 'translation_helper_file.tif')
            # again the check is needed if we have a raster file without valid EPSG code
            if self.src_parameters['overwrite_s_srs']:
                # that case is needed since when input raster had no valid EPSG then gdal command gives wired results
                cmd_pre = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                          '-a_srs "EPSG:{}" -co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -srcwin {} {} {} {} ' \
                          '"{}" "{}"'.format(self.ref_profile['crs'].to_epsg(),
                                             wOT,
                                             float(self.ref_profile['transform'].a),
                                             float(abs(self.ref_profile['transform'].e)),
                                             wResampling,
                                             UL_column,
                                             UL_row,
                                             LR_column - UL_column,
                                             LR_row - UL_row,
                                             path_in,
                                             path_temp)
            else:
                cmd_pre = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                          '-co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -srcwin {} {} {} {} ' \
                          '"{}" "{}"'.format(wOT,
                                             float(self.ref_profile['transform'].a),
                                             float(abs(self.ref_profile['transform'].e)),
                                             wResampling,
                                             UL_column,
                                             UL_row,
                                             LR_column - UL_column,
                                             LR_row - UL_row,
                                             path_in,
                                             path_temp)

            # create CMD for 2. step - the cut to the extent
            cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES -co INTERLEAVE=BAND ' \
                  '-ot {} -projwin {} {} {} {} ' \
                  '"{}" "{}"'.format(wOT,
                                     self.ref_extent.left,
                                     self.ref_extent.top,
                                     self.ref_extent.right,
                                     self.ref_extent.bottom,
                                     path_temp,
                                     path_out)
            log_message = '* file {} was cropped to AOI & resampled to target resolution in 2-step approach'.format(
                os.path.basename(path_in))
        elif mode == 'down-sampling':
            cmd_pre = None
            path_temp = os.path.join(self.temp_dir, 'translation_helper_file.tif')

            # again the check is needed if we have a raster file without valid EPSG code
            if self.src_parameters['overwrite_s_srs']:
                # that case is needed since when input raster had no valid EPSG then gdal command gives wired results
                cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                      '-a_srs "EPSG:{}" -co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -projwin {} {} {} {} ' \
                      '"{}" "{}"'.format(self.ref_profile['crs'].to_epsg(),
                                         wOT,
                                         float(self.ref_profile['transform'].a),
                                         float(abs(self.ref_profile['transform'].e)),
                                         wResampling,
                                         self.ref_extent.left,
                                         self.ref_extent.top,
                                         self.ref_extent.right,
                                         self.ref_extent.bottom,
                                         path_in,
                                         path_out)
            else:
                cmd = 'gdal_translate --config GDAL_CACHEMAX 256 -co COMPRESS=DEFLATE -co TILED=YES ' \
                      '-co INTERLEAVE=BAND -ot {} -tr {} {} -r {} -projwin {} {} {} {} ' \
                      '"{}" "{}"'.format(wOT,
                                         float(self.ref_profile['transform'].a),
                                         float(abs(self.ref_profile['transform'].e)),
                                         wResampling,
                                         self.ref_extent.left,
                                         self.ref_extent.top,
                                         self.ref_extent.right,
                                         self.ref_extent.bottom,
                                         path_in,
                                         path_out)
            log_message = '* file {} was cropped to AOI & resampled to target resolution'.format(
                os.path.basename(path_in))
        else:
            raise RuntimeError(f'this mode {mode} is currently not forseen.')

        if not os.path.exists(path_out):
            try:
                if cmd_pre is not None:
                    subprocess.check_call(cmd_pre, shell=True, stdout=self.GDAL_print)
                subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
                # now we run a check if all processing was successful or if we have a sub-pixel shift to resolve
                self._pixel_shift_adjustment(path_out)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                # delete file if it is partly processed
                if os.path.exists(path_out):
                    os.remove(path_out)
                raise OSError(
                    'Could not translate the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
                # remove temp files
                if os.path.exists(path_temp):
                    os.remove(path_temp)
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def Warp2AOI(self, path_in, path_out, wResampling='near', wOT='Float64', secure_run=False):
        """Warp a file to an AOI without any checks.

        Note: nodata value stays the same, but dtype can be adjusted.

        :param path_in: input file path (absolute) of raster file to be processed to reference file specifications
        :param path_out: output file path (absolute) of raster file
        :param wResampling: resampling method
        :param wOT: overwriting the input raster data type
        :param secure_run: self.src_parameters is reset before function execution (mainly used when called stand-alone)
        """
        # check if all parameters of input files are existing - when run in stand-alone mode
        self._full_pre_check(path_in, clean_src=secure_run)

        # first resolve gdal_translate and gdalwarp language issue
        if wResampling == 'nearest':
            wResampling = 'near'
        # resolve potential issue in wOT
        if wOT in _dtype_map.keys():
            wOT = _dtype_map[wOT]

        # resolve ESRI / EPSG issue
        if self.src_parameters['epsg'] in ESRI_IDENTIFIER:
            s_srs_string = 'ESRI:{}'.format(self.src_parameters['epsg'])
        else:
            s_srs_string = 'EPSG:{}'.format(self.src_parameters['epsg'])

        cmd = 'gdalwarp --config GDAL_CACHEMAX 256 -s_srs "{}" -t_srs "EPSG:{}" -te {} {} {} {} -tr {} {} -r {} ' \
              '-et 0 -ot {} -ovr None ' \
              '-wo SAMPLE_STEPS=50 -wo SOURCE_EXTRA=5 -wo SAMPLE_GRID=YES ' \
              '-co COMPRESS=DEFLATE -co INTERLEAVE=BAND -co BIGTIFF=YES -multi -co TILED=YES ' \
              '-overwrite "{}" "{}"'.format(s_srs_string,
                                            self.ref_profile['crs'].to_epsg(),
                                            self.ref_extent.left,
                                            self.ref_extent.bottom,
                                            self.ref_extent.right,
                                            self.ref_extent.top,
                                            float(self.ref_profile['transform'].a),
                                            float(abs(self.ref_profile['transform'].e)),
                                            wResampling,
                                            wOT,
                                            path_in,
                                            path_out)
        log_message = '* file {} was warped to AOI and resampled to target resolution'.format(os.path.basename(path_in))

        if not os.path.exists(path_out):
            try:
                subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
                # now we run a check if all processing was successful or if we have a sub-pixel shift to resolve
                self._pixel_shift_adjustment(path_out)
                logger.debug(log_message)
            except subprocess.CalledProcessError as e:
                # delete file if it is partly processed
                if os.path.exists(path_out):
                    os.remove(path_out)
                raise OSError('Could not warp the raster file ({}) to the AOI: {}'.format(os.path.basename(path_in), e))
            else:
                self.adapt_file_metadata(path_in, path_out)
            finally:
                # reset the src_parameter dic since function is a 'one time run'
                self.src_parameters = {}
        else:
            logger.warning('* file already processed. Use existing one {}!'.format(os.path.basename(path_out)))
            # reset the src_parameter dic since function is a 'one time run'
            self.src_parameters = {}

    def adapt_file_metadata(self, path_in, path_out):
        """Write metadata extracted from input file to raster file.

        Note: existing file metadata is removed and replaced with object specific (Metadata class)

        :param path_in: file path (absolute) to file to extract metadata from
        :param path_out: file path (absolute) to processed file which needs metadata infusion
        """
        # first remove existing metadata in dst_file (only allow our metadata)
        # Note: Structure metadata (incl. scale, offset, nodata value, Interleave, Area_or_point ) are not touched
        cmd = '{} -unsetmd "{}"'.format(_GDAL_EDIT, path_out)
        try:
            subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
        except subprocess.CalledProcessError as e:
            raise OSError('GDAL_EDIT issue with file ({}) : {}'.format(os.path.basename(path_out), e))

        # get metadata of input file (adapted ones - not only original)
        self.metadata.read_raster_tags([os.path.normpath(path_in)])

        # get unit if possible from input file
        try:
            punit = self.src_parameters['tags']['unit']
        except Exception:
            punit = ' '

        # create full metadata dict
        tags = self.metadata.prepare_raster_tags(
            'Original file: {}. Warped/translated to EPSG, resolution and extent of project AOI'.format(
                os.path.basename(os.path.normpath(path_in))), punit)

        with rasterio.open(path_out, 'r+') as dst:
            dst.update_tags(**tags)

    def check_raster_contains_ref_extent(self, raster_path):
        """Check if the bounding box of a given raster contains the bounding box of the reference grid."""
        with rasterio.open(raster_path) as ds_raster:
            raster_crs = ds_raster.crs
            raster_bbox = shapely.geometry.box(*ds_raster.bounds)
        # GeoDataFrame for easy coordinate transformation
        df_raster = gpd.GeoDataFrame({'id': 1, 'geometry': [raster_bbox]}, crs=raster_crs)

        # Transform raster bounding box to reference coordinate system if needed.
        # TODO : Only tranforming the 4 corners of the bounding box may not be sufficiently accurate for some
        #  coordinate transforms.  Better to create a polygon from a list of points on the bounding box and transform
        #  that.
        df_raster.to_crs(crs=self.ref_profile['crs'], inplace=True)

        bbox_ref = shapely.geometry.box(*self.ref_extent)
        logger.debug('Raster bbox: %s\nref bbox: %s', df_raster.loc[0, 'geometry'], bbox_ref)
        if not bbox_ref.within(df_raster.loc[0, 'geometry']):
            ref_epsg = self.ref_profile['crs'].to_epsg()
            raise Error(f'Raster file {raster_path} does not contain the complete reference extent.  Please provide a '
                        f'raster file with a minimum extent of {self.ref_extent} (in EPSG:{ref_epsg}).')

    def vector_in_raster_extent_check(self, raster_path, gdf, check_projected=True, check_unit=True, stand_alone=False):
        """Check a set of geographical regions is included in the raster bounds of a given file.

        Note: no content check in raster is done - so regions could be nodata.

        IMPORTANT: the input raster info is saved after the check in case further processing (Bring2AOI) is needed.

        :param raster_path: path to the raster file we want to check.
        :param gdf: GeoDataFrame containing shapes which should be within the raster bounds.
        :param check_projected: also check if raster has a valid projected coordinate system (optional)
        :param check_unit: also check if the raster has a coordinate unit allowed (optional)
        :param stand_alone: functions doesn't store the input profile for further processing steps
        :return: bool
        """
        # always freshly load the profile info
        self.src_parameters = self._load_profile(raster_path)
        # check for main current geoprocessing limitation
        self._epsg_check(self.src_parameters['epsg'], raster_path)

        # check if raster coordinate system is projected or geographic
        if check_projected:
            if self.src_parameters['projected'] is False:
                raise Error(f'Currently the tool only supports raster maps ({raster_path}) with a projected ' +
                            'coordinate system.')
        # check if linear_units is in list we currently support
        if check_unit:
            if self.src_parameters['unit'] not in LIST_UNITS:
                raise Error(
                    f'Raster map ({raster_path}) coordinate system unit is not supported. Currently the tool' +
                    ' only supports projected coordinate systems in the following units: ' + ', '.join(LIST_UNITS))

        # extract BBOX of raster and densify to avoid nonlinear transformations along the bounding box edges
        bbox = shapely.geometry.box(*self.src_parameters['bbox'])
        if self.src_parameters['geographic']:
            max_distance = 0.01
        elif self.src_parameters['unit'] in LIST_UNITS:
            max_distance = 1000
        else:
            raise NotImplementedError(f"for the unit ({self.src_parameters['unit']}) no maximum distance between 2 "
                                      f"nodes in the BBOX densification is implemented")
        bbox_seg = bbox.segmentize(max_distance)

        # bring bbox of raster into a geopandas DataFrame for easier handling
        df_raster = gpd.GeoDataFrame({"id": 1, "geometry": [bbox_seg]})
        # assign the GeoDataFrame the input EPSG code
        df_raster.crs = 'EPSG:{}'.format(self.src_parameters['epsg'])

        # check if we have to reproject something
        # Note: we always convert the raster AOI to avoid issues to change GeoDataFrame projections in the whole project
        # also gives an error if the GeoDataFrame has no valid epsg code
        if gdf.crs.to_epsg() != self.src_parameters['epsg']:
            df_raster.to_crs(epsg=gdf.crs.to_epsg(), inplace=True)

        # reset src_parameters if needed - otherwise further geoprocessing is possible without parameter extraction
        if stand_alone:
            self.src_parameters = {}

        # now we finally can check if all (valid) polygons in the GeoDataFrame are within the raster bbox
        if not gdf.geometry[gdf.is_valid].within(df_raster.loc[0, 'geometry']).all():
            logger.warning('Not all needed shapes specified by the GeoDataFrame are included in ' +
                           'the provided raster file {}'.format(raster_path))
            return False
        else:
            return True

    def crop_2_reporting_AOI(self, src_path, dst_path, path_mask, add_progress=lambda p: None,
                             block_shape=(4096, 4096)):
        """Mask an input raster by given raster and crop to reporting extent.

        Note: original metadata will be taken over

        :param src_path: path to raster which will be masked and cropped (mainly in statistical raster configuration)
        :param dst_path: path to output raster
        :param path_mask: path to the masking raster (dimension must be as src_path)
        :param add_progress: to use the progressbar in QGIS
        :param block_shape: size of blocks for processing
        """
        # check
        self._check2()
        # run block-wise processing of destination file
        with rasterio.open(src_path, 'r') as src_data, \
                rasterio.open(path_mask, 'r') as ds_mask:
            if src_data.nodatavals[0] :
                src_nodata = src_data.nodatavals[0]
            else:
                src_nodata = 0
            with rasterio.open(dst_path, 'w', **dict(self.reporting_profile.copy(), dtype=src_data.dtypes[0],
                                                     nodata=src_nodata)) as ds_out:
                nblocks = number_blocks(self.reporting_profile, block_shape)
                # copy tags to dst
                ds_out.update_tags(**src_data.tags())
                for _, dst_window in block_window_generator(block_shape, ds_out.height, ds_out.width):
                    # here comes the magic -> find the processing_block of dst file in the src file
                    dst_win_bounds = ds_out.window_bounds(dst_window)
                    src_window = src_data.window(dst_win_bounds[0], dst_win_bounds[1],
                                                 dst_win_bounds[2], dst_win_bounds[3])
                    # get data & mask
                    aData = src_data.read(1, window=src_window, masked=True)
                    aMask = ds_mask.read(1, window=src_window)
                    aData[aMask == ds_mask.nodata] = ds_out.nodata
                    ds_out.write(aData.filled(ds_out.nodata), window=dst_window, indexes=1)
                    add_progress(100. / nblocks)

    def vector_2_AOI(self, infile, outfile, mode='statistical'):
        """Reproject and cut a vector file to the desired reference extent."""
        if mode == 'statistical':
            pextent = self.ref_extent
            out_crs = self.ref_profile['crs']
        elif mode == 'reporting':
            self._check2()
            pextent = self.reporting_extent
            out_crs = self.reporting_profile['crs']

        cmd = ['ogr2ogr', '-overwrite',
               '-t_srs', str(out_crs).replace('"', '\\"'),
               '-clipdst',  str(pextent.left), str(pextent.bottom), str(pextent.right), str(pextent.top),
               '-nlt', 'POLYGON',
               outfile, infile]

        subprocess.run(cmd, check=True)

    def merge_raster(self, lPathIn, path_out, mode=None):
        """Merge several GeoTiff files into one.

        Note: use mainly to combine several tiles or raster with non-overlapping nodata areas.

        :param lPathIn: list of paths with all GeoTiff files to merge
        :param path_out: absolute path of the output file name for merged GeoTiff file
        :param mode: processing extent (statistical, regional, None)
        """
        # first generate the VRT file
        # write paths to a text file
        path_list = os.path.join(self.temp_dir, 'paths.txt')
        with open(path_list, "w") as outfile:
            outfile.write("\n".join(lPathIn))
        # create temp vrt
        path_vrt = os.path.join(self.temp_dir, os.path.basename(path_out).split('.')[0] + '.vrt')
        cmd = 'gdalbuildvrt -input_file_list "{}" -overwrite -q "{}"'.format(path_list, path_vrt)
        if mode is None:
            pass
        elif mode == 'statistic':
            self._check()
            cmd += ' -te {} {} {} {} -tr {} {}'.format(self.ref_extent.left,
                                                       self.ref_extent.bottom,
                                                       self.ref_extent.right,
                                                       self.ref_extent.top,
                                                       float(self.ref_profile['transform'].a),
                                                       float(abs(self.ref_profile['transform'].e)))
        elif mode == 'reporting':
            self._check2()
            cmd += ' -te {} {} {} {} -tr {} {}'.format(self.reporting_extent.left,
                                                       self.reporting_extent.bottom,
                                                       self.reporting_extent.right,
                                                       self.reporting_extent.top,
                                                       float(self.reporting_profile['transform'].a),
                                                       float(abs(self.reporting_profile['transform'].e)))
        else:
            raise RuntimeError('The given mode option is not forseen in merge_raster function')

        try:
            subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
        except subprocess.CalledProcessError as e:
            raise OSError(f'Could not generate the needed VRT file: {e}.')

        # transfer to GeoTiff
        cmd = 'gdal_translate -strict -co COMPRESS=DEFLATE "{}" "{}"'.format(path_vrt, path_out)
        try:
            subprocess.check_call(cmd, shell=True, stdout=self.GDAL_print)
        except subprocess.CalledProcessError as e:
            raise OSError(f'Could not translate the VRT file into raster file: {e}.')
        finally:
            # remove temp files
            if os.path.exists(path_vrt):
                os.remove(path_vrt)
            if os.path.exists(path_list):
                os.remove(path_list)

    def spatial_disaggregation_byArea(self, path_proxy_raster, data, path_area_raster, area_names, path_out,
                                      add_progress=lambda p: None, proxy_sums=None,
                                      processing_info='N/A', unit_info='N/A', block_shape=(2048, 2048)):
        """Disaggregate values per region using a proxy raster, such that the total for each region is preserved.

        Given an area raster and a table total values per area, disaggregate the totals according to the proxy raster,
        such that the sum of the disaggregated values for the pixels within each Area equals the given total for the
        area. Uses block processing to reduce memory usage.

        :param path_proxy_raster: path to the raster dataset containing the proxy for spatially disaggregation
        :param data: Series containing the area-specific total values to distribute, indexed by area codes
        :param path_area_raster: path to the raster holding the areas for which statistics are extracted
        :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name,
                           value = raster value)
        :param path_out: absolute output file name for spatially disaggregated raster file
        :param add_progress: callback function to update progress bar.
        :param proxy_sums: Optional Series of precomputed sum of proxy data per Area.
        :param processing_info: string describing the processing step (optional)
        :param unit_info:  string describing the unit of the spatially disaggregated totals (optional)
        :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
        :return: DataFrame containing sum of proxy values per region.  Can be reused in subsequent calls to
                 :meth:`spatial_disaggregation_byArea` to save computation effort.

        """
        progress_remain = 100.  # remaining progress to be used for progress bar
        if proxy_sums is None:
            # first get the proxy raster value sum of the different areas for which to disaggregate the table data
            proxy_sums = statistics_byArea(path_proxy_raster, path_area_raster, area_names,
                                           add_progress=lambda p: add_progress(0.4 * p),
                                           # assign 40% of progress to stats
                                           block_shape=block_shape)[SUM]
            progress_remain = 60.  # 40% assigned to the statistics extraction

        # calculate area-specific contribution factor
        # TODO make sure area_names and data have the same index before calling statistics_byArea, so proxy_sums will
        #  be complete! (and the reindex is not needed here anymore)?
        #  Take care that area_names can be a dict (convert to Series first?)!
        dis_factor = (data / proxy_sums.reindex(data.index)).fillna(0)

        # start processing
        with rasterio.open(path_proxy_raster) as src_proxy, \
                rasterio.open(path_area_raster) as src_area:

            dst_profile = src_proxy.profile
            dst_profile.update(dtype=rasterio.float32, nodata=np.nan)
            # number of to process blocks
            nblocks = number_blocks(dst_profile, block_shape)

            # init the output raster file
            with rasterio.open(path_out, 'w', **dst_profile) as dst:
                # add metadata if needed
                self.metadata.update_dataset_tags(dst, processing_info, unit_info, path_proxy_raster, path_area_raster)

                # now iterate over the blocks
                for _, window in block_window_generator(block_shape, dst_profile['height'], dst_profile['width']):
                    # read data
                    aProxy = src_proxy.read(1, window=window, masked=True)
                    aArea = src_area.read(1, window=window)

                    # init output block
                    aData = np.full_like(aProxy, dst_profile['nodata'], dtype=dst_profile['dtype'])

                    # check if empty
                    if aProxy.mask.all() or (aArea == 0).all():
                        dst.write(aData, 1, window=window)
                        add_progress(progress_remain / nblocks)
                        continue
                    # reduce workload by only looping over existing areas & ETclasses in the block
                    lAreas = np.unique(aArea[(aArea != 0) & (~aProxy.mask)]).tolist()

                    # loop over the areas to spatially disaggregate the data to the proxy
                    for area_name, area_value in area_names.items():
                        if area_value not in lAreas:
                            continue
                        np.multiply(aProxy.data, dis_factor[area_name],
                                    where=((aArea == area_value) & (~aProxy.mask)), out=aData)
                    # write to disk
                    dst.write(aData, 1, window=window)
                    add_progress(progress_remain / nblocks)  # remaining progress here
        return proxy_sums  # Return proxy_sums so it may be reused by the caller in subsequent disaggregations

    def spatial_disaggregation_byArea_byET(self, path_proxy_raster, data, path_area_raster, area_names,
                                           path_ET_raster, ET_names, path_out,
                                           add_progress=lambda p: None, proxy_sums=None,
                                           processing_info='N/A', unit_info='N/A', block_shape=(2048, 2048)):
        """Disaggregate values per region and ecosystem type using a proxy raster, preserving total values.

        Given area and ecosystem type rasters, and a table of total values per combination of area and ecosystem type,
        disaggregate the totals according to the proxy raster, such that the sum of the disaggregated values for the
        pixels for each ET class within each Area equals the given total for the ET class and area. Uses block
        processing to reduce memory usage.

        :param path_proxy_raster: path to the raster dataset containing the proxy for spatially disaggregation
        :param data: Series containing the area-ET-specific total values to distribute, MultiIndex is mandatory given
                      first the AreaID (GEO_ID) and second the ETClassID (ECOTYPE).
        :param path_area_raster: path to the raster holding the areas for which statistics are extracted
        :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name,
                           value = raster value)
        :param path_ET_raster: path to raster dataset containing the ET classes for sub-grouping
        :param ET_names: dict or Series mapping raster value to ecosystem types (key/index = ecosystem type,
                         value = raster value)
        :param path_out: absolute output file name for spatially disaggregated raster file
        :param add_progress: callback function to update progress bar.
        :param proxy_sums: Optional Series of precomputed sum of proxy data per Area and per ET.
        :param processing_info: string describing the processing step (optional)
        :param unit_info: string describing the unit of the spatially disaggregated totals (optional)
        :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
        :return: DataFrame containing sum of proxy values per region and per ecosystem type. Can be reused in subsequent
                 calls to :meth:`spatial_disaggregation_byArea_byET` to save computation effort.
        """
        progress_remain = 100.
        if proxy_sums is None:
            # first get the proxy raster value sum of the different areas for which to disaggregate the table data
            proxy_sums = statistics_byArea_byET(path_proxy_raster, path_area_raster, area_names, path_ET_raster,
                                                ET_names,
                                                add_progress=lambda p: add_progress(0.4 * p),  # 40% of progress here
                                                block_shape=block_shape)[SUM]
            progress_remain = 60.

        # calculate area-specific contribution factor
        # Note: proxy_sums has a MultiIndex
        # TODO make sure all indices from data appear in area_names and ET_names before calling statistics_byArea_byET,
        #  so proxy_sums exist for every row in data (and the reindex is perhaps not needed here anymore)?
        #  Take care that area_names and ET_names can be dict (convert to Series first?)!
        dis_factor = (data / proxy_sums.reindex(data.index)).fillna(0)

        # start processing
        with rasterio.open(path_proxy_raster) as src_proxy, \
                rasterio.open(path_area_raster) as src_area, \
                rasterio.open(path_ET_raster) as src_ET:

            # get info for block processing and output raster file
            dst_profile = src_proxy.profile
            dst_profile.update(dtype=rasterio.float32, nodata=np.nan)
            # number of to process blocks
            nblocks = number_blocks(dst_profile, block_shape)

            # init the output raster file
            with rasterio.open(path_out, 'w', **dst_profile) as dst:
                # add metadata if needed
                self.metadata.update_dataset_tags(dst, processing_info, unit_info,
                                                  path_proxy_raster, path_area_raster, path_ET_raster)

                # now iterate over the blocks
                for _, window in block_window_generator(block_shape, dst_profile['height'], dst_profile['width']):
                    # read data
                    aProxy = src_proxy.read(1, window=window, masked=True)
                    aArea = src_area.read(1, window=window)
                    aET = src_ET.read(1, window=window)

                    # init output block
                    aData = np.full_like(aProxy, dst_profile['nodata'], dtype=dst_profile['dtype'])

                    # check if empty
                    if aProxy.mask.all() or (aArea == 0).all():
                        dst.write(aData, 1, window=window)
                        add_progress(progress_remain / nblocks)
                        continue
                    # reduce workload by only looping over existing areas & ETclasses in the block
                    lAreas = np.unique(aArea[(aArea != 0) & (~aProxy.mask)]).tolist()
                    lET = np.unique(aET[(aArea != 0) & (~aProxy.mask)]).tolist()

                    # loop over the areas & ETclasses to spatially disaggregate the data to the proxy
                    for area_name, area_value in area_names.items():
                        if area_value not in lAreas:
                            continue
                        for eco_type, eco_id in ET_names.items():
                            if eco_id not in lET:
                                continue
                            np.multiply(aProxy.data, dis_factor[area_name, eco_type],
                                        where=((aArea == area_value) & (aET == eco_id) & (~aProxy.mask)),
                                        out=aData)
                    # write to disk
                    dst.write(aData, 1, window=window)
                    add_progress(progress_remain / nblocks)  # 60% of progress here
        return proxy_sums  # For optional reuse by the caller in subsequent disaggregations


def Bring2COG():
    """Translate from GeoTiff to COG.

    # TODO: put in GeoProcessing class

    implement via the rio-cogeo function.::

       from rio_cogeo.cogeo import cog_translate
       from rio_cogeo.profiles import cog_profiles

       def _translate(src_path, dst_path, profile="webp", profile_options={}, **options):
           \"\"\"Convert image to COG.\"\"\"
           # Format creation option (see gdalwarp `-co` option)
           output_profile = cog_profiles.get(profile)
           output_profile.update(dict(BIGTIFF="IF_SAFER"))
           output_profile.update(profile_options)

           # Dataset Open option (see gdalwarp `-oo` option)
           config = dict(
               GDAL_NUM_THREADS="ALL_CPUS",
               GDAL_TIFF_INTERNAL_MASK=True,
               GDAL_TIFF_OVR_BLOCKSIZE="128",
           )

           cog_translate(
               src_path,
               dst_path,
               output_profile,
               config=config,
               in_memory=False,
               quiet=True,
               **options,
           )
           return True

    """
    pass


def statistics_byArea(path_data_raster, path_area_raster, area_names, transform=None,
                      add_progress=lambda p: None, block_shape=(2048, 2048)):
    """Extract sum and count statistics for all areas given in the area_raster for the data_raster.

    Note: currently only works correctly for raster with absolute data values (relative datasets would need unit and
    pixel area)

    :param path_data_raster: path to the raster dataset containing the absolute values
    :param path_area_raster: path to the raster holding the areas for which statistics are extracted
    :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name,
                       value = raster value)
    :param transform: Optional single-argument function to transform the data by.
    :param add_progress: callback function to update progress bar.
    :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
    :return: pandas dataframe with area codes as index and columns for raster value sum and raster pixel count
    """
    # convert area_names to DataFrame, indexed by SHAPE_ID
    area_names = pd.DataFrame(area_names.items(), columns=[GEO_ID, SHAPE_ID]).set_index(SHAPE_ID)

    # ini pandas DataFrame to hold the results
    df = pd.DataFrame(0., index=area_names.index, columns=[SUM, COUNT], dtype=float)
    df.index.name = SHAPE_ID

    with rasterio.open(path_data_raster) as src_data, \
            rasterio.open(path_area_raster) as src_area:
        # extract raster info for progress bar
        src_profile = src_data.profile
        # number of to process blocks
        nblocks = number_blocks(src_profile, block_shape)

        # now iterate over the blocks of src files and process
        for _, window in block_window_generator(block_shape, src_profile['height'], src_profile['width']):
            # read data; convert to float to avoid overflow when original data is an integer type
            aData = src_data.read(1, window=window, masked=True).astype(float)
            aArea = src_area.read(1, window=window)

            # create valid data mask
            mValid = (aArea != src_area.nodata) & (~aData.mask)
            # check if all is empty
            if (~mValid).all():
                add_progress(100. / nblocks)
                continue

            if transform:
                aData = transform(aData)
            # bring valid data into DataFrame
            df_window = pd.DataFrame({SHAPE_ID: aArea[mValid].flatten(), SUM: aData[mValid].data.flatten(), COUNT: 1})
            # add block data to master DataFrame
            df = df.add(df_window.groupby(SHAPE_ID).sum(), fill_value=0.)
            add_progress(100. / nblocks)

    # If the SHAPE_ID index values do not agree, the rasterized shapefile doesn't match the currently selected regions.
    if not df.index.isin(area_names.index).all():
        raise Error('Mismatch between selected regions and rasterized regions shapefile.  '
                    'This may happen when continuing from a previous run with different settings.')

    return df.join(area_names).set_index(GEO_ID)


def statistics_byArea_byET(path_data_raster, path_area_raster, area_names, path_ET_raster, ET_names,
                           add_progress=lambda p: None,
                           block_shape=(2048, 2048)):
    """Extract sum and count statistics for per area and per ecosystem type.

    Note: currently only works correctly for raster with absolute data values (relative datasets would need
    unit and pixel area)

    :param path_data_raster: path to the raster dataset containing the absolute values
    :param path_area_raster: path to the raster holding the areas for which statistics are extracted
    :param area_names: dict or Series mapping raster value to clear name of areas (key/index = clear name/area,
                       value = raster value)
    :param path_ET_raster: path to raster dataset containing the ET classes for sub-grouping
    :param ET_names: dict  or Series mapping raster value to ecosystem types (key/index = clear name/ecosystem type,
                     value = raster value)
    :param add_progress: callback function to update progress bar.
    :param block_shape: tuple (y_size, x_size) for block processing of the statistic extraction (optional)
    :return: pandas dataframe with area codes and ET class as multi-index and columns for raster value sum and
             raster pixel count
    """
    # convert area_names and ET_names to DataFrame, indexed by SHAPE_ID and ECO_ID
    area_names = pd.DataFrame(area_names.items(), columns=[GEO_ID, SHAPE_ID]).set_index(SHAPE_ID)
    ET_names = pd.DataFrame(ET_names.items(), columns=[ECOTYPE, ECO_ID]).set_index(ECO_ID)
    # ini pandas DataFrame to hold the results
    index = pd.MultiIndex.from_product([area_names.index, ET_names.index], names=[SHAPE_ID, ECO_ID])
    df = pd.DataFrame(0., index=index, columns=[SUM, COUNT], dtype=float)

    with rasterio.open(path_data_raster) as src_data, \
            rasterio.open(path_area_raster) as src_area, \
            rasterio.open(path_ET_raster) as src_ET:
        # extract raster info for progress bar
        src_profile = src_data.profile
        # number of to process blocks
        nblocks = number_blocks(src_profile, block_shape)

        # now iterate over the blocks of src files and process
        for _, window in block_window_generator(block_shape, src_profile['height'], src_profile['width']):
            # read data
            aData = src_data.read(1, window=window, masked=True).astype(float)
            aArea = src_area.read(1, window=window)
            aET = src_ET.read(1, window=window)

            # create valid data mask
            mValid = (aArea != src_area.nodata) & (~aData.mask) & (aET != src_ET.nodata)
            # check if all is empty
            if (~mValid).all():
                add_progress(100. / nblocks)
                continue

            # bring valid data into DataFrame
            df_window = pd.DataFrame({SHAPE_ID: aArea[mValid].flatten(), ECO_ID: aET[mValid].flatten(),
                                      SUM: aData[mValid].data.flatten(), COUNT: 1})
            # add block data to master DataFrame
            df = df.add(df_window.groupby([SHAPE_ID, ECO_ID]).sum(), fill_value=0.)
            add_progress(100. / nblocks)

    # If the SHAPE_ID index values do not agree, the rasterized shapefile doesn't match the currently selected regions:
    if not df.index.get_level_values(SHAPE_ID).isin(area_names.index).all():
        raise Error('Mismatch between selected regions and rasterized regions shapefile.  '
                    'This may happen when continuing from a previous run with different settings.')

    return df.join(area_names).join(ET_names).set_index([GEO_ID, ECOTYPE])


def block_window_generator(block_shapes, img_height, img_width):
    """Return an iterator over a band's block windows and their indexes.

    Block windows are tuples::

        ((row_start, row_stop), (col_start, col_stop))

    For example, ``((0, 2), (0, 2))`` defines a 2 x 2 block at the upper
    left corner of the raster dataset.
    This iterator yields blocks "left to right" and "top to bottom"
    and is similar to Python's ``enumerate()`` in that it also returns
    indexes.
    Main change to default rasterio function is that you can define
    your own block_shape!!!
    """
    # get block_height and block_width separately
    block_h, block_w = block_shapes
    # calculate number of block in row and cloumn to process
    d, m = divmod(img_height, block_h)
    nrows = d + int(m > 0)
    d, m = divmod(img_width, block_w)
    ncols = d + int(m > 0)
    # start generation of windows
    for j in range(nrows):
        # get row_start
        row_start = j * block_h
        # correct block_height if we at end of image
        block_h_corr = min(block_h, img_height - row_start)
        for i in range(ncols):
            # get col_start
            col_start = i * block_w
            # correct block_width if we at end of image
            block_width_corr = min(block_w, img_width - col_start)
            # generate final window and yield result
            yield (j, i), ((row_start, row_start + block_h_corr), (col_start, col_start + block_width_corr))


def number_blocks(profile, block_shape):
    """Calculate the total number of blocks to process by given raster profile and block_shape.

    :param profile: rasterio profile of a raster dataset
    :param block_shape: tuple (y_size, x_size) for size of blocks in block processing
    :return: number of blocks to process
    """
    return math.ceil(profile['height'] * 1.0 / block_shape[0]) * math.ceil(profile['width'] * 1.0 / block_shape[1])


def pixel_area(crs: rasterio.crs.CRS, transform: affine.Affine):
    """Calculate the area of a pixel in units of m2."""
    affine_area = abs(transform.a * transform.e - transform.b * transform.d)
    unit, factor = crs.linear_units_factor
    return affine_area * factor ** 2


def coord_2_index(x, y, raster_x_min, raster_y_max, pixres, raster_x_max=None, raster_y_min=None):
    """Get array indices from given coordinates.

    rasterio makes it easy for us to find the row and column indices of a pixel by given coordinate (it takes the UL
    corner of a pixel into account in this case)..

    .. note::
        This function needs UL corner coordinate of UL pixel in raster and pix-resolution in same crs as input xy to
        work!!!

    .. note::
        if you also give the UL corner coordinate of first LR pixel outside raster then a plausibility check is done
        --> (== BBOX of raster) the x_max or y_min which can never reached since belongs to first pixel outside the
        raster.

    .. note::
        output tuple is directly in numpy indexing order (row, column).
    """
    index_column = int((x - raster_x_min) / float(pixres[0]))
    index_row = int((y - raster_y_max) / float(-pixres[1]))

    if (raster_x_max is None) or (raster_y_min is None):
        return index_row, index_column
    else:
        if (raster_y_min < y <= raster_y_max) and (raster_x_min <= x < raster_x_max):
            return index_row, index_column
        else:
            raise ValueError("The given coordinate is outside the dataset")


def average_rasters(output_file, *rasters, block_shape=(1024, 1024), **profile_args):
    """Calculate the average of a list of input rasters with same extent/resolution/projection.

    Metadata tags are taken from the first raster in the list.

    :param output_file: Name of the output file.
    :param block_shape: Block shape to use during processing.
    :param profile_args: Additional options for the output raster profile.

    """
    sum_rasters(output_file, *rasters, weight=1./len(rasters), block_shape=block_shape, **profile_args)


def sum_rasters(output_file, *rasters,
                weight=1,
                block_shape=(1024, 1024), dtype=rasterio.float32, compress='LZW', **profile_args):
    """Calculate the sum of a list of input rasters with same extent/resolution/projection.

    An optional weight can be provided (i.e. to calculate an average).
    Metadata tags are taken from the first raster in the list.
    """
    with ExitStack() as stack:
        input_ds = [stack.enter_context(rasterio.open(f)) for f in rasters]
        src_tags = input_ds[0].tags()
        del src_tags['AREA_OR_POINT']
        fill_value = input_ds[0].nodata

        profile = dict(input_ds[0].profile,
                       tiled=True, blockxsize=block_shape[1], blockysize=block_shape[0],
                       dtype=dtype, compress=compress, **profile_args)

        with rasterio.open(output_file, 'w', **profile) as out:
            out.update_tags(**src_tags)
            for _, window in block_window_generator(block_shape, out.profile['height'], out.profile['width']):
                result = sum(ds.read(1, window=window, masked=True) for ds in input_ds) * weight
                out.write(result.filled(fill_value).astype(out.profile['dtype']), 1, window=window)


#Might be interesting to move to Geoprocessing Class
def GSM(nosm, sm, gaussian_sigma, gaussian_kernel_radius, block_shape=(2048, 2048)):
    # create kernel
    radius = gaussian_kernel_radius
    y, x = np.ogrid[-radius: radius + 1, -radius: radius + 1]
    kernelr = x ** 2 + y ** 2 <= radius ** 2
    # struct = np.array([kernelr.astype(np.bool)])
    # some how there is a factor 10 in the sigma when converting SAGA to normal scipy stuff
    kernel = gaussian_kernel(gaussian_kernel_radius*2+1, sigma=gaussian_sigma/10)
    kernel[~kernelr] = 0
    kernel = kernel/np.sum(kernel)

    with rasterio.open(nosm, 'r') as ds_open:
        profile = ds_open.profile
        with rasterio.open(sm, 'w', **dict(profile, driver='GTiff', dtype='float64')) as ds_out:
            for _, window in block_window_generator(block_shape, ds_open.height, ds_open.width):
                # calc amout of padding:
                window = Window.from_slices(rows=window[0], cols=window[1])
                # add padding
                PaddedWindow = Window(window.col_off - gaussian_kernel_radius, window.row_off - gaussian_kernel_radius,
                                      window.width + gaussian_kernel_radius*2, window.height + gaussian_kernel_radius*2)

                # adapt window for padding
                aBlock = ds_open.read(1, window=PaddedWindow, boundless=True, masked=True).astype(np.float64)

                # should not use gaussian_filter since it is only for rectangular shapes and not circ shaped kernels
                # Does not have major impact if sigma > kernel_radius
                # output = gaussian_filter(aBlock,sigma=gaussian_sigma,radius=radius)[radius:-radius, radius:-radius]

                output = correlate(aBlock, kernel)[radius:-radius, radius:-radius]

                ds_out.write(output, window=window, indexes=1)


def gaussian_kernel(size, sigma=1):
    kernel_1D = np.linspace(-(size // 2), size // 2, size)
    for i in range(size):
        kernel_1D[i] = dnorm(kernel_1D[i], 0, sigma)
    kernel_2D = np.outer(kernel_1D.T, kernel_1D.T)
    kernel_2D *= 1.0 / kernel_2D.max()
    return kernel_2D


def dnorm(x, mu, sd):
    return 1 / (np.sqrt(2 * np.pi) * sd) * np.e ** (-np.power((x - mu) / sd, 2) / 2)


def add_area(inname, outname, scaling):
    gdf = gpd.read_file(inname).sort_index()
    gdf['AREA'] = gdf['geometry'].area * scaling
    gdf.to_file(outname)


def adding_stats(rasters, shapes, outname, stats):
    '''
    raster: list of rasters with elements to used to calc stats
    shapes: shapes over which the stats should be calculated
    outname: name of the output file
    stats: list of statistics to process
    '''
    gdf = gpd.read_file(shapes).sort_index()
    new_column = []
    for raster in rasters:
        with rasterio.open(raster) as ds:
            for atuple in gdf.itertuples():
                try:
                    value, _ = rasterio.mask.mask(ds, [atuple.geometry], crop=True, indexes=1)
                except Exception:
                    logger.exception("Something went wrong with the masking, "
                                     "could be due to shapes falling outside raster")
                    value = ds.nodata
                new_column.append([stat(value, where=(value != ds.nodata) & ~(np.isnan(value))) for stat in stats])

        new_df = pd.DataFrame(new_column, columns=[os.path.splitext(
            os.path.basename(raster))[0] + '_' + stat.__name__ for stat in stats])
        gdf = gpd.pd.concat([gdf, new_df], axis=1)
    gdf.to_file(outname)


def count(raster, where=filter):

    return len(raster[where])


def norm_1(raster):
    #function normalizes and inverts value
    return 1 / (1 + raster/100)

def add_color(file, ctable, type = 'Byte'):
    from osgeo import gdal
    if type == 'Byte':
        max = 255
    else:
        max = 65535

    ds = gdal.Open(file, 1)
    band = ds.GetRasterBand(1)     #single band VRT mosaics
    ct = gdal.ColorTable()         #gdal.GCI_PaletteIndex)

    #create dummy table
    '''
    for i in range(0,max):
        ct.SetColorEntry(i,(255,255,255,255))
    '''
    with open(ctable) as f:
        next(f)
        next(f)
        for line in f:
            #overwrite values
            ct.SetColorEntry(int(line.split(',')[0]),(int(line.split(',')[1]),int(line.split(',')[2]),int(line.split(',')[3]),int(line.split(',')[4])))    #(value,(R,G,B,alpha))

    band.SetRasterColorTable(ct)
    band.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
    #band.FlushCache()
    del band, ds
    #push to file
    return

