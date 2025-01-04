import re


def float_x1000(spec: str):
    return round(float(spec) * 1000)


def sorted_alphanum(lst):
    def convert(text):
        return int(text) if text.isdigit() else text

    def make_key(text):
        return [convert(c) for c in re.split('([0-9]+)', text)]

    return sorted(lst, key = make_key)