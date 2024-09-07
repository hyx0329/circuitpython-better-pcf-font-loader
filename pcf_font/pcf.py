from .cache import LruCache

try:
    from typing import Union, Tuple, Iterable, Dict
    from io import FileIO
except ImportError:
    pass

try:
    from micropython import const
except ImportError:

    def const(x):
        x


import gc
import struct
from displayio import Bitmap as DisplayioBitmap
from fontio import Glyph
from collections import namedtuple

# need this for easier handling of different padding schemes
from bitmaptools import readinto as _bitmap_readinto


_PCF_TABLETYPE_ACCELERATORS = const(1 << 1)
_PCF_TABLETYPE_METRICS = const(1 << 2)
_PCF_TABLETYPE_BITMAPS = const(1 << 3)
_PCF_TABLETYPE_BDFENCODINGS = const(1 << 5)
_PCF_TABLETYPE_BDFACCELERATORS = const(1 << 8)
_PCF_BYTE_MASK = const(1 << 2)
_PCF_BIT_MASK = const(1 << 3)
_PCF_SCAN_UNIT_MASK = const(3 << 4)
_PCF_GLYPH_PAD_MASK = const(3 << 0)
_PCF_COMPRESSED_METRICS = const(0x00000100)
_PCF_ACCEL_W_INKBOUNDS = const(0x00000100)


TableTocEntry = namedtuple("TableTocEntry", ("format", "size", "offset"))
MetricsEntry = namedtuple(
    "MetricsEntry",
    (
        "left_side_bearing",
        "right_side_bearing",
        "character_width",
        "character_ascent",
        "character_descent",
        "character_attributes",
    ),
)


def read_values(f: FileIO, format_: str) -> Tuple:
    size = struct.calcsize(format_)
    return struct.unpack(format_, f.read(size))


def bytes_per_row(width: int, bytes_align: int) -> int:
    unit_align_bits = bytes_align * 8
    # div floor
    block_count = (width + unit_align_bits - 1) // unit_align_bits
    return block_count * bytes_align


def read_metrics_entry_standard(f: FileIO) -> MetricsEntry:
    return MetricsEntry(*read_values(f, ">5hH"))


def read_metrics_entry_compressed(f: FileIO) -> MetricsEntry:
    (
        left_side_bearing,
        right_side_bearing,
        character_width,
        character_ascent,
        character_descent,
    ) = read_values(f, ">5B")
    left_side_bearing -= 0x80
    right_side_bearing -= 0x80
    character_width -= 0x80
    character_ascent -= 0x80
    character_descent -= 0x80
    attributes = 0
    return MetricsEntry(
        left_side_bearing,
        right_side_bearing,
        character_width,
        character_ascent,
        character_descent,
        attributes,
    )


