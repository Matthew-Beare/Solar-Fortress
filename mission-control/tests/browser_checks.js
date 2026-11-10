
// TEST FIXTURE ONLY: this block is never packaged or deployed.
setTimeout(async()=>{
 const reducedMotion=window.matchMedia('(prefers-reduced-motion:reduce)').matches;
 const proof={checks:[],failures:[],fixture:true,reducedMotion};
 const check=(name,pass,actual)=>{proof.checks.push({name,pass,actual});if(!pass)proof.failures.push({name,actual});};
 const base=sample||{};
 const keys=['Mains','Bulk','Absorption','Float','Inverter','Overload','LowBattery','Temperature'];
 const colors={Mains:'rgb(116, 255, 174)',Inverter:'rgb(116, 255, 174)',Bulk:'rgb(255, 211, 101)',Absorption:'rgb(255, 211, 101)',Float:'rgb(255, 211, 101)',Overload:'rgb(255, 95, 109)',LowBattery:'rgb(255, 95, 109)',Temperature:'rgb(255, 95, 109)'};
 for(const key of keys){
  for(const value of [0,1,2,3]){
   const d={...base,vebus_leds:Object.fromEntries(keys.map(k=>[k,k===key?value:0]))};
   renderPhysicalLeds(d,{live:true});
   const e=document.getElementById('led'+key+'Mobile'),c=getComputedStyle(e);
   check(key+' code '+value+' computed colour',c.backgroundColor===(value===0?'rgb(24, 35, 45)':colors[key]),c.backgroundColor);
   const steady=value<2||reducedMotion;
   check(key+' code '+value+' animation',steady===(c.animationName==='none')&&
    (!reducedMotion||value<2||c.outlineStyle==='dashed'),{animation:c.animationName,outline:c.outlineStyle,reducedMotion});
  }
 }
 renderPhysicalLeds({...base,vebus_leds:Object.fromEntries(keys.map(k=>[k,1]))},{live:false});
 check('stale readings are unavailable',[...document.querySelectorAll('.physical-led')].every(e=>e.dataset.ledState==='unknown'));
 for(const bad of [null,undefined,true,false,'junk',4,-1,Infinity]){
  renderPhysicalLeds({...base,vebus_leds:Object.fromEntries(keys.map(k=>[k,bad]))},{live:true});
  check('invalid LED reading '+String(bad),[...document.querySelectorAll('.physical-led')].every(e=>e.dataset.ledState==='unknown'));
 }
 renderPhysicalLeds({...base,vebus_leds:{Overload:2,LowBattery:3}},{live:true});
 const inverse=document.getElementById('ledLowBatteryMobile'),normal=document.getElementById('ledOverloadMobile');
 check('normal and inverse blink remain distinguishable',reducedMotion?
  normal.dataset.ledState==='2'&&inverse.dataset.ledState==='3'&&inverse.getAttribute('aria-label').includes('inverse blinking'):
  getComputedStyle(inverse).animationDelay==='-0.5s');
 const modeCases=[
  [{Mains:0,Inverter:1},false,'INVERTING'],
  [{Mains:1,Inverter:0},true,'PASS THROUGH'],
  [{Mains:1,Inverter:2},true,'POWER ASSIST'],
  [{Mains:1,Inverter:0},false,'AC NOT ACCEPTED'],
  [{Mains:null,Inverter:1},false,'UNKNOWN']
 ];
 for(const [leds,connected,want] of modeCases){
  const got=observedInverterMode({...base,vebus_leds:leds,ac_connected:connected},{live:true});
  check('observed mode '+want,got===want,got);
 }
 draw(base,{live:true,solarOk:true,controllerOk:true});layoutEnergyConnections();
 const front=document.querySelector('.multiplus-front').getBoundingClientRect();
 for(const e of document.querySelectorAll('.front-led-name')){
  const r=e.getBoundingClientRect();
  check('label fits: '+e.textContent,r.left>=front.left&&r.right<=front.right&&r.top>=front.top&&r.bottom<=front.bottom,{left:r.left,right:r.right,frontRight:front.right});
 }
 check('exactly eight physical LEDs',document.querySelectorAll('.physical-led[data-led-key]').length===8);
 const map=document.getElementById('energyMap'),r=map.getBoundingClientRect();
 for(const e of map.querySelectorAll('.energy-node')){
  const b=e.getBoundingClientRect();
  check('node fits '+e.id,b.left>=r.left&&b.right<=r.right,{left:b.left,right:b.right,mapRight:r.right});
 }
 for(const key of ['Pv','Grid','Dc','Battery','House']){
  const path=document.getElementById('wire'+key+'Mobile');
  check('conductor remains visible '+key,getComputedStyle(path).visibility==='visible'&&path.getAttribute('d').length>5,path.getAttribute('d'));
 }
 check('no document horizontal overflow',document.documentElement.scrollWidth<=window.innerWidth,{scroll:document.documentElement.scrollWidth,width:window.innerWidth});
 for(const host of document.querySelectorAll('.mini-chart-scroll')){
  const svg=host.querySelector('svg.chart'),labels=[...svg.querySelectorAll('text.axis-label')].filter(e=>Number(e.getAttribute('y'))>174);
  check('mini graph has no horizontal scrolling',host.scrollWidth<=host.clientWidth+1,{scroll:host.scrollWidth,width:host.clientWidth});
  check('mini graph fits card',Math.abs(svg.getBoundingClientRect().width-host.clientWidth)<=1);
  check('mini graph shows whole day',labels.length>=2&&labels[0].textContent==='12a'&&labels[labels.length-1].textContent==='12a',labels.map(e=>e.textContent));
 }
 const dc=document.getElementById('wireDcMobile').getBBox();
 check('busbar conductor is one straight segment',dc.width<.1||dc.height<.1,{width:dc.width,height:dc.height});
 const gridWire=document.getElementById('wireGridMobile'),endpoint=gridWire.getPointAtLength(gridWire.getTotalLength());
 const cabinet=document.getElementById('multiplusCabinet').getBoundingClientRect(),mapBounds=map.getBoundingClientRect();
 const left=cabinet.left-mapBounds.left,right=cabinet.right-mapBounds.left,top=cabinet.top-mapBounds.top,bottom=cabinet.bottom-mapBounds.top;
 check('grid conductor meets the visible inverter cabinet',
  Math.abs(endpoint.y-top)<1&&endpoint.x>=left&&endpoint.x<=right||
  Math.abs(endpoint.x-right)<1&&endpoint.y>=top&&endpoint.y<=bottom,{x:endpoint.x,y:endpoint.y,left,right,top,bottom});
 check('redundant inverter status line removed',document.getElementById('inverterTelemetryNote')===null);
 const fixed=Date.UTC(2026,10,9,23,49);
 const timing=fullChargeTiming({timestamp:'2026-11-09T23:49:00Z',last_full_charge_seconds:7*86400},fixed,fixed);
 check('rolling day seven has three days to request',timing.requestRemaining===3*86400);
 check('new full event restarts ten elapsed days',fullChargeTiming({timestamp:'2026-11-09T23:49:00Z',last_full_charge_seconds:0},fixed,fixed).requestRemaining===10*86400);
 showMaintenance({...base,maintenance:{phase:'idle',available:true}});
 check('last verified full charge includes Eastern time',/[AP]M ET$/.test(document.getElementById('maintenanceLastFull').textContent),document.getElementById('maintenanceLastFull').textContent);
 showMaintenance({...base,last_full_charge_seconds:0,maintenance:{phase:'hold',available:true,last_full_event_at:Date.parse(base.timestamp)/1000-3601}});
 check('maintenance window keeps its original full-event anchor',document.getElementById('maintenanceCountdown').textContent==='00:00:00');
 showMaintenance(base);
 check('graph selection starts clear after loading',explorerSelection===null);
 const originalMetric=document.getElementById('metricSelect').value,originalHistory=history;
 document.getElementById('metricSelect').value='load';
 document.querySelector('[data-view="analytics"]').click();
 renderExplorer();
 const plotted=document.getElementById('explorerPlot'),hit=document.getElementById('plotHit');
 const available=plotted._series.pts.filter(p=>p.v!==null);
 function tapAt(time){
  const series=plotted._series,b=series.box,rect=plotted.getBoundingClientRect();
  const x=b[0]+(time-series.window.start)/(series.window.end-series.window.start)*b[2];
  hit.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch',clientX:rect.left+x/plotted.viewBox.baseVal.width*rect.width,clientY:rect.top+50}));
 }
 if(available.length>=3){
  tapAt(available[Math.floor(available.length*.3)].t);
  const pinned=document.getElementById('plotHoverText').textContent;
  check('phone tap pins a timestamp and reading',!!explorerSelection&&pinned.startsWith('Selected · '),pinned);
  hit.dispatchEvent(new PointerEvent('pointerleave',{pointerType:'touch'}));
  check('lifting finger preserves pinned point',document.getElementById('explorerCrosshair').getAttribute('opacity')==='1'&&document.getElementById('plotHoverText').textContent===pinned);
  history=history.map(p=>p.t===explorerSelection.t?{...p,load:p.load+900}:p);
  renderExplorer();
  check('history and live redraw preserve the selected reading',document.getElementById('plotHoverText').textContent===pinned);
  tapAt(available[Math.floor(available.length*.8)].t);
  check('another tap selects a new timestamp',document.getElementById('plotHoverText').textContent!==pinned);
 }else{
  for(const name of ['phone tap pins a timestamp and reading','lifting finger preserves pinned point','history and live redraw preserve the selected reading','another tap selects a new timestamp'])check(name,false,'Fixture history missing');
 }
 explorerSelection=null;history=originalHistory;document.getElementById('metricSelect').value=originalMetric;
 document.querySelector('[data-view="overview"]').click();renderMiniCharts();
 window.parent.postMessage({solarFortressRenderProof:proof},'*');
},200);
