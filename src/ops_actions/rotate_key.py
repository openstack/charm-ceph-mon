#! /usr/bin/env python3
#
# Copyright 2024 Canonical Ltd
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

"""Rotate the key of one or more entities."""

import configparser
import json
import logging
import os
import subprocess

import charms.operator_libs_linux.v1.systemd as systemd


logger = logging.getLogger(__name__)
MGR_DIR = "/var/lib/ceph/mgr"


def _find_mgr_path(base):
    name = "ceph-" + base
    try:
        if name in os.listdir(MGR_DIR):
            return MGR_DIR + "/" + name
    except FileNotFoundError as exc:
        logger.exception(exc)
        return None


def _create_key(entity, event):
    try:
        cmd = ["sudo", "ceph", "auth", "get-or-create-pending",
               entity, "--format=json"]
        out = subprocess.check_output(cmd).decode("utf-8")
        return json.loads(out)[0]["pending_key"]
    except (subprocess.SubprocessError, json.decoder.JSONDecodeError) as exc:
        logger.exception(exc)
        event.fail("Failed to create key: %s" % str(exc))
        raise


def _replace_keyring_file(path, entity, key, event):
    path += "/keyring"
    try:
        c = configparser.ConfigParser(default_section=None)
        c.read(path)
        c[entity]["key"] = key

        with open(path, "w") as file:
            c.write(file)
    except (KeyError, IOError) as exc:
        logger.exception(exc)
        event.fail("Failed to replace keyring file: %s" % str(exc))
        raise


def _restart_daemon(entity, event):
    try:
        systemd.service_restart(entity)
    except systemd.SystemdError as exc:
        logger.exception(exc)
        event.fail("Failed to reload daemon: %s" % str(exc))
        raise


def _handle_rgw_key_rotation(entity, event, model):
    rgw_name = entity[7:]   # Skip 'client.'
    relations = model.relations.get('radosgw')
    if not relations:
        event.fail('No RadosGW relations found')
        return

    for relation in relations:
        for unit in relation.units:
            try:
                data = relation.data
                if data[unit]["key_name"] != rgw_name:
                    continue
            except KeyError:
                logger.exception('key name not found in relation data bag')
                continue

            data[model.unit][rgw_name + "_key"] = _create_key(entity, event)
            event.set_results({"message": "success"})
            return

    event.fail("Entity %s not found" % entity)


def _find_mds_unit(relations, mds_name):
    for relation in relations:
        for unit in relation.units:
            try:
                if mds_name == relation.data[unit]['mds-name']:
                    return relation.data
            except KeyError:
                logger.exception('mds name not found in relation data bag')


def _handle_mds_key_rotation(entity, event, model):
    mds_name = entity[4:]
    relations = model.relations.get('mds')
    if not relations:
        event.fail('No mds relations found')
        return

    bag = _find_mds_unit(relations, mds_name)
    if bag is None:
        event.fail('No unit found for entity: %s' % entity)
        return

    pending_key = _create_key(entity, event)
    bag[model.unit][mds_name + "_mds_key"] = pending_key
    event.set_results({'message': 'success'})


def rotate_key(event, model=None) -> None:
    """Rotate the key of the specified entity."""
    entity = event.params.get("entity")
    if entity.startswith("mgr"):
        if len(entity) > 3:
            if entity[3] != '.':
                event.fail("Invalid entity name: %s" % entity)
                return
            path = _find_mgr_path(entity[4:])
            if path is None:
                event.fail("Entity %s not found" % entity)
                return
        else:   # just 'mgr'
            try:
                path = MGR_DIR + "/" + os.listdir(MGR_DIR)[0]
                entity = "mgr." + os.path.basename(path)[5:]   # skip 'ceph-'
            except Exception:
                event.fail("No managers found")
                return

        key = _create_key(entity, event)
        _replace_keyring_file(path, entity, key, event)
        _restart_daemon("ceph-mgr@%s.service" % entity[4:], event)
        event.set_results({"message": "success"})
    elif entity.startswith('client.rgw.'):
        _handle_rgw_key_rotation(entity, event, model)
    elif entity.startswith('mds.'):
        _handle_mds_key_rotation(entity, event, model)
    else:
        event.fail("Unknown entity: %s" % entity)
