import asyncio
import json
import unittest
import httpx
import duel_async_gateway as edge

class AutoChessCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_capacity_fails_fast_without_consuming_control_or_calling_backend(self):
        async def forbidden(request):raise AssertionError('full capacity must not prepare')
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as backend:
            app=edge.create_app(http_client=backend,shared_secret='test-only')
            app.state.autochess_slots=asyncio.Semaphore(0)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                result=await client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'play','arguments':{'game':'autochess','action':'read','params':json.dumps({'roomId':'123456789abc','after':1,'wait_ms':25000})}}})
            self.assertTrue(result.json()['result']['isError'])
            self.assertEqual(app.state.control_slots._value,edge.MAX_INTERNAL_CONCURRENCY)
            self.assertEqual(app.state.autochess_slots._value,0)

    async def test_malformed_and_web_paths_do_not_enter_private_handshake(self):
        from autochess_adapter.async_edge import candidate
        for raw in (b'[]',b'null',b'{"params":null}',b'{"method":"tools/call","params":{"name":"play","arguments":[]}}'):
            self.assertIsNone(candidate(raw,'/mcp',edge))
        raw=json.dumps({'method':'tools/call','params':{'name':'play','arguments':{'game':'autochess'}}}).encode()
        self.assertIsNone(candidate(raw,'/autochess',edge))
