"""AutoChess edge extension; separate connection and wait budgets from Duel."""
import asyncio
import json
from urllib.parse import urlsplit
import anyio
import httpx


def candidate(raw, path, edge):
    if not edge._is_root_mcp_path(path):
        return None
    try:
        p = json.loads(raw)
        if p.get('method') == 'tools/call' and p.get('params', {}).get('name') == 'play' and p['params'].get('arguments', {}).get('game') == 'autochess':
            return p
    except (ValueError, AttributeError):
        pass
    return None


async def handle(request, payload, edge):
    app = request.app
    rid = payload.get('id')
    if 'id' not in payload:
        return edge._json_response(None, 202)
    secret = app.state.shared_secret
    if not secret:
        return edge._json_response(edge._gateway_tool_error(rid, '异步网关未配置'))
    args = payload['params']['arguments']
    params = args.get('params', {})
    for _ in range(3):
        if not isinstance(params, str): break
        try: params = json.loads(params)
        except ValueError: break
    waiting = args.get('action') == 'read' and bool((params if isinstance(params, dict) else args).get('waitMs', (params if isinstance(params, dict) else args).get('wait_ms', args.get('waitMs', 0))))
    sem = app.state.autochess_slots if waiting else app.state.control_slots
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0.2)
    except asyncio.TimeoutError:
        return edge._json_response(edge._gateway_tool_error(rid, 'AutoChess 等待容量已满，请稍后重试'))
    ticket = None
    transferred = False
    async def internal(op, body, headers=None):
        async with app.state.internal_slots:
            return await edge._internal_json_call(app.state.cedartoy_client, edge.CEDARTOY_ORIGIN+'/_internal/autochess-gateway/'+op,
                body, headers or {'Content-Type':'application/json','X-CedarToy-Gateway-Secret':secret})
    class Lease:
        released=False
        closed=False
        async def release(self):
            if self.released: return
            self.released=True
            try:
                with anyio.move_on_after(6, shield=True):
                    if not self.closed and ticket:
                        try: await internal('abandon', {'ticket':ticket})
                        except (httpx.HTTPError, RuntimeError): pass
            finally: sem.release()
    lease=Lease()
    try:
        prepared = await internal('prepare', payload, edge._internal_headers(request,secret))
        if prepared.get('kind') == 'response':
            return edge._json_response(prepared.get('body'), prepared.get('status_code',200))
        ticket=prepared.get('ticket')
        p=prepared['backend_payload']
        origin=urlsplit(p['origin'])
        if origin.scheme!='http' or origin.hostname!='127.0.0.1' or not origin.port or origin.username or origin.password or origin.path or origin.query or origin.fragment:
            raise RuntimeError('Invalid satellite origin')
        async def backend():
            try:
                res=await app.state.autochess_client.post(p['origin']+'/platform/autochess',json=p['body'],headers=p['headers'],timeout=30,follow_redirects=False)
                return {'status':res.status_code,'body':res.json()}
            except (httpx.HTTPError, ValueError):
                return {'error':True}
        async def stream():
            task=asyncio.create_task(backend())
            try:
                while not task.done():
                    done,_=await asyncio.wait({task},timeout=app.state.keepalive_seconds)
                    if not done: yield b' \n'
                completed=await internal('finalize',{'ticket':ticket,'completion':await task})
                lease.closed=True
                yield json.dumps(completed['body'],ensure_ascii=False).encode()
            except (httpx.HTTPError, RuntimeError, KeyError):
                yield json.dumps(edge._gateway_tool_error(rid,'AutoChess 网关暂不可用；重试保持相同 requestId'),ensure_ascii=False).encode()
            finally:
                with anyio.CancelScope(shield=True):
                    await edge._cancel_backend_task(task)
                await lease.release()
        result=edge._DuelStreamingResponse(stream(),lease=lease,headers={'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store, no-transform','X-Accel-Buffering':'no','Access-Control-Allow-Origin':'*'})
        transferred=True
        return result
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError):
        return edge._json_response(edge._gateway_tool_error(rid,'AutoChess 网关暂不可用'))
    finally:
        if not transferred: await lease.release()