class PcfFont:
    def __init__(self, filename: str, capacity: int = 128) -> None:
        self._file = open(filename, "rb")
        self._file.seek(0)
        # verify file magic/header
        if b"\x01fcp" != self._file.read(4):
            raise ValueError("Magic mismatch, unknown font file")

        (tables_count,) = read_values(self._file, "<I")
        tables: Dict[TableTocEntry] = dict()
        # read all necessary tables(toc entries only)
        necessary_tables_types = (
            _PCF_TABLETYPE_ACCELERATORS,
            _PCF_TABLETYPE_METRICS,
            _PCF_TABLETYPE_BITMAPS,
            _PCF_TABLETYPE_BDFENCODINGS,
            _PCF_TABLETYPE_BDFACCELERATORS,
        )
        for _ in range(tables_count):
            type_, format_, size, offset = read_values(self._file, "<IIII")
            if type_ in necessary_tables_types:
                tables[type_] = TableTocEntry(format_, size, offset)
        if len(tables) < 4:  # either bdf_accel or accel can be missed
            raise ValueError("Corrupted font data")
        # check data formats
        for entry in tables.values():
            if entry.format & (_PCF_BYTE_MASK | _PCF_BIT_MASK) != (
                _PCF_BYTE_MASK | _PCF_BIT_MASK
            ):
                raise ValueError("Only support MSByte data and MSBit glyph")
        # check bitmaps format
        if tables[_PCF_TABLETYPE_BITMAPS].format & _PCF_SCAN_UNIT_MASK != 0:
            raise ValueError("Only support bits stored in bytes")
        self._glyph_padding = 2 ** (
            tables[_PCF_TABLETYPE_BITMAPS].format & _PCF_GLYPH_PAD_MASK
        )

        # process all necessary tables, check formats at the same time

        # Bitmaps table
        self._file.seek(
            tables[_PCF_TABLETYPE_BITMAPS].offset + 4
        )  # skip 4 bytes format field
        (glyph_count,) = read_values(self._file, ">I")

        # Metrics table
        self._file.seek(tables[_PCF_TABLETYPE_METRICS].offset + 4)
        metrics_compressed = (
            tables[_PCF_TABLETYPE_METRICS].format & _PCF_COMPRESSED_METRICS > 0
        )
        (metrics_count,) = (
            read_values(self._file, ">H")
            if metrics_compressed
            else read_values(self._file, ">I")
        )
        if metrics_count != glyph_count:
            raise ValueError("Corrupted font data")
        self._metrics_compressed = metrics_compressed

        # Encoding table
        # TODO: use default_char as fallback?
        self._file.seek(tables[_PCF_TABLETYPE_BDFENCODINGS].offset + 4)
        (
            self._min_byte2,
            self._max_byte2,
            self._min_byte1,
            self._max_byte1,
            self._default_char,
        ) = read_values(self._file, ">hhhhh")

        # Accelerators table
        acc_table = (
            tables.get(_PCF_TABLETYPE_ACCELERATORS)
            if _PCF_TABLETYPE_ACCELERATORS in tables
            else tables.get(_PCF_TABLETYPE_BDFACCELERATORS)
        )
        self._file.seek(acc_table.offset + 4 + 8)
        self._ascent, self._descent = read_values(self._file, ">ii")
        if acc_table.format & _PCF_ACCEL_W_INKBOUNDS > 0:
            # has ink_minbounds and ink_maxbounds, use them instead of minbounds and maxbounds
            self._file.seek(acc_table.offset + 4 + 8 + 4 + 4 + 4 + 24)
        else:
            self._file.seek(acc_table.offset + 4 + 8 + 4 + 4 + 4)
        minbounds = read_metrics_entry_standard(self._file)
        maxbounds = read_metrics_entry_standard(self._file)
        width = maxbounds.right_side_bearing - minbounds.left_side_bearing
        height = maxbounds.character_ascent + maxbounds.character_descent

        self._bounding_box = (
            width,
            height,
            minbounds.left_side_bearing,
            -maxbounds.character_descent,
        )
        self._bitmap_position_lut_location = (
            tables[_PCF_TABLETYPE_BITMAPS].offset + 4 + 4
        )
        self._bitmap_data_location = (
            self._bitmap_position_lut_location + (glyph_count + 4) * 4
        )
        self._metrics_data_location = (
            tables[_PCF_TABLETYPE_METRICS].offset + 4 + (2 if metrics_compressed else 4)
        )
        self._encoded_glyph_indices_location = (
            tables[_PCF_TABLETYPE_BDFENCODINGS].offset + 4 + 5 * 2
        )

        # finally, prepare the cache
        self._cache = LruCache(capacity)

    def load_glyphs(self, code_points: Union[int, str, Iterable[int]]) -> None:
        """Loads displayio.Glyph objects into the cache."""
        if isinstance(code_points, int):
            code_points = (code_points,)
        elif isinstance(code_points, str):
            code_points = [ord(c) for c in code_points]

        # only load absent code points, in order
        code_points = sorted(c for c in code_points if not self._cache.contains(c))
        if not code_points:
            return

        # implied de-duplication here :)
        # char order is preserved somehow
        glyphs_indices = {
            c: index for c in code_points if (index := self._get_glyph_index(c)) >= 0
        }
        if not glyphs_indices:
            return

        bitmaps_offsets = [
            self._get_glyph_bitmap_offset(i) for i in glyphs_indices.values()
        ]
        char_metrics = [self._get_metrics(i) for i in glyphs_indices.values()]

        gc.collect()

        # batch creating bitmaps
        bitmaps = [None] * len(glyphs_indices)
        for i, (metrics, code_point) in enumerate(
            zip(char_metrics, glyphs_indices.keys())
        ):
            width = metrics.right_side_bearing - metrics.left_side_bearing
            height = metrics.character_ascent + metrics.character_descent
            bitmap = bitmaps[i] = DisplayioBitmap(width, height, 2)
            self._cache.put(
                code_point,
                Glyph(
                    bitmap,
                    0,
                    width,
                    height,
                    metrics.left_side_bearing,
                    -metrics.character_descent,
                    metrics.character_width,
                    0,
                ),
            )

        for bmp_offset, bitmap in zip(bitmaps_offsets, bitmaps):
            self._file.seek(self._bitmap_data_location + bmp_offset)
            _bitmap_readinto(
                bitmap,
                self._file,
                bits_per_pixel=1,
                element_size=self._glyph_padding,
                reverse_pixels_in_element=True,  # TODO: add glyph LSBit first support
            )

    def _get_glyph_index(self, code_point: int) -> int:
        # returns the index of the glyph
        # -1 if not available
        enc1 = (code_point >> 8) & 0xFF
        enc2 = code_point & 0xFF
        if not (self._min_byte1 <= enc1 <= self._max_byte1) or not (
            self._min_byte2 <= enc2 <= self._max_byte2
        ):
            return -1  # not available
        index_offset = (enc1 - self._min_byte1) * (
            self._max_byte2 - self._min_byte2 + 1
        ) + (enc2 - self._min_byte2)
        self._file.seek(self._encoded_glyph_indices_location + index_offset * 2)
        (glyph_index,) = read_values(self._file, ">H")
        if glyph_index == 0xFFFF:
            return -1  # not available
        else:
            return glyph_index

    def _get_glyph_bitmap_offset(self, glyph_index: int) -> int:
        self._file.seek(self._bitmap_position_lut_location + glyph_index * 4)
        (offset,) = read_values(self._file, ">I")
        return offset

    def _get_metrics(self, glyph_index) -> MetricsEntry:
        if self._metrics_compressed:
            self._file.seek(self._metrics_data_location + glyph_index * 5)
            return read_metrics_entry_compressed(self._file)
        else:
            self._file.seek(self._metrics_data_location + glyph_index * 12)
            return read_metrics_entry_standard(self._file)

    def get_glyph(self, code_point: int) -> Glyph:
        """Returns a displayio.Glyph for the given code point or None is unsupported."""
        if not self._cache.contains(code_point):
            # load glyph if not found
            self.load_glyphs(code_point)
            gc.collect()
        return self._cache.get(code_point)

    @property
    def ascent(self) -> int:
        """The number of pixels above the baseline of a typical ascender"""
        return self._ascent

    @property
    def descent(self) -> int:
        """The number of pixels below the baseline of a typical descender"""
        return self._descent

    def get_bounding_box(self) -> Tuple[int, int, int, int]:
        """Return the maximum glyph size as a 4-tuple of: width, height, x_offset, y_offset"""
        return self._bounding_box
