#!/usr/bin/python
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

import os
import subprocess
import socket
import sys
import uuid

sys.path.append('lib')
import ceph.utils as ceph
from ceph.broker import (
    process_requests
)

from charmhelpers.core import hookenv
from charmhelpers.core.hookenv import (
    log,
    DEBUG,
    config,
    relation_ids,
    related_units,
    is_relation_made,
    relation_get,
    relation_set,
    leader_set, leader_get,
    is_leader,
    remote_unit,
    Hooks, UnregisteredHookError,
    service_name,
    relations_of_type,
    status_set,
    local_unit,
    application_version_set)
from charmhelpers.core.host import (
    service_restart,
    mkdir,
    write_file,
    rsync,
    cmp_pkgrevno)
from charmhelpers.fetch import (
    apt_install,
    apt_update,
    filter_installed_packages,
    add_source,
    get_upstream_version,
)
from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.openstack.alternatives import install_alternative
from charmhelpers.contrib.network.ip import (
    get_ipv6_addr,
    format_ipv6_addr,
)
from charmhelpers.core.sysctl import create as create_sysctl
from charmhelpers.core.templating import render
from charmhelpers.contrib.storage.linux.ceph import (
    CephConfContext)
from utils import (
    get_networks,
    get_public_addr,
    get_cluster_addr,
    assert_charm_supports_ipv6
)

from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.contrib.hardening.harden import harden

hooks = Hooks()

NAGIOS_PLUGINS = '/usr/local/lib/nagios/plugins'
SCRIPTS_DIR = '/usr/local/bin'
STATUS_FILE = '/var/lib/nagios/cat-ceph-status.txt'
STATUS_CRONFILE = '/etc/cron.d/cat-ceph-health'


def check_for_upgrade():
    if not ceph.is_bootstrapped():
        log("Ceph is not bootstrapped, skipping upgrade checks.")
        return

    c = hookenv.config()
    old_version = ceph.resolve_ceph_version(c.previous('source') or
                                            'distro')
    log('old_version: {}'.format(old_version))
    # Strip all whitespace
    new_version = ceph.resolve_ceph_version(hookenv.config('source'))
    log('new_version: {}'.format(new_version))

    if (old_version in ceph.UPGRADE_PATHS and
            new_version == ceph.UPGRADE_PATHS[old_version]):
        log("{} to {} is a valid upgrade path.  Proceeding.".format(
            old_version, new_version))
        ceph.roll_monitor_cluster(new_version=new_version,
                                  upgrade_key='admin')
    else:
        # Log a helpful error message
        log("Invalid upgrade path from {} to {}.  "
            "Valid paths are: {}".format(old_version,
                                         new_version,
                                         ceph.pretty_print_upgrade_paths()))


@hooks.hook('install.real')
@harden()
def install():
    execd_preinstall()
    add_source(config('source'), config('key'))
    apt_update(fatal=True)
    apt_install(packages=ceph.determine_packages(), fatal=True)


def get_ceph_context():
    networks = get_networks('ceph-public-network')
    public_network = ', '.join(networks)

    networks = get_networks('ceph-cluster-network')
    cluster_network = ', '.join(networks)

    cephcontext = {
        'auth_supported': config('auth-supported'),
        'mon_hosts': config('monitor-hosts') or ' '.join(get_mon_hosts()),
        'fsid': leader_get('fsid'),
        'old_auth': cmp_pkgrevno('ceph', "0.51") < 0,
        'use_syslog': str(config('use-syslog')).lower(),
        'ceph_public_network': public_network,
        'ceph_cluster_network': cluster_network,
        'loglevel': config('loglevel'),
        'dio': str(config('use-direct-io')).lower(),
    }

    if config('prefer-ipv6'):
        dynamic_ipv6_address = get_ipv6_addr()[0]
        if not public_network:
            cephcontext['public_addr'] = dynamic_ipv6_address
        if not cluster_network:
            cephcontext['cluster_addr'] = dynamic_ipv6_address
    else:
        cephcontext['public_addr'] = get_public_addr()
        cephcontext['cluster_addr'] = get_cluster_addr()

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


