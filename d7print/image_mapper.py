import re
from bisect import bisect_left
from itertools import zip_longest
from xml.etree.ElementTree import ElementTree
from zipfile import ZipFile

from d7print.utils import float_x1000


class ImageMapper:

    def __init__(self):
        self._image_names: list[str] = ['']  # slice 0 is blank
        self._layers: list[MapDirective] = []
        self._layers_map: list[tuple[int, str]] = []
        self._supports: list[MapDirective] = []
        self._supports_map: list[tuple[int, str]] = []

    def set_image_pack(self, image_pack_path: str):
        self._image_names = ['']
        self._supports_map.clear()
        self._layers_map.clear()

        with (ZipFile(image_pack_path) as zf):
            for name in sorted(zf.namelist()):
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
        return [lr.spec for lr in self._layers]

    def get_support_specs(self) -> list[str]:
        return [sp.spec for sp in self._supports]

    def clear(self):
        self._layers = []
        self._supports = []
        self._supports_map = []
        self._layers_map = []

    def add_layer(self, args: str):
        self._layers.append(MapDirective(args))
        self._layers_map.clear()

    def add_support(self, args: str):
        self._supports.append(MapDirective(args))
        self._supports_map.clear()

    def get_layer(self, z: int) -> str | bool:
        return self._get_entry(z, self._layers, self._layers_map)

    def get_support(self, z: int) -> str | bool:
        return self._get_entry(z, self._supports, self._supports_map)

    def _get_entry(self, z: int, directives: list['MapDirective'], target_map: list[tuple[int, str]]) -> str | bool:
        if not target_map:
            new_map = {}
            for d in directives:
                if d.file:
                    new_map[d.z] = d.file
                else:
                    new_z = d.z
                    for i in range(d.number, min(d.to + 1, len(self._image_names)), d.increment):
                        new_map[new_z] = self._image_names[i]
                        new_z += d.step
            target_map.extend((k, v) for k, v in sorted(new_map.items()))

        index = bisect_left(target_map, (z, ''))
        if index < len(target_map):
            return target_map[index][1]
        else:
            return False


class MapDirective:
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
