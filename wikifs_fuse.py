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



#===============================================================================
class WikiFS(LoggingMixIn, Operations):
    def __init__(self, local_root, server_url, auth_token):
        self.local_root = local_root
        assert(not server_url.endswith("/"))
        self.server_url = server_url
        self.auth_token = auth_token
        self.rwlock = Lock()

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

        # ignore hidden files
        if any([p[0]=="." for p in parts]):
            return False

        # ignore temporary files
        if parts[-1][-1] == "~":
            return False

        # only files starting with "_"
        return parts[-1][0] == "_"

    #===========================================================================
    def _request(self, action, path, data=None):
        print("request: "+action)
        url = self.server_url + "/" + action
        headers = {"Authorization": self.auth_token}
        if data==None:
            resp = requests.get(url, params={'path':path}, headers=headers)
        else:
            resp = requests.post(url, params={'path':path}, headers=headers, data=data)
        resp.raise_for_status()
        # TODO maybe raise more meaning full error FuseOSError(errno.ENOENT)
        # TODO make error message available as log file, e.g. "/.wikifs.log"
        return resp

    #===========================================================================
    #https://www.cs.hmc.edu/~geoff/classes/hmc.cs135.201001/homework/fuse/fuse_doc.html
    # non-mandatory routines which we do not implement
    chown = None
    mknod = None
    acccess = None
    readlink = None
    symlink = None
    link = None
    statfs = None
    utimens = None
    getxattr = None
    listxattr = None

    #===========================================================================
    def readdir(self, path, fh):
        entries = set(['.', '..'])
        full_path = self._full_path(path)
        resp = self._request("readdir", path)
        resp.raise_for_status()
        entries.update(resp.json())

        # create directory locally
        if not os.path.exists(full_path):
            os.makedirs(full_path)

        entries.update(os.listdir(full_path))
        return entries

    #===========================================================================
    def getattr(self, path, fh=None):
        #TODO handle directories separately, would also simplify _is_wiki
        #TODO overwrite uid and gid
        if self._is_wiki(path):
            answer = self._request("getattr", path).json()
            if not answer:
                raise FuseOSError(errno.ENOENT)
            return answer

        full_path = self._full_path(path)
        st = os.lstat(full_path)
        return dict((key, getattr(st, key)) for key in ('st_atime', 'st_ctime',
            'st_gid', 'st_mode', 'st_mtime', 'st_nlink', 'st_size', 'st_uid'))


    #===========================================================================
    def create(self, path, mode):
        if self._is_wiki(path):
            self._request("aquire_lock", path)
            self._request("upload", path, data=b64encode(b""))

        full_path = self._full_path(path)
        return os.open(full_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)

    #===========================================================================
    def chmod(self, path, mode):
        full_path = self._full_path(path)

        if not self._is_wiki(path):
            return os.chmod(full_path, mode)

        want_lock = bool(mode & 0o000222)  # '?-w--w--w-'
        if want_lock:
            answer = self._request("aquire_lock", path).json()
            if answer['new_grant']:
                self._download_file(path)
            # make file writable so that it is later uploaded by release()
            os.chmod(full_path, 0o100664) # '-rw-rw-r--'
        else:
            self._request("release_lock", path)
            if os.path.exists(full_path):
                os.chmod(full_path, 0o100444) # '-r--r--r--'

    #===========================================================================
    def open(self, path, flags):
        full_path = self._full_path(path)

        if self._is_wiki(path):
            # explicitly check lock in case it got revoked
            resp = self._request("check_lock", path)
            locked = resp.json()['lock_is_yours']

            if not locked:
                self._download_file(path)
                os.chmod(full_path, 0o100444) # '-r--r--r--'

        return os.open(full_path, flags)

    #===========================================================================
    def release(self, path, fh):
        if self._is_wiki(path):
            full_path = self._full_path(path)
            st = os.lstat(full_path)
            writable = bool(st.st_mode & 0o000222)  # '?-w--w--w-'
            if writable:
                self._upload_file(path)

        return os.close(fh)

    #===========================================================================
    def _download_file(self, path):
       full_path = self._full_path(path)
       if os.path.exists(full_path):
           os.chmod(full_path, 0o100664) # '-rw-rw-r--'
       resp = self._request("download", path)
       content = b64decode(resp.content)
       f = open(full_path, "wb")
       f.write(content)
       f.close()

    #===========================================================================
    def _upload_file(self, path):
        full_path = self._full_path(path)
        content = open(full_path, "rb").read()
        self._request("upload", path, data=b64encode(content))

    #===========================================================================
    def rename(self, old_path, new_path):
        old_is_wiki = self._is_wiki(old_path)
        new_is_wiki = self._is_wiki(new_path)
        old_full_path = self._full_path(old_path)
        new_full_path = self._full_path(new_path)

        if not old_is_wiki and not new_is_wiki:
            # just a local move
            os.rename(old_full_path, new_full_path)

        elif old_is_wiki and new_is_wiki:
            # rename on server
            self._request("rename", old_path, data=new_path.encode("utf-8"))
            if os.path.exists(new_full_path):
                os.remove(new_full_path)
            if os.path.exists(old_full_path):
                os.rename(old_full_path, new_full_path)
                os.chmod(new_full_path, 0o100444) # '-r--r--r--'

        else:
            # we map this into a copy (sacrificing atomicity)
            f_old = self.open(old_path, os.O_RDONLY)
            f_new = self.create(new_path, 0o100664) # '-rw-rw-r--'

            offset = 0
            while True:
                content = self.read(old_path, size=4096, offset=offset, fh=f_old)
                if len(content)==0:
                    break # end of file reach
                self.write(new_path, content, offset, f_new)
                offset += len(content)
                print("Copied: "+str(content))
            self.release(old_path, f_old)
            self.release(new_path, f_new)

            # remove old_path
            self.unlink(old_path)

            # release lock on new_path
            self.chmod(new_path, 0o100444) # '-r--r--r--'


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
        full_path = self._full_path(path)
        if self._is_wiki(path):
            self._request("remove", path)

        if(os.path.exists(full_path)):
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
    def truncate(self, path, length, fh=None):
        full_path = self._full_path(path)
        with open(full_path, 'r+') as f:
            f.truncate(length)

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
