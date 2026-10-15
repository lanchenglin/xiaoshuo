"""Offline browser rendering + real FastAPI TestClient bridge; no browser networking."""
import sys,re,json,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from app.main import create_app
from playwright.sync_api import sync_playwright
root=ROOT; (root/'screenshots').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as data, TestClient(create_app(data,start_worker=True,password='test-ui-password-1234')) as client, sync_playwright() as pw:
 browser=pw.chromium.launch(executable_path='/usr/bin/chromium' if Path('/usr/bin/chromium').is_file() else None,headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
 errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 def bridge(source, payload):
  url=payload['url'];options=payload.get('options',{})
  assert url.startswith('/api/')
  response=client.request(options.get('method','GET'),url,headers=options.get('headers',{}),content=options.get('body'))
  try:body=response.json()
  except Exception:body={'detail':'non-json response'}
  return {'status':response.status_code,'body':body}
 page.expose_binding('__test_api',bridge)
 html=(root/'app/static/index.html').read_text()
 html=re.sub(r'<link[^>]+>|<script[^>]+></script>','',html)
 page.set_content(html)
 page.add_style_tag(content=(root/'app/static/style.css').read_text())
 page.add_script_tag(content="window.fetch=async(url,options={})=>{const r=await window.__test_api({url,options});return {ok:r.status>=200&&r.status<300,status:r.status,json:async()=>r.body};};")
 page.add_script_tag(content=(root/'app/static/app.js').read_text())
 page.add_script_tag(content=(root/'app/static/acquisition.js').read_text())
 page.locator('#login-password').fill('test-ui-password-1234')
 page.locator('#login-form button[type=submit]').click()
 page.locator('#workspace').wait_for(state='visible')
 page.locator('#demo-create').click()
 page.locator('#create-form [name=humanizer]').uncheck()  # Explicit native-rule fixture
 page.locator('#create-form button[type=submit]').click()
 page.wait_for_function("document.getElementById('metric-status').textContent.startsWith('已完成')",timeout=20000)
 assert page.locator('#metric-progress').inner_text()=='2 / 2'
 page.screenshot(path=str(root/'screenshots/dashboard.png'),full_page=True)
 page.locator('[data-tab=chapters]').click()
 page.locator('#chapters button').first.click()
 page.wait_for_function("document.getElementById('reader-text').textContent.includes('演示数据')")
 page.screenshot(path=str(root/'screenshots/reader.png'),full_page=True)
 page.locator('#open-settings').click()
 page.locator('#providers [data-key=model]').fill('mock-model')
 page.locator('#providers [data-key=base_url]').fill('https://model.example/v1')
 page.locator('#providers [data-key=api_key]').fill('ui-fixture-not-real-secret')
 page.locator('#settings-form > button[type=submit]').click()
 page.wait_for_function("document.getElementById('settings-result').textContent.includes('已加密保存')")
 page.locator('[data-close=settings-dialog]').click()
 page.locator('#open-settings').click()
 assert page.locator('#providers [data-key=api_key]').input_value()==''
 assert '已保存' in page.locator('#providers [data-key=api_key]').get_attribute('placeholder')
 page.locator('[data-close=settings-dialog]').click()
 page.evaluate("document.getElementById('toast').hidden=true")
 page.set_viewport_size({'width':390,'height':844})
 page.locator('[data-tab=overview]').click()
 page.screenshot(path=str(root/'screenshots/mobile.png'),full_page=True)
 widths=page.evaluate('({scroll:document.documentElement.scrollWidth,viewport:window.innerWidth})')
 assert widths['scroll']<=widths['viewport'],widths
 assert not errors,errors
 print(json.dumps({'method':'offline Chromium + FastAPI TestClient bridge (not direct HTTP browser navigation)','js_errors':errors,'chapters':2,'key_remasked':True,'mobile_widths':widths},ensure_ascii=False,indent=2))
 browser.close()
