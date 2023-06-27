#!/usr/bin/env python3

# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch
import unittest

from ops.testing import Harness

import ceph_metrics  # noqa: avoid circ. import
import charm


class TestCephCharm(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.harness = Harness(charm.CephMonCharm)
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    def test_init(self):
        self.assertTrue(self.harness.charm.framework)
        self.assertTrue(self.harness.charm.metrics_endpoint)
        self.assertTrue(self.harness.charm.ceph_status)

    @patch.object(charm.ceph_client.CephClientProvides, 'notify_all')
    @patch("charm.hooks")
    def test_on_config_changed(self, hooks, _notify_all):
        self.harness.update_config({"permit-insecure-cmr": None})
        hooks.config_changed.assert_called()

    @patch.object(charm.ceph_client.CephClientProvides, 'notify_all')
    @patch("charm.ops_openstack.core.apt_install")
    @patch("charm.ops_openstack.core.apt_update")
    @patch("charm.ops_openstack.core.add_source")
    @patch("charm.ops_openstack.core.OSBaseCharm.update_status")
    @patch("charm.hooks")
    @patch("charm.systemd")
    @patch("charm.apt")
    def test_on_install(
        self,
        _apt,
        _systemd,
        _hooks,
        _update_status,
        _add_source,
        apt_update,
        apt_install,
        _notify_all
    ):
        self.harness.update_config({"permit-insecure-cmr": None})
        self.harness.charm.on.install.emit()
        apt_install.assert_called_with(
            [
                "ceph",
                "gdisk",
                "radosgw",
                "lvm2",
                "parted",
                "smartmontools",
            ],
            fatal=True,
        )
        apt_update.assert_called()

    @patch("charm.hooks")
    def test_on_pre_commit(self, hooks):
        self.harness.charm.on.framework.on.pre_commit.emit()
        hooks.hookenv._run_atexit.assert_called()
