
// TEST FIXTURE ONLY: this block is never packaged or deployed.
setTimeout(async()=>{
 const proof={checks:[],failures:[],fixture:true};
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
   check(key+' code '+value+' animation',(value<2)===(c.animationName==='none'),c.animationName);
  }
 }
 renderPhysicalLeds({...base,vebus_leds:Object.fromEntries(keys.map(k=>[k,1]))},{live:false});
 check('stale readings are unavailable',[...document.querySelectorAll('.physical-led')].every(e=>e.dataset.ledState==='unknown'));
 for(const bad of [null,undefined,true,false,'junk',4,-1,Infinity]){
  renderPhysicalLeds({...base,vebus_leds:Object.fromEntries(keys.map(k=>[k,bad]))},{live:true});
  check('invalid LED reading '+String(bad),[...document.querySelectorAll('.physical-led')].every(e=>e.dataset.ledState==='unknown'));
 }
 renderPhysicalLeds({...base,vebus_leds:{Overload:2,LowBattery:3}},{live:true});
 check('opposite blink indication uses half-cycle offset',getComputedStyle(document.getElementById('ledLowBatteryMobile')).animationDelay==='-0.5s');
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
 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
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
 window.parent.postMessage({solarFortressRenderProof:proof},'*');
},200);
