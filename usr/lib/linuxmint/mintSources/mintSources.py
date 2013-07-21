#!/usr/bin/python

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
try:
    import urllib.request
    from urllib.error import URLError
    import urllib.parse
except ImportError:
    import pycurl



def add_ppa_via_cli(line, codename):   

    if line is not None:
        user, sep, ppa_name = line.split(":")[1].partition("/")
        ppa_name = ppa_name or "ppa"
        try:
            ppa_info = get_ppa_info_from_lp(user, ppa_name)
        except Exception, detail:
            print _("Cannot add PPA: '%s'.") % detail
            sys.exit(1)

        if "private" in ppa_info and ppa_info["private"]:
            print(_("Adding private PPAs is not supported currently"))
            sys.exit(1)
        
        print(_("You are about to add the following PPA to your system:"))            
        print(" %s" % (ppa_info["description"] or ""))            
        print(_(" More info: %s") % str(ppa_info["web_link"]))

        if sys.stdin.isatty():
            print(_("Press [ENTER] to continue or ctrl-c to cancel adding it"))
            sys.stdin.readline()
        
            (deb_line, file) = expand_ppa_line(line.strip(), codename)
            deb_line = expand_http_line(deb_line, codename)
            debsrc_line = 'deb-src' + deb_line[3:]
            
            # Add the key
            short_key = ppa_info["signing_key_fingerprint"][-8:]
            os.system("apt-key adv --keyserver keyserver.ubuntu.com --recv-keys %s" % short_key)

            # Add the PPA in sources.list.d
            with open(file, "w") as text_file:
                text_file.write("%s\n" % deb_line)
                text_file.write("%s\n" % debsrc_line)  

def get_ppa_info_from_lp(owner_name, ppa_name):
    DEFAULT_KEYSERVER = "hkp://keyserver.ubuntu.com:80/"
    # maintained until 2015
    LAUNCHPAD_PPA_API = 'https://launchpad.net/api/1.0/~%s/+archive/%s'
    # Specify to use the system default SSL store; change to a different path
    # to test with custom certificates.
    LAUNCHPAD_PPA_CERT = "/etc/ssl/certs/ca-certificates.crt"

    lp_url = LAUNCHPAD_PPA_API % (owner_name, ppa_name)
    try:
        try:
            request = urllib.request.Request(str(lp_url), headers={"Accept":" application/json"})
            lp_page = urllib.request.urlopen(request, cafile=LAUNCHPAD_PPA_CERT)
            json_data = lp_page.read().decode("utf-8", "strict")
        except URLError as e:
            raise PPAException("Error reading %s: %s" % (lp_url, e.reason), e)
    except PPAException:
        raise
    except:
        import pycurl
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

# i18n for menu item
menuName = _("Software Sources")
menuComment = _("Configure the sources for installable software and updates")

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
    def __init__(self, url, country_code):
        self.url = url
        self.country_code = country_code        

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
        elements = self.line.split(" ")
        name = elements[1].replace("deb-src ", "")
        name = name.replace("deb ", "")
        if name.startswith("http://") or name.startswith("ftp://"):                    
            name = name.replace("http://", "")
            name = name.replace("ftp://", "")
            parts = name.split("/")
            if len(parts) > 0:
                name = parts[0]    
        if self.line.startswith("deb-src"):
            name = "%s (%s)" % (name, _("Sources")) 
        return "<b>%s</b>\n<small><i>%s</i></small>\n<small><i>%s</i></small>" % (name, self.line, self.file)

class ComponentToggleCheckBox(gtk.CheckButton):
    def __init__(self, application, component):
        self.application = application
        self.component = component        
        gtk.CheckButton.__init__(self, self.component.description)
        self.set_active(component.selected)                    
        self.connect("toggled", self._on_toggled)
    
    def _on_toggled(self, widget):
        self.component.selected = widget.get_active()
        self.application.apply_official_sources()

