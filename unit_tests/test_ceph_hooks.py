import copy
import unittest
import sys

from mock import patch, MagicMock, DEFAULT, call

# python-apt is not installed as part of test-requirements but is imported by
# some charmhelpers modules so create a fake import.
mock_apt = MagicMock()
sys.modules['apt'] = mock_apt
mock_apt.apt_pkg = MagicMock()

import charmhelpers.contrib.storage.linux.ceph as ceph
import test_utils

with patch('charmhelpers.contrib.hardening.harden.harden') as mock_dec:
    mock_dec.side_effect = (lambda *dargs, **dkwargs: lambda f:
                            lambda *args, **kwargs: f(*args, **kwargs))
    import ceph_hooks


TO_PATCH = [
    'config',
    'is_leader',
    'is_relation_made',
    'leader_get',
    'leader_set',
    'log',
    'mon_relation',
    'relation_ids',
    'related_units',
    'relation_get',
    'relations_of_type',
    'status_set',
]

CHARM_CONFIG = {'config-flags': '',
                'auth-supported': False,
                'fsid': '1234',
                'loglevel': 1,
                'use-syslog': True,
                'osd-journal-size': 1024,
                'use-direct-io': True,
                'osd-format': 'ext4',
                'monitor-hosts': '',
                'prefer-ipv6': False,
                'default-rbd-features': None}


