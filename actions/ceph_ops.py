# Copyright 2016 Canonical Ltd
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
from subprocess import CalledProcessError, check_output

from charmhelpers.core.hookenv import (
    action_get,
    action_fail,
)
from charmhelpers.contrib.storage.linux.ceph import pool_set, \
    set_pool_quota, snapshot_pool, remove_pool_snapshot


def list_pools():
    """Return a list of all Ceph pools."""
    try:
        pool_list = check_output(['ceph', 'osd', 'pool', 'ls']).decode('UTF-8')
        return pool_list
    except CalledProcessError as e:
        action_fail(str(e))


def pool_get():
    """
    Returns a key from a pool using 'ceph osd pool get'.

    The key is provided via the 'key' action parameter and the
    pool provided by the 'pool_name' parameter. These are used when
    running 'ceph osd pool get <pool_name> <key>', the result of
    which is returned.

    On failure, 'unknown' will be returned.
    """
    key = action_get("key")
    pool_name = action_get("pool_name")
    try:
        value = (check_output(['ceph', 'osd', 'pool', 'get', pool_name, key])
                 .decode('UTF-8'))
        return value
    except CalledProcessError as e:
        action_fail(str(e))
        return 'unknown'


def set_pool():
    """
    Sets an arbitrary key key in a Ceph pool.

    Sets the key specified by the action parameter 'key' to the value
    specified in the action parameter 'value' for the pool specified
    by the action parameter 'pool_name' using the charmhelpers
    'pool_set' function.
    """
    key = action_get("key")
    value = action_get("value")
    pool_name = action_get("pool_name")
    pool_set(service='ceph', pool_name=pool_name, key=key, value=value)


def pool_stats():
    """
    Returns statistics for a pool.

    The pool name is provided by the action parameter 'name'.
    """
    try:
        pool_name = action_get("name")
        stats = (
            check_output(['ceph', 'osd', 'pool', 'stats', pool_name])
            .decode('UTF-8')
        )
        return stats
    except CalledProcessError as e:
        action_fail(str(e))


def delete_pool_snapshot():
    """
    Delete a pool snapshot.

    Deletes a snapshot from the pool provided by the action
    parameter 'name', with the snapshot name provided by
    action parameter 'snapshot-name'
    """
    pool_name = action_get("name")
    snapshot_name = action_get("snapshot-name")
    remove_pool_snapshot(service='ceph',
                         pool_name=pool_name,
                         snapshot_name=snapshot_name)


# Note only one or the other can be set
def set_pool_max_bytes():
    """
    Sets the max bytes quota for a pool.

    Sets the pool quota maximum bytes for the pool specified by
    the action parameter 'name' to the value specified by
    the action parameter 'max'
    """
    pool_name = action_get("name")
    max_bytes = action_get("max")
    set_pool_quota(service='ceph',
                   pool_name=pool_name,
                   max_bytes=max_bytes)


def snapshot_ceph_pool():
    """
    Snapshots a Ceph pool.

    Snapshots the pool provided in action parameter 'name' and
    uses the parameter provided in the action parameter 'snapshot-name'
    as the name for the snapshot.
    """
    pool_name = action_get("name")
    snapshot_name = action_get("snapshot-name")
    snapshot_pool(service='ceph',
                  pool_name=pool_name,
                  snapshot_name=snapshot_name)


def get_quorum_status(format_type="text"):
    """
    Return the output of 'ceph quorum_status'.

    On error, function_fail() is called with the exception info.
    """
    ceph_output = check_output(['ceph', 'quorum_status'],
                               timeout=60).decode("utf-8")
    ceph_output_json = json.loads(ceph_output)

    if format_type == "json":
        return {"message": json.dumps(ceph_output_json)}
    else:
        return {
            "election-epoch": ceph_output_json.get("election_epoch"),
            "quorum-age": ceph_output_json.get("quorum_age"),
            "quorum-leader-name": ceph_output_json.get("quorum_leader_name",
                                                       "unknown"),
            "quorum-names": ", ".join(ceph_output_json.get("quorum_names",
                                                           [])),
        }
