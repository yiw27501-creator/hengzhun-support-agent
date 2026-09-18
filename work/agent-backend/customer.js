'use strict';
const el=id=>document.getElementById(id);let selected=null,busy=false,timer=null;
let saved=[];try{saved=JSON.parse(sessionStorage.getItem('hengzhun-tasks')||'[]');if(!Array.isArray(saved))saved=[];}catch{saved=[];}
async function api(path,body){const response=await fetch('/api/workflow/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Workbench':'1'},body:body===undefined?undefined:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'请求未完成');return data;}
function choices(){el('saved').replaceChildren(new Option('选择已保存任务',''),...saved.map(id=>new Option(id,id)));if(selected)el('saved').value=selected.id;}
function lock(){el('start').disabled=busy;el('send').disabled=busy||!selected||!!selected.receipt;el('pause').disabled=busy||!selected||!!selected.receipt;el('saved').disabled=busy;}
async function read(){if(!selected)return;selected=await api('tasks/'+selected.id);const task=selected,u=task.understanding,ex=u?.result?.extraction;
el('order').textContent='演练订单：'+task.order_id+' · 任务：'+task.id;
el('question').textContent=task.clarification?.question||'暂无待回复问题';
let stage='等待授权核对',explain='客服需要核对最新诉求和订单事实；模型理解不等于执行授权。';
if(task.receipt){stage='已执行（沙箱）';explain='已生成业务回执，不能追溯取消。真实业务应进入后续售后处理，不得直接再次执行。';}
else if(u&&['PENDING','SUBMITTED'].includes(u.state)){stage='正在理解诉求';explain='消息已入库，等待后台理解。模型未启用时会明确转人工。';}
else if(task.clarification){stage='等待你补充信息';explain='请回复当前问题，系统会绑定到同一任务。';}
else if(ex?.dialogue_state==='paused'){stage='已暂停，等待核对';explain='客户已叫停；不会凭旧授权继续处理。';}
else if(task.state==='RETRYABLE'){stage='处理异常，待重试或核验';explain='不能将超时当成未执行，应先核对回执。';}
else if(task.state==='READY'&&task.order.facts.confirmed_intent&&(!task.order.facts.preference_conflict||task.order.facts.human_confirmed)){stage='已确认诉求，等待条件校验';explain='运营可提交后台处理，系统仍须核对身份、支付、库存和金额；此状态不代表已经退款或换货。';}
el('stage').textContent=stage;el('explain').textContent=explain;
el('history').replaceChildren();for(const row of task.trace.filter(x=>x.event==='consent.invalidated')){const p=document.createElement('p');p.textContent='消息版本 '+row.details.dialogue_version+' 已保存，旧授权失效';el('history').append(p);}lock();clearTimeout(timer);if(u&&['PENDING','SUBMITTED'].includes(u.state))timer=setTimeout(()=>read().catch(e=>el('status').textContent='查询中断：'+e.message),1500);}
async function act(fn){if(busy)return;busy=true;lock();try{await fn();choices();await read();el('status').textContent='已更新 · 本机演练，不涉及真实资金';}catch(e){el('status').textContent=e.message.includes('already committed')?'操作已执行，不能追溯撤销，请联系运营核对后续处理。':'未完成：'+e.message;}finally{busy=false;lock();}}
el('start').onclick=()=>act(async()=>{selected=await api('demo',{scenario:'conflict'});saved.unshift(selected.id);sessionStorage.setItem('hengzhun-tasks',JSON.stringify(saved));});
async function send(text){selected=await api('tasks/'+selected.id+'/messages',{message_id:crypto.randomUUID(),text,expected_version:selected.order.facts.dialogue_version||0,reply_to:selected.clarification?.id});}
el('message').onsubmit=e=>{e.preventDefault();if(selected)act(async()=>{await send(el('text').value.trim());el('text').value='';});};
el('pause').onclick=()=>act(()=>send('先别处理，我撤回之前的操作请求，请暂停。'));el('refresh').onclick=()=>act(async()=>{});el('saved').onchange=()=>{clearTimeout(timer);selected=el('saved').value?{id:el('saved').value}:null;act(async()=>{});};
act(async()=>{await api('session',{});choices();});
