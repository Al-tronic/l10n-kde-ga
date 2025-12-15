#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
N3 files translation script. Only labels and comments are handled for now.

@author: Sébastien Renard <renard@kde.org>
@license: GPL v3 or later

Description:
First, it extracts labels and comments from N3 files
and create GNU Gettext Portable Object Template file (.pot). So, usual translation
process and tools can be used to create translation for each language.

Finally, .po files of each language are process to reinject translated labels
and comment into N3 files.

Caveat: 
- multi line comments are changed into one lined comment during message injection
- only labels and comments are translated for now. 
Ex. : 

 nfo:lineCount
          a       rdf:Property ;
          rdfs:comment "The amount of lines in a text document" ;
          rdfs:domain nfo:TextDocument ;
          rdfs:label "lineCount" ;
          rdfs:label "Nombre de lignes"@fr ;
          rdfs:range xsd:integer .

"""

from optparse import OptionParser
import sys
import os
import re
import codecs
import shutil

try:
    from pology.catalog import Catalog
    from pology.message import Message
    from pology.monitored import Monlist, Monpair
except ImportError:
    print "Pology cannot be found"
    print "Please, get it from KDE trunk (trunk/l10n-support/pology)"
    print "and define PYTHONPATH to it. Ex: export PYTHONPATH=$PYTHONPATH:<kde trunk>/l10n-support/"
    sys.exit(3)

# Some constants
VERSION = 0.1

# RDFS line template
RDFS_LINE_TEMPLATE = u'          rdfs:%s "%s" ;\n'
RDFS_TRANSLATED_LINE_TEMPLATE = u'          rdfs:%s "%s"@%s ;\n'

# Regexp for N3 parsing
RE_RDFS_LABEL = re.compile('\s+rdfs:(label) "(.*?)"@?(\S*)\s?;')
RE_RDFS_COMMENT = re.compile('\s+rdfs:(comment) "(.*?)"@?(\S*)\s?;')
RE_RDFS_MULTILINE_START_COMMENT = re.compile('\s+rdfs:comment """(.+)')
RE_RDFS_MULTILINE_END_COMMENT = re.compile('\s*(.+)"""@?(\S*)\s?;')

def parseN3File(n3FileName):
    """N3 format file parser
        Handle rdfs labels and comments, single and multiline.
        @param n3FileName: name (str) of n3 file to read
        @return:  a generator that gives next rdfs item as a tuple of unicode (type, name, lang, source). 
        type can be "label", "comment" or None
        lang is language in unicode (two letters) or None for template line"""

    inMultiline = False # Flag used to indicate we are parsing a multiline comment
    multiline = "" # String used to concatenate multiline comment
    lineNo = 0 # Line number

    for line in codecs.open(n3FileName, "r", "utf-8"):
        lineNo += 1
        # Multiline preprocessing
        if inMultiline:
            result = RE_RDFS_MULTILINE_END_COMMENT.match(line)
            if result:
                inMultiline = False
                multiline += result.group(1)
                # recreate a single line rdfs comment to be processed the usual way
                if result.group(2):
                    # Add language
                    line = RDFS_TRANSLATED_LINE_TEMPLATE % (u"comment",
                                                            multiline.rstrip(),
                                                            result.group(2))
                else:
                    line = RDFS_LINE_TEMPLATE % (u"comment", multiline.rstrip())
            else:
                # Just add line and jump on next line
                multiline += line.rstrip()
                continue
        else:
            result = RE_RDFS_MULTILINE_START_COMMENT.match(line)
            if result:
                # Just add line and jump on next line
                multiline = result.group(1).rstrip()
                inMultiline = True
                continue

        # Add source reference
        parsedLine = list(matchRdfs(line))
        parsedLine.append((unicode(n3FileName), lineNo))

        # Yield parsed line
        yield parsedLine


