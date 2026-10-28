# Installing K2 OpenKlipper (step by step)

This puts open-source Klipper, an open strain-gauge probe driver, the CFS and
the closed-loop motors on a Creality K2, with Mainsail as the web interface and
Hark Screen on the touchscreen. It installs **next to** Creality's firmware:
nothing of Creality's is deleted, every system file it changes is backed up
first, and one command puts the printer back to stock.

> **Status: alpha.** Developed and used daily on one K2 Combo (model F021,
> Creality firmware 1.1.7.0) for real print jobs. It has not been tested on
> other K2s yet. Read "Before you start", and keep a way back (the uninstall
> command below) in mind.

## Before you start

| You need | Notes |
| --- | --- |
| A Creality **K2 or K2 Combo** (model F021) | K2 Pro (F012) and K2 Plus (F008) are untested; the installer refuses them unless you force it |
| Creality firmware **1.1.7.0** | Other versions may work; the installer tells you which you have |
| The printer on your network (Wi-Fi or cable) | and a Windows PC or Mac on the same network |
| About 15 minutes | and no print running |

It is your printer and your risk. The open stack is not made or supported by
Creality, and **modifying the printer's software may void Creality's
warranty**: check your warranty terms before installing. Uninstalling returns
the software to stock. While it is installed, Creality's own updates are switched off (an
update wiped an earlier install), so undo it first if you want a Creality
update.

## 1. Turn on root access on the printer

1. On the printer's touchscreen open **Settings** and scroll down to
   **Root account information**.
2. Read the warning, tick the box, wait for the 30-second countdown and press
   **OK**.
3. Write down the **password** it shows. The user name is `root`. (Many K2s show
   `creality_2024`, but use whatever yours shows.)

## 2. Find the printer's IP address

On the touchscreen: **Settings > Network**. It looks like `192.168.1.57`. Below,
replace `PRINTER-IP` with it.

## 3. Connect to the printer

- **Windows 10/11:** press Start, type `PowerShell`, open it.
- **Mac:** open **Terminal** (Applications > Utilities).

Type this and press Enter:

```sh
ssh root@PRINTER-IP
```

The first time it asks `Are you sure you want to continue connecting`: type
`yes` and press Enter. Then type the root password (nothing shows while you
type) and press Enter. You are in when the prompt ends in `root@...:~#`.

## 4. Check the printer (changes nothing)

Copy this whole line, paste it into the window and press Enter:

```sh
python3 -c "import urllib.request as u; open('/tmp/install.sh','wb').write(u.urlopen('https://raw.githubusercontent.com/grant0013/K2-OpenKlipper/main/install.sh').read())" && sh /tmp/install.sh --check
```

