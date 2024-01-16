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

import json
import re
import socket
import subprocess
import errno

import tenacity
from charms_ceph import utils as ceph_utils
from charmhelpers.core.hookenv import (
    DEBUG,
    cached,
    config,
    goal_state,
    is_leader,
    log,
    network_get_primary_address,
    related_units,
    relation_ids,
    relation_get,
    status_set,
    unit_get,
)

from charmhelpers.core.host import (
    lsb_release,
    CompareHostReleases,
    cmp_pkgrevno,
)
from charmhelpers.contrib.network.ip import (
    get_address_in_network,
    get_ipv6_addr
)
from charmhelpers.contrib.storage.linux import ceph

import dns.resolver


class OsdPostUpgradeError(Exception):
    """Error class for OSD post-upgrade operations."""
    pass


def enable_pocket(pocket):
    apt_sources = "/etc/apt/sources.list"
    with open(apt_sources, "r") as sources:
        lines = sources.readlines()
    with open(apt_sources, "w") as sources:
        for line in lines:
            if pocket in line:
                sources.write(re.sub('^# deb', 'deb', line))
            else:
                sources.write(line)


def is_mgr_module_enabled(module):
    """Is a given manager module enabled.

    :param module:
    :type module: str
    :returns: Whether the named module is enabled
    :rtype: bool
    """
    return module in ceph.enabled_manager_modules()


def mgr_enable_module(module):
    """Enable a Ceph Manager Module.

    :param module: The module name to enable
    :type module: str

    :raises: subprocess.CalledProcessError
    """
    if not is_mgr_module_enabled(module):
        subprocess.check_call(['ceph', 'mgr', 'module', 'enable', module])
        return True
    return False


def mgr_disable_module(module):
    """Enable a Ceph Manager Module.

    :param module: The module name to enable
    :type module: str

    :raises: subprocess.CalledProcessError
    """
    if is_mgr_module_enabled(module):
        subprocess.check_call(['ceph', 'mgr', 'module', 'disable', module])
        return True
    return False


def set_balancer_mode(mode):
    '''Set the balancer mode used by the Ceph manager.'''
    if not mode:
        return
    elif cmp_pkgrevno('ceph-common', '12.0.0') < 0:
        log('Luminous or later is required to set the balancer mode')
        return
    elif not is_mgr_module_enabled('balancer'):
        log("Balancer module is disabled")
        return

    try:
        subprocess.check_call(['ceph', 'balancer', 'mode', mode], shell=True)
    except subprocess.CalledProcessError:
        log('Failed to set balancer mode:', level='ERROR')


@cached
def get_unit_hostname():
    return socket.gethostname()


@cached
def get_host_ip(hostname=None):
    if config('prefer-ipv6'):
        return get_ipv6_addr()[0]

    hostname = hostname or unit_get('private-address')
    try:
        # Test to see if already an IPv4 address
        socket.inet_aton(hostname)
        return hostname
    except socket.error:
        # This may throw an NXDOMAIN exception; in which case
        # things are badly broken so just let it kill the hook
        answers = dns.resolver.query(hostname, 'A')
        if answers:
            return answers[0].address


@cached
def get_public_addr():
    if config('ceph-public-network'):
        return get_network_addrs('ceph-public-network')[0]

    try:
        return network_get_primary_address('public')
    except NotImplementedError:
        log("network-get not supported", DEBUG)

    return get_host_ip()


@cached
def get_cluster_addr():
    if config('ceph-cluster-network'):
        return get_network_addrs('ceph-cluster-network')[0]

    try:
        return network_get_primary_address('cluster')
    except NotImplementedError:
        log("network-get not supported", DEBUG)

    return get_host_ip()


def get_networks(config_opt='ceph-public-network'):
    """Get all configured networks from provided config option.

    If public network(s) are provided, go through them and return those for
    which we have an address configured.
    """
    networks = config(config_opt)
    if networks:
        networks = networks.split()
        return [n for n in networks if get_address_in_network(n)]

    return []


