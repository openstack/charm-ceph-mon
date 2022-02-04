#!/usr/bin/env python3
#
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

import sys

sys.path.append('hooks')
from subprocess import CalledProcessError
from charmhelpers.core.hookenv import action_get, log, action_fail
from charmhelpers.contrib.storage.linux.ceph import ErasurePool, ReplicatedPool


def create_pool():
    pool_name = action_get("name")
    pool_type = action_get("pool-type")
    percent_data = action_get("percent-data") or 10
    app_name = action_get("app-name") or 'unknown'
    try:
        if pool_type == "replicated":
            replicas = action_get("replicas")
            crush_profile_name = action_get("profile-name")
            replicated_pool = ReplicatedPool(name=pool_name,
                                             service='admin',
                                             replicas=replicas,
                                             app_name=app_name,
                                             profile_name=crush_profile_name,
                                             percent_data=float(percent_data),
                                             )
            replicated_pool.create()

        elif pool_type in ("erasure", "erasure-coded"):
            crush_profile_name = action_get("erasure-profile-name")
            allow_ec_overwrites = action_get("allow-ec-overwrites")
            erasure_pool = ErasurePool(name=pool_name,
                                       erasure_code_profile=crush_profile_name,
                                       service='admin',
                                       app_name=app_name,
                                       percent_data=float(percent_data),
                                       allow_ec_overwrites=allow_ec_overwrites,
                                       )
            erasure_pool.create()
        else:
            log("Unknown pool type of {}. Only erasure or replicated is "
                "allowed".format(pool_type))
            action_fail("Unknown pool type of {}. Only erasure or replicated "
                        "is allowed".format(pool_type))
    except CalledProcessError as e:
        action_fail("Pool creation failed because of a failed process. "
                    "Ret Code: {} Message: {}".format(e.returncode, str(e)))


if __name__ == '__main__':
    create_pool()
