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
import unittest

from mock import (
    call,
    patch,
)

from ceph import broker


class TestCephOps(unittest.TestCase):

    @patch.object(broker, 'create_erasure_profile')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_create_erasure_profile(self, mock_create_erasure):
        req = json.dumps({'api-version': 1,
                          'ops': [{
                              'op': 'create-erasure-profile',
                              'name': 'foo',
                              'erasure-type': 'jerasure',
                              'failure-domain': 'rack',
                              'k': 3,
                              'm': 2,
                          }]})
        rc = broker.process_requests(req)
        mock_create_erasure.assert_called_with(service='admin',
                                               profile_name='foo',
                                               coding_chunks=2,
                                               data_chunks=3,
                                               locality=None,
                                               failure_domain='rack',
                                               erasure_plugin_name='jerasure')
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'pool_exists')
    @patch.object(broker, 'ReplicatedPool')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_process_requests_create_replicated_pool(self,
                                                     mock_replicated_pool,
                                                     mock_pool_exists):
        mock_pool_exists.return_value = False
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'create-pool',
                               'pool-type': 'replicated',
                               'name': 'foo',
                               'replicas': 3
                           }]})
        rc = broker.process_requests(reqs)
        mock_pool_exists.assert_called_with(service='admin', name='foo')
        calls = [call(name=u'foo', service='admin', replicas=3)]
        mock_replicated_pool.assert_has_calls(calls)
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'pool_exists')
    @patch.object(broker, 'ReplicatedPool')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_process_requests_replicated_pool_weight(self,
                                                     mock_replicated_pool,
                                                     mock_pool_exists):
        mock_pool_exists.return_value = False
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'create-pool',
                               'pool-type': 'replicated',
                               'name': 'foo',
                               'weight': 40.0,
                               'replicas': 3
                           }]})
        rc = broker.process_requests(reqs)
        mock_pool_exists.assert_called_with(service='admin', name='foo')
        calls = [call(name=u'foo', service='admin', replicas=3,
                      percent_data=40.0)]
        mock_replicated_pool.assert_has_calls(calls)
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'delete_pool')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_process_requests_delete_pool(self,
                                          mock_delete_pool):
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'delete-pool',
                               'name': 'foo',
                           }]})
        mock_delete_pool.return_value = {'exit-code': 0}
        rc = broker.process_requests(reqs)
        mock_delete_pool.assert_called_with(service='admin', name='foo')
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'pool_exists')
    @patch.object(broker.ErasurePool, 'create')
    @patch.object(broker, 'erasure_profile_exists')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_process_requests_create_erasure_pool(self, mock_profile_exists,
                                                  mock_erasure_pool,
                                                  mock_pool_exists):
        mock_pool_exists.return_value = False
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'create-pool',
                               'pool-type': 'erasure',
                               'name': 'foo',
                               'erasure-profile': 'default'
                           }]})
        rc = broker.process_requests(reqs)
        mock_profile_exists.assert_called_with(service='admin', name='default')
        mock_pool_exists.assert_called_with(service='admin', name='foo')
        mock_erasure_pool.assert_called_with()
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'pool_exists')
    @patch.object(broker.Pool, 'add_cache_tier')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_process_requests_create_cache_tier(self, mock_pool,
                                                mock_pool_exists):
        mock_pool_exists.return_value = True
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'create-cache-tier',
                               'cold-pool': 'foo',
                               'hot-pool': 'foo-ssd',
                               'mode': 'writeback',
                               'erasure-profile': 'default'
                           }]})
        rc = broker.process_requests(reqs)
        mock_pool_exists.assert_any_call(service='admin', name='foo')
        mock_pool_exists.assert_any_call(service='admin', name='foo-ssd')

        mock_pool.assert_called_with(cache_pool='foo-ssd', mode='writeback')
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'pool_exists')
    @patch.object(broker.Pool, 'remove_cache_tier')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_process_requests_remove_cache_tier(self, mock_pool,
                                                mock_pool_exists):
        mock_pool_exists.return_value = True
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'remove-cache-tier',
                               'hot-pool': 'foo-ssd',
                           }]})
        rc = broker.process_requests(reqs)
        mock_pool_exists.assert_any_call(service='admin', name='foo-ssd')

        mock_pool.assert_called_with(cache_pool='foo-ssd')
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'snapshot_pool')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_snapshot_pool(self, mock_snapshot_pool):
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'snapshot-pool',
                               'name': 'foo',
                               'snapshot-name': 'foo-snap1',
                           }]})
        mock_snapshot_pool.return_value = {'exit-code': 0}
        rc = broker.process_requests(reqs)
        mock_snapshot_pool.assert_called_with(service='admin',
                                              pool_name='foo',
                                              snapshot_name='foo-snap1')
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'rename_pool')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_rename_pool(self, mock_rename_pool):
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'rename-pool',
                               'name': 'foo',
                               'new-name': 'foo2',
                           }]})
        mock_rename_pool.return_value = {'exit-code': 0}
        rc = broker.process_requests(reqs)
        mock_rename_pool.assert_called_with(service='admin',
                                            old_name='foo',
                                            new_name='foo2')
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'remove_pool_snapshot')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_remove_pool_snapshot(self, mock_snapshot_pool):
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'remove-pool-snapshot',
                               'name': 'foo',
                               'snapshot-name': 'foo-snap1',
                           }]})
        mock_snapshot_pool.return_value = {'exit-code': 0}
        rc = broker.process_requests(reqs)
        mock_snapshot_pool.assert_called_with(service='admin',
                                              pool_name='foo',
                                              snapshot_name='foo-snap1')
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'pool_set')
    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_set_pool_value(self, mock_set_pool):
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'set-pool-value',
                               'name': 'foo',
                               'key': 'size',
                               'value': 3,
                           }]})
        mock_set_pool.return_value = {'exit-code': 0}
        rc = broker.process_requests(reqs)
        mock_set_pool.assert_called_with(service='admin',
                                         pool_name='foo',
                                         key='size',
                                         value=3)
        self.assertEqual(json.loads(rc), {'exit-code': 0})

    @patch.object(broker, 'log', lambda *args, **kwargs: None)
    def test_set_invalid_pool_value(self):
        reqs = json.dumps({'api-version': 1,
                           'ops': [{
                               'op': 'set-pool-value',
                               'name': 'foo',
                               'key': 'size',
                               'value': 'abc',
                           }]})
        rc = broker.process_requests(reqs)
        self.assertEqual(json.loads(rc)['exit-code'], 1)
