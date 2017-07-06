// toggle display of all code cells' inputs

define([
    'jquery',
    'base/js/namespace',
    'base/js/dialog',
    'base/js/events',
    'base/js/utils',
    'require',
], function(
    $,
    Jupyter,
    dialog,
    events,
    utils,
    require
) {
    "use strict";

    //==========================================================================
    function is_wiki_path(path) {
        var parts = path.split("/");
        var basename = parts[parts.length -1];
        return(basename.charAt(0)=='_');
    }

    //==========================================================================
    function setup_notebook() {
        console.log("jupyterwiki: setup notebook");
        //alert(Jupyter.notebook.notebook_path);

        // remove old buttons
        var group_name = "jupyterwiki_btn_group";
        $("#"+group_name).remove();

        var is_wiki = is_wiki_path(Jupyter.notebook.notebook_path);
        if(!is_wiki)
            return // nothing todo

        if(!Jupyter.notebook.writable){
            Jupyter.toolbar.add_buttons_group(["jupyter_wiki:edit"], group_name);
             // disable code editing
             //TODO: make really read only, block create/remove of cells
             //$('.CodeMirror').each(function() {
             //    this.CodeMirror.setOption('readOnly', "nocursor");
             //});
        }else{
            Jupyter.toolbar.add_buttons_group(["jupyter_wiki:publish"], group_name);
        }

        // Rules:
        //  - directories musst not have dots in their name
        //  - files matching _* can only be created through the wiki-system
        //  - file names must have a dot in their name

        // case 1: wiki file
        //   -> edit page (creates a draft file, or continues with existing draft file)
        //      draft file names are Draft1_name.ipynb
        // case 2: draft file in wiki dir
        //   -> publish on wiki
        // case 3: non wiki file
        //   -> publish on wiki (renames file to  "_"+old_name)

    };

    //==========================================================================
    var on_wiki_edit = function () {
        perform_action("aquire_lock");
    };

    //==========================================================================
    var on_wiki_publish = function () {
        perform_action("release_lock");
    };

    //==========================================================================
    var perform_action = function (action) {
        var data = {'action':action, 'path': Jupyter.notebook.notebook_path};

        var settings = {
            processData : false,
            type : "POST",
            dataType: "json",
            data : JSON.stringify(data),
            contentType: 'application/json',
        };

        var url = Jupyter.notebook.base_url + "api/wiki";

        var future = utils.promising_ajax(url, settings);
        future.then(
            function (data) {
                if(data['success']){
                    location.reload();
                }else{
                    show_message("Error", data['message']);
                }
            },
            function(error) {
                show_message("Error", error);
            }
        );
    };

    //==========================================================================
    var show_message = function (title, message) {
        dialog.modal({
            notebook: Jupyter.notebook,
            keyboard_manager: Jupyter.notebook.keyboard_manager,
            title : title,
            body : $("<p/>").text(message),
            buttons : {
                "OK" : {}
            }
        });
    };

    //==========================================================================
    var load_ipython_extension = function() {

        var prefix = 'jupyter_wiki';
        var action_edit = {
            icon: 'fa-pencil-square-o', // a font-awesome class used on buttons, etc
            help    : 'Edit notebook on wiki',
            //help_index : 'zz',
            handler : on_wiki_edit
        };
        Jupyter.actions.register(action_edit, "edit", "jupyter_wiki");

        var action_publish = {
            icon: 'fa-cloud-upload', // a font-awesome class used on buttons, etc
            help    : 'Publish notebook to wiki',
            //help_index : 'zz',
            handler : on_wiki_publish
        };
        Jupyter.actions.register(action_publish, "publish", "jupyter_wiki");

        events.one('notebook_renamed.Notebook', setup_notebook);

        // init stuff once notebook is loaded
        if (Jupyter.notebook && Jupyter.notebook._fully_loaded) {
            console.log("Wiki: notebook already loaded.");
            setup_notebook();
        }else{
            console.log("Wiki: waiting for notebook to load.");
            events.one('notebook_loaded.Notebook', setup_notebook);
        }

    //    //TODO insert before #help_menu
    //    var $menubar = $('.navbar-nav');
    //    var $wikimenu_container = $('<li />').addClass("dropdown").appendTo($menubar);
    //    $('<a />').attr('href', '#').addClass("dropdown-toggle").attr('data-toggle','dropdown').html("Wiki").appendTo($wikimenu_container);
    //    var $wikimenu = $('<ul />').addClass("dropdown-menu").appendTo($wikimenu_container);
    //
    //    var menu_item = $wikimenu.append($('<li/>').append($('<a/>').attr('href', '#')
    //            .attr('id','jupyter_wiki_bar')
    //            .html('some entry')
    //            .attr('title', 'Jupyter_contrib_nbextensions documentation')));
    //
    //    var menu_item = $wikimenu.append($('<li/>').append($('<a/>').attr('href', '#')
    //            .attr('id','jupyter_wiki_edit')
    //            .html('Edit this ')
    //            .attr('title', 'Jupyter_contrib_nbextensions documentation')));
    //
    };

    //==========================================================================
    return {
        load_ipython_extension : load_ipython_extension
    };
});