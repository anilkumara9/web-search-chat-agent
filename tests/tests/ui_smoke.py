"""Optional browser smoke test. Requires Playwright + Chromium for development only.
Uses a temporary local DB with clearly synthetic test fixtures, never a live model.
Run from the repository root: python tests/ui_smoke.py
"""
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.memory import Memory


def run():
    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env.update(AGENT_DB=td+'/ui.db', AGENT_USER='ui-test', LLM_API_KEY='')
        m=Memory(env['AGENT_DB'])
        m.add('ui-test','fixture','user','Controlled UI fixture question')
        m.add('ui-test','fixture','assistant','**Controlled response** <img src=x onerror=alert(1)> https://example.com/test')
        m.add('other','hidden','user','other namespace fixture')
        m.db.close()
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        base=f'http://127.0.0.1:{port}'
        proc=subprocess.Popen([sys.executable,'server.py','--port',str(port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(base,timeout=.2).close();break
                except OSError:time.sleep(.1)
            with sync_playwright() as p:
                b=p.chromium.launch(args=['--no-sandbox'])
                page=b.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base);page.wait_for_selector('#status:text("Setup required")')
                assert page.locator('#notice').is_visible()
                page.locator('[data-view=history]').click();page.wait_for_selector('.history-item')
                assert page.locator('.history-item').count()==1
                page.locator('#history-filter').fill('absent');assert page.locator('.history-empty').is_visible()
                page.locator('#history-filter').fill('fixture');page.locator('.history-item').click()
                assert page.locator('#messages img').count()==0
                assert page.locator('#messages strong').inner_text()=='Controlled response'
                assert page.locator('.sources a').get_attribute('href')=='https://example.com/test'
                page.reload();page.wait_for_selector('#status:text("Setup required")')
                page.locator('[data-view=history]').click();assert page.locator('.history-item').count()==1
                page.locator('#new').click();page.locator('#question').fill('must not contact provider');page.locator('#question').press('Enter')
                assert 'credential missing' in page.locator('#notice').inner_text()
                for width in (320,390,768,1440):
                    page.set_viewport_size({'width':width,'height':900})
                    for view in ('chat','history','about'):
                        page.locator('[data-view='+view+']').click()
                        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),(width,view)
                assert not errors,errors
                b.close()
                print('UI smoke passed: missing-key state, scoped history/filter/reload, safe text+links, keyboard validation, 320/390/768/1440 widths across all views. Controlled fixtures only; no live model.')
        finally:
            proc.terminate();proc.wait(timeout=5)


if __name__=='__main__':
    run()
