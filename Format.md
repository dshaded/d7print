# Supported file formats and command syntax
## File handling
Printer software accepts the following file formats:
* Zip archives (any extension, e.g. `.cws`) with layer images, optional `manifest.xml` and `*.gcode` files
* Standalone `*.gcode` files 
* Standalone image files (png preferred, must match screen size)

Loading a zip archive sets the context for loading its image files with `slice` and `preload` commands. If the archive contains `manifest.xml` file then its content is used to index the images. Otherwise, all images with numbers in their name are sorted alphanumerically and then indexed. Image indexing starts from 1.

Loading a zip archive also loads the contents of the first found `*.gcode` file to the preview pane unless it starts with the `MAPFILE` header text. In that case the file contents is only used to provide a context for the preprocessor, typically - a list of `@layer` directives mapping each layer to its z-position.

Loading a `*.gcode` file simply sends its contents to the preview pane.

Directly loading an image is not possible. Use `slice` and `preload` commands.

## Supported commands
### Comments
Any text following the `;` character is treated as a comment and ignored by the printer.
### Printer commands
* `reset` - send `^X` "reset" command to GRBL.
* `hwreset` - issue a hardware reset pulse to GRBL board.
* `reboot` - issue reboot command to the host OS.
* `shutdown` or `poweroff` - issue shutdown command to the host OS.
* `delay n` - equivalent of G-code `G4` delay command, but the time `n` is given in milliseconds. Works slightly better with other printer commands as it does not require waiting for an `OK` response from GRBL. 
* `blank` - send a full-black image to the screen.
* `slice image_name.png` - send the specified image to the screen. The image is first looked up in the loaded zip archive and then in the uploads list. If the image name is missing, a previously preloaded image is sent.
* `preload image_name.png` - do the time-consuming image extraction and mask application and cache the result, but do not send it to the screen. **This command is executed in the background** even when the printer is waiting for a response from GRBL (typically from `G4`) or waiting for a `delay` to expire. This allows to do these lengthy computations (hundreds of ms) while moving or waiting. That's especially important for a `slice - delay - preload - slice - delay` sequence of support printing where the preload happens during the delay and the second slice immediately after it.

Pay attention that all these commands except the `preload` wait for the previous GRBL commands to be acknowledged but not necessarily executed. E.g. a sequence of `G1 z10 - delay 1000 - M3` will start waiting for the delay as soon as the printer starts the movement, not finishes it! Use `G4` for synchronization as this command is acknowledged only when it actually finishes waiting.

### Preprocessor directives
Preprocessor allows to generate a printing G-code program based on a set of printing rules and a mapping of sliced images to their z-positions. Current preprocessor configuration is displayed in the lower UI pane (below queue and log panes).

* `@layer clear` - removes all previously stored `@layer` and `@support` entries.
* `@layer 1.20 f image.png` - specifies that any layer positioned below 1.20mm and above any lower `@layer` directive must be mapped to `image.png`. E.g. if this is the only directive in the preprocessor config the printed model will be 1.20mm tall and all of its layers will be printed using `image.png`.
* `@layer 2.42 n 15 s 0.04 i 2 t 100` - allows to map a range of images from a pack file to a range of model heights. Named arguments (n, s, i, t) may be specified in any order:
  * `2.42` (required) - z-position of the first mapped image. E.g. 2.42mm.
  * `n 15` (required) - the index of the first mapped image (starting from 1). E.g. image #15 is located at 2.42mm. See file handling section for the details about image indexing.
  * `s 0.04` (optional, default 0 meaning a single image mapping) - mapping z-step. A distance between sliced images. E.g. the images were expected to be sliced with a step of 40um.
  * `i 2` (optional, default 1) - image index increment. E.g. the next mapped image will be #17 at 2.46mm.
  * `t 100` (optional, default 0 meaning a single image mapping) - last mapped image index. If this number is larger than the number of images in the archive it will be limited to the actual number. E.g. a total of 43 images (15, 17, 19, ... 97, 99) will be mapped with a step of 40um starting from 2.42mm.
* `@support`: same as `@layer`, but specifies additional images used for additional supports exposure. Does not have `clear` form.
* `@rule clear` - clears all previously added rules from the preprocessor config.
* `@rule 20 z 0-0.999 adh 1-0 adn 10-1 fd 5~240 tb 60~1` - defines a printing rule applicable for the specified model height range, layers range or for the whole model. Named arguments can be specified in any order (not all are used in the example line):
  * `20` (required) - rule id. Higher values take precedence over lower ones. E.g `@rule 100 hl 0.1` will completely override `@rule 99 hl 0.04`.
  * `l 2-50` (default - match all, incompatible with `z`) - specifies that this rule applies only to layers from `2` to `50` inclusive.
  * `z 0-0.999` (default - match all, incompatible with `l` and `hl`) - specifies that this rule applies only for layers located between `0mm` and `0.999mm` of model height inclusive.
  * `fd 5~240` and `fu 300` - feed-down and feed-up speeds translated to G-code F command. A range can be used to specify acceleration profile (see note below). 
  * `adh 1-0` and `auh 2.2` - down-deceleration and up-acceleration distances in millimeters (see note below).
  * `adn 10-1` and `aun 5` -  down-deceleration and up-acceleration step numbers (see note below).
  * `hl 0.120` (incompatible with `z`) - layer height in millimeters.
  * `hr 5` - retract height in millimeters.
  * `tb 60~1` - time before exposure. E.g. logarithmically descending from `60` to `1` second.
  * `te 5` - exposure time in seconds.
  * `ts 0.75` - additional supports exposure in seconds.
  * `ta 1.5` - delay before retract move in seconds.
* `@print 5`: adds the generated program to the command queue. The only argument specifies the starting layer (5 in this case). Any value less than 1 is treated as 1.
* `@preview 1`: same as print, but all generated commands are commented-out.

**A note on ranges**: Every named `@rule` argument can accept either a number or a range. No spaces are allowed between the range numbers and `-` (linear) or `~` (logarithmic) symbol. Ranges can be used to match the rule against a number of layers (`l` and `z`) or to vary the value of the parameter based on the layer index or position. `@rule l 1-10 hl 0.12-0.04 te 60~5` will linearly decrease layer height and logarithmically decrease exposure time starting with 0.12mm and 60 seconds for layer 1 and ending with 0.04mm and 5 seconds for layer 10.

**A note on acceleration profiles**: A combination of `fd+adh+adn` or `fu+auh+aun` works with ranges in a specific way. `adh`, `adn`, `auh`, `aun` are interpolated as usual based on the currently printed layer position within the `l` or `z` range. However, `fd` and `fu` are interpolated for the specified number of acceleration points with every point's speed computed individually for its position within the full range.\
E.g. `@rule 20 z 0-0.999 adh 1-0 adn 10-1 fd 5~240` will generate 10 `G1` commands for the lowest layer starting with `G1 F240 Z1.0` and logarithmically decelerating to `G1 F5.0 Z0.04` (for layer height of 0.04). It will generate 5 `G1` instructions for printing a layer at 0.5mm height starting with `G1 F240 Z1.0` (because acceleration height is reduced to 0.5 at this level and number of points reduced to 5) and ending with something like `G1 F38 Z0.5` (because feed is decreasing logarithmically).

### GRBL commands
Any other text lines are sent directly to GRBL. Any error response leads to an immediate halt `!` command being executed. Normally a `$H` homing command must be issued to start working with the printer.
