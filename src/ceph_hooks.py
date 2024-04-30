#!/usr/bin/env python3
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

import ast
import json
import os
import subprocess
import sys
import uuid
import pathlib

import tenacity

sys.path.append('lib')
import charms_ceph.utils as ceph
from charms_ceph.broker import (
    process_requests
)

from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import (
    log,
    DEBUG,
    ERROR,
    INFO,
    WARNING,
    config,
    relation_ids,
    related_units,
    is_relation_made,
    relation_get,
    relation_set,
    leader_set, leader_get,
    is_leader,
    remote_unit,
    Hooks,
    service_name,
    relations_of_type,
    status_set,
    local_unit,
)
from charmhelpers.core.host import (
    service_pause,
    mkdir,
    write_file,
    rsync,
    cmp_pkgrevno)
from charmhelpers.fetch import (
    apt_install,
    filter_installed_packages,
    add_source,
)
from charmhelpers.contrib.openstack.alternatives import install_alternative
from charmhelpers.contrib.openstack.utils import (
    clear_unit_paused,
    clear_unit_upgrading,
    get_os_codename_install_source,
    set_unit_paused,
    set_unit_upgrading,
)
from charmhelpers.contrib.network.ip import (
    get_ipv6_addr,
    format_ipv6_addr,
    get_relation_ip,
)
from charmhelpers.core.sysctl import create as create_sysctl
from charmhelpers.core.templating import render
from charmhelpers.contrib.storage.linux.ceph import (
    CephBrokerRq,
    CephConfContext,
    enable_pg_autoscale,
    send_osd_settings,
)
from utils import (
    assert_charm_supports_ipv6,
    get_cluster_addr,
    get_networks,
    get_public_addr,
    get_rbd_features,
    get_ceph_osd_releases,
    execute_post_osd_upgrade_steps,
    mgr_config_set_rbd_stats_pools,
    mgr_disable_module,
    mgr_enable_module,
    is_mgr_module_enabled,
    set_balancer_mode,
    try_disable_insecure_reclaim,
)

from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.contrib.hardening.harden import harden

hooks = Hooks()

NAGIOS_PLUGINS = '/usr/local/lib/nagios/plugins'
NAGIOS_FILE_FOLDER = '/var/lib/nagios'
SCRIPTS_DIR = '/usr/local/bin'
STATUS_FILE = '{}/cat-ceph-status.txt'.format(NAGIOS_FILE_FOLDER)
RADOSGW_STATUS_FILE = ('{}/current-radosgw-admin-sync-status.raw'
                       .format(NAGIOS_FILE_FOLDER))
STATUS_CRONFILE = '/etc/cron.d/cat-ceph-health'
HOST_OSD_COUNT_REPORT = '{}/host-osd-report.json'.format(NAGIOS_FILE_FOLDER)


def get_current_ceph_version():
    try:
        out = subprocess.check_output(['ceph-mon', '-v']).decode('utf-8')
    except subprocess.CalledProcessError as exc:
        log(("failed to get ceph version: %s. check that the ceph-mon "
            "binary is installed and runs correctly") % str(exc),
            level=ERROR)
        return ''

    # ceph version X.Y.Z (HASH) version-name (stable)
    return out.split()[4]


def check_for_upgrade():
    if not ceph.is_bootstrapped():
        log("Ceph is not bootstrapped, skipping upgrade checks.")
        return

    c = hookenv.config()
    old_version = ceph.resolve_ceph_version(c.previous('source') or
                                            'distro')

    if not old_version:
        old_version = get_current_ceph_version()
        if not old_version:
            log(("failed to get ceph version. check that the ceph-mon "
                 "binary is installed and runs correctly"), level=ERROR)
            return

    log('old_version: {}'.format(old_version))

    new_version = ceph.resolve_ceph_version(hookenv.config('source'))
    if not new_version:
        log(("new version not found. make sure the 'source' option has "
             "been set and try again (using 'distro' may help"), level=WARNING)
        return

    old_version_os = get_os_codename_install_source(c.previous('source') or
                                                    'distro')
    new_version_os = get_os_codename_install_source(hookenv.config('source'))

    log('new_version: {}'.format(new_version))

    if (old_version in ceph.UPGRADE_PATHS and
            new_version == ceph.UPGRADE_PATHS[old_version]):
        log("{} to {} is a valid upgrade path.  Proceeding.".format(
            old_version, new_version))
        ceph.roll_monitor_cluster(new_version=new_version,
                                  upgrade_key='admin')
    elif (old_version == new_version and
          old_version_os is not None and
          new_version_os is not None and
          old_version_os < new_version_os):
        # See LP: #1778823
        add_source(hookenv.config('source'), hookenv.config('key'))
        log(("The installation source has changed yet there is no new major "
             "version of Ceph in this new source. As a result no package "
             "upgrade will take effect. Please upgrade manually if you need "
             "to."), level=INFO)
    else:
        # Log a helpful error message
        log("Invalid upgrade path from {} to {}.  "
            "Valid paths are: {}".format(old_version,
                                         new_version,
                                         ceph.pretty_print_upgrade_paths()),
            level=ERROR)


