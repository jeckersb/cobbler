#!/usr/bin/python

"""
This is a script used to automatically log details from an Anaconda
install back to a cobbler server.

Copyright 2008, Red Hat, Inc
various@redhat.com

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301  USA
"""

import os
import sys
import string
import time
import re
import base64
import shlex

# on older installers (EL 2) we might not have xmlrpclib
# and can't do logging, however this is more widely
# supported than remote syslog and also provides more
# detail.
try:
    import xmlrpclib
except ImportError, e:
    print "xmlrpclib not available, exiting"
    sys.exit(0)

# shlex.split support arrived in python-2.3, the following will provide some
# accomodation for older distros (e.g. RHEL3)
if not hasattr(shlex, "split"):
    shlex.split = lambda s: s.split(" ")

class WatchedFile:
    def __init__(self, fn, alias):
        self.fn = fn
        self.alias = alias
        self.reset()

    def reset(self):
        self.where = 0
        self.last_size = 0
        self.lfrag=''
        self.re_list={}
        self.seen_line={}

    def exists(self):
        return os.access(self.fn, os.F_OK)

    def lookfor(self,pattern):
        self.re_list[pattern] = re.compile(pattern,re.MULTILINE)
        self.seen_line[pattern] = 0

    def seen(self,pattern):
        if self.seen_line.has_key(pattern):
            return self.seen_line[pattern]
        else:
            return 0

    def changed(self):
        if not self.exists():
            return 0
        size = os.stat(self.fn)[6]
        if size > self.last_size:
            self.last_size = size
            return 1
        else:
            return 0

    def uploadWrapper(self, blocksize = 262144):
        """upload a file in chunks using the uploadFile call"""
        retries = 3
        fo = file(self.fn, "r")
        totalsize = os.path.getsize(self.fn)
        ofs = 0
        while True:
            lap = time.time()
            contents = fo.read(blocksize)
            size = len(contents)
            data = base64.encodestring(contents)
            if size == 0:
                offset = -1
                sz = ofs
            else:
                offset = ofs
                sz = size
            del contents
            tries = 0
            while tries <= retries:
                debug("upload_log_data('%s', '%s', %s, %s, ...)\n" % (name, self.alias, sz, offset))
                if session.upload_log_data(name, self.alias, sz, offset, data):
                    break
                else:
                    tries = tries + 1
            if size == 0:
                break
            ofs += size
        fo.close()

    def update(self):
        if not self.exists():
            return
        if not self.changed():
            return
        try:
            self.uploadWrapper()
        except:
            raise

class MountWatcher:

    def __init__(self,mp):
        self.mountpoint = mp
        self.zero()

    def zero(self):
        self.line=''
        self.time = time.time()

    def update(self):
        fd = open('/proc/mounts')
        found = 0
        while 1:
            line = fd.readline()
            if not line:
                break
            parts = string.split(line)
            mp = parts[1]
            if mp == self.mountpoint:
                found = 1
                if line != self.line:
                    self.line = line
                    self.time = time.time()
        if not found:
            self.zero()
        fd.close()

    def stable(self):
        self.update()
        if self.line and (time.time() - self.time > 60):
            return 1
        else:
            return 0

def anamon_loop():
    alog = WatchedFile("/tmp/anaconda.log", "anaconda.log")
    alog.lookfor("step installpackages$")

    slog = WatchedFile("/tmp/syslog", "sys.log")
    llog = WatchedFile("/tmp/lvmout", "lvmout.log")
    kcfg = WatchedFile("/tmp/ks.cfg", "ks.cfg")
    scrlog = WatchedFile("/tmp/ks-script.log", "ks-script.log")
    dump = WatchedFile("/tmp/anacdump.txt", "anacdump.txt")
    mod = WatchedFile("/tmp/modprobe.conf", "modprobe.conf")
    ilog = WatchedFile("/mnt/sysimage/root/install.log", "install.log")
    ilog2 = WatchedFile("/mnt/sysimage/tmp/install.log", "tmp+install.log")
    ulog = WatchedFile("/mnt/sysimage/root/upgrade.log", "upgrade.log")
    ulog2 = WatchedFile("/mnt/sysimage/tmp/upgrade.log", "tmp+upgrade.log")
    sysimage = MountWatcher("/mnt/sysimage")

    # Were we asked to watch specific files?
    if watchfiles:
        watchlist = []
        waitlist = []

        # Create WatchedFile objects for each requested file
        for watchfile in watchfiles:
            if os.path.exists(watchfile):
                watchfilebase = os.path.basename(watchfile)
                watchlog = WatchedFile(watchfile, watchfilebase)
                watchlist.append(watchlog)

    # Use the default watchlist and waitlist
    else:
        watchlist = [alog, slog, dump, scrlog, mod, llog, kcfg]
        waitlist = [ilog, ilog2, ulog, ulog2]

    # Monitor loop
    while 1:
        time.sleep(5)

        # Not all log files are available at the start, we'll loop through the
        # waitlist to determine when each file can be added to the watchlist
        for watch in waitlist:
            if alog.seen("step installpackages$") or (sysimage.stable() and watch.exists()):
                debug("Adding %s to watch list\n" % watch.alias)
                watchlist.append(watch)
                waitlist.remove(watch)

        # Send any updates
        for wf in watchlist:
            wf.update()

        # If asked to run_once, exit now
        if exit:
            break

# Establish some defaults
name = ""
server = ""
port = "80"
daemon = 1
debug = lambda x,**y: None
watchfiles = []
exit = False

# Process command-line args
n = 0
while n < len(sys.argv):
    arg = sys.argv[n]
    if arg == '--name':
        n = n+1
        name = sys.argv[n]
    elif arg == '--watchfile':
        n = n+1
        watchfiles.extend(shlex.split(sys.argv[n]))
    elif arg == '--exit':
        exit = True
    elif arg == '--server':
        n = n+1
        server = sys.argv[n]
    elif arg == '--port':
        n = n+1
        port = sys.argv[n]
    elif arg == '--debug':
        debug = lambda x,**y: sys.stderr.write(x % y)
    elif arg == '--fg':
        daemon = 0
    n = n+1

# Create an xmlrpc session handle
session = xmlrpclib.Server("http://%s:%s/cobbler_api" % (server, port))

# Fork and loop
if daemon:
    if not os.fork():
        # Redirect the standard I/O file descriptors to the specified file.
        DEVNULL = getattr(os, "devnull", "/dev/null")
        os.open(DEVNULL, os.O_RDWR) # standard input (0)
        os.dup2(0, 1)               # Duplicate standard input to standard output (1)
        os.dup2(0, 2)               # Duplicate standard input to standard error (2)

        anamon_loop()
        sys.exit(1)
    sys.exit(0)
else:
    anamon_loop()
