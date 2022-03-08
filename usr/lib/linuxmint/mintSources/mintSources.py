#!/usr/bin/python3
import configparser
import datetime
import gettext
import glob
import json
import locale
import os
import pycurl
import re
import requests
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import argparse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('XApp', '1.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, GObject, Pango, XApp

from aptsources.sourceslist import SourcesList
from io import BytesIO
from CountryInformation import CountryInformation

import apt_pkg
import mintcommon.aptdaemon

BUTTON_LABEL_MAX_LENGTH = 30

# Used when launched by synaptic (via software-properties-gtk).
# The return code tells synaptic to refresh the cache if sources have changed
# If we try to refresh ourselves in this scenario, apt.Cache gets stuck waiting
# for synaptic to exit...
disable_refresh = False
sources_changed = False

FLAG_PATH = "/usr/share/iso-flag-png/%s.png"
FLAG_SIZE = 16

additional_repositories_file = "/etc/apt/sources.list.d/additional-repositories.list"

# i18n
APP = 'mintsources'
LOCALE_DIR = "/usr/share/linuxmint/locale"
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext

os.umask(0o022)

# Used as a decorator to run things in the background
def run_async(func):
    def wrapper(*args, **kwargs):
        thread = threading.Thread(target=func, args=args, kwargs=kwargs)
        thread.daemon = True
        thread.start()
        return thread
    return wrapper

# Used as a decorator to run things in the main loop, from another thread
def idle(func):
    def wrapper(*args):
        GObject.idle_add(func, *args)
    return wrapper

def signal_handler(signum, _):
    print("")
    sys.exit(128 + signum)

signal.signal(signal.SIGINT, signal_handler)

def remove_repository_via_cli(line, codename, forceYes):
    if line.startswith("ppa:"):
        user, sep, ppa_name = line.split(":")[1].partition("/")
        ppa_name = ppa_name or "ppa"
        try:
            ppa_info = get_ppa_info_from_lp(user, ppa_name, codename)
            print(_("You are about to remove the following PPA:"))
            if ppa_info["description"] is not None:
                print(" %s" % (ppa_info["description"]))
            print(_(" More info: %s") % str(ppa_info["web_link"]))

            if sys.stdin.isatty():
                if not forceYes:
                    print(_("Press Enter to continue or Ctrl+C to cancel"))
                    sys.stdin.readline()
            else:
                if not forceYes:
                    print(_("Unable to prompt for response.  Please run with -y"))
                    sys.exit(1)

        except KeyboardInterrupt as detail:
            print (_("Cancelling..."))
            sys.exit(1)
        except Exception as detail:
            print (_("Cannot get info about PPA: '%s'.") % detail)

        (deb_line, file) = expand_ppa_line(line.strip(), codename)
        deb_line = expand_http_line(deb_line, codename) + "\n"
        debsrc_line = 'deb-src' + deb_line[3:] + "\n"

        # Remove the PPA from sources.list.d
        try:
            with open(file, "r", encoding="utf-8", errors="ignore") as readfile:
                content = readfile.readlines()
                for line in (deb_line, debsrc_line):
                    if line in content:
                        content.remove(line)
                    elif "# %s" % line in content:
                        content.remove("# %s" % line)
            with open(file, "w", encoding="utf-8", errors="ignore") as writefile:
                writefile.writelines(content)

            # If file no longer contains any "deb" instances, delete it as well
            if not next((s for s in content if "deb " in s), None):
                os.unlink(file)
        except IOError as detail:
            print (_("failed to remove PPA: '%s'") % detail)

    elif line.startswith("deb ") or line.startswith("http"):
        # Remove the repository from sources.list.d
        try:
            with open(additional_repositories_file, "r", encoding="utf-8", errors="ignore") as readfile:
                content = readfile.readlines()
                line = "%s\n" % expand_http_line(line, codename)
                if line in content:
                    content.remove(line)
                elif "# %s" % line in content:
                    content.remove("# %s" % line)
            with open(additional_repositories_file, "w", encoding="utf-8", errors="ignore") as writefile:
                writefile.writelines(content)

            # If file no longer contains any "deb" instances, delete it as well
            if not next((s for s in content if "deb " in s), None):
                os.unlink(additional_repositories_file)
        except IOError as detail:
            print (_("failed to remove repository: '%s'") % detail)


def add_repository_via_cli(line, codename, forceYes, use_ppas):

    if line.startswith("ppa:"):
        if use_ppas != "true":
            print(_("Adding PPAs is not supported"))
            sys.exit(1)
        user, sep, ppa_name = line.split(":")[1].partition("/")
        ppa_name = ppa_name or "ppa"
        try:
            ppa_info = get_ppa_info_from_lp(user, ppa_name, codename)
        except Exception as detail:
            print (_("Cannot add PPA: '%s'.") % detail)
            sys.exit(1)

        if "private" in ppa_info and ppa_info["private"]:
            print(_("Adding private PPAs is not supported currently"))
            sys.exit(1)

        print(_("You are about to add the following PPA:"))
        if ppa_info["description"] is not None:
            print(" %s" % (ppa_info["description"]))
        print(_(" More info: %s") % str(ppa_info["web_link"]))

        if sys.stdin.isatty():
            if not(forceYes):
                print(_("Press Enter to continue or Ctrl+C to cancel"))
                sys.stdin.readline()
        else:
            if not(forceYes):
                print(_("Unable to prompt for response.  Please run with -y"))
                sys.exit(1)

        (deb_line, file) = expand_ppa_line(line.strip(), codename)
        deb_line = expand_http_line(deb_line, codename)
        debsrc_line = 'deb-src' + deb_line[3:]

        # Add the key if not in keyring
        add_new_key(ppa_info["signing_key_fingerprint"])

        # Add the PPA
        sources_enabled = os.path.exists("/etc/apt/sources.list.d/official-source-repositories.list")
        with open(file, "w", encoding="utf-8", errors="ignore") as text_file:
            text_file.write("%s\n" % deb_line)
            text_file.write("%s%s\n" % ("" if sources_enabled else "# ", debsrc_line))

    elif line.startswith("deb ") | line.startswith("http"):
        line = expand_http_line(line, codename)
        if repo_malformed(line):
            print(_("Malformed input, repository not added."))
            sys.exit(1)
        if repo_exists(line):
            print(_("Repository already exists."))
            #sys.exit(1) # from a result-oriented view it's not a fail
        else:
            with open(additional_repositories_file, "a", encoding="utf-8", errors="ignore") as f:
                f.write("%s\n" % line)

def add_new_key(key):
    # """ Add the key if not in keyring """
    # keys = subprocess.run(["apt-key","--quiet", "adv","--with-colons", "--batch",\
    #     "--fixed-list-mode", "--list-keys"], stdout=subprocess.PIPE,
    #         stderr=subprocess.DEVNULL).stdout.decode().split(":")
    # if not key in keys:
    #     add_key_remote(key)
    return add_key_remote(key)

def add_key_remote(key):
    try:
        proxy = []
        if os.environ.get('http_proxy') is not None:
            if os.environ.get('http_proxy') != "":
                # might be an good idea to check the environment variable content for url only ....
                # simple way could be
                # try:
                #     urllib.urlopen(url)
                # except IOError:
                #     print "Not a real URL"
                # is unclear to me about the fault message
                proxy=["--keyserver-options", "http-proxy=%s" % os.environ.get('http_proxy')]
        subprocess.run(["apt-key", "adv", "--keyserver", "hkps://keyserver.ubuntu.com:443"] + proxy + ["--recv-keys", key], check=True)
    except subprocess.CalledProcessError:
        return False
    return True

def repo_malformed(line):
    r = re.compile(r'(?:deb|deb-src)\s+(?:\[[^\]]+\]\s+)?\w+:/\S+?/?\s+\S+')
    match_line = r.match(line)
    if not match_line:
        return True
    return False

def repo_exists(line):
    r = re.compile(r'^[#\s]*(\S+)\s*(?:\[.*\])? \w+:/(\S+?)/? (.+)')
    match_line = r.match(line.strip())
    if match_line:
        repositories = SourcesList().list
        for repository in repositories:
            match_repo = r.match(repository.line.strip())
            if not match_repo:
                continue
            if match_repo.group(1, 2) == match_line.group(1, 2):
                if match_repo.group(3) == match_line.group(3):
                    return True
                repo_args = match_repo.group(3).split(" ")
                line_args = match_line.group(3).split(" ")
                if repo_args[0] == line_args[0]:
                    for arg in line_args[1:]:
                        if arg in repo_args[1:]:
                            return True
    return False

def retrieve_ppa_url(url):
    try:
        data = requests.get(url, timeout=10)
    except requests.exceptions.ConnectTimeout:
        raise PPAException(_("Connection timed out, check your connection or try again later."))
    except requests.exceptions.SSLError:
        raise PPAException(_("Failed to establish a secure connection."))
    except Exception as e:
        raise PPAException(_("Failed to download the PPA: %s." % e))
    return data

def get_ppa_info_from_lp(owner_name, ppa_name, base_codename):
    try:
        data = retrieve_ppa_url("https://launchpad.net/api/1.0/~%s/+archive/%s" % (owner_name, ppa_name))
    except PPAException as e:
        raise PPAException(e.value)
    if not data.ok:
        raise PPAException(_("No supported PPA of this name was found."))
    try:
        json_data = data.json()
    except json.decoder.JSONDecodeError:
        raise PPAException(_("No supported PPA of this name was found."))

    # Make sure the PPA supports our base release
    try:
        data = retrieve_ppa_url("http://ppa.launchpad.net/%s/%s/ubuntu/dists/%s" % (owner_name, ppa_name, base_codename))
    except PPAException as e:
        raise PPAException(e.value)
    if not data.ok:
        raise PPAException(_("This PPA does not support %s") % base_codename)

    return json_data

def encode(s):
    return re.sub("[^a-zA-Z0-9_-]", "_", s)

def expand_ppa_line(abrev, distro_codename):
    # leave non-ppa: lines unchanged
    if not abrev.startswith("ppa:"):
        return (abrev, None)
    # FIXME: add support for dependency PPAs too (once we can get them
    #        via some sort of API, see LP #385129)
    abrev = abrev.split(":")[1]
    ppa_owner = abrev.split("/")[0]
    try:
        ppa_name = abrev.split("/")[1]
    except IndexError:
        ppa_name = "ppa"
    sourceslistd = "/etc/apt/sources.list.d"
    line = "deb http://ppa.launchpad.net/%s/%s/ubuntu %s main" % (ppa_owner, ppa_name, distro_codename)
    filename = os.path.join(sourceslistd, "%s-%s-%s.list" % (encode(ppa_owner), encode(ppa_name), distro_codename))
    return (line, filename)

def expand_http_line(line, distro_codename):
    """
    short cut - this:
      apt-add-repository http://packages.medibuntu.org free non-free
    same as
      apt-add-repository 'deb http://packages.medibuntu.org/ base_codename free non-free'
    """
    if not line.startswith("http"):
        return line
    repo = line.split()[0]
    try:
        areas = line.split(" ",1)[1]
    except IndexError:
        areas = "main"
    line = "deb %s %s %s" % ( repo, distro_codename, areas )
    return line

class CurlCallback:
    def __init__(self):
        self.contents = ''

    def body_callback(self, buf):
        self.contents = self.contents + str(buf)


class PPAException(Exception):

    def __init__(self, value, original_error=None):
        self.value = value
        self.original_error = original_error

    def __str__(self):
        return repr(self.value)

SPEED_PIX_WIDTH = 125
SPEED_PIX_HEIGHT = 16

class Component():
    def __init__(self, name, description, selected):
        self.name = name
        self.description = description
        self.selected = selected
        self.widget = None

    def set_widget(self, widget):
        self.widget = widget

class Key():
    def __init__(self, pub):
        self.pub = pub
        self.sub = ""
        self.uid = ""

    def delete(self):
        subprocess.call(["apt-key", "del", self.pub])

    def get_name(self):
        return "%s\n<small>    %s</small>" % (GLib.markup_escape_text(self.uid), GLib.markup_escape_text(self.pub))

class Mirror():
    def __init__(self, country_code, url, name):
        self.country_code = country_code
        self.url = url
        self.name = name

class Repository():
    def __init__(self, application, line, file, selected, base_mirror_names = None, base_name = None):
        self.application = application
        self.line = line
        self.file = file
        self.selected = selected
        self.base_mirror_names = base_mirror_names
        self.base_name = base_name

    def modify_source_file(self, target_line):
        with open(self.file, "r", encoding="utf-8", errors="ignore") as readfile:
            content = readfile.readlines()
        line = next((s for s in content if s.strip().endswith(self.line)), None)
        if not line:
            # failed to find the line in the file, either the file got modified exernally
            # or the user previously edited the last "deb" or "deb-src" out and we silently
            # deleted it below - FIXME: UI will remain inconsistent until user deletes
            return
        if target_line:
            content[content.index(line)] = "%s%s\n" % ("" if self.selected else "# ", target_line)
        else:
            content.remove(line)

        # If the file no longer contains any "deb" instances, delete it as well
        if not next((s for s in content if "deb " in s or "deb-src " in s), None):
            os.unlink(self.file)
        else:
            with open(self.file, "w", encoding="utf-8", errors="ignore") as writefile:
                writefile.writelines(content)

        self.application.enable_reload_button()

    def switch(self):
        self.selected = (not self.selected)
        self.modify_source_file(self.line)

    def edit(self, newline):
        self.modify_source_file(newline)
        self.line = newline

    def delete(self):
        self.modify_source_file(None)

    def get_ppa_name(self):
        elements = self.line.split(" ")
        name = elements[1].replace("deb-src ", "")
        name = name.replace("deb ", "")
        name = name.replace("http://ppa.launchpad.net/", "")
        name = name.replace("/ubuntu", "")
        name = name.replace("/ppa", "")
        if self.line.startswith("deb-src"):
            suffix = _("Sources")
            name = "%s (%s)" % (name, suffix)
        return "<b>%s</b>\n%s\n%s" % (name, self.line, self.file)

    def get_repository_name(self):
        line = self.line.strip()
        name = line
        release = ""
        if line.startswith("deb cdrom:"):
            name = _("CD-ROM (Installation Disc)")
        else:
            try:
                elements = self.line.split(" ")
                for i, s in enumerate(elements):
                    if "://" in s:
                        #release = " / " + " ".join(elements[i+1:])
                        release = " / " + elements[i+1]
                        protocol, element = s.split("://", 1)
                        if not element.endswith("/"):
                            element += "/"
                        if protocol == "file":
                            name = _("Local Repository")
                            release = ""
                        elif element in self.base_mirror_names:
                            name = self.base_name
                        else:
                            name = element.split("/")[0]
                            subparts = name.split(".")
                            if len(subparts) > 2:
                                if subparts[-2] != "co":
                                    name = subparts[-2].capitalize()
                                else:
                                    name = subparts[-3].capitalize()
                            name = name.replace("Linuxmint", "Linux Mint")
                            name = name.replace("01", "Intel")
                            name = name.replace("Steampowered", "Steam")
                        break
            except:
                pass
            if self.line.startswith("deb-src"):
                name = "%s (%s)" % (name, _("Sources"))

        return "<b>%s</b>%s\n<small><i>%s</i></small>\n<small><i>%s</i></small>" % (name, release, self.line, self.file)

class ComponentSwitchBox(Gtk.Box):
    def __init__(self, application, component, window):
        self.application = application
        self.component = component
        self.window_object = window
        Gtk.Box.__init__(self)
        label = Gtk.Label(label=self.component.description)
        self.pack_start(label, False, False, 0)
        self.switch = Gtk.Switch()
        self.pack_end(self.switch, False, False, 0)
        self.switch.set_active(component.selected)
        self.switch.connect("notify::active", self._on_toggled)
        self.signal_handled = False

    def _on_toggled(self, widget, gparam):
        # As long as the interface isn't fully loaded, don't do anything
        if not self.application._interface_loaded:
            return

        if self.signal_handled:
            self.signal_handled = False
            return

        if widget.get_active() and os.path.exists("/etc/linuxmint/info"):
            if self.component.name == "romeo":
                if self.application.show_confirmation_dialog(self.application._main_window, _("Linux Mint uses Romeo to publish packages which are not tested. Once these packages are tested, they are then moved to the official repositories. Unless you are participating in beta-testing, you should not enable this repository. Are you sure you want to enable Romeo?"), yes_no=True):
                    self.component.selected = widget.get_active()
                    self.application.apply_official_sources()
                else:
                    widget.set_active(not widget.get_active())
                    self.signal_handled = True
            else:
                self.component.selected = widget.get_active()
                self.application.apply_official_sources()
        else:
            self.component.selected = widget.get_active()
            self.application.apply_official_sources()

    def set_active(self, active):
        self.switch.set_active(active)

class MirrorSelectionDialog(object):
    MIRROR_COLUMN = 0
    MIRROR_URL_COLUMN = 1
    MIRROR_COUNTRY_FLAG_COLUMN = 2
    MIRROR_SPEED_COLUMN = 3
    MIRROR_SPEED_LABEL_COLUMN = 4
    MIRROR_TOOLTIP_COLUMN = 5
    MIRROR_NAME_COLUMN = 6

    def __init__(self, application, ui_builder):
        self._application = application
        self._ui_builder = ui_builder

        self._dialog = ui_builder.get_object("mirror_selection_dialog")
        self._dialog.set_transient_for(application._main_window)

        self._dialog.set_title(_("Select a mirror"))

        self._mirrors_model = Gtk.ListStore(object, str, GdkPixbuf.Pixbuf, float, str, str, str)
        # mirror, name, flag, speed, speed label, country code (used to sort by flag), mirror name
        self._treeview = ui_builder.get_object("mirrors_treeview")
        self._treeview.set_model(self._mirrors_model)
        self._treeview.set_headers_clickable(True)
        self._treeview.connect("row-activated", self._row_activated)

        self._mirrors_model.set_sort_column_id(MirrorSelectionDialog.MIRROR_SPEED_COLUMN, Gtk.SortType.DESCENDING)

        r = Gtk.CellRendererPixbuf()
        col = Gtk.TreeViewColumn(_("Country"), r, pixbuf = MirrorSelectionDialog.MIRROR_COUNTRY_FLAG_COLUMN)
        col.set_cell_data_func(r, self.data_func_surface)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_TOOLTIP_COLUMN)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_("Mirror"), r, text = MirrorSelectionDialog.MIRROR_NAME_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_NAME_COLUMN)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_("Speed"), r, text = MirrorSelectionDialog.MIRROR_SPEED_LABEL_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_SPEED_COLUMN)
        col.set_min_width(int(1.1 * SPEED_PIX_WIDTH))

        self._treeview.set_tooltip_column(MirrorSelectionDialog.MIRROR_TOOLTIP_COLUMN)

        self.country_info = CountryInformation()

        with open('/usr/lib/linuxmint/mintSources/countries.json', encoding="utf-8", errors="ignore") as data_file:
            self.countries = json.load(data_file)

    def data_func_surface(self, column, cell, model, iter_, *args):
        pixbuf = model.get_value(iter_, MirrorSelectionDialog.MIRROR_COUNTRY_FLAG_COLUMN)
        surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, self._application.scale)
        cell.set_property("surface", surface)

    def _row_activated(self, treeview, path, view_column):
        self._dialog.response(Gtk.ResponseType.APPLY)

    def get_country(self, country_code):
        for country in self.countries:
            if country["cca2"] == country_code:
                return country
        return None

    def _update_list(self):
        self._mirrors_model.clear()
        for mirror in self.visible_mirrors:
            if mirror.country_code == "WD":
                flag = FLAG_PATH % '_united_nations'
                country_name = _("Worldwide")
            else:
                flag = FLAG_PATH % mirror.country_code.lower()
                country_name = self.country_info.get_country_name(mirror.country_code)
            if not os.path.exists(flag):
                flag = FLAG_PATH % '_generic'
            tooltip = country_name
            if mirror.name != mirror.url:
                tooltip = "%s: %s" % (country_name, mirror.url)
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(flag, -1, FLAG_SIZE * self._application.scale)
            self._mirrors_model.append((
                mirror,
                mirror.url,
                pixbuf,
                0,
                None,
                tooltip,
                mirror.name
            ))

        self._all_speed_tests()

    def get_url_last_modified(self, url):
        try:
            c = pycurl.Curl()
            c.setopt(pycurl.URL, url)
            c.setopt(pycurl.CONNECTTIMEOUT, 5)
            c.setopt(pycurl.TIMEOUT, 30)
            c.setopt(pycurl.FOLLOWLOCATION, 1)
            c.setopt(pycurl.NOBODY, 1)
            c.setopt(pycurl.OPT_FILETIME, 1)
            c.perform()
            filetime = c.getinfo(pycurl.INFO_FILETIME)
            if filetime < 0:
                return None
            else:
                return filetime
        except:
            return None

    def check_mirror_up_to_date(self, url, max_age):
        mirror_timestamp = self.get_url_last_modified(url)
        if mirror_timestamp is None:
            print ("Error: Can't find the age of %s !!" % url)
            return False
        mirror_date = datetime.datetime.fromtimestamp(mirror_timestamp)
        mirror_age = (self.default_mirror_date - mirror_date).days
        # print("age: %d, max: %d - %s"%(mirror_age, max_age, url))
        if (mirror_age > max_age):
            print ("Error: %s is out of date by %d days!" % (url, mirror_age))
            return False
        else:
            # Age is fine :)
            return True

    def check_mint_mirror_up_to_date(self, url):
        if (self.default_mirror_age is None or self.default_mirror_age < 2):
            # print("Skipping Mint mirror check, < 2 days old: %s" % url)
            # If the default server was updated recently, the age is irrelevant (it would measure the time between now and the last update)
            return True
        return self.check_mirror_up_to_date(url, 2)

    def check_base_mirror_up_to_date(self, url):
        return self.check_mirror_up_to_date(url, 14)

    @run_async
    def _all_speed_tests(self):
        model_iters = [] # Don't iterate through iters directly.. we're modifying their orders..
        iter = self._mirrors_model.get_iter_first()
        while iter is not None:
            model_iters.append(iter)
            iter = self._mirrors_model.iter_next(iter)

        for iter in model_iters:
            try:
                if iter is not None:
                    mirror = self._mirrors_model.get_value(iter, MirrorSelectionDialog.MIRROR_COLUMN)
                    if mirror in self.visible_mirrors:
                        url = self._mirrors_model.get_value(iter, MirrorSelectionDialog.MIRROR_URL_COLUMN)
                        self._speed_test (iter, url)
            except Exception as e:
                pass # null types will occur here...

    def _get_speed_label(self, speed):
        if speed > 0:
            divider = (1024 * 1.0)
            represented_speed = (speed / divider)   # translate it to kB/S
            unit = _("kB/s")
            if represented_speed > divider:
                represented_speed = (represented_speed / divider)   # translate it to MB/S
                unit = _("MB/s")
            if represented_speed > divider:
                represented_speed = (represented_speed / divider)   # translate it to GB/S
                unit = _("GB/s")
            num_int_digits = len("%d" % represented_speed)
            if (num_int_digits > 2):
                represented_speed = "%d %s" % (represented_speed, unit)
            else:
                represented_speed = "%.1f %s" % (represented_speed, unit)
            represented_speed = represented_speed.replace(".0", "")
        else:
            represented_speed = ("0 %s") % _("kB/s")
        return represented_speed

    def _speed_test(self, iter, url):
        download_speed = 0
        try:
            up_to_date = False

            if self.is_base:
                test_url = "%s/dists/%s/main/binary-amd64/Packages.gz" % (url, self.codename)
                up_to_date = self.check_base_mirror_up_to_date("%s/ls-lR.gz" % url)
            else:
                test_url = "%s/dists/%s/main/Contents-amd64.gz" % (url, self.codename)
                up_to_date = self.check_mint_mirror_up_to_date("%s/db/version" % url)

            if up_to_date:
                c = pycurl.Curl()
                buff = BytesIO()
                c.setopt(pycurl.URL, test_url)
                c.setopt(pycurl.CONNECTTIMEOUT, 5)
                c.setopt(pycurl.TIMEOUT, 20)
                c.setopt(pycurl.FOLLOWLOCATION, 1)
                c.setopt(pycurl.WRITEFUNCTION, buff.write)
                c.setopt(pycurl.NOSIGNAL, 1)
                c.perform()
                download_speed = c.getinfo(pycurl.SPEED_DOWNLOAD) # bytes/sec
            else:
                # the mirror is not up to date
                download_speed = -1
        except Exception as error:
            print ("Error '%s' on url %s" % (error, url))
            download_speed = 0

        self.show_speed_test_result(iter, download_speed)

    @idle
    def show_speed_test_result(self, iter, download_speed):
        if (iter is not None): # recheck as it can get null
            if download_speed == -1:
                # don't remove from model as this is not thread-safe
                self._mirrors_model.set_value(iter, MirrorSelectionDialog.MIRROR_SPEED_LABEL_COLUMN, _("Obsolete"))
            if download_speed == 0:
                # don't remove from model as this is not thread-safe
                self._mirrors_model.set_value(iter, MirrorSelectionDialog.MIRROR_SPEED_LABEL_COLUMN, _("Unreachable"))
            else:
                self._mirrors_model.set_value(iter, MirrorSelectionDialog.MIRROR_SPEED_COLUMN, download_speed)
                self._mirrors_model.set_value(iter, MirrorSelectionDialog.MIRROR_SPEED_LABEL_COLUMN, self._get_speed_label(download_speed))

    def run(self, mirrors, config, is_base):

        self.config = config
        self.is_base = is_base
        if self.is_base:
            self.codename = self.config["general"]["base_codename"]
            self.default_mirror = self.config["mirrors"]["base_default"]
        else:
            self.codename = self.config["general"]["codename"]
            self.default_mirror = self.config["mirrors"]["default"]

        # Try to find out where we're located...
        try:
            lookup = requests.get('http://geoip.ubuntu.com/lookup').text
            cur_country_code = re.search('<CountryCode>(.*)</CountryCode>', lookup).group(1)
            if cur_country_code == 'None': cur_country_code = None
        except Exception as detail:
            cur_country_code = None  # no internet connection

        self.local_country_code = cur_country_code or os.environ.get('LANG', 'US').split('.')[0].split('_')[-1]  # fallback to LANG location or 'US'

        self.bordering_countries = []
        self.network_neighbors = []
        self.subregion = []
        self.region = []
        self.local_country = self.get_country(self.local_country_code)
        if self.local_country is not None:
            for country in self.countries:
                country_code = country["cca2"]
                if country["region"] == self.local_country["region"]:
                    if country["subregion"] == self.local_country["subregion"]:
                        self.subregion.append(country_code)
                    else:
                        self.region.append(country_code)
                if country["cca3"] in self.local_country["borders"]:
                    self.bordering_countries.append(country_code)
                elif country["cca3"] in self.local_country["networkNeighbors"]:
                    self.network_neighbors.append(country_code)

        self.worldwide_mirrors = []
        self.local_mirrors = []
        self.bordering_mirrors = []
        self.network_neighbors_mirrors = []
        self.subregional_mirrors = []
        self.regional_mirrors = []
        self.official_mirrors = []
        self.other_mirrors = []

        for mirror in mirrors:
            if mirror.country_code == "WD":
                self.worldwide_mirrors.append(mirror)
                print (mirror)
            elif mirror.country_code == self.local_country_code:
                self.local_mirrors.append(mirror)
            elif mirror.country_code in self.bordering_countries:
                self.bordering_mirrors.append(mirror)
            elif mirror.country_code in self.network_neighbors:
                self.network_neighbors_mirrors.append(mirror)
            elif mirror.country_code in self.subregion:
                self.subregional_mirrors.append(mirror)
            elif mirror.country_code in self.region:
                self.regional_mirrors.append(mirror)
            elif mirror.url == self.default_mirror:
                self.official_mirrors.append(mirror)
            else:
                self.other_mirrors.append(mirror)

        self.worldwide_mirrors = sorted(self.worldwide_mirrors, key=lambda x: x.country_code)
        self.bordering_mirrors = sorted(self.bordering_mirrors, key=lambda x: x.country_code)
        self.network_neighbors_mirrors = sorted(self.network_neighbors_mirrors, key=lambda x: x.country_code)
        self.subregional_mirrors = sorted(self.subregional_mirrors, key=lambda x: x.country_code)
        self.regional_mirrors = sorted(self.regional_mirrors, key=lambda x: x.country_code)

        self.visible_mirrors = self.worldwide_mirrors + self.local_mirrors + self.bordering_mirrors + self.network_neighbors_mirrors + self.subregional_mirrors + self.regional_mirrors + self.official_mirrors

        if len(self.visible_mirrors) < 2:
            # We failed to identify the continent/country, let's show all mirrors
            self.visible_mirrors = mirrors

        # Try to find the age of the Mint archive
        self.default_mirror_age = None
        self.default_mirror_date = None

        if self.is_base:
            mirror_timestamp = self.get_url_last_modified("%s/ls-lR.gz" % self.default_mirror)
        else:
            mirror_timestamp = self.get_url_last_modified("%s/db/version" % self.default_mirror)

        if mirror_timestamp is not None:
            self.default_mirror_date = datetime.datetime.fromtimestamp(mirror_timestamp)
            now = datetime.datetime.now()
            self.default_mirror_age = (now - self.default_mirror_date).days

        self._update_list()
        self._dialog.show_all()
        retval = self._dialog.run()
        if retval == Gtk.ResponseType.APPLY:
            try:
                model, path = self._treeview.get_selection().get_selected_rows()
                iter = model.get_iter(path[0])
                res = model.get(iter, MirrorSelectionDialog.MIRROR_URL_COLUMN)[0]
            except:
                res = None
        else:
            res = None
        self._dialog.hide()
        self._mirrors_model.clear()
        return res

