# Third-party software and provenance

This repository combines upstream software with open K2 integration work.

## Klipper

The `klippy/` host tree is based on Klipper commit
`d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65`.

Klipper is licensed under GNU GPLv3. See `COPYING` and
<https://www.klipper3d.org/>.

`klippy/chelper/c_helper.so` was cross-built from the included Klipper C
sources for the K2 platform. It is not intended to be a proprietary binary.

## Mainsail

The bundled web client is Mainsail 2.18.2 from
<https://github.com/mainsail-crew/mainsail>. Its version information is
retained in `system/mainsail/release_info.json`.

## Moonraker compatibility

`system/moonraker-compat/update_manager.py` is a compatibility modification of
the Moonraker update manager distributed in the Creality appliance. Moonraker
is licensed under GNU GPLv3: <https://github.com/Arksine/moonraker>.

## Creality K2 integration

The PRTouch and K2 integration work was derived through study of GPL-covered
Creality K2 Klipper source, upstream Klipper behaviour, captured logs, and
supervised testing on owned hardware.

No proprietary Creality firmware image is intended to be distributed here.
If an included file has unclear provenance, open an issue before reusing or
redistributing it. Exact build identity is in `K2-BUILD-MANIFEST`.

## Mainsail (Hark K2 edition)

`system/mainsail/` is the build of the Hark K2 edition of Mainsail, revision 3
(based on Mainsail v2.18.2, GPLv3). It adds the CFS panel (an External spool
card on printers without a CFS; load, unload and pressure-advance calibration
from the slot dialog; printed flow and retraction tests with a picker for
the result), the control-centre dashboard, appearance settings and
movable cards. Source: https://github.com/grant0013/mainsail (branch
`hark-cfs-panel`, commit `42f0333`; revision 1 is tag `v2.18.2-hark.1`). Upstream:
https://github.com/mainsail-crew/mainsail.

## Native touchscreen

The C application builds against LVGL 8.4.0 (MIT) and cJSON 1.7.19 (MIT),
with pinned archive hashes in `touchscreen/CMakeLists.txt`. Their licence
files are copied into the printer package by `touchscreen/developer-tools/build.sh`.
Lucide icons use ISC with the retained Feather notices in
`touchscreen/assets/Lucide-LICENSE`. Liberation Sans and Noto Sans source
fonts retain their distribution notices, including the SIL Open Font License,
in `touchscreen/assets/Liberation-LICENSE` and `Noto-LICENSE`.

The Python service adapts platform and printer integration code from this
project, under its existing GPLv3 licence. Optional locally supplied vendor
artwork is not included in the shared native assets.
