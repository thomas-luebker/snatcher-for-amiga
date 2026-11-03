# Snatcher for the Amiga

**Snatcher** (Konami, 1994), the English Sega CD version, running **natively on AmigaOS 3.x**. The
game's own code rebuilt for the 68k: no emulator, AGA or RTG, with the CD audio, the FM music and
the voices.

Shown for the first time at **Amiga Meeting Nord, 6–8 November 2026**.

This repository holds the **DataTool**: the script that makes the game's data from **your own
disc**. The Amiga engine itself comes as a download under [Releases](../../releases). Neither
contains anything of Konami's: no code, no pictures, no sound. Without your own Snatcher disc
there is no game.

## What you need

- **Snatcher (USA) for the Sega CD** as a bin/cue image **with its audio tracks** (a redump set:
  one .bin per track, or one .bin for all). A plain .iso lacks the CD audio.
- **The US Sega CD BIOS** (`bios_CD_U.bin`), dumped from your own console. It's only needed for
  the FM music.
- **Genesis Plus GX for libretro**, built for your computer (`genesis_plus_gx_libretro.dylib` /
  `.so` / `.dll`), for example from a RetroArch installation or
  <https://buildbot.libretro.com/nightly/>.
- **Python 3 with numpy** on a Mac, Linux or Windows PC (`pip install numpy`).
- **An Amiga** with AmigaOS 3.1 or later, a 68020 or better and fast RAM. The target is a 68030
  at 25 MHz with 32 MB fast RAM; a 68060 or a PiStorm draws every picture. AGA or an RTG card
  (Picasso96 / CyberGraphX). About 200 MB free on a hard drive or CF card.

## Making the game

1. Download the engine archive from [Releases](../../releases) and unpack it on your Mac or PC. It
   holds the `Snatcher` drawer (the engine, without game data) and `Snatcher-DataTool` (this
   repository's `datatool/`).
2. Open a terminal in `Snatcher-DataTool` and run:

   ```
   python3 mksnatcherdata.py "path/to/Snatcher (USA).cue" \
           --bios path/to/bios_CD_U.bin --core path/to/genesis_plus_gx_libretro.dylib
   ```

   It copies the game's files off the disc's data track, converts the CD audio for Paula and
   records the FM music by playing the disc in Genesis Plus GX (a few minutes up to half an hour).
   Every file is checked against `snatcherdata.sha1`, the files of the tested build; any file that
   differs is named at the end. With a clone of this repository, add `--out path/to/Snatcher`.
3. Copy the `Snatcher` drawer and `Snatcher.info` to the Amiga. The drawer must stay writable,
   because the saves go there. Then double-click one of these:
   - **Snatcher AGA:** a native AGA screen.
   - **Snatcher Fullscreen:** its own 8-bit RTG screen.
   - **Snatcher:** a window on an RTG Workbench.

## Playing

| Key | |
|---|---|
| cursor keys / joystick | move: menus, title, shootout cursor, the keypad and keyboard |
| Space, Z / fire / left mouse button | select, shoot |
| Return / Start | choose on the title, skip the intro, OK when typing |
| X, Backspace / right mouse button | back, delete |
| Shift, A, F1 / second fire button | draw the blaster |
| Esc | quit (asks first) |

The videophone, JORDAN and every question the game asks are typed as on the Mega CD: move the
cursor over the keys, click them, or just type on the keyboard.

## Notes

- *Snatcher* is © Konami. This project is not affiliated with or endorsed by Konami. It is a
  fan port for people who own the original disc.
- The BIOS and the core are only used by the DataTool, once, to record the FM music that the
  game's own sound driver plays. The game on the Amiga doesn't need them.
- Problems and questions: [Issues](../../issues). Please give your Amiga's configuration (CPU,
  RAM, AGA/RTG), the start icon you used, and if possible a photo of the screen.

## License

The DataTool scripts in this repository are released under the [MIT License](LICENSE). The
engine binaries under Releases are free to download and use with your own disc. *Snatcher* itself,
its code, pictures, music and text, belongs to Konami and is not part of this project.