def get_ceph_context():
    networks = get_networks('ceph-public-network')
    public_network = ', '.join(networks)

    networks = get_networks('ceph-cluster-network')
    cluster_network = ', '.join(networks)

    cephcontext = {
        'auth_supported': 'cephx',
        'mon_hosts': config('monitor-hosts') or ' '.join(get_mon_hosts()),
        'fsid': leader_get('fsid'),
        'old_auth': cmp_pkgrevno('ceph', "0.51") < 0,
        'use_syslog': str(config('use-syslog')).lower(),
        'ceph_public_network': public_network,
        'ceph_cluster_network': cluster_network,
        'loglevel': config('loglevel'),
        'dio': str(config('use-direct-io')).lower(),
        'mon_data_avail_warn': int(config('monitor-data-available-warning')),
        'mon_data_avail_crit': int(config('monitor-data-available-critical')),
    }

    if config('prefer-ipv6'):
        cephcontext['ms_bind_ipv4'] = False
        cephcontext['ms_bind_ipv6'] = True

        local_addresses = get_ipv6_addr(dynamic_only=False)
        public_addr = get_public_addr()
        cluster_addr = get_cluster_addr()
        # try binding to the address that juju will pass to other charms
        if public_addr not in local_addresses:
            log(f"Couldn't find a match for our assigned "
                f"public ip {public_addr} "
                f"out of {local_addresses}, "
                f"using default {local_addresses[0]}",
                level=WARNING)
            public_addr = local_addresses[0]

        if cluster_addr not in local_addresses:
            log(f"Couldn't find a match for our assigned "
                f"cluster ip {cluster_addr} "
                f"out of {local_addresses}, "
                f"using default {local_addresses[0]}",
                level=WARNING)
            cluster_addr = local_addresses[0]

        if not public_network:
            cephcontext['public_addr'] = public_addr
        if not cluster_network:
            cephcontext['cluster_addr'] = cluster_addr
    else:
        cephcontext['public_addr'] = get_public_addr()
        cephcontext['cluster_addr'] = get_cluster_addr()

    rbd_features = get_rbd_features()
    if rbd_features:
        cephcontext['rbd_features'] = rbd_features

    if config('disable-pg-max-object-skew'):
        cephcontext['disable_object_skew'] = config(
            'disable-pg-max-object-skew')

    # NOTE(dosaboy): these sections must correspond to what is supported in the
    #                config template.
    sections = ['global', 'mds', 'mon']
    cephcontext.update(CephConfContext(permitted_sections=sections)())
    return cephcontext


def emit_cephconf():
    # Install ceph.conf as an alternative to support
    # co-existence with other charms that write this file
    charm_ceph_conf = "/var/lib/charm/{}/ceph.conf".format(service_name())
    mkdir(os.path.dirname(charm_ceph_conf), owner=ceph.ceph_user(),
          group=ceph.ceph_user())
    render('ceph.conf', charm_ceph_conf, get_ceph_context(), perms=0o644)
    install_alternative('ceph.conf', '/etc/ceph/ceph.conf',
                        charm_ceph_conf, 100)


JOURNAL_ZAPPED = '/var/lib/ceph/journal_zapped'


def update_host_osd_count_report(reset=False):
    """Update report showing hosts->osds. Used for monitoring."""
    current_osd_tree = ceph.get_osd_tree('admin')

    # Convert [CrushLocation,...] -> {<host>: [osdid],...} for easy comparison
    current_host_osd_map = {}
    for osd in current_osd_tree:
        osd_list = current_host_osd_map.get(osd.host, [])
        osd_list.append(osd.identifier)
        current_host_osd_map[osd.host] = osd_list

    pathlib.Path(NAGIOS_FILE_FOLDER).mkdir(parents=True, exist_ok=True)
    if not os.path.isfile(HOST_OSD_COUNT_REPORT) or reset:
        write_file(HOST_OSD_COUNT_REPORT, '{}')

    with open(HOST_OSD_COUNT_REPORT, "r") as f:
        expected_host_osd_map = json.load(f)

    if current_host_osd_map == expected_host_osd_map:
        return

    for host, osd_list in current_host_osd_map.items():
        if host not in expected_host_osd_map:
            expected_host_osd_map[host] = osd_list

        if len(osd_list) > len(expected_host_osd_map[host]):
            # osd list is growing, add them to the expected
            expected_host_osd_map[host] = osd_list

        if len(osd_list) == len(expected_host_osd_map[host]) and \
           osd_list != expected_host_osd_map[host]:
            # different osd ids, maybe hdd swap, refresh
            expected_host_osd_map[host] = osd_list

    write_file(HOST_OSD_COUNT_REPORT,
               json.dumps(expected_host_osd_map))


