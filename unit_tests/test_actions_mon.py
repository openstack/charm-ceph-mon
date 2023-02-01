# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json
import sys
import unittest.mock as mock

from test_utils import CharmTestCase

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
mock_apt = mock.MagicMock()
sys.modules['apt'] = mock_apt
mock_apt.apt_pkg = mock.MagicMock()

# mocking for rados
mock_rados = mock.MagicMock()
sys.modules['rados'] = mock_rados
mock_rados.connect = mock.MagicMock()

# mocking for psutil
mock_psutil = mock.MagicMock()
sys.modules['psutil'] = mock_psutil
mock_psutil.disks = mock.MagicMock()

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    # import health actions as actions
    import ceph_ops as actions


class OpsTestCase(CharmTestCase):

    def setUp(self):
        super(OpsTestCase, self).setUp(
            actions, ["check_output",
                      "action_get",
                      "action_fail",
                      "open"])

    @mock.patch('socket.gethostname')
    def test_get_quorum_status(self, mock_hostname):
        mock_hostname.return_value = 'mockhost'
        cmd_out = (
            '{"election_epoch":4,"quorum":[0,1,2],"quorum_names":["juju-18410c'
            '-zaza-b7061340ed19-1","juju-18410c-zaza-b7061340ed19-0","juju-184'
            '10c-zaza-b7061340ed19-2"],"quorum_leader_name":"juju-18410c-zaza-'
            'b7061340ed19-1","quorum_age":97785,"monmap":{"epoch":1,"fsid":"4f'
            '9dd22a-1b71-11ec-a02a-fa163ee765d3","modified":"2021-09-22 06:51:'
            '10.975225","created":"2021-09-22 06:51:10.975225","min_mon_releas'
            'e":14,"min_mon_release_name":"nautilus","features":{"persistent":'
            '["kraken","luminous","mimic","osdmap-prune","nautilus"],"optional'
            '":[]},"mons":[{"rank":0,"name":"juju-18410c-zaza-b7061340ed19-1",'
            '"public_addrs":{"addrvec":[{"type":"v2","addr":"10.5.0.122:3300",'
            '"nonce":0},{"type":"v1","addr":"10.5.0.122:6789","nonce":0}]},"ad'
            'dr":"10.5.0.122:6789/0","public_addr":"10.5.0.122:6789/0"},{"rank'
            '":1,"name":"juju-18410c-zaza-b7061340ed19-0","public_addrs":{"add'
            'rvec":[{"type":"v2","addr":"10.5.2.239:3300","nonce":0},{"type":"'
            'v1","addr":"10.5.2.239:6789","nonce":0}]},"addr":"10.5.2.239:6789'
            '/0","public_addr":"10.5.2.239:6789/0"},{"rank":2,"name":"juju-184'
            '10c-zaza-b7061340ed19-2","public_addrs":{"addrvec":[{"type":"v2",'
            '"addr":"10.5.3.201:3300","nonce":0},{"type":"v1","addr":"10.5.3.2'
            '01:6789","nonce":0}]},"addr":"10.5.3.201:6789/0","public_addr":"1'
            '0.5.3.201:6789/0"}]}}'
        )
        self.check_output.return_value = cmd_out.encode()

        result = actions.get_quorum_status()
        self.assertDictEqual(result, {
            "election-epoch": 4,
            "quorum-age": 97785,
            "quorum-names": "juju-18410c-zaza-b7061340ed19-1, "
                            "juju-18410c-zaza-b7061340ed19-0, "
                            "juju-18410c-zaza-b7061340ed19-2",
            "quorum-leader-name": "juju-18410c-zaza-b7061340ed19-1",
        })

        result = actions.get_quorum_status(format_type="json")
        self.assertDictEqual(json.loads(result["message"]),
                             json.loads(cmd_out))
