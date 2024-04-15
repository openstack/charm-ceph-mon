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


def _get_osd_tree():
    out = subprocess.check_output(["sudo", "ceph", "osd", "dump",
                                   "--format=json"])
    return json.loads(out.decode("utf8")).get("osds", ())


def _get_osd_addr(osd_id, tree=None):
    if tree is None:
        tree = _get_osd_tree()

    for osd in tree:
        if osd.get("osd") != osd_id:
            continue

        addr = osd["public_addr"]
        ix = addr.find(":")
        return addr if ix < 0 else addr[0:ix]


def _get_unit_addr(unit, rel_id):
    out = subprocess.check_output(["relation-get", "--format=json",
                                   "-r", str(rel_id), "private-address", unit])
    return out.decode("utf8").replace('"', '').strip()


def _find_osd_unit(relations, model, osd_id, tree):
    addr = _get_osd_addr(osd_id, tree)
    if not addr:
        return None

    for relation in relations:
        for unit in relation.units:
            if _get_unit_addr(unit.name, relation.id) == addr:
                return relation.data[model.unit]


def _handle_osd_key_rotation(entity, event, model, tree=None):
    osd_rels = model.relations.get("osd")
    if not osd_rels:
        event.fail("No OSD relations found")
        return

    if tree is None:
        tree = _get_osd_tree()

    osd_id = int(entity[4:])
    bag = _find_osd_unit(osd_rels, model, osd_id, tree)
    if bag is not None:
        key = _create_key(entity, event)
        bag["pending_key"] = json.dumps({osd_id: key})
        event.set_results({"message": "success"})
    else:
        event.fail("No OSD matching entity %s found" % entity)


def _add_osd_rotation(rotations, new_bag, osd_id, new_key):
    # NOTE(lmlg): We can't use sets or dicts for relation databags, as they
    # are mutable and don't implement a __hash__ method. So we use a simple
    # (bag, dict) array to map the rotations.
    elem = {osd_id: new_key}
    for bag, data in rotations:
        if bag is new_bag:
            data.update(elem)
            return

    rotations.append((new_bag, elem))


def _get_osd_ids():
    ret = subprocess.check_output(["sudo", "ceph", "osd", "ls"])
    return ret.decode("utf8").split("\n")


def _rotate_all_osds(event, model):
    tree = _get_osd_tree()
    osd_rels = model.relations.get("osd")
    ret = []

    if not osd_rels:
        event.fail("No OSD relations found")
        return

    for osd_id in _get_osd_ids():
        osd_id = osd_id.strip()
        if not osd_id:
            continue

        bag = _find_osd_unit(osd_rels, model, int(osd_id), tree)
        if bag is None:
            continue

        key = _create_key("osd." + osd_id, event)
        _add_osd_rotation(ret, bag, osd_id, key)

    for bag, elem in ret:
        bag["pending_key"] = json.dumps(elem)

    event.set_results({"message": "success"})


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
    elif entity.startswith("client.rgw."):
        _handle_rgw_key_rotation(entity, event, model)
    elif entity == "osd":
        _rotate_all_osds(event, model)
    elif entity.startswith("osd."):
        _handle_osd_key_rotation(entity, event, model)
    else:
        event.fail("Unknown entity: %s" % entity)