@hooks.hook('config-changed')
@harden()
def config_changed():
    '''
    Handle config-changed

    :returns: Whether or not relations should be notified after completion.
    :rtype: bool
    '''
    # Get the cfg object so we can see if the no-bootstrap value has changed
    # and triggered this hook invocation
    cfg = config()
    if config('prefer-ipv6'):
        assert_charm_supports_ipv6()

    check_for_upgrade()
    set_balancer_mode(config('balancer-mode'))

    log('Monitor hosts are ' + repr(get_mon_hosts()))

    sysctl_dict = config('sysctl')
    if sysctl_dict:
        create_sysctl(sysctl_dict, '/etc/sysctl.d/50-ceph-charm.conf')
    if relations_of_type('nrpe-external-master'):
        update_nrpe_config()

    if is_leader():
        if not config('no-bootstrap'):
            if not leader_get('fsid') or not leader_get('monitor-secret'):
                if config('fsid'):
                    fsid = config('fsid')
                else:
                    fsid = "{}".format(uuid.uuid1())
                if config('monitor-secret'):
                    mon_secret = config('monitor-secret')
                else:
                    mon_secret = "{}".format(ceph.generate_monitor_secret())
                opts = {
                    'fsid': fsid,
                    'monitor-secret': mon_secret,
                }
                try:
                    leader_set(opts)
                    status_set('maintenance',
                               'Created FSID and Monitor Secret')
                    log("Settings for the cluster are: {}".format(opts))
                except Exception as e:
                    # we're probably not the leader an exception occured
                    # let's log it anyway.
                    log("leader_set failed: {}".format(str(e)))
        elif (cfg.changed('no-bootstrap') and
              is_relation_made('bootstrap-source')):
            # User changed the no-bootstrap config option, we're the leader,
            # and the bootstrap-source relation has been made. The charm should
            # be in a blocked state indicating that the no-bootstrap option
            # must be set. This block is invoked when the user is trying to
            # get out of that scenario by enabling no-bootstrap.
            bootstrap_source_relation_changed()

        # This will only ensure that we are enabled if the 'pg-autotune' option
        # is explicitly set to 'true', and not if it is 'auto' or 'false'
        if (config('pg-autotune') == 'true' and
                cmp_pkgrevno('ceph', '14.2.0') >= 0):
            # The return value of the enable_module call will tell us if the
            # module was already enabled, in which case, we don't need to
            # re-configure the already configured pools
            if mgr_enable_module('pg_autoscaler'):
                ceph.monitor_key_set('admin', 'autotune', 'true')
                for pool in ceph.list_pools():
                    enable_pg_autoscale('admin', pool)
    # unconditionally verify that the fsid and monitor-secret are set now
    # otherwise we exit until a leader does this.
    if leader_get('fsid') is None or leader_get('monitor-secret') is None:
        log('still waiting for leader to setup keys')
        status_set('waiting', 'Waiting for leader to setup keys')
        return

    emit_cephconf()

    # Support use of single node ceph
    if (not ceph.is_bootstrapped() and int(config('monitor-count')) == 1 and
            is_leader()):
        status_set('maintenance', 'Bootstrapping single Ceph MON')
        # the following call raises an exception if it can't add the keyring
        try:
            ceph.bootstrap_monitor_cluster(leader_get('monitor-secret'))
        except FileNotFoundError as e:  # NOQA -- PEP8 is still PY2
            log("Couldn't bootstrap the monitor yet: {}".format(str(e)))
            return
        ceph.wait_for_bootstrap()
        ceph.wait_for_quorum()
        ceph.create_keyrings()
        if cmp_pkgrevno('ceph', '12.0.0') >= 0:
            status_set('maintenance', 'Bootstrapping single Ceph MGR')
            ceph.bootstrap_manager()
        try_disable_insecure_reclaim()
    for relid in relation_ids('dashboard'):
        dashboard_relation(relid)

    mgr_config_set_rbd_stats_pools()

    return True


def get_mon_hosts():
    hosts = []
    addr = get_public_addr()
    hosts.append(format_ipv6_addr(addr) or addr)

    rel_ids = relation_ids('mon')
    if config('no-bootstrap'):
        rel_ids += relation_ids('bootstrap-source')

    for relid in rel_ids:
        for unit in related_units(relid):
            addr = relation_get('ceph-public-address', unit, relid)
            if addr is not None:
                hosts.append(format_ipv6_addr(addr) or addr)

    return sorted(hosts)


def get_peer_units():
    """
    Returns a dictionary of unit names from the mon peer relation with
    a flag indicating whether the unit has presented its address
    """
    units = {}
    units[local_unit()] = True
    for relid in relation_ids('mon'):
        for unit in related_units(relid):
            addr = relation_get('ceph-public-address', unit, relid)
            units[unit] = addr is not None
    return units


@hooks.hook('mon-relation-joined')
def mon_relation_joined():
    public_addr = get_public_addr()
    for relid in relation_ids('mon'):
        relation_set(relation_id=relid,
                     relation_settings={'ceph-public-address': public_addr})


@hooks.hook('bootstrap-source-relation-changed')
def bootstrap_source_relation_changed():
    """Handles relation data changes on the bootstrap-source relation.

    The bootstrap-source relation to share remote bootstrap information with
    the ceph-mon charm. This relation is used to exchange the remote
    ceph-public-addresses which are used for the mon's, the fsid, and the
    monitor-secret.

    :returns: Whether or not relations should be notified after completion.
    :rtype: bool
    ''
    """
    if not config('no-bootstrap'):
        status_set('blocked', 'Cannot join the bootstrap-source relation when '
                              'no-bootstrap is False')
        return

    if not is_leader():
        log('Deferring leader-setting updates to the leader unit')
        return

    curr_fsid = leader_get('fsid')
    curr_secret = leader_get('monitor-secret')
    for relid in relation_ids('bootstrap-source'):
        for unit in related_units(relid=relid):
            mon_secret = relation_get('monitor-secret', unit, relid)
            fsid = relation_get('fsid', unit, relid)

            if not (mon_secret and fsid):
                log('Relation data is not ready as the fsid or the '
                    'monitor-secret are missing from the relation: '
                    'mon_secret = {} and fsid = {} '.format(mon_secret, fsid))
                continue

            if not (curr_fsid or curr_secret):
                curr_fsid = fsid
                curr_secret = mon_secret
            else:
                # The fsids and secrets need to match or the local monitors
                # will fail to join the mon cluster. If they don't,
                # bail because something needs to be investigated.
                assert curr_fsid == fsid, \
                    "bootstrap fsid '{}' != current fsid '{}'".format(
                        fsid, curr_fsid)
                assert curr_secret == mon_secret, \
                    "bootstrap secret '{}' != current secret '{}'".format(
                        mon_secret, curr_secret)
            opts = {
                'fsid': fsid,
                'monitor-secret': mon_secret,
            }
            try:
                leader_set(opts)
                log('Updating leader settings for fsid and monitor-secret '
                    'from remote relation data: {}'.format(opts))
            except Exception as e:
                # we're probably not the leader an exception occured
                # let's log it anyway.
                log("leader_set failed: {}".format(str(e)))

    # The leader unit needs to bootstrap itself as it won't receive the
    # leader-settings-changed hook elsewhere.
    if curr_fsid:
        return mon_relation()


