# -*- coding: utf-8 -*-

import re

from pology.normalize import canonical_header
from pology.proj.kde.header import equip_header as equip_header_tp_kde


def setup_common_extras (S, langcode, langname, teamaddr):

    # Set local merging for all branches.
    for branch in S.branches:
        branch["merge"] = True

    # Do not make fuzzy messages when merging branch catalogs.
    S.branches_fuzzy_merging = False
    
    # Compendium to use when merging summit catalogs.
    S.compendium_on_merge = S.relpath("../../nn/skript/kompendium/%s.po" % S.lang)
    S.compendium_fuzzy_exact = True
    S.merge_rebase_fuzzy = True
    S.merge_min_adjsim_fuzzy=.6

    # ----------------------------------------
    # Formatting.

    # Wrap messages?
    S.summit_wrap = False
    S.branches_wrap = True

    # Fine-wrap messages (on markup tags, etc.)?
    S.summit_fine_wrap = True
    S.branches_fine_wrap = False

    # ----------------------------------------
    # Bookkeeping.

    # Version control system for the catalogs, if any.
    S.version_control = "svn"
    
    S.hook_on_merge_cat.extend([
        # Remove obsolete messages from branch catalogs.
        (remove_obsolete, lambda bid: bid != "+"),
    ])
    
    S.hook_on_merge_head.extend([
        # Clean up the PO header
        (build_cleanup_header_hook(langcode, langname, teamaddr),),
        
        # Reorder/normalise the authors list
        (canonical_header,),
        
        # Set some common headers
        (equip_header_tp_kde,),
    ])

    # Do some search and replace stuff for Norwegian Nynorsk
    if langcode == "nn":
        S.hook_on_scatter_msgstr.extend([
            (formfeil_nn,),
        ])


# Hook factory for search and replace stuff
def build_formfeil_hook (pairs):

    compiled_pairs = []
    for rxstr, subst in pairs:
        regex = re.compile(rxstr, re.U)
        compiled_pairs.append((regex, subst))

    def hook (msgstr, msg, cat):
        for regex, subst in compiled_pairs:
            msgstr = regex.sub(subst, msgstr)
        return msgstr

    return hook


# Clean up the PO header by adding/fixing some common headers,
# setting a standard header title, and removing all header
# comments except the authors list
def build_cleanup_header_hook (langcode, langname, teamaddr):

    def cleanup_header (hdr, cat):
        hdr.set_field("Language-Team", "%s <%s>" % (langname, teamaddr))
        hdr.set_field("Plural-Forms", "nplurals=2; plural=n != 1;")
        hdr.title[:] = ['Translation of %s to %s' % (cat.name, langname), '']
        hdr.license = None
        hdr.copyright = None
#        hdr.comment[:] = [] # Currently also removes SPDX-FileCopyrightText
        return 0
    
    return cleanup_header


# Search and replace regexps for Norwegian Nynorsk
formfeil_nn = build_formfeil_hook((
    (r"\bmotta\b", r"ta imot"),
    (r"\bmottek\b", r"tek imot"),
    (r"\bmottok\b", r"tok imot"),
    (r"\bMotta\b", r"Ta imot"),
    (r"\bMottek\b", r"Tek imot"),
    (r"\bMottok\b", r"Tok imot"),
))


# Remove obsolete messages
def remove_obsolete (cat):

    for msg in cat:
        if msg.obsolete:
            cat.remove_on_sync(msg)
