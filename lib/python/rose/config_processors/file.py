# -*- coding: utf-8 -*-
#-----------------------------------------------------------------------------
# (C) British Crown Copyright 2012 Met Office.
# 
# This file is part of Rose, a framework for scientific suites.
# 
# Rose is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# Rose is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with Rose. If not, see <http://www.gnu.org/licenses/>.
#-----------------------------------------------------------------------------
"""Process "file:*" sections in a rose.config.ConfigNode."""

from hashlib import md5
from multiprocessing import Pool
import os
import re
import rose.config
from rose.config_processor import ConfigProcessError, ConfigProcessorBase
from rose.env import env_var_process, UnboundEnvironmentVariableError
from rose.fs_util import FileSystemEvent
from rose.reporter import Event
from rose.resource import ResourceLocator
from rose.scheme_handler import SchemeHandlersManager
import shlex
import sqlite3
import shutil
import tempfile
from time import sleep


class FileOverwriteError(Exception):

    """An exception raised in an attempt to overwrite an existing file.

    This will only be raised in non-overwrite mode.

    """

    def __str__(self):
        return ("%s: file already exists (and in no-overwrite mode)" %
                self.args[0])


class UnmatchedChecksumError(Exception):
    """An exception raised on an unmatched checksum."""

    def __str__(self):
        return ("Unmatched checksum, expected=%s, actual=%s" % tuple(self))


class ChecksumEvent(Event):
    """Report the checksum of a file."""

    def __str__(self):
        return "checksum: %s: %s" % self.args


class Loc(object):
    """Represent a location."""

    TYPE_TREE = "tree"
    TYPE_BLOB = "blob"

    def __init__(self, name, scheme=None, dep_locs=None):
        self.name = name
        self.real_name = None
        self.scheme = scheme
        self.dep_locs = dep_locs
        self.mode = None
        self.loc_type = None
        self.paths = None
        self.cache = None
        self.is_out_of_date = None # boolean

    def __str__(self):
        if self.real_name and self.real_name != self.name:
            return "%s (%s)" % (self.real_name, self.name)
        else:
            return self.name

    def update(self, other):
        self.name = other.name
        self.real_name = other.real_name
        self.scheme = other.scheme
        self.mode = other.mode
        self.paths = other.paths
        self.cache = other.cache
        self.is_out_of_date = other.is_out_of_date


class LocPath(object):
    """Represent a sub-path in a location."""

    def __init__(self, name):
        self.name = name
        self.checksum = None

    def __cmp__(self, other):
        return cmp(self.name, other.name) or cmp(self.checksum, other.checksum)

    def __eq__(self, other):
        return (self.name == other.name and self.checksum == other.checksum)

    def __str__(self):
        return self.name


class LocTask(object):
    """Represent a task for dealing with a location."""

    ST_DONE = "ST_DONE"
    #ST_FAILED = "ST_FAILED"
    ST_PENDING = "ST_PENDING"
    ST_READY = "ST_READY"
    ST_WORKING = "ST_WORKING"

    def __init__(self, loc, task_key):
        self.loc = loc
        self.task_key = None
        self.name = loc.name
        self.needed_by = {}
        self.pending_for = {}
        self.state = self.ST_READY

    def __str__(self):
        return "%s: %s" % (self.task_key, str(self.loc))

    def update(self, other):
        self.state = other.state
        self.loc.update(other.loc)

class LocTypeError(Exception):
    def __str__(self):
        return "%s <= %s, expected %s, got %s" % self.args