@hooks.hook('prometheus-relation-joined',
            'prometheus-relation-changed')
def prometheus_relation(relid=None, unit=None, prometheus_permitted=None,
                        module_enabled=None):
    log("DEPRECATION warning: relating to the prometheus2 machine charm is "
        "deprecated in favor of COS Lite", level=INFO)
    if not ceph.is_bootstrapped():
        return
    if prometheus_permitted is None:
        prometheus_permitted = cmp_pkgrevno('ceph', '12.2.0') >= 0

    if module_enabled is None:
        module_enabled = (is_mgr_module_enabled('prometheus') or
                          mgr_enable_module('prometheus'))
    log("checking if prometheus module is enabled")
    if prometheus_permitted and module_enabled:
        mgr_config_set_rbd_stats_pools()
        log("Updating prometheus")
        data = {
            'hostname': get_relation_ip('prometheus'),
            'port': 9283,
        }
        relation_set(relation_id=relid,
                     relation_settings=data)
    else:
        log("Couldn't enable prometheus, but are related. "
            "Prometheus is available in Ceph version: {} ; "
            "Prometheus Module is enabled: {}".format(
                prometheus_permitted, module_enabled), level=WARNING)


@hooks.hook('prometheus-relation-departed')
def prometheus_left():
    mgr_disable_module('prometheus')


@hooks.hook('mon-relation-departed',
            'mon-relation-changed',
            'leader-settings-changed',
            'bootstrap-source-relation-departed')
def mon_relation():
    '''
    Handle the mon relation

    :returns: Whether or not relations should be notified after completion.
    :rtype: bool
    '''
    if leader_get('monitor-secret') is None:
        log('still waiting for leader to setup keys')
        status_set('waiting', 'Waiting for leader to setup keys')
        return
    emit_cephconf()

    moncount = int(config('monitor-count'))
    if len(get_mon_hosts()) >= moncount:
        if ceph.is_bootstrapped():
            # The ceph-mon unit chosen for handling broker requests is based on
            # internal Ceph MON leadership and not Juju leadership.  To update
            # the relations on all ceph-mon units after pool creation
            # the unit handling the broker request will update a nonce on the
            # mon relation.
            notify_relations()
            return True
        else:
            if attempt_mon_cluster_bootstrap():
                notify_relations()
                return True
    else:
        log('Not enough mons ({}), punting.'
            .format(len(get_mon_hosts())))


def attempt_mon_cluster_bootstrap():
    status_set('maintenance', 'Bootstrapping MON cluster')
    # the following call raises an exception
    # if it can't add the keyring
    try:
        ceph.bootstrap_monitor_cluster(leader_get('monitor-secret'))
    except FileNotFoundError as e:  # NOQA -- PEP8 is still PY2
        log("Couldn't bootstrap the monitor yet: {}".format(str(e)))
        return False
    ceph.wait_for_bootstrap()
    ceph.wait_for_quorum()
    ceph.create_keyrings()
    if cmp_pkgrevno('ceph', '12.0.0') >= 0:
        status_set('maintenance', 'Bootstrapping Ceph MGR')
        ceph.bootstrap_manager()
    if ceph.monitor_key_exists('admin', 'autotune'):
        autotune = ceph.monitor_key_get('admin', 'autotune')
    else:
        ceph.wait_for_manager()
        autotune = config('pg-autotune')
        if (cmp_pkgrevno('ceph', '14.2.0') >= 0 and
                (autotune == 'true' or
                 autotune == 'auto')):
            ceph.monitor_key_set('admin', 'autotune', 'true')
        else:
            ceph.monitor_key_set('admin', 'autotune', 'false')
    if ceph.monitor_key_get('admin', 'autotune') == 'true':
        try:
            mgr_enable_module('pg_autoscaler')
        except subprocess.CalledProcessError:
            log("Failed to initialize autoscaler, it must be "
                "initialized on the last monitor", level='info')

    try_disable_insecure_reclaim()
    # If we can and want to
    if is_leader() and config('customize-failure-domain'):
        # But only if the environment supports it
        if os.environ.get('JUJU_AVAILABILITY_ZONE'):
            cmds = [
                "ceph osd getcrushmap -o /tmp/crush.map",
                "crushtool -d /tmp/crush.map| "
                "sed 's/step chooseleaf firstn 0 type host/step "
                "chooseleaf firstn 0 type rack/' > "
                "/tmp/crush.decompiled",
                "crushtool -c /tmp/crush.decompiled -o /tmp/crush.map",
                "crushtool -i /tmp/crush.map --test "
                "--num-rep 3 --show-statistics",
                "ceph osd setcrushmap -i /tmp/crush.map"
            ]
            for cmd in cmds:
                try:
                    subprocess.check_call(cmd, shell=True)
                except subprocess.CalledProcessError as e:
                    log("Failed to modify crush map:", level='error')
                    log("Cmd: {}".format(cmd), level='error')
                    log("Error: {}".format(e.output), level='error')
                    break
        else:
            log(
                "Your Juju environment doesn't"
                "have support for Availability Zones"
            )
    return True


def notify_relations(reprocess_broker_requests=False):
    notify_osds(reprocess_broker_requests=reprocess_broker_requests)
    notify_radosgws(reprocess_broker_requests=reprocess_broker_requests)
    notify_rbd_mirrors(reprocess_broker_requests=reprocess_broker_requests)
    notify_prometheus()


