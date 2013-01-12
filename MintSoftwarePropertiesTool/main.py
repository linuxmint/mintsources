#! /usr/bin/python
# -*- coding=utf-8 -*-

import os
import gtk
import urlparse
import ConfigParser
import aptsources.distro
import aptsources.distinfo
from aptsources.sourceslist import SourcesList

class ComponentToggleCheckBox(gtk.CheckButton):
    def __init__(self, application, repo, component):
        gtk.CheckButton.__init__(self, "%s (%s)" % (component.get_description(), component.name))
        self.set_active(component.name in repo["distro"].enabled_comps)
        
        self._repo = repo
        self._component = component
        self._application = application
        
        self.connect("toggled", self._on_toggled)
    
    def _on_toggled(self, widget):
        if widget.get_active():
            self._repo["distro"].enable_component(self._component.name)
        else:
            self._repo["distro"].disable_component(self._component.name)
        self._application.save_sourceslist()

class SourceCodeToggleCheckBox(gtk.CheckButton):
    def __init__(self, application, sourceslist, repo):
        gtk.CheckButton.__init__(self, _("Source code"))
        self.set_active(len(repo["distro"].source_code_sources) > 0)
        
        self._repo = repo
        self._application = application
        self._sourceslist = sourceslist
        
        self.connect("toggled", self._on_toggled)
    
    def _on_toggled(self, widget):
        sources = []
        sources.extend(self._repo["distro"].main_sources)
        sources.extend(self._repo["distro"].child_sources)

        # remove all exisiting sources
        for source in self._repo["distro"].source_code_sources:
            self._sourceslist.remove(source)
            
        if widget.get_active():
            for source in sources:
                self._sourceslist.add("deb-src",
                                     source.uri,
                                     source.dist,
                                     source.comps,
                                     "Added by mint-software-properties-tool",
                                     self._sourceslist.list.index(source)+1,
                                     source.file)
            for source in self._repo["distro"].cdrom_sources:
                self._sourceslist.add("deb-src",
                                     self._repo["distro"].source_template.base_uri,
                                     self._repo["distro"].source_template.name,
                                     source.comps,
                                     "Added by mint-software-properties-tool",
                                     self._sourceslist.list.index(source)+1,
                                     source.file)
        else:
            pass
        self._application.save_sourceslist()

class ServerSelectionComboBox(gtk.ComboBox):
    def __init__(self, repo):
        gtk.ComboBox.__init__(self)

