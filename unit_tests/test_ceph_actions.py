# Copyright 2019 Canonical Ltd
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

import unittest.mock as mock
from ops.testing import Harness
import subprocess

import test_utils
import ops_actions.copy_pool as copy_pool
import ops_actions.list_entities as list_entities

with mock.patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    # src.charm imports ceph_hooks, so we need to workaround the inclusion
    # of the 'harden' decorator.
    from src.charm import CephMonCharm


class CopyPoolTestCase(test_utils.CharmTestCase):

    def setUp(self):
        self.harness = Harness(CephMonCharm)

    @mock.patch.object(copy_pool.subprocess, 'check_call')
    def test_copy_pool(self, mock_check_call):
        _action_data = {
            'source': 'source-pool',
            'target': 'target-pool',
        }
        self.harness.begin()
        self.harness.charm.on_copy_pool_action(
            test_utils.MockActionEvent(_action_data))
        mock_check_call.assert_called_with([
            'rados', 'cppool',
            'source-pool', 'target-pool',
        ])

    @mock.patch.object(copy_pool.subprocess, 'check_call')
    def test_copy_pool_failed(self, mock_check_call):
        _action_data = {
            'source': 'source-pool',
            'target': 'target-pool',
        }
        self.harness.begin()
        mock_check_call.side_effect = subprocess.CalledProcessError(1, 'rados')
        event = test_utils.MockActionEvent(_action_data)
        self.harness.charm.on_copy_pool_action(event)
        mock_check_call.assert_called_with([
            'rados', 'cppool',
            'source-pool', 'target-pool',
        ])
        event.fail.assert_called_once_with(mock.ANY)