def notify_prometheus():
    if relation_ids('prometheus') and ceph.is_bootstrapped():
        prometheus_permitted = cmp_pkgrevno('ceph', '12.2.0') >= 0
        module_enabled = (is_mgr_module_enabled('prometheus') or
                          mgr_enable_module('prometheus'))
    for relid in relation_ids('prometheus'):
        for unit in related_units(relid):
            prometheus_relation(relid=relid, unit=unit,
                                prometheus_permitted=prometheus_permitted,
                                module_enabled=module_enabled)


def notify_osds(reprocess_broker_requests=False):
    for relid in relation_ids('osd'):
        for unit in related_units(relid):
            osd_relation(
                relid=relid, unit=unit,
                reprocess_broker_requests=reprocess_broker_requests)


def notify_radosgws(reprocess_broker_requests=False):
    for relid in relation_ids('radosgw'):
        for unit in related_units(relid):
            radosgw_relation(
                relid=relid, unit=unit,
                reprocess_broker_requests=reprocess_broker_requests)


def notify_rbd_mirrors(reprocess_broker_requests=False):
    for relid in relation_ids('rbd-mirror'):
        for unit in related_units(relid):
            rbd_mirror_relation(
                relid=relid, unit=unit,
                recurse=False,
                reprocess_broker_requests=reprocess_broker_requests)


def req_already_treated(request_id, relid, req_unit):
    """Check if broker request already handled.

    The local relation data holds all the broker request/responses that
    are handled as a dictionary. There will be a single entry for each
    unit that makes broker request in the form of broker-rsp-<unit name>:
    {reqeust-id: <id>, ..}. Verify if request_id exists in the relation
    data broker response for the requested unit.

    :param request_id: Request ID
    :type request_id: str
    :param relid: Relation ID
    :type relid: str
    :param req_unit: Requested unit name
    :type req_unit: str
    :returns: Whether request is already handled
    :rtype: bool
    """
    status = relation_get(rid=relid, unit=local_unit())
    response_key = 'broker-rsp-' + req_unit.replace('/', '-')
    if not status.get(response_key):
        return False
    data = None
    # relation_get returns the value of response key as a dict or json
    # encoded string
    if isinstance(status[response_key], str):
        try:
            data = json.loads(status[response_key])
        except (TypeError, json.decoder.JSONDecodeError):
            log('Not able to decode broker response for relid {} requested'
                'unit {}'.format(relid, req_unit), level=WARNING)
            return False
    else:
        data = status[response_key]
    if data.get('request-id') == request_id:
        return True
    return False


def notify_mons():
    """Update a nonce on the ``mon`` relation.

    This is useful for flagging that our peer mon units should update some of
    their client relations.

    Normally we would have handled this with leader storage, but for the Ceph
    case, the unit handling the broker requests is the Ceph MON leader and not
    necessarilly the Juju leader.

    A non-leader unit has no way of changing data in leader-storage.
    """
    nonce = uuid.uuid4()
    for relid in relation_ids('mon'):
        for unit in related_units(relid):
            relation_set(relation_id=relid,
                         relation_settings={'nonce': nonce})


def get_client_application_name(relid, unit):
    """Retrieve client application name from relation data.

    :param relid: Realtion ID
    :type relid: str
    :param unit: Remote unit name
    :type unit: str
    """
    if not unit:
        unit = remote_unit()
    app_name = relation_get(rid=relid, unit=unit).get(
        'application-name',
        hookenv.remote_service_name(relid=relid))
    return app_name


def retrieve_client_broker_requests():
    """Retrieve broker requests from client-type relations.

    :returns: Map of broker requests by request-id.
    :rtype: List[CephBrokerRq]
    """
    def _get_request(relation_data):
        if 'broker_req' in relation_data:
            rq = CephBrokerRq(raw_request_data=relation_data['broker_req'])
            yield rq.request_id, rq
        # Note that empty return from generator produces empty generator and
        # not None, ref PEP 479
        return

    # we use a dictionary with request_id as key to deduplicate the list.
    # we cannot use the list(set([])) trick here as CephBrokerRq is an
    # unhashable type. We also cannot just pass on the raw request either
    # as we need to intelligently compare them to avoid false negatives
    # due to reordering of keys
    return {
        request_id: request
        # NOTE(fnordahl): the ``rbd-mirror`` endpoint is omitted here as it is
        # typically a consumer of the ouptut of this function
        for endpoint in ('client', 'mds', 'radosgw')
        for relid in relation_ids(endpoint)
        for unit in related_units(relid)
        for request_id, request in _get_request(
            relation_get(rid=relid, unit=unit))
    }.values()


def handle_broker_request(relid, unit, add_legacy_response=False,
                          recurse=True, force=False):
    """Retrieve broker request from relation, process, return response data.

    :param relid: Realtion ID
    :type relid: str
    :param unit: Remote unit name
    :type unit: str
    :param add_legacy_response: (Optional) Adds the legacy ``broker_rsp`` key
                                to the response in addition to the new way.
    :type add_legacy_response: bool
    :param recurse: Whether we should call out to update relation functions or
                    not.  Mainly used to handle recursion when called from
                    notify_rbd_mirrors()
    :type recurse: bool
    :param force: Process broker requests even if they have already been
                    processed.
    :type force: bool
    :returns: Dictionary of response data ready for use with relation_set.
    :rtype: dict
    """
    def _get_broker_req_id(request):
        if isinstance(request, str):
            try:
                req_key = json.loads(request)['request-id']
            except (TypeError, json.decoder.JSONDecodeError):
                log('Not able to decode request id for broker request {}'.
                    format(request),
                    level=WARNING)
                req_key = None
        else:
            req_key = request['request-id']

        return req_key

    response = {}
    if not unit:
        unit = remote_unit()
    settings = relation_get(rid=relid, unit=unit)
    if 'broker_req' in settings:
        broker_req_id = _get_broker_req_id(settings['broker_req'])
        if broker_req_id is None:
            return {}

        if not ceph.is_leader():
            log("Not leader - ignoring broker request {}".format(
                broker_req_id),
                level=DEBUG)
            return {}

        if req_already_treated(broker_req_id, relid, unit) and not force:
            log("Ignoring already executed broker request {}".format(
                broker_req_id),
                level=DEBUG)
            return {}

        rsp = process_requests(settings['broker_req'])
        unit_id = settings.get('unit-name', unit).replace('/', '-')
        unit_response_key = 'broker-rsp-' + unit_id
        response.update({unit_response_key: rsp})
        if add_legacy_response:
            response.update({'broker_rsp': rsp})

        if relation_ids('rbd-mirror'):
            # NOTE(fnordahl): juju relation level data candidate
            # notify mons to flag that the other mon units should update
            # their ``rbd-mirror`` relations with information about new
            # pools.
            log('Notifying peers after processing broker request {}.'.format(
                broker_req_id),
                level=DEBUG)
            notify_mons()

            if recurse:
                # update ``rbd-mirror`` relations for this unit with
                # information about new pools.
                log('Notifying this units rbd-mirror relations after '
                    'processing broker request {}.'.format(broker_req_id),
                    level=DEBUG)
                notify_rbd_mirrors()

    return response


