# Overview

Ceph is a distributed storage and network file system designed to provide
excellent performance, reliability, and scalability.

This charm deploys a Ceph cluster.

# Usage

The ceph charm has two pieces of mandatory configuration for which no defaults
are provided. You _must_ set these configuration options before deployment or the charm will not work:

    fsid:
        uuid specific to a ceph cluster used to ensure that different
        clusters don't get mixed up - use `uuid` to generate one.

    monitor-secret:
        a ceph generated key used by the daemons that manage to cluster
        to control security.  You can use the ceph-authtool command to
        generate one:

            ceph-authtool /dev/stdout --name=mon. --gen-key

These two pieces of configuration must NOT be changed post bootstrap; attempting
to do this will cause a reconfiguration error and new service units will not join
the existing ceph cluster.

At a minimum you must provide a juju config file during initial deployment
with the fsid and monitor-secret options (contents of cepy.yaml below):

    ceph:
        fsid: ecbb8960-0e21-11e2-b495-83a88f44db01
        monitor-secret: AQD1P2xQiKglDhAA4NGUF5j38Mhq56qwz+45wg==

Boot things up by using:

    juju deploy -n 3 --config ceph.yaml ceph

By default the ceph cluster will not bootstrap until 3 service units have been
deployed and started; this is to ensure that a quorum is achieved prior to adding
storage devices.

## Actions

This charm supports pausing and resuming ceph's health functions on a cluster, for example when doing maintainance on a machine. to pause or resume, call:

`juju action do --unit ceph-mon/0 pause-health` or `juju action do --unit ceph-mon/0 resume-health`

## Scale Out Usage

You can use the Ceph OSD and Ceph Radosgw charms:

- [Ceph OSD](https://jujucharms.com/precise/ceph-osd)
- [Ceph Rados Gateway](https://jujucharms.com/precise/ceph-radosgw)

## Network Space support

This charm supports the use of Juju Network Spaces, allowing the charm to be bound to network space configurations managed directly by Juju.  This is only supported with Juju 2.0 and above.

Network traffic can be bound to specific network spaces using the public (front-side) and cluster (back-side) bindings:

    juju deploy ceph-mon --bind "public=data-space cluster=cluster-space"

alternatively these can also be provided as part of a Juju native bundle configuration:

    ceph-mon:
      charm: cs:xenial/ceph-mon
      num_units: 1
      bindings:
        public: data-space
        cluster: cluster-space

Please refer to the [Ceph Network Reference](http://docs.ceph.com/docs/master/rados/configuration/network-config-ref) for details on how using these options effects network traffic within a Ceph deployment.

**NOTE:** Spaces must be configured in the underlying provider prior to attempting to use them.

**NOTE**: Existing deployments using ceph-*-network configuration options will continue to function; these options are preferred over any network space binding provided if set.

# Contact Information

## Authors 

- Paul Collins <paul.collins@canonical.com>,
- James Page <james.page@ubuntu.com>

Report bugs on [Launchpad](http://bugs.launchpad.net/charms/+source/ceph/+filebug)

## Ceph

- [Ceph website](http://ceph.com)
- [Ceph mailing lists](http://ceph.com/resources/mailing-list-irc/)
- [Ceph bug tracker](http://tracker.ceph.com/projects/ceph)

# Technical Footnotes

This charm uses the new-style Ceph deployment as reverse-engineered from the
Chef cookbook at https://github.com/ceph/ceph-cookbooks, although we selected
a different strategy to form the monitor cluster. Since we don't know the
names *or* addresses of the machines in advance, we use the _relation-joined_
hook to wait for all three nodes to come up, and then write their addresses
to ceph.conf in the "mon host" parameter. After we initialize the monitor
cluster a quorum forms quickly, and OSD bringup proceeds.

See [the documentation](http://ceph.com/docs/master/dev/mon-bootstrap/) for more information on Ceph monitor cluster deployment strategies and pitfalls.
