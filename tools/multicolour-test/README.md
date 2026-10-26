# Two-colour pumpkin test print

A small real-world print for testing CFS colour changes: an orange ribbed
pumpkin (55 mm across, 46 mm tall, no supports needed) with a green stem and
a leaf lying on one rib. The two colours share about 5 mm of height, so a
0.2 mm slice gives around 23 colour changes.

```sh
python3 -m venv .venv && .venv/bin/pip install numpy
.venv/bin/python make_pumpkin.py      # body.npz + green.npz
.venv/bin/python build_3mf.py         # pumpkin.3mf: body = filament 3, stem/leaf = filament 1
```

Open `pumpkin.3mf` in OrcaSlicer, or slice it with the Orca CLI. The flush
volumes in the project favour orange over green (the light colour needs
more). Shape parameters are at the top of `make_pumpkin.py`.

On F021 it printed in 81 min with 23 changes averaging 101 s each.
