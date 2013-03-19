#! /usr/bin/python
# -*- coding=utf-8 -*-

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

class RepositoryToggleCheckBox(gtk.CheckButton):
    def __init__(self, repository):
        self.repository = repository        
        gtk.CheckButton.__init__(self, self.repository.line)
        self.set_active(repository.selected)      
        self.set_tooltip_text(self.repository.file)              
        self.connect("toggled", self._on_toggled)
    
    def _on_toggled(self, widget):
        self.repository.selected = widget.get_active()

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
    def __init__(self, application, ui_builder):
        self._application = application
        self._ui_builder = ui_builder
        
        self._dialog = ui_builder.get_object("mirror_selection_dialog")
        self._dialog.set_transient_for(application._main_window)
        
        self._mirrors = None
        self._mirrors_model = gtk.ListStore(object, str, gtk.gdk.Pixbuf, float, gtk.gdk.Pixbuf)
        self._treeview = ui_builder.get_object("mirrors_treeview")
        self._treeview.set_model(self._mirrors_model)
        self._treeview.set_headers_clickable(True)
        
        self._mirrors_model.set_sort_column_id(MirrorSelectionDialog.MIRROR_SPEED_COLUMN, gtk.SORT_DESCENDING)
        
        r = gtk.CellRendererPixbuf()
        col = gtk.TreeViewColumn(_("Country"), r, pixbuf = MirrorSelectionDialog.MIRROR_COUNTRY_COLUMN)
        self._treeview.append_column(col)
        col.set_sort_column_id(MirrorSelectionDialog.MIRROR_COUNTRY_COLUMN)

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
                None
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

        self.lsb_codename = commands.getoutput("lsb_release -sc")        

        glade_file = "/usr/lib/linuxmint/mintSources/mintSources.glade"        
            
        self.builder = gtk.Builder()
        self.builder.add_from_file(glade_file)
        self._main_window = self.builder.get_object("main_window")
        self._notebook = self.builder.get_object("notebook")
        self._official_repositories_box = self.builder.get_object("official_repositories_box")        
            
        config_parser = ConfigParser.RawConfigParser()
        config_parser.read("/usr/share/mintsources/%s/mintsources.conf" % self.lsb_codename)
        self.config = {}
        self.optional_components = []
        for section in config_parser.sections():
            if section.startswith("optional_component"):
                component = Component(config_parser.get(section, "name"), config_parser.get(section, "description"), False)
                self.optional_components.append(component)
            else:
                self.config[section] = {}                        
                for param in config_parser.options(section):                
                    self.config[section][param] = config_parser.get(section, param)     

        self.builder.get_object("label_title_official").set_markup("%s" % _("Official repositories"))     
        self.builder.get_object("label_title_ppa").set_markup("%s" % _("PPAs"))     
        self.builder.get_object("label_title_3rd").set_markup("%s" % _("Additional repositories"))     
        self.builder.get_object("label_title_keys").set_markup("%s" % _("Authentication keys"))     

        self.builder.get_object("label_mirrors").set_markup("<b>%s</b>" % _("Mirrors"))    
        self.builder.get_object("label_mirror_description").set_markup("%s (%s)" % (_("Main"), self.config["general"]["codename"]) )
        self.builder.get_object("label_base_mirror_description").set_markup("%s (%s)" % (_("Base"), self.config["general"]["base_codename"]) )
        self.builder.get_object("button_mirror").set_tooltip_text("Select a faster server...")
        self.builder.get_object("button_base_mirror").set_tooltip_text("Select a faster server...")

        self.builder.get_object("label_optional_components").set_markup("<b>%s</b>" % _("Optional components"))                    
        self.builder.get_object("label_source_code").set_markup("<b>%s</b>" % _("Source code"))

        self.builder.get_object("label_ppa_add").set_markup("%s" % _("Add a new PPA..."))
        self.builder.get_object("label_ppa_edit").set_markup("%s" % _("Edit URL..."))
        self.builder.get_object("label_ppa_remove").set_markup("%s" % _("Remove permanently"))
        
        self.builder.get_object("label_description").set_markup("<b>%s</b>" % self.config["general"]["description"])
        self.builder.get_object("image_icon").set_from_file("/usr/share/mintsources/%s/icon.png" % self.lsb_codename)

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


        self.mirrors = []
        mirrorsfile = open(self.config["mirrors"]["mirrors"], "r")
        for line in mirrorsfile.readlines():
            line = line.strip()
            if ("#LOC:" in line):
                country_code = line.split(":")[1]
            else:
                if country_code is not None:
                    mirror = Mirror(line, country_code)
                    self.mirrors.append(mirror)

        self.base_mirrors = []
        mirrorsfile = open(self.config["mirrors"]["base_mirrors"], "r")
        for line in mirrorsfile.readlines():
            line = line.strip()
            if ("#LOC:" in line):
                country_code = line.split(":")[1]
            else:
                if country_code is not None:
                    mirror = Mirror(line, country_code)
                    self.base_mirrors.append(mirror)     
        
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

        self._ppa_model = gtk.ListStore(object, bool, str, str, str, str)
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
        col = gtk.TreeViewColumn(_("PPA"), r, text = 2)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(2)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Type"), r, text = 3)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(3)      

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("URL"), r, text = 4)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(4)      

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("File"), r, text = 5)
        self._ppa_treeview.append_column(col)
        col.set_sort_column_id(5)      

        if (len(self.ppas) > 0):                                                                                    
            for repository in self.ppas:
                if repository.line.startswith("deb-src"):
                    type = _("Sources")
                else:
                    type = _("Packages")
                elements = repository.line.split(" ")
                name = elements[1].replace("deb-src", "")
                name = name.replace("deb", "")
                name = name.replace("http://ppa.launchpad.net/", "")
                name = name.replace("/ubuntu", "")
                name = name.replace("/ppa", "")
                tree_iter = self._ppa_model.append((repository, repository.selected, name, type, repository.line, repository.file))

        if (len(self.repositories) > 0):            
            table = gtk.Table()
            self.builder.get_object("vbox_repositories").pack_start(table, True, True)
            self.builder.get_object("vbox_repositories").show_all()
            nb_components = 0
            for repository in self.repositories:                
                cb = RepositoryToggleCheckBox(repository)
                table.attach(cb, 0, 1, nb_components, nb_components + 1, xoptions = gtk.FILL | gtk.EXPAND, yoptions = 0)
                nb_components += 1                        
        

        self.detect_official_sources()     

        self.builder.get_object("revert_button").connect("clicked", self.revert_to_default_sources)            
        self.builder.get_object("label_revert").set_markup(_("Restore the default settings"))
        self.builder.get_object("revert_button").set_tooltip_text("Restore the official repositories to their default settings")
        
        self._tab_buttons = [
            self.builder.get_object("toggle_official_repos"),
            self.builder.get_object("toggle_ppas"),
            self.builder.get_object("toggle_additional_repos"),
            self.builder.get_object("toggle_authentication_keys")
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

    def add_ppa(self, widget):
        image = gtk.Image()
        image.set_from_file("/usr/lib/linuxmint/mintSources/ppa.png")

        line = self.show_entry_dialog(self._main_window, _("Please enter the name of the PPA you want to add:"), "ppa:username/ppa", image)
        if line is not None:
            user, sep, ppa_name = line.split(":")[1].partition("/")
            ppa_name = ppa_name or "ppa"
            try:
                ppa_info = self.get_ppa_info_from_lp(user, ppa_name)                
            except Exception, detail:
                self.show_error_dialog(self._main_window, _("Cannot add PPA: '%s'.") % detail)
                return
        
            image = gtk.Image()
            image.set_from_file("/usr/lib/linuxmint/mintSources/ppa.png")
            if self.show_confirmation_dialog(self._main_window, "<b>%s</b>\n\n%s\n\n<i>%s</i>" % (line, ppa_info["description"], str(ppa_info["web_link"])), image):                                
                (deb_line, file) = self.expand_ppa_line(line.strip(), self.config["general"]["base_codename"])
                deb_line = self.expand_http_line(deb_line, self.config["general"]["base_codename"])
                debsrc_line = 'deb-src' + deb_line[3:]
                # Add the key
                short_key = ppa_info["signing_key_fingerprint"][-8:]
                os.system("apt-key adv --keyserver keyserver.ubuntu.com --recv-keys %s" % short_key)
                # Add the PPA in sources.list.d
                with open(file, "w") as text_file:
                    text_file.write("%s\n" % deb_line)
                    text_file.write("%s\n" % debsrc_line)
                
                # Add the package line in the UI                
                repository = Repository(self, deb_line, file, True)
                self.ppas.append(repository)                
                elements = repository.line.split(" ")
                name = elements[1].replace("deb-src", "")
                name = name.replace("deb", "")
                name = name.replace("http://ppa.launchpad.net/", "")
                name = name.replace("/ubuntu", "")
                name = name.replace("/ppa", "")
                tree_iter = self._ppa_model.append((repository, repository.selected, name, _("Packages"), repository.line, repository.file))

                # Add the source line in the UI                
                repository = Repository(self, debsrc_line, file, True)
                self.ppas.append(repository)                
                elements = repository.line.split(" ")
                name = elements[1].replace("deb-src", "")
                name = name.replace("deb", "")
                name = name.replace("http://ppa.launchpad.net/", "")
                name = name.replace("/ubuntu", "")
                name = name.replace("/ppa", "")
                tree_iter = self._ppa_model.append((repository, repository.selected, name, _("Sources"), repository.line, repository.file))

                self.enable_reload_button()
                

    def edit_ppa(self, widget):        
        selection = self._ppa_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):            
            repository = model.get(iter, 0)[0]
            url = self.show_entry_dialog(self._main_window, _("Edit the URL of the PPA"), repository.line)
            if url is not None:
                repository.edit(url)
                model.set_value(iter, 4, url)

    def remove_ppa(self, widget):        
        selection = self._ppa_treeview.get_selection()
        (model, iter) = selection.get_selected()
        if (iter != None):            
            repository = model.get(iter, 0)[0]
            if (self.show_confirmation_dialog(self._main_window, _("Are you sure you want to permanently remove this PPA?"))):
                model.remove(iter)                
                repository.delete()
                self.ppas.remove(repository)
            

    def show_confirmation_dialog(self, parent, message, image=None):
        d = gtk.MessageDialog(parent,
                              gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
                              gtk.MESSAGE_WARNING,
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

    def get_ppa_info_from_lp(self, owner_name, ppa_name):
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

    def encode(self, s):
        return re.sub("[^a-zA-Z0-9_-]", "_", s)

    def expand_ppa_line(self, abrev, distro_codename):        
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
        filename = os.path.join(sourceslistd, "%s-%s-%s.list" % (self.encode(ppa_owner), self.encode(ppa_name), distro_codename))
        return (line, filename)

    def expand_http_line(self, line, distro_codename):
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
        Application().run()