class TaskManager(object):
    """Manage a set of LocTask objects and their states."""

    def __init__(self, tasks, names=None):
        """Initiate a location task manager.

        tasks: A dict of all available tasks (task.name, task).
        names: A list of keys in tasks to process.
               If not set or empty, process all tasks.

        """
        self.tasks = tasks
        self.ready_tasks = []
        if not names:
            names = tasks.keys()
        for name in names:
            self.ready_tasks.append(self.tasks[name])
        self.working_tasks = {}

    def get_task(self):
        """Return the next task that requires processing."""
        while self.ready_tasks:
            task = self.ready_tasks.pop()
            for dep_key, dep_task in task.pending_for.items():
                if dep_task.state == dep_task.ST_DONE:
                    task.pending_for.pop(dep_key)
                    if dep_task.needed_by.has_key(task.name):
                        dep_task.needed_by.pop(task.name)
                else:
                    dep_task.needed_by[task.name] = task
                    if dep_task.state is None:
                        dep_task.state = dep_task.ST_READY
                        self.ready_tasks.append(dep_task)
            if task.pending_for:
                task.state = task.ST_PENDING
            else:
                self.working_tasks[task.name] = task
                task.state = task.ST_WORKING
                return task

    def get_dead_tasks(self):
        """Return pending tasks if there are no ready/working ones."""
        if not self.has_tasks:
            return [task if task.pending_for for task in self.tasks.values()]
        return (not self.has_tasks and
                any([task.pending_for for task in self.tasks.values()]))

    def has_tasks(self):
        """Return True if there are ready tasks or working tasks."""
        return bool(self.ready_tasks) or bool(self.working_tasks)

    def has_ready_tasks(self):
        """Return True if there are ready tasks."""
        return bool(self.ready_tasks)

    def put_task(self, task_proxy):
        """Tell the manager that a task has completed."""
        task = self.working_tasks.pop(task_proxy.name)
        task.update(task_proxy)
        for up_key, up_task in task.needed_by.items():
            task.needed_by.pop(up_key)
            up_task.pending_for.pop(task.name)
            if not up_task.pending_for:
                self.ready_tasks.append(up_task)
                up_task.state = up_task.ST_READY
        return task


class TaskRunner(object):
    """Runs LocTask objects with pool of workers."""

    NPROC = 6
    POLL_DELAY = 0.05

    def __init__(self, task_processor):
        self.task_processor = task_processor
        conf = ResourceLocator.default().get_conf()
        self.nproc = int(conf.get_value(
                ["rose.config_processors.file", "nproc"],
                default=self.NPROC))

    def run(self, config, task_manager):
        nproc = self.nproc
        if nproc > len(task_manager.tasks):
            nproc = len(task_manager.tasks)
        pool = Pool(processes=nproc)

        results = {}
        while task_manager.has_tasks():
            # Process results, if any is ready
            for name, result in results.items():
                if result.ready():
                    results.pop(name)
                    task_proxy, args_of_events = result.get()
                    for args_of_event in args_of_events:
                        self.task_processor.handle_event(*args_of_event)
                    task = task_manager.put_task(task_proxy)
                    self.task_processor.post_process_task(config, task)
            # Add some more tasks into the worker pool, as they are ready
            while task_manager.has_ready_tasks():
                task = task_manager.get_task()
                if task is None:
                    break
                result = pool.apply_async(_loc_task_run,
                                          [self.task_processor, config, task])
                results[task.name] = result
            if results:
                sleep(self.POLL_DELAY)

        dead_tasks = task_manager.get_dead_tasks()
        if dead_tasks:
            raise UnfinishedTasksError(dead_tasks)

    __call__ = run


class WorkerEventHandler(object):
    """Temporary event handler in a function run by a pool worker process.

    Events are collected in the self.events which is a list of tuples
    representing the arguments the report method in an instance of
    rose.reporter.Reporter.

    """
    def __init__(self):
        self.events = []

    def __call__(self, message, type=None, level=None, prefix=None, clip=None):
        self.events.append((message, type, level, prefix, clip))


def _loc_task_run(task_processor, task_proxy):
    """Helper for LocTaskRunner."""
    event_handler = WorkerEventHandler()
    task_processor.set_event_handler(event_handler)
    task_processor.process_task(task_proxy)
    task_processor.set_event_handler(None)
    return (task_proxy, event_handler.events)


class UnfinishedTasksError(Exception):
    """Error raised when there are no ready/working tasks but pending ones."""
    def __str__(self):
        ret = ""
        for task in self.args:
            ret += "[DEAD TASK] %s\n" % str(task)
        return ret