def get_network_addrs(config_opt):
    """Get all configured public networks addresses.

    If public network(s) are provided, go through them and return the
    addresses we have configured on any of those networks.
    """
    addrs = []
    networks = config(config_opt)
    if networks:
        networks = networks.split()
        addrs = [get_address_in_network(n) for n in networks]
        addrs = [a for a in addrs if a]

    if not addrs:
        if networks:
            msg = ("Could not find an address on any of '%s' - resolve this "
                   "error to retry" % (networks))
            status_set('blocked', msg)
            raise Exception(msg)
        else:
            return [get_host_ip()]

    return addrs


def assert_charm_supports_ipv6():
    """Check whether we are able to support charms ipv6."""
    _release = lsb_release()['DISTRIB_CODENAME'].lower()
    if CompareHostReleases(_release) < "trusty":
        raise Exception("IPv6 is not supported in the charms for Ubuntu "
                        "versions less than Trusty 14.04")


def has_rbd_mirrors():
    """Determine if we have or will have ``rbd-mirror`` charms related.

    :returns: True or False
    :rtype: bool
    """
    try:
        # NOTE(fnordahl): This optimization will not be useful until we get a
        # resolution on LP: #1818245
        raise NotImplementedError
        gs = goal_state()
        return 'rbd-mirror' in gs.get('relations', {})
    except NotImplementedError:
        for relid in relation_ids('rbd-mirror'):
            if related_units(relid):
                return True


def get_default_rbd_features():
    """Get default value for ``rbd_default_features``.

    This is retrieved by asking the installed Ceph binary to show its runtime
    config when using a empty configuration file.

    :returns: Installed Ceph's Default vaule for ``rbd_default_features``
    :rtype: int
    :raises: IndexError, json.JSONDecodeError, subprocess.CalledProcessError
    """
    ceph_conf = json.loads(subprocess.check_output(
        ['ceph-conf', '-c', '/dev/null', '-D', '--format', 'json'],
        universal_newlines=True))
    return int(ceph_conf['rbd_default_features'])


def add_rbd_mirror_features(rbd_features):
    """Take a RBD Features bitmap and add the features required for Mirroring.

    :param rbd_features: Input bitmap
    :type rbd_features: int
    :returns: Bitmap bitwise OR'ed with the features required for Mirroring.
    :rtype: int
    """
    RBD_FEATURE_EXCLUSIVE_LOCK = 4
    RBD_FEATURE_JOURNALING = 64
    return rbd_features | RBD_FEATURE_EXCLUSIVE_LOCK | RBD_FEATURE_JOURNALING


def get_rbd_features():
    """Determine if we should set, and what the rbd default features should be.

    :returns: None or the apropriate value to use
    :rtype: Option[int, None]
    """
    rbd_feature_config = config('default-rbd-features')
    if rbd_feature_config:
        return int(rbd_feature_config)
    elif has_rbd_mirrors():
        return add_rbd_mirror_features(get_default_rbd_features())


def get_ceph_osd_releases():
    ceph_osd_releases = set()
    for r_id in relation_ids('osd'):
        for unit in related_units(r_id):
            ceph_osd_release = relation_get(
                attribute='ceph_release',
                unit=unit, rid=r_id
            )
            if ceph_osd_release is not None:
                ceph_osd_releases.add(ceph_osd_release)
    return list(ceph_osd_releases)


def try_disable_insecure_reclaim():
    """Disable insecure global-id reclaim on supported versions.

    This function will disable insecure global-id reclaim on versions
    of ceph that are supported. Running this on a healthy cluster or
    a cluster that doesn't support the option won't have any effect.
    """
    if is_leader():
        try:
            subprocess.check_call([
                'ceph', '--id', 'admin',
                'config', 'set', 'mon',
                'auth_allow_insecure_global_id_reclaim', 'false'])
        except subprocess.CalledProcessError as e:
            log("Could not disable insecure reclaim: {}".format(e),
                level='ERROR')


