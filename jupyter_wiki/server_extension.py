# -*- coding: utf-8 -*-

import os
import itertools
from notebook.utils import url_path_join
from notebook.base.handlers import IPythonHandler, FilesRedirectHandler, path_regex
from tornado import web
import json
import xattr

class AppmodeHandler(IPythonHandler):
    #===========================================================================
    @web.authenticated
    def post(self):
        """get renders the notebook template if a name is given, or 
        redirects to the '/files/' handler if the name is not given."""

        #path = path.strip('/')
        #self.log.info('Wiki get: %s', path)
        data = self.get_json_body()
        action = data['action']
        path = data['path']
        cm = self.contents_manager
        full_path = cm._get_os_path(path)

        try:
            if action == "aquire_lock":
                os.chmod(full_path, 0o100664) # '-rw-rw-r--'
                self.finish(json.dumps({"success": True, "message": ""}))
            elif action == "release_lock":
                os.chmod(full_path, 0o100444) # '-r--r--r--'
                self.finish(json.dumps({"success": True, "message": ""}))
            else:
                err_msg = "Unknown action "+action
                self.finish(json.dumps({"success": False, "message": err_msg}))
            return
        except:
            pass

        # Ok, something went wrong let's try to retrieve error message
        try:
            err_msg = xattr.getxattr(full_path, "wikifs_error")
        except:
            err_msg = "Something went wrong"

        self.finish(json.dumps({"success": False, "message": err_msg}))
#===============================================================================    
def load_jupyter_server_extension(nbapp):
    #tmpl_dir = os.path.dirname(__file__)
    # does not work, because init_webapp() happens before init_server_extensions()
    #nbapp.extra_template_paths.append(tmpl_dir) # dows 

    # slight violation of Demeter's Laws
    #nbapp.web_app.settings['jinja2_env'].loader.searchpath.append(tmpl_dir)

    web_app = nbapp.web_app
    host_pattern = '.*$'
    route_pattern = url_path_join(web_app.settings['base_url'], r'/api/wiki')
    web_app.add_handlers(host_pattern, [(route_pattern, AppmodeHandler)])
    nbapp.log.info("Wiki server extension loaded.")

#EOF