class ServerSelectionComboBox(gtk.ComboBox):
    def __init__(self, application, repo):
        gtk.ComboBox.__init__(self)
        
        self._repo = repo
        self._application = application
        
        self._model = gtk.ListStore(str, str, bool, bool)
        self.set_model(self._model)
        
        cell = gtk.CellRendererText()
        self.pack_start(cell, True)
        self.add_attribute(cell, 'text', 0)
        
        self.set_row_separator_func(lambda m,i: m.get(i, 3)[0])
        
        self.refresh()
        
        self._block_on_changed = False
        self.connect("changed", self._on_changed)
    
    def _on_changed(self, widget):
        if self._block_on_changed:
            return
        url = self._model[widget.get_active()][1]
        if url == None:
            url = self._application.mirror_selection_dialog.run(self._repo)
        print url
        if url != None:
            self._repo["distro"].main_server = url
            self._repo["distro"].change_server(url)
            self._application.save_sourceslist()
            self._repo["distro"].get_sources(self._application.sourceslist)
        self.refresh()
    
    def refresh(self):
        self._block_on_changed = True
        self._model.clear()
        selected_iter = None
        for name, url, active in self._repo["distro"].get_server_list():
            tree_iter = self._model.append((name, url, active, False))
            if active:
                selected_iter = tree_iter
        self._model.append((None, None, None, True))
        self._model.append((_("Other..."), None, None, False))
        
        if selected_iter is not None:
            self.set_active_iter(selected_iter)
        
        self._block_on_changed = False

