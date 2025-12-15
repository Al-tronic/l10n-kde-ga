#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import codecs
import glob
import hashlib
import locale
import os
from optparse import OptionParser
import pipes
import re
import shutil
import subprocess
import sys
import time

HAS_POLOGY = True
try:
    from pology.catalog import Catalog
    from pology.fsops import collect_catalogs
    from pology.getfunc import get_hook_ireq
except:
    HAS_POLOGY = False


def main ():

    # Setup options and parse the command line.
    usage = u"""
%prog [OPTIONS] [DIRS...]
""".strip()
    description = u"""
Search the given directories for l10n data bundles and check their states; see [[add_link]] for details about lbundles. If no directory is given, the current working directory is searched.
    """.strip()
    version = u"""
%prog experimental
Copyright © 2007 Chusslove Illich (Часлав Илић) <caslav.ilic@gmx.net>
""".strip()

    opars = OptionParser(usage=usage, description=description, version=version)
    opars.add_option(
        "-s", "--source-top",
        dest="srctop", default="", metavar="DIRPATH",
        help="the top local directory of source roots, "
             "needed by any bundles which define them")
    opars.add_option(
        "-u", "--update",
        action="store_true", dest="update_src", default=False,
        help="update the source data from version control")
    opars.add_option(
        "-l", "--languages",
        dest="only_lang", default="", metavar="LANG[,LANG...]",
        help="limit checks to these languages only")
    (options, dirpaths) = opars.parse_args()

    if not dirpaths:
        dirpaths = ["."]

    # Check bundles in all paths.
    cstats = Check_stats()
    for dirpath in dirpaths:
        if not os.path.isdir(dirpath):
            warning("not a directory path, skipping: %s" % dirpath)
            continue
        check_bundles_in_dir(dirpath, cstats, options)

    # Synchronize all track files.
    for btrack in cstats.btracks:
        btrack.sync()

    # Report collected statistics.
    repstr = ""
    cwd = os.getcwd() + os.path.sep
    for state, files in cstats.new_state.iteritems():
        if files:
            # Try to report relative to current working directory.
            rfiles = []
            for file in files:
                if file[1].startswith(cwd):
                    rfiles.append((file[0], file[1][len(cwd):], file[2]))
                else:
                    rfiles.append(file)
            repstr += "New '%s': %d\n" % (state, len(rfiles))
            repstr += "".join(["  %s (%s %s)\n" % x for x in rfiles])
    if repstr:
        repstr = "--------------------\n" + repstr
        sys.stdout.write(repstr)


# Convert a raw string value into Unicode.
def str_to_unicode (rstr):

    if isinstance(rstr, unicode):
        return rstr
    return rstr.decode(locale.getpreferredencoding(), "replace")


# Convert a unicode string into raw byte sequence.
def unicode_to_str (ustr):

    if isinstance(ustr, str):
        return ustr
    return ustr.encode(locale.getpreferredencoding())


def message (msg, src="", line=0, dest=sys.stdout):

    if src:
        if line > 0:
            dest.write(unicode_to_str("%s:%d: %s\n" % (src, line, msg)))
        else:
            dest.write(unicode_to_str("%s: %s\n" % (src, msg)))
    else:
        dest.write(unicode_to_str("%s\n" % msg))


def error (msg, src="", line=0, code=1):

    sys.stdout.flush()
    message("error: %s" % msg, src=src, line=line, dest=sys.stderr)
    sys.exit(code)


def warning (msg, src="", line=0):

    sys.stdout.flush()
    message("warning: %s" % msg, src=src, line=line, dest=sys.stderr)


