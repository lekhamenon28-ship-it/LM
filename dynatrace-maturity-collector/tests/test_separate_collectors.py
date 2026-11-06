import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from dynatrace_client import ApiClient, PlatformClient
from dynatrace_maturity import Collector, tags

class SeparateCollectorTests(unittest.TestCase):
    def test_environment_pagination_only_sends_cursor(self):
        client=ApiClient('https://example.invalid','secret')
        with patch.object(client,'get',side_effect=[{'entities':[1],'nextPageKey':'next'},{'entities':[2]}]) as get:
            self.assertEqual(client.paged('/api/v2/entities',{'pageSize':500},'entities'),([1,2],2))
            self.assertEqual(get.call_args_list[1].args[1],{'nextPageKey':'next'})

    def test_platform_document_pagination(self):
        client=PlatformClient('https://example.invalid','secret')
        with patch.object(client,'get',side_effect=[{'documents':[1],'nextPageKey':'next'},{'documents':[2]}]) as get:
            self.assertEqual(list(client.documents()),[[1],[2]])
            self.assertEqual(get.call_args.args[1]['page-key'],'next')
            self.assertEqual(get.call_args.args[1]['filter'],"type='dashboard'")

    def test_api_family_guards(self):
        for client,path in [(ApiClient('https://invalid','x'),'/platform/document/v1/documents'),
                            (PlatformClient('https://invalid','x'),'/api/v2/entities')]:
            with self.assertRaises(ValueError):client.get(path)

    def test_immediate_grail_result_needs_no_token(self):
        client=PlatformClient('https://invalid','x')
        with patch.object(client,'request',return_value={'state':'SUCCEEDED','result':{'records':[{'count':3}]}}):
            now=datetime.now(timezone.utc)
            self.assertEqual(client.query('fetch spans',now,now),[{'count':3}])

    def test_platform_not_configured_is_not_empty(self):
        collector=Collector(None)
        self.assertEqual(collector.source('modern','/platform/test',lambda:[],True),[])
        self.assertEqual(collector.coverage[0]['status'],'not_configured')

    def test_multivalue_tags(self):
        self.assertEqual(tags([{'key':'env','value':'prod'},{'key':'env','value':'test'}]),{'env':['prod','test']})

    def test_source_retains_partial_rows(self):
        def rows():
            yield {'id':1}
            raise RuntimeError('failed page')
        collector=Collector(None)
        self.assertEqual(collector.source('test','/api/test',rows),[{'id':1}])
        self.assertEqual(collector.coverage[0]['status'],'partial')
