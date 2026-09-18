'use strict';
const byId=id=>document.getElementById(id);
let dialogueId=null,faqItems=[];
byId('dialogue-submit').onclick=async()=>{const button=byId('dialogue-submit');button.disabled=true;try{const lines=byId('dialogue-input').value.trim().split('\n').filter(x=>x.trim());const messages=lines.map((line,i)=>{const match=line.match(/^(客户|客服)[：:](.+)$/);if(!match)throw Error('每行必须以客户：或客服：开头');return {id:'m'+(i+1),role:match[1]==='客户'?'customer':'agent',text:match[2].trim()};});dialogueId=crypto.randomUUID();const data=await api('dialogues',{event_id:dialogueId,messages});byId('dialogue-result').textContent=JSON.stringify(data,null,2);}catch(e){byId('dialogue-result').textContent=e.message;}finally{button.disabled=false;}};
byId('dialogue-refresh').onclick=async()=>{if(!dialogueId)return;try{byId('dialogue-result').textContent=JSON.stringify(await api('dialogues/'+dialogueId),null,2);}catch(e){byId('dialogue-result').textContent=e.message;}};
byId('faq-load').onclick=async()=>{try{const data=await api('faq');faqItems=data.items;byId('faq-select').replaceChildren(new Option('请选择',''));faqItems.forEach((f,i)=>byId('faq-select').add(new Option(f.question_zh+' · '+f.source_count+' 条',String(i))));byId('faq-result').textContent=data.available?'已加载 '+faqItems.length+' 类待审核草稿':'尚未导入数据';}catch(e){byId('faq-result').textContent=e.message;}};
byId('faq-select').onchange=()=>{const f=faqItems[byId('faq-select').value];if(f)byId('faq-result').textContent=JSON.stringify(f,null,2);};
let selected=null,timer=null,working=false,lastHealth=null,polling=false;
byId('task-message-send').onclick=async()=>{if(!selected){byId('task-message-result').textContent='请先选择任务';return;}const button=byId('task-message-send');button.disabled=true;try{selected=await api('tasks/'+selected.id+'/messages',{message_id:crypto.randomUUID(),text:byId('task-message').value.trim(),expected_version:selected.order?.facts.dialogue_version||0,reply_to:selected.clarification?.id});byId('task-message-result').textContent='新消息已保存，旧授权已失效。当前消息版本：'+selected.order.facts.dialogue_version;await read();}catch(e){byId('task-message-result').textContent=e.message;}finally{button.disabled=false;}};
async function api(path,body){const r=await fetch('/api/workflow/'+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-Workbench':'1'},credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d;}
function lock(){byId('create').disabled=working||!lastHealth?.worker_alive;byId('enqueue').disabled=working||!selected||!['READY','RETRYABLE'].includes(selected.state);byId('task').disabled=working;byId('refresh').disabled=working;for(const e of byId('confirm').elements)e.disabled=working;}
async function act(fn){if(working)return;working=true;lock();try{await fn();}catch(e){byId('status').textContent='操作未完成：'+e.message;}finally{working=polling;lock();}}
async function health(){const h=await api('health');lastHealth=h.async_runtime;byId('health').textContent=lastHealth?`规划器 ${lastHealth.planner} · worker ${lastHealth.worker_alive?'在线':'离线'} · 真实模型 ${lastHealth.paid_model?'启用':'未启用'} · 已预留 ${lastHealth.reserved_calls}/${lastHealth.max_calls} 次调用`:'异步模式未启用，请使用 --async-jobs 启动本机服务。';}
async function list(){const data=await api('tasks');byId('task').replaceChildren(new Option('请选择任务',''));for(const t of data.tasks)byId('task').add(new Option(t.order_id+' · '+t.state,t.id));if(selected)byId('task').value=selected.id;}
async function read(){
 if(!selected)return;
 const id=selected.id;
 const [task,data]=await Promise.all([api('tasks/'+id),api('tasks/'+id+'/job')]);
 selected=task;
 byId('facts').textContent=`客户原话：${task.order.facts.customer_text}；消息版本：${task.order.facts.dialogue_version||0}；授权版本：${task.order.facts.authorized_version??'无'}；确认意愿：${task.order.facts.confirmed_intent||'未知'}；业务状态：${task.state}`;
 byId('result').textContent=JSON.stringify({state:task.state,understanding:task.understanding,plan:task.plan,receipt:task.receipt},null,2);
 const u=task.understanding,ex=u?.result?.extraction;
 byId('understanding-summary').textContent=u?`消息版本 ${u.version} · 处理状态 ${u.state} · 意图 ${ex?.intent||'未识别'} · 对话状态 ${ex?.dialogue_state||'当前规划器不提供'} · 不自动授权执行`:'尚无订单消息理解记录';
 byId('clarification-question').textContent=ex?.question?`建议追问：${ex.question}`:(ex?.dialogue_state==='paused'?'客户已叫停，等待客户主动恢复。':'');
 if(task.clarification)byId('clarification-question').textContent=`待回复的澄清问题（版本 ${task.clarification.version}）：${task.clarification.question}。在“客户补充消息”中提交回复，会绑定本问题并继续同一任务。`;
 byId('task-message-send').textContent=task.clarification?'回复当前澄清问题':'保存消息并使旧授权失效';
 byId('trace').textContent=JSON.stringify(task.trace,null,2);
 byId('job').textContent=data.job?`执行队列 ${data.job.status} · 尝试 ${data.job.attempts} 次`:'尚未提交执行';
 const active=data.job&&['QUEUED','RUNNING'].includes(data.job.status);
 const understandingPending=task.understanding&&['PENDING','SUBMITTED'].includes(task.understanding.state);
 polling=!!(active||understandingPending);
 byId('confirm').hidden=task.state!=='WAITING_HUMAN'||polling;
 await health();clearTimeout(timer);working=polling;
 if(polling)timer=setTimeout(()=>read().catch(e=>{polling=false;working=false;lock();byId('status').textContent='进度查询中断，请刷新：'+e.message;}),1000);
 lock();
}
byId('create').onclick=()=>act(async()=>{selected=await api('demo',{scenario:byId('scenario').value});await list();await read();byId('status').textContent='已创建，请提交异步处理。';});
byId('refresh').onclick=()=>act(async()=>{await health();await list();await read();});
byId('task').onchange=()=>act(async()=>{clearTimeout(timer);selected=byId('task').value?{id:byId('task').value}:null;if(selected)await read();});
byId('enqueue').onclick=async()=>{if(working||!selected)return;working=true;lock();try{await api('tasks/'+selected.id+'/enqueue',{});byId('status').textContent='已受理。后台处理中，页面正在查询进度。';await read();}catch(e){working=false;lock();byId('status').textContent='未提交：'+e.message;}};
byId('confirm').onsubmit=e=>{e.preventDefault();if(!byId('confirm').reportValidity())return;act(async()=>{await api('tasks/'+selected.id+'/confirm',{intent:byId('intent').value,source_message_id:selected.order.facts.message_id,note:byId('note').value.trim(),expected_version:selected.order.facts.dialogue_version});byId('confirm').reset();await read();byId('status').textContent='确认已保存，请再次提交异步处理。';});};
(async()=>{try{await api('session',{});await health();await list();byId('status').textContent='本机实验台已连接。';}catch(e){byId('status').textContent='连接失败：'+e.message;}lock();})();
