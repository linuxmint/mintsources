#! /usr/bin/python
# -*- coding=utf-8 -*-

import os
import gtk
import urlparse
import aptsources.distro
import aptsources.distinfo
from aptsources.sourceslist import SourcesList
import optparse
import gettext

gettext.install("mintsources", "/usr/share/linuxmint/locale")

# i18n for menu item
menuName = _("Software Sources")
menuComment = _("Configure the sources for installable software and updates")

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
    
    def refresh(self):
        selected_iter = None
        for name, url, active in self._repo["distro"].get_server_list():
            tree_iter = self._model.append((name, url, active, False))
            if active:
                selected_iter = tree_iter
        self._model.append((None, None, None, True))
        self._model.append((_("Other..."), None, None, False))
        
        if selected_iter is not None:
            self.set_active_iter(selected_iter)

class Application(object):
    def __init__(self):
        glade_file = "/usr/lib/linuxmint/mintSources/mintSources.glade"
            
        builder = gtk.Builder()
        builder.add_from_file(glade_file)
        self._main_window = builder.get_object("main_window")
        self._notebook = builder.get_object("notebook")
        self._official_repositories_box = builder.get_object("official_repositories_box")
        self._source_code_cb = builder.get_object("source_code_cb")
        
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
        
        self._source_code_cb.connect("toggled", self._on_source_code_cb_toggled)
        builder.get_object("menu_item_close").connect("activate", lambda w: gtk.main_quit())
    
    def _on_source_code_cb_toggled(self, widget):
        for repo in self._official_repositories:
            sources = []
            sources.extend(repo["distro"].main_sources)
            sources.extend(repo["distro"].child_sources)
            
            for source in repo["distro"].source_code_sources:
                if source in self.sourceslist.list:
                    self.sourceslist.remove(source)
            
            if widget.get_active():
                for source in sources:
                    self.sourceslist.add("deb-src",
                                         source.uri,
                                         source.dist,
                                         source.comps,
                                         _("Added by Software Sources"),
                                         self.sourceslist.list.index(source)+1,
                                         source.file)
                for source in repo["distro"].cdrom_sources:
                    self.sourceslist.add("deb-src",
                                         repo["distro"].source_template.base_uri,
                                         repo["distro"].source_template.name,
                                         source.comps,
                                         _("Added by Software Sources"),
                                         self.sourceslist.list.index(source)+1,
                                         source.file)
        
        self.save_sourceslist()
    
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
            server_hbox.pack_start(ServerSelectionComboBox(self, repo), True, True)
    
    def _load_official_repositories(self):
        config_parser = ConfigParser.RawConfigParser()
        config_parser.read(self._get_resource_file("/usr/share/mintsources/repositories.conf"))
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
                template.children = []
                child_index = 1
                while "child_%d_codename"%child_index in repo:
                    child_codename = repo["child_%d_codename"%child_index]
                    child_components = [aptsources.distinfo.Component(c.rstrip().lstrip()) for c in repo["child_%d_components"%child_index].split(",") if c.rstrip().lstrip() != ""]
                    child_path = repo["child_%d_path"%child_index]
                    child_template = aptsources.distinfo.Template()
                    child_template.name = child_codename
                    child_template.match_name = "^" + child_codename + "$"
                    child_template.base_uri = repo["baseuri"]
                    child_template.type = "deb"
                    child_template.components = child_components
                    child_template.match_uri = repo["matchuri"]
                    child_template.distribution = repo["distributionid"]
                    child_template.mirror_set = {}
                    f = open(repo["mirrors_list"])
                    mirrors = f.read().splitlines()
                    f.close()
                    for mirror in mirrors:
                        url_parts = urlparse.urlparse(mirror)
                        child_template.mirror_set[url_parts.netloc] = aptsources.distinfo.Mirror(url_parts.scheme, url_parts.netloc, child_path)
                    child_template.parents = [template]
                    child_template.child = True
                    template.children.append(child_template)
                    self.sourceslist.matcher.templates.append(child_template)
                    child_index += 1
                self.sourceslist.refresh()
            distro = aptsources.distro.get_distro(repo["distributionid"], repo["codename"], "foo", repo["release"])
            distro.get_sources(self.sourceslist)
            if len(distro.source_code_sources) > 0:
                self._source_code_cb.set_active(True)
            repo["distro"] = distro
            repo["advanced_components"] = [c.rstrip().lstrip() for c in repo["advanced_components"].split(",") if c.rstrip().lstrip() != ""]
            self._official_repositories.append(repo)
        
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


if __name__ == "__main__":
    if os.getuid() != 0:
        os.execvp("gksu", ("", " ".join(sys.argv)))
    else:
        Application().run()