class ConfigProcessorForFile(ConfigProcessorBase):

    SCHEME = "file"
    NPROC = 6
    POLL_DELAY = 0.05
    RE_FCM_SRC = re.compile(r"(?:\A[A-z][\w\+\-\.]*:)|(?:@[^/@]+\Z)")
    T_INSTALL = "install"
    T_PULL = "pull"

    def __init__(self, *args, **kwargs):
        ConfigProcessorBase.__init__(self, *args, **kwargs)
        self.file_loc_handlers_manager = FileLocHandlersManager(
                event_handler=self.manager.event_handler,
                popen=self.manager.popen,
                fs_util=self.manager.fs_util)
        self.loc_dao = LocDAO()

    def handle_event(self, *args):
        self.manager.handle_event(*args)

    def process(self, config, item, orig_keys=None, orig_value=None, **kwargs):
        """Install files according to the file:* sections in "config"."""
        nodes = {}
        if item == self.SCHEME:
            for key, node in config.value.items():
                if node.is_ignored() or not key.startswith(self.PREFIX):
                    continue
                nodes[key] = node
        else:
            node = config.get([item], no_ignore=True)
            if node is None:
                raise ConfigProcessError(orig_keys, item)
            nodes[item] = node

        if not nodes:
            return

        # Ensure that everything is overwritable
        # Ensure that container directories exist
        for key, node in sorted(nodes.items()):
            name = key[len(self.PREFIX):]
            if os.path.exists(name) and kwargs.get("no_overwrite_mode"):
                e = FileOverwriteError(name)
                raise ConfigProcessError([key], None, e)
            self.manager.fs_util.makedirs(self.manager.fs_util.dirname(name))

        # Create database to store information for incremental updates,
        # if it does not already exist.
        self.loc_dao.create()

        # Gets a list of sources and targets
        sources = {}
        targets = {}
        for key, node in sorted(nodes.items()):
            content_str = node.get_value("content")
            name = key[len(self.PREFIX):]
            target_sources = []
            if content_str is not None:
                for n in shlex.split(content_str):
                    sources[n] = Loc(n)
                    target_sources.append(sources[n])
            targets[name] = Loc(name)
            targets[name].dep_locs = sorted(target_sources)
            targets[name].mode = node.get_value("mode")

        # Where applicable, determine for each source:
        # * Its invariant name.
        # * Whether it can be considered unchanged.
        for source in sources.values():
            self._source_parse(config, source)

        # Inspect each target to see if it is out of date:
        # * Target does not already exist.
        # * Target exists, but does not have a database entry.
        # * Target exists, but does not match settings in database.
        # * Target exists, but a source cannot be considered unchanged.
        for target in targets.values():
            if os.path.islink(target.name):
                target.real_name = os.readlink(target.name)
            elif os.path.isfile(target.name):
                m = md5()
                f = open(target.name)
                m.update(f.read())
                target.paths = [LocPath("", m.hexdigest())]
            elif os.path.isdir(target.name):
                target.paths = []
                for dirpath, dirnames, filenames in os.walk(target.name):
                    for i in reversed(range(len(dirnames))):
                        dirname = dirnames[i]
                        if dirname.startswith("."):
                            dirnames.pop(i)
                            continue
                        target.paths.append(
                                LocPath(os.path.join(dirpath, dirname)))
                    for filename in filenames:
                        m = md5()
                        file_path = os.path.join(dirpath, filename)
                        f = open(file_path)
                        m.update(f.read())
                        target.paths.append(LocPath(file_path, m.hexdigest()))
                target.paths.sort()
            prev_target = self.loc_dao.select(target.name)
            target.is_out_of_date = (
                    not os.path.exists(target.name) or
                    prev_target is None or
                    prev_target.mode != target.mode or
                    prev_target.real_name != target.real_name or
                    prev_target.paths != target.paths or
                    any([s.is_out_of_date for s in target.dep_locs]))
            if target.is_out_of_date:
                self.loc_dao.delete(target)

        # Set up tasks for rebuilding all out-of-date targets.
        tasks = {}
        for target in targets.values():
            if not target.is_out_of_date:
                continue
            if target.dep_locs:
                if len(target.dep_locs) == 1 and target.mode == "symlink":
                    self.manager.fs_util.symlink(target.dep_locs[0], target.name)
                    self.loc_dao.update(target)
                else:
                    tasks[target.name] = LocTask(target, self.T_INSTALL)
                    for source in target.dep_locs:
                        tasks[source.name] = LocTask(source, self.T_PULL)
            elif target.mode == "mkdir":
                self.manager.fs_util.makedirs(target.name)
                self.loc_dao.update(target)
                target.loc_type = target.TYPE_TREE
                target.paths = [LocPath("", None)]
            else:
                self.manager.fs_util.create(target.name)
                self.loc_dao.update(target)
                target.loc_type = target.TYPE_BLOB
                target.paths = [LocPath("", md5().hexdigest())]

        TaskRunner(self)(config, TaskManager(tasks))

        # Target checksum compare and report
        for target in targets.values():
            if not target.is_out_of_date or target.loc_type == target.TYPE_TREE:
                continue
            keys = [self.PREFIX + target.name, "checksum"]
            checksum_expected = config.get_value(keys)
            if checksum_expected is None:
                continue
            checksum = target.paths[0].checksum
            if checksum_expected and checksum_expected != checksum:
                e = UnmatchedChecksumError(checksum_expected, checksum)
                raise ConfigProcessError(keys, checksum_expected, e)
            event = ChecksumEvent(target.name, checksum)
            self.handle_event(event)

    def process_task(self, config, task):
        """Process a task, helper for process."""
        if task.task_key == self.T_INSTALL:
            self._target_install(config, task.loc)
        elif task.task_key == self.T_PULL:
            self._source_pull(config, task.loc)
        task.state = task.ST_DONE

    def post_process_task(self, config, task):
        self.loc_dao.update(task.loc)

    def _source_parse(self, config, task):
        pass # TODO

    def _source_pull(self, config, task):
        pass # TODO

    def _target_install(self, config, task):
        f = None
        is_first = True
        # Install target
        for source in task.loc.dep_locs:
            if target.loc_type is None:
                target.loc_type = source.loc_type
            elif target.loc_type != source.loc_type
                raise LocTypeError(target.name, source.name, target.loc_type,
                                   source.loc_type)
            if target.loc_type == target.TYPE_BLOB:
                if f is None:
                    f = open(target.name, "wb")
                f.write(open(source.cache).read())
            else: # target.loc_type == target.TYPE_TREE
                args = []
                if is_first:
                    self.manager.fs_util.makedirs(target.name)
                    args.append("--delete-excluded")
                args.extend(["--checksum", source.cache, target.name])
                cmd = self.manager.popen.get_cmd("rsync", *args)
                out, err = self.manager.popen(*cmd)
            is_first = False
        if f is not None:
            f.close()

        # Calculate target checksum(s)
        if target.loc_type == target.TYPE_BLOB:
            m = md5()
            m.update(open(target).read())
            target.paths = [LocPath("", m.hexdigest())]
        else:
            target.paths = []
            for dirpath, dirnames, filenames in os.walk(target):
                path = dirpath[len(target) + 1:]
                target.paths.append(LocPath(path, None))
                for filename in filenames:
                    filepath = os.path.join(path, filename)
                    m = md5()
                    m.update(open(filepath).read())
                    target.paths.append(LocPath(filepath, m.hexdigest()))

    #def _source_export(self, source, target):
    #    """Export/copy a source file/directory in FS or FCM VC to a target."""
    #    if source == target:
    #        return
    #    elif self._source_is_fcm(source):
    #        command = ["fcm", "export", "--quiet", source, target]
    #        return self.manager.popen(*command)
    #    elif os.path.isdir(source):
    #        ignore = shutil.ignore_patterns(".*")
    #        return shutil.copytree(source, target, ignore=ignore)
    #    else:
    #        return shutil.copyfile(source, target)


    #def _source_is_fcm(self, source):
    #    """Return true if source is an FCM version controlled resource."""
    #    return self.RE_FCM_SRC.match(source) is not None


    #def _source_load(self, source):
    #    """Load and return the content of a source file in FS or FCM VC."""
    #    if self._source_is_fcm(source):
    #        f = tempfile.TemporaryFile()
    #        self.manager.popen("fcm", "cat", source, stdout=f)
    #        f.seek(0)
    #        return f.read()
    #    else:
    #        return open(source).read()

    def set_event_handler(self, event_handler):
        try:
            self.manager.event_handler.event_handler = event_handler
        except AttributeError:
            pass