@hooks.hook('config-changed')
@harden()
def config_changed():
    if config('prefer-ipv6'):
        assert_charm_supports_ipv6()

    check_for_upgrade()

    log('Monitor hosts are ' + repr(get_mon_hosts()))

    sysctl_dict = config('sysctl')
    if sysctl_dict:
        create_sysctl(sysctl_dict, '/etc/sysctl.d/50-ceph-charm.conf')
    if relations_of_type('nrpe-external-master'):
        update_nrpe_config()

    if is_leader():
        if not leader_get('fsid') or not leader_get('monitor-secret'):
            if config('fsid'):
                fsid = config('fsid')
            else:
                fsid = "{}".format(uuid.uuid1())
            if config('monitor-secret'):
                mon_secret = config('monitor-secret')
            else:
                mon_secret = "{}".format(ceph.generate_monitor_secret())
            status_set('maintenance', 'Creating FSID and Monitor Secret')
            opts = {
                'fsid': fsid,
                'monitor-secret': mon_secret,
            }
            log("Settings for the cluster are: {}".format(opts))
            leader_set(opts)
    else:
        if leader_get('fsid') is None or leader_get('monitor-secret') is None:
            log('still waiting for leader to setup keys')
            status_set('waiting', 'Waiting for leader to setup keys')
            sys.exit(0)

    emit_cephconf()

    # Support use of single node ceph
    if (not ceph.is_bootstrapped() and int(config('monitor-count')) == 1 and
            is_leader()):
        status_set('maintenance', 'Bootstrapping single Ceph MON')
        ceph.bootstrap_monitor_cluster(leader_get('monitor-secret'))
        ceph.wait_for_bootstrap()
        if cmp_pkgrevno('ceph', '12.0.0') >= 0:
            status_set('maintenance', 'Bootstrapping single Ceph MGR')
            ceph.bootstrap_manager()


def get_mon_hosts():
    hosts = []
    addr = get_public_addr()
    hosts.append('{}:6789'.format(format_ipv6_addr(addr) or addr))

    for relid in relation_ids('mon'):
        for unit in related_units(relid):
            addr = relation_get('ceph-public-address', unit, relid)
            if addr is not None:
                hosts.append('{}:6789'.format(
                    format_ipv6_addr(addr) or addr))

    hosts.sort()
    return hosts


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


@hooks.hook('mon-relation-departed',
            'mon-relation-changed')
def mon_relation():
    if leader_get('monitor-secret') is None:
        log('still waiting for leader to setup keys')
        status_set('waiting', 'Waiting for leader to setup keys')
        return
    emit_cephconf()

    moncount = int(config('monitor-count'))
    if len(get_mon_hosts()) >= moncount:
        status_set('maintenance', 'Bootstrapping MON cluster')
        ceph.bootstrap_monitor_cluster(leader_get('monitor-secret'))
        ceph.wait_for_bootstrap()
        ceph.wait_for_quorum()
        if cmp_pkgrevno('ceph', '12.0.0') >= 0:
            status_set('maintenance', 'Bootstrapping Ceph MGR')
            ceph.bootstrap_manager()
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
                    "crushtool -i /tmp/crush.map --test",
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
        notify_osds()
        notify_radosgws()
        notify_client()
    else:
        log('Not enough mons ({}), punting.'
            .format(len(get_mon_hosts())))


def notify_osds():
    for relid in relation_ids('osd'):
        osd_relation(relid)


def notify_radosgws():
    for relid in relation_ids('radosgw'):
        for unit in related_units(relid):
            radosgw_relation(relid=relid, unit=unit)


def notify_client():
    for relid in relation_ids('client'):
        client_relation_joined(relid)
    for relid in relation_ids('admin'):
        admin_relation_joined(relid)
    for relid in relation_ids('mds'):
        for unit in related_units(relid):
            mds_relation_joined(relid=relid, unit=unit)


