# Ready-built printer touchscreen

`armv7/` contains the native executable, raster icons, license notices, build
identity and checksum manifest used by the normal project installer.

Developers must update this directory when runtime or build inputs change:

1. Commit the source changes.
2. Run `sh touchscreen/developer-tools/build.sh arm` from the repository root.
3. Commit the updated files in `touchscreen/precompiled/armv7/`.

The installer validates source fingerprints and file checksums before changing
the installation. It never compiles the touchscreen on the printer. See the
[developer guide](../developer-tools/README.md) for build and release details.
