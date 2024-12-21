import os
from zipfile import ZipFile

import numpy as np
from PIL import Image


class Display:

    def __init__(self, pack_dir: str, fb_device: str):
        self._fb_device = fb_device
        self._image_pack_dir = pack_dir
        self._image_pack_file: str = ''
        self._img_mask = self._load_image('mask.png', os.path.dirname(os.path.abspath(__file__)), '')
        self._preload_buf = self._black()
        self._preload_name = ''

    def set_image_pack(self, image_pack_path: str):
        self._image_pack_file = image_pack_path

    def get_image_pack(self) -> str:
        return self._image_pack_file

    def blank(self):
        self._black().tofile(self._fb_device)

    def preload(self, image_name: str):
        if image_name and image_name != self._preload_name:
            img = self._load_image(image_name, self._image_pack_dir, self._image_pack_file)
            if img.shape != self._img_mask.shape:
                raise ValueError(f'Image shape {img.shape} does not match expected {self._img_mask.shape}')
            self._preload_buf = np.multiply(img, self._img_mask, dtype='uint32') // 255 * 0x00010101
            self._preload_name = image_name

    def show(self, image_name: str):
        self.preload(image_name)
        self._preload_buf.tofile(self._fb_device)

    @staticmethod
    def _image_to_array_8(img: Image.Image) -> np.ndarray:
        return np.array(img.getchannel(0), dtype='uint8')

    def _black(self):
        return np.zeros(self._img_mask.shape, dtype='uint32')

    def _load_image(self, image_name: str, directory: str, pack_file: str) -> np.ndarray:
        if pack_file:
            with ZipFile(f'{directory}/{pack_file}') as zf:
                if image_name in zf.namelist():
                    with zf.open(image_name) as zi, Image.open(zi) as i:
                        return self._image_to_array_8(i)
        with Image.open(f'{directory}/{image_name}') as i:
            return self._image_to_array_8(i)
