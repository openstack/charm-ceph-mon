#! /usr/bin/python3
import logging

from ops.main import main

import ceph_status
import charms.operator_libs_linux.v0.apt as apt
import charms.operator_libs_linux.v1.systemd as systemd

from ops.charm import CharmEvents
from ops.framework import EventBase, EventSource

import ops_openstack.core
import charms_ceph.utils as ceph
from charms_ceph.broker import (
    process_requests
)
import ceph_hooks as hooks
import ceph_client
import ceph_metrics

import ops_actions


logger = logging.getLogger(__name__)


class NotifyClientEvent(EventBase):
    def __init__(self, handle):
        super().__init__(handle)


class CephCharmEvents(CharmEvents):
    """Custom charm events."""

    notify_clients = EventSource(NotifyClientEvent)


class CephMonCharm(ops_openstack.core.OSBaseCharm):

    release = 'quincy'

    PACKAGES = [
        'ceph', 'gdisk',
        'radosgw', 'lvm2', 'parted', 'smartmontools',
    ]

    on = CephCharmEvents()

    # General charm control callbacks.

    # TODO: Figure out how to do hardening in an operator-framework
    # world

    def _initialise_config(self):
        # The following two lines are a horrible hack to deal with the
        # lifecycle of a charm changing compared to the classic charm.
        # The previous (classic) version of the charm initialised a
        # Config object in the install hook and let it go out of scope.
        # As a result of this, the config_changed processing attempts
        # to upgrade Ceph from distro to the configured release when it
        # runs during the install or upgrade-charm hooks.
        c = hooks.config()
        c.save()

    def on_install(self, event):
        self._initialise_config()
        self.install_pkgs()
        rm_packages = ceph.determine_packages_to_remove()
        if rm_packages:
            apt.remove_package(packages=rm_packages, fatal=True)
        try:
            # we defer and explicitly run `ceph-create-keys` from
            # add_keyring_to_ceph() as part of bootstrap process
            # LP: #1719436.
            systemd.service_pause('ceph-create-keys')
        except systemd.SystemdError:
            pass

    def on_config(self, event):
        if hooks.config_changed():
            self.on.notify_clients.emit()

    def on_pre_series_upgrade(self, event):
        hooks.pre_series_upgrade()

    def on_upgrade(self, event):
        self._initialise_config()
        self.metrics_endpoint.update_alert_rules()
        hooks.upgrade_charm()
        self.on.notify_clients.emit()

    def on_post_series_upgrade(self, event):
        hooks.post_series_upgrade()

    # Relations.
    def on_mon_relation_joined(self, event):
        hooks.mon_relation_joined()

    def on_bootstrap_source_relation_changed(self, event):
        if hooks.bootstrap_source_relation_changed():
            self.on.notify_clients.emit()

    def on_prometheus_relation_joined_or_changed(self, event):
        hooks.prometheus_relation()

    def on_prometheus_relation_departed(self, event):
        hooks.prometheus_left()

    def on_mon_relation(self, event):
        if hooks.mon_relation():
            self.on.notify_clients.emit()

    def on_osd_relation(self, event):
        hooks.osd_relation()
        self.on.notify_clients.emit()

    def on_dashboard_relation_joined(self, event):
        hooks.dashboard_relation()

    def on_radosgw_relation(self, event):
        hooks.radosgw_relation()

    def on_rbd_mirror_relation(self, event):
        if hooks.rbd_mirror_relation():
            self.on.notify_clients.emit()

    def on_mds_relation(self, event):
        hooks.mds_relation_joined()

    def on_admin_relation(self, event):
        hooks.admin_relation_joined()

    def on_nrpe_relation(self, event):
        hooks.update_nrpe_config()

    def on_commit(self, _event):
        self.ceph_status.assess_status()

    # Actions.

    def _observe_action(self, on_action, callable):
        def _make_method(fn):
            return lambda _, event: fn(event)

        method_name = 'on_' + str(on_action.event_kind)
        method = _make_method(callable)
        # In addition to being a method, the action callbacks _must_ have
        # the same '__name__' as their attribute name (this is how lookups
        # work in the operator framework world).
        method.__name__ = method_name

        inst = type(self)
        setattr(inst, method_name, method)
        self.framework.observe(on_action, getattr(self, method_name))

    def is_blocked_insecure_cmr(self):
        remote_block = False
        remote_unit_name = hooks.remote_unit()
        if remote_unit_name and hooks.is_cmr_unit(remote_unit_name):
            remote_block = not self.config['permit-insecure-cmr']
        return remote_block

    def notify_clients(self, _event):
        self.clients.notify_all()
        for relation in self.model.relations['admin']:
            hooks.admin_relation_joined(str(relation.id))

        for relation in self.model.relations['mds']:
            for unit in relation.units:
                hooks.mds_relation_joined(
                    relid=str(relation.id), unit=unit.name)

    def __init__(self, *args):
        super().__init__(*args)
        self._stored.is_started = True

        if self.is_blocked_insecure_cmr():
            logging.error(
                "Not running hook, CMR detected and not supported")
            return

        fw = self.framework

        self.clients = ceph_client.CephClientProvides(self)
        self.metrics_endpoint = ceph_metrics.CephMetricsEndpointProvider(self)
        self.ceph_status = ceph_status.StatusAssessor(self)

        self._observe_action(self.on.change_osd_weight_action,
                             ops_actions.change_osd_weight.change_osd_weight)
        self._observe_action(self.on.copy_pool_action,
                             ops_actions.copy_pool.copy_pool)
        self._observe_action(self.on.create_crush_rule_action,
                             ops_actions.create_crush_rule.create_crush_rule)
        self._observe_action(
            self.on.create_erasure_profile_action,
            ops_actions.create_erasure_profile.create_erasure_profile_action)
        self._observe_action(self.on.get_health_action,
                             ops_actions.get_health.get_health_action)

        fw.observe(self.on.install, self.on_install)
        fw.observe(self.on.config_changed, self.on_config)
        fw.observe(self.on.pre_series_upgrade, self.on_pre_series_upgrade)
        fw.observe(self.on.upgrade_charm, self.on_upgrade)
        fw.observe(self.on.post_series_upgrade, self.on_post_series_upgrade)

        fw.observe(self.on.mon_relation_joined, self.on_mon_relation_joined)
        fw.observe(self.on.bootstrap_source_relation_changed,
                   self.on_bootstrap_source_relation_changed)
        fw.observe(self.on.prometheus_relation_joined,
                   self.on_prometheus_relation_joined_or_changed)
        fw.observe(self.on.prometheus_relation_changed,
                   self.on_prometheus_relation_joined_or_changed)
        fw.observe(self.on.prometheus_relation_departed,
                   self.on_prometheus_relation_departed)

        for key in ('mon_relation_departed', 'mon_relation_changed',
                    'leader_settings_changed',
                    'bootstrap_source_relation_departed'):
            fw.observe(getattr(self.on, key), self.on_mon_relation)

        fw.observe(self.on.osd_relation_joined,
                   self.on_osd_relation)
        fw.observe(self.on.osd_relation_changed,
                   self.on_osd_relation)

        fw.observe(self.on.dashboard_relation_joined,
                   self.on_dashboard_relation_joined)

        fw.observe(self.on.radosgw_relation_changed,
                   self.on_radosgw_relation)
        fw.observe(self.on.radosgw_relation_joined,
                   self.on_radosgw_relation)

        fw.observe(self.on.rbd_mirror_relation_changed,
                   self.on_rbd_mirror_relation)
        fw.observe(self.on.rbd_mirror_relation_joined,
                   self.on_rbd_mirror_relation)

        fw.observe(self.on.mds_relation_changed,
                   self.on_mds_relation)
        fw.observe(self.on.mds_relation_joined,
                   self.on_mds_relation)

        fw.observe(self.on.admin_relation_changed,
                   self.on_admin_relation)
        fw.observe(self.on.admin_relation_joined,
                   self.on_admin_relation)

        fw.observe(self.on.nrpe_external_master_relation_joined,
                   self.on_nrpe_relation)
        fw.observe(self.on.nrpe_external_master_relation_changed,
                   self.on_nrpe_relation)

        fw.observe(self.on.notify_clients, self.notify_clients)

    def ready_for_service(self):
        return hooks.ready_for_service()

    def process_broker_request(self, broker_req_id, requests, recurse=True):
        broker_result = process_requests(requests)
        if hooks.relation_ids('rbd-mirror'):
            # NOTE(fnordahl): juju relation level data candidate
            # notify mons to flag that the other mon units should update
            # their ``rbd-mirror`` relations with information about new
            # pools.
            logger.debug('Notifying peers after processing broker'
                         'request {}.'.format(broker_req_id))
            hooks.notify_mons()
            # notify_rbd_mirrors is the only case where this is False
            if recurse:
                # update ``rbd-mirror`` relations for this unit with
                # information about new pools.
                logger.debug(
                    'Notifying this units rbd-mirror relations after '
                    'processing broker request {}.'.format(broker_req_id))
                hooks.notify_rbd_mirrors()
        return broker_result


if __name__ == '__main__':
    main(CephMonCharm)
