# MSTS .s -> OBJ Converter

A small desktop app that converts an uncompressed MSTS / Open Rails `.s`
shape file into a Wavefront `.obj` (+ `.mtl`) that Blender, 3ds Max, etc.
can open directly.

This is the same conversion logic used earlier on the KRG WAP7 locomotive
(77,873 points / 146,121 triangles / 49 sub-objects), now wrapped in a
small GUI and hardened for large files: geometry is streamed straight to
disk instead of being held in memory, so peak RAM use stays low
(~265 MB measured on that 115 MB shape file) regardless of model size.

## What's in this folder

| File                    | Purpose                                                  |
|--------------------------|-----------------------------------------------------------|
| `app.py`                 | The GUI (Open / Export / Exit buttons)                    |
| `s_converter.py`         | The actual `.s` parser / `.obj` writer - all the real work |
| `requirements.txt`       | Python dependency (just `numpy`)                          |
| `build.bat`               | Double-click this on Windows to build the standalone .exe |
| `BUILD_EXE.txt`          | Step-by-step build instructions (also covers manual build)|
| `CONVERSION_TEST_LOG.txt`| Real console output from testing against the WAP7 file    |

## Quick start (no .exe yet, just running with Python)

```
pip install -r requirements.txt
python app.py
```

## Building the standalone Windows .exe

**No Python on your PC? No problem.** See `BUILD_EXE.txt` Option A -
upload this folder to a free GitHub repo and a GitHub-hosted Windows
machine builds the .exe for you; you just download the finished file.

If you do have/want Python locally: install it (tick "Add to PATH"),
put all these files in one folder, double-click `build.bat`. Your
`.exe` appears in `dist\`.

## Using the app

1. **Open .s File...** - pick an uncompressed MSTS/Open Rails `.s` shape.
   (If it's compressed, it needs decompressing first - this tool only
   reads the plain-text "uncompressed" variant, same as before.)
2. **Export as .obj...** - choose where to save. Conversion runs in the
   background with a progress bar and live log, so the window stays
   responsive even on very large files.
3. **Exit** - closes the app. If a conversion is still running it'll ask
   first.

The exported `.mtl` references textures as `<name>.dds` (matching the
workflow used previously). If your textures are `.tga` or another
format instead, open the `.mtl` in a text editor and adjust the
extension on the `map_Kd` lines, or just relink the textures inside
Blender after import - either takes a few seconds.

## What it does NOT do

- No animation/skeleton export - each part's geometry is baked into a
  single static rest pose (same simplification as before).
- Only the first (highest-detail) LOD is exported if the shape has more
  than one.
- Compressed `.s` files aren't supported (only the uncompressed
  text-based SIMISA format).

## Why there's no .exe already in this download

I built and tested this from a sandboxed Linux environment with no
internet access and no Windows/cross-compiler toolchain, so I can't
produce a genuine Windows binary myself. `build.bat` does it for you in
about a minute on your own machine, which also means it's actually
been compiled and run on real Windows rather than guessed at - the more
trustworthy way to get this.
