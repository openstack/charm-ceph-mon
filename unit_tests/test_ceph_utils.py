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

import json
import mock

import test_utils

from hooks import utils


class CephUtilsTestCase(test_utils.CharmTestCase):

    def setUp(self):
        super().setUp()

    @mock.patch.object(utils, 'related_units')
    @mock.patch.object(utils, 'relation_ids')
    def test_has_rbd_mirrors(self, _relation_ids, _related_units):
        # NOTE(fnordahl): This optimization will not be useful until we get a
        # resolution on LP: #1818245
        # _goal_state.return_value = {'relations': {'rbd-mirror': None}}
        # self.assertTrue(utils.has_rbd_mirrors())
        # _goal_state.assert_called_once_with()
        # _goal_state.side_effect = NotImplementedError
        _relation_ids.return_value = ['arelid']
        _related_units.return_value = ['aunit/0']
        self.assertTrue(utils.has_rbd_mirrors())
        _relation_ids.assert_called_once_with('rbd-mirror')
        _related_units.assert_called_once_with('arelid')

    @mock.patch.object(utils.ceph, 'enabled_manager_modules')
    def test_mgr_module_enabled(self, _enabled_modules):
        _enabled_modules.return_value = []
        self.assertFalse(utils.is_mgr_module_enabled('test-module'))

    @mock.patch.object(utils.ceph, 'enabled_manager_modules')
    def test_mgr_module__is_enabled(self, _enabled_modules):
        _enabled_modules.return_value = ['test-module']
        self.assertTrue(utils.is_mgr_module_enabled('test-module'))

    @mock.patch.object(utils.ceph, 'enabled_manager_modules')
    @mock.patch.object(utils.subprocess, 'check_call')
    def test_mgr_disable_module(self, _call, _enabled_modules):
        _enabled_modules.return_value = ['test-module']
        utils.mgr_disable_module('test-module')
        _call.assert_called_once_with(
            ['ceph', 'mgr', 'module', 'disable', 'test-module'])

    @mock.patch.object(utils.ceph, 'enabled_manager_modules')
    @mock.patch.object(utils.subprocess, 'check_call')
    def test_mgr_enable_module(self, _call, _enabled_modules):
        _enabled_modules.return_value = []
        utils.mgr_enable_module('test-module')
        _call.assert_called_once_with(
            ['ceph', 'mgr', 'module', 'enable', 'test-module'])

    @mock.patch.object(utils.ceph, 'enabled_manager_modules')
    @mock.patch.object(utils.subprocess, 'check_call')
    def test_mgr_enable_module_again(self, _call, _enabled_modules):
        _enabled_modules.return_value = ['test-module']
        utils.mgr_enable_module('test-module')
        _call.assert_not_called()

    @mock.patch.object(utils.subprocess, 'check_output')
    def test_get_default_rbd_features(self, _check_output):
        _check_output.return_value = json.dumps(
            {'a': 'b',
             'rbd_default_features': '61',
             'c': 'd'})
        self.assertEquals(
            utils.get_default_rbd_features(),
            61)
        _check_output.assert_called_once_with(
            ['ceph-conf', '-c', '/dev/null', '-D', '--format', 'json'],
            universal_newlines=True)

    def test_add_mirror_rbd_features(self):
        DEFAULT_FEATURES = 61
        RBD_FEATURE_EXCLUSIVE_LOCK = 4
        RBD_FEATURE_JOURNALING = 64
        COMBINED_FEATURES = (DEFAULT_FEATURES | RBD_FEATURE_EXCLUSIVE_LOCK |
                             RBD_FEATURE_JOURNALING)
        self.assertEqual(utils.add_rbd_mirror_features(DEFAULT_FEATURES),
                         COMBINED_FEATURES)

    @mock.patch.object(utils, 'get_default_rbd_features')
    @mock.patch.object(utils, 'has_rbd_mirrors')
    @mock.patch.object(utils, 'config')
    def test_get_rbd_features(self, _config, _has_rbd_mirrors,
                              _get_default_rbd_features):
        _config.side_effect = \
            lambda key: {'default-rbd-features': 42}.get(key, None)
        self.assertEquals(utils.get_rbd_features(), 42)
        _has_rbd_mirrors.return_value = True
        _get_default_rbd_features.return_value = 61
        _config.side_effect = lambda key: {}.get(key, None)
        self.assertEquals(utils.get_rbd_features(), 125)
        _has_rbd_mirrors.return_value = False
        self.assertEquals(utils.get_rbd_features(), None)

    @mock.patch.object(utils, '_is_required_osd_release')
    @mock.patch.object(utils, '_all_ceph_versions_same')
    @mock.patch.object(utils, '_set_require_osd_release')
    @mock.patch.object(utils, 'log')
    def test_execute_post_osd_upgrade_steps_executes(
            self, log, _set_require_osd_release,
            _all_ceph_versions_same, _is_required_osd_release):
        release = 'luminous'

        _all_ceph_versions_same.return_value = True
        _is_required_osd_release.return_value = False

        utils.execute_post_osd_upgrade_steps(release)

        _set_require_osd_release.assert_called_once_with(release)

    @mock.patch.object(utils, '_is_required_osd_release')
    @mock.patch.object(utils, '_all_ceph_versions_same')
    @mock.patch.object(utils, '_set_require_osd_release')
    @mock.patch.object(utils, 'log')
    def test_execute_post_osd_upgrade_steps_no_exec_already_set(
            self, log, _set_require_osd_release,
            _all_ceph_versions_same, _is_required_osd_release):
        release = 'jewel'

        _all_ceph_versions_same.return_value = True
        _is_required_osd_release.return_value = True

        utils.execute_post_osd_upgrade_steps(release)

        _set_require_osd_release.assert_not_called()

    @mock.patch.object(utils, '_is_required_osd_release')
    @mock.patch.object(utils, '_all_ceph_versions_same')
    @mock.patch.object(utils, '_set_require_osd_release')
    @mock.patch.object(utils, 'log')
    def test_execute_post_osd_upgrade_steps_handle_upgrade_error(
            self, log, _set_require_osd_release,
            _all_ceph_versions_same, _is_required_osd_release):
        release = 'luminous'

        _all_ceph_versions_same.side_effect = utils.OsdPostUpgradeError()

        utils.execute_post_osd_upgrade_steps(release)

        log.assert_called_with(message=mock.ANY, level='ERROR')

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils.json, 'loads')
    @mock.patch.object(utils, 'log')
    def test_all_ceph_versions_same_one_overall_one_osd_true(
            self, log, json_loads, subprocess_check_output):
        mock_versions_dict = dict(
            osd=dict(version_1=1),
            overall=dict(version_1=2)
        )
        json_loads.return_value = mock_versions_dict

        return_bool = utils._all_ceph_versions_same()

        self.assertTrue(
            return_bool,
            msg='all_ceph_versions_same returned False but should be True')
        log.assert_not_called()

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils.json, 'loads')
    @mock.patch.object(utils, 'log')
    def test_all_ceph_versions_same_two_overall_returns_false(
            self, log, json_loads, subprocess_check_output):
        mock_versions_dict = dict(
            osd=dict(version_1=1),
            overall=dict(version_1=1, version_2=2)
        )
        json_loads.return_value = mock_versions_dict

        return_bool = utils._all_ceph_versions_same()

        self.assertFalse(
            return_bool,
            msg='all_ceph_versions_same returned True but should be False')
        log.assert_called_once()

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils.json, 'loads')
    @mock.patch.object(utils, 'log')
    def test_all_ceph_versions_same_one_overall_no_osd_returns_false(
            self, log, json_loads, subprocess_check_output):
        mock_versions_dict = dict(
            osd=dict(),
            overall=dict(version_1=1)
        )
        json_loads.return_value = mock_versions_dict

        return_bool = utils._all_ceph_versions_same()

        self.assertFalse(
            return_bool,
            msg='all_ceph_versions_same returned True but should be False')
        log.assert_called_once()

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils, 'log')
    def test_all_ceph_versions_same_cmd_not_found(
            self, log, subprocess_check_output):
        call_exception = utils.subprocess.CalledProcessError(
            22, mock.MagicMock()
        )
        subprocess_check_output.side_effect = call_exception

        return_bool = utils._all_ceph_versions_same()

        self.assertFalse(return_bool)

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils, 'log')
    def test_all_ceph_versions_same_raise_error_on_unknown_rc(
            self, log, subprocess_check_output):
        call_exception = utils.subprocess.CalledProcessError(
            0, mock.MagicMock()
        )
        subprocess_check_output.side_effect = call_exception

        with self.assertRaises(utils.OsdPostUpgradeError):
            utils._all_ceph_versions_same()

    @mock.patch.object(utils.subprocess, 'check_call')
    @mock.patch.object(utils, 'log')
    def test_set_require_osd_release_success(self, log, check_call):
        release = 'luminous'
        utils._set_require_osd_release(release)
        expected_call = mock.call(
            ['ceph', 'osd', 'require-osd-release', release,
             '--yes-i-really-mean-it']
        )
        check_call.assert_called_with(expected_call.args[0])

    @mock.patch.object(utils.subprocess, 'check_call')
    @mock.patch.object(utils, 'log')
    def test_set_require_osd_release_raise_call_error(self, log, check_call):
        release = 'luminous'
        check_call.side_effect = utils.subprocess.CalledProcessError(
            0, mock.mock.MagicMock()
        )
        expected_call = mock.call(
            ['ceph', 'osd', 'require-osd-release', release,
             '--yes-i-really-mean-it']
        )

        with self.assertRaises(utils.OsdPostUpgradeError):
            utils._set_require_osd_release(release)

        check_call.assert_called_with(expected_call.args[0])
        log.assert_called_once()

    @mock.patch.object(utils, 'relation_ids')
    @mock.patch.object(utils, 'related_units')
    @mock.patch.object(utils, 'relation_get')
    def test_get_ceph_osd_releases_one_release(
            self, relation_get, related_units, relation_ids):
        r_ids = ['a', 'b', 'c']
        r_units = ['1']
        ceph_release = 'mimic'

        relation_ids.return_value = r_ids
        related_units.return_value = r_units
        relation_get.return_value = ceph_release

        releases = utils.get_ceph_osd_releases()

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0], ceph_release)

    @mock.patch.object(utils, 'relation_ids')
    @mock.patch.object(utils, 'related_units')
    @mock.patch.object(utils, 'relation_get')
    def test_get_ceph_osd_releases_two_releases(
            self, relation_get, related_units, relation_ids):
        r_ids = ['a', 'b']
        r_units = ['1']
        ceph_release_1 = 'luminous'
        ceph_release_2 = 'mimic'

        relation_ids.return_value = r_ids
        related_units.return_value = r_units
        relation_get.side_effect = [ceph_release_1, ceph_release_2]

        releases = utils.get_ceph_osd_releases()

        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0], ceph_release_1)
        self.assertEqual(releases[1], ceph_release_2)

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils.json, 'loads')
    def test_is_required_osd_release_not_set_return_false(
            self, loads, check_output):
        release = 'luminous'
        previous_release = 'jewel'
        osd_dump_dict = dict(require_osd_release=previous_release)

        loads.return_value = osd_dump_dict

        return_bool = utils._is_required_osd_release(release)

        self.assertFalse(return_bool)

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils.json, 'loads')
    def test_is_required_osd_release_is_set_return_true(
            self, loads, check_output):
        release = 'luminous'
        osd_dump_dict = dict(require_osd_release=release)

        loads.return_value = osd_dump_dict

        return_bool = utils._is_required_osd_release(release)

        self.assertTrue(return_bool)

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils.json, 'loads')
    def test_is_required_osd_release_subprocess_error(self, loads,
                                                      check_output):
        release = 'luminous'

        call_exception = utils.subprocess.CalledProcessError(
            0, mock.MagicMock()
        )
        check_output.side_effect = call_exception

        with self.assertRaises(utils.OsdPostUpgradeError):
            utils._is_required_osd_release(release)

    @mock.patch.object(utils.subprocess, 'check_output')
    @mock.patch.object(utils.json, 'loads')
    def test_is_required_osd_release_json_loads_error(self, loads,
                                                      check_output):
        release = 'luminous'

        call_exception = utils.json.JSONDecodeError(
            '', mock.MagicMock(), 0
        )
        loads.side_effect = call_exception

        with self.assertRaises(utils.OsdPostUpgradeError):
            utils._is_required_osd_release(release)

    @mock.patch.object(utils.subprocess, 'check_call')
    @mock.patch.object(utils, 'is_mgr_module_enabled')
    @mock.patch.object(utils, 'cmp_pkgrevno')
    def test_balancer_mode(self,
                           cmp_pkgrevno,
                           is_mgr_module_enabled,
                           check_call):
        cmp_pkgrevno.return_value = 0
        is_mgr_module_enabled.return_value = True
        utils.set_balancer_mode('upmap')
        check_call.assert_called_with(['ceph', 'balancer', 'mode',
                                       'upmap'], shell=True)

    @mock.patch.object(utils.subprocess, 'check_call')
    @mock.patch.object(utils, 'cmp_pkgrevno')
    def test_balancer_mode_before_luminous(self,
                                           cmp_pkgrevno,
                                           check_call):
        cmp_pkgrevno.return_value = -1
        utils.set_balancer_mode('upmap')
        check_call.assert_not_called()

    @mock.patch.object(utils.subprocess, 'check_call')
    @mock.patch.object(utils, 'is_mgr_module_enabled')
    @mock.patch.object(utils, 'cmp_pkgrevno')
    def test_balancer_mode_no_balancer(self,
                                       cmp_pkgrevno,
                                       is_mgr_module_enabled,
                                       check_call):
        cmp_pkgrevno.return_value = 0
        is_mgr_module_enabled.return_value = False
        utils.set_balancer_mode('upmap')
        check_call.assert_not_called()
