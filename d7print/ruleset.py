import math
import re
from itertools import zip_longest

from d7print.utils import float_x1000

MAX_LAYER = 99999  # just a protection from potential long loops
# all known rule args
_KNOWN_DATA = ('l', 'z', 'fd', 'adh', 'adn', 'fu', 'auh', 'aun', 'hl', 'hr', 'tb', 'te', 'ts', 'ta')
# these arguments are given directly in internal units - no need to multiply by 1000
_X1_DATA = ('l', 'fd', 'fu', 'adn', 'aun')
# these arguments are matchers used to determine if the rule applies to the specific layer
# they do not specify actual printing parameters
_MATCHER_DATA = ('l', 'z')


class Ruleset:
    """Rule-based printing engine responsible for determining speeds and times based on a layer position.
    See Format.md on rule format description."""

    def __init__(self):
        self._directives: dict[int, RuleDirective] = {}
        self._layer_positions: list[int] = [0]

    def get_rule_specs(self) -> list[str]:
        """Return a list of rules added to this ruleset in text form."""
        return [r.spec for r in self._directives.values()]

    def clear(self):
        """Remove all rules."""
        self._directives.clear()
        self._layer_positions = [0]

    def add_rule(self, args: str):
        """Parse and add the rule to ruleset."""
        directive = RuleDirective(args)
        self._directives[directive.prio] = directive
        self._directives = dict(sorted(self._directives.items()))
        self._layer_positions = [0]

    def get_layer_rule(self, layer: int) -> 'CombinedRule':
        """Compute resulting print rule for the given layer number.
        Note that layers do not always match images 1-to-1.
        Some images might be skipped and others might be used by multiple layers."""
        z = self._get_position(layer)
        result = CombinedRule(layer, z)
        for d in self._directives.values():  # higher-numbered rules have priority
            if d.z.is_empty() and d.l.matches(layer):  # Layer-number rules interpolate based on layer number
                result.add_rule(d, d.l.fraction(layer))
            elif d.l.is_empty() and d.z.matches(z):  # Height-range rules interpolate based on relative height
                result.add_rule(d, d.z.fraction(z))
        return result

    def _get_position(self, layer: int):
        """Compute layer Z-position based on @layer/@support directives."""
        if layer > MAX_LAYER:
            raise ValueError(f"Requested layer number #{layer} is higher than max supported {MAX_LAYER}")

        z = self._layer_positions[-1]
        if layer >= len(self._layer_positions):  # compute layer positions if not done yet
            for i in range(len(self._layer_positions), layer + 1):
                h = None
                for d in self._directives.values():  # check every directive for a match
                    if d.l.matches(i):
                        h = d.hl.compute(d.l.fraction(i), h)  # the last one matching wins
                if h is None:
                    raise ValueError(f"Could not determine the height of layer #{i}")
                z += h
                self._layer_positions.append(z)
        return self._layer_positions[layer]


class CombinedRule:
    """A resulting print rule for some layer."""

    def __init__(self, layer: int, z: int):
        self._layer = layer
        self._z = z
        # data are actual range expressions specified in the source directive
        self._data = dict((arg, RangeExpr()) for arg in _KNOWN_DATA if arg not in _MATCHER_DATA)
        # fraction is the interpolation value in the range of [0.0..1.0]
        # this value is computed differently for layer-based and height-based rules, so it's done at the ruleset level
        self._fractions = dict((arg, 0.0) for arg in _KNOWN_DATA if arg not in _MATCHER_DATA)

    def add_rule(self, rule: 'RuleDirective', fraction: float):
        """Add a new rule to this combined rule. All non-empty rule values overwrite previously stored ones."""
        for arg in self._data.keys():
            if rule[arg].is_present():
                self._data[arg] = rule[arg]
                self._fractions[arg] = fraction

    @property
    def z(self):
        """Layer curing position (micrometers)."""
        return self._z

    @property
    def time_before(self) -> int:
        """Delay before exposure start (ms)."""
        return self.get('tb')

    @property
    def time_expose(self) -> int:
        """Layer exposure time (ms)."""
        return self.get('te')

    @property
    def time_support(self) -> int:
        """Supports exposure time (ms)."""
        return self.get('ts')

    @property
    def time_after(self) -> int:
        """Delay after exposure end (ms)."""
        return self.get('ta')

    def build_feed_down(self):
        """Feed-down profile: a list of (z, f) tuples literally translatable to "G1 F{f} Z{z}"."""
        feed = self._data['fd']
        if not feed.is_range():  # constant feed - only one G1 command
            return [(self._z, self.get('fd'))]

        accel_points = self.get('adn')  # number of acceleration points (interpolated based on layer position)
        accel_dist = self.get('adh')  # acceleration distance (interpolated based on layer position)
        # compute total acceleration distance to interpolate by intermediate platform positions (at least 1um)
        accel_dist_max = max(self._data['adh'].low, self._data['adh'].high, 1)
        # compute the first acceleration point (interpolated accel points may be 0 at the boundary)
        start = self._z + accel_dist if accel_points > 0 else self._z
        step = accel_dist / max(accel_points, 1)  # compute z-step for every acceleration point
        result = []
        for i in range(0, accel_points + 1):  # +1 point for the start of accel profile
            offset = step * i
            z = round(start - offset)  # discard fractions of micrometers
            # interpolate based on platform position
            f = feed.compute((accel_dist_max - offset) / accel_dist_max, feed.low)
            result.append((z, f))

        return result

    def build_feed_up(self):
        """Feed-up profile: a list of (z, f) tuples literally translatable to "G1 F{f} Z{z}"."""
        retract_height = self.get('hr')
        feed = self._data['fu']
        if not feed.is_range():  # constant feed - only one G1 command
            return [(self._z + retract_height, self.get('fu'))]

        accel_points = self.get('aun')  # number of acceleration points (interpolated based on layer position)
        accel_dist = self.get('auh')  # acceleration distance (interpolated based on layer position)
        # compute total acceleration distance to interpolate by intermediate platform positions (at least 1um)
        accel_dist_max = max(self._data['auh'].low, self._data['auh'].high, 1)
        step = accel_dist / max(accel_points, 1)  # compute z-step for every acceleration point
        result = []
        for i in range(0, accel_points):  # +1 point for the retract top point
            offset = step * i
            z = round(self._z + offset + step)  # discard fractions of micrometers
            # interpolate based on platform position
            f = feed.compute((accel_dist_max - accel_dist + offset) / accel_dist_max, feed.low)
            result.append((z, f))
        result.append((self._z + retract_height, feed.high))

        return result

    def get(self, arg) -> int:
        """Gets the interpolated data value based on its fraction."""
        result = self._data[arg].compute(self._fractions[arg], None)
        if result is None or result < 0:
            raise ValueError(f'Failed to determine parameter {arg} for layer {self._layer}')
        return result


