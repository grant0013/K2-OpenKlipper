# prtouch_open — Klipper extras package
#
# Part of K2-PRTouch — an open reimplementation of Creality K2's strain-gauge probe.
# Copyright (C) 2026 Grant Harkness <grantharkness0013@gmail.com>
# Distributed under the GNU GPLv3 license.
#
# Installation overview:
#   1. Drop this whole directory into /usr/share/klipper/klippy/extras/
#      as `prtouch_open/`. No active behaviour change yet.
#   2. Replace /usr/share/klipper/klippy/extras/prtouch_v3.py with a tiny
#      loader that does:
#          from . import probe as probes
#          from .prtouch_open import endstop as _endstop
#          def load_config(config):
#              prt = _endstop.PRTouchEndstopWrapper(config)
#              config.get_printer().add_object(
#                  'probe', probes.PrinterProbe(config, prt),
#              )
#              return prt
#      That swap is Phase B in our M1 install plan; this __init__.py is
#      intentionally inert (no top-level side effects) so Phase A can drop
#      the package without changing live probe behaviour.
"""K2-PRTouch open reimplementation — package init.

Sub-modules:
    cfg.py            PrtouchConfig — printer.cfg schema reader
    mcu_protocol.py   wire-level CommandWrapper bindings (12 commands)
    pres.py           PressureSampler + check_trigger cascade
    step.py           StepCapture — Z-position capture
    filter.py         pure-Python IIR filter chain (parity-verified)
    motion.py         probe_by_step orchestration
    endstop.py        PRTouchEndstopWrapper — the duck-typed probe facade

This __init__ deliberately does NOT define load_config — the live entry
point lives at extras/prtouch_v3.py (Creality's), which Phase B replaces
with a thin wrapper around endstop.PRTouchEndstopWrapper. Keeping load_config
out of here means simply having the package on disk is a no-op until
prtouch_v3.py is rewired.
"""
