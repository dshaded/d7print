import re
from bisect import bisect_left
from itertools import zip_longest
from xml.etree.ElementTree import ElementTree
from zipfile import ZipFile

from d7print.utils import float_x1000, sorted_alphanum


class ImageMapper:
    """Maintains mappings between layer heights, image indexes and image file names.
    Supports separate support image for every layer.
    Layer numbering starts with 1.
    Uses manifest.xml with an explicit id-to-image mapping if available.
    Otherwise, sorts all available images with at least one number in a name in alphanumeric order.
    Slice zero is blank, actual numbering starts from 1.
    See Format.md for the description of height to layer directive format."""

    def __init__(self):
        self._image_names: list[str] = ['']  # slice 0 is blank
        self._layers: list[MapDirective] = []
        self._layers_map: list[tuple[int, str]] = []
        self._supports: list[MapDirective] = []
        self._supports_map: list[tuple[int, str]] = []

    def set_image_pack(self, image_pack_path: str):
        """Load image index to image name mapping from the given pack file. Use manifest.xml if available."""
        self._image_names = ['']
        self._supports_map.clear()
        self._layers_map.clear()

        with (ZipFile(image_pack_path) as zf):
            for name in sorted_alphanum(zf.namelist()):
                if re.fullmatch(r'.*\d.*\.png', name, re.IGNORECASE):
                    self._image_names.append(name)
                elif name.lower() == 'manifest.xml':
                    self._image_names = ['']
                    with zf.open(name, 'r') as manifest:
                        et = ElementTree()
                        et.parse(manifest)
                        for tag in et.iterfind('.//Slice/name'):
                            self._image_names.append(tag.text.strip())
                    break

    def get_layer_specs(self) -> list[str]:
        """Gets a list of string-formatted height to image index mappings for the main layer images. (See Format.md)"""
        return [lr.spec for lr in self._layers]

    def get_support_specs(self) -> list[str]:
        """Gets a list of string-formatted height to image index mappings for the support images. (See Format.md)"""
        return [sp.spec for sp in self._supports]

    def clear(self):
        self._layers = []
        self._supports = []
        self._supports_map = []
        self._layers_map = []

    def add_layer(self, args: str):
        """Add a string-formatted height to image index mapping for the main layer image. (See Format.md)"""
        self._layers.append(MapDirective(args))
        self._layers_map.clear()

    def add_support(self, args: str):
        """Add a string-formatted height to image index mapping for the support image. (See Format.md)"""
        self._supports.append(MapDirective(args))
        self._supports_map.clear()

    def get_layer(self, z: int) -> str | bool:
        """Get a main layer image name for the given height. False if there are no more images left at/above this height."""
        return self._get_entry(z, self._layers, self._layers_map)

    def get_support(self, z: int) -> str | bool:
        """Get a support image name for the given height. False if there are no more images left at/above this height."""
        return self._get_entry(z, self._supports, self._supports_map)

    def _get_entry(self, z: int, directives: list['MapDirective'], target_map: list[tuple[int, str]]) -> str | bool:
        if not target_map:  # cache an ordered list of (height, file name) tuples
            new_map = {}
            for d in directives:
                if d.file:  # single file explicitly specified
                    new_map[d.z] = d.file
                else:  # use "number", "to" and "increment" to map multiple heights to appropriate files
                    new_z = d.z
                    for i in range(d.number, min(d.to + 1, len(self._image_names)), d.increment):
                        new_map[new_z] = self._image_names[i]
                        new_z += d.step
            target_map.extend((k, v) for k, v in sorted(new_map.items()))

        # empty string ensures that we get the index of the matching entry in case of exact height match
        index = bisect_left(target_map, (z, ''))
        if index < len(target_map):
            return target_map[index][1]
        else:
            return False


class MapDirective:
    """Parses layer mapping rules into components. (See Format.md)"""

    def __init__(self, spec: str):
        self.spec = spec
        spec_list = spec.lower().split()
        self.z = float_x1000(spec_list.pop(0))
        self.step = 0
        self.number = 0
        self.increment = 1
        self.to = 0
        self.file = ''

        for arg, val in zip_longest(spec_list[::2], spec_list[1::2]):
            if arg == 's':
                self.step = float_x1000(val)
            elif arg == 'n':
                self.number = int(val)
            elif arg == 'i':
                self.increment = int(val)
            elif arg == 't':
                self.to = int(val)
            elif arg == 'f':
                self.file = val or ''
            else:
                raise ValueError(f'Unknown argument: {arg}')

        if self.z < 0:
            raise ValueError(f'Mapped z value can not be negative')
        if self.file:
            if self.step or self.number or self.to:
                raise ValueError('File name can not be combined with other mapping arguments')
        else:
            if self.step < 0 or self.number < 0 or self.to < 0:
                raise ValueError('Arguments Step, Number, To can not be negative')
            if self.increment < 1:
                raise ValueError('Increment can not be less than 1')
            if self.to < self.number:
                self.to = self.number