@hooks.hook('osd-relation-joined')
@hooks.hook('osd-relation-changed')
def osd_relation(relid=None, unit=None, reprocess_broker_requests=False):
    if ceph.is_quorum():
        log('mon cluster in quorum - providing fsid & keys')
        public_addr = get_public_addr()
        data = {
            'fsid': leader_get('fsid'),
            'osd_bootstrap_key': ceph.get_osd_bootstrap_key(),
            'auth': 'cephx',
            'ceph-public-address': public_addr,
            'osd_upgrade_key': ceph.get_named_key('osd-upgrade',
                                                  caps=ceph.osd_upgrade_caps),
            'osd_disk_removal_key': ceph.get_named_key(
                'osd-removal',
                caps={
                    'mgr': ['allow *'],
                    'mon': [
                        'allow r',
                        'allow command "osd crush reweight"',
                        'allow command "osd purge"',
                        'allow command "osd destroy"',
                    ]
                }
            ),
            # Provide a key to the osd for use by the crash module:
            # https://docs.ceph.com/en/latest/mgr/crash/
            'client_crash_key': ceph.create_named_keyring(
                'client',
                'crash',
                caps={
                    'mon': ['profile crash'],
                    'mgr': ['profile crash'],
                }
            )
        }

        data.update(handle_broker_request(
            relid, unit, force=reprocess_broker_requests))
        relation_set(relation_id=relid,
                     relation_settings=data)

        if is_leader():
            ceph_osd_releases = get_ceph_osd_releases()
            if len(ceph_osd_releases) == 1:
                execute_post_osd_upgrade_steps(ceph_osd_releases[0])

        # NOTE: radosgw key provision is gated on presence of OSD
        #       units so ensure that any deferred hooks are processed
        notify_radosgws()
        notify_rbd_mirrors()
        send_osd_settings()

        for dashboard_relid in relation_ids('dashboard'):
            dashboard_relation(dashboard_relid)

        if ready_for_service():
            update_host_osd_count_report()

        if is_leader():
            osd_host = relation_get(rid=relid, unit=unit, attribute='osd-host')
            osd = f"osd/host:{osd_host}"
            osd_memory_target = relation_get(
                rid=relid, unit=unit, attribute='osd-memory-target'
            )
            if all([osd_host, osd_memory_target]):
                ceph.ceph_config_set(
                    "osd_memory_target",
                    osd_memory_target,
                    osd,
                )

    else:
        log('mon cluster not in quorum - deferring fsid provision')


def related_osds(num_units=3):
    '''
    Determine whether there are OSD units currently related

    @param num_units: The minimum number of units required
    @return: boolean indicating whether the required number of
             units where detected.
    '''
    units = 0
    for r_id in relation_ids('osd'):
        units += len(related_units(r_id))
    if units >= num_units:
        return True
    return False


def sufficient_osds(minimum_osds=3):
    '''
    Determine if the minimum number of OSD's have been
    bootstrapped into the cluster.

    @param expected_osds: The minimum number of OSD's required
    @return: boolean indicating whether the required number of
             OSD's where detected.
    '''
    bootstrapped_osds = 0
    for r_id in relation_ids('osd'):
        for unit in related_units(r_id):
            unit_osds = relation_get(
                attribute='bootstrapped-osds',
                unit=unit, rid=r_id
            )
            if unit_osds is not None:
                bootstrapped_osds += int(unit_osds)
    if bootstrapped_osds >= minimum_osds:
        return True
    return False


def ready_for_service():
    '''
    Determine whether the Ceph cluster is ready to service
    storage traffic from clients

    @return: boolean indicating whether the Ceph cluster is
             ready for pool creation/client usage.
    '''
    if not ceph.is_quorum():
        log('mon cluster is not in quorum', level=DEBUG)
        return False
    if is_leader():
        if leader_get('bootstrapped-osds') is None and \
                not sufficient_osds(config('expected-osd-count') or 3):
            log('insufficient osds bootstrapped', level=DEBUG)
            return False
        leader_set({'bootstrapped-osds': True})
    else:
        if leader_get('bootstrapped-osds') is None:
            return False
    return True


@hooks.hook('dashboard-relation-joined')
def dashboard_relation(relid=None):
    """Inform dashboard that mons are ready"""
    if not ready_for_service():
        log("mon cluster is not in quorum, dashboard notification skipped",
            level=WARNING)
        return

    relation_set(relation_id=relid, relation_settings={'mon-ready': True})


