#!/usr/bin/python3
import gettext
import os
import subprocess
import sys
import threading
import locale

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Vte', '2.91')
from gi.repository import Gtk, Vte, GLib

import apt
import aptkit.simpleclient

# i18n
APP = 'mintsources'
LOCALE_DIR = "/usr/share/linuxmint/locale"
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)
_ = gettext.gettext

(PKG_ID, PKG_CHECKED, PKG_NAME, PKG_INSTALLED_VERSION, PKG_REPO_VERSION, PKG_SORT_NAME) = range(6)

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
        GLib.idle_add(func, *args)
    return wrapper

# Returns a tuple containing two lists
# The first list is a list of orphaned packages (packages which have no origin)
# The second list is a list of packages which version is not the official version (this does not
# include packages which simply aren't up to date)
def get_foreign_packages(find_orphans=True, find_downgradable_packages=True):
    orphan_packages = []
    downgradable_packages = []

    cache = apt.Cache()

    # python-apt doesn't give us a constant for this value
    # it's "required" in English, but it could be anything in
    # other languages
    required_priority = cache['dpkg'].installed.priority

    for key in cache.keys():
        pkg = cache[key]
        if (pkg.is_installed):
            installed_version = pkg.installed.version

            # Find packages which aren't downloadable
            if (pkg.candidate is None) or (not pkg.candidate.downloadable):
                if find_orphans:
                    downloadable = False
                    for version in pkg.versions:
                        if version.downloadable:
                            downloadable = True
                    if not downloadable:
                        orphan_packages.append([pkg, installed_version])
            if pkg.candidate is not None:
                if find_downgradable_packages:
                    best_version = None
                    archive = None
                    for version in pkg.versions:
                        if not version.downloadable:
                            continue
                        for origin in version.origins:
                            if origin.origin is not None and origin.origin.lower() in ("ubuntu", "canonical", "debian", "linuxmint"):
                                if best_version is None:
                                    best_version = version
                                    archive = origin.archive
                                else:
                                    if version.policy_priority > best_version.policy_priority:
                                        best_version = version
                                        archive = origin.archive
                                    elif version.policy_priority == best_version.policy_priority:
                                        # same priorities, compare version
                                        return_code = subprocess.call(["dpkg", "--compare-versions", version.version, "gt", best_version.version])
                                        if return_code == 0:
                                            best_version = version
                                            archive = origin.archive

                    if best_version is not None and installed_version != best_version and pkg.candidate.version != best_version.version:
                        downgradable_packages.append([pkg, installed_version, best_version, archive])

    return (orphan_packages, downgradable_packages)