@hooks.hook('osd-relation-joined')
@hooks.hook('osd-relation-changed')
def osd_relation(relid=None):
    if ceph.is_quorum():
        log('mon cluster in quorum - providing fsid & keys')
        public_addr = get_public_addr()
        data = {
            'fsid': leader_get('fsid'),
            'osd_bootstrap_key': ceph.get_osd_bootstrap_key(),
            'auth': config('auth-supported'),
            'ceph-public-address': public_addr,
            'osd_upgrade_key': ceph.get_named_key('osd-upgrade',
                                                  caps=ceph.osd_upgrade_caps),
        }

        unit = remote_unit()
        settings = relation_get(rid=relid, unit=unit)
        """Process broker request(s)."""
        if 'broker_req' in settings:
            if ceph.is_leader():
                rsp = process_requests(settings['broker_req'])
                unit_id = unit.replace('/', '-')
                unit_response_key = 'broker-rsp-' + unit_id
                data[unit_response_key] = rsp
            else:
                log("Not leader - ignoring broker request", level=DEBUG)

        relation_set(relation_id=relid,
                     relation_settings=data)
        # NOTE: radosgw key provision is gated on presence of OSD
        #       units so ensure that any deferred hooks are processed
        notify_radosgws()
        notify_client()
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


@hooks.hook('radosgw-relation-changed')
@hooks.hook('radosgw-relation-joined')
def radosgw_relation(relid=None, unit=None):
    # Install radosgw for admin tools
    apt_install(packages=filter_installed_packages(['radosgw']))
    if not unit:
        unit = remote_unit()

    # NOTE: radosgw needs some usage OSD storage, so defer key
    #       provision until OSD units are detected.
    if ceph.is_quorum() and related_osds():
        log('mon cluster in quorum and osds related '
            '- providing radosgw with keys')
        public_addr = get_public_addr()
        data = {
            'fsid': leader_get('fsid'),
            'radosgw_key': ceph.get_radosgw_key(),
            'auth': config('auth-supported'),
            'ceph-public-address': public_addr,
        }

        settings = relation_get(rid=relid, unit=unit)
        """Process broker request(s)."""
        if 'broker_req' in settings:
            if ceph.is_leader():
                rsp = process_requests(settings['broker_req'])
                unit_id = unit.replace('/', '-')
                unit_response_key = 'broker-rsp-' + unit_id
                data[unit_response_key] = rsp
            else:
                log("Not leader - ignoring broker request", level=DEBUG)

        relation_set(relation_id=relid, relation_settings=data)
    else:
        log('mon cluster not in quorum or no osds - deferring key provision')


@hooks.hook('mds-relation-changed')
@hooks.hook('mds-relation-joined')
def mds_relation_joined(relid=None, unit=None):
    if ceph.is_quorum() and related_osds():
        log('mon cluster in quorum and OSDs related'
            '- providing mds client with keys')
        mds_name = relation_get(attribute='mds-name',
                                rid=relid, unit=unit)
        if not unit:
            unit = remote_unit()
        public_addr = get_public_addr()
        data = {
            'fsid': leader_get('fsid'),
            'mds_key': ceph.get_mds_key(name=mds_name),
            'auth': config('auth-supported'),
            'ceph-public-address': public_addr}
        settings = relation_get(rid=relid, unit=unit)
        """Process broker request(s)."""
        if 'broker_req' in settings:
            if ceph.is_leader():
                rsp = process_requests(settings['broker_req'])
                unit_id = unit.replace('/', '-')
                unit_response_key = 'broker-rsp-' + unit_id
                data[unit_response_key] = rsp
            else:
                log("Not leader - ignoring mds broker request", level=DEBUG)

        relation_set(relation_id=relid, relation_settings=data)
    else:
        log('Waiting on mon quorum or min osds before provisioning mds keys')


@hooks.hook('admin-relation-changed')
@hooks.hook('admin-relation-joined')
def admin_relation_joined(relid=None):
    if ceph.is_quorum():
        name = relation_get('keyring-name')
        if name is None:
            name = 'admin'
        log('mon cluster in quorum - providing client with keys')
        mon_hosts = config('monitor-hosts') or ' '.join(get_mon_hosts())
        data = {'key': ceph.get_named_key(name=name, caps=ceph.admin_caps),
                'fsid': leader_get('fsid'),
                'auth': config('auth-supported'),
                'mon_hosts': mon_hosts,
                }
        relation_set(relation_id=relid,
                     relation_settings=data)
    else:
        log('mon cluster not in quorum - deferring key provision')


