#!/usr/bin/python2
import gtk
import os
import sys
import apt
import commands
import gettext
import tempfile
from subprocess import Popen, PIPE
import subprocess
import vte

gettext.install("mintsources", "/usr/share/linuxmint/locale")

class Foreign_Browser():

    def __init__(self):
        architecture = commands.getoutput("dpkg --print-architecture")
        codename = commands.getoutput("lsb_release -u -c -s")

        self.packages_to_downgrade = []

        glade_file = "/usr/lib/linuxmint/mintSources/mintSources.glade"

        self.builder = gtk.Builder()
        self.builder.add_from_file(glade_file)

        self.window = self.builder.get_object("foreign_window")
        self.window.set_title(_("Foreign packages"))
        self.window.set_icon_from_file("/usr/share/icons/hicolor/scalable/apps/software-sources.svg")
        self.window.connect("destroy", gtk.main_quit)
        self.builder.get_object("button_foreign_cancel").connect("clicked", gtk.main_quit)
        self.downgrade_button = self.builder.get_object("button_foreign_downgrade")
        self.downgrade_button.connect("clicked", self.install)
        self.downgrade_button.set_label(_("Downgrade"))
        self.downgrade_button.set_sensitive(False)
        self.builder.get_object("button_foreign_cancel").set_label(_("Cancel"))
        self.builder.get_object("label_foreign_explanation").set_markup("<i>%s</i>" % _("The version of the following packages doesn't match the one from the repositories:"))
        self.builder.get_object("label_foreign_warning").set_markup("<b>%s</b>" % _("WARNING"))
        self.builder.get_object("label_foreign_review").set_markup("<i>%s</i>" % _("Review the information below very carefully before validating the command:"))

        self.model = gtk.ListStore(str, bool, str, str, str, str)
        treeview = self.builder.get_object("treeview_foreign_pkgs")
        treeview.set_model(self.model)
        self.model.set_sort_column_id(5, gtk.SORT_ASCENDING)

        r = gtk.CellRendererToggle()
        r.connect("toggled", self.toggled)
        col = gtk.TreeViewColumn("", r)
        col.set_cell_data_func(r, self.datafunction_checkbox)
        treeview.append_column(col)
        col.set_sort_column_id(1)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Package"), r, markup = 2)
        treeview.append_column(col)
        col.set_sort_column_id(2)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Installed version"), r, markup = 3)
        treeview.append_column(col)
        col.set_sort_column_id(3)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Repository version"), r, markup = 4)
        treeview.append_column(col)
        col.set_sort_column_id(4)

        cache = apt.Cache()
        for key in cache.keys():
            pkg = cache[key]
            if (pkg.is_installed):
                candidate_version = pkg.candidate.version
                installed_version = pkg.installed.version

                if not pkg.candidate.downloadable:
                    # The candidate is not downloadable...
                    # See if there's a version that is...
                    best_version = None
                    for version in pkg.versions:
                        if version.downloadable:
                            if best_version is None:
                                best_version = version
                            else:
                                if version.policy_priority > best_version.policy_priority:
                                    best_version = version
                                elif version.policy_priority == best_version.policy_priority:
                                    # same priorities, compare version
                                    if commands.getoutput("dpkg --compare-versions %s gt %s && echo 'OK'" % (version.version, best_version.version)) == "OK":
                                        best_version = version

                    if best_version is not None:
                        for origin in best_version.origins:
                            self.model.append(("%s=%s" % (pkg.name, best_version.version), False, "<b>%s</b>" % pkg.name, installed_version, "%s (%s)" % (best_version.version, origin.archive), "%s %s" % (best_version.source_name, pkg.name)))
                            break



        treeview.show()
        self.window.show_all()

    def datafunction_checkbox(self, column, cell, model, iter):
        cell.set_property("activatable", True)
        if (model.get_value(iter, 0) in self.packages_to_downgrade):
            cell.set_property("active", True)
        else:
            cell.set_property("active", False)

    def toggled (self, renderer, path):
        iter = self.model.get_iter(path)
        if (iter != None):
            pkg = self.model.get_value(iter, 0)
            if pkg in self.packages_to_downgrade:
                self.packages_to_downgrade.remove(pkg)
            else:
                self.packages_to_downgrade.append(pkg)

        self.downgrade_button.set_sensitive(len(self.packages_to_downgrade) > 0)

    def install (self, button):
        self.builder.get_object("notebook1").set_current_page(1)
        term = vte.Terminal()
        pid = term.fork_command('dash')
        term.set_emulation('xterm')
        term.feed_child("apt-get install %s\n" % " ".join(self.packages_to_downgrade))
        term.show()
        self.builder.get_object("vbox_vte").add(term)
        self.builder.get_object("vbox_vte").show_all()

if __name__ == "__main__":
    foreign_browser = Foreign_Browser()
    gtk.main()
