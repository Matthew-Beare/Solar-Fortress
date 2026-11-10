const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','Solar_Fortress_Mission_Control_Live.html'),'utf8');
const code=html.slice(html.indexOf('const MAINTENANCE_REQUEST_SECONDS='),html.indexOf('function showMaintenance('));
const ctx={num:x=>x===null||x===undefined||x===''||typeof x==='boolean'||!Number.isFinite(Number(x))?null:Number(x),
 asClock:n=>{n=Math.max(0,Math.ceil(n));return [Math.floor(n/3600),Math.floor(n%3600/60),n%60].map(v=>String(v).padStart(2,'0')).join(':');}};
vm.createContext(ctx);vm.runInContext(code,ctx);
let checks=0;
function eq(actual,want){assert.equal(actual,want);checks++;}
eq(ctx.telemetryEpochMs('2026-11-09 06:49:00 PM EST'),Date.UTC(2026,10,9,23,49));
eq(ctx.telemetryEpochMs('2026-07-01 01:30:00 PM EDT'),Date.UTC(2026,6,1,17,30));
eq(ctx.telemetryEpochMs('2026-11-09 12:00:00 AM EST'),Date.UTC(2026,10,9,5));
eq(ctx.telemetryEpochMs('2026-11-09 12:00:00 PM EST'),Date.UTC(2026,10,9,17));
eq(ctx.telemetryEpochMs('2026-11-01 01:30:00 AM EST')-ctx.telemetryEpochMs('2026-11-01 01:30:00 AM EDT'),3600000);
eq(ctx.telemetryEpochMs('unverified date'),null);
const now=Date.UTC(2026,10,9,23,49),data={timestamp:'2026-11-09T23:49:00Z',last_full_charge_seconds:7*86400,
 maintenance:{phase:'idle',available:true,last_full_event_at:now/1000-300}};
let t=ctx.fullChargeTiming(data,now,now);
eq(t.requestRemaining,3*86400);
eq(t.lastFullMs,Date.UTC(2026,10,2,23,49));
eq(ctx.maintenanceRemaining(t.requestRemaining),'3d 0h 0m');
// Every new full event restarts the rolling ten elapsed days; the maintenance
// checkpoint's older full event must not determine this countdown.
t=ctx.fullChargeTiming({...data,last_full_charge_seconds:0},now,now);
eq(t.requestRemaining,10*86400);eq(t.lastFullMs,now);
t=ctx.fullChargeTiming({...data,last_full_charge_seconds:10*86400},now,now);
eq(t.requestRemaining,0);
// Browser/Pi wall clocks may differ; only elapsed time since reception advances age.
t=ctx.fullChargeTiming({...data,last_full_charge_seconds:60},Date.UTC(2040,0,1)+5000,Date.UTC(2040,0,1));
eq(t.ageSeconds,65);eq(t.requestRemaining,10*86400-65);
for(const bad of [null,undefined,true,-1,'invalid']){
 t=ctx.fullChargeTiming({...data,last_full_charge_seconds:bad},now,now);
 eq(t.requestRemaining,null);eq(t.lastFullMs,null);
}
eq(ctx.maintenanceRemaining(61),'00:01:01');
console.log('Rolling full-charge display assertions passed: '+checks);