class CephHooksTestCase(unittest.TestCase):
    def setUp(self):
        super(CephHooksTestCase, self).setUp()

    @patch.object(ceph_hooks, 'get_public_addr', lambda *args: "10.0.0.1")
    @patch.object(ceph_hooks, 'get_cluster_addr', lambda *args: "10.1.0.1")
    @patch.object(ceph_hooks, 'cmp_pkgrevno', lambda *args: 1)
    @patch.object(ceph_hooks, 'get_mon_hosts', lambda *args: ['10.0.0.1',
                                                              '10.0.0.2'])
    @patch.object(ceph_hooks, 'get_networks', lambda *args: "")
    @patch.object(ceph_hooks, 'leader_get', lambda *args: '1234')
    @patch.object(ceph, 'config')
    @patch.object(ceph_hooks, 'config')
    def test_get_ceph_context(self, mock_config, mock_config2):
        config = copy.deepcopy(CHARM_CONFIG)
        mock_config.side_effect = lambda key: config[key]
        mock_config2.side_effect = lambda key: config[key]
        ctxt = ceph_hooks.get_ceph_context()
        expected = {'auth_supported': False,
                    'ceph_cluster_network': '',
                    'ceph_public_network': '',
                    'cluster_addr': '10.1.0.1',
                    'dio': 'true',
                    'fsid': '1234',
                    'loglevel': 1,
                    'mon_hosts': '10.0.0.1 10.0.0.2',
                    'old_auth': False,
                    'public_addr': '10.0.0.1',
                    'use_syslog': 'true'}
        self.assertEqual(ctxt, expected)

    @patch.object(ceph_hooks, 'get_public_addr', lambda *args: "10.0.0.1")
    @patch.object(ceph_hooks, 'get_cluster_addr', lambda *args: "10.1.0.1")
    @patch.object(ceph_hooks, 'cmp_pkgrevno',
                  lambda pkg, ver: -1 if ver == '12.1.0' else 1)
    @patch.object(ceph_hooks, 'get_mon_hosts', lambda *args: ['10.0.0.1',
                                                              '10.0.0.2'])
    @patch.object(ceph_hooks, 'get_networks', lambda *args: "")
    @patch.object(ceph_hooks, 'leader_get', lambda *args: '1234')
    @patch.object(ceph, 'config')
    @patch.object(ceph_hooks, 'config')
    def test_get_ceph_context_rbd_features(self, mock_config, mock_config2):
        config = copy.deepcopy(CHARM_CONFIG)
        config['default-rbd-features'] = 1
        mock_config.side_effect = lambda key: config[key]
        mock_config2.side_effect = lambda key: config[key]
        ctxt = ceph_hooks.get_ceph_context()
        expected = {'auth_supported': False,
                    'ceph_cluster_network': '',
                    'ceph_public_network': '',
                    'cluster_addr': '10.1.0.1',
                    'dio': 'true',
                    'fsid': '1234',
                    'loglevel': 1,
                    'mon_hosts': '10.0.0.1 10.0.0.2',
                    'old_auth': False,
                    'public_addr': '10.0.0.1',
                    'use_syslog': 'true',
                    'rbd_features': 1}
        self.assertEqual(ctxt, expected)

    @patch.object(ceph_hooks, 'get_public_addr', lambda *args: "10.0.0.1")
    @patch.object(ceph_hooks, 'get_cluster_addr', lambda *args: "10.1.0.1")
    @patch.object(ceph_hooks, 'cmp_pkgrevno', lambda *args: 1)
    @patch.object(ceph_hooks, 'get_mon_hosts', lambda *args: ['10.0.0.1',
                                                              '10.0.0.2'])
    @patch.object(ceph_hooks, 'get_networks', lambda *args: "")
    @patch.object(ceph_hooks, 'leader_get', lambda *args: '1234')
    @patch.object(ceph, 'config')
    @patch.object(ceph_hooks, 'config')
    def test_get_ceph_context_w_config_flags(self, mock_config, mock_config2):
        config = copy.deepcopy(CHARM_CONFIG)
        config['config-flags'] = '{"mon": {"mon sync max retries": 10}}'
        mock_config.side_effect = lambda key: config[key]
        mock_config2.side_effect = lambda key: config[key]
        ctxt = ceph_hooks.get_ceph_context()
        expected = {'auth_supported': False,
                    'ceph_cluster_network': '',
                    'ceph_public_network': '',
                    'cluster_addr': '10.1.0.1',
                    'dio': 'true',
                    'fsid': '1234',
                    'loglevel': 1,
                    'mon_hosts': '10.0.0.1 10.0.0.2',
                    'old_auth': False,
                    'mon': {'mon sync max retries': 10},
                    'public_addr': '10.0.0.1',
                    'use_syslog': 'true'}
        self.assertEqual(ctxt, expected)

    @patch.object(ceph_hooks, 'get_public_addr', lambda *args: "10.0.0.1")
    @patch.object(ceph_hooks, 'get_cluster_addr', lambda *args: "10.1.0.1")
    @patch.object(ceph_hooks, 'cmp_pkgrevno', lambda *args: 1)
    @patch.object(ceph_hooks, 'get_mon_hosts', lambda *args: ['10.0.0.1',
                                                              '10.0.0.2'])
    @patch.object(ceph_hooks, 'get_networks', lambda *args: "")
    @patch.object(ceph_hooks, 'leader_get', lambda *args: '1234')
    @patch.object(ceph, 'config')
    @patch.object(ceph_hooks, 'config')
    def test_get_ceph_context_w_config_flags_invalid(self, mock_config,
                                                     mock_config2):
        config = copy.deepcopy(CHARM_CONFIG)
        config['config-flags'] = ('{"mon": {"mon sync max retries": 10},'
                                  '"foo": "bar"}')
        mock_config.side_effect = lambda key: config[key]
        mock_config2.side_effect = lambda key: config[key]
        ctxt = ceph_hooks.get_ceph_context()
        expected = {'auth_supported': False,
                    'ceph_cluster_network': '',
                    'ceph_public_network': '',
                    'cluster_addr': '10.1.0.1',
                    'dio': 'true',
                    'fsid': '1234',
                    'loglevel': 1,
                    'mon_hosts': '10.0.0.1 10.0.0.2',
                    'old_auth': False,
                    'mon': {'mon sync max retries': 10},
                    'public_addr': '10.0.0.1',
                    'use_syslog': 'true'}
        self.assertEqual(ctxt, expected)

    def test_nrpe_dependency_installed(self):
        with patch.multiple(ceph_hooks,
                            apt_install=DEFAULT,
                            rsync=DEFAULT,
                            log=DEFAULT,
                            write_file=DEFAULT,
                            nrpe=DEFAULT) as mocks:
            ceph_hooks.update_nrpe_config()
        mocks["apt_install"].assert_called_once_with(
            ["python-dbus", "lockfile-progs"])

    def test_upgrade_charm_with_nrpe_relation_installs_dependencies(self):
        with patch.multiple(
                ceph_hooks,
                apt_install=DEFAULT,
                rsync=DEFAULT,
                log=DEFAULT,
                write_file=DEFAULT,
                nrpe=DEFAULT,
                emit_cephconf=DEFAULT,
                mon_relation_joined=DEFAULT,
                is_relation_made=DEFAULT) as mocks, patch(
                    "charmhelpers.contrib.hardening.harden.config"):
            mocks["is_relation_made"].return_value = True
            ceph_hooks.upgrade_charm()
        mocks["apt_install"].assert_called_with(
            ["python-dbus", "lockfile-progs"])


