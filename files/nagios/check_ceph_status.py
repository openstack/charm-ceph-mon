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
# Authors: Jacek Nykis <jacek.nykis@canonical.com>
#          Xav Paice <xav.paice@canonical.com>
#          James Troup <james.troup@canonical.com>

import re
import argparse
import json
import os
import subprocess
import sys
import time
import traceback


class CriticalError(Exception):
    """This indicates a critical error."""
    pass


class WarnError(Exception):
    """This indicates a warning condition."""
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


def get_ceph_version():
    """
    Uses CLI to get the ceph version, because the status output changes from
    Luminous onwards (12.2.0 or higher)

    :returns: list of integers, just the actual version number
    """
    try:
        out_string = subprocess.check_output(['ceph',
                                              '--version']).decode('UTF-8')
    except subprocess.CalledProcessError as e:
        raise UnknownError(
            "UNKNOWN: could not determine Ceph version, error: {}".format(e))
    out_version = [int(x) for x in out_string.split(" ")[2].split(".")]
    return out_version


def check_ceph_status(args):
    """
    Used to check the status of a Ceph cluster.  Uses the output of 'ceph
    status' to determine if health is OK, and if not, should we alert on that
    situation.

    If status is HEALTH_OK then this function returns OK with no further check.
    Otherwise, look for known situations which could cause ceph status to
    return not OK, but things which represent general operations and don't
    warrant a pager event.  These include OSD reweight actions, and
    nodeep-scrub flag setting, with limits for the amount of misplaced data.

    :param args: argparse object formatted in the convention of generic Nagios
    checks
    :returns string, describing the status of the ceph cluster.
    """

    ignorable = (r'\d+ pgs (?:backfill|degraded|recovery_wait|stuck unclean)|'
                 'recovery \d+\/\d+ objects (?:degraded|misplaced)')
    if args.ignore_nodeepscrub:
        ignorable = ignorable + '|nodeep-scrub flag\(s\) set'
    status_critical = False
    if args.status_file:
        check_file_freshness(args.status_file)
        with open(args.status_file) as f:
            tree = f.read()
        status_data = json.loads(tree)
    else:
        try:
            tree = (subprocess.check_output(['ceph',
                                             'status',
                                             '--format', 'json'])
                    .decode('UTF-8'))
        except subprocess.CalledProcessError as e:
            raise UnknownError(
                "UNKNOWN: ceph status command failed with error: {}".format(e))
        status_data = json.loads(tree)

    required_keys = ['health', 'monmap', 'pgmap']
    if not all(key in status_data.keys() for key in required_keys):
        raise UnknownError('UNKNOWN: status data is incomplete')
    ceph_version = get_ceph_version()
    if ceph_version[0] >= 12 and ceph_version[1] >= 2:
        # This is Luminous or above
        overall_status = status_data['health'].get('status')
        luminous = True
    else:
        overall_status = status_data['health'].get('overall_status')
        luminous = False

    if overall_status != 'HEALTH_OK':
        # Health is not OK, check if any lines are not in our list of OK
        # any lines that don't match, check is critical
        status_msg = []
        if luminous:
            status_messages = [x['summary']['message'] for x in status_data['health'].get('checks').values()]
        else:
            status_messages = [x['summary'] for x in status_data['health']['summary']]
        for status in status_messages:
            if not re.match(ignorable, status):
                status_critical = True
                status_msg.append(status)
        # If we got this far, then the status is not OK but the status lines
        # are all in our list of things we consider to be operational tasks.
        # Check the thresholds and return CRITICAL if exceeded,
        # otherwise there's something not accounted for and we want to know
        # about it with a WARN alert.
        degraded_ratio = float(status_data['pgmap'].get('degraded_ratio', 0.0))
        if degraded_ratio > args.degraded_thresh:
            status_critical = True
        if degraded_ratio > 0:
            status_msg.append("Degraded ratio: {}".format(degraded_ratio))
        misplaced_ratio = float(status_data['pgmap'].get('misplaced_ratio', 0.0))
        if misplaced_ratio > args.misplaced_thresh:
            status_critical = True
        if misplaced_ratio > 0:
            status_msg.append("Misplaced ratio: {}".format(misplaced_ratio))
        recovering = int(status_data['pgmap'].get('recovering_objects_per_sec', 0))
        # We only check recovering rate when the cluster has degraded or misplaced PGs
        if (degraded_ratio > 0 or misplaced_ratio > 0) and recovering < args.recovery_rate:
            status_critical = True
            status_msg.append("Recovering objects/sec {}".format(recovering))
        if status_critical:
            msg = 'CRITICAL: ceph health: "{} {}"'.format(
                  overall_status,
                  ", ".join(status_msg))
            raise CriticalError(msg)
        if overall_status == 'HEALTH_WARN' and status_msg:
            msg = "WARNING: {}".format(", ".join(status_msg))
            raise WarnError(msg)
    message = "All OK"
    print(message)
    return message


def parse_args(args):
    parser = argparse.ArgumentParser(description='Check ceph status')
    parser.add_argument('-f', '--file', dest='status_file',
                        default=False,
                        help='Optional file with "ceph status" output. '
                             'Generally useful for testing, and if the Nagios '
                             'user account does not have rights for the Ceph '
                             'config files.')
    parser.add_argument('--degraded_thresh', dest='degraded_thresh',
                        default=1, type=float,
                        help="Threshold for degraded ratio (0.1 = 10%)")
    parser.add_argument('--misplaced_thresh', dest='misplaced_thresh',
                        default=10, type=float,
                        help="Threshold for misplaced ratio (0.1 = 10%)")
    parser.add_argument('--recovery_rate', dest='recovery_rate',
                        default=1, type=int,
                        help="Recovery rate below which we consider recovery "
                             "to be stalled")
    parser.add_argument('--ignore_nodeepscrub', dest='ignore_nodeepscrub',
                        default=False, action='store_true',
                        help="Whether to ignore the nodeep-scrub flag.  If "
                             "the nodeep-scrub flag is set, the check returns "
                             "warning if this param is passed, otherwise "
                             "returns critical.")
    return parser.parse_args(args)


def main(args):
    EXIT_CODES = {'ok': 0, 'warning': 1, 'critical': 2, 'unknown': 3}
    exitcode = 'ok'
    try:
        msg = check_ceph_status(args)
    except UnknownError as msg:
        print(msg)
        exitcode = 'unknown'
    except CriticalError as msg:
        print(msg)
        exitcode = 'critical'
    except WarnError as msg:
        print(msg)
        exitcode = 'warning'
    except:
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
