#!/usr/bin/python2

import os
import sys
import gtk
import gobject
import urlparse
import ConfigParser
import aptsources.distro
import aptsources.distinfo
from aptsources.sourceslist import SourcesList
import gettext
import thread
import pycurl
import cStringIO
from CountryInformation import CountryInformation
import commands
import re
import json
import datetime

import urllib
import pycurl
from optparse import OptionParser
from sets import Set

BUTTON_LABEL_MAX_LENGTH = 30

def add_repository_via_cli(line, codename, forceYes, use_ppas):

    if line.startswith("ppa:"):
        if use_ppas != "true":
            print(_("Adding PPAs is not supported"))
            sys.exit(1)
        user, sep, ppa_name = line.split(":")[1].partition("/")
        ppa_name = ppa_name or "ppa"
        try:
            ppa_info = get_ppa_info_from_lp(user, ppa_name, codename)
        except Exception, detail:
            print _("Cannot add PPA: '%s'.") % detail
            sys.exit(1)

        if "private" in ppa_info and ppa_info["private"]:
            print(_("Adding private PPAs is not supported currently"))
            sys.exit(1)

        print(_("You are about to add the following PPA to your system:"))
        if ppa_info["description"] is not None:
            print(" %s" % (ppa_info["description"].encode("utf-8") or ""))
        print(_(" More info: %s") % str(ppa_info["web_link"]))

        if sys.stdin.isatty():
            if not(forceYes):
                print(_("Press [ENTER] to continue or ctrl-c to cancel adding it"))
                sys.stdin.readline()
        else:
            if not(forceYes):
                print(_("Unable to prompt for response.  Please run with -y"))
                sys.exit(1)

        (deb_line, file) = expand_ppa_line(line.strip(), codename)
        deb_line = expand_http_line(deb_line, codename)
        debsrc_line = 'deb-src' + deb_line[3:]

        # Add the key
        short_key = ppa_info["signing_key_fingerprint"][-8:]
        os.system("apt-key adv --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys %s" % short_key)

        # Add the PPA in sources.list.d
        with open(file, "w") as text_file:
            text_file.write("%s\n" % deb_line)
            text_file.write("%s\n" % debsrc_line)
    elif line.startswith("deb "):
        with open("/etc/apt/sources.list.d/additional-repositories.list", "a") as text_file:
            text_file.write("%s\n" % expand_http_line(line, codename))

def get_ppa_info_from_lp(owner_name, ppa_name, base_codename):
    DEFAULT_KEYSERVER = "hkp://keyserver.ubuntu.com:80/"
    # maintained until 2015
    LAUNCHPAD_PPA_API = 'https://launchpad.net/api/1.0/~%s/+archive/%s'
    # Specify to use the system default SSL store; change to a different path
    # to test with custom certificates.
    LAUNCHPAD_PPA_CERT = "/etc/ssl/certs/ca-certificates.crt"

    lp_url = LAUNCHPAD_PPA_API % (owner_name, ppa_name)
    try:
        callback = CurlCallback()
        curl = pycurl.Curl()
        curl.setopt(pycurl.SSL_VERIFYPEER, 1)
        curl.setopt(pycurl.SSL_VERIFYHOST, 2)
        curl.setopt(pycurl.WRITEFUNCTION, callback.body_callback)
        if LAUNCHPAD_PPA_CERT:
            curl.setopt(pycurl.CAINFO, LAUNCHPAD_PPA_CERT)
        curl.setopt(pycurl.URL, str(lp_url))
        curl.setopt(pycurl.HTTPHEADER, ["Accept: application/json"])
        curl.perform()
        curl.close()
        json_data = callback.contents
    except pycurl.error as e:
        raise PPAException("Error reading %s: %s" % (lp_url, e), e)

    # Make sure the PPA supports our base release
    repo_url = "http://ppa.launchpad.net/%s/%s/ubuntu/dists/%s" % (owner_name, ppa_name, base_codename)
    try:
        if (urllib.urlopen(repo_url).getcode() == 404):
            raise PPAException(_("This PPA does not support %s") % base_codename)
    except Exception as e:
        print e
        raise PPAException(_("This PPA does not support %s") % base_codename)

    return json.loads(json_data)

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
    except IndexError as e:
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
      apt-add-repository 'deb http://packages.medibuntu.org/ '$(lsb_release -cs)' free non-free'
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
        self.contents = self.contents + buf


class PPAException(Exception):

    def __init__(self, value, original_error=None):
        self.value = value
        self.original_error = original_error

    def __str__(self):
        return repr(self.value)

gettext.install("mintsources", "/usr/share/linuxmint/locale")

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
        os.system("apt-key del %s" % self.pub)

    def get_name(self):
        return "<b>%s</b>\n<small><i>%s</i></small>" % (gobject.markup_escape_text(self.uid), gobject.markup_escape_text(self.pub))

class Mirror():
    def __init__(self, country_code, url, name):
        self.country_code = country_code
        self.url = url
        self.name = name

