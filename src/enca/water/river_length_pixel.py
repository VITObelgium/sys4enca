import os
import subprocess

import enca
from enca.framework.config_check import ConfigShape


_GLORIC = 'GLORIC'


class RiverLength(enca.ENCARun):

    component = 'WATER_RIVER_LENGTH_PX'

    def __init__(self, config):
        """Initialize config template."""
        super().__init__(config)

        self.config_template.update({
            self.component: {
                _GLORIC: ConfigShape()}})

    def _start(self):
        # Convert GLORIC shapefile to correct EPSG
        ref_epsg = self.accord.ref_profile['crs'].to_epsg()
        temp_file = os.path.join(self.temp_dir(), f'GLORIC_EPSG{ref_epsg}.shp')

        extent = self.accord.ref_extent

        cmd = ['ogr2ogr',
               '-f', 'ESRI shapefile',
               '-overwrite',
               '-t_srs', self.accord.ref_profile['crs'].to_string(),
               '-clipdst', str(extent.left), str(extent.bottom), str(extent.right), str(extent.top),
               temp_file,
               self.config[self.component][_GLORIC]]
        subprocess.run(cmd, check=True)

        # Rasterize reprojected GLORIC file to AOI:
        out_file = os.path.join(self.maps, 'NCA_WATER_river-length_pixel.tif')
        cmd = ['gdal_rasterize',
               '-burn', '1',
               '-at',
               '-l', os.path.basename(temp_file)[:-4],
               '-init', '0',
               '-co', 'COMPRESS=LZW', '-ot', 'Float32',
               '-te', str(extent.left), str(extent.bottom), str(extent.right), str(extent.top),
               '-tr', str(self.accord.ref_profile['transform'].a), str(abs(self.accord.ref_profile['transform'].e)), 
               temp_file,
               out_file]
        subprocess.run(cmd, check=True)
