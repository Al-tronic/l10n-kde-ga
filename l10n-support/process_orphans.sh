#! /usr/bin/env bash

# This script automatizes the moving and deleting of translations with
# the help of the rule file process_orphans.txt

ORPHLIST=${ORPHLIST:-scripts/process_orphans.txt}
if test ! -s $ORPHLIST; then
    echo "ERROR: could not find the process_orphans.txt ($ORPHLIST) rule file! Aborting!"
    exit 1
fi

# Process rules found from this line onvards.
FROMLINE=${1:-0}
if [[ $FROMLINE = -* ]]; then
    numlines=`cat $ORPHLIST | wc -l`
    FROMLINE=$(($numlines + $FROMLINE + 1))
fi

ONLYLANG=$2

# Script is not tested, so force verbose mode:
VERBOSE=yes

if test -n "$ONLYLANG"; then
    subdirs=$ONLYLANG
elif test -f ./inst-apps; then
    subdirs=`cat ./inst-apps`
else
    subdirs=`cat ./subdirs`
fi

# Log all output from VCS operations.
# Do not empty the file here, but add to any existing content.
VCSLOG=vcs_ops.log
echo >>$VCSLOG # ...with an empty line after existing content.

process_command ()
{
    command=$1
    orig=$2
    dest=$3

    for lang in $subdirs; do
        poorig=./$lang/$orig
        podest=./$lang/$dest
        if test $lang = "templates" && test ! -d $poorig; then
            poorig=${poorig}t
            podest=${podest}t
            potfile=
        fi
        potfile="templates/$dest"t
        podestdir=`dirname $podest`

        # Does the rule need to be done manually?
        manual=
        if test -f $poorig || test -d $poorig; then
            test -z "$VERBOSE" || echo "Checking file $poorig..."
            if test -f $poorig -a ! -s $poorig; then
                test -z "$VERBOSE" || echo "$poorig is an empty file. Removing."
                svn remove --force $poorig | tee -a $VCSLOG
            elif test $command = "delete"; then
                test -z "$VERBOSE" || echo "$poorig matches a delete rule. Removing."
                svn remove --force $poorig | tee -a $VCSLOG
            elif test $command = "move" -a ! -e $podest; then
                test -z "$VERBOSE" -a ! -d $poorig || echo "$poorig matches a move rule and $podest does not exist. Moving."
                svn move --parents $poorig $podest | tee -a $VCSLOG
            elif test $command = "copy" -a ! -e $podest; then
                test -z "$VERBOSE" -a ! -d $poorig || echo "$poorig matches a copy rule and $podest does not exist. Copying."
                svn copy --parents $poorig $podest | tee -a $VCSLOG
                if test -f $potfile -a $podest != $potfile; then
                    msgmerge --previous -o $podest $podest $potfile
                    msgattrib --no-obsolete -o $podest $podest
                fi
            elif test $command = "move" -a -f $podest; then
                if test -n "`LC_ALL=C msgfmt -o /dev/null --statistic $poorig 2>&1 |egrep "^0 translated messages"`"; then
                    test -z "$VERBOSE" || echo "$poorig matches a move rule and is untranslated. Removing."
                    svn remove --force $poorig | tee -a $VCSLOG
                else
                    msgfmtnew="`LC_ALL=C msgfmt -o /dev/null --statistic $podest 2>&1`"
                    if test -n "`echo "$msgfmtnew"|fgrep -v "fuzzy"|fgrep -v "untranslated"`"; then
                        test -z "$VERBOSE" || echo "$poorig matches a move rule and $podest is fully translated. Removing $poorig."
                        svn remove --force $poorig | tee -a $VCSLOG
                    elif test -n "`echo "$msgfmtnew"|egrep "^0 translated messages"`"; then
                        test -z "$VERBOSE" || echo "$poorig matches a move rule and $podest is untranslated. Moving."
                        # As svn cannot do a remove followed by a move/cop, we have to do it with mv(1)
                        mv $poorig $podest && svn remove --force $poorig | tee -a $VCSLOG
                    else
                        #manual=yes
                        test -z "$VERBOSE" || echo "$poorig matches a move rule and $podest is partially translated. Merging in and removing $poorig."
                        if test ! -f "$potfile"; then
                            msgcat --use-first -o $podest $podest $poorig && svn status $podest | tee -a $VCSLOG
                        else
                            msgmerge --previous -C $poorig -o $podest $podest $potfile && svn status $podest | tee -a $VCSLOG
                        fi
                        svn remove --force $poorig | tee -a $VCSLOG
                    fi
                fi
            elif test $command = "copy" -a -f $podest; then
                if test -n "`LC_ALL=C msgfmt -o /dev/null --statistic $poorig 2>&1 |egrep "^0 translated messages"`"; then
                    test -z "$VERBOSE" || echo "$poorig matches a copy rule and is untranslated. Doing nothing."
                else
                    msgfmtnew=`LC_ALL=C msgfmt -o /dev/null --statistic $podest 2>&1`
                    if test -n "`echo "$msgfmtnew"|fgrep -v "fuzzy"|fgrep -v "untranslated"`"; then
                        test -z "$VERBOSE" || echo "$poorig matches a copy rule and $podest is fully translated. Doing nothing."
                    elif test -n "`echo "$msgfmtnew"|egrep "^0 translated messages"`"; then
                        test -z "$VERBOSE" || echo "$poorig matches a copy rule and $podest is untranslated. Copying."
                        # As svn cannot do a remove followed by a copy, we have to do it with cp(1)
                        cp $poorig $podest && svn status $podest | tee -a $VCSLOG
                        if test -f $potfile; then
                            msgmerge --previous -o $podest $podest $potfile
                            msgattrib --no-obsolete -o $podest $podest
                        fi
                    else
                        #manual=yes
                        test -z "$VERBOSE" || echo "$poorig matches a copy rule and $podest is partially translated. Merging in $poorig."
                        if test ! -f "$potfile"; then
                            msgcat --use-first -o $podest $podest $poorig && svn status $podest | tee -a $VCSLOG
                        else
                            msgmerge --previous -C $poorig -o $podest $podest $potfile && svn status $podest | tee -a $VCSLOG
                        fi
                    fi
                fi
            elif test $command = "merge" -a -f $poorig; then
                # without dest it's a move
                if test ! -f "$podest" -a ! -f "$potfile"; then
                    svn move --parents $poorig $podest | tee -a $VCSLOG
                elif test ! -f "$podest"; then
                    svn move --parents $poorig $podest | tee -a $VCSLOG
                    msgmerge --previous -o $podest $podest $potfile
                elif test ! -f "$potfile"; then
                    msgcat --use-first -o $podest $podest $poorig && svn remove --force $poorig | tee -a $VCSLOG && svn status $podest | tee -a $VCSLOG
                else
                    msgmerge --previous -C $poorig -o $podest $podest $potfile && svn remove --force $poorig | tee -a $VCSLOG && svn status $podest | tee -a $VCSLOG
                fi
            elif test $command = "mergekeep" -a -f $poorig; then
                # without dest it's a copy
                if test ! -f "$podest" -a ! -f "$potfile"; then
                    svn copy --parents $poorig $podest | tee -a $VCSLOG
                elif test ! -f "$podest"; then
                    svn copy --parents $poorig $podest | tee -a $VCSLOG
                    msgmerge --previous -o $podest $podest $potfile
                elif test ! -f "$potfile"; then
                    msgcat --use-first -o $podest $podest $poorig && svn status $podest | tee -a $VCSLOG
                else
                    msgmerge --previous -C $poorig -o $podest $podest $potfile && svn status $podest | tee -a $VCSLOG
                fi
            else
                manual=yes
            fi

            if test -n "$manual"; then
                echo "*** WARNING ***: $command rule for file $poorig cannot be applied automatically. Skipping."
            fi
        elif test -d $poorig; then
            test -z "$VERBOSE" || echo "Checking directory $poorig..."
            if test $command = "move" -a ! -e $podest; then
                test -z "$VERBOSE" || echo "$poorig matches a move rule and $podest does not exist. Moving."
                svn move --parents $poorig $podest | tee -a $VCSLOG
            else
                manual=yes
            fi

            if test -n "$manual"; then
                echo "*** WARNING ***: $command rule for directory $poorig cannot be applied automatically. Skipping."
            fi
        fi
    done
}

lineno=0
cat $ORPHLIST | while read line; do
    ((lineno++))
    if test $lineno -lt $FROMLINE; then
        continue
    fi
    # echo "DEBUG: $line"
    if test -z "$line"; then
        continue # empty line
    elif test `echo "$line"|cut -b1 -` = "#"; then
        continue # comment
    fi
    command1=`echo "$line"|cut -d" " -f1 -`
    orig1=`echo "$line"|cut -d" " -f2 -`
    # Careful: $dest is not valid if the $command is "delete"
    dest1=`echo "$line"|cut -d" " -f3 -`
    test -z "$VERBOSE" || echo "===> Processing rule: $command1 $orig1 $dest1"
    if test -z "$command1"; then
        echo "ERROR: rule without command. Skipping!"
        continue
    fi
    for sedexpr in "" \
        "s:summit/:summit-ascript/:" \
        "s:summit/messages/:oldmessages/:" \
    ; do
        orig2=`echo $orig1 | sed "$sedexpr"`
        dest2=`echo $dest1 | sed "$sedexpr"`
        if test -z "$sedexpr" || test $orig2 != $orig1; then
            process_command $command1 $orig2 $dest2
        fi
    done
done
