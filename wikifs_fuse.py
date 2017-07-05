#!/usr/bin/env python3

import os
import sys
import stat
import errno
import os.path

import logging
from threading import Lock
from fuse import FUSE, FuseOSError, Operations, LoggingMixIn

import requests
from base64 import b64decode, b64encode

import configparser
import tempfile
import shutil


#===============================================================================
class WikiFS(LoggingMixIn, Operations):
    def __init__(self, local_root, server_url, auth_token):
        self.local_root = local_root
        assert(not server_url.endswith("/"))
        self.server_url = server_url
        self.auth_token = auth_token
        self.rwlock = Lock()
        self.mirror = {}

    #===========================================================================
    def _full_path(self, path):
        if path.startswith("/"):
            path = path[1:]
        return os.path.join(self.local_root, path)

    #===========================================================================
    def _is_wiki(self, path):
        if path[-1] == "/":
            return False # a directory

        assert(path[0]=="/")
        parts = path[1:].split("/")

        # ignore hidden files and directories
        if any([p[0]=="." for p in parts]):
            return False

        # ignore temporary files
        if parts[-1][-1] == "~":
            return False

        # only accept files starting with "_"
        return parts[-1][0] == "_"

    #===========================================================================
    def _request(self, action, path, json=None):
        print("request: "+action)
        url = self.server_url + "/" + action
        headers = {"Authorization": self.auth_token}
        if json==None:
            resp = requests.get(url, params={'path':path}, headers=headers)
        else:
            resp = requests.post(url, params={'path':path}, headers=headers, json=json)

        if resp.status_code == 404:
            raise FuseOSError(errno.ENOENT) # No such file or directory

        #raise FuseOSError(errno.EACCES) # Permission denied
        #raise FuseOSError(errno.EBUSY) # Device or resource busy
        #raise FuseOSError(errno.EIO) # I/O error
        #raise FuseOSError(errno.EREMOTEIO) # Remote I/O error

        resp.raise_for_status()
        # TODO maybe raise more meaning full error FuseOSError(errno.ENOENT)
        # TODO make error message available as log file, e.g. "/.wikifs.log"
        return resp.json()

    #===========================================================================
    def _mirror_path(self, path):
        if not self._is_wiki(path):
            return self._full_path(path)

        with self.rwlock:
            # create new mirror if needed
            if path not in self.mirror.keys():
                tmp_f, tmp_fn = tempfile.mkstemp()
                os.close(tmp_f)
                print("new mirror "+tmp_fn + "  -> "+path)
                self.mirror[path] = {'tmp_fn':tmp_fn, 'mtime':None, 'refs':0}

            # update mirror
            entry = self.mirror[path]
            tmp_fn = entry['tmp_fn']
            answer = self._request("download", path)
            if entry['mtime']==None or answer['lock_is_yours']==False:
                # update file content and mode
                os.chmod(tmp_fn, 0o100664) # '-rw-rw-r--'
                content = b64decode(answer['content'])
                open(tmp_fn, "wb").write(content)
                os.chmod(tmp_fn, answer['st_mode'])
                entry['mtime'] = os.lstat(tmp_fn).st_mtime

            entry['refs'] += 1
            return tmp_fn

    #===========================================================================
    def _release_mirror(self, path):
        if not self._is_wiki(path):
            return

        with self.rwlock:
            # check for changes
            entry = self.mirror[path]
            tmp_fn = entry['tmp_fn']
            st = os.lstat(tmp_fn)
            is_dirty = st.st_mtime != entry['mtime']

            # upload file content, if needed
            if is_dirty:
                content = open(tmp_fn, "rb").read()
                self._request("upload", path, json={"content":b64encode(content)})
                entry['mtime'] = os.lstat(tmp_fn).st_mtime
                # The server may ignore the update.
                # This will get corrected upon the next _mirror_path() call.

            # remove usused mirror
            entry['refs'] -= 1
            if entry['refs'] == 0:
                self.mirror.pop(path)
                os.remove(entry['tmp_fn'])

    #===========================================================================
    #https://www.cs.hmc.edu/~geoff/classes/hmc.cs135.201001/homework/fuse/fuse_doc.html
    # non-mandatory routines which we do not implement
    chown = None
    mknod = None
    readlink = None
    symlink = None
    link = None
    statfs = None
    utimens = None
    getxattr = None
    listxattr = None

    #===========================================================================
    def access(self, path, mode):
        try:
            mirror_path = self._mirror_path(path)
            os.access(mirror_path, mode)
        finally:
            self._release_mirror(path)

    #===========================================================================
    def readdir(self, path, fh):
        entries = set(['.', '..'])
        full_path = self._full_path(path)
        answer = self._request("readdir", path)
        entries.update(answer)

        # create directory locally
        if not os.path.exists(full_path):
            os.makedirs(full_path)

        entries.update(os.listdir(full_path))
        return entries

    #===========================================================================
    def getattr(self, path, fh=None):
        #TODO handle directories separately, would also simplify _is_wiki
        #TODO overwrite uid and gid
        #TODO handle directories which only exist on the server
        if self._is_wiki(path):
            return self._request("getattr", path)

        full_path = self._full_path(path)
        st = os.lstat(full_path)
        return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
            'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))


    #===========================================================================
    def create(self, path, mode):
        if self._is_wiki(path):
            #TODO ensure check that if mode request write permissions
            #TODO currently uses two http calls
            self._request("create", path)
            mirror_path = self._mirror_path(path)
            return os.open(mirror_path, os.O_WRONLY | os.O_TRUNC)
        else:
            full_path = self._full_path(path)
            return os.open(full_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)

    #===========================================================================
    def chmod(self, path, mode):
        if self._is_wiki(path):
            self._request("chmod", path, json={"mode":mode})
        else:
            full_path = self._full_path(path)
            return os.chmod(full_path, mode)

    #===========================================================================
    def open(self, path, flags):
        mirror_path = self._mirror_path(path)
        return os.open(mirror_path, flags)

    #===========================================================================
    def truncate(self, path, length, fh=None):
        mirror_path = self._mirror_path(path)
        with open(mirror_path, 'r+') as f:
            f.truncate(length)
        self._release_mirror(path)

    #===========================================================================
    def release(self, path, fh):
        os.close(fh)
        self._release_mirror(path)

    #===========================================================================
    def rename(self, old_path, new_path):
        old_is_wiki = self._is_wiki(old_path)
        new_is_wiki = self._is_wiki(new_path)

        assert old_path not in self.mirror.keys()
        assert new_path not in self.mirror.keys()

        if old_is_wiki and new_is_wiki:
            # rename on server
            self._request("rename", old_path, json={"new_path":new_path})

        elif not old_is_wiki and not new_is_wiki:
           # just a local move
           old_full_path = self._full_path(old_path)
           new_full_path = self._full_path(new_path)
           os.rename(old_full_path, new_full_path)

        else:
            # move between wiki and local

            # first ensure that new_path can be created
            mode = self.getattr(old_path)['st_mode']
            fh = self.create(new_path, mode)
            self.release(new_path, fh)

            # then make new_path writable and copy content
            self.chmod(new_path, 0o100664) # '-rw-rw-r--'
            mirror_old = self._mirror_path(old_path)
            mirror_new = self._mirror_path(new_path)
            shutil.copyfile(mirror_old, mirror_new)
            self._release_mirror(old_path)
            self._release_mirror(new_path)

            # then restore mode and remove old_path
            self.chmod(new_path, mode)
            self.unlink(old_path)

    #===========================================================================
    def mkdir(self, path, mode):
        #TODO: assert that directory has valid name
        full_path = self._full_path(path)
        return os.mkdir(full_path, mode)

    #===========================================================================
    def rmdir(self, path):
        full_path = self._full_path(path)
        return os.rmdir(full_path)

    #===========================================================================
    def unlink(self, path):
        if self._is_wiki(path):
            assert path not in self.mirror.keys()
            self._request("remove", path)
        else:
            full_path = self._full_path(path)
            os.unlink(full_path)

    #===========================================================================
    def read(self, path, size, offset, fh):
        with self.rwlock:
            os.lseek(fh, offset, 0)
            return os.read(fh, size)

    #===========================================================================
    def write(self, path, data, offset, fh):
        with self.rwlock:
            os.lseek(fh, offset, 0)
            return os.write(fh, data)

    #===========================================================================
    def flush(self, path, fh):
        return os.fsync(fh)

    #===========================================================================
    def fsync(self, path, datasync, fh):
        if datasync != 0:
          return os.fdatasync(fh)
        else:
          return os.fsync(fh)

#===============================================================================
if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('usage: %s <config_file> <mountpoint>' % sys.argv[0])
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(sys.argv[1])
    local_root = config['wikifs']["local_root"]
    server_url = config['wikifs']["server_url"]
    auth_token = config['wikifs']["auth_token"]
    mnt_point = sys.argv[2]

    logging.basicConfig(level=logging.DEBUG)
    print
    fs = WikiFS(local_root=local_root, server_url=server_url, auth_token=auth_token)
    print(mnt_point)
    fuse = FUSE(fs, mnt_point, foreground=True)

#EOF