@hooks.hook('client-relation-joined')
def client_relation_joined(relid=None):
    if ceph.is_quorum():
        log('mon cluster in quorum - providing client with keys')
        service_name = None
        if relid is None:
            units = [remote_unit()]
            service_name = units[0].split('/')[0]
        else:
            units = related_units(relid)
            if len(units) > 0:
                service_name = units[0].split('/')[0]

        if service_name is not None:
            public_addr = get_public_addr()
            data = {'key': ceph.get_named_key(service_name),
                    'auth': config('auth-supported'),
                    'ceph-public-address': public_addr}
            if config('rbd-features'):
                data['rbd_features'] = config('rbd-features')
            relation_set(relation_id=relid,
                         relation_settings=data)
    else:
        log('mon cluster not in quorum - deferring key provision')


@hooks.hook('client-relation-changed')
def client_relation_changed():
    """Process broker requests from ceph client relations."""
    if ceph.is_quorum():
        settings = relation_get()
        if 'broker_req' in settings:
            if not ceph.is_leader():
                log("Not leader - ignoring broker request", level=DEBUG)
            else:
                rsp = process_requests(settings['broker_req'])
                unit_id = remote_unit().replace('/', '-')
                unit_response_key = 'broker-rsp-' + unit_id
                # broker_rsp is being left for backward compatibility,
                # unit_response_key superscedes it
                data = {
                    'broker_rsp': rsp,
                    unit_response_key: rsp,
                }
                relation_set(relation_settings=data)
    else:
        log('mon cluster not in quorum', level=DEBUG)


@hooks.hook('upgrade-charm.real')
@harden()
def upgrade_charm():
    emit_cephconf()
    apt_install(packages=filter_installed_packages(
        ceph.determine_packages()), fatal=True)
    ceph.update_monfs()
    mon_relation_joined()
    if is_relation_made("nrpe-external-master"):
        update_nrpe_config()


@hooks.hook('start')
def start():
    # In case we're being redeployed to the same machines, try
    # to make sure everything is running as soon as possible.
    if ceph.systemd():
        service_restart('ceph-mon')
    else:
        service_restart('ceph-mon-all')
    if cmp_pkgrevno('ceph', '12.0.0') >= 0:
        service_restart('ceph-mgr@{}'.format(socket.gethostname()))


@hooks.hook('nrpe-external-master-relation-joined')
@hooks.hook('nrpe-external-master-relation-changed')
def update_nrpe_config():
    # python-dbus is used by check_upstart_job
    apt_install(['python-dbus', 'lockfile-progs'])
    log('Refreshing nagios checks')
    if os.path.isdir(NAGIOS_PLUGINS):
        rsync(os.path.join(os.getenv('CHARM_DIR'), 'files', 'nagios',
                           'check_ceph_status.py'),
              os.path.join(NAGIOS_PLUGINS, 'check_ceph_status.py'))

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
    nrpe_setup.add_check(
        shortname="ceph",
        description='Check Ceph health {%s}' % current_unit,
        check_cmd='check_ceph_status.py -f {}'.format(STATUS_FILE)
    )
    nrpe_setup.write()


VERSION_PACKAGE = 'ceph-common'


def assess_status():
    '''Assess status of current unit'''
    application_version_set(get_upstream_version(VERSION_PACKAGE))
    moncount = int(config('monitor-count'))
    units = get_peer_units()
    # not enough peers and mon_count > 1
    if len(units.keys()) < moncount:
        status_set('blocked', 'Insufficient peer units to bootstrap'
                              ' cluster (require {})'.format(moncount))
        return

    # mon_count > 1, peers, but no ceph-public-address
    ready = sum(1 for unit_ready in units.itervalues() if unit_ready)
    if ready < moncount:
        status_set('waiting', 'Peer units detected, waiting for addresses')
        return

    # active - bootstrapped + quorum status check
    if ceph.is_bootstrapped() and ceph.is_quorum():
        status_set('active', 'Unit is ready and clustered')
    else:
        # Unit should be running and clustered, but no quorum
        # TODO: should this be blocked or waiting?
        status_set('blocked', 'Unit not clustered (no quorum)')
        # If there's a pending lock for this unit,
        # can i get the lock?
        # reboot the ceph-mon process


@hooks.hook('update-status')
@harden()
def update_status():
    log('Updating status.')


if __name__ == '__main__':
    try:
        hooks.execute(sys.argv)
    except UnregisteredHookError as e:
        log('Unknown hook {} - skipping.'.format(e))
    assess_status()
