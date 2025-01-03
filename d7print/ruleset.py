import math
import re
from itertools import zip_longest

from d7print.utils import float_x1000

MAX_LAYER = 99999
_KNOWN_DATA = ('l', 'z', 'fd', 'adh', 'adn', 'fu', 'auh', 'aun', 'hl', 'hr', 'tb', 'te', 'ts', 'ta')
_X1_DATA = ('l', 'fd', 'fu', 'adn', 'aun')
_MATCHER_DATA = ('l', 'z')


class Ruleset:

    def __init__(self):
        self._directives: dict[int, RuleDirective] = {}
        self._layer_positions: list[int] = [0]

    def get_rule_specs(self) -> list[str]:
        return [r.spec for r in self._directives.values()]

    def clear(self):
        self._directives.clear()
        self._layer_positions = [0]

    def add_rule(self, args: str):
        directive = RuleDirective(args)
        self._directives[directive.prio] = directive
        self._directives = dict(sorted(self._directives.items()))
        self._layer_positions = [0]

    def get_layer_rule(self, layer: int) -> 'CombinedRule':
        z = self._get_position(layer)
        result = CombinedRule(layer, z)
        for d in self._directives.values():
            if d.z.is_empty() and d.l.matches(layer):
                result.add_rule(d, d.l.fraction(layer))
            elif d.l.is_empty() and d.z.matches(z):
                result.add_rule(d, d.z.fraction(z))
        return result

    def _get_position(self, layer: int):
        if layer > MAX_LAYER:
            raise ValueError(f"Requested layer number #{layer} is higher than max supported {MAX_LAYER}")

        z = self._layer_positions[-1]
        if layer >= len(self._layer_positions):
            for i in range(len(self._layer_positions), layer + 1):
                h = None
                for d in self._directives.values():
                    if d.l.matches(i):
                        h = d.hl.compute(d.l.fraction(i), h)
                if h is None:
                    raise ValueError(f"Could not determine the height of layer #{i}")
                z += h
                self._layer_positions.append(z)
        return self._layer_positions[layer]


class CombinedRule:
    def __init__(self, layer: int, z: int):
        self._layer = layer
        self._z = z
        self._data = dict((arg, RangeExpr()) for arg in _KNOWN_DATA if arg not in _MATCHER_DATA)
        self._fractions = dict((arg, 0.0) for arg in _KNOWN_DATA if arg not in _MATCHER_DATA)

    def add_rule(self, rule: 'RuleDirective', fraction: float):
        for arg in self._data.keys():
            if rule[arg].is_present():
                self._data[arg] = rule[arg]
                self._fractions[arg] = fraction

    @property
    def z(self):
        return self._z

    @property
    def time_before(self) -> int:
        return self.get('tb')

    @property
    def time_expose(self) -> int:
        return self.get('te')

    @property
    def time_support(self) -> int:
        return self.get('ts')

    @property
    def time_after(self) -> int:
        return self.get('ta')

    def build_feed_down(self):
        feed = self._data['fd']
        if not feed.is_range():
            return [(self._z, self.get('fd'))]

        accel_points = self.get('adn')
        accel_dist = self.get('adh')
        accel_dist_max = max(self._data['adh'].low, self._data['adh'].high, 1)
        start = self._z + accel_dist if accel_points > 0 else self._z
        step = accel_dist / max(accel_points, 1)
        result = []
        for i in range(0, accel_points + 1):
            offset = step * i
            z = round(start - offset)
            f = feed.compute((accel_dist_max - offset) / accel_dist_max, feed.low)
            result.append((z, f))

        return result

    def build_feed_up(self):
        retract_height = self.get('hr')
        feed = self._data['fu']
        if not feed.is_range():
            return [(self._z + retract_height, self.get('fu'))]

        accel_points = self.get('aun')
        accel_dist = self.get('auh')
        accel_dist_max = max(self._data['auh'].low, self._data['auh'].high, 1)
        step = accel_dist / max(accel_points, 1)
        result = []
        for i in range(0, accel_points):
            offset = step * i
            z = round(self._z + offset + step)
            f = feed.compute((accel_dist_max - accel_dist + offset) / accel_dist_max, feed.low)
            result.append((z, f))
        result.append((self._z + retract_height, feed.high))

        return result

    def get(self, arg) -> int:
        result = self._data[arg].compute(self._fractions[arg], None)
        if result is None or result < 0:
            raise ValueError(f'Failed to determine parameter {arg} for layer {self._layer}')
        return result


class RuleDirective:
    def __init__(self, spec: str):
        self.spec = spec
        spec_list = spec.lower().split()
        self.prio = spec_list.pop(0) if spec_list else ''
        try:
            self.prio = int(self.prio)
        except ValueError:
            raise ValueError(f'Invalid rule priority: "{self.prio}"')

        self._data = dict((arg, RangeExpr()) for arg in _KNOWN_DATA)

        for arg, val in zip_longest(spec_list[::2], spec_list[1::2]):
            if arg in _KNOWN_DATA:
                self._data[arg] = RangeExpr(val, arg not in _X1_DATA)
            else:
                raise ValueError(f'Unknown argument: {arg}')

        if self.z.is_present() and (self.l.is_present() or self.hl.is_present()):
            raise ValueError('Positional rule can not reference layer number or layer height')

        val_range = any(v.is_range() for k, v in self._data.items() if k not in _MATCHER_DATA)
        if not self.l.is_range() and not self.z.is_range() and val_range:
            raise ValueError('Value range can not be used without layer or position range')

    def __getitem__(self, item):
        return self._data[item]

    def __getattribute__(self, name):
        if name in _KNOWN_DATA:
            return self._data[name]
        return super().__getattribute__(name)


class RangeExpr:
    def __init__(self, spec: str = '', x1000: bool = True):
        vals = re.split('([~-])', spec, maxsplit=1)
        if len(vals) == 3:
            self.low = vals[0].strip()
            self.mode = vals[1]
            self.high = vals[2].strip()
        elif spec.strip():
            self.low = vals[0].strip()
            self.mode = '='
            self.high = self.low
        else:
            self.low = 0
            self.mode = ''
            self.high = 0

        if x1000:
            self.low = float_x1000(self.low)
            self.high = float_x1000(self.high)
        else:
            self.low = round(float(self.low))
            self.high = round(float(self.high))

        if self.low < 0 or self.high < 0:
            raise ValueError('Print parameter can not be negative')

    def is_present(self):
        return not self.is_empty()

    def is_empty(self):
        return self.mode == ''

    def is_range(self):
        return self.low != self.high and self.mode != '='

    def matches(self, value: int):
        return self.is_empty() or self.low <= value <= self.high

    def compute(self, fraction: float, default: int | None) -> int | None:
        if self.is_empty():
            return default
        if self.mode == '-':
            return round(self.low + (self.high - self.low) * fraction)
        if self.mode == '~' and self.low > 0 and self.high > 0:
            log = math.log(self.high, self.low)
            return round(self.low ** ((log - 1.0) * fraction + 1.0))
        return self.low

    def fraction(self, value: int):
        if value <= self.low:
            return 0.0
        if value >= self.high:
            return 1.0
        return (value - self.low) / (self.high - self.low)
