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

import mock
import sys

import test_utils

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
mock_apt = mock.MagicMock()
sys.modules['apt'] = mock_apt
mock_apt.apt_pkg = mock.MagicMock()

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import ceph_hooks as hooks

TO_PATCH = [
    'status_set',
    'config',
    'ceph',
    'is_relation_made',
    'relation_ids',
    'relation_get',
    'related_units',
    'local_unit',
    'application_version_set',
    'get_upstream_version',
]

NO_PEERS = {
    'ceph-mon1': True
}

ENOUGH_PEERS_INCOMPLETE = {
    'ceph-mon1': True,
    'ceph-mon2': False,
    'ceph-mon3': False,
}

ENOUGH_PEERS_COMPLETE = {
    'ceph-mon1': True,
    'ceph-mon2': True,
    'ceph-mon3': True,
}


class ServiceStatusTestCase(test_utils.CharmTestCase):
    def setUp(self):
        super(ServiceStatusTestCase, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.test_config.set('monitor-count', 3)
        self.local_unit.return_value = 'ceph-mon1'
        self.get_upstream_version.return_value = '10.2.2'
        self.is_relation_made.return_value = False

    @mock.patch.object(hooks, 'get_peer_units')
    def test_assess_status_no_peers(self, _peer_units):
        _peer_units.return_value = NO_PEERS
        hooks.assess_status()
        self.status_set.assert_called_with('blocked', mock.ANY)
        self.application_version_set.assert_called_with('10.2.2')

    @mock.patch.object(hooks, 'get_peer_units')
    def test_assess_status_peers_incomplete(self, _peer_units):
        _peer_units.return_value = ENOUGH_PEERS_INCOMPLETE
        hooks.assess_status()
        self.status_set.assert_called_with('waiting', mock.ANY)
        self.application_version_set.assert_called_with('10.2.2')

    @mock.patch.object(hooks, 'get_peer_units')
    def test_assess_status_peers_complete_active(self, _peer_units):
        _peer_units.return_value = ENOUGH_PEERS_COMPLETE
        self.ceph.is_bootstrapped.return_value = True
        self.ceph.is_quorum.return_value = True
        hooks.assess_status()
        self.status_set.assert_called_with('active', mock.ANY)
        self.application_version_set.assert_called_with('10.2.2')

    @mock.patch.object(hooks, 'get_peer_units')
    def test_assess_status_peers_complete_down(self, _peer_units):
        _peer_units.return_value = ENOUGH_PEERS_COMPLETE
        self.ceph.is_bootstrapped.return_value = False
        self.ceph.is_quorum.return_value = False
        hooks.assess_status()
        self.status_set.assert_called_with('blocked', mock.ANY)
        self.application_version_set.assert_called_with('10.2.2')

    def test_get_peer_units_no_peers(self):
        self.relation_ids.return_value = ['mon:1']
        self.related_units.return_value = []
        self.assertEqual({'ceph-mon1': True},
                         hooks.get_peer_units())

    def test_get_peer_units_peers_incomplete(self):
        self.relation_ids.return_value = ['mon:1']
        self.related_units.return_value = ['ceph-mon2',
                                           'ceph-mon3']
        self.relation_get.return_value = None
        self.assertEqual({'ceph-mon1': True,
                          'ceph-mon2': False,
                          'ceph-mon3': False},
                         hooks.get_peer_units())

    def test_get_peer_units_peers_complete(self):
        self.relation_ids.return_value = ['mon:1']
        self.related_units.return_value = ['ceph-mon2',
                                           'ceph-mon3']
        self.relation_get.side_effect = ['ceph-mon2',
                                         'ceph-mon3']
        self.assertEqual({'ceph-mon1': True,
                          'ceph-mon2': True,
                          'ceph-mon3': True},
                         hooks.get_peer_units())

    def test_no_bootstrap_not_set(self):
        self.is_relation_made.return_value = True
        hooks.assess_status()
        self.status_set.assert_called_with('blocked', mock.ANY)
        self.application_version_set.assert_called_with('10.2.2')
