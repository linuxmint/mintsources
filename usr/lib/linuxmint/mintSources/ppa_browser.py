#!/usr/bin/python3
import os
import sys
import apt
import gettext
import tempfile
import subprocess
import mintcommon
import platform
import locale
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

# i18n
APP = 'mintsources'
LOCALE_DIR = "/usr/share/linuxmint/locale"
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext

class PPA_Browser():

    def __init__(self, base_codename, ppa_owner, ppa_name, parent_window, standalone=False):
        self.is_standalone = standalone
        if platform.machine() == "X86_64":
            architecture = "amd64"
        else:
            architecture = "i386"
        ppa_origin = "LP-PPA-%s-%s" % (ppa_owner, ppa_name)
        ppa_origin_simple = "LP-PPA-%s" % (ppa_owner)
        ppa_file = "/var/lib/apt/lists/ppa.launchpad.net_%s_%s_ubuntu_dists_%s_main_binary-%s_Packages" % (ppa_owner, ppa_name, base_codename, architecture)

        if not os.path.exists(ppa_file):
            print ("%s not found!" % ppa_file)
            sys.exit(1)

        self.packages_to_install = []
        self.packages_installed_from_ppa = []

        glade_file = "/usr/lib/linuxmint/mintSources/mintsources.glade"

        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintsources")
        self.builder.add_from_file(glade_file)

        self.window = self.builder.get_object("ppa_window")
        self.window.set_title(_("PPA"))
        self.window.set_icon_name("software-sources")
        self.window.connect("destroy", self.exit)
        if parent_window is not None:
            self.window.set_transient_for(parent_window)
            self.window.set_modal(True)
        self.builder.get_object("button_cancel").connect("clicked", self.exit)
        self.install_button = self.builder.get_object("button_install")
        self.install_button.connect("clicked", self.install)
        self.install_button.set_sensitive(False)
        self.builder.get_object("label_ppa_name").set_markup("%s/%s" % (ppa_owner, ppa_name))

        self.model = Gtk.ListStore(object, bool, str)
        treeview = self.builder.get_object("treeview_ppa_pkgs")
        treeview.set_model(self.model)
        self.model.set_sort_column_id(2, Gtk.SortType.ASCENDING)

        r = Gtk.CellRendererToggle()
        r.connect("toggled", self.toggled)
        col = Gtk.TreeViewColumn("", r)
        col.set_cell_data_func(r, self.datafunction_checkbox)
        treeview.append_column(col)
        col.set_sort_column_id(1)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn("", r, markup = 2)
        treeview.append_column(col)
        col.set_sort_column_id(2)

        cache = apt.Cache()
        self.apt = mintcommon.APT(self.window)

        packages = subprocess.getoutput("grep 'Package:' %s | sort | awk {'print $2;'}" % ppa_file).split("\n")
        for package in packages:
            if package in cache:
                pkg = cache[package]
                candidate = pkg.candidate
                if candidate is not None and candidate.downloadable:
                    for origin in candidate.origins:
                        if origin.origin == ppa_origin or origin.origin == ppa_origin_simple:
                            if pkg.is_installed:
                                if pkg.installed.version != candidate.version:
                                    already_installed_str = _("version %s already installed") % pkg.installed.version
                                    self.model.append((pkg, False, "<b>%s</b>\n%s (%s)" % (pkg.name, candidate.version, already_installed_str)))
                                else:
                                    already_installed_str = _("already installed")
                                    self.model.append((pkg, False, "<b>%s</b>\n%s (%s)" % (pkg.name, candidate.version, already_installed_str)))
                                    self.packages_installed_from_ppa.append(pkg.name)
                            else:
                                self.model.append((pkg, False, "<b>%s</b>\n%s" % (pkg.name, candidate.version)))
                            break

        treeview.show()
        self.window.show_all()

    def datafunction_checkbox(self, column, cell, model, iter, data):
        if (model.get_value(iter, 0).name in self.packages_installed_from_ppa):
            cell.set_property("activatable", False)
            cell.set_property("active", True)
        else:
            cell.set_property("activatable", True)
            if (model.get_value(iter, 0).name in self.packages_to_install):
                cell.set_property("active", True)
            else:
                cell.set_property("active", False)

    def toggled (self, renderer, path):
        iter = self.model.get_iter(path)
        if (iter != None):
            pkg = self.model.get_value(iter, 0)
            if pkg.name in self.packages_to_install:
                self.packages_to_install.remove(pkg.name)
            else:
                self.packages_to_install.append(pkg.name)

        self.install_button.set_sensitive(len(self.packages_to_install) > 0)

    def install (self, button):
        self.apt.set_finished_callback(self.exit)
        self.apt.install_packages(self.packages_to_install)

    def exit(self, transaction=None, exit_state=None):
        self.window.hide()
        if self.is_standalone:
            sys.exit(0)

if __name__ == "__main__":
    base_codename = sys.argv[1]
    ppa_owner = sys.argv[2]
    ppa_name = sys.argv[3]
    PPA_Browser(base_codename, ppa_owner, ppa_name, True)
    Gtk.main()
