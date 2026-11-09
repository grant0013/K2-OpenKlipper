# K2 OpenKlipper v0.4.24-alpha.1

**The max flow test now runs up to 40 mm³/s and no longer reports "no limit"
when the force falls away.**

Please keep the reports coming. A klippy.log and what you saw are the most
useful things to send. The test now writes every step to the log, so a log
from a run that looks wrong is very useful.

## Max flow test

Run it with `FLOW_CAL_STATIC SLOT=A` (any slot). It is still off at print
start unless you turn it on.

- **The ramp goes up to 40 mm³/s instead of 24.** Nearly half of the runs on
  one K2 found no limit by 24.
- **It is one continuous run that stops at the limit.** A pause partway makes
  every later step read low, so the ramp never picks up again once it stops.
- **Force falling under the line now counts as a limit.** Before, only force
  going over the line did, so a run could end with "no cap needed" when the
  filament had stopped being pushed through properly.
- **A sudden loss of force stops the ramp at once** (the idea is FlowTune's),
  and a noisy start to the ramp needs a bigger bend before it counts.

Tested on one K2 with Elegoo PETG at 250 C: two runs gave 14.0 and 12.5 mm³/s
and stopped at 18 to 20 mm³/s, using about 120 mm of filament. The limit on
PETG bends gently, so expect a mm³/s or two between runs. A filament with no
limit runs all the way to 40, about 400 mm. **Not tested yet:** PLA, other
PETGs, or a K2 Plus.

## Print time

- **Print time no longer jumps to days in the history.** A CFS check at print
  start could add the host's uptime to it. **Not confirmed yet** on a finished
  print.

The full list is in [CHANGELOG.md](CHANGELOG.md).

## Updating

Mainsail's Update button, the touchscreen's Machine page, or
`OKL_UPDATE CONFIRM=1`. Your settings are kept.

Alpha software on your own printer: stay near it for the first prints.
