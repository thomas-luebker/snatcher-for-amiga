Snatcher-DataTool -- the game data for the Snatcher Amiga engine, from your own disc
====================================================================================

Runs on a Mac, Linux or Windows PC: Python 3 with numpy (pip install numpy). Unpack the whole
package, then in this drawer:

  python3 mksnatcherdata.py "Snatcher (USA).cue" \
          --bios bios_CD_U.bin --core genesis_plus_gx_libretro.dylib

It writes the game's data into the Snatcher drawer beside this one; that drawer (with
Snatcher.info) is then the complete game -- copy it to the Amiga. --out DIR writes elsewhere.

The disc:  Snatcher (USA), Sega CD, as bin/cue WITH the audio tracks (a redump set, one .bin
           per track, or one .bin for all). A plain .iso lacks the CD audio (intro, title, ending).
--bios     the US Sega CD BIOS (bios_CD_U.bin, Model 1 v1.10 tested), dumped from your console.
--core     Genesis Plus GX for libretro, built for this computer: genesis_plus_gx_libretro
           .dylib (Mac) / .so (Linux) / .dll (Windows), e.g. from a RetroArch installation or
           https://buildbot.libretro.com/nightly/
           The two are only for SNATCHER.MUS, the FM music: the tool boots the disc in the
           emulator and records every song and sound effect the game's own driver plays
           (a few minutes to half an hour). --no-music makes everything else without them.

What it writes:
  SUBCODE.BIN SP*.BIN DATA_*.BIN PCM*.BIN   copied off the disc's data track
  Snatcher.cdda                             the audio tracks, 8-bit for Paula (~62 MB)
  SNATCHER.MUS                              the FM music (~54 MB)
Each file is checked against snatcherdata.sha1, the files of the tested build. A file that
differs is named at the end -- usually another disc image or another core version; the game
may still work. The tool writes no game data anywhere else.
