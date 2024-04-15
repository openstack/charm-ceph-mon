#! /usr/bin/env python3
#
# Copyright 2024 Canonical Ltd
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

"""Retrieve a list of entities recognized by the Ceph cluster."""

import json
import logging
import subprocess
import yaml


logger = logging.getLogger(__name__)


def list_entities(event):
    try:
        # NOTE(lmlg): Don't bother passing --format=json or the likes,
        # since it sometimes contain escaped strings that are incompatible
        # with python's json module. This method of fetching entities is
        # simple enough and portable across Ceph versions.
        out = subprocess.check_output(['sudo', 'ceph', 'auth', 'ls'])
        ret = []

        for line in out.decode('utf-8').split('\n'):
            if line and not (line.startswith(' ') or line.startswith('\t') or
                             line.startswith('\n')):
                ret.append(line)

        fmt = event.params.get('format', 'text')
        if fmt == 'json':
            msg = json.dumps(str(ret))
        elif fmt == 'yaml':
            msg = yaml.safe_dump(str(ret))
        else:
            msg = '\n'.join(ret)

        event.set_results({'message': msg})
    except Exception as e:
        logger.warning(e)
        event.fail('failed to list entities: {}'.format(str(e)))
