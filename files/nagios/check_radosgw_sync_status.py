#!/usr/bin/env python3

# Copyright (C) 2005, 2006, 2007, 2012 James Troup <james.troup@canonical.com>
# Copyright (C) 2014, 2017 Canonical
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Authors: Danny Cocks <danny.cocks@canonical.com>
#          Based on check_ceph_status.py and authors therein

import re
import argparse
import os
import subprocess
import sys
import time
import traceback


class CriticalError(Exception):
    """This indicates a critical error."""
    pass


class UnknownError(Exception):
    """This indicates a unknown error was encountered."""
    pass


def check_file_freshness(filename, newer_than=3600):
    """
    Check a file exists, is readable and is newer than <n> seconds (where
    <n> defaults to 3600).
    """
    # First check the file exists and is readable
    if not os.path.exists(filename):
        raise CriticalError("%s: does not exist." % (filename))
    if os.access(filename, os.R_OK) == 0:
        raise CriticalError("%s: is not readable." % (filename))

    # Then ensure the file is up-to-date enough
    mtime = os.stat(filename).st_mtime
    last_modified = time.time() - mtime
    if last_modified > newer_than:
        raise CriticalError("%s: was last modified on %s and is too old "
                            "(> %s seconds)."
                            % (filename, time.ctime(mtime), newer_than))
    if last_modified < 0:
        raise CriticalError("%s: was last modified on %s which is in the "
                            "future."
                            % (filename, time.ctime(mtime)))


def check_radosgw_status(args):
    """
    Used to check the status of multizone RadosGW Ceph. Uses the output of
    'radosgw-admin sync status', generated during the separate cronjob, to
    determine if health is OK, and if not, should we alert on that situation.

    As this is the first iteration of this function, we will only do a very
    basic check and will rely on the charm config option
    `nagios_rgw_additional_checks` which is passed to this script via
    `args.additional_check`

    :param args: argparse object formatted in the convention of generic Nagios
    checks
    :returns string, describing the status of the ceph cluster.
    :raises: UnknownError, CriticalError
    """

    if args.status_file:
        check_file_freshness(args.status_file)
        with open(args.status_file) as f:
            status_data = f.read()
    else:
        try:
            status_data = (subprocess.check_output(['radosgw-admin',
                                                    'sync',
                                                    'status'])
                           .decode('UTF-8'))
        except subprocess.CalledProcessError as e:
            raise UnknownError(
                "UNKNOWN: radosgw-admin sync status command"
                "failed with error: {}".format(e))

    # If the realm name is empty, i.e. the first line is
    #    realm <some-uuid> ()
    # then we assume this means this is not multizone, so exit early.
    lines = status_data.split('\n')
    if len(lines) >= 1 and re.match(r"realm .* \(\)", lines[0].strip()):
        return "No multizone detected"

    # This is a hangover from check_ceph_status.py and not directly applicable
    # here. I include it for an additional check.
    required_strings = ['realm', 'zonegroup', 'zone']
    if not all(s in status_data for s in required_strings):
        raise UnknownError('UNKNOWN: status data is incomplete')

    # The default message if we end up with no alerts
    message_all_ok = "All OK"
    # The list to collect messages
    msgs = []

    # The always-done checks go here.
    # Currently none

    # Handle checks to do with given expected zones that should be connected.
    if args.zones:
        for zone in args.zones.split(','):
            search_regex = r"data sync source:.*\(" + zone + r"\)"
            if re.search(search_regex, status_data) is None:
                msg = ("CRITICAL: Missing expected sync source '{}'"
                       .format(zone))
                msgs.append(msg)

    # For additional checks, also test these things
    if args.additional_checks:
        for check in args.additional_checks:
            m = re.search(check, status_data)
            if m is not None:
                msgs.append("CRITICAL: {}".format(m.group(0)))

    complete_output = '\n'.join(msgs)
    if any(msg.startswith("CRITICAL") for msg in msgs):
        raise CriticalError(complete_output)
    elif len(msgs) >= 1:
        raise UnknownError(complete_output)
    else:
        return message_all_ok


def parse_args(args):
    parser = argparse.ArgumentParser(description='Check ceph status')
    parser.add_argument('-f', '--file', dest='status_file',
                        default=False,
                        help='Optional file with "ceph status" output. '
                             'Generally useful for testing, and if the Nagios '
                             'user account does not have rights for the Ceph '
                             'config files.')
    parser.add_argument('--zones', dest='zones',
                        default=None,
                        help="Check if the given zones, as a comma-separated "
                             "list, are present in the output. If they are "
                             "missing report critical.")
    parser.add_argument('--additional_check', dest='additional_checks',
                        action='append',
                        help="Check if a given pattern exists in any status"
                             "message. If it does, report critical")

    return parser.parse_args(args)


def main(args):
    # Note: leaving "warning" in here, as a reminder for the expected NRPE
    # returncodes, even though this script doesn't output any warnings.
    EXIT_CODES = {'ok': 0, 'warning': 1, 'critical': 2, 'unknown': 3}
    exitcode = 'unknown'
    try:
        output_msg = check_radosgw_status(args)
        print(output_msg)
        exitcode = 'ok'
    except UnknownError as msg:
        print(msg)
        exitcode = 'unknown'
    except CriticalError as msg:
        print(msg)
        exitcode = 'critical'
    except Exception:
        print("%s raised unknown exception '%s'" % ('check_ceph_status',
                                                    sys.exc_info()[0]))
        print('=' * 60)
        traceback.print_exc(file=sys.stdout)
        print('=' * 60)
        exitcode = 'unknown'
    return EXIT_CODES[exitcode]


if __name__ == '__main__':
    args = parse_args(sys.argv[1:])
    status = main(args)
    sys.exit(status)
