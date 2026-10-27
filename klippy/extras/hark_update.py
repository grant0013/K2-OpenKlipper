# K2 OpenKlipper updates from GitHub releases
#
# Compares the installed VERSION with the newest release of the project on
# GitHub (a tested, tagged version; never a branch) and installs it on request
# with install.sh --update, which keeps the printer's own settings. While
# idle, a new release is offered in Mainsail as a dialog with an Update button;
# the touchscreen's Machine page shows it too.
#
#   OKL_UPDATE_CHECK        check now and report
#   OKL_UPDATE CONFIRM=1    install the newest release (refused while printing)
#
# [hark_update]
# repo: grant0013/K2-OpenKlipper
# check_interval: 21600     # seconds between checks, 0 = only on request
# notify: True              # offer new releases in Mainsail
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, os, subprocess, threading, urllib.request

UPDATE_LOG = "/tmp/okl-update.log"


class HarkUpdate:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.repo = config.get('repo', 'grant0013/K2-OpenKlipper')
        here = os.path.dirname(os.path.realpath(__file__))
        self.build = config.get(
            'build_dir', os.path.dirname(os.path.dirname(here)))
        self.interval = config.getfloat('check_interval', 21600., minval=0.)
        self.notify = config.getboolean('notify', True)
        self.notified = None
        self.checking = False
        self.status = {
            'installed': self._installed(), 'latest': None,
            'update_available': False, 'notes': '', 'url': '',
            'checked': False, 'error': '', 'updating': False,
        }
        self.gcode.register_command(
            'OKL_UPDATE_CHECK', self.cmd_OKL_UPDATE_CHECK,
            desc="Check GitHub for a newer K2 OpenKlipper release")
        self.gcode.register_command(
            'OKL_UPDATE', self.cmd_OKL_UPDATE,
            desc="Install the newest K2 OpenKlipper release (CONFIRM=1)")
        self.printer.register_event_handler('klippy:ready', self._ready)

    def _installed(self):
        try:
            with open(os.path.join(self.build, 'VERSION')) as f:
                return f.read().strip() or 'unknown'
        except OSError:
            return 'unknown'

    def _ready(self):
        if self.interval > 0:
            # First look a minute after start, out of the way of start-up
            self.reactor.register_timer(
                self._timer, self.reactor.monotonic() + 60.)

    def _timer(self, eventtime):
        self._start_check(None)
        return eventtime + self.interval

    def _start_check(self, gcmd_report):
        if self.checking:
            return
        self.checking = True
        threading.Thread(target=self._check, args=(gcmd_report,),
                         daemon=True).start()

    def _check(self, report):
        # Runs on its own thread: no printer objects touched here
        result = {'error': ''}
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/%s/releases?per_page=10"
                % self.repo, headers={
                    'Accept': 'application/vnd.github+json',
                    'User-Agent': 'k2-openklipper'})
            rels = [r for r in json.load(urllib.request.urlopen(req, timeout=30))
                    if not r.get('draft')]
            if rels:
                rel = rels[0]
                result.update({
                    'latest': rel.get('tag_name', ''),
                    'notes': (rel.get('body') or '').strip(),
                    'url': rel.get('html_url', '')})
            else:
                result['error'] = 'no releases published yet'
        except Exception as e:
            result['error'] = str(e)[:120]
        self.reactor.register_async_callback(
            lambda et: self._apply(result, report))

    @staticmethod
    def _key(version):
        # "v0.3.0-alpha.1" -> ((0, 3, 0), (0, 1)); a final release sorts after
        # its alpha/beta/rc pre-releases
        import re
        v = (version or '').strip().lstrip('vV')
        main, _, pre = v.partition('-')
        nums = tuple(int(n) for n in re.findall(r'\d+', main)[:3])
        nums += (0,) * (3 - len(nums))
        if not pre:
            return nums, (9, 0)
        rank = {'alpha': 0, 'beta': 1, 'rc': 2}.get(re.sub(r'[^a-z]', '', pre.lower()), 0)
        num = re.findall(r'\d+', pre)
        return nums, (rank, int(num[0]) if num else 0)

    def _newer(self, latest, installed):
        try:
            return self._key(latest) > self._key(installed)
        except Exception:
            return False

    def _apply(self, result, report):
        self.checking = False
        self.status['installed'] = self._installed()
        self.status['checked'] = True
        self.status['error'] = result.get('error', '')
        if result.get('latest'):
            self.status['latest'] = result['latest']
            self.status['notes'] = result.get('notes', '')[:2000]
            self.status['url'] = result.get('url', '')
            self.status['update_available'] = self._newer(
                result['latest'], self.status['installed'])
        if report is not None:
            if self.status['error']:
                self.gcode.respond_info(
                    "K2 OpenKlipper update check failed: %s"
                    % self.status['error'])
            elif self.status['update_available']:
                self.gcode.respond_info(
                    "K2 OpenKlipper %s is available (installed %s). "
                    "OKL_UPDATE CONFIRM=1 installs it.\n%s"
                    % (self.status['latest'], self.status['installed'],
                       self.status['url']))
            else:
                self.gcode.respond_info(
                    "K2 OpenKlipper %s is up to date."
                    % self.status['installed'])
        if (self.notify and self.status['update_available']
                and self.notified != self.status['latest']
                and not self._busy()):
            self.notified = self.status['latest']
            self._offer()

    def _busy(self):
        stats = self.printer.lookup_object('print_stats', None)
        if stats is None:
            return False
        state = stats.get_status(self.reactor.monotonic()).get('state')
        return state in ('printing', 'paused')

    def _offer(self):
        # Mainsail shows "action:prompt" lines as a dialog with buttons
        first = self.status['notes'].split('\n')[0][:120] if self.status['notes'] else ''
        lines = [
            "action:prompt_begin K2 OpenKlipper update",
            "action:prompt_text Version %s is available (this printer has %s)."
            % (self.status['latest'], self.status['installed']),
            "action:prompt_text Your settings in printer-overrides.cfg are kept. "
            "The printer restarts during the update (about 2 minutes).",
        ]
        if first:
            lines.append("action:prompt_text %s" % first)
        lines += [
            "action:prompt_footer_button Later|RESPOND TYPE=command MSG=action:prompt_end",
            "action:prompt_footer_button Update now|OKL_UPDATE CONFIRM=1|primary",
            "action:prompt_show",
        ]
        for line in lines:
            self.gcode.respond_raw("// " + line)

    def get_status(self, eventtime):
        return dict(self.status)

    def cmd_OKL_UPDATE_CHECK(self, gcmd):
        gcmd.respond_info("Checking GitHub for K2 OpenKlipper releases...")
        self._start_check(gcmd)

    def cmd_OKL_UPDATE(self, gcmd):
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error("OKL_UPDATE requires CONFIRM=1")
        if self._busy():
            raise gcmd.error("Not while printing or paused")
        tag = self.status.get('latest')
        if not tag:
            raise gcmd.error("No release known yet: run OKL_UPDATE_CHECK first")
        if not self.status.get('update_available'):
            raise gcmd.error("Already on %s" % self.status['installed'])
        self.gcode.respond_raw("// action:prompt_end")
        # The installer stops this Klipper, so it runs in its own session.
        # It is the release's own install.sh, fetched for that tag.
        script = (
            "python3 -c \"import urllib.request as u; open('/tmp/okl-install.sh','wb')"
            ".write(u.urlopen('https://raw.githubusercontent.com/%s/%s/install.sh',"
            " timeout=60).read())\" && OKL_REF=%s sh /tmp/okl-install.sh --update"
            % (self.repo, tag, tag))
        with open(UPDATE_LOG, 'w') as log:
            subprocess.Popen(['/bin/sh', '-c', script], stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True, close_fds=True)
        self.status['updating'] = True
        gcmd.respond_info(
            "Updating to K2 OpenKlipper %s. Klipper restarts in about a "
            "minute; this page reconnects by itself. Log: %s"
            % (tag, UPDATE_LOG))
        logging.info("hark_update: started update to %s", tag)


def load_config(config):
    return HarkUpdate(config)
