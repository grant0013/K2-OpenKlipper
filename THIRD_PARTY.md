# Third-party software and provenance

This repository combines upstream software with open K2 integration work.

## Klipper

The `klippy/` host tree is based on Klipper commit
`d7cea5bb1aca70849f28d0bb98ab1b96b9f6db65`.

Klipper is licensed under GNU GPLv3. See `COPYING` and
<https://www.klipper3d.org/>.

`harkscreen/klipper-logo.png` is the Klipper logo from the Klipper
repository (`docs/img/klipper-logo.png`), with its grey recoloured light grey
so it shows on a dark screen. It identifies the Klipper software the screen
runs on; this project is not affiliated with the Klipper project.

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

`system/mainsail/` is the build of the Hark K2 edition of Mainsail, revision 2
(based on Mainsail v2.18.2, GPLv3). It adds the CFS panel (hidden on printers
without a CFS), the control-centre dashboard, appearance settings and movable
cards. Source: https://github.com/grant0013/mainsail (branch `hark-cfs-panel`,
commit `85512fb`; revision 1 is tag `v2.18.2-hark.1`). Upstream:
https://github.com/mainsail-crew/mainsail.
