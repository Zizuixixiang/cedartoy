#!/usr/bin/env python3
"""Fail-closed host acceptance; real runtime, isolated stores, live original UI.

Run with the existing platform Python, NOT the new worker venv. Does not install
anything. Requires Node + Playwright + installed Chromium supplied by the host.
No production configuration is loaded or modified; no production service starts.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def isolated_env(root):
    # These overrides are set BEFORE importing server (tarot store opens at import).
    env = {k: v for k, v in os.environ.items() if not k.startswith(('NOWHERE_', 'PYTHON', 'TOY_', 'TURTLE_', 'TAROT_'))}
    env.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(Path(os.environ.get('XDG_CACHE_HOME', str(Path.home()/'.cache')))/'ms-playwright'))
    env.update(TURTLE_SOUP_DB=str(root/'accounts.db'), SESSIONS_DB=str(root/'sessions.db'),
               TAROT_DB_PATH=str(root/'tarot.db'), CAMPING_PLAZA_DB_PATH=str(root/'camping.db'),
               NOWHERE_SAVE_ROOT=str(root/'vendor_saves/nowhere'), NOWHERE_SHARED_DB=str(root/'shared.db'),
               NOWHERE_HOME=str(root/'home'), NOWHERE_PYTHON=str(ROOT/'.venv-nowhere/bin/python'),
               NOWHERE_REAL_TEST='1', TOY_SECRET=secrets.token_hex(32),
               PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT),
               TMPDIR=str(root), XDG_CACHE_HOME=str(root/'cache'))
    # Don't pass arbitrary external service credentials/config to game processes.
    for key in list(env):
        if key.endswith(('_TOKEN','_SECRET','_API_KEY')) and key != 'TOY_SECRET': env.pop(key)
    return env


def preflight(env):
    manifest=ROOT/'.nowhere-runtime/manifest.json'
    if not manifest.is_file():
        raise RuntimeError('独立环境尚未完成；先运行 python3 scripts/prepare_nowhere_env.py --prepare')
    metadata=json.loads(manifest.read_text())
    from scripts.prepare_nowhere_env import metadata as expected_metadata
    if any(metadata.get(k)!=v for k,v in expected_metadata().items()):
        raise RuntimeError('运行环境 manifest 与当前固定版本不符')
    python=ROOT/'.venv-nowhere/bin/python'
    result=subprocess.run([str(python),'-I','-c',
        'import sys,fastmcp,httpx,numpy,scipy,skyfield,timezonefinder,uvicorn,opencc,ephem,jsonschema;assert sys.version_info[:3]==(3,11,13)'],
        env=env,cwd=ROOT,capture_output=True,timeout=60)
    if result.returncode: raise RuntimeError('独立环境版本或必需依赖检查失败；未启动验收')
    freeze=subprocess.check_output([str(python),'-I','-m','pip','freeze','--all'],env=env,cwd=ROOT,text=True,timeout=30)
    if freeze!=metadata['freeze']: raise RuntimeError('依赖版本与安装记录发生漂移')
    if not shutil.which('node'): raise RuntimeError('宿主需要 Node；验收不会自动安装')
    check="const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');require('fs').accessSync(chromium.executablePath());"
    result=subprocess.run(['node','-e',check],cwd=ROOT,env=env,capture_output=True,timeout=20)
    if result.returncode: raise RuntimeError('宿主需要可用 Playwright 与已安装 Chromium；通过 PLAYWRIGHT_MODULE 指定已有模块，不自动下载浏览器')
    return {k:metadata[k] for k in ('python','release','sha256','upstream','requirements_sha256')}


def child_suite():
    # The runner's child always inherits temporary paths prepared by its parent.
    root=Path(os.environ['TMPDIR']).resolve()
    for key in ('TURTLE_SOUP_DB','SESSIONS_DB','TAROT_DB_PATH','NOWHERE_SAVE_ROOT','NOWHERE_SHARED_DB'):
        if not Path(os.environ[key]).resolve().is_relative_to(root):
            raise RuntimeError('隔离路径校验失败')
    suites=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in (
        'tests_toy.test_nowhere', 'tests_toy.test_nowhere_handoff',
        'tests_toy.test_root_mcp_protocol', 'tests_toy.test_reset_guest_claim_code',
        'tests_toy.nowhere_live_acceptance'))
    def interrupted(signum, frame):
        raise KeyboardInterrupt('acceptance interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        result=unittest.TextTestRunner(verbosity=2).run(suites)
    finally:
        from nowhere_adapter.handler import POOL
        POOL.close()
    summary={'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
             'skips':len(result.skipped),'expected_failures':len(result.expectedFailures)}
    print('RESULT '+json.dumps(summary),flush=True)
    if result.skipped or result.expectedFailures or not result.wasSuccessful(): return 1
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,help='new evidence directory (must not exist); default /tmp/nowhere-acceptance-…')
    parser.add_argument('--preflight',action='store_true',help='verify prerequisites only; never counts as acceptance')
    args=parser.parse_args()
    output=(args.output or Path('/tmp')/('nowhere-acceptance-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3))).resolve()
    if output.exists() or output.is_relative_to(ROOT/'data') or output.is_relative_to(ROOT/'vendor'):
        print('STOP: evidence directory must be new and outside production data/vendor',file=sys.stderr);return 1
    output.mkdir(parents=True)
    report={'status':'NOT_RUN','started':datetime.now(timezone.utc).isoformat(),'stages':[],
            'visual_review':'PENDING: generated screenshots still require human visual review'}
    try:
        with tempfile.TemporaryDirectory(prefix='nowhere-acceptance-') as temp:
            env=isolated_env(Path(temp));env['NOWHERE_SCREENSHOTS']=str(output/'screenshots')
            report['runtime']=preflight(env)
            if args.preflight:
                report['status']='PREFLIGHT_ONLY';print('Preflight OK; acceptance NOT RUN');return 0
            for label,command,timeout in (
                ('real-tests-and-live-browser',[sys.executable,str(Path(__file__).resolve()),'--internal-suite'],1800),
                ('cross-process-persistence',[sys.executable,str(ROOT/'scripts/persistence_check.py'),'--nowhere'],600),
            ):
                print('RUN '+label,flush=True);start=time.monotonic()
                with (output/(label+'.log')).open('w') as log:
                    proc=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                    try: code=proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        # Internal suite handles SIGTERM and closes its worker pool.
                        proc.terminate()
                        try: proc.wait(timeout=15)
                        except subprocess.TimeoutExpired: proc.kill();proc.wait()
                        raise RuntimeError(label+' exceeded total timeout; FAIL')
                report['stages'].append({'name':label,'exit_code':code,'seconds':round(time.monotonic()-start,2)})
                if code: raise RuntimeError(label+' failed; inspect evidence log')
            browser=json.loads((output/'screenshots/report.json').read_text())
            if {x['width'] for x in browser}!={360,428,1280} or any(x['mode']!='live-real-engine' for x in browser):
                raise RuntimeError('缺少真实页面三尺寸浏览器记录')
            for width in (360,428,1280):
                for state in ('map','postcard','trail'):
                    if not (output/f'screenshots/{width}-{state}.png').is_file(): raise RuntimeError('缺少截图')
            report['status']='AUTOMATED_PASS_VISUAL_REVIEW_PENDING'
            print('PASS automated acceptance; inspect 9 screenshots before visual sign-off')
    except Exception as exc:
        report['status']='FAIL'
        report['error']=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
        print('STOP: '+report['error'],file=sys.stderr)
        return 1
    finally:
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print('Evidence: '+str(output))
    return 0


if __name__=='__main__':
    if sys.argv[1:]==['--internal-suite']:
        sys.exit(child_suite())
    sys.exit(main())