class Foreign_Browser():

    def __init__(self):

        self.downgrade_mode = (sys.argv[1] == "downgrade") # whether to downgrade or remove packages

        glade_file = "/usr/lib/linuxmint/mintSources/mintsources.glade"

        self.builder = Gtk.Builder()
        self.builder.set_translation_domain("mintsources")
        self.builder.add_from_file(glade_file)

        self.window = self.builder.get_object("foreign_window")
        self.window.set_title(_("Foreign packages"))
        self.window.set_icon_name("software-sources")
        self.window.connect("destroy", Gtk.main_quit)
        self.builder.get_object("button_foreign_cancel").connect("clicked", Gtk.main_quit)
        self.action_button = self.builder.get_object("button_foreign_action")
        self.action_button.connect("clicked", self.install)
        if self.downgrade_mode:
            self.action_button.set_label(_("Downgrade"))
            self.builder.get_object("label_foreign_explanation").set_markup("%s" % _("The version of the following packages doesn't match the one from the repositories:"))
        else:
            self.action_button.set_label(_("Remove"))
            self.builder.get_object("label_foreign_explanation").set_markup("%s" % _("The packages below are installed on your computer but not present in the repositories:"))
        self.action_button.set_sensitive(False)

        self.select_button = self.builder.get_object("button_foreign_select")
        self.select_button.connect("clicked", self.select_all)
        self.select_button.set_label(_("Select All"))
        self.select_button_selects_all = True

        self.model = Gtk.ListStore(str, bool, str, str, str, str)
        # PKG_ID, PKG_CHECKED, PKG_NAME, PKG_INSTALLED_VERSION, PKG_REPO_VERSION, PKG_SORT_NAME

        treeview = self.builder.get_object("treeview_foreign_pkgs")
        treeview.set_model(self.model)
        self.model.set_sort_column_id(PKG_SORT_NAME, Gtk.SortType.ASCENDING)

        cr = Gtk.CellRendererToggle()
        cr.connect("toggled", self.toggled)
        col = Gtk.TreeViewColumn("", cr)
        col.set_cell_data_func(cr, self.datafunction_checkbox)
        treeview.append_column(col)
        col.set_sort_column_id(PKG_CHECKED)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_("Package"), r, markup = PKG_NAME)
        treeview.append_column(col)
        col.set_sort_column_id(PKG_NAME)

        r = Gtk.CellRendererText()
        col = Gtk.TreeViewColumn(_("Installed version"), r, markup = PKG_INSTALLED_VERSION)
        treeview.append_column(col)
        col.set_sort_column_id(PKG_INSTALLED_VERSION)

        if self.downgrade_mode:
            r = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(_("Repository version"), r, markup = PKG_REPO_VERSION)
            treeview.append_column(col)
            col.set_sort_column_id(PKG_REPO_VERSION)

        self.builder.get_object("stack1").set_visible_child_name("spin_page")
        self.builder.get_object("spinner").set_size_request(32, 32)
        self.builder.get_object("spinner").start()
        self.load_foreign_packages()

        treeview.show()
        treeview.connect("row-activated", self.treeview_row_activated)
        self.window.show_all()

    @run_async
    def load_foreign_packages(self):
        if self.downgrade_mode:
            (orphans, foreigns) = get_foreign_packages(find_orphans=False, find_downgradable_packages=True)
        else:
            (orphans, foreigns) = get_foreign_packages(find_orphans=True, find_downgradable_packages=False)
        self.update_ui(orphans, foreigns)

    @idle
    def update_ui(self, orphans, foreigns):
        if self.downgrade_mode:
            # downgrade mode
            # Find packages which candidate isn't available
            for foreign in foreigns:
                (pkg, installed_version, best_version, archive) = foreign
                iter = self.model.insert_before(None, None)
                self.model.set_value(iter, PKG_ID, "%s=%s" % (pkg.name, best_version.version))
                self.model.set_value(iter, PKG_CHECKED, False)
                self.model.set_value(iter, PKG_NAME, "<b>%s</b>" % pkg.name)
                self.model.set_value(iter, PKG_INSTALLED_VERSION, installed_version)
                self.model.set_value(iter, PKG_REPO_VERSION, "%s (%s)" % (best_version.version, archive))
                self.model.set_value(iter, PKG_SORT_NAME, "%s %s" % (best_version.source_name, pkg.name))
        else:
            # remove mode
            # Find packages which aren't downloadable
            for orphan in orphans:
                (pkg, installed_version) = orphan
                iter = self.model.insert_before(None, None)
                self.model.set_value(iter, PKG_ID, "%s" % (pkg.name))
                self.model.set_value(iter, PKG_CHECKED, False)
                self.model.set_value(iter, PKG_NAME, "<b>%s</b>" % pkg.name)
                self.model.set_value(iter, PKG_INSTALLED_VERSION, installed_version)
                self.model.set_value(iter, PKG_REPO_VERSION, "")
                self.model.set_value(iter, PKG_SORT_NAME, "%s" % (pkg.name))

        self.builder.get_object("spinner").stop()
        self.builder.get_object("stack1").set_visible_child_name("main_page")

    def datafunction_checkbox(self, column, cell, model, iter, data):
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
        if iter is not None:
            checked = self.model.get_value(iter, PKG_CHECKED)
            self.model.set_value(iter, PKG_CHECKED, not(checked))

        iter = self.model.get_iter_first()
        num_selected = 0
        while iter is not None:
            checked = self.model.get_value(iter, PKG_CHECKED)
            if (checked):
                num_selected = num_selected + 1
            iter = self.model.iter_next(iter)
        if num_selected > 0:
            self.action_button.set_sensitive(True)
        else:
            self.action_button.set_sensitive(False)

    def install (self, button):
        foreign_packages = []
        iter = self.model.get_iter_first()
        while iter is not None:
            if (self.model.get_value(iter, PKG_CHECKED)):
                foreign_packages.append(self.model.get_value(iter, PKG_ID))
            iter = self.model.iter_next(iter)
        if self.downgrade_mode:
            self.builder.get_object("stack1").set_visible_child_name("vte")
            terminal = Vte.Terminal()
            terminal.spawn_sync(Vte.PtyFlags.DEFAULT, os.environ['HOME'], ["/bin/dash"], [], GLib.SpawnFlags.DO_NOT_REAP_CHILD, None, None,)
            cmd = "apt-get install %s\n" % " ".join(foreign_packages)
            # try/catch VTE call due to regressions and obscure documentation
            try:
                # Mint 19.x
                terminal.feed_child(cmd, -1)
            except:
                # LMDE 4
                terminal.feed_child(cmd.encode("UTF-8"))
            terminal.show()
            self.builder.get_object("box_vte").add(terminal)
            self.builder.get_object("box_vte").show_all()
        else:
            apt = aptkit.simpleclient.SimpleAPTClient(self.window)
            apt.set_finished_callback(self.exit)
            apt.remove_packages(foreign_packages)

    def exit(self, transaction=None, exit_state=None):
        sys.exit(0)

    def select_all (self, button):
        iter = self.model.get_iter_first()
        while iter is not None:
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
    Gtk.main()
