# A better PCF font loader for CircuitPython

PCF is abbreviation for X11 Portable Compiled Font.

There's [Adafruit_CircuitPython_Bitmap_Font](https://github.com/adafruit/Adafruit_CircuitPython_Bitmap_Font) but it does not support glyphs that padded to bytes & chars(2 bytes) and the code is a bit confusing.

Pros:

- cleaner code(I think)
- limited glyph cache
    - useful for fonts with numerous code points(like CJK fonts) so memory won't be exhausted by cache
- support different glyph paddings
    - thus font file can be smaller
- faster(batch load) than Adafruit_CircuitPython_Bitmap_Font
    - 158ms VS 242ms when loading 72 unique CJK characters
    - 173ms VS 341ms when loading 72 unique/125 total CJK characters
    - tested on RP2040, both use the same PCF font, *YMMV though*

Cons:

- only PCF is supported
- may not work on some older CircuitPython
    - needs `bitmaptools.readinto`
- consumes __more__ RAM, possibly
    - about(less than) 1KB with default configuration
    - maybe neglectable for CircuitPython

Example:

```python
import board
from adafruit_display_text import label
from pcf_font import PcfFont

# replace with your own font
font = PcfFont("fonts/fusion-pixel-12px-proportional-zh_hans.pcf")
text = "世界，你好！World, hello!"
text_area = label.Label(font, text=text)
text_area.x = 10
text_area.y = 10
board.DISPLAY.root_group = text_area
```

It's designed to be API compatible, and can replace Adafruit_CircuitPython_Bitmap_Font if only PCF is used.

## License

This project is released under [Unlicense](https://unlicense.org/).
