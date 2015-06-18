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

gettext.install("mintsources", "/usr/share/linuxmint/locale")

class PPA_Browser():

    def __init__(self, ppa_owner, ppa_name):
        architecture = commands.getoutput("dpkg --print-architecture")
        codename = commands.getoutput("lsb_release -u -c -s")
        ppa_origin = "LP-PPA-%s-%s" % (ppa_owner, ppa_name)
        ppa_origin_simple = "LP-PPA-%s" % (ppa_owner)
        ppa_file = "/var/lib/apt/lists/ppa.launchpad.net_%s_%s_ubuntu_dists_%s_main_binary-%s_Packages" % (ppa_owner, ppa_name, codename, architecture)

        if not os.path.exists(ppa_file):
            print "%s not found!" % ppa_file
            sys.exit(1)

        # print "Using origin: %s" % ppa_origin
        # print "Using release info: %s" % ppa_file

        self.packages_to_install = []

        glade_file = "/usr/lib/linuxmint/mintSources/mintSources.glade"

        self.builder = gtk.Builder()
        self.builder.add_from_file(glade_file)

        self.window = self.builder.get_object("ppa_window")
        self.window.set_title(_("PPA"))
        self.window.set_icon_from_file("/usr/share/icons/hicolor/scalable/apps/software-sources.svg")
        self.window.connect("destroy", gtk.main_quit)
        self.builder.get_object("button_cancel").connect("clicked", gtk.main_quit)
        self.install_button = self.builder.get_object("button_install")
        self.install_button.connect("clicked", self.install)
        self.install_button.set_label(_("Install"))
        self.install_button.set_sensitive(False)
        self.builder.get_object("label_ppa_name").set_markup("<b>%s/%s</b>" % (ppa_owner, ppa_name))
        self.builder.get_object("button_cancel").set_label(_("Cancel"))
        self.builder.get_object("label_explanation").set_markup("<i>%s</i>" % _("This PPA provides the following packages. Please select the ones you want to install:"))

        self.model = gtk.ListStore(object, bool, str)
        treeview = self.builder.get_object("treeview_ppa_pkgs")
        treeview.set_model(self.model)
        self.model.set_sort_column_id(2, gtk.SORT_ASCENDING)

        r = gtk.CellRendererToggle()
        r.connect("toggled", self.toggled)
        col = gtk.TreeViewColumn("", r)
        col.set_cell_data_func(r, self.datafunction_checkbox)
        treeview.append_column(col)
        col.set_sort_column_id(1)

        r = gtk.CellRendererText()
        col = gtk.TreeViewColumn("", r, markup = 2)
        treeview.append_column(col)
        col.set_sort_column_id(2)

        cache = apt.Cache()

        packages = commands.getoutput("grep 'Package:' %s | sort | awk {'print $2;'}" % ppa_file).split("\n")
        for package in packages:
            if package in cache:
                pkg = cache[package]
                candidate = pkg.candidate
                if candidate is not None and candidate.downloadable:
                    for origin in candidate.origins:
                        if origin.origin == ppa_origin or origin.origin == ppa_origin_simple:
                            if pkg.is_installed and pkg.installed.version != candidate.version:
                                already_installed_str = _("version %s already installed") % pkg.installed.version
                                self.model.append((pkg, False, "<b>%s</b> <small>%s (%s)</small>" % (pkg.name, candidate.version, already_installed_str)))
                            else:
                                self.model.append((pkg, False, "<b>%s</b> <small>%s</small>" % (pkg.name, candidate.version)))


        treeview.show()
        self.window.show_all()

        try:
            parent_window_xid = int(sys.argv[3])
            parent = gtk.gdk.window_foreign_new(parent_window_xid)
            self.window.realize()
            self.window.window.set_transient_for(parent)
        except:
            pass

    def datafunction_checkbox(self, column, cell, model, iter):
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
        cmd = ["pkexec", "/usr/sbin/synaptic", "--hide-main-window",  \
                "--non-interactive", "--parent-window-id", "%s" % self.window.window.xid]
        cmd.append("-o")
        cmd.append("Synaptic::closeZvt=true")
        cmd.append("--progress-str")
        cmd.append("\"" + _("Please wait, this can take some time.") + "\"")
        cmd.append("--finish-str")
        cmd.append("\"" + _("The packages were installed.") + "\"")
        f = tempfile.NamedTemporaryFile()
        for pkg in self.packages_to_install:
            f.write("%s\tinstall\n" % pkg)
        cmd.append("--set-selections-file")
        cmd.append("%s" % f.name)
        f.flush()
        comnd = Popen(' '.join(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True)
        returnCode = comnd.wait()
        f.close()
        if (returnCode == 0):
            sys.exit(0)

if __name__ == "__main__":
    ppa_owner = sys.argv[1]
    ppa_name = sys.argv[2]
    ppa_browser = PPA_Browser(ppa_owner, ppa_name)
    gtk.main()
