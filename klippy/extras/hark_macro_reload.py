# Reload G-code macro bodies from the config files without a restart
#
# Idea from Kalico's RELOAD_GCODE_MACROS (KalicoCrew/kalico, GPLv3).
# On the K2 a Klipper RESTART can leave nozzle_mcu shut down, so editing a
# macro used to cost a FIRMWARE_RESTART. This swaps the compiled template
# of every [gcode_macro], [delayed_gcode], [homing_override] and the
# [idle_timeout] G-code in place.
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import jinja2
import configfile

# Section prefix -> attribute holding its TemplateWrapper
TEMPLATE_ATTRS = (
    ('gcode_macro', 'template'),
    ('delayed_gcode', 'timer_gcode'),
    ('homing_override', 'template'),
    ('idle_timeout', 'idle_gcode'),
)


class MacroReload:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        # Sources as loaded at startup, to report what a reload changed
        self.sources = {}
        self.variables = {}
        for prefix, attr in TEMPLATE_ATTRS:
            for section in config.get_prefix_sections(prefix):
                name = section.get_name()
                self.sources[name] = section.get('gcode', '', note_valid=False)
                self.variables[name] = self._variables(section)
        self.gcode.register_command(
            'RELOAD_GCODE_MACROS', self.cmd_RELOAD_GCODE_MACROS,
            desc=self.cmd_RELOAD_GCODE_MACROS_help)

    def _variables(self, section):
        return dict((o, section.get(o, note_valid=False))
                    for o in section.get_prefix_options('variable_'))

    def _targets(self):
        targets = []
        for prefix, attr in TEMPLATE_ATTRS:
            for name, obj in self.printer.lookup_objects(prefix):
                if name == 'gcode_macro':
                    continue    # the module object itself, not a section
                wrapper = getattr(obj, attr, None)
                if wrapper is not None and hasattr(wrapper, 'template'):
                    targets.append((prefix, name, wrapper))
        return targets

    cmd_RELOAD_GCODE_MACROS_help = (
        "Reload macro G-code from the config files without a restart")

    def cmd_RELOAD_GCODE_MACROS(self, gcmd):
        # Parse the files the way Klipper does at startup, [include]s and
        # all. (A second PrinterConfig would re-register SAVE_CONFIG, and
        # PrinterConfig.read_config skips includes, so it saw only the
        # one-line printer.cfg pointer on the K2.)
        main_file = self.printer.get_start_args()['config_file']
        try:
            reader = configfile.ConfigFileReader()
            data = reader.read_config_file(main_file)
            fileconfig = reader.build_fileconfig_with_includes(data, main_file)
            new_config = configfile.ConfigWrapper(
                self.printer, fileconfig, {}, 'printer')
        except Exception as e:
            raise gcmd.error("Config files could not be read: %s" % (e,))
        env = self.printer.lookup_object('gcode_macro').env
        loaded = set()
        compiled, errors, missing = [], [], []
        # Compile everything first: one bad macro and nothing is swapped
        for prefix, name, wrapper in self._targets():
            loaded.add(name)
            if not new_config.has_section(name):
                missing.append(name)
                continue
            section = new_config.getsection(name)
            script = section.get('gcode', '', note_valid=False)
            if script == self.sources.get(name):
                continue
            if not script and prefix != 'gcode_macro':
                continue    # gcode left out: keep the module's default
            try:
                template = env.from_string(script)
            except jinja2.exceptions.TemplateSyntaxError as e:
                lines = script.splitlines()
                line = lines[e.lineno - 1] if 0 < e.lineno <= len(lines) \
                    else ''
                errors.append("[%s] line %d: %s # %s"
                              % (name, e.lineno, line.strip(), e.message))
                continue
            except Exception as e:
                errors.append("[%s]: %s" % (name, e))
                continue
            compiled.append((name, wrapper, template, script))
        if errors:
            raise gcmd.error("No macros reloaded, fix these first:\n%s"
                             % "\n".join(errors))
        for name, wrapper, template, script in compiled:
            wrapper.template = template
            self.sources[name] = script
            logging.info("RELOAD_GCODE_MACROS: reloaded [%s]", name)
        # Things a reload cannot do
        added = []
        changed_vars = []
        for prefix, attr in TEMPLATE_ATTRS:
            for section in new_config.get_prefix_sections(prefix):
                name = section.get_name()
                if name not in loaded:
                    added.append(name)
                elif (name in self.variables
                      and self._variables(section) != self.variables[name]):
                    changed_vars.append(name)
        msg = ["Reloaded %d macro%s%s" % (
            len(compiled), "" if len(compiled) == 1 else "s",
            (": " + ", ".join(n.split(None, 1)[-1] for n, w, t, s
                              in compiled)) if compiled else "")]
        if added:
            msg.append("New sections need a RESTART: %s" % ", ".join(added))
        if missing:
            msg.append("Removed from the files but still loaded until a "
                       "RESTART: %s" % ", ".join(missing))
        if changed_vars:
            msg.append("variable_ defaults changed (running values kept; "
                       "use SET_GCODE_VARIABLE or RESTART): %s"
                       % ", ".join(changed_vars))
        gcmd.respond_info("\n".join(msg))


def load_config(config):
    return MacroReload(config)