class Repository():
    def __init__(self, application, line, file, selected):
        self.application = application
        self.line = line
        self.file = file
        self.selected = selected

    def switch(self):
        self.selected = (not self.selected)

        readfile = open(self.file, "r")
        content = readfile.read()
        readfile.close()

        if self.selected:
            content = content.replace("#%s" % self.line, self.line)
            content = content.replace("# %s" % self.line, self.line)
        else:
            content = content.replace(self.line, "# %s" % self.line)

        with open(self.file, "w") as writefile:
            writefile.write(content)

        self.application.enable_reload_button()

    def edit(self, newline):
        readfile = open(self.file, "r")
        content = readfile.read()
        readfile.close()
        content = content.replace(self.line, newline)
        with open(self.file, "w") as writefile:
            writefile.write(content)
        self.line = newline
        self.application.enable_reload_button()

    def delete(self):
        readfile = open(self.file, "r")
        content = readfile.read()
        readfile.close()
        content = content.replace(self.line, "")
        with open(self.file, "w") as writefile:
            writefile.write(content)

        # If the file no longer contains any "deb" instances, delete it as well
        if "deb" not in content:
            os.unlink(self.file)

        self.application.enable_reload_button()

    def get_ppa_name(self):
        elements = self.line.split(" ")
        name = elements[1].replace("deb-src ", "")
        name = name.replace("deb ", "")
        name = name.replace("http://ppa.launchpad.net/", "")
        name = name.replace("/ubuntu", "")
        name = name.replace("/ppa", "")
        if self.line.startswith("deb-src"):
            name = "%s (%s)" % (name, _("Sources"))
        return "<b>%s</b>\n<small><i>%s</i></small>\n<small><i>%s</i></small>" % (name, self.line, self.file)

    def get_repository_name(self):
        line = self.line.strip()
        name = line
        if line.startswith("deb cdrom:"):
            name = _("CD-ROM (Installation Disc)")
        else:
            try:
                elements = self.line.split(" ")
                for element in elements:
                   for protocol in ['http://', 'ftp://', 'https://']:
                        if element.startswith(protocol):
                            name = element.replace(protocol, "").split("/")[0]
                            subparts = name.split(".")
                            if len(subparts) > 2:
                                if subparts[-2] != "co":
                                    name = subparts[-2].capitalize()
                                else:
                                    name = subparts[-3].capitalize()
                            break
                name = name.replace("Linuxmint", "Linux Mint")
                name = name.replace("01", "Intel")
                name = name.replace("Steampowered", "Steam")
            except:
                pass
            if self.line.startswith("deb-src"):
                name = "%s (%s)" % (name, _("Sources"))
        return "<b>%s</b>\n<small><i>%s</i></small>\n<small><i>%s</i></small>" % (name, self.line, self.file)