class LocDAO(object):
    """DAO for information for incremental updates."""

    DB_FILE_NAME = ".rose-config_processors-file.db"

    def __int__(self):
        self.conn = None

    def get_conn(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.DB_FILE_NAME)
        return self.conn

    def create(self):
        """Create the database file if it does not exist."""
        if not os.path.exists(self.DB_FILE_NAME):
            conn = self.get_conn()
            c = conn.cursor()
            c.execute("""CREATE TABLE locs(
                          name TEXT,
                          real_name TEXT,
                          scheme TEXT,
                          mode TEXT,
                          loc_type TEXT,
                          PRIMARY KEY(name))""")
            c.execute("""CREATE TABLE paths(
                          name TEXT,
                          path TEXT,
                          checksum TEXT,
                          UNIQUE(name, path))""")
            c.execute("""CREATE TABLE dep_names(
                          name TEXT,
                          dep_name TEXT,
                          UNIQUE(name, dep_name))""")
            conn.commit()

    def delete(self, loc):
        """Remove settings related to loc from the database."""
        conn = self.get_conn()
        c = conn.cursor()
        c.execute("""DELETE FROM locs WHERE name=?""", loc.name)
        c.execute("""DELETE FROM dep_names WHERE name=?""", loc.name)
        c.execute("""DELETE FROM paths WHERE name=?""", loc.name)
        conn.commit()

    def select(self, name):
        """Query database for settings matching name.
        
        Reconstruct setting as a Loc object and return it.
 
        """
        conn = self.get_conn()
        c = conn.cursor()

        c.execute("""SELECT real_name,scheme,mode,loc_type FROM locs""" +
                  """ WHERE name=?""", name)
        row = c.fetchone()
        if row is None:
            return
        loc = Loc(name)
        loc.real_name, loc.scheme, loc.mode, loc.loc_type = row

        c.execute("""SELECT path,checksum FROM paths WHERE name=?""", name)
        for row in c:
            path, checksum = row
            if loc.paths is None:
                loc.paths = []
            loc.paths.append(LocPath(path, checksum))

        c.execute("""SELECT dep_name FROM dep_names WHERE name=?""", name)
        for row in c:
            dep_name, = row
            if loc.dep_locs is None:
                loc.dep_locs = []
            loc.dep_locs.append(self.select(dep_name))

    def update(self, loc):
        """Insert or update settings related to loc to the database."""
        conn = self.get_conn()
        c = conn.cursor()
        c.execute("""INSERT OR REPLACE INTO locs VALUES(?,?,?,?,?)""",
                  loc.name, loc.real_name, loc.scheme, loc.mode, loc.loc_type)
        if loc.paths:
            for path in loc.paths:
                c.execute("""INSERT OR REPLACE INTO paths VALUES(?,?,?)""",
                          name, path.name, path.checksum)
        if loc.dep_locs:
            for dep_loc in loc.dep_locs:
                c.execute("""INSERT OR REPLACE INTO dep_names VALUES(?,?)""",
                          name, dep_loc.name)


