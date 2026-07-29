"""Targeted abnormal-point confirmation for K2 strain-gauge bed meshes."""

import logging
import statistics


def _coord_key(value):
    return round(value, 4)


def find_spatial_outliers(base_points, positions, threshold):
    """Return points whose Z differs from their orthogonal-neighbour median."""
    if len(base_points) != len(positions):
        return []
    xs = sorted({_coord_key(point[0]) for point in base_points})
    ys = sorted({_coord_key(point[1]) for point in base_points})
    x_lookup = {value: index for index, value in enumerate(xs)}
    y_lookup = {value: index for index, value in enumerate(ys)}
    point_lookup = {
        (_coord_key(point[0]), _coord_key(point[1])): index
        for index, point in enumerate(base_points)
    }
    outliers = []
    for index, (point, result) in enumerate(zip(base_points, positions)):
        x_key = _coord_key(point[0])
        y_key = _coord_key(point[1])
        x_index = x_lookup[x_key]
        y_index = y_lookup[y_key]
        neighbour_indices = []
        for next_x_index, next_y_index in (
            (x_index - 1, y_index),
            (x_index + 1, y_index),
            (x_index, y_index - 1),
            (x_index, y_index + 1),
        ):
            if not (0 <= next_x_index < len(xs)
                    and 0 <= next_y_index < len(ys)):
                continue
            neighbour_index = point_lookup.get(
                (xs[next_x_index], ys[next_y_index])
            )
            if neighbour_index is not None:
                neighbour_indices.append(neighbour_index)
        if len(neighbour_indices) < 2:
            continue
        predicted_z = statistics.median(
            positions[neighbour_index].bed_z
            for neighbour_index in neighbour_indices
        )
        residual = result.bed_z - predicted_z
        if abs(residual) > threshold:
            outliers.append({
                "index": index,
                "x": point[0],
                "y": point[1],
                "old_z": result.bed_z,
                "predicted_z": predicted_z,
                "residual": residual,
            })
    return outliers


def choose_confirmed_z(old_z, repeat_zs):
    """Preserve real geometry while rejecting one isolated bad reading."""
    return statistics.median([old_z] + list(repeat_zs))


