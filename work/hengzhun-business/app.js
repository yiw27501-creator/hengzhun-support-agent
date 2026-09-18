'use strict';
const el=id=>document.getElementById(id);let items=[],filter='all',selected=null,busy=false,timer=null,pendingBatch=null;
const names={done:'已办结',human:'待人工处理',processing:'Agent 处理中',waiting:'等待客户 · 无需客服接管',system:'等待物流 / 仓库'};
const operationsLink=document.createElement('a');operationsLink.href='/operations';operationsLink.textContent='模拟运营分析';document.querySelector('aside nav').append(operationsLink);
const eventNames={'event.received':'收到缺货售后工单','context.loaded':'核对订单事实与当前授权','planner.completed':'已完成处理方案校验','human.requested':'自动处理暂停，交给人工核对','guardrail.blocked':'安全校验未通过，未执行操作','tool.executed':'已执行沙箱业务操作','tool.response_lost':'未收到工具响应，准备查询回执','execution.reconciled':'回执核对完成，未重复执行','result.verified':'已核验办理结果','human.confirmed':'运营已核对当前客户意愿','consent.invalidated':'收到新消息，旧授权失效','planner.failed':'处理遇到异常，等待重试或核对'};
async function api(path,body){const r=await fetch('/api/workflow/'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Workbench':'1'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw Error(d.error||'请求未完成');return d;}
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
function list(){const query=el('search').value.trim();el('tickets').replaceChildren();const shown=items.filter(x=>(filter==='all'||x.status===filter)&&(!query||[x.order_id,x.product,x.message,x.customer].join(' ').includes(query)));el('total').textContent=shown.length+' 单';for(const x of shown){const b=node('button','','ticket'+(selected===x.id?' selected':''));const top=node('div','','ticket-top');top.append(node('span',x.customer),node('span',names[x.status],'pill '+x.status));b.append(top,node('h3',x.title),node('p',x.message),node('small',x.order_id+' · '+x.product));b.onclick=()=>act(async()=>{selected=x.id;await detail();list();});el('tickets').append(b);}if(!shown.length)el('tickets').append(node('p','暂无符合条件的工单。','scope'));}
async function detail(){if(!selected)return;const {task:t,meta}=await api('business/'+selected);const f=t.order.facts;const status=t.state==='COMPLETED'?'done':t.state==='WAITING_HUMAN'?'human':'processing';el('empty').hidden=true;el('case').hidden=false;el('order').textContent=t.order_id+' · '+meta.customer;el('title').textContent=meta.title;el('badge').replaceChildren(node('span',names[status],'pill '+status));el('facts').textContent=`${meta.product} · 实付 ¥${(f.paid_minor/100).toFixed(2)} · ${f.identity_verified?'身份已核验（演练）':'身份未核验'} · ${f.fresh_stock?'库存快照有效':'库存信息待更新'}`;el('message').textContent=f.customer_text;
let title='正在核对处理条件',text='后台会检查订单、授权与风险，符合条件才执行。';if(t.receipt){title=t.receipt.action==='refund'?'退款操作已完成（沙箱）':'换货操作已完成（沙箱）';text='已取得业务回执。'+(t.trace.some(r=>r.event==='execution.reconciled')?'曾发生响应丢失，已通过回执核验，没有重复办理。':'系统已核验业务结果。');}else if(status==='human'){title='已暂停，需要你核对';text=!f.identity_verified?'身份尚未验证，不能办理退款或换货。':!f.fresh_stock?'库存信息已过期，不能按旧库存安排换货。':f.confirmed_intent===null?'尚无当前有效授权。请确认客户是否仍希望继续，不能沿用旧选择。':t.plan?.reason||'当前条件无法自动处理，请查看核对依据。';}
el('outcome-title').textContent=title;el('outcome-text').textContent=text;el('handoff-reason').textContent=text;el('handoff').hidden=status!=='human';el('confirm').hidden=!f.identity_verified||!f.fresh_stock;el('timeline').replaceChildren();for(const e of t.trace.filter(e=>eventNames[e.event])){const row=node('li',eventNames[e.event]);row.append(node('time',new Date(e.at).toLocaleTimeString('zh-CN')));el('timeline').append(row);}el('evidence').textContent=JSON.stringify({task_id:t.id,state:t.state,receipt:t.receipt,trace:t.trace},null,2);el('case').dataset.version=f.dialogue_version;el('case').dataset.message=f.message_id;}
async function refresh(){const d=await api('business');items=d.items;const m=d.metrics;el('automatic').textContent=m.automatic;el('human').textContent=m.human_waiting;el('rate').textContent=m.automatic_rate===null?'—':(m.automatic_rate*100).toFixed(0)+'%';el('duration').textContent=m.mean_completion_seconds===null?'—':m.mean_completion_seconds.toFixed(1)+'s';el('mode').textContent=d.mode==='rule'?'规则演练 · 无模型费用':'真实模型模式 · 批量演练禁用';el('generate').disabled=busy||d.mode!=='rule';list();if(selected)await detail();clearTimeout(timer);if(m.processing>0)timer=setTimeout(()=>{if(!busy)refresh().catch(showError);},2000);}
function showError(e){el('notice').textContent='未完成：'+e.message+'。请刷新页面后重试；不要重复确认已执行订单。';}
async function act(fn){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();await refresh();el('notice').textContent='状态已更新。自动处理与人工接管使用同一任务记录。';}catch(e){showError(e);}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);el('generate').disabled=el('mode').textContent.includes('批量演练禁用');}}
el('generate').onclick=()=>act(async()=>{if(!pendingBatch)pendingBatch={event_id:crypto.randomUUID(),count:Number(el('count').value)};const d=await api('business/batch',pendingBatch);pendingBatch=null;el('notice').textContent=`已提交 ${d.count} 单合成工单，后台自动处理中。`;});
document.querySelectorAll('[data-filter]').forEach(b=>b.onclick=()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('chosen',x===b));list();});el('search').oninput=list;
el('new-message').onsubmit=e=>{e.preventDefault();act(async()=>{await api('tasks/'+selected+'/messages',{message_id:crypto.randomUUID(),text:el('reply').value.trim(),expected_version:Number(el('case').dataset.version)});el('reply').value='';});};
el('confirm').onsubmit=e=>{e.preventDefault();if(!confirm('请确认已核对最新客户意愿。本操作只执行沙箱业务，不涉及真实资金。'))return;act(async()=>{await api('tasks/'+selected+'/confirm',{intent:el('intent').value,source_message_id:el('case').dataset.message,expected_version:Number(el('case').dataset.version),note:el('note').value.trim()});await api('tasks/'+selected+'/enqueue',{});el('confirm').reset();});};
const baseDetail=detail;
const baseRefresh=refresh;
refresh=async function(){await baseRefresh();const d=await api('business');if(d.business_model)el('mode').textContent=`对话：真实模型 · 执行：规则沙箱 · 剩余 ${d.model_budget.remaining_calls} 次调用`;};
detail=async function(){
 await baseDetail();
 const {task:t,meta,chat_enabled,chat_records}=await api('business/'+selected);const j=meta.journey;
 let chat=el('business-chat');if(!chat){chat=node('section','','outcome');chat.id='business-chat';el('handoff').before(chat);}chat.replaceChildren();
 chat.append(node('h3','客户原话 → 模型理解 → 工具核验'),node('p',chat_enabled?'真实模型已启用：每次发送最多 1 次付费调用；业务查询为模拟工具，执行仍需明确确认。':'真实模型未启用；不会将规则回复伪装成模型输出。'));
 if(chat_enabled&&!t.receipt&&meta.kind!=='return'){
  const form=node('form',''),label=node('label','客户的新消息（仅合成数据）'),input=node('textarea','');input.required=true;input.maxLength=2000;label.append(input);form.append(label);const send=node('button','发送并让 Agent 核验','primary');form.append(send);
  let pending=null;form.onsubmit=e=>{e.preventDefault();if(!pending)pending={request_id:crypto.randomUUID(),text:input.value,expected_version:t.order.facts.dialogue_version};act(()=>api('business/'+selected+'/chat',pending));};chat.append(form);
 }
 for(const r of chat_records||[]){chat.append(node('p',r.status+' · '+(r.reply||'等待结果或已被新消息取代')));if(r.result)chat.append(node('small',`模型 ${r.result.model} · 工具 ${r.result.decision.tool} · tokens ${r.result.usage?.total_tokens??'未知'} · ${r.result.latency_ms}ms`));}
 let box=el('journey');if(!box){box=node('section','','outcome');box.id='journey';el('handoff').before(box);}box.replaceChildren();box.hidden=!j;
 if(!j)return;
 if(meta.kind==='return'){
  el('handoff').hidden=true;
  box.append(node('h3','已签收退货 · 寄回验收后退款'),node('p','所有客户、客服、物流和仓库操作均为本机沙箱演练，不涉及真实物流或资金。','hint'));
  for(const message of j.messages)box.append(node('p',message));
  if(j.return_id)box.append(node('p','退货单：'+j.return_id));if(j.tracking)box.append(node('p','模拟物流单号：'+j.tracking));
  const opts={return_review:[['propose_return','客服：提出退货退款方案']],return_confirm:[['accept_return','客户：同意方案并创建退货单'],['decline_return','客户：不同意，重新沟通']],return_shipping:[['ship_return','客户：模拟提交寄回物流']],return_transit:[['receive_return','物流：模拟仓库签收'],['logistics_exception','物流：模拟运输异常']],return_exception:[['retry_logistics','客服：核查后重新查询物流']],return_inspection:[['pass_inspection','仓库：模拟验收通过'],['fail_inspection','仓库：模拟验收不通过']],return_dispute:[['reinspect','客服：申请重新验收']]};
  if(t.state!=='COMPLETED'){
   for(const [action,label] of opts[j.stage]||[]){const b=node('button',label,'secondary');b.onclick=()=>act(()=>api('business/'+selected+'/advance',{action,expected_version:j.version}));box.append(b);}
   const status=items.find(x=>x.id===selected)?.status||'processing';el('badge').replaceChildren(node('span',names[status],'pill'));
   el('outcome-title').textContent=names[status];el('outcome-text').textContent=j.messages.at(-1);
  }
  return;
 }
 box.append(node('h3','Agent 与客户协作'),node('p','以下按钮模拟客户或购买人的操作，不代表客服审核，也不是真实身份验证。','hint'));
 for(const message of j.messages)box.append(node('p',message));
 const options={verify:[['verify_recipient','模拟：收件人验证通过']],authorize:[['buyer_authorize','模拟：购买人授予售后授权']],offer:[['replace','客户确认换货'],['refund','客户确认退款'],['pause','客户暂不处理']],paused:[['product_info','客户询问商品信息'],['replace','客户重新确认换货'],['refund','客户重新确认退款']]};
 if(t.state!=='COMPLETED'&&j.stage!=='executing'){
  for(const [action,label] of [...(options[j.stage]||[]),...(j.stage==='human'?[]:[['human','客户要求转人工']])]){
   const b=node('button',label,'secondary');b.onclick=()=>act(()=>api('business/'+selected+'/advance',{action,expected_version:j.version}));box.append(b);
  }
  if(j.stage!=='human'){
   el('handoff').hidden=true;el('badge').replaceChildren(node('span',names.waiting,'pill'));
   el('outcome-title').textContent='Agent 已推进，等待客户下一步';el('outcome-text').textContent=j.messages.at(-1);
  }
 }
};
const returnDemo=node('button','新增已签收退货工单','secondary');el('generate').after(returnDemo);
returnDemo.onclick=()=>act(async()=>{await api('business/batch',{event_id:crypto.randomUUID(),count:1,profile:'return'});});
act(async()=>{await api('session',{});});
