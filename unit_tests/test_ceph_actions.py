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
import ops_actions.rotate_key as rotate_key

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


class ListEntities(test_utils.CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    @mock.patch.object(list_entities.subprocess, 'check_output')
    def test_list_entities(self, check_output):
        check_output.return_value = b"""
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


# Needs to be outside as the decorator wouldn't find it otherwise.
MGR_KEYRING_FILE = """
[mgr.host-1]
  key = old-key
"""


class RotateKey(test_utils.CharmTestCase):
    """Run tests for action."""

    def setUp(self):
        self.harness = Harness(CephMonCharm)
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    def test_invalid_entity(self):
        event = test_utils.MockActionEvent({'entity': '???'})
        self.harness.charm.on_rotate_key_action(event)
        event.fail.assert_called_once()

    def test_invalid_mgr(self):
        event = test_utils.MockActionEvent({'entity': 'mgr-123'})
        self.harness.charm.on_rotate_key_action(event)
        event.fail.assert_called_once()

    @mock.patch('builtins.open', new_callable=mock.mock_open,
                read_data=MGR_KEYRING_FILE)
    @mock.patch.object(rotate_key.systemd, 'service_restart')
    @mock.patch.object(rotate_key.subprocess, 'check_output')
    @mock.patch.object(rotate_key.os, 'listdir')
    def test_rotate_mgr_key(self, listdir, check_output, service_restart,
                            _open):
        listdir.return_value = ['ceph-host-1']
        check_output.return_value = b'[{"pending_key": "new-key"}]'

        event = test_utils.MockActionEvent({'entity': 'mgr.host-1'})
        self.harness.charm.on_rotate_key_action(event)

        event.set_results.assert_called_with({'message': 'success'})
        listdir.assert_called_once_with('/var/lib/ceph/mgr')
        check_output.assert_called_once()
        service_restart.assert_called_once_with('ceph-mgr@host-1.service')

        calls = any(x for x in _open.mock_calls
                    if any(p is not None and 'new-key' in p for p in x.args))
        self.assertTrue(calls)

    @mock.patch.object(rotate_key.subprocess, "check_output")
    def test_rotate_osd_key(self, output):
        class MockUnit:
            def __init__(self, name):
                self.name = name

        bag = {}
        osd_rel = mock.MagicMock(
            units=[MockUnit("ceph-osd/0"), MockUnit("ceph-osd/1")],
            data={1: bag}
        )
        model = mock.MagicMock(
            unit=1,
            relations={"osd": [osd_rel]}
        )

        def _check_output(args):
            if "dump" in args:
                return b'''{"osds":[{"osd":1,"public_addr":"1.1.1.1"},
                                    {"osd":2,"public_addr":"1.1.1.2"}]}'''
            elif "relation-get" in args:
                if args[-1] == "ceph-osd/0":
                    return b'1.1.1.1'
                else:
                    return b'1.1.1.2'
            elif "get-or-create-pending" in args:
                return b'[{"pending_key":"new-key"}]'
            elif "ls" in args:
                return b'1\n2'

        output.side_effect = _check_output
        event = test_utils.MockActionEvent({"entity": "osd.2"})
        rotate_key.rotate_key(event, model=model)

        event.set_results.assert_called_with({"message": "success"})
        data = rotate_key.json.loads(bag["pending_key"])
        self.assertEqual(data, {"2": "new-key"})

        output.reset_mock()
        event = test_utils.MockActionEvent({"entity": "osd"})
        bag.clear()
        rotate_key.rotate_key(event, model=model)

        event.set_results.assert_called_with({"message": "success"})
        data = rotate_key.json.loads(bag["pending_key"])
        self.assertEqual(data, {"1": "new-key", "2": "new-key"})
