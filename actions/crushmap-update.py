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
import base64
from charmhelpers.core.hookenv import action_get, action_fail
from subprocess import check_output, CalledProcessError, PIPE, Popen


def update_crushmap():
    try:
        encoded_text = action_get("map")
        json_map = base64.b64decode(encoded_text)
        try:
            # This needs json_map passed to it from stdin
            crushtool = Popen(
                ["crushtool", "-o", "compiled_crushmap", "-m", "compile"],
                stdin=PIPE)
            crushtool_stdout, crushtool_stderr = crushtool.communicate(
                input=json_map)
            if crushtool_stderr is not None:
                action_fail(
                    "Failed to compile json: {}".format(crushtool_stderr))
            check_output(
                ["ceph", "osd", "setcrushmap", "-i", "compiled_crushmap"])
        except (CalledProcessError, OSError) as err2:
            action_fail("Crush compile or load failed with error: {}".format(
                err2.output))
    except TypeError as err:
        action_fail(
            "Unable to base64 decode: {}. Error: {}".format(encoded_text, err))


if __name__ == '__main__':
    update_crushmap()
