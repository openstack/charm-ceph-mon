# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Provide ceph metrics to prometheus

Configure prometheus scrape jobs via the metrics-endpoint relation.
"""
import json
import logging
import os.path
import pathlib
import socket

from typing import Optional, Union, List, TYPE_CHECKING

import ops.model

if TYPE_CHECKING:
    import charm

from charms.prometheus_k8s.v0 import prometheus_scrape
from charms.grafana_agent.v0 import cos_agent
from charms_ceph import utils as ceph_utils
from ops.framework import BoundEvent
from utils import mgr_config_set_rbd_stats_pools


logger = logging.getLogger(__name__)

DEFAULT_CEPH_JOB = {
    "metrics_path": "/metrics",
    "static_configs": [{"targets": ["*:9283"]}],
}
DEFAULT_CEPH_METRICS_ENDPOINT = {
    "path": "/metrics",
    "port": 9283,
}
DEFAULT_ALERT_RULES_RELATIVE_PATH = "files/prometheus_alert_rules"


class CephMetricsEndpointProvider(prometheus_scrape.MetricsEndpointProvider):
    def __init__(
        self,
        charm: "charm.CephMonCharm",
        relation_name: str = prometheus_scrape.DEFAULT_RELATION_NAME,
        jobs=None,
        alert_rules_path: str = DEFAULT_ALERT_RULES_RELATIVE_PATH,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        if jobs is None:
            jobs = [DEFAULT_CEPH_JOB]
        super().__init__(
            charm,
            relation_name=relation_name,
            jobs=jobs,
            alert_rules_path=alert_rules_path,
            refresh_event=refresh_event,
        )
        events = charm.on[relation_name]
        self.framework.observe(
            events.relation_departed, self._on_relation_departed
        )
        self.framework.observe(
            self.on.alert_rule_status_changed,
            self._on_alert_rule_status_changed,
        )
        charm._stored.set_default(alert_rule_errors=None)

    def _on_relation_changed(self, event):
        """Enable prometheus on relation change"""
        if not self._charm.unit.is_leader():
            return

        if not ceph_utils.is_bootstrapped():
            logger.debug("not bootstrapped, defer rel changed: %s", event)
            event.defer()
            return

        logger.debug(
            "is_leader and is_bootstrapped, running rel changed: %s", event
        )
        mgr_config_set_rbd_stats_pools()
        ceph_utils.mgr_enable_module("prometheus")
        logger.debug("module_enabled")
        self.update_alert_rules()
        super()._on_relation_changed(event)

    def _on_relation_departed(self, event):
        """Disable prometheus on depart of relation"""
        if self._charm.unit.is_leader() and ceph_utils.is_bootstrapped():
            logger.debug(
                "is_leader and is_bootstrapped, running rel departed: %s",
                event,
            )
            ceph_utils.mgr_disable_module("prometheus")
            logger.debug("module_disabled")
            # We're not related to prom, don't care about alert rules
            self._charm._stored.alert_rule_errors = None

    def have_alert_rule_errors(self):
        return bool(self._charm._stored.alert_rule_errors)

    def _on_alert_rule_status_changed(self, event):
        logger.debug(
            "alert rule status changed: %s, %s, %s",
            event,
            event.valid,
            event.errors,
        )
        if event.errors:
            logger.warning("invalid alert rules: %s", event.errors)
            self._charm._stored.alert_rule_errors = event.errors
        else:
            self._charm._stored.alert_rule_errors = None

    def get_alert_rules_resource(self):
        try:
            return self._charm.model.resources.fetch("alert-rules")
        except ops.model.ModelError as e:
            logger.warning("can't get alert-rules resource: %s", e)

    def _set_alert_rules(self, rules_dict):
        logger.debug("set alert rules: %s", rules_dict)
        # alert rules seem ok locally, clear any errors
        # prometheus may still signal alert rule errors
        # via the relation though
        self._charm._stored.alert_rule_errors = None

        for relation in self._charm.model.relations[self._relation_name]:
            relation.data[self._charm.app]["alert_rules"] = json.dumps(
                rules_dict
            )

    def update_alert_rules(self):
        if self._charm.unit.is_leader() and ceph_utils.is_bootstrapped():
            resource = self.get_alert_rules_resource()
            if resource is None or not os.path.getsize(resource):
                logger.debug("empty rules resource, clearing alert rules")
                self._set_alert_rules({})
                return
            sink = pathlib.Path(self._alert_rules_path) / "alert.yaml.rules"
            if sink.exists() or sink.is_symlink():
                sink.unlink()
            sink.symlink_to(resource)
            alert_rules = prometheus_scrape.AlertRules(topology=self.topology)
            alert_rules.add_path(str(sink), recursive=True)
            alert_rules_as_dict = alert_rules.as_dict()
            if not alert_rules_as_dict:
                msg = "invalid alert rules: {}".format(sink.open().read())
                logger.warning(msg)
                self._charm._stored.alert_rule_errors = msg
                return
            self._set_alert_rules(alert_rules_as_dict)


class CephCOSAgentProvider(cos_agent.COSAgentProvider):

    def __init__(self, charm):
        super().__init__(
            charm,
            metrics_rules_dir="./files/prometheus_alert_rules",
            dashboard_dirs=["./files/grafana_dashboards"],
            scrape_configs=self._custom_scrape_configs,
        )
        events = self._charm.on[cos_agent.DEFAULT_RELATION_NAME]
        self.framework.observe(
            events.relation_departed, self._on_relation_departed
        )

    def _on_refresh(self, event):
        """Enable prometheus on relation change"""
        if not self._charm.unit.is_leader():
            return

        if not ceph_utils.is_bootstrapped():
            logger.debug("not bootstrapped, defer _on_refresh: %s", event)
            event.defer()
            return

        logger.debug("refreshing cos_agent relation")
        mgr_config_set_rbd_stats_pools()
        ceph_utils.mgr_enable_module("prometheus")
        super()._on_refresh(event)

    def _on_relation_departed(self, event):
        """Disable prometheus on depart of relation"""
        if self._charm.unit.is_leader() and ceph_utils.is_bootstrapped():
            logger.debug(
                "is_leader and is_bootstrapped, running rel departed: %s",
                event,
            )
            ceph_utils.mgr_disable_module("prometheus")
            logger.debug("module_disabled")

    def _custom_scrape_configs(self):
        fqdn = socket.getfqdn()
        fqdn_parts = fqdn.split('.')
        domain = '.'.join(fqdn_parts[1:]) if len(fqdn_parts) > 1 else fqdn
        return [
            {
                "metrics_path": "/metrics",
                "static_configs": [{"targets": ["localhost:9283"]}],
                "honor_labels": True,
                "metric_relabel_configs": [
                    {
                        # localhost:9283 is the generic default instance label
                        # added by grafana-agent which is kinda useless.
                        # Replace it with a somewhat more meaningful label
                        "source_labels": ["instance"],
                        "regex": "^localhost:9283$",
                        "target_label": "instance",
                        "action": "replace",
                        "replacement": "ceph_cluster",
                    },
                    {   # if we have a non-empty hostname label, use it as the
                        # instance label
                        "source_labels": ["hostname"],
                        "regex": "(.+)",
                        "target_label": "instance",
                        "action": "replace",
                        "replacement": "${1}",
                    },
                    {   # tack on the domain to the instance label to make it
                        # conform to grafana-agent's node-exporter expectations
                        "source_labels": ["instance"],
                        "regex": "(.*)",
                        "target_label": "instance",
                        "action": "replace",
                        "replacement": "${1}." + domain,
                    },
                ]
            },

        ]