class Application(object):
    def __init__(self):
        parser = argparse.ArgumentParser(description="Software sources for Linux Mint")
        parser.add_argument("-n", "--no-update", action="store_true", help="Disable cache refresh prompting")
        args = parser.parse_known_args()

        try:
            known_args = args[0]
            global disable_refresh
            disable_refresh = known_args.no_update
        except (AttributeError, IndexError) as e:
            print(e)

        # Prevent settings from being saved until the interface is fully loaded
        self._interface_loaded = False
        self._currently_applying_sources = False

        self.lsb_codename = subprocess.getoutput("lsb_release -sc")

        glade_file = "/usr/lib/linuxmint/mintSources/mintsources.glade"

        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintsources")
        self.builder.add_from_file(glade_file)
        self._main_window = self.builder.get_object("main_window")
        self._info_box = self.builder.get_object("box_infobar")
        self._info_revealer = self.builder.get_object("info_revealer")

        self._main_window.set_title(_("Software Sources"))

        self._main_window.set_icon_name("software-sources")

        self.scale = self._main_window.get_scale_factor()

        self._official_repositories_page = self.builder.get_object("official_repositories_page")

        self.apt = mintcommon.aptdaemon.APT(self._main_window)

        config_parser = configparser.RawConfigParser()
        config_parser.read("/usr/share/mintsources/%s/mintsources.conf" % self.lsb_codename)
        self.config = {}
        self.optional_components = []
        self.system_keys = []
        for section in config_parser.sections():
            if section.startswith("optional_component"):
                component_name = config_parser.get(section, "name")
                component_description = config_parser.get(section, "description")
                if component_name in ["backport", "backports"]:
                    component_description = "%s (%s)" % (_("Backported packages"), component_name)
                elif component_name in ["romeo", "unstable"]:
                    component_description = "%s (%s)" % (_("Unstable packages"), component_name)
                component = Component(component_name, component_description, False)
                self.optional_components.append(component)
            elif section.startswith("key"):
                self.system_keys.append(config_parser.get(section, "pub"))
            else:
                self.config[section] = {}
                for param in config_parser.options(section):
                    self.config[section][param] = config_parser.get(section, param)

        if self.config["general"]["use_ppas"] == "false":
            self.builder.get_object("main_stack").remove(self.builder.get_object("ppas_page"))

        self.builder.get_object("label_mirror_description").set_markup("%s (%s)" % (_("Main"), self.config["general"]["codename"]) )
        self.builder.get_object("label_base_mirror_description").set_markup("%s (%s)" % (_("Base"), self.config["general"]["base_codename"]) )

        self.selected_components = []
        if (len(self.optional_components) > 0):
            for i in range(len(self.optional_components)):
                component = self.optional_components[i]
                cb = ComponentSwitchBox(self, component, self._main_window)
                component.set_widget(cb)
                self.builder.get_object("box_optional_components").pack_start(cb, True, False, 0)

            self.builder.get_object("box_optional_components").show_all()

        self.mirrors = self.read_mirror_list(self.config["mirrors"]["mirrors"])
        self.base_mirrors = self.read_mirror_list(self.config["mirrors"]["base_mirrors"])

        self.base_mirror_names = set()
        for mirror in self.base_mirrors:
            m = mirror.name.split("://")[1]
            if not m.endswith("/"):
                m += "/"
            self.base_mirror_names.add(m)

        if "debian" in self.config["mirrors"]["base_default"]:
            self.base_name = "Debian"
        else:
            self.base_name = "Ubuntu"

        self.read_source_lists()

        # Add PPAs
        self._ppa_model = Gtk.ListStore(object, bool, str)
        self._ppa_treeview = self.builder.get_object("treeview_ppa")
        self._ppa_treeview.set_model(self._ppa_model)
        self._ppa_treeview.set_headers_clickable(True)
        self._ppa_treeview.connect("row-activated", self.on_ppa_treeview_doubleclick)
        selection = self._ppa_treeview.get_selection()
        selection.connect("changed", self.ppa_selected)

        self._ppa_model.set_sort_column_id(2, Gtk.SortType.ASCENDING)

        r = Gtk.CellRendererToggle()
        r.connect("toggled", self.ppa_toggled)
        col = Gtk.TreeViewColumn(_("Enabled"), r)
        col.set_cell_data_func(r, self.datafunction_checkbox)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(1)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_("PPA"), r, markup = 2)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(2)

        self.refresh_ppa_model()

        # Add repositories
        self._repository_model = Gtk.ListStore(object, bool, str)
        self._repository_treeview = self.builder.get_object("treeview_repository")
        self._repository_treeview.set_model(self._repository_model)
        self._repository_treeview.set_headers_clickable(True)
        repo_selection = self._repository_treeview.get_selection()
        repo_selection.connect("changed", self.repo_selected)

        self._repository_model.set_sort_column_id(2, Gtk.SortType.ASCENDING)

        r = Gtk.CellRendererToggle()
        r.connect("toggled", self.repository_toggled)
        col = Gtk.TreeViewColumn(_("Enabled"), r)
        col.set_cell_data_func(r, self.datafunction_checkbox)
        self._repository_treeview.append_column(col)
        col.set_sort_column_id(1)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_("Repository"), r, markup = 2)
        self._repository_treeview.append_column(col)
        col.set_sort_column_id(2)

        self.refresh_repository_model()

        self._keys_model = Gtk.ListStore(object, str)
        self._keys_treeview = self.builder.get_object("treeview_keys")
        self._keys_treeview.set_model(self._keys_model)
        self._keys_treeview.set_headers_clickable(True)
        keys_selection = self._keys_treeview.get_selection()
        keys_selection.connect("changed", self.key_selected)

        self._keys_model.set_sort_column_id(1, Gtk.SortType.ASCENDING)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_("Key"), r, markup = 1)
        self._keys_treeview.append_column(col)
        col.set_sort_column_id(1)

        self.load_keys()

        if not os.path.exists("/etc/apt/sources.list.d/official-package-repositories.list"):
            print ("Sources missing, generating default sources list!")
            self.generate_missing_sources()

        self.detect_official_sources()

        self.builder.get_object("revert_button").connect("clicked", self.revert_to_default_sources)

        self._main_window.connect("delete_event", lambda w,e: Gtk.main_quit())

        self.mirror_selection_dialog = MirrorSelectionDialog(self, self.builder)

        self.builder.get_object("button_mirror").connect("clicked", self.select_new_mirror)
        self.builder.get_object("button_base_mirror").connect("clicked", self.select_new_base_mirror)

        self.builder.get_object("button_ppa_add").connect("clicked", self.add_ppa)
        self.builder.get_object("button_ppa_edit").connect("clicked", self.edit_ppa)
        self.builder.get_object("button_ppa_remove").connect("clicked", self.remove_ppa)
        self.builder.get_object("button_ppa_examine").connect("clicked", self.examine_ppa)

        self.builder.get_object("button_repository_add").connect("clicked", self.add_repository)
        self.builder.get_object("button_repository_edit").connect("clicked", self.edit_repository)
        self.builder.get_object("button_repository_remove").connect("clicked", self.remove_repository)

        self.builder.get_object("button_keys_add").connect("clicked", self.add_key)
        self.builder.get_object("button_keys_fetch").connect("clicked", self.fetch_key)
        self.builder.get_object("button_keys_remove").connect("clicked", self.remove_key)

        self.builder.get_object("button_mergelist").connect("clicked", self.fix_mergelist)
        self.builder.get_object("button_purge").connect("clicked", self.fix_purge)
        self.builder.get_object("button_duplicates").connect("clicked", self.remove_duplicates)
        self.builder.get_object("button_fix_missing_keys").connect("clicked", self.fix_missing_keys)
        self.builder.get_object("button_remove_foreign").connect("clicked", self.remove_foreign)
        self.builder.get_object("button_downgrade_foreign").connect("clicked", self.downgrade_foreign)

        self.builder.get_object("source_code_switch").connect("notify::active", self.apply_official_sources)
        self.builder.get_object("debug_symbol_switch").connect("notify::active", self.apply_official_sources)

        # From now on, we handle modifications to the settings and save them when they happen
        self._interface_loaded = True

    def refresh_repository_model(self):
        self._repository_model.clear()
        if len(self.repositories):
            for repository in self.repositories:
                self._repository_model.append((repository, repository.selected, repository.get_repository_name()))

    def refresh_ppa_model(self):
        self._ppa_model.clear()
        if (len(self.ppas) > 0):
            for repository in self.ppas:
                self._ppa_model.append((repository, repository.selected, repository.get_ppa_name()))

    def read_source_lists(self):
        self.repositories = []
        self.ppas = []
        source_files = []
        if os.path.exists("/etc/apt/sources.list"):
            source_files.append("/etc/apt/sources.list")
        for file in os.listdir("/etc/apt/sources.list.d"):
            if file.endswith(".list"):
                source_files.append("/etc/apt/sources.list.d/%s" % file)

        if "/etc/apt/sources.list.d/official-package-repositories.list" in source_files:
            source_files.remove("/etc/apt/sources.list.d/official-package-repositories.list")

        if "/etc/apt/sources.list.d/official-source-repositories.list" in source_files:
            source_files.remove("/etc/apt/sources.list.d/official-source-repositories.list")

        if "/etc/apt/sources.list.d/official-dbgsym-repositories.list" in source_files:
            source_files.remove("/etc/apt/sources.list.d/official-dbgsym-repositories.list")

        for source_file in source_files:
            with open(source_file, "r", encoding="utf-8", errors="ignore") as file:
                for line in file.readlines():
                    line = line.strip()
                    if line != "":
                        selected = True
                        if line.startswith("#"):
                            line = line.replace('#', '').strip()
                            selected = False
                        if line.startswith("deb"):
                            repository = Repository(self, line, source_file, selected, self.base_mirror_names, self.base_name)
                            if "ppa.launchpad" in line and self.config["general"]["use_ppas"] != "false":
                                self.ppas.append(repository)
                            else:
                                self.repositories.append(repository)

    def set_button_text(self, label, text):
        label.set_text(text)
        if len(text) > BUTTON_LABEL_MAX_LENGTH:
            label.set_tooltip_text(text)
            label.set_max_width_chars(BUTTON_LABEL_MAX_LENGTH)
            label.set_ellipsize(Pango.EllipsizeMode.END)

    def read_mirror_list(self, path):
        mirror_list = []
        country_code = None
        mirrorsfile = open(path, "r", encoding="utf-8", errors="ignore")
        for line in mirrorsfile.readlines():
            line = line.strip()
            if line != "":
                if ("#LOC:" in line):
                    country_code = line.split(":")[1]
                else:
                    if country_code is not None:
                        if ("ubuntu-ports" not in line):
                            elements = line.split(" ")
                            url = elements[0]
                            if len(elements) > 1:
                                name = " ".join(elements[1:])
                            else:
                                name = url
                            if url[-1] == "/":
                                url = url[:-1]
                            mirror = Mirror(country_code, url, name)
                            mirror_list.append(mirror)
        if path.endswith("Debian.mirrors"):
            mirror = Mirror("WD", "https://deb.debian.org/debian", "https://deb.debian.org/debian/")
            mirror_list.append(mirror)
        return mirror_list

    def remove_foreign(self, widget):
        subprocess.Popen(["/usr/lib/linuxmint/mintSources/foreign_packages.py", "remove"])

    def downgrade_foreign(self, widget):
        subprocess.Popen(["/usr/lib/linuxmint/mintSources/foreign_packages.py", "downgrade"])

    def fix_purge(self, widget):
        os.system("aptitude purge ~c -y")
        image = Gtk.Image()
        image.set_from_icon_name("preferences-other-symbolic", Gtk.IconSize.DIALOG)
        self.show_confirmation_dialog(self._main_window, _("There is no more residual configuration on the system."), image, affirmation=True)

    def fix_mergelist(self, widget):
        os.system("rm /var/lib/apt/lists/* -vrf")
        image = Gtk.Image()
        image.set_from_icon_name("preferences-other-symbolic", Gtk.IconSize.DIALOG)
        self.show_confirmation_dialog(self._main_window, _("The problem was fixed. Please reload the cache."), image, affirmation=True)
        self.enable_reload_button()

    def remove_duplicates(self, widget):
        knownlines = set()

        # Parse official sources first
        for listfile in glob.glob("/etc/apt/sources.list.d/official*.list"):
            with open(listfile, encoding="utf-8", errors="ignore") as f:
                lines = []
                for line in f.readlines():
                    line = line.strip()
                    if line not in knownlines:
                        if not line.startswith('#'):
                            knownlines.add(line)

        # Now parse other sources and remove any duplicates
        found_duplicates = False
        for listfile in glob.glob("/etc/apt/sources.list") + glob.glob("/etc/apt/sources.list.d/*.list"):
            if not listfile.startswith("/etc/apt/sources.list.d/official"):
                with open(listfile, encoding="utf-8", errors="ignore") as f:
                    lines = []
                    found_duplicates_in_this_file = False
                    for line in f.readlines():
                        line = line.strip()
                        if line not in knownlines:
                            if not line.startswith('#'):
                                knownlines.add(line)
                            lines.append(line)
                        else:
                            found_duplicates = True
                            found_duplicates_in_this_file = True
                if found_duplicates_in_this_file:
                    print("Found duplicates in %s, rewriting it." % listfile)
                    if not lines:
                        os.unlink(listfile)
                    else:
                        with open(listfile, 'w', encoding="utf-8", errors="ignore") as f:
                            for line in lines:
                                f.write("%s\n" % line)
        image = Gtk.Image()
        image.set_from_icon_name("preferences-other-symbolic", Gtk.IconSize.DIALOG)
        if found_duplicates:
            self.show_confirmation_dialog(self._main_window, _("Duplicate entries were removed. Please reload the cache."), image, affirmation=True)
            self.enable_reload_button()
            self.read_source_lists()
            self.refresh_ppa_model()
            self.refresh_repository_model()
        else:
            self.show_confirmation_dialog(self._main_window, _("No duplicate entries were found."), image, affirmation=True)

    def fix_missing_keys(self, widget):
        image = Gtk.Image()
        image.set_from_icon_name("dialog-password-symbolic", Gtk.IconSize.DIALOG)

        #get paths from apt
        apt_pkg.init()
        trusted = apt_pkg.config.find_file("Dir::Etc::trusted")
        trustedparts = apt_pkg.config.find_dir("Dir::Etc::trustedparts")
        lists = apt_pkg.config.find_dir("Dir::State::lists")
        if not os.path.isfile(trusted) or not os.path.isdir(trustedparts) or not os.path.isdir(lists):
            self.show_confirmation_dialog(self._main_window,
                _("Error with your APT configuration, you may have to reload the cache first."),
                image, affirmation=True)
            return

        self._main_window.get_window().set_cursor(Gdk.Cursor(Gdk.CursorType.WATCH))
        Gdk.flush()

        cmd_stub = ["gpg", "--no-default-keyring", "--no-options"]
        keyrings = [trusted] + glob.glob("%s*.gpg" % trustedparts)
        for keyring in keyrings:
            cmd_stub.extend(["--keyring", keyring])

        # build repository list
        class RepositoryInfo():
            def __init__(self, path, uri):
                self.path = path
                self.uri = uri
                self.added = False
                self.missing = False

        repositories = []
        tempdir = None
        apt_source_list = apt_pkg.SourceList()
        apt_source_list.read_main_list()
        for metaindex in apt_source_list.list:
            # can't seem to get the metaindex filename from apt_pkg so rebuild it:
            filename = apt_pkg.uri_to_filename("%sdists/%s/" % (metaindex.uri, metaindex.dist))
            path = os.path.join(lists, filename + "InRelease")
            if not os.path.isfile(path):
                path = os.path.join(lists, filename + "Release")
                if not os.path.isfile(path):
                    path = None
            if not path:
                print("W: Release file missing for %s, trying to retrieve" % metaindex.uri)
                data = requests.get("%sdists/%s/InRelease" % (metaindex.uri, metaindex.dist))
                data_gpg = None
                if not data.ok:
                    data = requests.get("%sdists/%s/Release" % (metaindex.uri, metaindex.dist))
                    data_gpg = requests.get("%sdists/%s/Release.gpg" % (metaindex.uri, metaindex.dist))
                if data.ok and (not data_gpg or data_gpg.ok):
                    if not tempdir:
                        tempdir = tempfile.TemporaryDirectory(prefix="mintsources-")
                    filename_stub = apt_pkg.uri_to_filename("%sdists/%s/" % (metaindex.uri, metaindex.dist))
                    if data_gpg:
                        path = os.path.join(tempdir.name, filename_stub + "Release")
                        with open(path + ".gpg", "w") as f:
                            f.write(data_gpg.text)
                    else:
                        path = os.path.join(tempdir.name, filename_stub + "InRelease")
                    with open(path, "w") as f:
                        f.write(data.text)
            if path:
                repositories.append(RepositoryInfo(path, metaindex.uri))
            else:
                print("E: Could not retrieve release file for %s" % metaindex.uri, file=sys.stderr)

        r = re.compile(r"^gpg\:\s+using \S+ key (.+)$", re.MULTILINE | re.IGNORECASE)
        # try to verify all repository lists using gpg
        for repository in repositories:
            if repository.path.endswith("_InRelease"):
                command = cmd_stub + (["--verify", repository.path])
            else:
                command = cmd_stub + (["--verify", repository.path + ".gpg", repository.path])
            result = subprocess.run(command, stderr=subprocess.PIPE, env={"LC_ALL": "C"})
            if result.returncode == 2:
                # missing key
                repository.missing = True
                message = result.stderr.decode()
                try:
                    # parse gpg output for key id or fingerprint
                    key = r.search(message).group(1)
                    key = re.sub(r"\s", "", key)
                    # get key from keyserver
                    success = add_new_key(key)
                    if not success:
                        raise ValueError("Retrieving key %s failed" % key)
                    repository.added = True
                except (AttributeError, IndexError):
                    print("E: Could not identify the key in the output:\n\n%s" % message, file=sys.stderr)
                    continue
                except ValueError as e:
                    print("E: %s" % str(e), file=sys.stderr)
                    continue

        if tempdir:
            tempdir.cleanup()

        self._main_window.get_window().set_cursor(None)

        keys_added = [x.uri for x in repositories if x.added]
        keys_missing = [x.uri for x in repositories if (x.missing and not x.added)]
        keys_missing_count = len(keys_missing)
        keys_added_count = len(keys_added)
        if keys_missing_count or keys_added_count:
            if not keys_missing_count:
                msg_info = _("All missing keys were successfully added.")
            else:
                msg_info = _("Not all missing keys could be found.")
            msg_log = ""
            if keys_added:
                msg_repos_added = _("Keys were added for the following repositories:")
                repo_list = "\n".join([' - %s' % uri for uri in keys_added])
                msg_log = "%s\n%s\n" % (msg_repos_added, repo_list)
            if keys_missing:
                msg_repos_missing = _("Keys are still missing for the following repositories:")
                msg_action = _("Add the remaining missing key(s) manually or remove the corresponding repositories or PPAs.")
                repo_list = "\n".join([' - %s' % uri for uri in keys_missing])
                if keys_added:
                    msg_log += "\n"
                msg_log = "%s%s\n%s\n\n%s\n" % (msg_log, msg_repos_missing, repo_list, msg_action)

            msg = "%s\n\n%s" % (msg_info, msg_log)
            if keys_added:
                msg += "\n%s" % _("Please reload the cache.")
                self.load_keys()
                self.enable_reload_button()
            self.show_confirmation_dialog(self._main_window, msg, image, affirmation=True)
        else:
            self.show_confirmation_dialog(self._main_window, _("No missing keys were found."), image, affirmation=True)

    def load_keys(self):
        self.keys = []
        key = None
        output = subprocess.getoutput("apt-key list")
        lines = []
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("/etc/apt"):
                continue
            if line.startswith("-----"):
                continue
            if line == "":
                continue
            lines.append(line)

        for key_data in "\n".join(lines).split("pub   "):
            key_data = key_data.split("\n")
            if len(key_data) > 3:
                extra = key_data[0]
                pub = key_data[1]
                name = key_data[2]
                name = name.replace("uid ", "")
                if "]" in name:
                    name = name.split("]")[1].strip()
                key = Key(pub)
                key.uid = name
                if pub not in self.system_keys:
                    self.keys.append(key)

        self._keys_model.clear()
        for key in self.keys:
            self._keys_model.append((key, key.get_name()))

    def add_key(self, widget):
        dialog = Gtk.FileChooserDialog(_("Open.."),
                               self._main_window,
                               Gtk.FileChooserAction.OPEN,
                               (_("Cancel"), Gtk.ResponseType.CANCEL,
                                _("Open"), Gtk.ResponseType.OK))
        dialog.set_default_response(Gtk.ResponseType.OK)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            subprocess.call(["apt-key", "add", dialog.get_filename()])
            self.load_keys()
            self.enable_reload_button()
        dialog.destroy()

    def fetch_key(self, widget):
        image = Gtk.Image()
        image.set_from_icon_name("dialog-password-symbolic", Gtk.IconSize.DIALOG)
        fingerprint = self.show_entry_dialog(self._main_window, _("Please enter the fingerprint of the public key you want to download from keyserver.ubuntu.com:"), "", image)
        if fingerprint is not None:
            add_key_remote(fingerprint)
            self.load_keys()
            self.enable_reload_button()

    def add_new_key(self, key):
        add_new_key(key)
        self.load_keys()

    def remove_key(self, widget):
        selection = self._keys_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            key = model.get(iter, 0)[0]
            image = Gtk.Image()
            image.set_from_icon_name("dialog-password-symbolic", Gtk.IconSize.DIALOG)
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this key?"), image, yes_no=True)):
                key.delete()
                self.load_keys()

    def key_selected(self, selection):
        self.builder.get_object("button_keys_remove").set_sensitive(True)

    def add_ppa(self, widget):
        image = Gtk.Image()
        image.set_from_icon_name("process-stop-symbolic", Gtk.IconSize.DIALOG)
        default_line = "ppa:ppa-owner/ppa-name"
        start_line = default_line
        clipboard_text = self.get_clipboard_text("ppa")
        if clipboard_text is None:
            clipboard_text = self.get_clipboard_text("https")
        if clipboard_text is not None:
            start_line = clipboard_text

        line = self.show_entry_dialog(self._main_window, _("Please enter the name or the URL of the PPA you want to add:"), start_line, image)
        if line:
            # If the user pasted the launchpad URL, parse that into a ppa: line
            if line.startswith("https://launchpad.net/"):
                match = re.match(r'https://launchpad.net/~(\S+)/\+archive/ubuntu/(\S+)', line.split("?", 1)[0])
                if match:
                    line = "ppa:%s/%s" % (match.group(1), match.group(2))
            try:
                if not line.startswith("ppa:") or line == default_line:
                    raise ValueError(_("The name of the PPA you entered isn't formatted correctly."))
                user, sep, ppa_name = line.split(":", 1)[1].partition("/")
                ppa_name = ppa_name or "ppa"
                ppa_info = get_ppa_info_from_lp(user, ppa_name, self.config["general"]["base_codename"])
            except Exception as error_msg:
                self.show_error_dialog(self._main_window, error_msg)
                return

            image = Gtk.Image()
            image.set_from_icon_name("process-stop-symbolic", Gtk.IconSize.DIALOG)
            info_text = "%s\n\n%s\n\n%s\n\n%s" % (line,
                self.format_string(ppa_info["displayname"]),
                self.format_string(ppa_info["description"]), str(ppa_info["web_link"]))
            if self.show_confirm_ppa_dialog(self._main_window, info_text):
                (deb_line, file) = expand_ppa_line(line.strip(), self.config["general"]["base_codename"])
                deb_line = expand_http_line(deb_line, self.config["general"]["base_codename"])
                debsrc_line = 'deb-src' + deb_line[3:]

                # Add the key if not in keyring
                self.add_new_key(ppa_info["signing_key_fingerprint"])

                # Add the PPA in sources.list.d
                sources_enabled = self.builder.get_object("source_code_switch").get_active()
                with open(file, "w", encoding="utf-8", errors="ignore") as text_file:
                    text_file.write("%s\n" % deb_line)
                    text_file.write("%s%s\n" % ("" if sources_enabled else "# ", debsrc_line))

                # Add the package line to the UI or replace it if it exists
                def add_to_ui(line, selected=True):
                    repo = next((repo for repo in self.ppas if (repo.line == line and repo.file == file)), None)
                    if repo:
                        iter = next((item.iter for item in self._ppa_model if line in self._ppa_model.get_value(item.iter, 2).split("\n")), None)
                        if iter:
                            self._ppa_model.remove(iter)
                        self.ppas.remove(repo)
                    repository = Repository(self, line, file, selected)
                    self.ppas.append(repository)
                    tree_iter = self._ppa_model.append((repository, selected, repository.get_ppa_name()))

                add_to_ui(deb_line)
                add_to_ui(debsrc_line, sources_enabled)

                self.enable_reload_button()


    def format_string(self, text):
        if text is None:
            text = ""
        text = text.replace("<", "&lt;").replace(">", "&gt;")
        return text

    def edit_ppa(self, widget):
        selection = self._ppa_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            repository = model.get(iter, 0)[0]
            url = self.show_entry_dialog(self._main_window, _("Edit the URL of the PPA"), repository.line)
            if url is not None:
                repository.edit(url)
                model.set_value(iter, 2, repository.get_ppa_name())

    def remove_ppa(self, widget):
        selection = self._ppa_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            repository = model.get(iter, 0)[0]
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this PPA?"), yes_no=True)):
                model.remove(iter)
                repository.delete()
                self.ppas.remove(repository)

    def ppa_selected(self, selection):
        self.builder.get_object("button_ppa_edit").set_sensitive(True)
        self.builder.get_object("button_ppa_remove").set_sensitive(True)

        try:
            self.builder.get_object("button_ppa_examine").set_sensitive(False)
            (model, iter) = selection.get_selected()
            if (iter != None):
                repository = model.get_value(iter, 0)
                ppa_name = model.get_value(iter, 2)
                if repository.selected and repository.line.startswith("deb http://ppa.launchpad.net"):
                    self.builder.get_object("button_ppa_examine").set_sensitive(True)
        except Exception as detail:
            print (detail)

    def on_ppa_treeview_doubleclick(self, treeview, path, column):
        self.examine_ppa(None)

    def examine_ppa(self, widget):
        try:
            selection = self._ppa_treeview.get_selection()
            (model, iter) = selection.get_selected()
            if (iter != None):
                repository = model.get_value(iter, 0)
                ppa_name = model.get_value(iter, 2)
                if repository.selected and repository.line.startswith("deb http://ppa.launchpad.net"):
                    line = repository.line.split()[1].replace("http://ppa.launchpad.net/", "")
                    if line.endswith("/ubuntu"):
                        line = line[:-7]
                        ppa_owner, ppa_name = line.split("/")
                        architecture = subprocess.getoutput("dpkg --print-architecture")
                        ppa_file = "/var/lib/apt/lists/ppa.launchpad.net_%s_%s_ubuntu_dists_%s_main_binary-%s_Packages" % (ppa_owner, ppa_name, self.config["general"]["base_codename"], architecture)
                        if os.path.exists(ppa_file):
                            os.system("/usr/lib/linuxmint/mintSources/ppa_browser.py %s %s %s &" % (self.config["general"]["base_codename"], ppa_owner, ppa_name))
                        else:
                            print ("%s not found!" % ppa_file)
                            self.show_error_dialog(self._main_window, _("The content of this PPA is not available. Please refresh the cache and try again."))
        except Exception as detail:
            print (detail)

    def repo_selected(self, selection):
        self.builder.get_object("button_repository_edit").set_sensitive(True)
        self.builder.get_object("button_repository_remove").set_sensitive(True)

    def add_repository(self, widget):
        image = Gtk.Image()
        image.set_from_icon_name("network-workgroup-symbolic", Gtk.IconSize.DIALOG)
        start_line = ""
        default_line = "deb http://packages.domain.com/ %s main" % self.config["general"]["base_codename"]
        clipboard_text = self.get_clipboard_text("deb")
        if clipboard_text != None:
            start_line = clipboard_text
        else:
            start_line = default_line

        line = self.show_entry_dialog(self._main_window, _("Please enter the name of the repository you want to add:"), start_line, image)
        if not line or line == default_line:
            return
        line = expand_http_line(line, self.config["general"]["base_codename"])
        if repo_malformed(line):
            self.show_confirmation_dialog(self._main_window, _("Malformed input, repository not added."), image, affirmation=True)
        else:
            if not repo_exists(line):
                # Add the repository in sources.list.d
                with open(additional_repositories_file, "a", encoding="utf-8", errors="ignore") as f:
                    f.write("%s\n" % line)
                # Add the line in the UI
                repository = Repository(self, line, additional_repositories_file, True, self.base_mirror_names, self.base_name)
                self.repositories.append(repository)
                tree_iter = self._repository_model.append((repository, repository.selected, repository.get_repository_name()))
                self.enable_reload_button()
            else:
                self.show_confirmation_dialog(self._main_window, _("This repository is already configured, you cannot add it a second time."), image, affirmation=True)

    def edit_repository(self, widget):
        selection = self._repository_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            repository = model.get(iter, 0)[0]
            url = self.show_entry_dialog(self._main_window, _("Edit the URL of the repository"), repository.line)
            if url is not None:
                repository.edit(url)
                model.set_value(iter, 2, repository.get_repository_name())

    def remove_repository(self, widget):
        selection = self._repository_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            repository = model.get(iter, 0)[0]
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this repository?"), yes_no=True)):
                model.remove(iter)
                repository.delete()
                self.repositories.remove(repository)

    def show_confirmation_dialog(self, parent, message, image=None, affirmation=None, yes_no=False):
        buttons = Gtk.ButtonsType.OK_CANCEL
        default_button = Gtk.ResponseType.OK
        confirmation_button = Gtk.ResponseType.OK
        if yes_no:
            buttons = Gtk.ButtonsType.YES_NO
            default_button = Gtk.ResponseType.NO
            confirmation_button = Gtk.ResponseType.YES

        if affirmation is None:
            d = Gtk.MessageDialog(parent,
                              Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                              Gtk.MessageType.WARNING,
                              buttons,
                              message)
        else:
            d = Gtk.MessageDialog(parent,
                              Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                              Gtk.MessageType.INFO,
                              Gtk.ButtonsType.OK,
                              message)
        if image is not None:
            image.show()
            d.set_image(image)

        d.set_default_response(default_button)
        r = d.run()
        d.destroy()
        if r == confirmation_button:
            return True
        else:
            return False

    def show_confirm_ppa_dialog(self, parent, message):
        b = Gtk.TextBuffer()
        b.set_text(message)
        t =  Gtk.TextView()
        t.set_buffer(b)
        t.set_wrap_mode(Pango.WrapMode.WORD)
        s = Gtk.ScrolledWindow()
        s.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        s.set_shadow_type(Gtk.ShadowType.OUT)
        default_button = Gtk.ResponseType.ACCEPT
        confirmation_button = Gtk.ResponseType.ACCEPT
        d = Gtk.Dialog(None, parent,
                       Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                       (_("Cancel"), Gtk.ResponseType.REJECT,
                       _("OK"), Gtk.ResponseType.ACCEPT))
        d.set_size_request(550, 400)
        d.get_content_area().pack_start(s, True, True, 0)
        d.set_title("")
        s.show()
        s.add(t)
        t.show()
        d.set_default_response(default_button)
        r = d.run()
        d.destroy()
        if r == confirmation_button:
            return True
        else:
            return False

    def show_error_dialog(self, parent, message, image=None):
        d = Gtk.MessageDialog(parent,
                              Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                              Gtk.MessageType.ERROR,
                              Gtk.ButtonsType.OK,
                              message)

        if image is not None:
            image.show()
            d.set_image(image)

        d.set_default_response(Gtk.ResponseType.OK)
        r = d.run()
        d.destroy()
        if r == Gtk.ResponseType.OK:
            return True
        else:
            return False

    def show_entry_dialog(self, parent, message, default='', image=None):
        d = Gtk.MessageDialog(parent,
                              Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
                              Gtk.MessageType.QUESTION,
                              Gtk.ButtonsType.OK_CANCEL,
                              message)

        if image is not None:
            image.show()
            d.set_image(image)

        entry = Gtk.Entry()
        entry.set_text(default)
        entry.set_margin_start(6)
        entry.set_margin_end(6)
        entry.show()
        d.get_content_area().pack_end(entry, False, False, 0)
        entry.connect('activate', lambda _: d.response(Gtk.ResponseType.OK))
        d.set_default_response(Gtk.ResponseType.OK)

        r = d.run()
        text = entry.get_text()
        d.destroy()
        if r == Gtk.ResponseType.OK:
            return text
        else:
            return None

    def datafunction_checkbox(self, column, cell, model, iter, data):
        cell.set_property("activatable", True)
        if (model.get_value(iter, 0).selected):
            cell.set_property("active", True)
        else:
            cell.set_property("active", False)

    def ppa_toggled(self, renderer, path):
        iter = self._ppa_model.get_iter(path)
        if (iter != None):
            repository = self._ppa_model.get_value(iter, 0)
            repository.switch()
            self.builder.get_object("button_ppa_examine").set_sensitive(repository.selected)

    def repository_toggled(self, renderer, path):
        iter = self._repository_model.get_iter(path)
        if (iter != None):
            repository = self._repository_model.get_value(iter, 0)
            repository.switch()

    def select_new_mirror(self, widget):
        url = self.mirror_selection_dialog.run(self.mirrors, self.config, False)
        if url is not None and self.selected_mirror != url:
            self.selected_mirror = url
            self.builder.get_object("label_mirror_name").set_text(self.selected_mirror)
            self.apply_official_sources()

    def select_new_base_mirror(self, widget):
        url = self.mirror_selection_dialog.run(self.base_mirrors, self.config, True)
        if url is not None and self.selected_base_mirror != url:
            self.selected_base_mirror = url
            self.builder.get_object("label_base_mirror_name").set_text(self.selected_base_mirror)
            self.apply_official_sources()

    def run(self):
        self._main_window.show_all()
        Gtk.main()

    def revert_to_default_sources(self, widget):
        self.selected_mirror = self.config["mirrors"]["default"]
        self.builder.get_object("label_mirror_name").set_text(self.selected_mirror)
        self.selected_base_mirror = self.config["mirrors"]["base_default"]
        self.builder.get_object("label_base_mirror_name").set_text(self.selected_base_mirror)

        self._currently_applying_sources = True
        self.builder.get_object("source_code_switch").set_active(False)
        self.builder.get_object("debug_symbol_switch").set_active(False)

        for component in self.optional_components:
            component.selected = False
            component.widget.set_active(False)

        self._currently_applying_sources = False

        self.apply_official_sources()


    def enable_reload_button(self):
        if disable_refresh:
            global sources_changed
            sources_changed = True
            return

        if not self._info_revealer.get_reveal_child():
            if self._info_box.get_children() == []:
                infobar = Gtk.InfoBar()
                infobar.set_message_type(Gtk.MessageType.INFO)
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                image = Gtk.Image.new_from_icon_name("dialog-information-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
                box.pack_start(image, False, False, 0)
                info_label = Gtk.Label()
                infobar_message = "<b>%s</b>\n%s" % (_("Your configuration changed"), _("Click OK to update your APT cache"))
                info_label.set_markup(infobar_message)
                box.pack_start(info_label, False, False, 0)
                infobar.get_content_area().pack_start(box, False, False, 0)
                infobar.add_button(_("OK"), Gtk.ResponseType.OK)
                infobar.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
                infobar.connect("response", self._on_infobar_response)
                infobar.show_all()
                self._info_box.pack_start(infobar, True, True, 0)

            self._info_revealer.set_reveal_child(True)

    def _on_infobar_response(self, infobar, response_id):
        self._info_revealer.set_reveal_child(False)

        if response_id == Gtk.ResponseType.OK:
            self.apt.update_cache()

    def apply_official_sources(self, widget=None, gparam=None):
        # As long as the interface isn't fully loaded, don't save anything
        if not self._interface_loaded:
            return

        if self._currently_applying_sources:
            return

        self.update_flags()

        # Check which components are selected
        selected_components = []
        for component in self.optional_components:
            if component.selected:
                selected_components.append(component.name)

        # Update official packages repositories
        os.system("rm -f /etc/apt/sources.list.d/official-package-repositories.list")
        template = open('/usr/share/mintsources/%s/official-package-repositories.list' % self.lsb_codename, 'r', encoding="utf-8", errors="ignore").read()
        template = template.replace("$codename", self.config["general"]["codename"])
        template = template.replace("$basecodename", self.config["general"]["base_codename"])
        template = template.replace("$optionalcomponents", ' '.join(selected_components))
        template = template.replace("$mirror", self.selected_mirror)
        template = template.replace("$basemirror", self.selected_base_mirror)

        with open("/etc/apt/sources.list.d/official-package-repositories.list", "w", encoding="utf-8", errors="ignore") as text_file:
            text_file.write(template)

        # Update official sources repositories
        os.system("rm -f /etc/apt/sources.list.d/official-source-repositories.list")
        if (self.builder.get_object("source_code_switch").get_active()):
            template = open('/usr/share/mintsources/%s/official-source-repositories.list' % self.lsb_codename, 'r', encoding="utf-8", errors="ignore").read()
            template = template.replace("$codename", self.config["general"]["codename"])
            template = template.replace("$basecodename", self.config["general"]["base_codename"])
            template = template.replace("$optionalcomponents", ' '.join(selected_components))
            template = template.replace("$mirror", self.selected_mirror)
            template = template.replace("$basemirror", self.selected_base_mirror)
            with open("/etc/apt/sources.list.d/official-source-repositories.list", "w", encoding="utf-8", errors="ignore") as text_file:
                text_file.write(template)

        # Update dbgsym repositories
        os.system("rm -f /etc/apt/sources.list.d/official-dbgsym-repositories.list")
        if (self.builder.get_object("debug_symbol_switch").get_active()):
            template = open('/usr/share/mintsources/%s/official-dbgsym-repositories.list' % self.lsb_codename, 'r', encoding="utf-8", errors="ignore").read()
            template = template.replace("$codename", self.config["general"]["codename"])
            template = template.replace("$basecodename", self.config["general"]["base_codename"])
            template = template.replace("$optionalcomponents", ' '.join(selected_components))
            template = template.replace("$mirror", self.selected_mirror)
            template = template.replace("$basemirror", self.selected_base_mirror)
            with open("/etc/apt/sources.list.d/official-dbgsym-repositories.list", "w", encoding="utf-8", errors="ignore") as text_file:
                text_file.write(template)

        self.enable_reload_button()

    def generate_missing_sources(self):
        os.system("rm -f /etc/apt/sources.list.d/official-package-repositories.list")
        os.system("rm -f /etc/apt/sources.list.d/official-source-repositories.list")
        os.system("rm -f /etc/apt/sources.list.d/official-dbgsym-repositories.list")

        template = open('/usr/share/mintsources/%s/official-package-repositories.list' % self.lsb_codename, 'r', encoding="utf-8", errors="ignore").read()
        template = template.replace("$codename", self.config["general"]["codename"])
        template = template.replace("$basecodename", self.config["general"]["base_codename"])
        template = template.replace("$optionalcomponents", '')
        template = template.replace("$mirror", self.config["mirrors"]["default"])
        template = template.replace("$basemirror", self.config["mirrors"]["base_default"])

        with open("/etc/apt/sources.list.d/official-package-repositories.list", "w", encoding="utf-8", errors="ignore") as text_file:
            text_file.write(template)

    def detect_official_sources(self):
        self.selected_mirror = self.config["mirrors"]["default"]
        self.selected_base_mirror = self.config["mirrors"]["base_default"]

        # Detect source code and dbgsym repositories
        self.builder.get_object("source_code_switch").set_active(os.path.exists("/etc/apt/sources.list.d/official-source-repositories.list"))
        self.builder.get_object("debug_symbol_switch").set_active(os.path.exists("/etc/apt/sources.list.d/official-dbgsym-repositories.list"))

        listfile = open('/etc/apt/sources.list.d/official-package-repositories.list', 'r', encoding="utf-8", errors="ignore")
        for line in listfile.readlines():
            if (self.config["detection"]["main_identifier"] in line):
                for component in self.optional_components:
                    if component.name in line:
                        component.widget.set_active(True)
                elements = line.split(" ")
                if elements[0] == "deb":
                    mirror = elements[1]
                    if "$" not in mirror:
                        self.selected_mirror = mirror.rstrip('/')
            if (self.config["detection"]["base_identifier"] in line):
                elements = line.split(" ")
                if elements[0] == "deb":
                    mirror = elements[1]
                    if "$" not in mirror:
                        self.selected_base_mirror = mirror.rstrip('/')

        self.builder.get_object("label_mirror_name").set_text(self.selected_mirror)
        self.builder.get_object("label_base_mirror_name").set_text(self.selected_base_mirror)

        self.update_flags()

    def update_flags(self):
        mint_flag_path = FLAG_PATH % '_generic'
        base_flag_path = FLAG_PATH % '_generic'

        selected_mirror = self.selected_mirror
        if selected_mirror[-1] == "/":
            selected_mirror = selected_mirror[:-1]

        selected_base_mirror = self.selected_base_mirror
        if selected_base_mirror[-1] == "/":
            selected_base_mirror = selected_base_mirror[:-1]

        for mirror in self.mirrors:
            if mirror.url[-1] == "/":
                url = mirror.url[:-1]
            else:
                url = mirror.url
            if url in selected_mirror:
                if mirror.country_code == "WD":
                    flag = FLAG_PATH % '_united_nations'
                else:
                    flag = FLAG_PATH % mirror.country_code.lower()
                if os.path.exists(flag):
                    mint_flag_path = flag
                break

        for mirror in self.base_mirrors:
            if mirror.url[-1] == "/":
                url = mirror.url[:-1]
            else:
                url = mirror.url
            if url in selected_base_mirror:
                if mirror.country_code == "WD":
                    flag = FLAG_PATH % '_united_nations'
                else:
                    flag = FLAG_PATH % mirror.country_code.lower()
                if os.path.exists(flag):
                    base_flag_path = flag
                break

        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(mint_flag_path, -1, FLAG_SIZE * self.scale)
        surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, self.scale)
        self.builder.get_object("image_mirror").set_from_surface(surface)

        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(base_flag_path, -1, FLAG_SIZE * self.scale)
        surface = Gdk.cairo_surface_create_from_pixbuf(pixbuf, self.scale)
        self.builder.get_object("image_base_mirror").set_from_surface(surface)

    def get_clipboard_text(self, source_type):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text()
        if text is not None and text.strip().startswith(source_type):
            return text
        else:
            return None

