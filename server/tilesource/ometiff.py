#!/usr/bin/env python
# -*- coding: utf-8 -*-

##############################################################################
#  Copyright Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
##############################################################################

import math
import six
from six.moves import range

from .base import TileSourceException
from ..cache_util import LruCacheMetaclass, methodcache
from ..constants import SourcePriority
from .tiff import TiffFileTileSource
from .tiff_reader import TiledTiffDirectory, InvalidOperationTiffException, \
    TiffException, IOTiffException

try:
    import girder
    from girder import logger
    from .base import GirderTileSource
except ImportError:
    girder = None
    import logging as logger
    logger.getLogger().setLevel(logger.INFO)
from .base import TILE_FORMAT_PIL

try:
    import PIL.Image
except ImportError:
    PIL = None


_omeUnitsToMeters = {
    'Ym': 1e24,
    'Zm': 1e21,
    'Em': 1e18,
    'Pm': 1e15,
    'Tm': 1e12,
    'Gm': 1e9,
    'Mm': 1e6,
    'km': 1e3,
    'hm': 1e2,
    'dam': 1e1,
    'm': 1,
    'dm': 1e-1,
    'cm': 1e-2,
    'mm': 1e-3,
    u'\u00b5m': 1e-6,
    'nm': 1e-9,
    'pm': 1e-12,
    'fm': 1e-15,
    'am': 1e-18,
    'zm': 1e-21,
    'ym': 1e-24,
    u'\u00c5': 1e-10,
}


