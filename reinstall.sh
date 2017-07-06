#!/bin/bash

jupyter nbextension uninstall --sys-prefix --py jupyter_wiki
jupyter nbextension install   --sys-prefix --py --symlink jupyter_wiki
jupyter nbextension enable    --sys-prefix --py jupyter_wiki
jupyter nbextension list

jupyter serverextension disable --sys-prefix --py jupyter_wiki
jupyter serverextension enable  --sys-prefix --py jupyter_wiki
jupyter serverextension list

#EOF
