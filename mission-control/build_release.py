#!/usr/bin/env python3
"""Rebuild the offline browser verifier and manifest from the deployable HTML."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTML = 'Solar_Fortress_Mission_Control_Live.html'
KEYS = ('Mains', 'Bulk', 'Absorption', 'Float', 'Inverter', 'Overload', 'LowBattery', 'Temperature')


def fixtures():
    base = dict(controller_running=True, victron_connected=True, error='',
                soc=98.3, battery_power=-223, battery_voltage=13.25, battery_current=-16.8,
                battery_state='DISCHARGING', solar_power=0, solar_today_yield=1.33,
                solar_seven_day_yield=7.4, solar_chargers=[], ac_input_power=0,
                ac_output_power=198, ac_connected=False, ac_present=True,
                mode='BATTERY FIRST', grid_charging=False, ac_policy='battery_first',
                last_full_charge_seconds=5300, maintenance={'phase': 'idle', 'available': True, 'last_full_event_at': 0},
                runtime='4d 3h', daylight='10h 26m', peak_sun_hours=2.7,
                telemetry_history_error=None)
    result = {}
    for name in ('inverting', 'float', 'blink', 'partial', 'all_on'):
        d = dict(base)
        leds = dict.fromkeys(KEYS, 0)
        if name == 'float':
            leds.update(Mains=1, Float=1)
            d.update(battery_power=20, battery_current=1.5, battery_state='CHARGING',
                     ac_connected=True, ac_input_power=225)
        elif name == 'blink':
            leds.update(Inverter=1, Overload=2, LowBattery=3)
        elif name == 'partial':
            leds = {'Mains': 0, 'Inverter': 1}
        elif name == 'all_on':
            leds = dict.fromkeys(KEYS, 1)
        else:
            leds['Inverter'] = 1
        d['vebus_leds'] = leds
        result[name] = d
    return result


def script_json(value):
    return json.dumps(value, ensure_ascii=True).replace('</', '<\\/')


def generate():
    source = (HERE/HTML).read_text()
    source_hash = hashlib.sha256(source.encode()).hexdigest()
    checks = (HERE/'tests'/'browser_checks.js').read_text()
    require_marker = '})();\n\n</script>'
    if require_marker not in source:
        raise RuntimeError('Expected application scope marker missing')
    instrumented = source.replace(require_marker, checks+'\n})();\n\n</script>')
    data = script_json(fixtures())
    common = """
function fixturePage(source,d){
 const now=Date.now(),samples=[];
 const offsetName=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',timeZoneName:'shortOffset'}).formatToParts(new Date(now)).find(p=>p.type==='timeZoneName')?.value||'GMT';
 const offsetMatch=offsetName.match(/GMT([+-])([0-9]{1,2})(?::([0-9]{2}))?/);
 const offset=offsetMatch?(offsetMatch[1]==='-'?-1:1)*(Number(offsetMatch[2])*60+Number(offsetMatch[3]||0))*60000:0;
 for(let i=2880;i>=0;i--){const t=now-i*30000,h=((t+offset)/3600000%24+24)%24;
  samples.push({t,solar:Math.round(Math.max(0,Math.sin((h-6)/12*Math.PI))*640),soc:78+12*Math.sin((h-10)/24*Math.PI*2),load:Math.floor(h)%3===0?210:55,grid:0,battery:-225});}
 const prefix='<script>window.fetch=async function(url){return {ok:true,json:async()=>String(url).includes("/api/status")?({...'+JSON.stringify(d)+',timestamp:new Date().toISOString()}):({source:"pi-sqlite",server_time_ms:'+now+',samples:'+JSON.stringify(samples)+'})};};<\\/script>';
 return source.replace('<script>',prefix+'<script>');
}
"""
    verifier = """<!doctype html><html><head><meta charset="utf-8"><title>Solar Fortress 0.2.8 rendering verification</title>
<style>body{margin:0;background:#071421;color:#e8f5ff;font:14px system-ui}header{padding:20px;background:#12344c}h1{margin:0 0 8px;font-size:22px}#report{padding:16px;white-space:pre-wrap}iframe{display:block;border:0;margin:0;max-width:none}</style></head>
<body><header><h1>OFFLINE RENDER VERIFICATION</h1><div>Simulated readings only. This page never connects to the Pi.</div></header><pre id="report">Running browser checks...</pre><div id="frameHost"></div>
<script>
const source=SOURCE,fixtures=FIXTURES;
COMMON
const queue=[];for(const width of [320,390,820,1280,1920])for(const name of Object.keys(fixtures))queue.push({width,name});
const results=[];let active=null,total=0;
function next(){
 if(!queue.length){
  const failures=results.flatMap(r=>r.failures.map(f=>({width:r.width,fixture:r.fixture,...f})));
  document.documentElement.setAttribute('data-sf-render-verification',failures.length?'fail':'pass');
  document.documentElement.setAttribute('data-sf-source-sha256','HASH');
  document.getElementById('frameHost').replaceChildren();
  document.getElementById('report').textContent=JSON.stringify({passed:failures.length===0,source_sha256:'HASH',fixtures:results.length,checks:total,motionPreferences:[...new Set(results.map(r=>r.reducedMotion))],failures},null,2);
  return;
 }
 active=queue.shift();const frame=document.createElement('iframe');frame.title='Rendering '+active.name+' at '+active.width+' pixels';frame.width=active.width;frame.height=3600;
 frame.srcdoc=fixturePage(source,fixtures[active.name]);document.getElementById('frameHost').replaceChildren(frame);
}
window.addEventListener('message',event=>{
 const frame=document.querySelector('iframe');
 if(!active||event.source!==frame?.contentWindow||!event.data?.solarFortressRenderProof)return;
 const proof=event.data.solarFortressRenderProof;
 results.push({width:active.width,fixture:active.name,reducedMotion:proof.reducedMotion,failures:proof.failures});
 total+=proof.checks.length;document.getElementById('report').textContent=JSON.stringify({completed:results.length,checks:total,results},null,2);
 active=null;setTimeout(next,0);
});
next();
</script></body></html>
"""
    verifier = (verifier.replace('SOURCE', script_json(instrumented))
                .replace('FIXTURES', data).replace('COMMON', common).replace('HASH', source_hash))
    preview = """<!doctype html><html><head><meta charset="utf-8"><title>Solar Fortress 0.2.8 offline preview</title><style>body{margin:0;background:#071421;color:#fff;font:12px system-ui}header{padding:9px;background:#70501e}iframe{display:block;border:0;margin:auto;max-width:none}</style></head><body><header>OFFLINE PREVIEW — SIMULATED READINGS. Not the live Pi.</header><div id="host"></div><script>
const source=SOURCE,fixtures=FIXTURES;
COMMON
const q=new URLSearchParams(location.search),name=q.get('case')||'inverting',width=Math.max(320,Number(q.get('width')||1280));
const f=document.createElement('iframe');f.title='Solar Fortress offline preview';f.width=width;f.height=3000;f.srcdoc=fixturePage(source,fixtures[name]||fixtures.inverting);document.getElementById('host').append(f);
</script></body></html>"""
    preview = preview.replace('SOURCE', script_json(source)).replace('FIXTURES', data).replace('COMMON', common)
    (HERE/'verify_render.html').write_text(verifier)
    (HERE/'preview.html').write_text(preview)
    constraints = json.loads((HERE/'release_constraints.json').read_text())
    manifest = dict(version='0.2.8', html_sha256=source_hash,
                    verifier_sha256=hashlib.sha256(verifier.encode()).hexdigest(), **constraints)
    (HERE/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    print(json.dumps(generate(), indent=2))