def extractMessagesFromN3(n3FileName, potFileName):
    """Create a GNU Gettext Portable Object Template from an N3 file
    @param n3FileName: n3file full path
    @param potFileName: pot full path"""
    if os.path.exists(potFileName):
        os.unlink(potFileName)
    cat = Catalog(potFileName, create=True)
    n3File = parseN3File(n3FileName)
    for (n3Type, n3Name, lang, source) in n3File:
        if not lang and n3Type in ("label", "comment"):
            msg = Message()
            msg.msgid = unicode(n3Name)
            msg.source = Monlist((Monpair(init=source),))
            msg.auto_comment = Monlist((unicode(n3Type),))
            cat.add_last(msg)

    # Write catalog to disk
    cat.sync()

def injectMessagesFromPo(n3FileName, poFileName, lang):
    """inject po message translation into N3 file
    @param n3FileName: n3file full path
    @param poFileName: pot full path"""
    tmpSuffix = ".tmp"
    cat = Catalog(poFileName)
    if not lang:
        # Try to get it from po catalog
        lang = cat.language()
        if not lang:
            print "Language cannot be guessed. Use --lang to set it. Aborting."
            return

    n3File = parseN3File(n3FileName)
    n3NewFile = codecs.open(n3FileName + tmpSuffix, "w", "utf-8")
    for (n3Type, n3Name, n3lang, source) in n3File:
        if not n3Type:
            # non rdfs line. Just write it to new file and continue
            n3NewFile.write(n3Name)
            continue
        if n3lang == lang:
            # translated line in our language. Skip it.
            # New translation is added below
            continue

        # rdfs standard line
        n3NewFile.write(RDFS_LINE_TEMPLATE % (n3Type, n3Name))

        # check for translation 
        for msg in cat.select_by_key(None, n3Name):
            if msg.translated:
                n3NewFile.write(RDFS_TRANSLATED_LINE_TEMPLATE % (n3Type, msg.msgstr[0], lang))
                break

    # Overwrite n3 file with new translated one
    n3NewFile.close()
    shutil.move(n3FileName + tmpSuffix, n3FileName)


def matchRdfs(line, translated=False):
    """Check if line is matching an rdfs label or comment
    @param line: line (str/unicode) to be checked
    @type translated: bool
    @return: tuple (n3type, n3name, lang)"""
    result = RE_RDFS_LABEL.match(line)
    if result:
        return result.groups()
    else:
        result = RE_RDFS_COMMENT.match(line)
        if result:
            return result.groups()
    # Return line unchanged
    return (None, line, None)

def parseOptions():
    """Parses command argument using optparse python module
    @return: (options, argv) tuple"""

    usage = "usage: %prog [options]"
    version = "%%prog %s" % VERSION
    parser = OptionParser(usage=usage, version=version)

    # Timeout
    parser.add_option("-o", "--output", dest="output", type="str",
              help="Output file (.pot if extracting or n3 file if injecting translations)")

    # Extract from N3 files
    parser.add_option("-e", "--extract", dest="extract", type="str",
              help="Extract messages from N3 files")

    # Inject from po file
    parser.add_option("-i", "--inject", dest="inject", type="str",
              help="Inject messages from this .po file")

    # PO file lang
    parser.add_option("-l", "--lang", dest="lang", type="str",
              help="Lang of the .po file")

    # Verbose
    parser.add_option("-v", "--verbose", dest="verbose", action="store_true",
              help="Tell what happened on standard output")

    return parser

def usage():
    parseOptions().print_help(sys.stderr)

def main():
    """Main function, all started here"""
    rc = 2

    # Options & args stuff
    (options, argv) = parseOptions().parse_args()

    if options.extract and options.inject:
        print "You cannot extract and inject at the same time. Choose your camp !"
        usage()

    if options.extract and options.output:
        extractMessagesFromN3(options.extract, options.output)
        rc = 0

    if options.inject and options.output:
        injectMessagesFromPo(options.output, options.inject, options.lang)
        rc = 0

    if rc == 2:
        usage()

    sys.exit(rc)

# Main
if __name__ == "__main__":
    main()