class K2MeshGuard:
    """Re-probe only mesh points that disagree sharply with their neighbours."""

    def __init__(self, config, probe):
        self.printer = config.get_printer()
        self.probe = probe
        self.enabled = config.getboolean("mesh_guard_enabled", True)
        self.threshold = config.getfloat(
            "mesh_guard_residual", 0.08, above=0.0, maxval=1.0
        )
        self.repeat_count = config.getint(
            "mesh_guard_samples", 2, minval=2, maxval=4
        )
        self.max_points = config.getint(
            "mesh_guard_max_points", 6, minval=1, maxval=20
        )
        self.bmc = None
        self.active_gcmd = None
        self.original_finalize = None
        if self.enabled:
            self.printer.register_event_handler(
                "klippy:ready", self._handle_ready
            )

    def _handle_ready(self):
        bedmesh = self.printer.lookup_object("bed_mesh", None)
        if bedmesh is None:
            logging.info("PRTouch mesh guard: [bed_mesh] is not configured")
            return
        self.bmc = bedmesh.bmc
        self.original_finalize = self.bmc.probe_finalize
        probe_helper = self.bmc.probe_mgr.probe_helper
        probe_helper.finalize_callback = self._probe_finalize

        gcode = self.printer.lookup_object("gcode")
        gcode.register_command("BED_MESH_CALIBRATE", None)
        gcode.register_command(
            "BED_MESH_CALIBRATE",
            self.cmd_BED_MESH_CALIBRATE,
            desc=self.bmc.cmd_BED_MESH_CALIBRATE_help,
        )
        logging.info(
            "PRTouch mesh guard enabled: residual=%.3f samples=%d max_points=%d",
            self.threshold, self.repeat_count, self.max_points
        )

    def cmd_BED_MESH_CALIBRATE(self, gcmd):
        self.active_gcmd = gcmd
        try:
            self.bmc.cmd_BED_MESH_CALIBRATE(gcmd)
        finally:
            self.active_gcmd = None

    def _probe_finalize(self, positions):
        gcmd = self.active_gcmd
        if gcmd is None:
            return self.original_finalize(positions)
        base_points = self.bmc.probe_mgr.get_base_points()
        mesh_positions = list(positions)
        trailing_positions = []
        if len(mesh_positions) == len(base_points) + 1:
            trailing_positions = [mesh_positions.pop()]
        if len(mesh_positions) != len(base_points):
            gcmd.respond_info(
                "PRTouch mesh guard: skipped (generated=%d probed=%d)"
                % (len(base_points), len(mesh_positions))
            )
            return self.original_finalize(positions)

        outliers = find_spatial_outliers(
            base_points, mesh_positions, self.threshold
        )
        if not outliers:
            gcmd.respond_info("PRTouch mesh guard: no abnormal points")
            return self.original_finalize(positions)
        if len(outliers) > self.max_points:
            details = ", ".join(
                "(%.1f,%.1f residual=%+.3f)"
                % (item["x"], item["y"], item["residual"])
                for item in outliers
            )
            raise gcmd.error(
                "PRTouch mesh guard found %d abnormal points (limit %d): %s"
                % (len(outliers), self.max_points, details)
            )

        gcmd.respond_info(
            "PRTouch mesh guard: confirming %d abnormal point%s"
            % (len(outliers), "" if len(outliers) == 1 else "s")
        )
        self._confirm_outliers(gcmd, base_points, mesh_positions, outliers)
        return self.original_finalize(mesh_positions + trailing_positions)

    def _confirm_outliers(self, gcmd, base_points, positions, outliers):
        probe_session = self.probe.probe_session
        if probe_session.hw_probe_session is None:
            raise gcmd.error(
                "PRTouch mesh guard requires the active mesh probe session"
            )
        params = dict(gcmd.get_command_parameters())
        params["SAMPLES"] = "1"
        params["SAMPLES_TOLERANCE_RETRIES"] = "0"
        gcode = self.printer.lookup_object("gcode")
        repeat_gcmd = gcode.create_gcode_command(
            "BED_MESH_CALIBRATE", "", params
        )
        toolhead = self.printer.lookup_object("toolhead")
        probe_helper = self.bmc.probe_mgr.probe_helper
        horizontal_move_z = probe_helper.horizontal_move_z
        xy_speed = probe_helper.speed
        lift_speed = self.probe.get_probe_params(gcmd)["lift_speed"]
        x_offset, y_offset = self.probe.get_offsets(gcmd)[:2]

        for outlier in outliers:
            index = outlier["index"]
            bed_x, bed_y = base_points[index][:2]
            current = toolhead.get_position()
            safe_z = max(current[2], horizontal_move_z)
            toolhead.manual_move([None, None, safe_z], lift_speed)
            toolhead.manual_move(
                [bed_x - x_offset, bed_y - y_offset, None], xy_speed
            )
            repeats = []
            for _repeat_index in range(self.repeat_count):
                probe_session.run_probe(repeat_gcmd)
                repeat_result = probe_session.pull_probed_results()[0]
                repeats.append(repeat_result.bed_z)
                current = toolhead.get_position()
                safe_z = max(current[2], horizontal_move_z)
                toolhead.manual_move([None, None, safe_z], lift_speed)

            old_result = positions[index]
            final_z = choose_confirmed_z(old_result.bed_z, repeats)
            positions[index] = old_result._replace(bed_z=final_z)
            gcmd.respond_info(
                "PRTouch mesh guard: X%.2f Y%.2f old=%.6f "
                "repeat=%s final=%.6f residual=%+.6f"
                % (
                    bed_x,
                    bed_y,
                    old_result.bed_z,
                    ",".join("%.6f" % value for value in repeats),
                    final_z,
                    outlier["residual"],
                )
            )