if __name__ == "__main__":
    lsb_codename = subprocess.getoutput("lsb_release -sc")
    config_dir = "/usr/share/mintsources/%s" % lsb_codename
    if not os.path.exists(config_dir):
        print ("LSB codename: '%s'." % lsb_codename)
        if os.path.exists("/etc/linuxmint/info"):
            print ("Version of base-files: '%s'." % subprocess.getoutput("dpkg-query -f '${Version}' -W base-files"))
            print ("Your LSB codename isn't a valid Linux Mint codename.")
        else:
            print ("This codename isn't currently supported.")
        print ("Please check your LSB information with \"lsb_release -a\".")
        sys.exit(1)

    args = sys.argv[1:]
    if len(args) > 1 and args[0] == "add-apt-repository":
        ppa_line = next((arg for arg in args[1:] if not arg.startswith("-")), None)
        if not ppa_line:
            sys.exit(1)
        config_parser = configparser.RawConfigParser()
        config_parser.read("/usr/share/mintsources/%s/mintsources.conf" % lsb_codename)
        codename = config_parser.get("general", "base_codename")
        use_ppas = config_parser.get("general", "use_ppas")
        if "-r" in args:
            remove_repository_via_cli(ppa_line, codename, "-y" in args)
        else:
            add_repository_via_cli(ppa_line, codename, "-y" in args, use_ppas)
    else:
        Application().run()

    exit(1 if sources_changed else 0)