def execute_post_osd_upgrade_steps(ceph_osd_release):
    """Executes post-upgrade steps.

    Allows execution of any steps that need to be taken after osd upgrades
    have finished (often specified in ceph upgrade docs).

    :param str ceph_osd_release: the new ceph-osd release.
    """
    log('Executing post-ceph-osd upgrade commands.')
    try:
        if (_all_ceph_versions_same() and
                not _is_required_osd_release(ceph_osd_release)):
            log('Setting require_osd_release to {}.'.format(ceph_osd_release))
            _set_require_osd_release(ceph_osd_release)
    except OsdPostUpgradeError as upgrade_error:
        msg = 'OSD post-upgrade steps failed: {}'.format(
            upgrade_error)
        log(message=msg, level='ERROR')


def _get_versions():
    """Gets the ceph versions.

    Retry if the commands fails to give the cluster time to settle.

    :return tuple: (bool, dict) True if successful, False if not, and a dict
    """
    try:
        versions_command = 'ceph versions'
        versions_str = subprocess.check_output(
            versions_command.split()).decode('UTF-8')
    except subprocess.CalledProcessError as call_error:
        if call_error.returncode == errno.EINVAL:
            log('Calling "ceph versions" failed. Command requires '
                'luminous and above.', level='WARNING')
            return False, {}
        else:
            log('Calling "ceph versions" failed.', level='ERROR')
            raise OsdPostUpgradeError(call_error)
    log('Versions: {}'.format(versions_str), level='DEBUG')
    versions_dict = json.loads(versions_str)
    return True, versions_dict


def _all_ceph_versions_same():
    """Checks that ceph-mon and ceph-osd have converged to the same version.

    :return boolean: True if all same, false if not or command failed.
    """
    ok, versions_dict = _get_versions()
    if not ok:
        return False
    if len(versions_dict['overall']) > 1:
        log('All upgrades of mon and osd have not completed.')
        return False
    if len(versions_dict.get('osd', [])) < 1:
        log('Monitors have converged but no osd versions found.',
            level='WARNING')
        return False
    return True


def _is_required_osd_release(release):
    """Checks to see if require_osd_release is set to input release.

    Runs and parses the ceph osd dump command to determine if
    require_osd_release is set to the input release. If so, return
    True. Else, return False.

    :param str release: the release to check against
    :return bool: True if releases match, else False.
    :raises: OsdPostUpgradeError
    """
    try:
        dump_command = 'ceph osd dump -f json'
        osd_dump_str = subprocess.check_output(
            dump_command.split()).decode('UTF-8')
        osd_dump_dict = json.loads(osd_dump_str)
    except subprocess.CalledProcessError as cmd_error:
        log(message='Command {} failed.'.format(cmd_error.cmd),
            level='ERROR')
        raise OsdPostUpgradeError(cmd_error)
    except json.JSONDecodeError as decode_error:
        log(message='Failed to decode JSON.',
            level='ERROR')
        raise OsdPostUpgradeError(decode_error)
    return osd_dump_dict.get('require_osd_release') == release


def _set_require_osd_release(release):
    """Attempts to set the required_osd_release osd config option.

    :param str release: The release to set option to
    :raises: OsdPostUpgradeError
    """
    try:
        command = 'ceph osd require-osd-release {} ' \
                  '--yes-i-really-mean-it'.format(release)
        subprocess.check_call(command.split())
    except subprocess.CalledProcessError as call_error:
        msg = 'Unable to execute command <{}>'.format(call_error.cmd)
        log(message=msg, level='ERROR')
        raise OsdPostUpgradeError(call_error)


@tenacity.retry(
    wait=tenacity.wait_exponential(multiplier=1, max=10),
    reraise=True,
    stop=tenacity.stop_after_attempt(30))
def mgr_config_set_rbd_stats_pools():
    """Update ceph mgr config with the value from rbd-status-pools config
    """
    if is_leader() and ceph_utils.is_bootstrapped():
        ceph_utils.mgr_config_set(
            'mgr/prometheus/rbd_stats_pools',
            config('rbd-stats-pools')
        )
