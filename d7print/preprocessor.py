from time import time

from d7print.image_mapper import ImageMapper
from d7print.ruleset import Ruleset


class Preprocessor:

    def __init__(self):
        self._image_mapper: ImageMapper = ImageMapper()
        self._ruleset: Ruleset = Ruleset()
        self._cfg_version = 1

    def set_image_pack(self, image_pack_path: str):
        self._image_mapper.set_image_pack(image_pack_path)

    def get_cfg(self) -> list[str]:
        result = []
        result.extend('@rule ' + x for x in self._ruleset.get_rule_specs())
        result.extend('@layer ' + x for x in self._image_mapper.get_layer_specs())
        result.extend('@support ' + x for x in self._image_mapper.get_support_specs())
        return result

    def get_cfg_version(self):
        return self._cfg_version

    def preprocess_line(self, line: str) -> list[str]:
        ln = line.strip()
        if not ln.startswith('@'):
            return [line]

        result = ['; ' + line]

        directive, _, args = ln.partition(' ')
        dl = directive.lower()

        try:
            if dl == '@layer':
                if args.strip().lower() == 'clear':
                    self._image_mapper.clear()
                else:
                    self._image_mapper.add_layer(args)
                self._cfg_version += 1
            elif dl == '@support':
                self._image_mapper.add_support(args)
                self._cfg_version += 1
            elif dl == '@rule':
                if args.strip().lower() == 'clear':
                    self._ruleset.clear()
                else:
                    self._ruleset.add_rule(args)
                self._cfg_version += 1
            elif dl == '@print':
                result.extend(self._print(args))
            elif dl == '@preview':
                result.extend(';; ' + cmd for cmd in self._print(args))
            else:
                raise ValueError(f'Unknown directive "{dl}"')
            return result
        except Exception as e:
            raise ValueError(f'Failed to preprocess {line}: {e}')

    def _print(self, args) -> list[str]:
        try:
            layer = int(args.strip())
        except ValueError:
            raise ValueError(f'Invalid print argument - {args}')
        if layer < 1:
            layer = 1

        try:
            result = ['! ; HOLD before printing']
            while True:
                rule = self._ruleset.get_layer_rule(layer)
                image = self._image_mapper.get_layer(rule.z)
                support = self._image_mapper.get_support(rule.z)
                if image is False:
                    return result

                result.append(f';###### Layer {layer} @ {rule.z / 1000:.2f}mm #####')
                for z, f in rule.build_feed_down():
                    result.append(f'G1 F{f} Z{z / 1000:.2f}')
                result.append(f'G4 P{rule.time_before / 1000:.1f}')
                result.append(f'preload {image}')
                result.append(f'slice')
                result.append(f'M3')
                result.append(f'delay {rule.time_expose}')
                if support and rule.time_support > 0:
                    result.append(f'preload {support}')
                    result.append(f'slice')
                    result.append(f'delay {rule.time_support}')
                result.append(f'M5')
                result.append(f'delay {rule.time_after}')
                for z, f in rule.build_feed_up():
                    result.append(f'G1 F{f} Z{z / 1000:.2f}')

                layer += 1
        except Exception as e:
            raise ValueError(f'Failed to preprocess layer #{layer}: {e}')