@hooks.hook('radosgw-relation-changed')
@hooks.hook('radosgw-relation-joined')
def radosgw_relation(relid=None, unit=None, reprocess_broker_requests=False):
    # Install radosgw for admin tools
    apt_install(packages=filter_installed_packages(['radosgw']))
    if not unit:
        unit = remote_unit()
    if is_unsupported_cmr(unit):
        return

    # NOTE: radosgw needs some usage OSD storage, so defer key
    #       provision until OSD units are detected.
    if ready_for_service():
        log('mon cluster in quorum and osds bootstrapped '
            '- providing radosgw with keys')
        public_addr = get_public_addr()
        data = {
            'fsid': leader_get('fsid'),
            'auth': 'cephx',
            'ceph-public-address': public_addr,
        }
        key_name = relation_get('key_name', unit=unit, rid=relid)
        if key_name:
            # New style, per unit keys
            data['{}_key'.format(key_name)] = (
                ceph.get_radosgw_key(name=key_name)
            )
        else:
            # Old style global radosgw key
            data['radosgw_key'] = ceph.get_radosgw_key()

        data.update(handle_broker_request(
            relid, unit, force=reprocess_broker_requests))
        relation_set(relation_id=relid, relation_settings=data)


@hooks.hook('rbd-mirror-relation-joined')
@hooks.hook('rbd-mirror-relation-changed')
def rbd_mirror_relation(
        relid=None, unit=None, recurse=True,
        reprocess_broker_requests=False):
    '''
    Handle the rbd mirror relation

    :param recurse: Whether we should call out to update relation functions or
                    not.  Mainly used to handle recursion when called from
                    notify_rbd_mirrors()
    :type recurse: bool
    :returns: Whether or not relations should be notified after completion.
    :rtype: bool
    '''
    if ready_for_service():
        log('mon cluster in quorum and osds bootstrapped '
            '- providing rbd-mirror client with keys')
        if not unit:
            unit = remote_unit()
        if is_unsupported_cmr(unit):
            return

        # Add some tenacity in getting pool details
        @tenacity.retry(wait=tenacity.wait_exponential(max=20),
                        reraise=True)
        def get_pool_details():
            return ceph.list_pools_detail()

        # handle broker requests first to get a updated pool map
        data = (handle_broker_request(
            relid, unit, recurse=recurse, force=reprocess_broker_requests))
        data.update({
            'auth': 'cephx',
            'ceph-public-address': get_public_addr(),
            'pools': json.dumps(get_pool_details(), sort_keys=True),
            'broker_requests': json.dumps(
                [rq.request for rq in retrieve_client_broker_requests()],
                sort_keys=True),
        })
        cluster_addr = get_cluster_addr()
        if cluster_addr:
            data['ceph-cluster-address'] = cluster_addr
        # handle both classic and reactive Endpoint peers
        try:
            unique_id = json.loads(
                relation_get('unique_id', unit=unit, rid=relid))
        except (TypeError, json.decoder.JSONDecodeError):
            unique_id = relation_get('unique_id', unit=unit, rid=relid)
        if unique_id:
            data['{}_key'.format(unique_id)] = ceph.get_rbd_mirror_key(
                'rbd-mirror.{}'.format(unique_id))

        relation_set(relation_id=relid, relation_settings=data)

        # make sure clients are updated with the appropriate RBD features
        # bitmap.
        if recurse:
            return True


@hooks.hook('mds-relation-changed')
@hooks.hook('mds-relation-joined')
def mds_relation_joined(
        relid=None, unit=None, reprocess_broker_requests=False):
    if ready_for_service():
        log('mon cluster in quorum and osds bootstrapped '
            '- providing mds client with keys')
        mds_name = relation_get(attribute='mds-name',
                                rid=relid, unit=unit)
        if not unit:
            unit = remote_unit()
        if is_unsupported_cmr(unit):
            return
        public_addr = get_public_addr()
        data = {
            'fsid': leader_get('fsid'),
            '{}_mds_key'.format(mds_name):
                ceph.get_mds_key(name=mds_name),
            'auth': 'cephx',
            'ceph-public-address': public_addr}
        data.update(
            handle_broker_request(
                relid, unit, force=reprocess_broker_requests))
        relation_set(relation_id=relid, relation_settings=data)


@hooks.hook('admin-relation-changed')
@hooks.hook('admin-relation-joined')
def admin_relation_joined(relid=None):
    if is_unsupported_cmr(remote_unit()):
        return
    if ceph.is_quorum():
        name = relation_get('keyring-name')
        if name is None:
            name = 'admin'
        log('mon cluster in quorum - providing admin client with keys')
        mon_hosts = config('monitor-hosts') or ' '.join(get_mon_hosts())
        data = {'key': ceph.get_named_key(name=name, caps=ceph.admin_caps),
                'fsid': leader_get('fsid'),
                'auth': 'cephx',
                'mon_hosts': mon_hosts,
                }
        relation_set(relation_id=relid,
                     relation_settings=data)


@hooks.hook('upgrade-charm.real')
@harden()
def upgrade_charm():
    emit_cephconf()
    apt_install(packages=filter_installed_packages(
        ceph.determine_packages()), fatal=True)
    try:
        # we defer and explicitly run `ceph-create-keys` from
        # add_keyring_to_ceph() as part of bootstrap process
        # LP: #1719436.
        service_pause('ceph-create-keys')
    except ValueError:
        pass
    ceph.update_monfs()
    mon_relation_joined()
    if is_relation_made("nrpe-external-master"):
        update_nrpe_config()
    if not ceph.monitor_key_exists('admin', 'autotune'):
        autotune = config('pg-autotune')
        if (cmp_pkgrevno('ceph', '14.2.0') >= 0 and
                (autotune == 'true' or
                 autotune == 'auto')):
            ceph.monitor_key_set('admin', 'autotune', 'true')
        else:
            ceph.monitor_key_set('admin', 'autotune', 'false')

    # NOTE(jamespage):
    # Reprocess broker requests to ensure that any cephx
    # key permission changes are applied
    notify_relations(reprocess_broker_requests=True)