class CreateCrushRuleTestCase(test_utils.CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.addCleanup(self.harness.cleanup)

    @mock.patch("ops_actions.create_crush_rule.subprocess.check_call")
    def test_create_crush_rule(self, mock_check_call):
        """Test reweight_osd action has correct calls."""
        self.harness.begin()
        self.harness.charm.on_create_crush_rule_action(
            test_utils.MockActionEvent({
                'name': 'replicated_nvme',
                'failure-domain': 'host',
                'device-class': 'nvme',
            }))
        expected = [
            'ceph', 'osd', 'crush', 'rule',
            'create-replicated',
            'replicated_nvme',
            'default',
            'host',
            'nvme',
        ]
        mock_check_call.assert_called_once_with(expected)

    @mock.patch("ops_actions.create_crush_rule.subprocess.check_call")
    def test_create_crush_rule_no_class(self, mock_check_call):
        """Test reweight_osd action has correct calls."""
        self.harness.begin()
        self.harness.charm.on_create_crush_rule_action(
            test_utils.MockActionEvent({
                'name': 'replicated_whoknows',
                'failure-domain': 'disk',
            }))
        expected = [
            'ceph', 'osd', 'crush', 'rule',
            'create-replicated',
            'replicated_whoknows',
            'default',
            'disk'
        ]
        mock_check_call.assert_called_once_with(expected)

    @mock.patch("ops_actions.create_crush_rule.subprocess.check_call")
    def test_create_crush_rule_failed(self, mock_check_call):
        """Test reweight_osd action has correct calls."""
        self.harness.begin()
        mock_check_call.side_effect = subprocess.CalledProcessError(1, 'test')
        event = test_utils.MockActionEvent({
            'name': 'replicated_nvme',
            'failure-domain': 'host',
            'device-class': 'nvme',
        })
        self.harness.charm.on_create_crush_rule_action(event)
        expected = [
            'ceph', 'osd', 'crush', 'rule',
            'create-replicated',
            'replicated_nvme',
            'default',
            'host',
            'nvme',
        ]
        mock_check_call.assert_called_once_with(expected)
        event.fail.assert_called_once_with(
            'rule creation failed due to exception')


class CreateErasureProfileTestCase(test_utils.CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.addCleanup(self.harness.cleanup)

    @mock.patch('ops_actions.create_erasure_profile.create_erasure_profile')
    def test_create_jerasure_profile(self, mock_create_erasure_profile):
        self.harness.begin()
        self.harness.charm.on_create_erasure_profile_action(
            test_utils.MockActionEvent({
                'name': 'erasure',
                'plugin': 'jerasure',
                'failure-domain': 'disk',
                'k': 6,
                'm': 3,
            }))
        mock_create_erasure_profile.assert_called_once_with(
            service='admin', erasure_plugin_name='jerasure',
            profile_name='erasure', data_chunks=None,
            coding_chunks=None, failure_domain='disk', device_class=None
        )

    @mock.patch('ops_actions.create_erasure_profile.create_erasure_profile')
    def test_create_isa_profile(self, mock_create_erasure_profile):
        self.harness.begin()
        self.harness.charm.on_create_erasure_profile_action(
            test_utils.MockActionEvent({
                'name': 'erasure',
                'plugin': 'isa',
                'failure-domain': 'disk',
                'k': 6,
                'm': 3,
            }))
        mock_create_erasure_profile.assert_called_once_with(
            service='admin', erasure_plugin_name='isa',
            profile_name='erasure', data_chunks=None,
            coding_chunks=None, failure_domain='disk', device_class=None
        )

    @mock.patch('ops_actions.create_erasure_profile.create_erasure_profile')
    def test_create_lrc_profile(self, mock_create_erasure_profile):
        self.harness.begin()
        self.harness.charm.on_create_erasure_profile_action(
            test_utils.MockActionEvent({
                'name': 'erasure',
                'plugin': 'lrc',
                'failure-domain': 'disk',
                'k': 6,
                'm': 3,
                'locality-chunks': 2,
                'crush-locality': 'host',
            }))
        mock_create_erasure_profile.assert_called_once_with(
            service='admin', erasure_plugin_name='lrc',
            profile_name='erasure', data_chunks=None,
            coding_chunks=None, locality=2, crush_locality='host',
            failure_domain='disk', device_class=None
        )

    @mock.patch('ops_actions.create_erasure_profile.create_erasure_profile')
    def test_create_shec_profile(self, mock_create_erasure_profile):
        self.harness.begin()
        self.harness.charm.on_create_erasure_profile_action(
            test_utils.MockActionEvent({
                'name': 'erasure',
                'plugin': 'shec',
                'failure-domain': 'disk',
                'k': 6,
                'm': 3,
                'durability-estimator': 2
            }))
        mock_create_erasure_profile.assert_called_once_with(
            service='admin', erasure_plugin_name='shec',
            profile_name='erasure', data_chunks=None,
            coding_chunks=None, durability_estimator=2,
            failure_domain='disk', device_class=None
        )

    @mock.patch('ops_actions.create_erasure_profile.create_erasure_profile')
    def test_create_clay_profile(self, mock_create_erasure_profile):
        self.harness.begin()
        self.harness.charm.on_create_erasure_profile_action(
            test_utils.MockActionEvent({
                'name': 'erasure',
                'plugin': 'clay',
                'failure-domain': 'disk',
                'k': 6,
                'm': 3,
                'helper-chunks': 2,
                'scalar-mds': 'jerasure'
            }))
        mock_create_erasure_profile.assert_called_once_with(
            service='admin', erasure_plugin_name='clay',
            profile_name='erasure', data_chunks=None,
            coding_chunks=None, helper_chunks=2,
            scalar_mds='jerasure', failure_domain='disk', device_class=None
        )


class GetHealthTestCase(test_utils.CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    @mock.patch('ops_actions.get_health.check_output')
    def test_get_health_action(self, mock_check_output):
        mock_check_output.return_value = b'yay'
        event = test_utils.MockActionEvent({})
        self.harness.charm.on_get_health_action(event)
        event.set_results.assert_called_once_with(({'message': 'yay'}))

    @mock.patch('ops_actions.get_health.check_output')
    def test_get_health_action_error(self, mock_check_output):
        mock_check_output.side_effect = subprocess.CalledProcessError(
            1, 'test')
        event = test_utils.MockActionEvent({})
        self.harness.charm.on_get_health_action(event)
        event.fail.assert_called_once_with(
            'ceph health failed with message: '
            "Command 'test' returned non-zero exit status 1.")


class GetErasureProfile(test_utils.CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    @mock.patch('ops_actions.get_erasure_profile.ceph')
    def test_get_erasure_profile_ok(self, mock_ceph):
        mock_ceph.get_erasure_profile.return_value = "foo-erasure-params"
        event = test_utils.MockActionEvent({"name": "foo-profile"})
        self.harness.charm.on_get_erasure_profile_action(event)
        event.set_results.assert_called_once_with((
            {"message": "foo-erasure-params"}
        ))

    @mock.patch('ops_actions.get_erasure_profile.ceph')
    def test_get_erasure_profile_notfound(self, mock_ceph):
        mock_ceph.get_erasure_profile.return_value = None
        event = test_utils.MockActionEvent({"name": "notfound-profile"})
        self.harness.charm.on_get_erasure_profile_action(event)
        event.set_results.assert_called_once_with((
            {"message": None}
        ))


class ListEntities(test_utils.CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    @mock.patch.object(list_entities.subprocess, 'check_call')
    def test_list_entities(self, check_call):
        check_call.return_value = b"""
client.admin
  key: AQAOwwFmTR3TNxAAIsdYgastd0uKntPtEnoWug==
mgr.0
  key: AQAVwwFm/CmaJhAAdacns6DdFe4xZE1iwj8izg==
"""
        event = test_utils.MockActionEvent({})
        self.harness.charm.on_list_entities_action(event)
        event.set_results.assert_called_once_with(
            {"message": "client.admin\nmgr.0"}
        )
