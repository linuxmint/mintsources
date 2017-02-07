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

(PKG_ID, PKG_CHECKED, PKG_NAME, PKG_INSTALLED_VERSION, PKG_REPO_VERSION, PKG_SORT_NAME) = range(6)

class Foreign_Browser():

    def __init__(self):

        architecture = commands.getoutput("dpkg --print-architecture")

        self.downgrade_mode = (sys.argv[1] == "downgrade") # whether to downgrade or remove packages

        glade_file = "/usr/lib/linuxmint/mintSources/mintSources.glade"

        self.builder = gtk.Builder()
        self.builder.add_from_file(glade_file)

        self.window = self.builder.get_object("foreign_window")
        self.window.set_title(_("Foreign packages"))
        self.window.set_icon_from_file("/usr/share/icons/hicolor/scalable/apps/software-sources.svg")
        self.window.connect("destroy", gtk.main_quit)
        self.builder.get_object("button_foreign_cancel").connect("clicked", gtk.main_quit)
        self.action_button = self.builder.get_object("button_foreign_action")
        self.action_button.connect("clicked", self.install)
        if self.downgrade_mode:
            self.action_button.set_label(_("Downgrade"))
            self.builder.get_object("label_foreign_explanation").set_markup("<i>%s</i>" % _("The version of the following packages doesn't match the one from the repositories:"))
        else:
            self.action_button.set_label(_("Remove"))
            self.builder.get_object("label_foreign_explanation").set_markup("<i>%s</i>" % _("The packages below are installed on your computer but not present in the repositories:"))
        self.action_button.set_sensitive(False)

        self.select_button = self.builder.get_object("button_foreign_select")
        self.select_button.connect("clicked", self.select_all)
        self.select_button.set_label(_("Select All"))
        self.select_button_selects_all = True

        self.builder.get_object("button_foreign_cancel").set_label(_("Cancel"))
        self.builder.get_object("label_foreign_warning").set_markup("<b>%s</b>" % _("WARNING"))
        self.builder.get_object("label_foreign_review").set_markup("<i>%s</i>" % _("Review the information below very carefully before validating the command:"))

        self.model = gtk.ListStore(str, bool, str, str, str, str)
        # PKG_ID, PKG_CHECKED, PKG_NAME, PKG_INSTALLED_VERSION, PKG_REPO_VERSION, PKG_SORT_NAME

        treeview = self.builder.get_object("treeview_foreign_pkgs")
        treeview.set_model(self.model)
        self.model.set_sort_column_id(PKG_SORT_NAME, gtk.SORT_ASCENDING)

        cr = gtk.CellRendererToggle()
        cr.connect("toggled", self.toggled)
        col = gtk.TreeViewColumn("", cr)
        col.set_cell_data_func(cr, self.datafunction_checkbox)
        treeview.append_column(col)
        col.set_sort_column_id(PKG_CHECKED)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Package"), r, markup = PKG_NAME)
        treeview.append_column(col)
        col.set_sort_column_id(PKG_NAME)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn(_("Installed version"), r, markup = PKG_INSTALLED_VERSION)
        treeview.append_column(col)
        col.set_sort_column_id(PKG_INSTALLED_VERSION)

        if self.downgrade_mode:
            r = gtk.CellRendererText()
            col = gtk.TreeViewColumn(_("Repository version"), r, markup = PKG_REPO_VERSION)
            treeview.append_column(col)
            col.set_sort_column_id(PKG_REPO_VERSION)

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

                    if self.downgrade_mode:
                        if best_version is not None:
                            for origin in best_version.origins:
                                iter = self.model.insert_before(None, None)
                                self.model.set_value(iter, PKG_ID, "%s=%s" % (pkg.name, best_version.version))
                                self.model.set_value(iter, PKG_CHECKED, False)
                                self.model.set_value(iter, PKG_NAME, "<b>%s</b>" % pkg.name)
                                self.model.set_value(iter, PKG_INSTALLED_VERSION, installed_version)
                                self.model.set_value(iter, PKG_REPO_VERSION, "%s (%s)" % (best_version.version, origin.archive))
                                self.model.set_value(iter, PKG_SORT_NAME, "%s %s" % (best_version.source_name, pkg.name))
                                break
                    else:
                        if best_version is None:
                            iter = self.model.insert_before(None, None)
                            self.model.set_value(iter, PKG_ID, "%s" % (pkg.name))
                            self.model.set_value(iter, PKG_CHECKED, False)
                            self.model.set_value(iter, PKG_NAME, "<b>%s</b>" % pkg.name)
                            self.model.set_value(iter, PKG_INSTALLED_VERSION, installed_version)
                            self.model.set_value(iter, PKG_REPO_VERSION, "")
                            self.model.set_value(iter, PKG_SORT_NAME, "%s" % (pkg.name))

        treeview.show()
        treeview.connect("row-activated", self.treeview_row_activated)
        self.window.show_all()

        parent_window_xid = int(sys.argv[2])
        try:
            parent = gtk.gdk.window_foreign_new(parent_window_xid)
            self.window.realize()
            self.window.window.set_transient_for(parent)
        except:
            pass

    def datafunction_checkbox(self, column, cell, model, iter):
        cell.set_property("activatable", True)
        checked = model.get_value(iter, PKG_CHECKED)
        if (checked):
            cell.set_property("active", True)
        else:
            cell.set_property("active", False)

    def treeview_row_activated(self, treeview, path, view_column):
        self.toggled(None, path)

    def toggled(self, renderer, path):
        iter = self.model.get_iter(path)
        if (iter != None):
            checked = self.model.get_value(iter, PKG_CHECKED)
            self.model.set_value(iter, PKG_CHECKED, not(checked))

        iter = self.model.get_iter_first()
        num_selected = 0
        while (iter != None):
            checked = self.model.get_value(iter, PKG_CHECKED)
            if (checked):
                num_selected = num_selected + 1
            iter = self.model.iter_next(iter)
        if num_selected > 0:
            self.action_button.set_sensitive(True)
        else:
            self.action_button.set_sensitive(False)

    def install (self, button):
        self.builder.get_object("notebook1").set_current_page(1)
        term = vte.Terminal()
        pid = term.fork_command('dash')
        term.set_emulation('xterm')

        foreign_packages = []
        iter = self.model.get_iter_first()
        while (iter != None):
            if (self.model.get_value(iter, PKG_CHECKED)):
                foreign_packages.append(self.model.get_value(iter, PKG_ID))
            iter = self.model.iter_next(iter)

        if self.downgrade_mode:
            term.feed_child("apt-get install %s\n" % " ".join(foreign_packages))
        else:
            term.feed_child("apt-get remove --purge %s\n" % " ".join(foreign_packages))

        term.show()
        self.builder.get_object("vbox_vte").add(term)
        self.builder.get_object("vbox_vte").show_all()

    def select_all (self, button):
        iter = self.model.get_iter_first()
        while (iter != None):
            pkg = self.model.set_value(iter, PKG_CHECKED, self.select_button_selects_all)
            iter = self.model.iter_next(iter)
        self.select_button_selects_all = not (self.select_button_selects_all)
        if self.select_button_selects_all:
            self.select_button.set_label(_("Select All"))
            self.action_button.set_sensitive(False)
        else:
            self.select_button.set_label(_("Clear"))
            self.action_button.set_sensitive(True)

if __name__ == "__main__":
    foreign_browser = Foreign_Browser()
    gtk.main()
