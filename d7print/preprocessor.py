from time import time

from d7print.image_mapper import ImageMapper
from d7print.ruleset import Ruleset


class Preprocessor:
    """Rule-based G-code generator.
    Creates a flexible model printing program based on a set of rules specifying speeds, timings and layer images
    depending on printing position. See Format.md for the details on rule and layer specification."""

    def __init__(self):
        self._image_mapper: ImageMapper = ImageMapper()
        self._ruleset: Ruleset = Ruleset()
        self._cfg_version = 1  # increment this value when a new rule is added

    def set_image_pack(self, image_pack_path: str):
        self._image_mapper.set_image_pack(image_pack_path)

    def get_cfg(self) -> list[str]:
        """Returns a list of all rules, layers and supports in text format."""
        result = []
        result.extend('@rule ' + x for x in self._ruleset.get_rule_specs())
        result.extend('@layer ' + x for x in self._image_mapper.get_layer_specs())
        result.extend('@support ' + x for x in self._image_mapper.get_support_specs())
        return result

    def get_cfg_version(self):
        """Gets current config version (incremented on every rule, layer or support update)."""
        return self._cfg_version

    def preprocess_line(self, line: str) -> list[str]:
        """Take a line and replace it with a preprocessed one (or potentially many). See details in Format.md."""
        ln = line.strip()
        if not ln.startswith('@'):  # Not a preprocessor directive - do not change
            return [line]

        result = ['; ' + line]  # First - add a comment with the original directive

        directive, _, args = ln.partition(' ')  # args are always separated by whitespace
        dl = directive.lower()

        try:
            if dl == '@layer':
                if args.strip().lower() == 'clear':
                    self._image_mapper.clear()
                else:
                    self._image_mapper.add_layer(args)  # image mapper will parse the args
                self._cfg_version += 1
            elif dl == '@support':
                self._image_mapper.add_support(args)  # image mapper will parse the args
                self._cfg_version += 1
            elif dl == '@rule':
                if args.strip().lower() == 'clear':
                    self._ruleset.clear()
                else:
                    self._ruleset.add_rule(args)  # ruleset will parse the args
                self._cfg_version += 1
            elif dl == '@print':
                result.extend(self._print(args))  # output the printing program
            elif dl == '@preview':
                result.extend(';; ' + cmd for cmd in self._print(args))  # output commented-out printing program
            else:
                raise ValueError(f'Unknown directive "{dl}"')
            return result
        except Exception as e:
            raise ValueError(f'Failed to preprocess {line}: {e}')

    def _print(self, args) -> list[str]:
        """Generate the printing program. The only parameter is the starting layer number (starting from 1)."""
        try:
            layer = int(args.strip())
        except ValueError:
            raise ValueError(f'Invalid print argument - {args}')
        if layer < 1:
            layer = 1

        try:
            result = ['! ; HOLD before printing']  # Always pause before actually printing
            while True:  # repeat until there are more layers to print
                rule = self._ruleset.get_layer_rule(layer)
                image = self._image_mapper.get_layer(rule.z)
                support = self._image_mapper.get_support(rule.z)
                if image is False:
                    return result

                result.append(f';###### Layer {layer} @ {rule.z / 1000:.2f}mm #####')
                for z, f in rule.build_feed_down():  # add feed-down commands (multiple for decelerated movement)
                    result.append(f'G1 F{f} Z{z / 1000:.2f}')
                # Pause to allow resin to escape. Prefer G4 to delay because of perfect sync with the previous G1.
                result.append(f'G4 P{rule.time_before / 1000:.1f}')
                result.append(f'preload {image}')  # preload the image while moving and waiting
                result.append(f'slice')  # display it after the wait is over
                result.append(f'M3')  # LED on
                # Wait for the layer exposure.
                # Prefer delay to G4 because M3 will resul in a wait for ok response.
                result.append(f'delay {rule.time_expose}')
                if support and rule.time_support > 0:
                    result.append(f'preload {support}')  # preload the support image during the delay
                    result.append(f'slice')  # display it immediately after the delay
                    result.append(f'delay {rule.time_support}')  # wait for support exposure period
                result.append(f'M5')  # LED off
                result.append(f'delay {rule.time_after}')  # wait for the resin to stabilize
                for z, f in rule.build_feed_up():  # add feed-up commands (multiple for accelerated movement)
                    result.append(f'G1 F{f} Z{z / 1000:.2f}')

                layer += 1
        except Exception as e:
            raise ValueError(f'Failed to preprocess layer #{layer}: {e}')
