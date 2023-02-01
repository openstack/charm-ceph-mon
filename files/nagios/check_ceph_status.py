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
    :raises: UnknownError
    """
    try:
        out_string = subprocess.check_output(['ceph',
                                              '--version']).decode('UTF-8')
    except subprocess.CalledProcessError as e:
        raise UnknownError(
            "UNKNOWN: could not determine Ceph version, error: {}".format(e))
    out_version = [int(x) for x in out_string.split(" ")[2].split(".")]
    return out_version


def get_status_and_messages(status_data):
    """
    Used to get general status of a Ceph cluster as well as a list of
    error/warning messages.

    :param status_data: JSON formatted output from ceph health
    :type status_data: str
    :returns:
        - string representing overall status of the cluster
        - list of error or warning messages
    :rtype: tuple(str, list)
    :raises: UnknownError
    """

    try:
        ceph_version = get_ceph_version()
    except UnknownError as e:
        raise UnknownError(e)
    if ceph_version[0] >= 12 and ceph_version[1] >= 2:
        # This is Luminous or above
        overall_status = status_data['health'].get('status')
        status_messages = [x['summary']['message'] for x in
                           status_data['health'].get('checks', {}).values()]
    else:
        overall_status = status_data['health'].get('overall_status')
        status_messages = [x['summary'] for x in
                           status_data['health']['summary']]
    return overall_status, status_messages


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
    :raises: UnknownError
    """

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

    try:
        overall_status, status_messages = get_status_and_messages(status_data)
    except UnknownError as e:
        raise UnknownError(e)

    message_all_ok = "All OK"

    # if it is just additional check, deal with it and ignore overall health
    if args.additional_check is not None:
        for status_message in status_messages:
            if re.search(args.additional_check, status_message) is not None:
                if args.additional_check_critical:
                    msg = "CRITICAL: {}".format(status_message)
                    raise CriticalError(msg)
                else:
                    msg = "WARNING: {}".format(status_message)
                    raise WarnError(msg)
        print(message_all_ok)
        return message_all_ok

    # if it is just --check_num_osds, deal with it and ignore overall health
    if args.check_num_osds:
        osdmap = status_data['osdmap']['osdmap']
        num_osds = osdmap['num_osds']
        num_up_osds = osdmap['num_up_osds']
        num_in_osds = osdmap['num_in_osds']
        if num_osds != num_up_osds or num_up_osds != num_in_osds:
            msg = "CRITICAL: OSDs: {}, OSDs up: {}, OSDs in: {}".format(
                num_osds, num_up_osds, num_in_osds)
            raise CriticalError(msg)
        message_ok = "OK: {} OSDs, all up and in".format(num_osds)
        print(message_ok)
        return message_ok

    if overall_status != 'HEALTH_OK':
        # Health is not OK, collect status message(s) and
        # decide whether to return warning or critical
        status_critical = False
        status_msg = []
        for status in status_messages:
            status_msg.append(status)
            # Check if nedeepscrub is set and whether it should raise an error
            if args.raise_nodeepscrub:
                if re.match("nodeep-scrub flag", status):
                    status_critical = True
            # Check if noout is set
            if re.match("noout flag", status):
                status_critical = True
                status_msg.append("noout flag is set")
        if overall_status == 'HEALTH_CRITICAL' or \
           overall_status == 'HEALTH_ERR':
            # HEALTH_ERR, report critical
            status_critical = True
        else:
            # HEALTH_WARN
            # Check the threshold for a list of operational tasks,
            # and return CRITICAL if exceeded
            degraded_ratio = float(status_data['pgmap'].get('degraded_ratio',
                                                            0.0))
            if degraded_ratio > args.degraded_thresh:
                status_critical = True
            if degraded_ratio > 0:
                status_msg.append("Degraded ratio: {}".format(degraded_ratio))
            misplaced_ratio = float(status_data['pgmap'].get('misplaced_ratio',
                                                             0.0))
            if misplaced_ratio > args.misplaced_thresh:
                status_critical = True
            if misplaced_ratio > 0:
                status_msg.append("Misplaced ratio: {}".
                                  format(misplaced_ratio))
            recovering = float(status_data['pgmap'].
                               get('recovering_objects_per_sec', 0.0))
            if (degraded_ratio > 0 or misplaced_ratio > 0) \
               and recovering > 0 \
               and recovering < args.recovery_rate:
                status_critical = True
            if recovering > 0:
                status_msg.append("Recovering objects/s {}".format(recovering))
        if status_critical:
            msg = 'CRITICAL: ceph health: "{} {}"'.format(
                  overall_status,
                  ", ".join(status_msg))
            raise CriticalError(msg)
        else:
            # overall_status == 'HEALTH_WARN':
            msg = "WARNING: {}".format(", ".join(status_msg))
            raise WarnError(msg)
    print(message_all_ok)
    return message_all_ok


def parse_args(args):
    parser = argparse.ArgumentParser(description='Check ceph status')
    parser.add_argument('-f', '--file', dest='status_file',
                        default=False,
                        help='Optional file with "ceph status" output. '
                             'Generally useful for testing, and if the Nagios '
                             'user account does not have rights for the Ceph '
                             'config files.')
    parser.add_argument('--degraded_thresh', dest='degraded_thresh',
                        default=1.0, type=float,
                        help="Threshold for degraded ratio (0.1 = 10%)")
    parser.add_argument('--misplaced_thresh', dest='misplaced_thresh',
                        default=1.0, type=float,
                        help="Threshold for misplaced ratio (0.1 = 10%)")
    parser.add_argument('--recovery_rate', dest='recovery_rate',
                        default=1, type=int,
                        help="Recovery rate (in objects/s) below which we"
                             "consider recovery to be stalled")
    parser.add_argument('--raise_nodeepscrub', dest='raise_nodeepscrub',
                        default=False, action='store_true',
                        help="Whether to raise an error for the nodeep-scrub"
                             "flag. If the nodeep-scrub flag is set,"
                             "the check returns critical if this param is"
                             "passed, otherwise it returns warning.")
    parser.add_argument('--additional_check', dest='additional_check',
                        default=None,
                        help="Check if a given pattern exists in any status"
                             "message. If it does, report warning or critical"
                             "for this check according to content of"
                             "additional_check_critical parameter")
    parser.add_argument('--additional_check_critical',
                        dest='additional_check_critical', default=False,
                        action='store_true',
                        help="Specifies what is returned if a check is"
                             "positive. If the argument is not provided,"
                             "check returns a warning. Otherwise it "
                             "returns an error condition.")
    parser.add_argument('--check_num_osds',
                        dest='check_num_osds', default=False,
                        action='store_true',
                        help="Check whether all OSDs are up and in")

    return parser.parse_args(args)


def main(args):
    EXIT_CODES = {'ok': 0, 'warning': 1, 'critical': 2, 'unknown': 3}
    exitcode = 'ok'
    try:
        check_ceph_status(args)
    except UnknownError as msg:
        print(msg)
        exitcode = 'unknown'
    except CriticalError as msg:
        print(msg)
        exitcode = 'critical'
    except WarnError as msg:
        print(msg)
        exitcode = 'warning'
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