# Execute command line with output capturing.
# Return standard output, standard error, exit code (unless aborted).
def check_system (cmdline, echo=False, wdir=None, env=None, abort=True):

    if not isinstance(cmdline, basestring):
        cmdline = map(unicode_to_str, cmdline)
        strcmdline = " ".join(pipes.quote(a) for a in cmdline)
    else:
        cmdline = unicode_to_str(cmdline)
        strcmdline = cmdline

    if echo:
        sys.stdout.write("%s\n" % strcmdline)
    if wdir is not None:
        cwd = os.getcwd()
        os.chdir(wdir)
    shell = isinstance(cmdline, basestring)
    p = subprocess.Popen(cmdline, shell=shell, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    strout, strerr = map(str_to_unicode, p.communicate())
    retcode = p.returncode
    if wdir is not None:
        os.chdir(cwd)
    if echo:
        if strout:
            sys.stdout.write(strout)
            sys.stdout.flush()
        if strerr:
            sys.stderr.write(strerr)
            sys.stderr.flush()
    if retcode != 0 and abort:
        if echo:
            error("*** non-zero exit from previous command")
        else:
            error("*** non-zero exit from: %s" % strcmdline)
    return retcode, strout, strerr


# Apply the regex string to logical lines in the string up to first match,
# and return list of captures, or full match if no captures defined;
# if no line matches, return empty list.
def first_match_in_lines (rxstr, linestr):

    lines = linestr.split("\n")
    rx = re.compile(rxstr)
    groups = []
    for line in lines:
        line = line[:-1]
        m = rx.search(line)
        if m:
            groups = list(m.groups())
            if not groups:
                groups = [m.group()]
            break
    return groups


# Version control data for different VCS's
class Vcs_data:

    def __init__ (self, vcs_id=None):

        # These are the defaults for no VCS, which are overriden later.

        # Execution environment when output is to be captured and parsed.
        self._ocenv = os.environ.copy()
        self._ocenv["LC_ALL"] = "C"

        if vcs_id in (None, "none"):

            # Internal subdirectories for VCS's bookkeeping.
            self.ignore_dirs = []

            # Is file belonging to VCS, for bookkeeping.
            def is_own_file (filepath):
                return False
            self.is_own_file = is_own_file

            # Is file under version control.
            def is_versioned_file (filepath):
                return True
            self.is_versioned_file = is_versioned_file

            # Update file to latest revision.
            def update_file (filepath):
                warning("cannot update file '%s', no VCS specified" % filepath)
                retcode = 1
                return retcode
            self.update_file = update_file

            # Add file to version control.
            def add_file (filepath):
                retcode = 0
                return retcode
            self.add_file = add_file

            # Move/rename file within the repository.
            def move_file (srcpath, dstpath):
                retcode = check_system(["mv", srcpath, dstpath], echo=True)[0]
                return retcode
            self.move_file = move_file

            # Remove file from the repository.
            def remove_file (filepath):
                retcode = check_system(["rm", filepath], echo=True)[0]
                return retcode
            self.remove_file = remove_file

            # Discard local modifications to file.
            def revert_file (filepath):
                #warning("cannot revert file '%s', no VCS specified" % filepath)
                #retcode = 1
                retcode = 0
                return retcode
            self.revert_file = revert_file

            # Retrieve revision string.
            def get_revision (filepath):
                #return time.strftime("%Y-%m-%d_%H:%m_%Z") # do not use spaces
                return file_checksum(filepath)
            self.get_revision = get_revision

            # Advise the VCS to mention that the file is modifed.
            def say_modified_file (filepath):
                #warning("cannot report modified file '%s', no VCS specified"
                        #% filepath)
                message("modified: %s" % filepath)
            self.say_modified_file = say_modified_file

        elif vcs_id == "svn":

            self.ignore_dirs = [".svn"]

            def svnesc (filepath):
                if "@" in filepath:
                    filepath += "@"
                return filepath

            def is_own_file (filepath):
                return ".svn" in filepath.split(os.path.sep)
            self.is_own_file = is_own_file

            def is_versioned_file (filepath):
                strout = check_system(["svn", "info", svnesc(filepath)],
                                      env=self._ocenv, abort=False)[1]
                return bool(first_match_in_lines(r"^Repository", strout))
            self.is_versioned_file = is_versioned_file

            self._updated_files = set()
            def update_file (filepath):
                if filepath not in self._updated_files:
                    retcode = check_system(["svn", "up", svnesc(filepath)],
                                           echo=True)[0]
                    if retcode == 0:
                        self._updated_files.add(filepath)
                    return retcode
                else:
                    return 1
            self.update_file = update_file

            def add_file (filepath):
                # Find first path that needs adding, by backtracking.
                cpath = filepath
                ppath = None
                while check_system(["svn", "info", svnesc(cpath)],
                                   abort=False)[0] != 0:
                    ppath = cpath
                    cpath = os.path.dirname(cpath)
                    if not cpath:
                        return 1
                if ppath:
                    retcode = check_system(["svn", "add", svnesc(ppath)])[0]
                    return retcode
                else:
                    return 1
            self.add_file = add_file

            def move_file (srcpath, dstpath):
                retcode = check_system(["svn", "mv", svnesc(srcpath), dstpath],
                                       echo=True)[0]
                return retcode
            self.move_file = move_file

            def remove_file (filepath):
                retcode = check_system(["svn", "rm", svnesc(filepath)],
                                       echo=True)[0]
                return retcode
            self.remove_file = remove_file

            def revert_file (filepath):
                retcode = check_system(["svn", "revert", svnesc(filepath)],
                                       echo=True)[0]
                return retcode
            self.revert_file = revert_file

            def get_revision (filepath):
                strout = check_system(["svn", "info", svnesc(filepath)],
                                      env=self._ocenv)[1]
                lst = first_match_in_lines(r"^Last Changed Rev: *([0-9]+)",
                                           strout)
                if not lst:
                    error("cannot extract Subversion data for '%s'" % filepath)
                return lst[0]
            self.get_revision = get_revision

            def say_modified_file (filepath):
                strout = check_system(["svn", "status", svnesc(filepath)])[1]
                message(strout.rstrip())
            self.say_modified_file = say_modified_file

        elif vcs_id == "git":

            def get_git_root (filepath):
                root = None
                pdir = os.path.dirname(filepath)
                while True:
                    gitpath = os.path.join(pdir, ".git")
                    if os.path.isdir(gitpath):
                        root = pdir
                        break
                    prev_pdir = pdir
                    pdir = os.path.dirname(pdir)
                    if pdir == prev_pdir:
                        break
                if root is None:
                    error("cannot find Git repository for '%s'" % filepath)
                return root

            self.ignore_dirs = [".git"]

            def is_own_file (filepath):
                return ".git" in filepath.split(os.path.sep)
            self.is_own_file = is_own_file

            def is_versioned_file (filepath):
                root = get_git_root(filepath)
                ret = check_system(["git", "status", filepath], wdir=root,
                                   env=self._ocenv, abort=False)
                retcode, strout, strerr = ret
                if retcode != 0:
                    return False
                return not bool(first_match_in_lines(r"untracked", strout))
            self.is_versioned_file = is_versioned_file

            self._updated_roots = set()
            def update_file (filepath):
                root = get_git_root(filepath)
                if root not in self._updated_roots:
                    retcode = check_system(["git", "pull", "--rebase"],
                                           wdir=root, echo=True)[0]
                    if retcode == 0:
                        self._updated_roots.add(root)
                    return retcode
                else:
                    return 0
            self.update_file = update_file

            def add_file (filepath):
                root = get_git_root(filepath)
                retcode = check_system(["git", "add", filepath], wdir=root)[0]
                return retcode
            self.add_file = add_file

            def move_file (srcpath, dstpath):
                root = get_git_root(filepath)
                retcode = check_system(["git", "mv", srcpath, dstpath],
                                       wdir=root, echo=True)[0]
                return retcode
            self.move_file = move_file

            def remove_file (filepath):
                root = get_git_root(filepath)
                retcode = check_system(["git", "rm", filepath], wdir=root,
                                       echo=True)[0]
                return retcode
            self.remove_file = remove_file

            def revert_file (filepath):
                root = get_git_root(filepath)
                retcode = check_system(["git", "checkout", filepath], wdir=root,
                                       echo=True)[0]
                return retcode
            self.revert_file = revert_file

            def get_revision (filepath):
                root = get_git_root(filepath)
                strout = check_system(["git", "log", "-1", filepath],
                                      wdir=root, env=self._ocenv)[1]
                lst = first_match_in_lines(r"^commit +([0-9a-zA-Z]+)", strout)
                if not lst:
                    error("cannot extract Git log for '%s'" % filepath)
                return lst[0]
            self.get_revision = get_revision

            def say_modified_file (filepath):
                root = get_git_root(filepath)
                strout = check_system(["git", "status", filepath], wdir=root)[1]
                lst = first_match_in_lines(r"(modified:.*)", strout)
                if lst:
                    message(lst[0])
            self.say_modified_file = say_modified_file

        elif vcs_id:
            error("unknown version control type '%s'" % vcs_id)


    @staticmethod
    def detect_vcs_id_from_path (path):

        path = os.path.normpath(path)
        path_els = path.split(os.path.sep)
        vcs_id = None
        while path_els:
            dir_path = os.path.sep.join(path_els)
            if os.path.exists(os.path.join(dir_path, ".svn")):
                vcs_id = "svn"
            elif os.path.exists(os.path.join(dir_path, ".git")):
                vcs_id = "git"
            if vcs_id is not None:
                break
            path_els.pop()
        return vcs_id


L10N_DIRNAME = "l10n"
SPEC_FILENAME = "l10n-spec"
TRACK_FILENAME = "l10n-track"


# Data handler for bundle specification files.
class Bundle_spec:

    def __init__ (self, filepath, options):

        self.owndir = os.path.dirname(filepath)
        self.srcroot = ""
        self.srcvcs = Vcs_data()
        self.locvcs = Vcs_data()
        self.languages = []
        self.tracked = {}
        self.ignored = {}
        self.strict = False
        self.ignsubs = FNM_STATES.values()
        self.trbysubdir = False
        self.greedy = False
        self.grlevel = 0
        self.gronlystarted = False
        self.grmonoling = False
        self.uicatroot = ""
        self.uicatrootalt = ""
        self.uicatrootalt2 = ""
        self.uicatbylang = False
        self.uimsgstrfspecs = []
        self.tighttrack = False

        locvcs_read = False
        try_srcvcs_auto = False
        try_locvcs_auto = False

        ifl = codecs.open(filepath, "r", "UTF-8")
        clineno = 1; line = ""; lineno = 1
        while True:
            cline = ifl.readline()
            if cline:
                cline = cline[:cline.find("#")] # strip comment and newline
                if cline.endswith("\\"): # continuation line
                    line += cline[:-1]
                    continue
                line += cline
                if not line.strip():
                    continue
            elif not line: # unless a continuation fell of end of file
                break

            fields = line.split("=", 1)
            if len(fields) != 2:
                error("field not in key=value format", filepath, lineno)
            key = fields[0].strip().lower()
            value = fields[1].strip()

            if 0: pass
            elif key == "source-root":
                self.srcroot = self._parse_path(value, filepath, lineno)
            elif key == "source-vcs":
                value = value.lower()
                if value == "auto":
                    try_srcvcs_auto = True
                else:
                    self.srcvcs = Vcs_data(value)
            elif key == "bundle-vcs":
                value = value.lower()
                if value == "auto":
                    try_locvcs_auto = True
                else:
                    self.locvcs = Vcs_data(value)
                locvcs_read = True
            elif key == "languages":
                self.languages = value.split()
            elif key == "track-unbundled":
                self.tracked = self._parse_fmap(value, key, filepath, lineno)
            elif key == "ignore-unbundled":
                self.ignored = self._parse_fmap(value, key, filepath, lineno)
            elif key == "strict-state":
                self.strict = self._parse_bool(value, filepath, lineno)
            elif key == "ignore-substr":
                self.ignsubs.extend(value.split())
            elif key == "track-by-subdir":
                self.trbysubdir = self._parse_bool(value, filepath, lineno)
            elif key == "greedy-bundling":
                self.greedy = self._parse_bool(value, filepath, lineno)
            elif key == "greedy-from-level":
                self.grlevel = self._parse_int(value, filepath, lineno)
            elif key == "greedy-only-started":
                self.gronlystarted = self._parse_bool(value, filepath, lineno)
            elif key == "greedy-monolingual":
                self.grmonoling = self._parse_bool(value, filepath, lineno)
            elif key == "ui-catalog-root":
                self.uicatroot = self._parse_path(value, filepath, lineno)
            elif key == "ui-catalog-root-alter":
                self.uicatrootalt = self._parse_path(value, filepath, lineno)
            elif key == "ui-catalog-root-alter-2":
                self.uicatrootalt2 = self._parse_path(value, filepath, lineno)
            elif key == "ui-catalog-bylang":
                self.uicatbylang = self._parse_bool(value, filepath, lineno)
            elif key == "ui-msgstr-filter":
                self.uimsgstrfspecs.extend(value.split())
            elif key == "tight-track-file":
                self.tighttrack = self._parse_bool(value, filepath, lineno)
            else:
                error("unknown field key '%s'" % key, filepath, lineno)

            clineno += 1
            line = ""
            lineno = clineno

        ifl.close()

        # If a final external path is not resolved as absolute,
        # request that local repository top is specified
        if not options.srctop:
            if self.srcroot and not os.path.isabs(self.srcroot):
                error("source root given relative to external top, "
                      "but the external top not given in the command line",
                      filepath)
            if self.uicatroot and not os.path.isabs(self.uicatroot):
                error("UI catalog root given relative to external top, "
                      "but the external top not given in the command line",
                      filepath)
            if self.uicatrootalt and not os.path.isabs(self.uicatrootalt):
                error("alternative UI catalog root given relative to "
                      "external top, "
                      "but the external top not given in the command line",
                      filepath)
            if self.uicatrootalt2 and not os.path.isabs(self.uicatrootalt2):
                error("second alternative UI catalog root given relative to "
                      "external top, "
                      "but the external top not given in the command line",
                      filepath)

        # There should be no unbundled tracks/ignores for in-source boundle.
        if not self.srcroot and (self.tracked or self.ignored):
            warning("unbundled files always ignored in in-source boundles, "
                    "specifying track/ignore serves no purpose", filepath)
            self.tracked = {}
            self.ignored = {}

        # Must have none or a single language in monolingual greedy bundle.
        if (    self.greedy and self.grmonoling
            and len(self.languages) not in (0, 1)
        ):
            error("monolingual greedy bundle selected, "
                  "but more than one language registered", filepath)

        # Expand globs.
        if self.srcroot:
            basedir = os.path.dirname(filepath)
            offbase_loc = len(basedir + os.path.sep)
            origdir = os.path.join(options.srctop, self.srcroot)
            if not os.path.isdir(origdir):
                error("source root directory '%s' does not exist" % origdir)
            offbase_orig = len(origdir + os.path.sep)
            for fmapname in ("tracked", "ignored"):
                new_fmap = {}
                for loc, orig in self.__dict__[fmapname].iteritems():
                    if loc != orig:
                        # No globbing for explicite pairs.
                        new_fmap[loc] = orig
                    else:
                        locs = glob.glob(os.path.join(basedir, loc))
                        locs = [x[offbase_loc:] for x in locs]
                        origs = glob.glob(os.path.join(origdir, orig))
                        origs = [x[offbase_orig:] for x in origs]
                        all = list(set(locs).union(set(origs)))
                        for path in all:
                            new_fmap[path] = path
                self.__dict__[fmapname] = new_fmap

        # Automatically resolve VCS if requested.
        if try_srcvcs_auto:
            srcvcs_id = Vcs_data.detect_vcs_id_from_path(self.srcroot)
            if srcvcs_id is None:
                error("cannot detect supported VCS from '%s'" % self.srcroot)
            self.srcvcs = Vcs_data(srcvcs_id)
            #print "srcvcs_id=%s" % srcvcs_id
        if try_locvcs_auto:
            locvcs_id = Vcs_data.detect_vcs_id_from_path(self.owndir)
            if locvcs_id is None:
                error("cannot detect supported VCS from '%s'" % self.owndir)
            self.locvcs = Vcs_data(locvcs_id)
            #print "locvcs_id=%s" % locvcs_id

        # There should be no bundle VCS for in-source boundle.
        if not self.srcroot:
            if locvcs_read:
                warning("bundle VCS ignored in in-source boundles", filepath)
            self.locvcs = self.srcvcs

        # If tracking by subdir and greedy mode,
        # automatically engage tracking only started subdirectories.
        if self.greedy and self.trbysubdir:
            self.gronlystarted = True

        # Collect paths of UI catalogs if requested.
        uicatroots = filter(bool, (self.uicatroot,
                                   self.uicatrootalt, self.uicatrootalt2))
        if uicatroots and not HAS_POLOGY:
            warning("bundle requires tracking UI text by PO files, "
                    "but Pology module not present", filepath)
            self.uicatroot = ""
            self.uicatrootalt = ""
            self.uicatrootalt2 = ""
        self.uicatpaths = []
        for uicatroot in uicatroots:
            catsearchpath = os.path.join(options.srctop, uicatroot)
            if not os.path.isdir(catsearchpath):
                error("UI catalog root directory '%s' does not exist"
                      % catsearchpath)
            catpaths = collect_catalogs([catsearchpath])
            uicatpaths = {}
            for catpath in catpaths:
                catname = None
                if self.uicatbylang:
                    catpdir = os.path.basename(os.path.dirname(catpath))
                    if not catpdir:
                        warning("strange UI catalog path '%s' in "
                                "by-language collection of catalogs, "
                                "skipping it" % catpath, filepath)
                    else:
                        catname = catpdir
                else:
                    catfile = os.path.basename(catpath)
                    catname = catfile[:catfile.rfind(".")]

                if catname not in uicatpaths:
                    uicatpaths[catname] = []
                uicatpaths[catname].append(catpath)
            self.uicatpaths.append(uicatpaths)

        # Create msgstr filters for UI catalogs if requested.
        if self.uimsgstrfspecs and not HAS_POLOGY:
            warning("Bundle requires filtering UI text in PO files, "
                    "but Pology module not present.", filepath)
        self.uimsgstrfs = [get_hook_ireq(x, abort=True)
                           for x in self.uimsgstrfspecs]


    def _parse_path (self, pathspec, filepath, lineno):

        pathspec_els = pathspec.strip().split(";")
        path = None
        for pathspec1 in pathspec_els:
            if not pathspec1:
                continue
            spec_conc = pathspec1.startswith("!")
            spec_repl = ":" in pathspec1
            spec_pins = pathspec1.startswith("^")
            if sum((spec_conc, spec_repl, spec_pins)) > 1:
                spec_descs = []
                if spec_conc:
                    spec_descs.append("concatenation")
                if spec_repl:
                    spec_descs.append("substring replacement")
                if spec_pins:
                    spec_descs.append("parent directory insertion")
                error("the path specification element '%s' "
                      "mixes different specification types: %s."
                      % (pathspec1, ", ".join(spec_descs)),
                      filepath, lineno)
            lbdir = os.path.normpath(os.path.join(os.getcwd(), self.owndir))
            if spec_conc:
                if path is None:
                    path = lbdir
                path = os.path.join(path, pathspec1)
            elif spec_repl:
                if path is None:
                    path = lbdir
                lst = [x.strip() for x in pathspec1.split(":")]
                if len(lst) != 2:
                    error("the path specification element '%s' "
                          "does not contain exactly one colon"
                          % (pathspec1),
                          filepath, lineno)
                sfind, srepl = lst
                if sfind not in path:
                    error("the path specification element '%s' "
                          "states substring to replace '%s' which "
                          "does not exist in the path assembled so far '%s'"
                          % (pathspec1, sfind, path),
                          filepath, lineno)
                path = path.replace(sfind, srepl, 1)
            elif spec_pins:
                if path is None:
                    error("the path specification element '%s' "
                          "cannot be the first in sequence"
                          % (pathspec1),
                          filepath, lineno)
                num_pins_str = pathspec1[1:]
                try:
                    num_pins = int(num_pins_str)
                    assert num_pins > 0
                except (ValueError, AssertionError) as e:
                    error("the path specification element '%s' "
                          "contains invalid number of parent directories "
                          "to insert '%s'"
                          % (pathspec1, num_pins_str),
                          filepath, lineno)
                if not os.path.exists(path):
                    path_els = path.split(os.path.sep)
                    path = None
                    # Try inserting from the back.
                    for i in range(len(path_els) - 1, 0, -1):
                        for k in range(1, num_pins + 1):
                            glob_els = path_els[:i] + ["*"] * k + path_els[i:]
                            glob_path = os.path.sep.join(glob_els)
                            found_paths = glob.glob(glob_path)
                            if len(found_paths) == 1:
                                path = found_paths[0]
                                break
                        if path is not None:
                            break
                if path is None:
                    error("the path specification element '%s' "
                          "does not result in an existing path "
                          "after all paths with inserted parents "
                          "have been tried"
                          % (pathspec1),
                          filepath, lineno)
            else:
                path = pathspec1
            # Normalization important for stripping common path-prefixes later.
            path = os.path.normpath(path)

        return path


    def _parse_fmap (self, mapstr, key, filepath, lineno):

        fmap = {}
        mapstr += " " # sentry
        cpath = [""]
        inpair = False
        inpath = False
        escaped = False
        for c in mapstr:
            if escaped:
                escaped = False
                cpath[-1] += c
            elif inpath:
                if c == "\\":
                    escaped = True
                elif c.isspace():
                    inpath = False
                    # Do not try to resolve globs here.
                    fmap[cpath[0]] = cpath[0]
                    cpath = [""]
                else:
                    cpath[-1] += c
            elif inpair:
                if c == "\\":
                    escaped = True
                elif c == ")":
                    if not cpath[-1]:
                        cpath.pop()
                    if len(cpath) != 2:
                        error("wrong number of paths in mapping pair",
                              filepath, lineno)
                    fmap[cpath[0]] = cpath[1]
                    inpair = False
                    cpath = [""]
                elif c.isspace():
                    cpath.append("")
                else:
                    cpath[-1] += c
            else:
                if c == "(":
                    inpair = True
                elif not c.isspace():
                    inpath = True
                    if c == "\\":
                        escaped = True
                    else:
                        cpath = [c]

        return fmap


    def _parse_bool (self, strval, filepath, lineno):

        strval = strval.lower()
        if strval in ("0", "no", "false", "no_way_hose"):
            return False
        else:
            return True


    def _parse_int (self, strval, filepath, lineno):

        try:
            val = int(strval)
        except:
            error("expected integer value, got '%s'" % strval,
                  filepath, lineno)
        return val


# Bundle track states.
BTR_S_UNDEF    = "undef"
BTR_S_OK       = "ok"
BTR_S_FUZZY    = "fuzzy"
BTR_S_OBSOLETE = "obsolete"
BTR_S_MISSING  = "missing"
BTR_STATES = (BTR_S_UNDEF, BTR_S_OK, BTR_S_FUZZY, BTR_S_OBSOLETE, BTR_S_MISSING)
BTR_MAXLEN = max([len(x) for x in BTR_STATES])

# File name markers.
FNM_STATES = {
    BTR_S_OK       : "",
    BTR_S_FUZZY    : "~fuzzy",
    BTR_S_OBSOLETE : "~obsolete",
    BTR_S_MISSING  : "",
}

def filename_by_state (filename, state):

    if state not in FNM_STATES:
        error("unknown state for filename '%s'" % state)
    for fntag in FNM_STATES.itervalues():
        filename = filename.replace(fntag, "")
    p = filename.rfind(".")
    if p >= 0:
        filename = filename[:p] + FNM_STATES[state] + filename[p:]
    else:
        filename = filename + FNM_STATES[state]
    return filename


# Quote string, improbable as part of expected strings.
QT_STR = u"¦"
QT_LEN = len(QT_STR)


# Data handler for bundle tracking items.
class Bundle_track_item:

    def __init__ (self, filepath="", checksum="", revision=""):

        self.state = BTR_S_UNDEF
        self.filepath = filepath
        self.checksum = checksum
        self.revision = revision


# Data handler for bundle tracking files.
class Bundle_track:

    def __init__ (self, bspec):

        self.bspec = bspec
        self.trfilepaths = []

        self._oldlines = {}
        self._items = {}

        if not bspec.trbysubdir:
            trfilepath = os.path.join(bspec.owndir, TRACK_FILENAME)
            if os.path.exists(trfilepath):
                self._parse_track(trfilepath)
                self.trfilepaths.append(trfilepath)
        else:
            for root, dirs, files in os.walk(bspec.owndir):
                if TRACK_FILENAME in files:
                    trfilepath = os.path.join(root, TRACK_FILENAME)
                    basepath = root[len(bspec.owndir + os.path.sep):]
                    self._parse_track(trfilepath, basepath)
                    self.trfilepaths.append(trfilepath)

    def _parse_track (self, filepath, basepath=""):

        oldlines = []
        self._oldlines[filepath] = oldlines

        # Parse items from the file.
        ifl = codecs.open(filepath, "r", "UTF-8")
        lineno = 0
        for line in ifl.readlines():
            lineno += 1
            oldlines += [line[:-1]]
            line = line[:line.find("#")]
            if not line:
                continue

            item = Bundle_track_item()

            # Get current state.
            line = line.strip() + " "
            tok, line = line.split(" ", 1)
            if tok not in BTR_STATES:
                error("unknown state '%s'", tok, filepath, lineno)
            item.state = tok

            # Get tracked file path.
            line = line.strip() + " "
            if not line:
                error("missing file path", filepath, lineno)
            p1 = line.find(QT_STR)
            p2 = line.find(QT_STR, p1 + QT_LEN)
            if p1 != 0 or p2 < 0:
                error("underquoted file path", filepath, lineno)
            item.filepath = os.path.join(basepath, line[p1+QT_LEN:p2])
            line = line[p2+QT_LEN:]

            # Get tracked file checksum.
            line = line.strip() + " "
            if not line:
                error("missing source checksum", filepath, lineno)
            item.checksum, line = line.split(" ", 1)

            # Get tracked file revision, if any.
            line = line.strip() + " "
            if line:
                item.revision, line = line.split(" ", 1)

            # Assert nothing else in the line:
            line = line.strip()
            if line:
                error("junk towards end of line: %s" % line, filepath, lineno)

            if item.filepath in self._items:
                error("repeated file path '%s' while reading bundle track "
                      "file '%s'" % item.filepath, filepath, lineno)
            self._items[item.filepath] = item

        ifl.close()

    def __contains__ (self, filepath):

        return filepath in self._items

    def __getitem__ (self, filepath):

        return self._items[filepath]

    def __iter__ (self):

        return self._items.itervalues()

    def add (self, item):

        if item.filepath in self._items:
            error("trying to add repeated file path '%s' into "
                  "bundle track at '%s'" % (item.filepath, self.path))
        self._items[item.filepath] = item

    def pop (self, item):

        if item.filepath not in self._items:
            error("trying to pop non-existant file path '%s' out of "
                  "bundle track at '%s'" % (item.filepath, self.path))
        self._items.pop(item.filepath)

    def chpath (self, oldpath, newpath):

        if oldpath not in self._items:
            error("trying to change path of non-present file path '%s' from "
                  "bundle track at '%s'" % (oldpath, self.path))
        if newpath in self._items:
            error("trying to change path of file '%s' into already existing "
                  "path '%s' in the bundle track at '%s'"
                  % (oldpath, newpath, self.path))
        item = self._items.pop(oldpath)
        item.filepath = newpath
        self._items[newpath] = item

    def sync (self):

        # Order data for output.
        trfilepaths = {} # dict while collecting, for membership checks
        langs = {}
        items = {}
        for item in self._items.itervalues():
            lang, ufilepath = bundle_split(item.filepath)
            subdir = os.path.dirname(ufilepath)
            if self.bspec.trbysubdir:
                trfilepath = os.path.join(self.bspec.owndir, subdir,
                                          TRACK_FILENAME)
            else:
                trfilepath = os.path.join(self.bspec.owndir, TRACK_FILENAME)

            if trfilepath not in trfilepaths:
                trfilepaths[trfilepath] = True
                langs[trfilepath] = []
                items[trfilepath] = {}
            if lang not in langs[trfilepath]:
                langs[trfilepath].append(lang)
                items[trfilepath][lang] = []
            items[trfilepath][lang].append(item)

        # Sort everything.
        trfilepaths = trfilepaths.keys()
        trfilepaths.sort()
        for trfilepath in trfilepaths:
            langs[trfilepath].sort()
            for citems in items[trfilepath].itervalues():
                citems.sort(lambda x, y: cmp(x.filepath, y.filepath))

        # NOTE: Do *not* try to make equal-width columns for filenames, etc.
        # This would change all lines when a file is added or removed,
        # producing useless version control deltas.

        # Format string for entries.
        ifmt = ""
        ifmt += "%-" + str(BTR_MAXLEN) + "s  " # state
        ifmt += QT_STR + "%s" + QT_STR + "  " # filepath
        ifmt += "%s  " # cheksum
        ifmtr = ifmt
        ifmtr += "%s  " # revision
        ifmtr = ifmtr.rstrip()

        # Output to files.
        changes = False
        lines_by_file = {}
        modified_files = {}
        for trfilepath in trfilepaths:
            lines = []
            lines += ["# Do not edit manually, "
                      "except to remove complete lines."]
            lines += [""]
            for lang in langs[trfilepath]:
                if lang:
                    lines += ["# %s" % lang]
                elif len(langs[trfilepath]) > 1: # avoid in monolingual bundle
                    lines += ["# -"]
                prevdir = None
                for item in items[trfilepath][lang]:
                    mod_filepath = item.filepath
                    if self.bspec.trbysubdir:
                        mod_filepath = os.path.basename(mod_filepath)
                    currdir = os.path.dirname(mod_filepath)
                    if prevdir is not None and prevdir != currdir:
                        if not self.bspec.tighttrack:
                            lines += [""]
                    prevdir = currdir

                    if item.revision:
                        lines += [ifmtr % (item.state, mod_filepath,
                                           item.checksum, item.revision)]
                    else:
                        lines += [ifmt % (item.state, mod_filepath,
                                          item.checksum)]
                lines += [""]

            # Write file if anything changed.
            lines_by_file[trfilepath] = lines
            if lines != self._oldlines.get(trfilepath, None):
                ofl = codecs.open(trfilepath, "w", "UTF-8")
                ofl.write("\n".join(lines))
                ofl.write("\n")
                ofl.close()
                modified_files[trfilepath] = True
                changes = True

        # VCS ops on tracking files.
        # - remove tracking files no longer present
        for trfilepath in self.trfilepaths:
            if trfilepath not in lines_by_file:
                if self.bspec.locvcs.is_versioned_file(trfilepath):
                    self.bspec.locvcs.remove_file(trfilepath)
                else:
                    unlink(trfilepath)
        # - add all new tracking files under VCS and inform of any modified
        for trfilepath in trfilepaths:
            if not self.bspec.locvcs.is_versioned_file(trfilepath):
                self.bspec.locvcs.add_file(trfilepath)
            elif trfilepath in modified_files:
                self.bspec.locvcs.say_modified_file(trfilepath)

        self.trfilepaths = trfilepaths
        self._oldlines = lines_by_file

        return changes


# Data handler for collecting check statistics.
class Check_stats:

    def __init__ (self):

        self.btracks = []
        self.new_state = {
            BTR_S_OK : [],
            BTR_S_FUZZY : [],
            BTR_S_OBSOLETE : [],
            BTR_S_MISSING : [],
        }


def check_bundles_in_dir (path, cstats, options, top=True):

    path = os.path.normpath(path)

    # Do not walk through the whole tree here,
    # check_bundle will do that for each dir that contains a bundle.

    # If this is the entry point of the recursion,
    # check if the directory is covered by a spec upward in the tree.
    onlysubdir = ""
    if top:
        abspath = os.path.abspath(path)
        parent = abspath
        while True:
            nparent = os.path.dirname(parent)
            if nparent == parent:
                break
            parent = nparent
            if os.path.isfile(os.path.join(parent, SPEC_FILENAME)):
                onlysubdir = abspath[len(parent + os.path.sep):]
                path = parent
                break

    files = []
    subdirs = []
    for item in os.listdir(path):
        itempath = os.path.join(path, item)
        if os.path.isfile(itempath):
            files.append(item)
        elif os.path.isdir(itempath):
            subdirs.append(item)

    if SPEC_FILENAME in files:
        # A bundle dir.

        # Load the bundle spec.
        bspec = Bundle_spec(os.path.join(path, SPEC_FILENAME), options)

        # Load or create bundle track.
        btrack = Bundle_track(bspec)
        cstats.btracks.append(btrack) # to sync them at the end

        # Collect all files present in bundle dir (or subdir).
        filepaths = []
        bdir = os.path.join(bspec.owndir, onlysubdir)
        for root, dirs, files in os.walk(bdir):
            for file in files:
                if file not in (SPEC_FILENAME, TRACK_FILENAME):
                    filepaths.append(os.path.join(root, file))

        # In greedy mode, collect all files from the original dir,
        # and for each not present in the bundle dir,
        # construct a "virtual" bundled counterpart for each language.
        # Such files will later be declared missing.
        if bspec.greedy:
            filepaths.extend(greedy_collect_files(filepaths, bspec, options,
                                                  onlysubdir))

            # Collect all files present in the track
            # (may have missing files in greedy mode).
            # Do not add before collection from original dir,
            # not to prevent adding of bundled virtuals.
            for item in btrack:
                if item.filepath.startswith(onlysubdir):
                    filepath = os.path.join(bspec.owndir, item.filepath)
                    if filepath not in filepaths:
                        filepaths.append(filepath)

        # Check bundled files.
        filepaths.sort()
        check_bundle(filepaths, bspec, btrack, cstats, options)

    else:
        # Not a bundle dir, continue search recursively.
        for subdir in subdirs:
            dirpath = os.path.join(path, subdir)
            check_bundles_in_dir(dirpath, cstats, options, False)


def check_bundle (filepaths, bspec, btrack, cstats, options):

    for filepath in filepaths:

        # Relative path, from bundle spec file to here.
        bfilepath = filepath[len(bspec.owndir) + len(os.path.sep):]

        # Ignore files for version control bookkeeping.
        if bspec.locvcs.is_own_file(filepath):
            continue

        # Split file path into language code and non-bundled path.
        # If the file is not bundled, lang is empty.
        lang, dummy = bundle_split(bfilepath)

        # Check bundling, possibly skipping this file.
        if bspec.srcroot:
            # Out-of-source bundle.
            # Make sure all unbundled files are either
            # explicitely tracked or explicitely ignored,
            # or that greedy mode is engaged.
            if not lang:
                if (    bfilepath not in bspec.tracked
                    and bfilepath not in bspec.ignored
                    and not bspec.greedy
                ):
                    warning("non-tracked non-ignored file '%s' "
                            "in out-of-source bundle" % filepath)
                    continue
            # Skip ignored files.
            if bfilepath in bspec.ignored:
                continue
        else:
            # In-source bundle.
            # Skip any non-bundled file.
            if not lang:
                continue

        # Warn about and skip bundled files of non-cleared language,
        # if the languages have been constrained in the spec.
        if lang and bspec.languages and lang not in bspec.languages:
            warning("bundled file '%s' for non-cleared language '%s', "
                    "skipped" % (filepath, lang))
            continue

        # Skip bundled file if options limit the languages to check.
        if lang and options.only_lang and lang not in options.only_lang:
            continue

        # Check the file.
        check_file_in_bundle(filepath, bfilepath, bspec, btrack,
                             cstats, options)


# Parse the language and non-bundled path for the bundled file path (tuple).
# Return empty language string if the filepath is not in bundle form.
def bundle_split (filepath, ignsubs=[]):

    filepath = os.path.normpath(filepath)
    file = os.path.basename(filepath)
    pardir = os.path.dirname(filepath)

    # Language.
    lang = os.path.basename(pardir)
    pardir = os.path.dirname(pardir)
    if not lang or not pardir:
        return ("", filepath)

    # Check if really bundled path.
    ldir = os.path.basename(pardir)
    pardir = os.path.dirname(pardir)
    if ldir != L10N_DIRNAME:
        return ("", filepath)

    # Remove ignored substrings.
    for ignsub in ignsubs:
        file = file.replace(ignsub, "")

    return (lang, os.path.join(pardir, file))


def check_file_in_bundle (filepath, bfilepath, bspec, btrack, cstats, options):

    # Split relative path within bundle into language
    # and relative path of original file.
    lang, borigpath = bundle_split(bfilepath, bspec.ignsubs)

    # Determine full path to the original file.
    if bspec.srcroot:
        # Out-of-source bundle: join top source dir given in the command line,
        # relative source dir, and relative path within bundle.
        if lang:
            origpath = os.path.join(options.srctop, bspec.srcroot, borigpath)
        else:
            # Unbundled file in bundle dir, use remapping of its relative path.
            if not bspec.greedy:
                if borigpath not in bspec.tracked:
                    error("internal: non-bundled non-tracked file '%s' "
                          "strayed into check" % filepath)
                origpath = os.path.join(options.srctop, bspec.srcroot,
                                        bspec.tracked[borigpath])
            else:
                origpath = os.path.join(options.srctop, bspec.srcroot,
                                        bspec.tracked.get(borigpath, borigpath))
    else:
        # In-source bundle: the relative path within bundle is the one.
        origpath = os.path.join(bspec.owndir, borigpath)

    # The path may have a state marker in strict mode; remove for original.
    if bspec.strict:
        origpath = filename_by_state(origpath, BTR_S_OK)

    # Update the original file from version control if requested.
    if options.update_src:
        bspec.srcvcs.update_file(origpath)

    # If a more actual version exists, remove this file
    # and stop further processing.
    for state in (BTR_S_OK, BTR_S_FUZZY, BTR_S_OBSOLETE):
        ofilepath = filename_by_state(filepath, state)
        if filepath == ofilepath:
            # Reached current version.
            break

        if os.path.isfile(ofilepath):
            # If the more actual file is not versioned and the current is,
            # make the more actual inherit the history of the current.
            # Otherwise, just remove the current.
            if (    bspec.locvcs.is_versioned_file(filepath)
                and not bspec.locvcs.is_versioned_file(ofilepath)
            ):
                shutil.move(ofilepath, filepath)
                bspec.locvcs.move_file(filepath, ofilepath)
            else:
                bspec.locvcs.revert_file(filepath)
                if bspec.locvcs.is_versioned_file(filepath):
                    bspec.locvcs.remove_file(filepath)
                else:
                    check_system(["rm", filepath], echo=True)
            if bfilepath in btrack:
                btrack.pop(btrack[bfilepath])
            return

    # If the bundled file is not yet tracked, add it.
    checksum_computed = False
    if bfilepath not in btrack:
        if os.path.isfile(origpath):
            checksum = file_checksum(origpath)
            revision = bspec.srcvcs.get_revision(origpath)
            checksum_computed = True
        else:
            checksum = "0"
            revision = "0"
        btrack.add(Bundle_track_item(bfilepath, checksum, revision))

    # If neither bundled nor original file exists (possible in greedy mode),
    # remove bundled from tracking.
    # Stop further processing.
    if not os.path.isfile(filepath) and not os.path.isfile(origpath):
        btrack.pop(btrack[bfilepath])
        return

    # If there is existing unbundled counterpart to a missing bundled file
    # (possible in greedy mode) remove it from tracking.
    # Stop further processing.
    ufilepath = os.path.join(bspec.owndir, borigpath)
    if not os.path.isfile(filepath) and os.path.isfile(ufilepath):
        btrack.pop(btrack[bfilepath])
        return

    # If the local file is unbundled and does not exist (greedy mode),
    # remove it from tracking if not monolingual bundle
    # (bundled counterparts will become missing).
    # Stop further processing.
    if not os.path.isfile(filepath) and not lang and not bspec.grmonoling:
        btrack.pop(btrack[bfilepath])
        return

    # If the local file does not exist (greedy mode) and is flagged
    # with dirty-state (strict mode), remove it from tracking
    # (clear-state counterpart will become missing).
    # Stop further processing.
    cfilepath = filename_by_state(filepath, BTR_S_OK)
    if not os.path.isfile(filepath) and cfilepath != filepath:
        btrack.pop(btrack[bfilepath])
        return

    # If the bundled file does not exists (greedy mode) make it missing.
    # Stop further processing.
    if not os.path.isfile(filepath):
        set_file_state(filepath, bfilepath, origpath, bspec, btrack, cstats,
                       options, BTR_S_MISSING)
        return

    # If the original file no longer exists, make the bundled obsolete.
    # Stop further processing.
    if not os.path.isfile(origpath):
        set_file_state(filepath, bfilepath, origpath, bspec, btrack, cstats,
                       options, BTR_S_OBSOLETE)
        return

    # Compute checksum of the original file, if not already done above.
    if not checksum_computed:
        checksum = file_checksum(origpath)

    # Check for text mismatch (po-track mode).
    text_matches = True
    if bspec.uicatroot or bspec.uicatrootalt:
        text_matches = text_track_clear(filepath, bspec)

    # If the file was missing until now, assign the current checksum.
    if btrack[bfilepath].state == BTR_S_MISSING:
        btrack[bfilepath].checksum = checksum

    # Compare the current checksum of the original file with the recorded.
    # If they differ, make the bundled file fuzzy.
    # Also make it fuzzy if text is not matching (po-track mode).
    # Stop further processing.
    if btrack[bfilepath].checksum != checksum or not text_matches:
        set_file_state(filepath, bfilepath, origpath, bspec, btrack, cstats,
                       options, BTR_S_FUZZY)
        return

    # All checks passed, bundled file in pristine state.
    set_file_state(filepath, bfilepath, origpath, bspec, btrack, cstats,
                   options, BTR_S_OK)


def set_file_state (filepath, bfilepath, origpath, bspec, btrack, cstats,
                    options, state):

    if bspec.strict and state != BTR_S_MISSING:
        nbfilepath = filename_by_state(bfilepath, state)
        if nbfilepath != bfilepath:
            nfilepath = filename_by_state(filepath, state)
            btrack.chpath(bfilepath, nbfilepath)
            bspec.locvcs.revert_file(filepath)
            if bspec.locvcs.is_versioned_file(filepath):
                bspec.locvcs.move_file(filepath, nfilepath)
            else:
                check_system(["mv", filepath, nfilepath], echo=True)
            filepath = nfilepath
            bfilepath = nbfilepath

    if btrack[bfilepath].state != state:
        cstats.new_state[state].append((filepath, origpath,
                                        btrack[bfilepath].revision))
    btrack[bfilepath].state = state

    if state != BTR_S_MISSING and not bspec.locvcs.is_versioned_file(filepath):
        bspec.locvcs.add_file(filepath)


checksum_cache = {}

def file_checksum (filepath):

    checksum = checksum_cache.get(filepath)
    if checksum is None:
        checksum = hashlib.md5(open(filepath, "rb").read()).hexdigest()
        checksum_cache[filepath] = checksum

    return checksum


def greedy_collect_files (filepaths, bspec, options, onlysubdir=""):

    # The level of subdirectory within the bundle.
    sublevel = lambda r: len([x for x in r.split(os.path.sep) if x])

    extra_filepaths = []

    if bspec.strict:
        # In strict mode, replace each state-flagged path with a clear one,
        # to avoid considering it missing when collecting from originals.
        clear_filepaths = []
        for filepath in filepaths:
            clear_filepaths.append(filename_by_state(filepath, BTR_S_OK))
        filepaths = clear_filepaths

    if bspec.srcroot:
        # Out-of-source bundle.
        specdir = os.path.join(options.srctop, bspec.srcroot)
        origdir = os.path.join(specdir, onlysubdir)
        for root, dirs, files in os.walk(origdir):
            # Root relative to spec file.
            striplen = len(specdir) + len(os.path.sep)
            broot = root[striplen:]
            bdir = os.path.join(bspec.owndir, broot)

            # Skip VCS bookkeeping directories.
            pdirs = set(broot.split(os.path.sep))
            if pdirs.intersection(bspec.locvcs.ignore_dirs):
                continue
            # Skip files above greed level.
            if sublevel(broot) < bspec.grlevel:
                continue
            # Skip files if their complete subdir does not
            # exist in the bundle yet, if requested by spec.
            if bspec.gronlystarted and not os.path.isdir(bdir):
                continue

            for file in files:
                # Skip ignored files.
                bfilepath = os.path.join(broot, file)
                if bfilepath in bspec.ignored:
                    continue

                if not bspec.grmonoling:
                    for lang in bspec.languages:
                        fp1 = os.path.join(bspec.owndir, broot, file)
                        fp2 = os.path.join(bspec.owndir, broot,
                                           L10N_DIRNAME, lang, file)
                        if fp1 not in filepaths and fp2 not in filepaths:
                            extra_filepaths.append(fp2)
                else:
                    fp1 = os.path.join(bspec.owndir, broot, file)
                    if fp1 not in filepaths:
                        extra_filepaths.append(fp1)

    else:
        # In-source bundle.
        for filepath in filepaths:
            # Root and path relative to spec file.
            striplen = len(bspec.owndir) + len(os.path.sep)
            bfilepath = filepath[striplen:]
            broot = os.path.dirname(bfilepath)

            # Skip VCS bookkeeping directories.
            pdirs = set(broot.split(os.path.sep))
            if pdirs.intersection(bspec.locvcs.ignore_dirs):
                continue
            # Skip files above greed level.
            if sublevel(broot) < bspec.grlevel:
                continue
            # Skip ignored files.
            if bfilepath in bspec.ignored:
                continue

            lang, dummy = bundle_split(bfilepath)
            if not lang:
                if not bspec.grmonoling:
                    for lang in bspec.languages:
                        fp2 = os.path.join(bspec.owndir, broot,
                                           L10N_DIRNAME, lang, file)
                        extra_filepaths.append(fp2)
                else:
                    fp1 = os.path.join(bspec.owndir, broot, file)
                    extra_filepaths.append(fp1)

    return extra_filepaths


tt_uicats = {}
tt_last_dirname = ""

def text_track_clear (filepath, bspec):

    global tt_uicats
    global tt_last_dirname

    dirname = os.path.dirname(filepath)
    popath = filepath
    pdot = popath.rfind(".")
    if pdot >= 0:
        popath = popath[:pdot]
    popath += ".po"
    if not os.path.isfile(popath):
        # Missing counterpart PO means there is no text to the resource.
        return True

    # Add to version control if not there.
    if not bspec.locvcs.is_versioned_file(popath):
        bspec.locvcs.add_file(popath)

    # Open the tracking catalog and collect names of dependent UI catalogs.
    trcat = Catalog(popath)
    uicatnames = []
    for field in trcat.header.select_fields("X-Associated-UI-Catalogs"):
        lst = field[1].split()
        if not lst: # skip empty fields
            continue
        # The order of collecting catalog names is important;
        # earlier catalogs have higher priority when searching for messages.
        for uicatname in lst:
            if uicatname not in uicatnames:
                uicatnames.append(uicatname)
    if len(uicatnames) == 0:
        warning("text tracking catalog states no UI catalogs", filepath)
        return True

    # Clear old UI catalogs if directory switch and over threshold.
    if tt_last_dirname != dirname:
        maxmsg = 10000
        nmsg = reduce(lambda s, cat: s + len(cat), tt_uicats.values(), 0)
        if nmsg > maxmsg:
            tt_uicats = {}
    tt_last_dirname = dirname

    # Collect UI catalogs and open those which were not opened already.
    uicats = []
    for uicatname in uicatnames:
        # Look for this catalog in roots by priority.
        for uicatpaths in bspec.uicatpaths:
            catpaths = uicatpaths.get(uicatname)
            if catpaths:
                break
        if catpaths is None:
            warning("UI catalog '%s' requested, but not present among "
                    "collected UI catalogs" % uicatname, filepath)
            return True
        for catpath in catpaths:
            if catpath not in tt_uicats:
                tt_uicats[catpath] = Catalog(catpath, monitored=False)
            uicats.append(tt_uicats[catpath])

    # Try to find and match each tracking message from the tracking catalog
    # in one of the dependent UI catalogs.
    # If the tracking message is not found in UI catalogs,
    # flag the message as not present in UI catalogs;
    # do not make it obsolete, as someone may remove obsolete entries
    # as a matter of course; also to preserve manual ordering.
    # If the message is found in UI catalogs,
    # but its translation is not the same as in the tracking catalog,
    # flag it as fuzzy.
    uptodate = True
    for msg in trcat:
        # Check if present in UI catalogs
        omsg = None
        for uicat in uicats:
            if msg in uicat:
                omsg = uicat[msg]
                break
        f_obsolete = u"obsolete"
        if omsg is None:
            msg.flag.add(f_obsolete)
            uptodate = False
            continue
        msg.flag.remove(f_obsolete)

        # Check if translations match.
        fmsgstr = list(msg.msgstr)
        fomsgstr = list(omsg.msgstr)
        for flt in bspec.uimsgstrfs:
            fmsgstr = map(flt, fmsgstr)
            fomsgstr = map(flt, fomsgstr)
        f_fuzzy = u"fuzzy"
        if fmsgstr != fomsgstr:
            msg.flag.add(f_fuzzy)
            uptodate = False
            continue
        msg.flag.remove(f_fuzzy)

    if trcat.sync():
        bspec.locvcs.say_modified_file(popath)

    return uptodate


if __name__ == '__main__':
    main()

