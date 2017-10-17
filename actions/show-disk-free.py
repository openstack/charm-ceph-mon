#!/usr/bin/python
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
from subprocess import check_output, CalledProcessError
from charmhelpers.core.hookenv import log, action_set, action_fail

if __name__ == '__main__':
    try:
        out = check_output(['ceph', '--id', 'admin',
                            'osd', 'df', 'tree']).decode('UTF-8')
        action_set({'message': out})
    except CalledProcessError as e:
        log(e)
        action_fail(
            "ceph osd df tree failed with message: {}".format(e.message)
        )