@hooks.hook('nrpe-external-master-relation-joined')
@hooks.hook('nrpe-external-master-relation-changed')
def update_nrpe_config():
    apt_install('lockfile-progs', fatal=True)
    log('Refreshing nagios checks')
    if os.path.isdir(NAGIOS_PLUGINS):
        rsync(os.path.join(os.getenv('CHARM_DIR'), 'files', 'nagios',
                           'check_ceph_status.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_ceph_status.py'))

        rsync(os.path.join(os.getenv('CHARM_DIR'), 'files', 'nagios',
                           'check_ceph_osd_count.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_ceph_osd_count.py'))

        rsync(os.path.join(os.getenv('CHARM_DIR'), 'files', 'nagios',
                           'check_radosgw_sync_status.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_radosgw_sync_status.py'))

    script = os.path.join(SCRIPTS_DIR, 'collect_ceph_status.sh')
    rsync(os.path.join(os.getenv('CHARM_DIR'), 'files',
                       'nagios', 'collect_ceph_status.sh'),
          script)
    cronjob = "{} root {}\n".format('*/5 * * * *', script)
    write_file(STATUS_CRONFILE, cronjob)

    # Find out if nrpe set nagios_hostname
    hostname = nrpe.get_nagios_hostname()
    current_unit = nrpe.get_nagios_unit_name()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    check_cmd = 'check_ceph_status.py -f {} --degraded_thresh {}' \
        ' --misplaced_thresh {}' \
        ' --recovery_rate {}'.format(STATUS_FILE,
                                     config('nagios_degraded_thresh'),
                                     config('nagios_misplaced_thresh'),
                                     config('nagios_recovery_rate'))
    if config('nagios_raise_nodeepscrub'):
        check_cmd = check_cmd + ' --raise_nodeepscrub'
    nrpe_setup.add_check(
        shortname="ceph",
        description='Check Ceph health {{{}}}'.format(current_unit),
        check_cmd=check_cmd
    )

    check_cmd = 'check_ceph_osd_count.py {} '.format(
        HOST_OSD_COUNT_REPORT)
    nrpe_setup.add_check(
        shortname='ceph_osd_count',
        description='Check if osd count matches expected count',
        check_cmd=check_cmd
    )

    if config('nagios_additional_checks'):
        additional_critical = config('nagios_additional_checks_critical')
        x = ast.literal_eval(config('nagios_additional_checks'))

        for key, value in x.items():
            name = "ceph-{}".format(key.replace(" ", ""))
            log("Adding check {}".format(name))
            check_cmd = 'check_ceph_status.py -f {}' \
                ' --additional_check \"{}\"' \
                ' {}'.format(STATUS_FILE, value,
                             "--additional_check_critical"
                             if additional_critical is True else "")
            nrpe_setup.add_check(
                shortname=name,
                description='Additional Ceph checks {{{}}}'.format(
                            current_unit),
                check_cmd=check_cmd
            )
    if config('nagios_check_num_osds'):
        check_cmd = 'check_ceph_status.py -f {} --check_num_osds'.format(
            STATUS_FILE)
        nrpe_setup.add_check(
            shortname='ceph_num_osds',
            description='Check whether all OSDs are up and in',
            check_cmd=check_cmd
        )

    check_cmd = ('check_radosgw_sync_status.py -f {}'
                 .format(RADOSGW_STATUS_FILE))
    if config('nagios_rgw_zones'):
        check_cmd += ' --zones "{}"'.format(config('nagios_rgw_zones'))
    if config('nagios_rgw_additional_checks'):
        x = ast.literal_eval(config('nagios_rgw_additional_checks'))
        for check in x:
            check_cmd += ' --additional_check \"{}\"'.format(check)
    nrpe_setup.add_check(
        shortname='radosgw_multizone',
        description='Check multizone radosgw health',
        check_cmd=check_cmd
    )

    nrpe_setup.write()


VERSION_PACKAGE = 'ceph-common'


def is_cmr_unit(unit_name):
    '''Is the remote unit connected via a cross model relation.

    :param unit_name: Name of unit
    :type unit_name: str
    :returns: Whether unit is connected via cmr
    :rtype: bool
    '''
    return unit_name.startswith('remote-')


def is_unsupported_cmr(unit_name):
    '''If unit is connected via CMR and if that is supported.

    :param unit_name: Name of unit
    :type unit_name: str
    :returns: Whether unit is supported
    :rtype: bool
    '''
    unsupported = False
    if unit_name and is_cmr_unit(unit_name):
        unsupported = not config('permit-insecure-cmr')
    if unsupported:
        log("CMR detected and not supported", "ERROR")
    return unsupported


@hooks.hook('pre-series-upgrade')
def pre_series_upgrade():
    log("Running prepare series upgrade hook", "INFO")
    # NOTE: The Ceph packages handle the series upgrade gracefully.
    # In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm sets the paused and
    # upgrading states.
    set_unit_paused()
    set_unit_upgrading()


@hooks.hook('post-series-upgrade')
def post_series_upgrade():
    log("Running complete series upgrade hook", "INFO")
    # In order to indicate the step of the series upgrade process for
    # administrators and automated scripts, the charm clears the paused and
    # upgrading states.
    clear_unit_paused()
    clear_unit_upgrading()
