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

import openslide
import PIL
from pkg_resources import DistributionNotFound, get_distribution

from large_image import config
from large_image.cache_util import LruCacheMetaclass, methodcache
from large_image.constants import SourcePriority
from large_image.exceptions import TileSourceException
from large_image.tilesource import FileTileSource, nearPowerOfTwo


try:
    __version__ = get_distribution(__name__).version
except DistributionNotFound:
    # package is not installed
    pass


@six.add_metaclass(LruCacheMetaclass)
class OpenslideFileTileSource(FileTileSource):
    """
    Provides tile access to SVS files and other files the openslide library can
    read.
    """
    cacheName = 'tilesource'
    name = 'svsfile'
    extensions = {
        None: SourcePriority.MEDIUM,
        'bif': SourcePriority.LOW,  # Ventana
        'mrxs': SourcePriority.PREFERRED,  # MIRAX
        'ndpi': SourcePriority.PREFERRED,  # Hamamatsu
        'scn': SourcePriority.LOW,  # Leica
        'svs': SourcePriority.PREFERRED,
        'svslide': SourcePriority.PREFERRED,
        #'tif': SourcePriority.MEDIUM,
        'tif': SourcePriority.PREFERRED,
        'tiff': SourcePriority.MEDIUM,
        'vms': SourcePriority.HIGH,  # Hamamatsu
        'vmu': SourcePriority.HIGH,  # Hamamatsu
    }
    mimeTypes = {
        None: SourcePriority.FALLBACK,
        'image/mirax': SourcePriority.PREFERRED,  # MIRAX
        'image/tiff': SourcePriority.MEDIUM,
        'image/x-tiff': SourcePriority.MEDIUM,
    }

    def __init__(self, path, **kwargs):
        """
        Initialize the tile class.  See the base class for other available
        parameters.

        :param path: a filesystem path for the tile source.
        """
        super(OpenslideFileTileSource, self).__init__(path, **kwargs)

        largeImagePath = self._getLargeImagePath()

        try:
            self._openslide = openslide.OpenSlide(largeImagePath)
        except openslide.lowlevel.OpenSlideUnsupportedFormatError:
            raise TileSourceException('File cannot be opened via OpenSlide.')
        except openslide.lowlevel.OpenSlideError:
            raise TileSourceException('File will not be opened via OpenSlide.')

        svsAvailableLevels = self._getAvailableLevels(largeImagePath)
        if not len(svsAvailableLevels):
            raise TileSourceException('OpenSlide image size is invalid.')
        self.sizeX = svsAvailableLevels[0]['width']
        self.sizeY = svsAvailableLevels[0]['height']
        if (self.sizeX != self._openslide.dimensions[0] or
                self.sizeY != self._openslide.dimensions[1]):
            msg = ('OpenSlide reports a dimension of %d x %d, but base layer '
                   'has a dimension of %d x %d -- using base layer\'s '
                   'dimensions.' % (
                       self._openslide.dimensions[0],
                       self._openslide.dimensions[1], self.sizeX, self.sizeY))
            config.getConfig('logger').info(msg)

        self._getTileSize()

        self.levels = int(math.ceil(max(
            math.log(float(self.sizeX) / self.tileWidth),
            math.log(float(self.sizeY) / self.tileHeight)) / math.log(2))) + 1
        if self.levels < 1:
            raise TileSourceException(
                'OpenSlide image must have at least one level.')
        self._svslevels = []
        # Precompute which SVS level should be used for our tile levels.  SVS
        # level 0 is the maximum resolution.  The SVS levels are in descending
        # resolution and, we assume, are powers of two in scale.  For each of
        # our levels (where 0 is the minimum resolution), find the lowest
        # resolution SVS level that contains at least as many pixels.  If this
        # is not the same scale as we expect, note the scale factor so we can
        # load an appropriate area and scale it to the tile size later.
        maxSize = 16384   # This should probably be based on available memory
        for level in range(self.levels):
            levelW = max(1, self.sizeX / 2 ** (self.levels - 1 - level))
            levelH = max(1, self.sizeY / 2 ** (self.levels - 1 - level))
            # bestlevel and scale will be the picked svs level and the scale
            # between that level and what we really wanted.  We expect scale to
            # always be a positive integer power of two.
            bestlevel = svsAvailableLevels[0]['level']
            scale = 1
            for svslevel in range(len(svsAvailableLevels)):
                if (svsAvailableLevels[svslevel]['width'] < levelW - 1 or
                        svsAvailableLevels[svslevel]['height'] < levelH - 1):
                    break
                bestlevel = svsAvailableLevels[svslevel]['level']
                scale = int(round(svsAvailableLevels[svslevel]['width'] / levelW))
            # If there are no tiles at a particular level, we have to read a
            # larger area of a higher resolution level.  If such an area would
            # be excessively large, we could have memroy issues, so raise an
            # error.
            if (self.tileWidth * scale > maxSize or
                    self.tileHeight * scale > maxSize):
                msg = ('OpenSlide has no small-scale tiles (level %d is at %d '
                       'scale)' % (level, scale))
                config.getConfig('logger').info(msg)
                raise TileSourceException(msg)
            self._svslevels.append({
                'svslevel': bestlevel,
                'scale': scale
            })

    def _getTileSize(self):
        """
        Get the tile size.  The tile size isn't in the official openslide
        interface documentation, but every example has the tile size in the
        properties.  If the tile size has an excessive aspect ratio or isn't
        set, fall back to a default of 256 x 256.  The read_region function
        abstracts reading the tiles, so this may be less efficient, but will
        still work.
        """
        # Try to read it, but fall back to 256 if it isn't set.
        width = height = 256
        try:
            width = int(self._openslide.properties[
                'openslide.level[0].tile-width'])
        except (ValueError, KeyError):
            pass
        try:
            height = int(self._openslide.properties[
                'openslide.level[0].tile-height'])
        except (ValueError, KeyError):
            pass
        # If the tile size is too small (<4) or wrong (<=0), use a default value
        if width < 4:
            width = 256
        if height < 4:
            height = 256
        # If the tile has an excessive aspect ratio, use default values
        if max(width, height) / min(width, height) >= 4:
            width = height = 256
        # Don't let tiles be bigger than the whole image.
        self.tileWidth = min(width, self.sizeX)
        self.tileHeight = min(height, self.sizeY)

    def _getAvailableLevels(self, path):
        """
        Some SVS files (notably some NDPI variants) have levels that cannot be
        read.  Get a list of levels, check that each is at least potentially
        readable, and return a list of these sorted highest-resolution first.

        :param path: the path of the SVS file.  After a failure, the file is
            reopened to reset the error state.
        :returns: levels.  A list of valid levels, each of which is a
            dictionary of level (the internal 0-based level number), width, and
            height.
        """
        levels = []
        svsLevelDimensions = self._openslide.level_dimensions
        for svslevel in range(len(svsLevelDimensions)):
            try:
                self._openslide.read_region((0, 0), svslevel, (1, 1))
                level = {
                    'level': svslevel,
                    'width': svsLevelDimensions[svslevel][0],
                    'height': svsLevelDimensions[svslevel][1],
                }
                if level['width'] > 0 and level['height'] > 0:
                    # add to the list so that we can sort by resolution
                    levels.append((level['width'] * level['height'], level))
            except openslide.lowlevel.OpenSlideError:
                self._openslide = openslide.OpenSlide(path)
        # sort highest resolution first.
        levels = [entry[-1] for entry in sorted(levels, reverse=True, key=lambda lt: lt[0])]
        # Discard levels that are not a power-of-two compared to the highest
        # resolution level.
        levels = [entry for entry in levels if
                  nearPowerOfTwo(levels[0]['width'], entry['width']) and
                  nearPowerOfTwo(levels[0]['height'], entry['height'])]
        return levels

    def getNativeMagnification(self):
        """
        Get the magnification at a particular level.

        :return: magnification, width of a pixel in mm, height of a pixel in mm.
        """
        try:
            mag = self._openslide.properties[
                openslide.PROPERTY_NAME_OBJECTIVE_POWER]
            mag = float(mag) if mag else None
        except (KeyError, ValueError, openslide.lowlevel.OpenSlideError):
            mag = None
        try:
            mm_x = float(self._openslide.properties[
                openslide.PROPERTY_NAME_MPP_X]) * 0.001
            mm_y = float(self._openslide.properties[
                openslide.PROPERTY_NAME_MPP_Y]) * 0.001
        except Exception:
            mm_x = mm_y = None
        # Estimate the magnification if we don't have a direct value
        if mag is None and mm_x is not None:
            mag = 0.01 / mm_x
        return {
            'magnification': mag,
            'mm_x': mm_x,
            'mm_y': mm_y,
        }

    @methodcache()
    def getTile(self, x, y, z, pilImageAllowed=False, **kwargs):
        if z < 0:
            raise TileSourceException('z layer does not exist')
        try:
            svslevel = self._svslevels[z]
        except IndexError:
            raise TileSourceException('z layer does not exist')
        # When we read a region from the SVS, we have to ask for it in the
        # SVS level 0 coordinate system.  Our x and y is in tile space at the
        # specifed z level, so the offset in SVS level 0 coordinates has to be
        # scaled by the tile size and by the z level.
        scale = 2 ** (self.levels - 1 - z)
        offsetx = x * self.tileWidth * scale
        if not (0 <= offsetx < self.sizeX):
            raise TileSourceException('x is outside layer')
        offsety = y * self.tileHeight * scale
        if not (0 <= offsety < self.sizeY):
            raise TileSourceException('y is outside layer')
        # We ask to read an area that will cover the tile at the z level.  The
        # scale we computed in the __init__ process for this svs level tells
        # how much larger a region we need to read.
        try:
            tile = self._openslide.read_region(
                (offsetx, offsety), svslevel['svslevel'],
                (self.tileWidth * svslevel['scale'],
                 self.tileHeight * svslevel['scale']))
        except openslide.lowlevel.OpenSlideError as exc:
            raise TileSourceException(
                'Failed to get OpenSlide region (%r).' % exc)
        # Always scale to the svs level 0 tile size.
        if svslevel['scale'] != 1:
            tile = tile.resize((self.tileWidth, self.tileHeight),
                               PIL.Image.LANCZOS)
        return self._outputTile(tile, 'PIL', x, y, z, pilImageAllowed, **kwargs)

    def getPreferredLevel(self, level):
        """
        Given a desired level (0 is minimum resolution, self.levels - 1 is max
        resolution), return the level that contains actual data that is no
        lower resolution.

        :param level: desired level
        :returns level: a level with actual data that is no lower resolution.
        """
        level = max(0, min(level, self.levels - 1))
        scale = self._svslevels[level]['scale']
        while scale > 1:
            level += 1
            scale /= 2
        return level

    def getAssociatedImagesList(self):
        """
        Get a list of all associated images.

        :return: the list of image keys.
        """
        try:
            return sorted(self._openslide.associated_images)
        except openslide.lowlevel.OpenSlideError:
            return []

    def _getAssociatedImage(self, imageKey):
        """
        Get an associated image in PIL format.

        :param imageKey: the key of the associated image.
        :return: the image in PIL format or None.
        """
        try:
            if imageKey in self._openslide.associated_images:
                return self._openslide.associated_images[imageKey]
        except openslide.lowlevel.OpenSlideError:
            pass
        return None
