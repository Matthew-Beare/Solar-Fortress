// Execute the actual shipped LED and mode functions, without Victron access.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'..','Solar_Fortress_Mission_Control_Live.html'),'utf8');
const keys=['Mains','Bulk','Absorption','Float','Inverter','Overload','LowBattery','Temperature'];
const elements=keys.map(key=>({dataset:{ledKey:key},style:{setProperty(k,v){this[k]=v;}},setAttribute(k,v){this[k]=v;}}));
const source=html.slice(html.indexOf('function ledValue('),html.indexOf('function layoutEnergyConnections('))+
html.slice(html.indexOf('function observedInverterMode('),html.indexOf('function renderPower('));
const context={num:x=>x===null||x===undefined||x===''||typeof x==='boolean'||!Number.isFinite(Number(x))?null:Number(x),document:{querySelectorAll:()=>elements},txt:()=>{}};
vm.createContext(context);vm.runInContext(source,context);
let checks=0;
for(const key of keys)for(const value of [0,1,2,3]){
 context.renderPhysicalLeds({vebus_leds:Object.fromEntries(keys.map(k=>[k,k===key?value:0]))},{live:true});
 assert.equal(elements.find(e=>e.dataset.ledKey===key).dataset.ledState,String(value));checks++;
 for(const e of elements)if(e.dataset.ledKey!==key){assert.equal(e.dataset.ledState,'0');checks++;}
}
for(const invalid of [undefined,null,true,false,'junk',-1,4,Infinity]){
 context.renderPhysicalLeds({vebus_leds:Object.fromEntries(keys.map(k=>[k,invalid]))},{live:true});
 for(const e of elements){assert.equal(e.dataset.ledState,'unknown');checks++;}
}
context.renderPhysicalLeds({vebus_leds:Object.fromEntries(keys.map(k=>[k,1]))},{live:false});
for(const e of elements){assert.equal(e.dataset.ledState,'unknown');checks++;}
for(const [leds,connected,want] of [
 [{Mains:0,Inverter:1},false,'INVERTING'],[{Mains:1,Inverter:0},true,'PASS THROUGH'],
 [{Mains:1,Inverter:2},true,'POWER ASSIST'],[{Mains:1,Inverter:0},false,'AC NOT ACCEPTED'],
 [{Mains:null,Inverter:1},false,'UNKNOWN']]){
 assert.equal(context.observedInverterMode({vebus_leds:leds,ac_connected:connected},{live:true}),want);checks++;
}
console.log('LED logic assertions passed: '+checks);

require('./test_display_logic.cjs');