It prints your model, Creality firmware version, free space and print state,
then asks **which extruder is fitted**: `1` for the stock Creality extruder,
`2` for a Phaetus DXC2. The DXC2 needs longer pulls after each filament cut
(Phaetus' K2 instructions) and a deeper cut, so the answer sets those two
values. For the stock extruder it uses your printer's own Creality cutter
calibration. Then it asks **whether a CFS is connected**. The default comes
from Creality's own config, which lists the CFS it has seen; answer `n` on a
plain K2 that feeds from the rear spool holder (see
[Printers without a CFS](#printers-without-a-cfs)). It ends with `Checks passed. Nothing was changed`. If it says `STOPPED:`, the
line after it says why (for example a print is running, or less than 200 MB is
free: delete old G-code files or timelapses on the printer).

## 5. Install

```sh
sh /tmp/install.sh
```

It asks the extruder and CFS questions again (answer the same). To answer
them in advance, run it as `OKL_EXTRUDER=stock OKL_CFS=yes sh /tmp/install.sh`
(`dxc2` for a DXC2, `no` for no CFS).

It downloads the open stack (about 5 MB), backs up the files it changes to
`/mnt/UDISK/openklipper-backups/<date>`, installs, and starts it. It takes a minute or two. The touchscreen switches to Hark Screen, and the last lines show the
Mainsail address:

```
==> Done.
    Mainsail:   http://PRINTER-IP:4409/
```

Open that address in your browser. Mainsail should show **Printer is ready**.
From now on the open stack starts by itself 30 seconds after every power-on.

## 6. Set up OrcaSlicer

In OrcaSlicer, start from Creality's profile for your printer, then in
**Printer settings**:

1. **Machine G-code > Machine start G-code**, replace everything with:

   ```gcode
   START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer] BED_TEMP=[bed_temperature_initial_layer_single] TOOL=[initial_no_support_extruder] TOWER_X={wipe_tower_x[0]} TOWER_Y={wipe_tower_y[0]} TOWER_W={if enable_prime_tower}[prime_tower_width]{else}0{endif}
   T[initial_no_support_extruder]
   M204 S2000
   M83
   ```

2. **Multimaterial > Change filament G-code**, replace everything with:

   ```gcode
   SET_GCODE_VARIABLE MACRO=_CFS_TC VARIABLE=next_purge VALUE={flush_length}
   G90
   G1 Z{max(z_after_toolchange, 0.6)} F600
   ```

   and leave **Purge in prime tower** off.

3. **Connection** (the Wi-Fi icon next to the printer name): host type
   **Octo/Klipper**, address `http://PRINTER-IP:7126` (note **7126**, not 7125).

Save the printer profile under a new name, for example "K2 OpenKlipper". The
reasons for each line are in [config/ORCA-K2-PROFILE.cfg](../config/ORCA-K2-PROFILE.cfg).

## 7. First print

1. In Mainsail, check the **CFS** panel shows your loaded slots. Set each slot's
   filament with its edit button (material, colour).
2. Print something small first, a calibration cube in PLA. `START_PRINT` heats
   up, homes, loads and purges the slot, cleans the nozzle, measures Z with the
   nozzle strain gauge, scrapes the nozzle on the bed strip, probes the mesh and
   prints a prime line. That takes about 5 minutes, all shown in Mainsail.
3. Watch the first layer. If it does not stick or looks too squashed, change
   the first-layer correction in **`printer-overrides.cfg`** (Mainsail >
   Machine): add

   ```ini
   [gcode_macro START_PRINT]
   variable_hot_z_comp: 0.05
   ```

   (0.04 mm by default; bigger = nozzle lower), save and restart. See
   "Your settings" below.
4. Optional: make the touchscreen your own (name, logo, colours):
   [customising the screen](SCREEN-CUSTOMISE.md).
5. Optional, per filament slot: pressure advance measured by the nozzle strain
   gauge, `PA_CAL_RUN SLOT=A` (see [PA calibration](PA-CALIBRATION.md)), and
   input shaping with `SHAPER_CALIBRATE`.

## Going back to stock

SSH in as in step 3, then:

```sh
sh /mnt/UDISK/k2-mainline-20260729-v62/uninstall.sh
reboot
```

That stops the open stack starting at boot, puts back every system file from
the install backup (Creality's app services, nginx, Moonraker) and restarts
Creality's services. Your G-code files are not touched, and the open-stack
files stay on the printer so `install.sh` can bring it back. To delete them as
well: `sh /mnt/UDISK/k2-mainline-20260729-v62/uninstall.sh --purge`.

## Your settings

The configuration is two files in Mainsail's **Machine** page:

- **`k2-openklipper.cfg`**: the open-stack configuration. It is managed:
  replaced from the installed version on every start and every update, so
  never edit it.
- **`printer-overrides.cfg`**: your printer's own settings, loaded last so they
  win. Updates never touch it. To change a setting, repeat its section header
  with only the options you want to change, for example
  `[gcode_macro START_PRINT]` and `variable_hot_z_comp: 0.05`. The installer
  puts your extruder settings here.

## Updates

New versions come from this project's **GitHub releases**: versions tested on
a K2 before they are released, never unreleased work. When one is out:

- Mainsail shows a **K2 OpenKlipper update** dialog (while the printer is idle)
  with **Update now**, and
- the touchscreen's **Machine** page shows an **Update** row you can tap.

Or from Mainsail's console: `OKL_UPDATE_CHECK` to check, `OKL_UPDATE CONFIRM=1`
to install. Over SSH: `sh /mnt/UDISK/k2-mainline-20260729-v62/install.sh --update`.

An update keeps `printer-overrides.cfg`, your touchscreen theme, CFS slot data
and Mainsail settings, backs up first, and restarts Klipper (about 2 minutes).
It is refused while printing. A printer installed before v0.3.0 has its
settings moved into `printer-overrides.cfg` on the first update.

## Creality firmware updates

While K2 OpenKlipper is installed, Creality's updater is switched off: a
Creality update once wiped an earlier install. To update Creality's firmware
safely:

```sh
sh /mnt/UDISK/k2-mainline-20260729-v62/creality-update.sh
```

It saves your settings to the G-code folder (`okl-settings-backup-<date>.tar.gz`,
also downloadable from Mainsail), puts the printer back to stock, and tells you
the next steps: reboot, update from the touchscreen as usual, then run the
install command (steps 3 to 5) again. The installer finds the backup and
restores your settings.

## What the installer changes

| Where | Change |
| --- | --- |
| `/mnt/UDISK/k2-mainline-20260729-v62` | The open stack (Klipper, K2 modules, configs, scripts) |
| `/mnt/UDISK/printer_data_mainline` | Its settings and logs (shares the stock G-code folder) |
| `/mnt/UDISK/mainsail-mainline`, `harkscreen`, `harkcam` | Web interface, touchscreen app, camera stream |
| `/etc/init.d/app` | Edited in place: master-server, Monitor and upgrade-server are removed from its start list (they would fight Klipper for the printer, and allow an OTA). Nothing else in Creality's file changes |
| `/etc/rc.local` | One line: start the open stack 30 s after boot while `/mnt/UDISK/.system/boot-mainline` exists |
| `/etc/nginx/nginx.conf` | Adds Mainsail on port 4409; Creality's Fluidd page on 4408 now talks to the open stack too. No access logs (they live in RAM) |
| Moonraker | A second instance on port 7126; the stock one on 7125 is switched off |

All of these are backed up in `/mnt/UDISK/openklipper-backups/` first. Nothing
is flashed: the printer's control boards keep Creality's firmware, and motor
settings are only ever written to RAM, so a power cycle restores them.

## Troubleshooting

- **Colour-change cuts are incomplete** (the CFS reports it cannot pull the
  filament back, or a strand stays in the extruder): the cutter does not go in
  far enough. In Mainsail, make `cutter_cut_x` in `printer-overrides.cfg` 0.2 to
  0.4 more negative (allowed range -10 to -5), save, and restart.

- **Mainsail says Klipper is not ready, or homing fails after a restart:**
  power the printer off and on. Klipper restarts without a power cycle can leave
  the motor controllers unhappy.
- **The touchscreen is blank or shows the Creality logo after boot:** wait a
  minute (the open stack starts 30 s after boot), then refresh Mainsail.
- **Logs** for a bug report: in Mainsail, Machine > logs > `klippy.log`, plus
  `/tmp/mainline-boot.log` after a boot.
- **Want stock back quickly without uninstalling:**
  `sh /mnt/UDISK/k2-mainline-20260729-v62/switch-to-stock.sh` switches until the
  next reboot.

Bug reports and results from other K2s are very welcome:
[GitHub issues](https://github.com/grant0013/K2-OpenKlipper/issues).

## Going further

- [Run Klipper on a separate PC](EXTERNAL-HOST.md) over one USB cable, and leave
  the K2's own small computer to pass messages.
- [AI print-failure detection](../tools/ai-detect/) on that PC: a fast
  spaghetti detector plus a local vision model as a second opinion.

## Printers without a CFS

A K2 without a CFS feeds one filament from the rear spool holder. The
installer sets this up when you answer `n` to the CFS question; to change it
later, put this in `printer-overrides.cfg` and restart:

```
[k2_cfs]
cfs_fitted: False
```

With it:

- **Load and unload** with `LOAD_FILAMENT` and `UNLOAD_FILAMENT` (Mainsail's
  extruder panel shows them as Load/Unload buttons). Push the filament in
  from the back until it stops at the extruder, then `LOAD_FILAMENT` heats,
  feeds and purges it at the chute. `UNLOAD_FILAMENT` cuts it at the knife and
  pulls it back out of the gears; then pull it out from the back. Both work
  from a runout pause too, followed by `RESUME`.
- **Printing**: slice for one filament (T0). The print start primes the spool
  at the chute. A file that asks for T1-T3 stops with a message.
- **Filament settings**: slot A's record in the CFS slot data (pressure
  advance, retraction, temperatures) is used for the rear spool when it is set.
  `PA_CAL_STATIC` and `PA_CAL_RUN` use slot A by default.
- **Runout** pauses the print and says to load a new spool.
- The CFS panel in Mainsail and the CFS page on the touchscreen are hidden.

If a CFS is connected but `cfs_fitted` is still `True` and the CFS does not
answer, the console says so at startup and CFS commands explain how to fix
it.