class MirrorSelectionDialog(object):
    MIRROR_COLUMN = 0
    MIRROR_URL_COLUMN = 1
    MIRROR_COUNTRY_COLUMN = 2
    MIRROR_SPEED_COLUMN = 3
    MIRROR_SPEED_BAR_COLUMN = 4
    MIRROR_COUNTRY_CODE_COLUMN = 5 # invisible
    
    def __init__(self, application, ui_builder):
        self._application = application
        self._ui_builder = ui_builder
        
        self._dialog = ui_builder.get_object("mirror_selection_dialog")
        self._dialog.set_transient_for(application._main_window)

        self._dialog.set_title(_("Select a mirror"))
        
        self._mirrors = None
        self._mirrors_model = gtk.ListStore(object, str, gtk.gdk.Pixbuf, float, gtk.gdk.Pixbuf, str)
        # mirror, name, flag, speed, speed chart, country code (used to sort by flag)
        self._treeview = ui_builder.get_object("mirrors_treeview")
        self._treeview.set_model(self._mirrors_model)
        self._treeview.set_headers_clickable(True)
        
        self._mirrors_model.set_sort_column_id(MirrorSelectionDialog.MIRROR_SPEED_COLUMN, gtk.SORT_DESCENDING)
        
        r = gtk.CellRendererPixbuf()
        col = gtk.TreeViewColumn(_("Country"), r, pixbuf = MirrorSelectionDialog.MIRROR_COUNTRY_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_COUNTRY_CODE_COLUMN)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("URL"), r, text = MirrorSelectionDialog.MIRROR_URL_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_URL_COLUMN)            
        
        r = gtk.CellRendererPixbuf()
        col = gtk.TreeViewColumn(_("Speed"), r, pixbuf = MirrorSelectionDialog.MIRROR_SPEED_BAR_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_SPEED_COLUMN)
        col.set_min_width(int(1.1 * SPEED_PIX_WIDTH))
        
        self._speed_test_lock = thread.allocate_lock()
        self._current_speed_test_index = -1
        self._best_speed = -1
        
        self._speed_pixbufs = {}
        self.country_info = CountryInformation()
    
    def _update_list(self):
        self._mirrors_model.clear()
        for mirror in self._mirrors:
            flag = "/usr/lib/linuxmint/mintSources/flags/generic.png"
            if os.path.exists("/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower()):
                flag = "/usr/lib/linuxmint/mintSources/flags/%s.png" % mirror.country_code.lower()            
            self._mirrors_model.append((
                mirror,
                mirror.url,
                gtk.gdk.pixbuf_new_from_file(flag),
                -1,
                None,
                mirror.country_code.lower()
            ))
        self._next_speed_test()
    
    def _next_speed_test(self):
        test_mirror = None
        for i in range(len(self._mirrors_model)):
            url = self._mirrors_model[i][MirrorSelectionDialog.MIRROR_URL_COLUMN]
            speed = self._mirrors_model[i][MirrorSelectionDialog.MIRROR_SPEED_COLUMN]
            if speed == -1:
                test_mirror = url
                self._current_speed_test_index = i
                break
        if test_mirror:
            self._speed_test_result = None
            gobject.timeout_add(100, self._check_speed_test_done)
            thread.start_new_thread(self._speed_test, (test_mirror,))
    
    def _check_speed_test_done(self):
        self._speed_test_lock.acquire()
        speed_test_result = self._speed_test_result
        self._speed_test_lock.release()
        if speed_test_result != None and len(self._mirrors_model) > 0:
            self._mirrors_model[self._current_speed_test_index][MirrorSelectionDialog.MIRROR_SPEED_COLUMN] = speed_test_result
            self._best_speed = max(self._best_speed, speed_test_result)
            self._update_relative_speeds()
            self._next_speed_test()
            return False
        else:
            return True
    
    def _update_relative_speeds(self):
        if self._best_speed > 0:
            for i in range(len(self._mirrors_model)):
                self._mirrors_model[i][MirrorSelectionDialog.MIRROR_SPEED_BAR_COLUMN] = self._get_speed_pixbuf(int(100 * self._mirrors_model[i][MirrorSelectionDialog.MIRROR_SPEED_COLUMN] / self._best_speed))
    
    def _get_speed_pixbuf(self, speed):
        represented_speed = 10 * (speed / 10)
        if speed > 0:
            if not speed in self._speed_pixbufs:
                color_pix = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, False, 8, SPEED_PIX_WIDTH * speed / 100, SPEED_PIX_HEIGHT)
                red = 0xff000000
                green = 0x00ff0000
                if represented_speed > 50:
                    red_level = (100 - represented_speed) / 50.
                    green_level = 1
                else:
                    red_level = 1
                    green_level = (represented_speed / 50.)
                red_level = int(255 * red_level) * 0x01000000
                green_level = int(255 * green_level) * 0x00010000
                color = red_level + green_level
                color_pix.fill(color)
                final_pix = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, False, 8, SPEED_PIX_WIDTH, SPEED_PIX_HEIGHT)
                final_pix.fill(0xffffffff)
                color_pix.copy_area(0, 0, SPEED_PIX_WIDTH * speed / 100, SPEED_PIX_HEIGHT, final_pix, 0, 0)
                del color_pix
                self._speed_pixbufs[speed] = final_pix
            pix = self._speed_pixbufs[speed]
        else:
            pix = None
        return pix
    
    def _speed_test(self, url):
        try:
            c = pycurl.Curl()
            buff = cStringIO.StringIO()
            c.setopt(pycurl.URL, url)
            c.setopt(pycurl.CONNECTTIMEOUT, 10)
            c.setopt(pycurl.TIMEOUT, 10)
            c.setopt(pycurl.FOLLOWLOCATION, 1)
            c.setopt(pycurl.WRITEFUNCTION, buff.write)
            c.setopt(pycurl.NOSIGNAL, 1)
            c.perform()
            download_speed = c.getinfo(pycurl.SPEED_DOWNLOAD)
        except:
            download_speed = -2
        self._speed_test_lock.acquire()
        self._speed_test_result = download_speed
        self._speed_test_lock.release()
    
    def run(self, mirrors):
        self._mirrors = mirrors
        self._best_speed = -1
        self._update_list()
        self._dialog.show_all()
        if self._dialog.run() == gtk.RESPONSE_APPLY:
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
        self._mirrors = None
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

        self.builder.get_object("label_mirrors").set_markup("<b>%s</b>" % _("Mirrors"))    
        self.builder.get_object("label_mirror_description").set_markup("%s (%s)" % (_("Main"), self.config["general"]["codename"]) )
        self.builder.get_object("label_base_mirror_description").set_markup("%s (%s)" % (_("Base"), self.config["general"]["base_codename"]) )
        self.builder.get_object("button_mirror").set_tooltip_text(_("Select a faster server..."))
        self.builder.get_object("button_base_mirror").set_tooltip_text(_("Select a faster server..."))

        self.builder.get_object("label_optional_components").set_markup("<b>%s</b>" % _("Optional components"))                    
        self.builder.get_object("label_source_code").set_markup("<b>%s</b>" % _("Source code"))

        self.builder.get_object("label_ppa_add").set_markup("%s" % _("Add a new PPA..."))
        self.builder.get_object("label_ppa_edit").set_markup("%s" % _("Edit URL..."))
        self.builder.get_object("label_ppa_remove").set_markup("%s" % _("Remove permanently"))

        self.builder.get_object("label_repository_add").set_markup("%s" % _("Add a new repository..."))
        self.builder.get_object("label_repository_edit").set_markup("%s" % _("Edit URL..."))
        self.builder.get_object("label_repository_remove").set_markup("%s" % _("Remove permanently"))

        self.builder.get_object("label_keys_add").set_markup("%s" % _("Import key file..."))
        self.builder.get_object("label_keys_fetch").set_markup("%s" % _("Download a key..."))
        self.builder.get_object("label_keys_remove").set_markup("%s" % _("Remove permanently"))

        self.builder.get_object("button_mergelist_label").set_markup("%s" % _("Fix MergeList problems"))
        self.builder.get_object("button_mergelist").set_tooltip_text("%s" % _("If you experience MergeList problems, click this button to solve the problem."))
        self.builder.get_object("button_purge_label").set_markup("%s" % _("Purge residual configuration"))
        self.builder.get_object("button_purge").set_tooltip_text("%s" % _("Packages sometimes leave configuration files on the system even after they are removed."))
        
        self.builder.get_object("label_description").set_markup("<b>%s</b>" % self.config["general"]["description"])
        self.builder.get_object("image_icon").set_from_file("/usr/share/mintsources/%s/icon.png" % self.lsb_codename)

        self.builder.get_object("source_code_cb").set_label(_("Enable source code repositories"))

        self.builder.get_object("source_code_cb").connect("toggled", self.apply_official_sources)
               
        self.selected_components = []
        if (len(self.optional_components) > 0):            
            components_table = gtk.Table()
            self.builder.get_object("vbox_optional_components").pack_start(components_table, True, True)
            self.builder.get_object("vbox_optional_components").show_all()
            nb_components = 0
            for i in range(len(self.optional_components)):
                component = self.optional_components[i]                
                cb = ComponentToggleCheckBox(self, component)
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
                        repository = Repository(self, line.replace('#', '').strip(), source_file, selected)                    
                        if "ppa.launchpad" in line:
                            self.ppas.append(repository)                                                
                        else:                        
                            self.repositories.append(repository)
            file.close() 

        # Add PPAs
        self._ppa_model = gtk.ListStore(object, bool, str)
        self._ppa_treeview = self.builder.get_object("treeview_ppa")
        self._ppa_treeview.set_model(self._ppa_model)
        self._ppa_treeview.set_headers_clickable(True)
        
        self._ppa_model.set_sort_column_id(2, gtk.SORT_DESCENDING)

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
        
        self._repository_model.set_sort_column_id(2, gtk.SORT_DESCENDING)

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
        
        self._keys_model.set_sort_column_id(1, gtk.SORT_DESCENDING)        
        
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

        self.builder.get_object("button_repository_add").connect("clicked", self.add_repository)
        self.builder.get_object("button_repository_edit").connect("clicked", self.edit_repository)
        self.builder.get_object("button_repository_remove").connect("clicked", self.remove_repository)

        self.builder.get_object("button_keys_add").connect("clicked", self.add_key)
        self.builder.get_object("button_keys_fetch").connect("clicked", self.fetch_key)
        self.builder.get_object("button_keys_remove").connect("clicked", self.remove_key)

        self.builder.get_object("button_mergelist").connect("clicked", self.fix_mergelist)
        self.builder.get_object("button_purge").connect("clicked", self.fix_purge)
        
        # From now on, we handle modifications to the settings and save them when they happen
        self._interface_loaded = True

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
                            mirror = Mirror(line, country_code)
                            mirror_list.append(mirror)
        return mirror_list

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
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this key?"), image)):                
                key.delete()
                self.load_keys()                

    def add_ppa(self, widget):
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/ppa.png")

        line = self.show_entry_dialog(self._main_window, _("Please enter the name of the PPA you want to add:"), "ppa:username/ppa", image)
        if line is not None:
            user, sep, ppa_name = line.split(":")[1].partition("/")
            ppa_name = ppa_name or "ppa"
            try:
                ppa_info = get_ppa_info_from_lp(user, ppa_name)                
            except Exception, detail:
                self.show_error_dialog(self._main_window, _("Cannot add PPA: '%s'.") % detail)
                return
        
            image = gtk.Image()
            image.set_from_file("/usr/lib/linuxmint/mintSources/ppa.png")
            if self.show_confirmation_dialog(self._main_window, "<b>%s</b>\n\n%s\n\n<i>%s</i>" % (line, ppa_info["description"].replace("<", "&lt;").replace(">", "&gt;"), str(ppa_info["web_link"])), image):                                
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
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this PPA?"))):
                model.remove(iter)                
                repository.delete()
                self.ppas.remove(repository)

    def add_repository(self, widget):
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/3rd.png")

        line = self.show_entry_dialog(self._main_window, _("Please enter the name of the repository you want to add:"), "deb http://packages.domain.com/ %s main" % self.config["general"]["base_codename"], image)
        if line is not None:                                
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
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this repository?"))):
                model.remove(iter)                
                repository.delete()
                self.repositories.remove(repository)
            

    def show_confirmation_dialog(self, parent, message, image=None, affirmation=None):
        if affirmation is None:
            d = gtk.MessageDialog(parent,
                              gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                              gtk.MESSAGE_WARNING,
                              gtk.BUTTONS_OK_CANCEL,
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
        
        d.set_default_response(gtk.RESPONSE_OK)
        r = d.run()        
        d.destroy()
        if r == gtk.RESPONSE_OK:
            return True
        else:
            return False

    def show_error_dialog(self, parent, message, image=None):        
        d = gtk.MessageDialog(parent,
                              gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                              gtk.MESSAGE_ERROR,
                              gtk.BUTTONS_OK_CANCEL,
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
        url = self.mirror_selection_dialog.run(self.mirrors)
        if url is not None:
            self.selected_mirror = url
            self.builder.get_object("label_mirror_name").set_text(self.selected_mirror)       
        self.apply_official_sources()

    def select_new_base_mirror(self, widget):
        url = self.mirror_selection_dialog.run(self.base_mirrors)
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
                        self.selected_mirror = mirror
            if (self.config["detection"]["base_identifier"] in line):
                elements = line.split(" ")
                if elements[0] == "deb":                    
                    mirror = elements[1]
                    if "$" not in mirror:
                        self.selected_base_mirror = mirror

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

if __name__ == "__main__":
    if os.getuid() != 0:
        os.execvp("gksu", ("", " ".join(sys.argv)))
    else:        
        if len(sys.argv) > 2 and (sys.argv[1] == "add-apt-repository"):
            ppa_line = sys.argv[2]
            lsb_codename = commands.getoutput("lsb_release -sc")
            config_parser = ConfigParser.RawConfigParser()
            config_parser.read("/usr/share/mintsources/%s/mintsources.conf" % lsb_codename)
            codename = config_parser.get("general", "base_codename")
            add_ppa_via_cli(ppa_line, codename)
        else:
            Application().run()
