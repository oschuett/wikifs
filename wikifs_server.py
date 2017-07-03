#!/usr/bin/env python3

import os
import json
import subprocess
from base64 import b64encode, b64decode

from functools import wraps
from flask import Flask, render_template, json, request, abort, redirect, url_for, flash, session
app = Flask(__name__)

#===============================================================================
def to_full_path(path):
    if path.startswith("/"):
        path = path[1:]
    return os.path.join(app.config['WIKIFS_ROOT'], path)

#===============================================================================
def token_required(func):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        global current_user, userdb
        current_user = None
        if "Authorization" not in request.headers:
            return abort(401)
        token = request.headers["Authorization"]

        if token not in userdb:
            reload_userdb() # reload userdb and check again.
            if token not in userdb:
                return abort(401)

        current_user = userdb[token]
        return func(*args, **kwargs)
    return decorated_view

#===============================================================================
def reload_userdb():
    global userdb
    fn = os.path.join(app.config['WIKIFS_ROOT'], "users.db")
    print("reloading user database from: "+fn)
    userdb = {}
    for line in open(fn).readlines():
        line = line.strip()
        if not line: co
        if not line or line[0]=="#": continue
        username, auth_token = line.split()
        userdb[auth_token] = username

#===============================================================================
@app.route('/wikifs/getattr')
@token_required
def api_getattr():
    path = request.args["path"]
    full_path = to_full_path(path)

    if not os.path.exists(full_path):
        #abort(404)
        return ("{}")

    st = os.lstat(full_path)
    answer = dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
            'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))

    if user_has_lock(path):
        answer['st_mode'] = 0o100664 # '-rw-rw-r--'
    else:
        answer['st_mode'] = 0o100444 # '-r--r--r--'

    return json.dumps(answer)

#===============================================================================
@app.route('/wikifs/aquire_lock')
@token_required
def api_aquire_lock():
    path = request.args["path"]

    if user_has_lock(path):
        # TODO update lock timestamp
        return json.dumps({'new_grant': False})

    # create new lock
    aquire_lock(path)
    return json.dumps({'new_grant': True})


#===============================================================================
@app.route('/wikifs/release_lock')
@token_required
def api_release_lock():
    path = request.args["path"]
    if user_has_lock(path):
        git_commit_file(path)
        release_lock(path)

    return("Ok")

#===============================================================================
@app.route('/wikifs/check_lock')
@token_required
def api_check_lock():
    path = request.args["path"]
    answer = {'lock_is_yours': user_has_lock(path)}
    return json.dumps(answer)

#===============================================================================
@app.route('/wikifs/remove')
@token_required
def api_remove():
    path = request.args["path"]
    try:
        aquire_lock(path)
        git_remove_file(path)
    finally:
        release_lock(path)

    return("Ok")

#===============================================================================
@app.route('/wikifs/rename', methods=['POST'])
@token_required
def api_rename():
    old_path = request.args["path"]
    new_path = request.get_data().decode("utf-8")
    print("new_path: "+new_path)

    try:
        aquire_lock(old_path)
        aquire_lock(new_path)

        # commit changes before renameing
        git_commit_file(old_path)
        git_commit_file(new_path)

        git_rename_file(old_path, new_path)

    finally:
        release_lock(old_path)
        release_lock(new_path)

    return("Ok")

#===============================================================================
@app.route('/wikifs/readdir')
@token_required
def api_readdir():
    path = request.args["path"]
    full_path = to_full_path(path)
    if os.path.exists(full_path):
        entries = [fn for fn in os.listdir(full_path) if fn[0]=="_"]
    else:
        entries = []
    return(json.dumps(entries))

#===============================================================================
@app.route('/wikifs/download')
@token_required
def api_download():
    path = request.args["path"]
    full_path = to_full_path(path)
    if not os.path.exists(full_path):
        abort(404)

    content = open(full_path, 'rb').read()
    answer = b64encode(content)
    return(answer)


#===============================================================================
@app.route('/wikifs/upload', methods=['POST'])
@token_required
def api_upload():
    path = request.args["path"]
    full_path = to_full_path(path)

    assert user_has_lock(path)

    # write file
    content = b64decode(request.get_data())
    f = open(full_path, "wb")
    f.write(content)
    f.close()

    return("Ok")

#===============================================================================
def to_lock_path(path):
    full_path = to_full_path(path)
    dn = os.path.dirname(full_path)
    bn = os.path.basename(full_path)
    assert(bn[0]=="_") # make sure it's a wiki path
    lock_fn = dn + "/LOCK_" + bn[1:]
    return lock_fn

#===============================================================================
def user_has_lock(path):
    lock_path = to_lock_path(path)
    if not os.path.exists(lock_path):
        return False
    lock_user = open(lock_path).read()
    return lock_user == current_user

#===============================================================================
def aquire_lock(path):
    if user_has_lock(path):
        return

    lock_path = to_lock_path(path)

    # lock available?
    assert not os.path.exists(lock_path)

    # create directory if it does not exist
    d = os.path.dirname(lock_path)
    if not os.path.exists(d):
        os.makedirs(d)

    # create new lock
    f = open(lock_path, "w")
    f.write(current_user)
    f.close()

#===============================================================================
def release_lock(path):
    if user_has_lock(path):
        lock_path = to_lock_path(path)
        os.remove(lock_path)

#===============================================================================
def git_commit_file(path):
    full_path = to_full_path(path)
    if not os.path.exists(full_path):
        return

    # make a git commit, if needed
    commit_msg = None
    devnull = open(os.devnull, 'w')
    cmd = ["git", "ls-files", "--error-unmatch", full_path]
    file_tracked = subprocess.call(cmd, stdout=devnull, stderr=devnull, cwd=app.config['WIKIFS_ROOT'])
    if file_tracked != 0:
        commit_msg = "New "+path
    else:
        cmd = ["git", "diff-index", "--quiet", "HEAD", full_path]
        has_changed = subprocess.call(cmd, cwd=app.config['WIKIFS_ROOT'])
        if has_changed != 0:
            commit_msg = "Edit "+path

    if commit_msg:
        subprocess.check_call(["git", "add", full_path], cwd=app.config['WIKIFS_ROOT'])
        subprocess.check_call(["git", "commit", "-m", commit_msg], cwd=app.config['WIKIFS_ROOT'])

#===============================================================================
def git_remove_file(path):
    commit_msg = "Remove "+path
    full_path = to_full_path(path)
    subprocess.check_call(["git", "rm", "-f", full_path], cwd=app.config['WIKIFS_ROOT'])
    subprocess.check_call(["git", "commit", "-m", commit_msg], cwd=app.config['WIKIFS_ROOT'])

#===============================================================================
def git_rename_file(old_path, new_path):
    commit_msg = "Rename "+old_path+" -> "+new_path
    old_full_path = to_full_path(old_path)
    new_full_path = to_full_path(new_path)
    subprocess.check_call(["git", "mv", "-f", old_full_path, new_full_path], cwd=app.config['WIKIFS_ROOT'])
    subprocess.check_call(["git", "commit", "-m", commit_msg], cwd=app.config['WIKIFS_ROOT'])

#===============================================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: app <wikifs_root>")
        sys.exit(255)

    app.config['WIKIFS_ROOT'] = os.path.realpath(sys.argv[1])
    reload_userdb()
    app.run(port=5002, debug=True)

#EOF