class Application(object):
    def __init__(self, options):
        self._cli_options = options
        glade_file = self._get_resource_file("/usr/share/mint-software-properties-tool/mint-software-properties-tool.glade")
            
        builder = gtk.Builder()
        builder.add_from_file(glade_file)
        self._main_window = builder.get_object("main_window")
        self._notebook = builder.get_object("notebook")
        self._official_repositories_box = builder.get_object("official_repositories_box")
        
        self.sourceslist = SourcesList()
        
        self._load_official_repositories()
        self._build_official_repositories_tab()
        
        self._tab_buttons = [
            builder.get_object("toggle_official_repos"),
            builder.get_object("toggle_ppas"),
            builder.get_object("toggle_additional_repos"),
            builder.get_object("toggle_authentication_keys")
        ]
        
        self._main_window.connect("delete_event", lambda w,e: gtk.main_quit())
        for i in range(len(self._tab_buttons)):
            self._tab_buttons[i].connect("clicked", self._on_tab_button_clicked, i)
            self._tab_buttons[i].set_active(False)
    
    def save_sourceslist(self):
        self.sourceslist.backup(".save")
        self.sourceslist.save()
    
    def _build_official_repositories_tab(self):
        first_repo = True
        for repo in self._official_repositories:
            if first_repo:
                first_repo = False
            else:
                self._official_repositories_box.pack_start(gtk.HSeparator(), False, False)
            frame = gtk.Frame()
            label = gtk.Label()
            label.set_markup("<b>%s</b>" % repo["section"])
            frame.set_label_widget(label)
            self._official_repositories_box.pack_start(frame, False, False)
            frame.set_shadow_type(gtk.SHADOW_NONE)
            alignment = gtk.Alignment()
            frame.add(alignment)
            alignment.set_padding(0, 0, 12, 0)
            alignment.set(0.5, 0.5, 1, 1)
            
            vbox = gtk.VBox()
            vbox.set_spacing(10)
            alignment.add(vbox)
            components_table = gtk.Table()
            vbox.pack_start(components_table, True, True)
            nb_components = 0
            for i in range(len(repo["distro"].source_template.components)):
                component = repo["distro"].source_template.components[i]
                if not component.name in repo["advanced_components"]:
                    cb = ComponentToggleCheckBox(self, repo, component)
                    components_table.attach(cb, 0, 1, nb_components, nb_components + 1, xoptions = gtk.FILL | gtk.EXPAND, yoptions = 0)
                    nb_components += 1
            cb = SourceCodeToggleCheckBox(self, self.sourceslist, repo)
            components_table.attach(cb, 0, 1, nb_components, nb_components + 1, xoptions = gtk.FILL | gtk.EXPAND, yoptions = 0)
            nb_components += 1
            if repo["advanced_components"]:
                if nb_components > 0:
                    line = nb_components - 1
                else:
                    line = nb_components
                    components_table.attach(gtk.Label(), 0, 1, line, line + 1, xoptions = gtk.FILL | gtk.EXPAND, yoptions = 0)
                advanced_components_button = gtk.Button(_("Advanced options"))
                components_table.attach(advanced_components_button, 1, 2, line, line + 1, xoptions = 0, yoptions = 0)
            
            server_hbox = gtk.HBox()
            server_hbox.set_spacing(5)
            vbox.pack_start(server_hbox, False, False)
            label = gtk.Label(_("Server:"))
            server_hbox.pack_start(label, False, False)
            server_hbox.pack_start(ServerSelectionComboBox(repo), True, True)
    
    def _load_official_repositories(self):
        config_parser = ConfigParser.RawConfigParser()
        config_parser.read(self._get_resource_file("/etc/mint-software-properties-tool/repositories.conf"))
        self._official_repositories = []
        self.sourceslist.refresh()
        for section in config_parser.sections():
            repo = {'section': section, "advanced_components": ""}
            for param in config_parser.options(section):
                repo[param] = config_parser.get(section, param)
            if "mirrors_list" in repo:
                template = aptsources.distinfo.Template()
                template.name = repo["codename"]
                template.match_name = "^" + repo["codename"] + "$"
                template.base_uri = repo["baseuri"]
                template.type = "deb"
                template.components = [aptsources.distinfo.Component(c.rstrip().lstrip()) for c in repo["components"].split(",") if c.rstrip().lstrip() != ""]
                template.match_uri = repo["matchuri"]
                template.distribution = repo["distributionid"]
                template.mirror_set = {}
                f = open(repo["mirrors_list"])
                mirrors = f.read().splitlines()
                f.close()
                for mirror in mirrors:
                    url_parts = urlparse.urlparse(mirror)
                    if "path" in repo:
                        path = repo["path"]
                    else:
                        path = url_parts.path
                    template.mirror_set[url_parts.netloc] = aptsources.distinfo.Mirror(url_parts.scheme, url_parts.netloc, path)
                self.sourceslist.matcher.templates.append(template)
                self.sourceslist.refresh()
            distro = aptsources.distro.get_distro(repo["distributionid"], repo["codename"], "foo", repo["release"])
            distro.get_sources(self.sourceslist)
            repo["distro"] = distro
            repo["advanced_components"] = [c.rstrip().lstrip() for c in repo["advanced_components"].split(",") if c.rstrip().lstrip() != ""]
            self._official_repositories.append(repo)
    
    def _get_resource_file(self, filename):
        if self._cli_options.dev_mode:
            base_path = os.path.join(os.getcwd(), "files")
        else:
            base_path = ""
        return base_path + filename
        
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
        self._main_window.show_all()
        gtk.main()