class ComponentToggleCheckBox(gtk.CheckButton):
    def __init__(self, application, component, window):
        self.application = application
        self.component = component
        self.window_object = window
        gtk.CheckButton.__init__(self, self.component.description)
        self.set_active(component.selected)
        self.connect("toggled", self._on_toggled)

    def _on_toggled(self, widget):
        # As long as the interface isn't fully loaded, don't do anything
        if not self.application._interface_loaded:
            return

        if widget.get_active() and os.path.exists("/etc/linuxmint/info"):
            if self.component.name == "romeo":
                if self.application.show_confirmation_dialog(self.application._main_window, _("Linux Mint uses Romeo to publish packages which are not tested. Once these packages are tested, they are then moved to the official repositories. Unless you are participating in beta-testing, you should not enable this repository. Are you sure you want to enable Romeo?"), yes_no=True):
                    self.component.selected = widget.get_active()
                    self.application.apply_official_sources()
                else:
                    widget.set_active(not widget.get_active())
            elif self.component.name == "backport":
                if self.application.show_confirmation_dialog(self.application._main_window, _("Backports are packages coming from newer Linux Mint releases. When backports are made available, the development team publishes an official announcement on http://blog.linuxmint.com. This announcement might include important information about the new features, design decisions or even known issues which relate to you. It is therefore strongly recommended to not enable backports until you have read this information. Are you sure you want to enable backports?"), yes_no=True):
                    self.component.selected = widget.get_active()
                    self.application.apply_official_sources()
                else:
                    widget.set_active(not widget.get_active())
            else:
                self.component.selected = widget.get_active()
                self.application.apply_official_sources()
        else:
            self.component.selected = widget.get_active()
            self.application.apply_official_sources()

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

        self._mirrors_model = gtk.ListStore(object, str, gtk.gdk.Pixbuf, float, str, str, str)
        # mirror, name, flag, speed, speed label, country code (used to sort by flag), mirror name
        self._treeview = ui_builder.get_object("mirrors_treeview")
        self._treeview.set_model(self._mirrors_model)
        self._treeview.set_headers_clickable(True)

        self._mirrors_model.set_sort_column_id(MirrorSelectionDialog.MIRROR_SPEED_COLUMN, gtk.SORT_DESCENDING)

        r = gtk.CellRendererPixbuf()
        col = gtk.TreeViewColumn(_("Country"), r, pixbuf = MirrorSelectionDialog.MIRROR_COUNTRY_FLAG_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_TOOLTIP_COLUMN)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("URL"), r, text = MirrorSelectionDialog.MIRROR_NAME_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_NAME_COLUMN)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Speed"), r, text = MirrorSelectionDialog.MIRROR_SPEED_LABEL_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_SPEED_COLUMN)
        col.set_min_width(int(1.1 * SPEED_PIX_WIDTH))

        self._treeview.set_tooltip_column(MirrorSelectionDialog.MIRROR_TOOLTIP_COLUMN)

        self.country_info = CountryInformation()

        with open('/usr/lib/linuxmint/mintSources/countries.json') as data_file:
            self.countries = json.load(data_file)

    def get_country(self, country_code):
        for country in self.countries:
            if country["cca2"] == country_code:
                return country
        return None

    def _update_list(self):
        self._mirrors_model.clear()
        for mirror in self.visible_mirrors:
            flag = "/usr/lib/linuxmint/mintSources/flags/generic.png"
            if os.path.exists("/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower()):
                flag = "/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower()
            country_name = self.country_info.get_country_name(mirror.country_code)
            tooltip = country_name
            if mirror.name != mirror.url:
                tooltip = "%s: %s" % (country_name, mirror.name)
            self._mirrors_model.append((
                mirror,
                mirror.url,
                gtk.gdk.pixbuf_new_from_file(flag),
                0,
                None,
                tooltip,
                mirror.name
            ))

        thread.start_new_thread(self._all_speed_tests, ())

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

    def check_mirror_up_to_date(self, url):
        if (self.default_mirror_age is None or self.default_mirror_age < 2):
            # If the default server was updated recently, the age is irrelevant (it would measure the time between now and the last update)
            #print "OK - default mirror age is not conclusive %s" % url
            return True
        mirror_timestamp = self.get_url_last_modified(url)
        if mirror_timestamp is None:
            print "Error: Can't find the age of %s !!" % url
            return False
        mirror_date = datetime.datetime.fromtimestamp(mirror_timestamp)
        mirror_age = (self.default_mirror_date - mirror_date).days
        if (mirror_age > 2):
            print "Error: %s is out of date by %d days!" % (url, mirror_age)
            return False
        else:
            # Age is fine :)
            return True

    def _all_speed_tests(self):
        model_iters = [] # Don't iterate through iters directly.. we're modifying their orders..
        iter = self._mirrors_model.get_iter_first()
        while iter is not None:
            model_iters.append(iter)
            iter = self._mirrors_model.iter_next(iter)

        for iter in model_iters:
            mirror = self._mirrors_model.get_value(iter, MirrorSelectionDialog.MIRROR_COLUMN)
            if mirror in self.visible_mirrors:
                self._speed_test (self._mirrors_model, iter)

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

    def _speed_test(self, model, iter):
        if iter is not None:
            url = model.get_value(iter, MirrorSelectionDialog.MIRROR_URL_COLUMN)
            download_speed = 0
            try:
                if self.is_base:
                    test_url = "%s/dists/%s/main/binary-amd64/Packages.gz" % (url, self.codename)
                else:
                    test_url = "%s/dists/%s/main/Contents-amd64.gz" % (url, self.codename)
                if (self.is_base or self.check_mirror_up_to_date("%s/db/version" % url)):
                    c = pycurl.Curl()
                    buff = cStringIO.StringIO()
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
                    download_speed = 0
            except Exception, error:
                print "Error '%s' on url %s" % (error, url)
                download_speed = 0

            if (iter is not None): # recheck as it can get null
                if download_speed == 0:
                    # don't remove from model as this is not thread-safe
                    model.set_value(iter, MirrorSelectionDialog.MIRROR_SPEED_LABEL_COLUMN, "offline")
                else:
                    model.set_value(iter, MirrorSelectionDialog.MIRROR_SPEED_COLUMN, download_speed)
                    model.set_value(iter, MirrorSelectionDialog.MIRROR_SPEED_LABEL_COLUMN, self._get_speed_label(download_speed))

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
            lookup = str(urllib.urlopen('http://geoip.ubuntu.com/lookup').read())
            cur_country_code = re.search('<CountryCode>(.*)</CountryCode>', lookup).group(1)
            if cur_country_code == 'None': cur_country_code = None
        except Exception, detail:
            cur_country_code = None  # no internet connection

        self.local_country_code = cur_country_code or os.environ.get('LANG', 'US').split('.')[0].split('_')[-1]  # fallback to LANG location or 'US'

        self.bordering_countries = []
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

        self.local_mirrors = []
        self.bordering_mirrors = []
        self.subregional_mirrors = []
        self.regional_mirrors = []
        self.other_mirrors = []

        for mirror in mirrors:
            if mirror.country_code == self.local_country_code:
                self.local_mirrors.append(mirror)
            elif mirror.country_code in self.bordering_countries:
                self.bordering_mirrors.append(mirror)
            elif mirror.country_code in self.subregion:
                self.subregional_mirrors.append(mirror)
            elif mirror.country_code in self.region:
                self.regional_mirrors.append(mirror)
            elif mirror.url == self.default_mirror:
                self.other_mirrors.append(mirror)

        self.bordering_mirrors = sorted(self.bordering_mirrors, key=lambda x: x.country_code)
        self.subregional_mirrors = sorted(self.subregional_mirrors, key=lambda x: x.country_code)
        self.regional_mirrors = sorted(self.regional_mirrors, key=lambda x: x.country_code)

        self.visible_mirrors = self.local_mirrors + self.bordering_mirrors + self.subregional_mirrors + self.regional_mirrors + self.other_mirrors

        if len(self.visible_mirrors) < 2:
            # We failed to identify the continent/country, let's show all mirrors
            self.visible_mirrors = mirrors

        # Try to find the age of the Mint archive
        self.default_mirror_age = None
        self.default_mirror_date = None
        mirror_timestamp = self.get_url_last_modified("%s/db/version" % self.default_mirror)
        if mirror_timestamp is not None:
            self.default_mirror_date = datetime.datetime.fromtimestamp(mirror_timestamp)
            now = datetime.datetime.now()
            self.default_mirror_age = (now - self.default_mirror_date).days
            #print "Default mirror (%s/db/version) age: %d days" % (self.default_mirror, self.default_mirror_age)

        self._update_list()
        self._dialog.show_all()
        retval = self._dialog.run()
        if retval == gtk.RESPONSE_APPLY:
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

        # Prevent settings from being saved until the interface is fully loaded
        self._interface_loaded = False

        self.lsb_codename = commands.getoutput("lsb_release -sc")

        glade_file = "/usr/lib/linuxmint/mintSources/mintSources.glade"

        self.builder = gtk.Builder()
        self.builder.add_from_file(glade_file)
        self._main_window = self.builder.get_object("main_window")

        self._main_window.set_title(_("Software Sources"))

        self._main_window.set_icon_from_file("/usr/share/icons/hicolor/scalable/apps/software-sources.svg")

        self._notebook = self.builder.get_object("notebook")
        self._official_repositories_box = self.builder.get_object("official_repositories_box")

        config_parser = ConfigParser.RawConfigParser()
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
            self.builder.get_object("vbuttonbox1").remove(self.builder.get_object("toggle_ppas"))

        self.builder.get_object("reload_button_label").set_markup("%s" % _("No action required"))

        self.builder.get_object("label_title_official").set_markup("%s" % _("Official repositories"))
        self.builder.get_object("label_title_ppa").set_markup("%s" % _("PPAs"))
        self.builder.get_object("label_title_3rd").set_markup("%s" % _("Additional repositories"))
        self.builder.get_object("label_title_keys").set_markup("%s" % _("Authentication keys"))
        self.builder.get_object("label_title_maintenance").set_markup("%s" % _("Maintenance"))

        self.builder.get_object("label_mirrors").set_markup("<b>%s</b>" % _("Mirrors"))
        self.builder.get_object("label_mirror_description").set_markup("%s (%s)" % (_("Main"), self.config["general"]["codename"]) )
        self.builder.get_object("label_base_mirror_description").set_markup("%s (%s)" % (_("Base"), self.config["general"]["base_codename"]) )
        self.builder.get_object("button_mirror").set_tooltip_text(_("Select a faster server..."))
        self.builder.get_object("button_base_mirror").set_tooltip_text(_("Select a faster server..."))

        self.builder.get_object("label_optional_components").set_markup("<b>%s</b>" % _("Optional components"))
        self.builder.get_object("label_source_code").set_markup("<b>%s</b>" % _("Source code"))

        self.set_button_text(self.builder.get_object("label_ppa_add"), _("Add a new PPA..."))
        self.set_button_text(self.builder.get_object("label_ppa_edit"), _("Edit URL..."))
        self.set_button_text(self.builder.get_object("label_ppa_remove"), _("Remove"))
        self.set_button_text(self.builder.get_object("label_ppa_examine"), _("Open PPA"))
        self.builder.get_object("label_ppa_examine").set_tooltip_text(_("Look inside the PPA and install packages it provides"))

        self.set_button_text(self.builder.get_object("label_repository_add"), _("Add a new repository..."))
        self.set_button_text(self.builder.get_object("label_repository_edit"), _("Edit URL..."))
        self.set_button_text(self.builder.get_object("label_repository_remove"), _("Remove"))

        self.set_button_text(self.builder.get_object("label_keys_add"), _("Import key file..."))
        self.set_button_text(self.builder.get_object("label_keys_fetch"), _("Download a key..."))
        self.set_button_text(self.builder.get_object("label_keys_remove"), _("Remove"))

        self.builder.get_object("button_mergelist_label").set_markup("%s" % _("Fix MergeList problems"))
        self.builder.get_object("button_mergelist").set_tooltip_text("%s" % _("If you experience MergeList problems, click this button to solve the problem."))
        self.builder.get_object("button_purge_label").set_markup("%s" % _("Purge residual configuration"))
        self.builder.get_object("button_purge").set_tooltip_text("%s" % _("Packages sometimes leave configuration files on the system even after they are removed."))
        self.builder.get_object("button_remove_foreign_label").set_markup("%s" % _("Remove foreign packages"))
        self.builder.get_object("button_remove_foreign").set_tooltip_text("%s" % _("Packages which do not come from known repositories are listed here and can be removed."))
        self.builder.get_object("button_downgrade_foreign_label").set_markup("%s" % _("Downgrade foreign packages"))
        self.builder.get_object("button_downgrade_foreign").set_tooltip_text("%s" % _("Packages which version does not come from known repositories are listed here and can be downgraded."))

        self.builder.get_object("label_description").set_markup("<b>%s</b>" % self.config["general"]["description"])
        self.builder.get_object("image_icon").set_from_file("/usr/share/mintsources/%s/icon.png" % self.lsb_codename)

        self.builder.get_object("source_code_cb").set_label(_("Enable source code repositories"))

        self.builder.get_object("source_code_cb").connect("toggled", self.apply_official_sources)

        self.selected_components = []
        if (len(self.optional_components) > 0):
            if os.path.exists("/etc/linuxmint/info"):
                # This is Mint, we want to warn people about Romeo/Backport
                warning_label = gtk.Label()
                warning_label.set_alignment(0, 0.5)
                warning_label.set_markup("<span font_style='oblique' font_stretch='ultracondensed' foreground='#3c3c3c'>%s</span>" % _("Warning: Backports and unstable packages can introduce regressions and negatively impact your system. Please do not enable these options in Linux Mint unless it was suggested by the development team."))
                warning_label.set_line_wrap(True)
                warning_label.connect("size-allocate", self.label_size_allocate)
                self.builder.get_object("vbox_optional_components").pack_start(warning_label, True, True, 6)
            components_table = gtk.Table()
            self.builder.get_object("vbox_optional_components").pack_start(components_table, True, True)
            self.builder.get_object("vbox_optional_components").show_all()
            nb_components = 0
            for i in range(len(self.optional_components)):
                component = self.optional_components[i]
                cb = ComponentToggleCheckBox(self, component, self._main_window)
                component.set_widget(cb)
                components_table.attach(cb, 0, 1, nb_components, nb_components + 1, xoptions = gtk.FILL | gtk.EXPAND, yoptions = 0)
                nb_components += 1

        self.mirrors = self.read_mirror_list(self.config["mirrors"]["mirrors"])
        self.base_mirrors = self.read_mirror_list(self.config["mirrors"]["base_mirrors"])

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

        for source_file in source_files:
            file = open(source_file, "r")
            for line in file.readlines():
                line = line.strip()
                if line != "":
                    selected = True
                    if line.startswith("#"):
                        line = line.replace('#', '').strip()
                        selected = False
                    if line.startswith("deb"):
                        repository = Repository(self, line, source_file, selected)
                        if "ppa.launchpad" in line and self.config["general"]["use_ppas"] != "false":
                            self.ppas.append(repository)
                        else:
                            self.repositories.append(repository)
            file.close()

        # Add PPAs
        self._ppa_model = gtk.ListStore(object, bool, str)
        self._ppa_treeview = self.builder.get_object("treeview_ppa")
        self._ppa_treeview.set_model(self._ppa_model)
        self._ppa_treeview.set_headers_clickable(True)
        self._ppa_treeview.connect("row-activated", self.on_ppa_treeview_doubleclick)
        selection = self._ppa_treeview.get_selection()
        selection.connect("changed", self.ppa_selected)

        self._ppa_model.set_sort_column_id(2, gtk.SORT_ASCENDING)

        r = gtk.CellRendererToggle()
        r.connect("toggled", self.ppa_toggled)
        col = gtk.TreeViewColumn(_("Enabled"), r)
        col.set_cell_data_func(r, self.datafunction_checkbox)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(1)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("PPA"), r, markup = 2)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(2)

        if (len(self.ppas) > 0):
            for repository in self.ppas:
                tree_iter = self._ppa_model.append((repository, repository.selected, repository.get_ppa_name()))

        # Add repositories
        self._repository_model = gtk.ListStore(object, bool, str)
        self._repository_treeview = self.builder.get_object("treeview_repository")
        self._repository_treeview.set_model(self._repository_model)
        self._repository_treeview.set_headers_clickable(True)

        self._repository_model.set_sort_column_id(2, gtk.SORT_ASCENDING)

        r = gtk.CellRendererToggle()
        r.connect("toggled", self.repository_toggled)
        col = gtk.TreeViewColumn(_("Enabled"), r)
        col.set_cell_data_func(r, self.datafunction_checkbox)
        self._repository_treeview.append_column(col)
        col.set_sort_column_id(1)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Repository"), r, markup = 2)
        self._repository_treeview.append_column(col)
        col.set_sort_column_id(2)

        if (len(self.repositories) > 0):
            for repository in self.repositories:
                tree_iter = self._repository_model.append((repository, repository.selected, repository.get_repository_name()))

        self._keys_model = gtk.ListStore(object, str)
        self._keys_treeview = self.builder.get_object("treeview_keys")
        self._keys_treeview.set_model(self._keys_model)
        self._keys_treeview.set_headers_clickable(True)

        self._keys_model.set_sort_column_id(1, gtk.SORT_ASCENDING)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Key"), r, markup = 1)
        self._keys_treeview.append_column(col)
        col.set_sort_column_id(1)

        self.load_keys()

        if not os.path.exists("/etc/apt/sources.list.d/official-package-repositories.list"):
            print "Sources missing, generating default sources list!"
            self.generate_missing_sources()

        self.detect_official_sources()

        self.builder.get_object("revert_button").connect("clicked", self.revert_to_default_sources)
        self.builder.get_object("label_revert").set_markup(_("Restore the default settings"))
        self.builder.get_object("revert_button").set_tooltip_text(_("Restore the official repositories to their default settings"))

        self._tab_buttons = [
            self.builder.get_object("toggle_official_repos"),
            self.builder.get_object("toggle_ppas"),
            self.builder.get_object("toggle_additional_repos"),
            self.builder.get_object("toggle_authentication_keys"),
            self.builder.get_object("toggle_maintenance")
        ]

        self._main_window.connect("delete_event", lambda w,e: gtk.main_quit())
        for i in range(len(self._tab_buttons)):
            self._tab_buttons[i].connect("clicked", self._on_tab_button_clicked, i)
            self._tab_buttons[i].set_active(False)


        self.mirror_selection_dialog = MirrorSelectionDialog(self, self.builder)

        self.builder.get_object("button_mirror").connect("clicked", self.select_new_mirror)
        self.builder.get_object("button_base_mirror").connect("clicked", self.select_new_base_mirror)
        self.builder.get_object("reload_button").connect("clicked", self.update_apt_cache)

        self.builder.get_object("button_ppa_add").connect("clicked", self.add_ppa)
        self.builder.get_object("button_ppa_edit").connect("clicked", self.edit_ppa)
        self.builder.get_object("button_ppa_remove").connect("clicked", self.remove_ppa)
        self.builder.get_object("button_ppa_examine").connect("clicked", self.examine_ppa)
        self.builder.get_object("button_ppa_examine").set_sensitive(False)

        self.builder.get_object("button_repository_add").connect("clicked", self.add_repository)
        self.builder.get_object("button_repository_edit").connect("clicked", self.edit_repository)
        self.builder.get_object("button_repository_remove").connect("clicked", self.remove_repository)

        self.builder.get_object("button_keys_add").connect("clicked", self.add_key)
        self.builder.get_object("button_keys_fetch").connect("clicked", self.fetch_key)
        self.builder.get_object("button_keys_remove").connect("clicked", self.remove_key)

        self.builder.get_object("button_mergelist").connect("clicked", self.fix_mergelist)
        self.builder.get_object("button_purge").connect("clicked", self.fix_purge)
        self.builder.get_object("button_remove_foreign").connect("clicked", self.remove_foreign)
        self.builder.get_object("button_downgrade_foreign").connect("clicked", self.downgrade_foreign)

        # From now on, we handle modifications to the settings and save them when they happen
        self._interface_loaded = True

    def set_button_text(self, button, text):
        if len(text) > BUTTON_LABEL_MAX_LENGTH:
            button.set_tooltip_text(text)
            encoded = text.encode('utf-8')[:BUTTON_LABEL_MAX_LENGTH] + ".."
            text = encoded.decode('utf-8', 'ignore')
        button.set_text(text)

    def label_size_allocate(self, widget, rect):
        widget.set_size_request(rect.width, -1)

    def read_mirror_list(self, path):
        mirror_list = []
        country_code = None
        mirrorsfile = open(path, "r")
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
        return mirror_list

    def remove_foreign(self, widget):
        os.system("/usr/lib/linuxmint/mintSources/foreign_packages.py remove %s" % self._main_window.window.xid)

    def downgrade_foreign(self, widget):
        os.system("/usr/lib/linuxmint/mintSources/foreign_packages.py downgrade %s" % self._main_window.window.xid)

    def fix_purge(self, widget):
        os.system("aptitude purge ~c -y")
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/maintenance.png")
        self.show_confirmation_dialog(self._main_window, _("There is no more residual configuration on the system."), image, affirmation=True)

    def fix_mergelist(self, widget):
        os.system("rm /var/lib/apt/lists/* -vf")
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/maintenance.png")
        self.show_confirmation_dialog(self._main_window, _("The problem was fixed. Please reload the cache."), image, affirmation=True)
        self.enable_reload_button()

    def load_keys(self):
        self.keys = []
        key = None
        output = commands.getoutput("apt-key list")
        for line in output.split("\n"):
            line = line.strip()
            if line.startswith("pub"):
                pub = line[3:].strip()
                pub = pub[6:]
                pub = pub.split(" ")[0]
                key = Key(pub)
                if pub not in self.system_keys:
                    self.keys.append(key)
            elif line.startswith("uid") and key is not None:
                key.uid = line[3:].strip()
            elif line.startswith("sub") and key is not None:
                key.sub = line[3:].strip()

        self._keys_model.clear()
        for key in self.keys:
            tree_iter = self._keys_model.append((key, key.get_name()))

    def add_key(self, widget):
        dialog = gtk.FileChooserDialog(_("Open.."),
                               None,
                               gtk.FILE_CHOOSER_ACTION_OPEN,
                               (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        dialog.set_default_response(gtk.RESPONSE_OK)
        response = dialog.run()
        if response == gtk.RESPONSE_OK:
            os.system("apt-key add %s" % dialog.get_filename())
            self.load_keys()
            self.enable_reload_button()
        dialog.destroy()

    def fetch_key(self, widget):
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/keyring.png")
        line = self.show_entry_dialog(self._main_window, _("Please enter the 8 characters of the public key you want to download from keyserver.ubuntu.com:"), "", image)
        if line is not None:
            res = os.system("apt-key adv --keyserver keyserver.ubuntu.com --recv-keys %s" % line)
            self.load_keys()
            self.enable_reload_button()

    def remove_key(self, widget):
        selection = self._keys_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):
            key = model.get(iter, 0)[0]
            image = gtk.Image()
            image.set_from_file("/usr/lib/linuxmint/mintSources/keyring.png")
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this key?"), image, yes_no=True)):
                key.delete()
                self.load_keys()

    def add_ppa(self, widget):
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/ppa.png")
        start_line = ""
        clipboard_text = self.get_clipboard_text("ppa")
        if clipboard_text != None:
            start_line = clipboard_text
        else:
            start_line = "ppa:username/ppa"

        line = self.show_entry_dialog(self._main_window, _("Please enter the name of the PPA you want to add:"), start_line, image)
        if line is not None:
            user, sep, ppa_name = line.split(":")[1].partition("/")
            ppa_name = ppa_name or "ppa"
            try:
                ppa_info = get_ppa_info_from_lp(user, ppa_name, self.config["general"]["base_codename"])
            except Exception, detail:
                self.show_error_dialog(self._main_window, _("Cannot add PPA: '%s'.") % detail)
                return

            image = gtk.Image()
            image.set_from_file("/usr/lib/linuxmint/mintSources/ppa.png")
            info_text = "%s\n\n%s\n\n%s\n\n%s" % (line, self.format_string(ppa_info["displayname"]), self.format_string(ppa_info["description"]), str(ppa_info["web_link"]))
            if self.show_confirm_ppa_dialog(self._main_window, info_text):
                (deb_line, file) = expand_ppa_line(line.strip(), self.config["general"]["base_codename"])
                deb_line = expand_http_line(deb_line, self.config["general"]["base_codename"])
                debsrc_line = 'deb-src' + deb_line[3:]

                # Add the key
                short_key = ppa_info["signing_key_fingerprint"][-8:]
                os.system("apt-key adv --keyserver keyserver.ubuntu.com --recv-keys %s" % short_key)
                self.load_keys()

                # Add the PPA in sources.list.d
                with open(file, "w") as text_file:
                    text_file.write("%s\n" % deb_line)
                    text_file.write("%s\n" % debsrc_line)

                # Add the package line in the UI
                repository = Repository(self, deb_line, file, True)
                self.ppas.append(repository)
                tree_iter = self._ppa_model.append((repository, repository.selected, repository.get_ppa_name()))

                # Add the source line in the UI
                repository = Repository(self, debsrc_line, file, True)
                self.ppas.append(repository)
                tree_iter = self._ppa_model.append((repository, repository.selected, repository.get_ppa_name()))

                self.enable_reload_button()


    def format_string(self, text):
        if text is None:
            text = ""
        text = text.encode("utf-8")
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
        try:
            self.builder.get_object("button_ppa_examine").set_sensitive(False)
            (model, iter) = selection.get_selected()
            if (iter != None):
                repository = model.get_value(iter, 0)
                ppa_name = model.get_value(iter, 2)
                if repository.line.startswith("deb http://ppa.launchpad.net"):
                    self.builder.get_object("button_ppa_examine").set_sensitive(True)
        except Exception, detail:
            print detail

    def on_ppa_treeview_doubleclick(self, treeview, path, column):
        self.examine_ppa(None)

    def examine_ppa(self, widget):
        try:
            selection = self._ppa_treeview.get_selection()
            (model, iter) = selection.get_selected()
            if (iter != None):
                repository = model.get_value(iter, 0)
                ppa_name = model.get_value(iter, 2)
                if repository.line.startswith("deb http://ppa.launchpad.net"):
                    line = repository.line.split()[1].replace("http://ppa.launchpad.net/", "")
                    if line.endswith("/ubuntu"):
                        line = line[:-7]
                        ppa_owner, ppa_name = line.split("/")
                        architecture = commands.getoutput("dpkg --print-architecture")
                        codename = commands.getoutput("lsb_release -u -c -s")
                        ppa_file = "/var/lib/apt/lists/ppa.launchpad.net_%s_%s_ubuntu_dists_%s_main_binary-%s_Packages" % (ppa_owner, ppa_name, codename, architecture)
                        if os.path.exists(ppa_file):
                            os.system("/usr/lib/linuxmint/mintSources/ppa_browser.py %s %s %s" % (ppa_owner, ppa_name, self._main_window.window.xid))
                        else:
                            print "%s not found!" % ppa_file
                            self.show_error_dialog(self._main_window, _("The content of this PPA is not available. Please refresh the cache and try again."))
        except Exception, detail:
            print detail

    def add_repository(self, widget):
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/3rd.png")
        start_line = ""
        clipboard_text = self.get_clipboard_text("deb")
        if clipboard_text != None:
            start_line = clipboard_text
        else:
            start_line = "deb http://packages.domain.com/ %s main" % self.config["general"]["base_codename"]

        line = self.show_entry_dialog(self._main_window, _("Please enter the name of the repository you want to add:"), start_line, image)
        if line is not None and line.strip().startswith("deb"):
            # Add the repository in sources.list.d
            with open("/etc/apt/sources.list.d/additional-repositories.list", "a") as text_file:
                text_file.write("%s\n" % line)

            # Add the line in the UI
            repository = Repository(self, line, "/etc/apt/sources.list.d/additional-repositories.list", True)
            self.repositories.append(repository)
            tree_iter = self._repository_model.append((repository, repository.selected, repository.get_repository_name()))

            self.enable_reload_button()


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
        buttons = gtk.BUTTONS_OK_CANCEL
        default_button = gtk.RESPONSE_OK
        confirmation_button = gtk.RESPONSE_OK
        if yes_no:
            buttons = gtk.BUTTONS_YES_NO
            default_button = gtk.RESPONSE_NO
            confirmation_button = gtk.RESPONSE_YES

        if affirmation is None:
            d = gtk.MessageDialog(parent,
                              gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                              gtk.MESSAGE_WARNING,
                              buttons,
                              message)
        else:
            d = gtk.MessageDialog(parent,
                              gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                              gtk.MESSAGE_INFO,
                              gtk.BUTTONS_OK,
                              message)
        d.set_markup(message)
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
        b = gtk.TextBuffer()
        b.set_text(message)
        t =  gtk.TextView(b)
        t.set_wrap_mode(gtk.WRAP_WORD)
        s = gtk.ScrolledWindow()
        s.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        s.set_shadow_type(gtk.SHADOW_OUT)
        default_button = gtk.RESPONSE_ACCEPT
        confirmation_button = gtk.RESPONSE_ACCEPT
        d = gtk.Dialog(None, parent,
                       gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                       (gtk.STOCK_CANCEL, gtk.RESPONSE_REJECT,
                      gtk.STOCK_OK, gtk.RESPONSE_ACCEPT))
        d.set_size_request(550, 400)
        d.vbox.pack_start(s, True, True, 0)
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
        d = gtk.MessageDialog(parent,
                              gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                              gtk.MESSAGE_ERROR,
                              gtk.BUTTONS_OK,
                              message)

        d.set_markup(message)
        if image is not None:
            image.show()
            d.set_image(image)

        d.set_default_response(gtk.RESPONSE_OK)
        r = d.run()
        d.destroy()
        if r == gtk.RESPONSE_OK:
            return True
        else:
            return False

    def show_entry_dialog(self, parent, message, default='', image=None):
        d = gtk.MessageDialog(parent,
                              gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                              gtk.MESSAGE_QUESTION,
                              gtk.BUTTONS_OK_CANCEL,
                              message)

        d.set_markup(message)
        if image is not None:
            image.show()
            d.set_image(image)

        entry = gtk.Entry()
        entry.set_text(default)
        entry.show()
        d.vbox.pack_end(entry)
        entry.connect('activate', lambda _: d.response(gtk.RESPONSE_OK))
        d.set_default_response(gtk.RESPONSE_OK)

        r = d.run()
        text = entry.get_text().decode('utf8')
        d.destroy()
        if r == gtk.RESPONSE_OK:
            return text
        else:
            return None

    def datafunction_checkbox(self, column, cell, model, iter):
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

    def repository_toggled(self, renderer, path):
        iter = self._repository_model.get_iter(path)
        if (iter != None):
            repository = self._repository_model.get_value(iter, 0)
            repository.switch()

    def select_new_mirror(self, widget):
        url = self.mirror_selection_dialog.run(self.mirrors, self.config, False)
        if url is not None:
            self.selected_mirror = url
            self.builder.get_object("label_mirror_name").set_text(self.selected_mirror)
        self.apply_official_sources()

    def select_new_base_mirror(self, widget):
        url = self.mirror_selection_dialog.run(self.base_mirrors, self.config, True)
        if url is not None:
            self.selected_base_mirror = url
            self.builder.get_object("label_base_mirror_name").set_text(self.selected_base_mirror)
        self.apply_official_sources()

    def _on_tab_button_clicked(self, button, page_index):
        if page_index == self._notebook.get_current_page() and button.get_active() == True:
            return
        if page_index != self._notebook.get_current_page() and button.get_active() == False:
            return
        self._notebook.set_current_page(page_index)
        for i in self._tab_buttons:
            i.set_active(False)
        button.set_active(True)

    def run(self):
        gobject.threads_init()
        self._main_window.show_all()
        gtk.main()

    def revert_to_default_sources(self, widget):
        self.selected_mirror = self.config["mirrors"]["default"]
        self.builder.get_object("label_mirror_name").set_text(self.selected_mirror)
        self.selected_base_mirror = self.config["mirrors"]["base_default"]
        self.builder.get_object("label_base_mirror_name").set_text(self.selected_base_mirror)
        self.builder.get_object("source_code_cb").set_active(False)

        for component in self.optional_components:
            component.selected = False
            component.widget.set_active(False)

        self.apply_official_sources()

    def enable_reload_button(self):
        self.builder.get_object("reload_button").set_sensitive(True)
        self.builder.get_object("reload_button_label").set_markup("<b>%s</b>" % _("Update the cache"))
        self.builder.get_object("reload_button").set_tooltip_text(_("Click here to update your APT cache with your new sources"))
        self.builder.get_object("reload_button_image").set_from_stock(gtk.STOCK_REFRESH, gtk.ICON_SIZE_BUTTON)

    def disable_reload_button(self):
        self.builder.get_object("reload_button").set_sensitive(False)
        self.builder.get_object("reload_button_label").set_markup("%s" % _("No action required"))
        self.builder.get_object("reload_button").set_tooltip_text(_("Your APT cache is up to date"))
        self.builder.get_object("reload_button_image").set_from_stock(gtk.STOCK_OK, gtk.ICON_SIZE_BUTTON)

    def update_apt_cache(self, widget=None):
        self.disable_reload_button()
        from subprocess import Popen, PIPE
        cmd = ["sudo", "/usr/sbin/synaptic", "--hide-main-window", "--update-at-startup", "--non-interactive"]
        comnd = Popen(' '.join(cmd), shell=True)
        #returnCode = comnd.wait()

    def apply_official_sources(self, widget=None):
        # As long as the interface isn't fully loaded, don't save anything
        if not self._interface_loaded:
            return

        self.update_flags()

        # Check which components are selected
        selected_components = []
        for component in self.optional_components:
            if component.selected:
                selected_components.append(component.name)

        # Update official packages repositories
        os.system("rm -f /etc/apt/sources.list.d/official-package-repositories.list")
        template = open('/usr/share/mintsources/%s/official-package-repositories.list' % self.lsb_codename, 'r').read()
        template = template.replace("$codename", self.config["general"]["codename"])
        template = template.replace("$basecodename", self.config["general"]["base_codename"])
        template = template.replace("$optionalcomponents", ' '.join(selected_components))
        template = template.replace("$mirror", self.selected_mirror)
        template = template.replace("$basemirror", self.selected_base_mirror)

        with open("/etc/apt/sources.list.d/official-package-repositories.list", "w") as text_file:
            text_file.write(template)

        # Update official sources repositories
        os.system("rm -f /etc/apt/sources.list.d/official-source-repositories.list")
        if (self.builder.get_object("source_code_cb").get_active()):
            template = open('/usr/share/mintsources/%s/official-source-repositories.list' % self.lsb_codename, 'r').read()
            template = template.replace("$codename", self.config["general"]["codename"])
            template = template.replace("$basecodename", self.config["general"]["base_codename"])
            template = template.replace("$optionalcomponents", ' '.join(selected_components))
            template = template.replace("$mirror", self.selected_mirror)
            template = template.replace("$basemirror", self.selected_base_mirror)
            with open("/etc/apt/sources.list.d/official-source-repositories.list", "w") as text_file:
                text_file.write(template)

        self.enable_reload_button()

    def generate_missing_sources(self):
        os.system("rm -f /etc/apt/sources.list.d/official-package-repositories.list")
        os.system("rm -f /etc/apt/sources.list.d/official-source-repositories.list")

        template = open('/usr/share/mintsources/%s/official-package-repositories.list' % self.lsb_codename, 'r').read()
        template = template.replace("$codename", self.config["general"]["codename"])
        template = template.replace("$basecodename", self.config["general"]["base_codename"])
        template = template.replace("$optionalcomponents", '')
        template = template.replace("$mirror", self.config["mirrors"]["default"])
        template = template.replace("$basemirror", self.config["mirrors"]["base_default"])

        with open("/etc/apt/sources.list.d/official-package-repositories.list", "w") as text_file:
            text_file.write(template)

    def detect_official_sources(self):
        self.selected_mirror = self.config["mirrors"]["default"]
        self.selected_base_mirror = self.config["mirrors"]["base_default"]

        # Detect source code repositories
        self.builder.get_object("source_code_cb").set_active(os.path.exists("/etc/apt/sources.list.d/official-source-repositories.list"))

        listfile = open('/etc/apt/sources.list.d/official-package-repositories.list', 'r')
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
        self.builder.get_object("image_mirror").set_from_file("/usr/lib/linuxmint/mintSources/flags/generic.png")
        self.builder.get_object("image_base_mirror").set_from_file("/usr/lib/linuxmint/mintSources/flags/generic.png")

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
                if os.path.exists("/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower()):
                    self.builder.get_object("image_mirror").set_from_file("/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower())

        for mirror in self.base_mirrors:
            if mirror.url[-1] == "/":
                url = mirror.url[:-1]
            else:
                url = mirror.url
            if url in selected_base_mirror:
                if os.path.exists("/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower()):
                    self.builder.get_object("image_base_mirror").set_from_file("/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower())

    def get_clipboard_text(self, source_type):
        clipboard = gtk.Clipboard(display=gtk.gdk.display_get_default(), selection="CLIPBOARD")
        text = clipboard.wait_for_text()
        if text is not None and text.strip().startswith(source_type):
            return text
        else:
            return None

if __name__ == "__main__":
    if os.getuid() != 0:
        os.execvp("gksu", ("", " ".join(sys.argv)))
    else:
        usage = "usage: %prog [options] [repository]"
        parser = OptionParser(usage=usage)
        parser.add_option("-y", "--yes", dest="forceYes", action="store_true",
            help="force yes on all confirmation questions", default=False)

        (options, args) = parser.parse_args()

        if len(args) > 1 and (args[0] == "add-apt-repository"):
            ppa_line = args[1]
            lsb_codename = commands.getoutput("lsb_release -sc")
            config_parser = ConfigParser.RawConfigParser()
            config_parser.read("/usr/share/mintsources/%s/mintsources.conf" % lsb_codename)
            codename = config_parser.get("general", "base_codename")
            use_ppas = config_parser.get("general", "use_ppas")
            add_repository_via_cli(ppa_line, codename, options.forceYes, use_ppas)
        else:
            Application().run()