#class FileLocHandlerBase(object):
#    """Base class for a file location handler."""
#
#    SCHEME = None
#    PULL_MODE = "PULL_MODE"
#    PUSH_MODE = "PUSH_MODE"
#
#    def __init__(self, manager):
#        self.manager = manager
#
#    def handle_event(self, *args, **kwargs):
#        """Call self.event_handler with given arguments if possible."""
#        self.manager.handle_event(*args, **kwargs)
#
#    def can_handle(self, file_loc, mode=PULL_MODE):
#        """Return True if this handler can handle file_loc."""
#        return False
#
#    def load(self, file_loc, **kwargs):
#        """Return a temporary file handle to read a remote file_loc."""
#        f = tempfile.NamedTemporaryFile()
#        self.pull(f.name, file_loc)
#        f.seek(0)
#        return f
#
#    def pull(self, file_path, file_loc, **kwargs):
#        """Pull remote file_loc to local file_path."""
#        raise NotImplementedError()
#
#    def push(self, file_path, file_loc, **kwargs):
#        """Push local file_path to remote file_loc."""
#        raise NotImplementedError()
#
#
#class FileLocHandlersManager(SchemeHandlersManager):
#    """Manage location handlers."""
#
#    PULL_MODE = FileLocHandlerBase.PULL_MODE
#    PUSH_MODE = FileLocHandlerBase.PUSH_MODE
#
#    def __init__(self, event_handler=None, popen=None, fs_util=None):
#        path = os.path.join(os.path.dirname(__file__), "file_loc_handlers")
#        SchemeHandlersManager.__init__(self, path, ["load", "pull", "push"])
#        self.event_handler = event_handler
#        if popen is None:
#            popen = RosePopener(event_handler)
#        self.popen = popen
#        if fs_util is None:
#            fs_util = FileSystemUtil(event_handler)
#        self.fs_util = fs_util
#
#    def handle_event(self, *args, **kwargs):
#        """Call self.event_handler with given arguments if possible."""
#        if callable(self.event_handler):
#            return self.event_handler(*args, **kwargs)
#
#    def load(self, file_loc, **kwargs):
#        if file_loc.scheme:
#            p = self.get_handler(file_loc.scheme)
#        else:
#            p = self.guess_handler(file_loc, mode=self.PULL_MODE)
#        return p.load(file_loc, **kwargs)
#
#    def pull(self, file_path, file_loc, **kwargs):
#        if file_loc.scheme:
#            p = self.get_handler(file_loc.scheme)
#        else:
#            p = self.guess_handler(file_loc, mode=self.PULL_MODE)
#        return p.pull(file_path, file_loc, **kwargs)
#
#    def push(self, file_path, file_loc, **kwargs):
#        if file_loc.scheme:
#            p = self.get_handler(file_loc.scheme)
#        else:
#            p = self.guess_handler(file_loc, mode=self.PUSH_MODE)
#        return p.push(file_path, file_loc, **kwargs)
