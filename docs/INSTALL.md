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
Creality. While it is installed, Creality's own updates are switched off (an
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
and ends with `Checks passed. Nothing was changed`. If it says `STOPPED:`, the
line after it says why (for example a print is running, or less than 200 MB is
free: delete old G-code files or timelapses on the printer).

## 5. Install

```sh
sh /tmp/install.sh
```

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
3. Watch the first layer. If it does not stick or looks too squashed, the
   first-layer correction is `variable_hot_z_comp` in the `START_PRINT` macro
   (Mainsail > Machine > `k2-mainline-20260729-v62.cfg`; 0.04 mm on our printer;
   bigger = nozzle lower).
4. Optional, per filament slot: pressure advance measured by the nozzle strain
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

Run the uninstall before installing a Creality firmware update. If a Creality
update ever replaces the system anyway, the printer simply comes back as
stock; run the install again afterwards.

## What the installer changes

| Where | Change |
| --- | --- |
| `/mnt/UDISK/k2-mainline-20260729-v62` | The open stack (Klipper, K2 modules, configs, scripts) |
| `/mnt/UDISK/printer_data_mainline` | Its settings and logs (shares the stock G-code folder) |
| `/mnt/UDISK/mainsail-mainline`, `harkscreen`, `harkcam` | Web interface, touchscreen app, camera stream |
| `/etc/init.d/app` | Creality's app services without master-server, Monitor and upgrade-server (they would fight Klipper for the printer, and allow an OTA) |
| `/etc/rc.local` | One line: start the open stack 30 s after boot while `/mnt/UDISK/.system/boot-mainline` exists |
| `/etc/nginx/nginx.conf` | Adds Mainsail on port 4409. Creality's Fluidd page on 4408 stays but talks to the stock Moonraker, which is switched off: use Mainsail |
| Moonraker | A second instance on port 7126; the stock one on 7125 is switched off |

All of these are backed up in `/mnt/UDISK/openklipper-backups/` first. Nothing
is flashed: the printer's control boards keep Creality's firmware, and motor
settings are only ever written to RAM, so a power cycle restores them.

## Troubleshooting

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