class RelatedUnitsTestCase(unittest.TestCase):

    _units = {
        'osd:0': ['ceph-osd-a/0',
                  'ceph-osd-a/1',
                  'ceph-osd-a/2'],
        'osd:23': ['ceph-osd-b/1',
                   'ceph-osd-b/2',
                   'ceph-osd-b/3'],
    }

    def setUp(self):
        super(RelatedUnitsTestCase, self).setUp()

    @patch.object(ceph_hooks, 'relation_ids')
    @patch.object(ceph_hooks, 'related_units')
    def test_related_osd_single_relation(self,
                                         related_units,
                                         relation_ids):
        relation_ids.return_value = ['osd:0']
        related_units.side_effect = lambda x: self._units.get(x)
        self.assertTrue(ceph_hooks.related_osds())
        self.assertFalse(ceph_hooks.related_osds(6))
        relation_ids.assert_called_with('osd')
        related_units.assert_called_with('osd:0')

    @patch.object(ceph_hooks, 'relation_ids')
    @patch.object(ceph_hooks, 'related_units')
    def test_related_osd_multi_relation(self,
                                        related_units,
                                        relation_ids):
        relation_ids.return_value = ['osd:0', 'osd:23']
        related_units.side_effect = lambda x: self._units.get(x)
        self.assertTrue(ceph_hooks.related_osds())
        self.assertTrue(ceph_hooks.related_osds(6))
        self.assertFalse(ceph_hooks.related_osds(9))
        relation_ids.assert_called_with('osd')
        related_units.assert_has_calls([
            call('osd:0'),
            call('osd:23')
        ])


