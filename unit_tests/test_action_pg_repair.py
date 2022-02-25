# Copyright 2022 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the pg_repair action."""

from actions import pg_repair as action
import unittest.mock as mock
from test_utils import CharmTestCase
import json


class PlacementGroupRepairTestCase(CharmTestCase):
    """Run tests for the action."""

    def setUp(self):
        """Init mocks for test cases."""
        super(PlacementGroupRepairTestCase, self).setUp(
            action,
            [
                "function_fail",
                "function_set",
                "get_rados_inconsistent_objs",
                "get_rados_inconsistent_pgs",
            ],
        )

    @mock.patch("actions.pg_repair.get_rados_inconsistent_pgs")
    def test_get_inconsistent_pgs(self, _rados_inc_pgs):
        """Test collection of all inconsistent placement groups."""
        _rados_inc_pgs.side_effect = (["1.a", "2.b"], ["2.b", "3.c"], [])
        ceph_pools = ["testPool0", "testPool1", "testPool2"]
        result = action.get_inconsistent_pgs(ceph_pools)
        self.assertEqual(result, {"1.a", "2.b", "3.c"})

    @mock.patch("actions.pg_repair.get_rados_inconsistent_objs")
    def test_safe_case_detection(self, _rados_inc_objs):
        """Test that safe case is detected."""
        _rados_inc_objs.return_value = rados_inc_obj_output_safe()
        result = action.is_pg_safe_to_repair("")
        self.assertTrue(result)

    @mock.patch("actions.pg_repair.get_rados_inconsistent_objs")
    def test_unsafe_case_detection_extra_erros(self, _rados_inc_objs):
        """Test that the unsafe case of extra errors is detected."""
        _rados_inc_objs.return_value = rados_inc_obj_output_extra_errors()
        result = action.is_pg_safe_to_repair("")
        self.assertFalse(result)

    @mock.patch("actions.pg_repair.get_rados_inconsistent_objs")
    def test_unsafe_case_detection_multiple_read_errors(self, _rados_inc_objs):
        """Test that the unsafe case of multiple read errors is detected."""
        _rados_inc_objs.return_value = (
            rados_inc_obj_output_multiple_read_errors()
        )
        result = action.is_pg_safe_to_repair("")
        self.assertFalse(result)

    @mock.patch("actions.pg_repair.get_rados_inconsistent_objs")
    def test_get_safe_pg_repair(self, _rados_inc_objs):
        _rados_inc_objs.side_effect = (
            rados_inc_obj_output_safe(),
            rados_inc_obj_output_extra_errors(),
            rados_inc_obj_output_multiple_read_errors(),
        )
        inconsistent_pgs = ("3.1f2", "12.ab3", "16.222")
        result = action.get_safe_pg_repairs(inconsistent_pgs)
        self.assertEqual(result, {"3.1f2"})

    @mock.patch("actions.pg_repair.list_pools")
    def test_pg_repair_no_ceph_pools(self, _list_pools):
        """Test action fails when no Ceph pools found."""
        _list_pools.return_value = []
        action.pg_repair()
        msg = "No Ceph pools found."
        self.function_set.assert_called_once_with(msg)

    @mock.patch("actions.pg_repair.get_inconsistent_pgs")
    @mock.patch("actions.pg_repair.list_pools")
    def test_pg_repair_no_inconsistent_pgs(self, _list_pools, _get_inc_pgs):
        _list_pools.return_value = ["testPool"]
        _get_inc_pgs.return_value = []
        action.pg_repair()
        msg = "No inconsistent placement groups found."
        self.function_set.assert_called_once_with(msg)

    @mock.patch("actions.pg_repair.check_output")
    @mock.patch("actions.pg_repair.get_rados_inconsistent_objs")
    @mock.patch("actions.pg_repair.get_rados_inconsistent_pgs")
    @mock.patch("actions.pg_repair.list_pools")
    def test_pg_repair_safe_case(
        self, _list_pools, _rados_inc_pgs, _rados_inc_objs, _check_output
    ):
        """Test action succeeds with one read error."""
        _list_pools.return_value = ["testPool"]
        _rados_inc_pgs.return_value = {"16.abf", "12.bd4"}
        _rados_inc_objs.return_value = rados_inc_obj_output_safe()
        _check_output.return_value = b""
        action.pg_repair()
        self.function_set.assert_called_once_with(
            {"message": "placement groups repaired: ['12.bd4', '16.abf']"}
        )

    @mock.patch("actions.pg_repair.get_rados_inconsistent_objs")
    @mock.patch("actions.pg_repair.get_rados_inconsistent_pgs")
    @mock.patch("actions.pg_repair.list_pools")
    def test_pg_repair_extra_errors(
        self, _list_pools, _rados_inc_pgs, _rados_inc_objs
    ):
        """Test action fails with errors other than read errors."""
        _list_pools.return_value = ["testPool"]
        _rados_inc_pgs.return_value = {"16.abf", "12.bd4"}
        _rados_inc_objs.return_value = rados_inc_obj_output_extra_errors()
        action.pg_repair()
        self.function_set.assert_called_once()

    @mock.patch("actions.pg_repair.get_rados_inconsistent_objs")
    @mock.patch("actions.pg_repair.get_rados_inconsistent_pgs")
    @mock.patch("actions.pg_repair.list_pools")
    def test_pg_repair_multiple_read_errors(
        self, _list_pools, _rados_inc_pgs, _rados_inc_objs
    ):
        """Test action fails with multiple read errors."""
        _list_pools.return_value = ["testPool"]
        _rados_inc_pgs.return_value = {"16.abf", "12.bd4"}
        _rados_inc_objs.return_value = (
            rados_inc_obj_output_multiple_read_errors()
        )
        action.pg_repair()
        self.function_set.assert_called_once()


