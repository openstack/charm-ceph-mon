#!/usr/bin/env python3
#
# Copyright 2022 Canonical Ltd
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
import json
import os
import sys
from subprocess import check_output, CalledProcessError

_path = os.path.dirname(os.path.realpath(__file__))
_hooks = os.path.abspath(os.path.join(_path, "../hooks"))
_lib = os.path.abspath(os.path.join(_path, "../lib"))


def _add_path(path):
    if path not in sys.path:
        sys.path.insert(1, path)


_add_path(_hooks)
_add_path(_lib)


from charmhelpers.core.hookenv import (
    log,
    function_fail,
    function_set,
)
from charms_ceph.utils import list_pools


def get_rados_inconsistent_objs(pg):
    """Get all inconsistent objects for a given placement group.

    :param pg: Name of a placement group
    :type pg: str
    :return: list of inconsistent objects
    :rtype: list[str]
    """
    return json.loads(
        check_output(
            ["rados", "list-inconsistent-obj", pg, "--format=json-pretty"]
        ).decode("UTF-8")
    )


def get_rados_inconsistent_pgs(pool):
    """Get all inconsistent placement groups for a given pool.

    :param pool: Name of a Ceph pool
    :type pool: str
    :returns: list of inconsistent placement group IDs
    :rtype: list[str]
    """
    return json.loads(
        check_output(["rados", "list-inconsistent-pg", pool]).decode("UTF-8")
    )


def get_inconsistent_pgs(ceph_pools):
    """Get all inconsistent placement groups for a list of pools.

    :param ceph_pools: List of names of Ceph pools
    :type ceph_pools: list[str]
    :returns: list of inconsistent placement group IDs as a set
    :rtype: set[str]
    """
    inconsistent_pgs = set()
    for pool in ceph_pools:
        inconsistent_pgs.update(get_rados_inconsistent_pgs(pool))
    return inconsistent_pgs


def get_safe_pg_repairs(inconsistent_pgs):
    """Filters inconsistent placement groups for ones that are safe to repair.

    :param inconsistent_pgs: List of inconsistent placement groups
    :type inconsistent_pgs: list[str]
    :returns: list of safely repairable placement groups as a set
    :rtype: set[str]
    """
    return {pg for pg in inconsistent_pgs if is_pg_safe_to_repair(pg)}


def is_pg_safe_to_repair(pg):
    """Determines if a placement group is safe to repair.

    :param pg: Name of an inconsistent placement group
    :type pg: str
    :returns: placement group is safe to repair
    :rtype: bool
    """
    # Additional tests for known safe cases can be added here.
    return has_read_error_only(pg)


def has_read_error_only(pg):
    """Determines if an inconsistent placement group is caused by a read error.
    Returns False if no read errors are found, or if any errors other than read
    errors are found.

    :param pg: ID of an inconsistent placement group
    :type pg: str
    :returns: placement group is safe to repair
    :rtype: bool
    """
    rados_inconsistent_objs = get_rados_inconsistent_objs(pg)
    read_error_found = False
    for inconsistent in rados_inconsistent_objs.get("inconsistents", []):
        for shard in inconsistent.get("shards", []):
            errors = shard.get("errors", [])
            if errors == ["read_error"]:
                if read_error_found:
                    return False
                read_error_found = True
                continue
            elif errors:
                # Error other than "read_error" detected
                return False
    return read_error_found


def perform_pg_repairs(pgs):
    """Runs `ceph pg repair` on a group of placement groups.
    All placement groups provided should be confirmed as safe prior to using
    this method.

    :param pgs: List of safe-to-repair placement groups
    :type pg: list[str]
    """
    for pg in pgs:
        log("Repairing ceph placement group {}".format(pg))
        check_output(["ceph", "pg", "repair", pg])


def pg_repair():
    """Repair all inconsistent placement groups caused by read errors."""
    ceph_pools = list_pools()
    if not ceph_pools:
        msg = "No Ceph pools found."
        log(msg)
        function_set(msg)
        return

    # Get inconsistent placement groups
    inconsistent_pgs = get_inconsistent_pgs(ceph_pools)
    if not inconsistent_pgs:
        msg = "No inconsistent placement groups found."
        log(msg)
        function_set(msg)
        return

    # Filter for known safe cases
    safe_pg_repairs = get_safe_pg_repairs(inconsistent_pgs)
    unsafe_pg_repairs = inconsistent_pgs.difference(safe_pg_repairs)

    # Perform safe placement group repairs
    if unsafe_pg_repairs:
        log(
            "Ignoring unsafe placement group repairs: {}".format(
                unsafe_pg_repairs
            )
        )
    if safe_pg_repairs:
        log("Safe placement group repairs found: {}".format(safe_pg_repairs))
        perform_pg_repairs(safe_pg_repairs)
        function_set(
            {
                "message": "placement groups repaired: {}".format(
                    sorted(safe_pg_repairs)
                )
            }
        )
    else:
        msg = "No safe placement group repairs found."
        log(msg)
        function_set(msg)


def main():
    try:
        pg_repair()
    except CalledProcessError as e:
        log(e)
        function_fail(
            "Safe placement group repair failed with error: {}".format(str(e))
        )


if __name__ == "__main__":
    main()