@six.add_metaclass(LruCacheMetaclass)
class OMETiffFileTileSource(TiffFileTileSource):
    """
    Provides tile access to TIFF files.
    """
    cacheName = 'tilesource'
    name = 'ometifffile'
    extensions = {
        None: SourcePriority.LOW,
        'tif': SourcePriority.MEDIUM,
        'tiff': SourcePriority.MEDIUM,
        'ome': SourcePriority.PREFERRED,
    }

    def __init__(self, path, **kwargs):
        """
        Initialize the tile class.  See the base class for other available
        parameters.

        :param path: a filesystem path for the tile source.
        """
        super(TiffFileTileSource, self).__init__(path, **kwargs)

        largeImagePath = self._getLargeImagePath()

        try:
            base = TiledTiffDirectory(largeImagePath, 0)
        except TiffException:
            raise TileSourceException('Not a tiled OME Tiff')
        info = getattr(base, '_description_xml', None)
        if not info.get('OME'):
            raise TileSourceException('Not an OME Tiff')
        self._omeinfo = info['OME']
        if isinstance(self._omeinfo['Image'], dict):
            self._omeinfo['Image'] = [self._omeinfo['Image']]
        for img in self._omeinfo['Image']:
            if isinstance(img['Pixels'].get('TiffData'), dict):
                img['Pixels']['TiffData'] = [img['Pixels']['TiffData']]
            if isinstance(img['Pixels'].get('Plane'), dict):
                img['Pixels']['Plane'] = [img['Pixels']['Plane']]
        try:
            self._omebase = self._omeinfo['Image'][0]['Pixels']
            if len({entry['UUID']['FileName'] for entry in self._omebase['TiffData']}) > 1:
                raise TileSourceException('OME Tiff references multiple files')
            if (len(self._omebase['TiffData']) != int(self._omebase['SizeC']) *
                    int(self._omebase['SizeT']) * int(self._omebase['SizeZ']) or
                    len(self._omebase['TiffData']) != len(
                        self._omebase.get('Plane', self._omebase['TiffData']))):
                raise TileSourceException('OME Tiff contains frames that contain multiple planes')
        except (KeyError, ValueError, IndexError):
            raise TileSourceException('OME Tiff does not contain an expected record')
        omeimages = [
            entry['Pixels'] for entry in self._omeinfo['Image'] if
            len(entry['Pixels']['TiffData']) == len(self._omebase['TiffData'])]
        levels = [max(0, int(math.ceil(math.log(max(
            float(entry['SizeX']) / base.tileWidth,
            float(entry['SizeY']) / base.tileHeight)) / math.log(2))))
            for entry in omeimages]
        omebylevel = dict(zip(levels, omeimages))
        self._omeLevels = [omebylevel.get(key) for key in range(max(omebylevel.keys()) + 1)]
        self._tiffDirectories = [
            TiledTiffDirectory(largeImagePath, int(entry['TiffData'][0]['IFD']))
            if entry else None
            for entry in self._omeLevels]
        self._directoryCache = {}
        self._directoryCacheMaxSize = max(20, len(self._omebase['TiffData']) * 3)
        self.tileWidth = base.tileWidth
        self.tileHeight = base.tileHeight
        self.levels = len(self._tiffDirectories)
        self.sizeX = base.imageWidth
        self.sizeY = base.imageHeight

        # We can get the embedded images, but we don't currently use non-tiled
        # images as associated images.  This would require enumerating tiff
        # directories not mentioned by the ome list.
        self._associatedImages = {}

    def getMetadata(self):
        """
        Return a dictionary of metadata containing levels, sizeX, sizeY,
        tileWidth, tileHeight, magnification, mm_x, mm_y, and frames.

        :returns: metadata dictonary.
        """
        result = super(OMETiffFileTileSource, self).getMetadata()
        # We may want to reformat the frames to standardize this across sources
        result['frames'] = self._omebase.get('Plane', self._omebase['TiffData'])
        result['omeinfo'] = self._omeinfo
        return result

    def getNativeMagnification(self):
        """
        Get the magnification for the highest-resolution level.

        :return: magnification, width of a pixel in mm, height of a pixel in mm.
        """
        result = super(OMETiffFileTileSource, self).getNativeMagnification()
        if result['mm_x'] is None and 'PhysicalSizeX' in self._omebase:
            result['mm_x'] = (
                float(self._omebase['PhysicalSizeX']) * 1e3 *
                _omeUnitsToMeters[self._omebase.get('PhysicalSizeXUnit', '\u00b5m')])
        if result['mm_y'] is None and 'PhysicalSizeY' in self._omebase:
            result['mm_y'] = (
                float(self._omebase['PhysicalSizeY']) * 1e3 *
                _omeUnitsToMeters[self._omebase.get('PhysicalSizeYUnit', '\u00b5m')])
        if not result.get('magnification') and result.get('mm_x'):
            result['magnification'] = 0.01 / result['mm_x']
        return result

    @methodcache()
    def getTile(self, x, y, z, pilImageAllowed=False, sparseFallback=False,
                **kwargs):
        if (z < 0 or z >= len(self._omeLevels) or self._omeLevels[z] is None or
                kwargs.get('frame') in (None, 0, '0', '')):
            return super(OMETiffFileTileSource, self).getTile(
                x, y, z, pilImageAllowed=pilImageAllowed, sparseFallback=sparseFallback, **kwargs)
        frame = int(kwargs['frame'])
        if frame < 0 or frame >= len(self._omebase['TiffData']):
            raise TileSourceException('Frame does not exist')
        dirnum = int(self._omeLevels[z]['TiffData'][frame]['IFD'])
        if dirnum in self._directoryCache:
            dir = self._directoryCache[dirnum]
        else:
            if len(self._directoryCache) >= self._directoryCacheMaxSize:
                self._directoryCache = {}
            dir = TiledTiffDirectory(self._getLargeImagePath(), dirnum)
            self._directoryCache[dirnum] = dir
        try:
            tile = dir.getTile(x, y)
            format = 'JPEG'
            if PIL and isinstance(tile, PIL.Image.Image):
                format = TILE_FORMAT_PIL
            return self._outputTile(tile, format, x, y, z, pilImageAllowed,
                                    **kwargs)
        except InvalidOperationTiffException as e:
            raise TileSourceException(e.args[0])
        except IOTiffException as e:
            return self.getTileIOTiffException(
                x, y, z, pilImageAllowed=pilImageAllowed,
                sparseFallback=sparseFallback, exception=e, **kwargs)


if girder:
    class OMETiffGirderTileSource(OMETiffFileTileSource, GirderTileSource):
        """
        Provides tile access to Girder items with a TIFF file.
        """
        cacheName = 'tilesource'
        name = 'ometiff'