def rados_inc_obj_output_safe():
    return json.loads("""{
        "epoch": 873,
        "inconsistents": [
            {
                "object": {
                    "data": "nothing to see here"
                },
                "errors": [],
                "union_shard_errors": [
                    "read_error"
                ],
                "selected_object_info": {
                    "data": "nothing to see here"
                },
                "shards": [
                    {
                        "osd": 53,
                        "primary": true,
                        "errors": [
                            "read_error"
                        ],
                        "size": 4046848
                    },
                    {
                        "osd": 56,
                        "primary": false,
                        "errors": [],
                        "size": 4046848,
                        "omap_digest": "0xffffffff",
                        "data_digest": "0xb86056e7"
                    },
                    {
                        "osd": 128,
                        "primary": false,
                        "errors": [],
                        "size": 4046848,
                        "omap_digest": "0xffffffff",
                        "data_digest": "0xb86056e7"
                    }
                ]
            }
        ]
    }""")


def rados_inc_obj_output_extra_errors():
    return json.loads("""{
        "epoch": 873,
        "inconsistents": [
            {
                "object": {
                    "data": "nothing to see here"
                },
                "errors": [],
                "union_shard_errors": [
                    "read_error"
                ],
                "selected_object_info": {
                    "data": "nothing to see here"
                },
                "shards": [
                    {
                        "osd": 53,
                        "primary": true,
                        "errors": [
                            "read_error",
                            "some_other_error"
                        ],
                        "size": 4046848
                    },
                    {
                        "osd": 56,
                        "primary": false,
                        "errors": [],
                        "size": 4046848,
                        "omap_digest": "0xffffffff",
                        "data_digest": "0xb86056e7"
                    },
                    {
                        "osd": 128,
                        "primary": false,
                        "errors": [],
                        "size": 4046848,
                        "omap_digest": "0xffffffff",
                        "data_digest": "0xb86056e7"
                    }
                ]
            }
        ]
    }""")


def rados_inc_obj_output_multiple_read_errors():
    return json.loads("""{
        "epoch": 873,
        "inconsistents": [
            {
                "object": {
                    "data": "nothing to see here"
                },
                "errors": [],
                "union_shard_errors": [
                    "read_error"
                ],
                "selected_object_info": {
                    "data": "nothing to see here"
                },
                "shards": [
                    {
                        "osd": 53,
                        "primary": true,
                        "errors": [
                            "read_error"
                        ],
                        "size": 4046848
                    },
                    {
                        "osd": 56,
                        "primary": false,
                        "errors": [
                            "read_error"
                        ],
                        "size": 4046848,
                        "omap_digest": "0xffffffff",
                        "data_digest": "0xb86056e7"
                    },
                    {
                        "osd": 128,
                        "primary": false,
                        "errors": [],
                        "size": 4046848,
                        "omap_digest": "0xffffffff",
                        "data_digest": "0xb86056e7"
                    }
                ]
            }
        ]
    }""")
