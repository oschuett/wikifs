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
    fn = os.path.join(app.config['WIKIFS_ROOT'], "userdb.json")
    print("reloading user database from: "+fn)
    userdb = json.load(open(fn))

#===============================================================================
@app.route('/wikifs/getattr')
@token_required
def api_getattr():
    path = request.args["path"]
    full_path = to_full_path(path)

    if not os.path.exists(full_path):
        abort(404)

    st = os.lstat(full_path)
    answer = dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
            'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))

    if user_has_lock(path):
        answer['st_mode'] = 0o100664 # '-rw-rw-r--'
    else:
        answer['st_mode'] = 0o100444 # '-r--r--r--'

    return json.dumps(answer)

#===============================================================================
@app.route('/wikifs/chmod', methods=['POST'])
@token_required
def api_chmod():
    path = request.args["path"]
    mode = request.get_json()['mode']
    want_lock = bool(mode & 0o000222)  # '?-w--w--w-'
    if want_lock:
        aquire_lock(path)
    elif user_has_lock(path):
        git_commit_file(path)
        release_lock(path)

    return json.dumps({})

    #TODO handle executable bit properly (might require a commit)

#===============================================================================
@app.route('/wikifs/create')
@token_required
def api_create():
    path = request.args["path"]

    full_path = to_full_path(path)
    if os.path.exists(full_path):
        abort(409, "File %s already exists."%path) # Conflict

    aquire_lock(path)
    open(full_path, "w")

    return json.dumps({})

#===============================================================================
@app.route('/wikifs/remove')
@token_required
def api_remove():
    path = request.args["path"]

    full_path = to_full_path(path)
    if not os.path.exists(full_path):
        abort(404)

    try:
        aquire_lock(path)
        git_remove_file(path)
    finally:
        release_lock(path)

    return json.dumps({})

#===============================================================================
@app.route('/wikifs/rename', methods=['POST'])
@token_required
def api_rename():
    old_path = request.args["path"]
    new_path = request.get_json()['new_path']
    print("new_path: "+new_path)

    #try:
    had_lock = user_has_lock(old_path)
    aquire_lock(old_path)
    aquire_lock(new_path)

    git_rename_file(old_path, new_path)

    release_lock(old_path)
    if not had_lock:
        release_lock(new_path)

    #finally:
    #    release_lock(old_path)
    #    release_lock(new_path)

    return json.dumps({})

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
    answer = {'content': b64encode(content)}
    answer['lock_is_yours'] = user_has_lock(path)
    if user_has_lock(path):
        answer['st_mode'] = 0o100664 # '-rw-rw-r--'
    else:
        answer['st_mode'] = 0o100444 # '-r--r--r--'

    return(json.dumps(answer))


#===============================================================================
@app.route('/wikifs/upload', methods=['POST'])
@token_required
def api_upload():
    path = request.args["path"]
    full_path = to_full_path(path)

    if not user_has_lock(path):
        abort(403, "File %s not locked"%path) # Forbidden

    # write file
    content = b64decode(request.get_json()['content'])
    f = open(full_path, "wb")
    f.write(content)
    f.close()

    #print("Wrote: "+str(content))

    return(json.dumps({}))

#===============================================================================
def to_lock_path(path):
    full_path = to_full_path(path)
    dn = os.path.dirname(full_path)
    bn = os.path.basename(full_path)
    assert bn[0] == "_" # make sure it's a wiki path
    lock_fn = dn + "/LOCK_" + bn[1:]
    return lock_fn

#===============================================================================
def user_has_lock(path):
    lock_path = to_lock_path(path)
    if not os.path.exists(lock_path):
        return False
    lock_user = open(lock_path).read().strip()
    return lock_user == current_user['username']

#===============================================================================
def aquire_lock(path):
    if user_has_lock(path):
        return

    lock_path = to_lock_path(path)

    # lock available?
    if os.path.exists(lock_path):
        username = open(lock_path).read().strip()
        abort(410, "File %s already locked by user %s."%(path, username)) # Gone

    # create directory if it does not exist
    d = os.path.dirname(lock_path)
    if not os.path.exists(d):
        os.makedirs(d)

    # create new lock
    f = open(lock_path, "w")
    f.write(current_user['username']+"\n")
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
    if git_file_tracked(path):
        cmd = ["git", "diff-index", "--quiet", "HEAD", full_path]
        has_changed = subprocess.call(cmd, cwd=app.config['WIKIFS_ROOT'])
        if has_changed != 0:
            commit_msg = "Edit "+path
    else:
         commit_msg = "New "+path

    if commit_msg:
        author = '--author="'+current_user['git_author']+'"'
        subprocess.check_call(["git", "add", full_path], cwd=app.config['WIKIFS_ROOT'])
        subprocess.check_call(["git", "commit", author, "-m", commit_msg], cwd=app.config['WIKIFS_ROOT'])

#===============================================================================
def git_file_tracked(path):
    full_path = to_full_path(path)
    cmd = ["git", "ls-files", "--error-unmatch", full_path]
    devnull = open("/dev/null", "w")
    file_tracked = subprocess.call(cmd, stdout=devnull, stderr=devnull, cwd=app.config['WIKIFS_ROOT'])
    return file_tracked == 0

#===============================================================================
def git_remove_file(path):
    full_path = to_full_path(path)
    if git_file_tracked(path):
        commit_msg = "Remove "+path
        author = '--author="'+current_user['git_author']+'"'
        subprocess.check_call(["git", "rm", "-f", full_path], cwd=app.config['WIKIFS_ROOT'])
        subprocess.check_call(["git", "commit", author, "-m", commit_msg], cwd=app.config['WIKIFS_ROOT'])
    else:
        os.remove(full_path)

#===============================================================================
def git_rename_file(old_path, new_path):
    old_full_path = to_full_path(old_path)
    new_full_path = to_full_path(new_path)
    if git_file_tracked(old_path):
        commit_msg = "Rename "+old_path+" -> "+new_path
        author = '--author="'+current_user['git_author']+'"'
        subprocess.check_call(["git", "mv", "-f", old_full_path, new_full_path], cwd=app.config['WIKIFS_ROOT'])
        subprocess.check_call(["git", "commit", author, "-m", commit_msg], cwd=app.config['WIKIFS_ROOT'])
    else:
        print("rename: "+old_full_path + " -> "+new_full_path)
        os.rename(old_full_path, new_full_path)

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