class BootstrapSourceTestCase(test_utils.CharmTestCase):

    def setUp(self):
        super(BootstrapSourceTestCase, self).setUp(ceph_hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get
        self.leader_get.side_effect = self.test_leader_settings.get
        self.leader_set.side_effect = self.test_leader_settings.set
        self.relation_get.side_effect = self.test_relation.get
        self.test_config.set('no-bootstrap', True)
        self.is_leader.return_value = True
        self.relation_ids.return_value = ['bootstrap-source:0']
        self.related_units.return_value = ['ceph/0', 'ceph/1', 'ceph/2']

    def test_bootstrap_source_no_bootstrap(self):
        """Ensure the config option of no-bootstrap is set to continue"""
        self.test_config.set('no-bootstrap', False)
        ceph_hooks.bootstrap_source_relation_changed()
        self.status_set.assert_called_once_with('blocked',
                                                'Cannot join the '
                                                'bootstrap-source relation '
                                                'when no-bootstrap is False')

    def test_bootstrap_source_not_leader(self):
        """Ensure the processing is deferred to the leader"""
        self.is_leader.return_value = False
        ceph_hooks.bootstrap_source_relation_changed()
        self.assertEqual(self.leader_set.call_count, 0)

    def test_bootstrap_source_relation_data_not_ready(self):
        """Ensures no bootstrapping done if relation data not present"""
        ceph_hooks.bootstrap_source_relation_changed()
        expected_calls = []
        relid = 'bootstrap-source:0'
        for unit in ('ceph/0', 'ceph/1', 'ceph/2'):
            expected_calls.append(call('monitor-secret', unit, relid))
            expected_calls.append(call('fsid', unit, relid))
        self.relation_get.has_calls(expected_calls)
        self.assertEqual(self.leader_set.call_count, 0)
        self.assertEqual(self.mon_relation.call_count, 0)

    def test_bootstrap_source_good_path(self):
        """Tests the good path where all is setup and relations established"""
        self.test_relation.set({'monitor-secret': 'abcd',
                                'fsid': '1234'})
        ceph_hooks.bootstrap_source_relation_changed()
        self.leader_set.assert_called_with({'fsid': '1234',
                                            'monitor-secret': 'abcd'})
        self.mon_relation.assert_called_once_with()

    def test_bootstrap_source_different_fsid_secret(self):
        """Tests where the bootstrap relation has a different fsid"""
        self.test_relation.set({'monitor-secret': 'abcd',
                                'fsid': '1234'})
        self.test_leader_settings.set({'monitor-secret': 'mysecret',
                                       'fsid': '7890'})
        self.assertRaises(AssertionError,
                          ceph_hooks.bootstrap_source_relation_changed)

    @patch.object(ceph_hooks, 'emit_cephconf')
    @patch.object(ceph_hooks, 'create_sysctl')
    @patch.object(ceph_hooks, 'check_for_upgrade')
    @patch.object(ceph_hooks, 'get_mon_hosts')
    @patch.object(ceph_hooks, 'bootstrap_source_relation_changed')
    def test_config_changed_no_bootstrap_changed(self,
                                                 bootstrap_source_rel_changed,
                                                 get_mon_hosts,
                                                 check_for_upgrade,
                                                 create_sysctl,
                                                 emit_ceph_conf):
        """Tests that changing no-bootstrap invokes the bs relation changed"""
        self.relations_of_type.return_value = []
        self.is_relation_made.return_value = True
        self.test_config.set_changed('no-bootstrap', True)
        ceph_hooks.config_changed()
        bootstrap_source_rel_changed.assert_called_once()

    @patch.object(ceph_hooks, 'get_public_addr')
    def test_get_mon_hosts(self, get_public_addr):
        """Tests that bootstrap-source relations are used"""
        unit_addrs = {
            'mon:0': {
                'ceph-mon/0': '172.16.0.2',
                'ceph-mon/1': '172.16.0.3',
            },
            'bootstrap-source:1': {
                'ceph/0': '172.16.10.2',
                'ceph/1': '172.16.10.3',
                'cehp/2': '172.16.10.4',
            }
        }

        def rel_ids_side_effect(relname):
            for key in unit_addrs.keys():
                if key.split(':')[0] == relname:
                    return [key]
            return None

        def rel_get_side_effect(attr, unit, relid):
            return unit_addrs[relid][unit]

        def rel_units_side_effect(relid):
            if relid in unit_addrs:
                return unit_addrs[relid].keys()
            return []

        self.relation_ids.side_effect = rel_ids_side_effect
        self.related_units.side_effect = rel_units_side_effect
        get_public_addr.return_value = '172.16.0.4'
        self.relation_get.side_effect = rel_get_side_effect
        hosts = ceph_hooks.get_mon_hosts()
        self.assertEqual(hosts, [
            '172.16.0.2:6789', '172.16.0.3:6789', '172.16.0.4:6789',
            '172.16.10.2:6789', '172.16.10.3:6789', '172.16.10.4:6789',
        ])