class RuleDirective:
    """Parses @rule directive arguments."""

    def __init__(self, spec: str):
        self.spec = spec
        spec_list = spec.lower().split()
        self.prio = spec_list.pop(0) if spec_list else ''  # rule priority always goes first
        try:
            self.prio = int(self.prio)
        except ValueError:
            raise ValueError(f'Invalid rule priority: "{self.prio}"')

        self._data = dict((arg, RangeExpr()) for arg in _KNOWN_DATA)  # prepare an empty range for every known arg

        for arg, val in zip_longest(spec_list[::2], spec_list[1::2]):  # iterate pairs of arg names and values
            if arg in _KNOWN_DATA:
                self._data[arg] = RangeExpr(val, arg not in _X1_DATA)  # parse the range (x1000 for any arg not in _X1)
            else:
                raise ValueError(f'Unknown argument: {arg}')

        if self.z.is_present() and (self.l.is_present() or self.hl.is_present()):
            # layer positions are computed based on l and hl args
            # it's unclear how to compute layer height for rules like "every layer between 1 and 10mm should be 100um"
            raise ValueError('Positional rule can not reference layer number or layer height')

        val_range = any(v.is_range() for k, v in self._data.items() if k not in _MATCHER_DATA)
        if not self.l.is_range() and not self.z.is_range() and val_range:
            # speed or time ranges can only work when coupled with layer or height ranges
            raise ValueError('Value range can not be used without layer or position range')

    def __getitem__(self, item):
        return self._data[item]

    def __getattribute__(self, name):
        if name in _KNOWN_DATA:
            return self._data[name]
        return super().__getattribute__(name)


class RangeExpr:
    """Interpolating value range. Supports linear and logarithmic interpolation. See Format.md for details."""
    def __init__(self, spec: str = '', x1000: bool = True):
        vals = re.split('([~-])', spec, maxsplit=1)
        if len(vals) == 3:  # range
            self.low = vals[0].strip()
            self.mode = vals[1]
            self.high = vals[2].strip()
        elif spec.strip():  # single value
            self.low = vals[0].strip()
            self.mode = '='
            self.high = self.low
        else:  # empty spec
            self.low = 0
            self.mode = ''
            self.high = 0

        if x1000:  # convert seconds to milliseconds and millimeters to micrometers
            self.low = float_x1000(self.low)
            self.high = float_x1000(self.high)
        else:  # use raw values (layer number, feed or acceleration points number)
            self.low = round(float(self.low))
            self.high = round(float(self.high))

        if self.low < 0 or self.high < 0:
            raise ValueError('Print parameter can not be negative')

    def is_present(self):
        """True if this parameter was specified."""
        return not self.is_empty()

    def is_empty(self):
        """True if this parameter was missing."""
        return self.mode == ''

    def is_range(self):
        """True if this parameter was specified as an interpolation range."""
        return self.low != self.high and self.mode != '='

    def matches(self, value: int):
        """True if the given value falls within the interpolation range (or if the range is empty)."""
        return self.is_empty() or self.low <= value <= self.high

    def compute(self, fraction: float, default: int | None) -> int | None:
        """Interpolate the value given a fraction in the range [0.0..1.0] as [low..high]."""
        if self.is_empty():
            return default
        if self.mode == '-':
            return round(self.low + (self.high - self.low) * fraction)
        if self.mode == '~' and self.low > 0 and self.high > 0:
            log = math.log(self.high, self.low)
            return round(self.low ** ((log - 1.0) * fraction + 1.0))
        return self.low

    def fraction(self, value: int):
        """Map the value to a fraction of this range (clamps to [0.0..1.0])."""
        if value <= self.low:
            return 0.0
        if value >= self.high:
            return 1.0
        return (value - self.low) / (self.high - self.low)
