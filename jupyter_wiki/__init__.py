# -*- coding: utf-8 -*-

# Jupyter Extension points

def _jupyter_nbextension_paths():
    return [dict(
        section="notebook",
        src="static",
        dest="jupyter_wiki",
        require="jupyter_wiki/main")]

def _jupyter_server_extension_paths():
    return [{"module":"jupyter_wiki.server_extension"}]

#EOF