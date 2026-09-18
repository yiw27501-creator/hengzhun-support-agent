"""Business presentation layer over the existing sandbox engine, not a new agent."""
import json
import uuid
from datetime import datetime
from engine import Conflict, now

SCENARIOS=[
 ('replace','缺货 · 同意换货','可以换同档商品，价格不要增加。','已确认换货，核对库存后可处理。'),
 ('refund','缺货 · 同意退款','不等补货了，我确认按实付金额退款。','已确认退款，仍需校验支付和金额。'),
 ('conflict','客户改口','刚才说可以换，现在先不要处理，我再想想。','客户已叫停，旧选择不能继续用于执行。'),
 ('timeout','回执查询','可以换同档商品，换好后告诉我。','演练工具响应丢失：先查回执，禁止重复换货。'),
 ('identity','身份待核实','这是朋友买的订单，帮我直接退掉。','身份未验证，人工也不能绕过校验。'),
 ('stock','库存待核实','如果还有同档现货，就帮我换。','库存快照已过期，不能假定仍有货。')]


class Business:
    def __init__(self,engine,runtime):
        self.engine=engine;self.runtime=runtime
        with engine.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS business_batches(id TEXT PRIMARY KEY,count INTEGER,created_at TEXT);
            CREATE TABLE IF NOT EXISTS business_tickets(task_id TEXT PRIMARY KEY,batch_id TEXT,body TEXT);''')

    def batch(self,event_id,count,profile='shortage'):
        if profile not in {'shortage','return'}:raise ValueError('Invalid profile')
        if not isinstance(event_id,str) or not 1<=len(event_id)<=80 or type(count) is not int or count not in ({1} if profile=='return' else {6,30,60}):
            raise ValueError('Select 6, 30 or 60 synthetic tickets')
        if not self.runtime or self.runtime.key:
            raise Conflict('Bulk simulation requires rule mode; no automatic paid batch calls')
        with self.engine.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute('SELECT count FROM business_batches WHERE id=?',(event_id,)).fetchone()
            if prior and prior['count']!=count:raise Conflict('Batch ID reused with another count')
            if not prior:
                if c.execute('SELECT COUNT(*) FROM business_tickets').fetchone()[0]+count>600:
                    raise Conflict('Local demonstration limit is 600 tickets')
                c.execute('INSERT INTO business_batches VALUES(?,?,?)',(event_id,count,now()))
                for i in range(count):
                    kind,title,message,why=SCENARIOS[i%len(SCENARIOS)]
                    if profile=='return':kind,title,message,why='return','已签收 · 退货退款','商品已经收到，我想寄回退款，请客服帮我办理。','签收退货必须先确认方案、寄回、验收，再退款。'
                    task=uuid.uuid4().hex;order='HZ-'+task[:8].upper();created=now()
                    intent='refund' if kind=='refund' else 'replace'
                    facts=dict(payment='paid',paid_minor=19900,stock=3,price_minor=18900,fresh_stock=kind!='stock',
                        identity_verified=kind!='identity',safety_risk=False,sku='ALT-A',confirmed_intent=None if kind in {'conflict','identity','stock'} else intent,
                        preference_conflict=kind=='conflict',human_confirmed=False,customer_text=message,message_id='message-'+task,
                        message_at=created,accept_substitute=kind in {'replace','timeout'},dialogue_version=1,
                        authorized_version=1 if kind in {'replace','refund','timeout'} else None)
                    if kind=='return':facts.update(return_required=True,return_inspected=False,confirmed_intent=None)
                    c.execute('INSERT INTO orders(id,facts,fail_mode) VALUES(?,?,?)',(order,json.dumps(facts,ensure_ascii=False),'timeout_after_commit' if kind=='timeout' else 'none'))
                    c.execute('INSERT INTO tasks(id,event_id,order_id,state,created_at) VALUES(?,?,?,?,?)',(task,'batch-'+task,order,'READY',created))
                    c.execute('INSERT INTO task_messages VALUES(?,?,?,?,?)',(task,facts['message_id'],1,message,created))
                    meta=dict(customer='演练客户 '+task[:4].upper(),product=['轻量耳机','便携音箱','桌面台灯'][i%3],title=title,kind=kind,why=why,
                              source='项目编写的中文合成场景；均衡配比不是商家真实分布',sandbox=True)
                    if kind=='return':meta['journey']=dict(stage='return_review',version=1,messages=['客户请求人工协助。模拟订单已签收，符合演练退货政策；客服可提出寄回退款方案。'],history=[])
                    c.execute('INSERT INTO business_tickets VALUES(?,?,?)',(task,event_id,json.dumps(meta,ensure_ascii=False)))
                    self.engine.trace(c,task,'event.received',order_id=order,environment='business-simulation')
            ids=[r[0] for r in c.execute('SELECT task_id FROM business_tickets WHERE batch_id=?',(event_id,))]
        # Idempotent outbox-like recovery: retrying the same batch re-enqueues missing jobs.
        for task in ids:
            self.prepare(task)
            self.runtime.queue.enqueue(task)
        return dict(batch_id=event_id,count=len(ids),mode='rule',paid_calls=0)

    def prepare(self,task):
        """Persist simulated department responses before offering customer choices."""
        with self.engine.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body FROM business_tickets WHERE task_id=?',(task,)).fetchone()
            if not row:raise KeyError(task)
            meta=json.loads(row[0])
            if meta.get('journey'):return
            kind=meta['kind']
            if kind not in {'stock','identity','conflict'}:return
            messages={
                'stock':'已重新查询模拟库存系统：替代商品 ALT-A 可用 3 件，价格 ¥189，不高于原实付 ¥199。尚未换货，请确认是否接受替代商品。',
                'identity':'已查询模拟购买记录：订单为礼物订单。收件关系不等于退款授权，请先在演练中完成收件人验证，再由购买人授权。',
                'conflict':'已暂停，不会继续换货。你可以继续考虑；如果愿意，可以查看替代商品信息，或重新选择方案。'}
            meta['journey']={'stage':{'stock':'offer','identity':'verify','conflict':'paused'}[kind],
                             'messages':[messages[kind]],'version':1}
            if kind=='stock':
                order=c.execute('SELECT order_id FROM tasks WHERE id=?',(task,)).fetchone()[0]
                f=json.loads(c.execute('SELECT facts FROM orders WHERE id=?',(order,)).fetchone()[0])
                f.update(stock=3,fresh_stock=True,price_minor=18900)
                c.execute('UPDATE orders SET facts=? WHERE id=?',(json.dumps(f,ensure_ascii=False),order))
            self.engine.trace(c,task,'department.checked',tool={'stock':'inventory.lookup','identity':'purchase.lookup','conflict':'execution.pause'}[kind],sandbox=True)
            c.execute('UPDATE business_tickets SET body=? WHERE task_id=?',(json.dumps(meta,ensure_ascii=False),task))

    def advance(self,task,action,expected_version):
        if not self.runtime or self.runtime.key:raise Conflict('Simulation controls require rule mode')
        if self.detail(task)['meta']['kind']=='return':return self.advance_return(task,action,expected_version)
        allowed={'verify':{'verify_recipient'},'authorize':{'buyer_authorize'},'offer':{'replace','refund','pause'},
                 'paused':{'product_info','replace','refund'},'human':set(),'executing':set()}
        with self.engine.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT b.body,t.order_id FROM business_tickets b JOIN tasks t ON t.id=b.task_id WHERE t.id=?',(task,)).fetchone()
            if not row:raise KeyError(task)
            meta=json.loads(row['body']);j=meta.get('journey')
            if not isinstance(action,str):raise ValueError('Action must be a string')
            if not j or type(expected_version) is not int or expected_version!=j['version']:raise Conflict('Journey changed; refresh')
            if action not in allowed.get(j['stage'],set())|({'human'} if j['stage'] not in {'human','executing'} else set()):raise Conflict('Action not allowed in current stage')
            if c.execute('SELECT 1 FROM executions WHERE order_id=?',(row['order_id'],)).fetchone():raise Conflict('Already executed')
            f=json.loads(c.execute('SELECT facts FROM orders WHERE id=?',(row['order_id'],)).fetchone()[0])
            if action=='verify_recipient':
                j['stage']='authorize';reply='模拟收件人验证通过。仍未获得购买人授权，不执行退款；请由购买人确认。'
            elif action=='buyer_authorize':
                j['stage']='offer';f['identity_verified']=True;reply='模拟购买人授权已收到。请选择售后方案；退款仅按原支付路径执行（沙箱）。'
            elif action=='product_info':
                reply='模拟商品目录 ALT-A：售价 ¥189，可用库存 3 件。暂无其他已验证优势或到货承诺，不编造推荐理由。当前仍暂停，查看信息不会触发换货。'
            elif action=='pause':
                j['stage']='paused';reply='已暂停。你确认之前，不会继续办理。'
            elif action=='human':
                j['stage']='human';reply='已按客户要求转人工，已核验的信息与处理记录会一并保留。'
            else:
                if not f['identity_verified']:raise Conflict('Verification required')
                version=f.get('dialogue_version',0)+1;message=uuid.uuid4().hex
                reply='客户通过演练选项明确确认'+('换货' if action=='replace' else '退款')
                c.execute('INSERT INTO task_messages VALUES(?,?,?,?,?)',(task,message,version,reply,now()))
                f.update(dialogue_version=version,authorized_version=version,message_id=message,customer_text=reply,
                         confirmed_intent=action,accept_substitute=action=='replace',preference_conflict=False,human_confirmed=False)
                c.execute("UPDATE tasks SET state='READY',plan=NULL,attempts=0 WHERE id=?",(task,))
                j['stage']='executing'
            j['version']+=1;j['messages'].append(reply)
            c.execute('UPDATE orders SET facts=? WHERE id=?',(json.dumps(f,ensure_ascii=False),row['order_id']))
            c.execute('UPDATE business_tickets SET body=? WHERE task_id=?',(json.dumps(meta,ensure_ascii=False),task))
            self.engine.trace(c,task,'business.progressed',action=action,stage=j['stage'],sandbox=True)
        if action in {'replace','refund'}:self.runtime.queue.resume(task)
        return self.detail(task)

    def advance_return(self,task,action,expected_version):
        transitions={
            'return_review':{'propose_return':('return_confirm','客服已提出退货退款方案：寄回并验收通过后，按原支付路径退 ¥199；演练运费由商家承担。请客户确认。')},
            'return_confirm':{'accept_return':('return_shipping','客户确认方案。已创建模拟退货单，请提交寄回物流。'),'decline_return':('return_review','客户未接受方案，退回客服重新沟通，未执行退款。')},
            'return_shipping':{'ship_return':('return_transit','已登记模拟寄回物流，等待仓库签收。')},
            'return_transit':{'receive_return':('return_inspection','模拟仓库已签收，待验收；签收不等于验收通过。'),'logistics_exception':('return_exception','模拟物流异常，转客服核查，尚未退款。')},
            'return_exception':{'retry_logistics':('return_transit','客服已提交重新查询物流，继续等待仓库签收。')},
            'return_inspection':{'pass_inspection':('executing','模拟验收通过，Agent 将进行退款前校验并执行退款。'),'fail_inspection':('return_dispute','模拟验收发现商品不符，转人工争议处理，不能直接退款。')},
            'return_dispute':{'reinspect':('return_inspection','客服申请重新验收，等待仓库提供新结果。')},
        }
        with self.engine.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT b.body,t.order_id FROM business_tickets b JOIN tasks t ON t.id=b.task_id WHERE t.id=?',(task,)).fetchone()
            meta=json.loads(row['body']);j=meta['journey']
            if type(expected_version) is not int or expected_version!=j['version']:raise Conflict('Journey changed; refresh')
            if not isinstance(action,str) or action not in transitions.get(j['stage'],{}):raise Conflict('Action not allowed in current stage')
            if c.execute('SELECT 1 FROM executions WHERE order_id=?',(row['order_id'],)).fetchone():raise Conflict('Already executed')
            f=json.loads(c.execute('SELECT facts FROM orders WHERE id=?',(row['order_id'],)).fetchone()[0])
            stage,reply=transitions[j['stage']][action]
            if action=='accept_return':
                j['return_id']='RET-'+task[:12].upper()
                v=f.get('dialogue_version',0)+1;mid=uuid.uuid4().hex
                c.execute('INSERT INTO task_messages VALUES(?,?,?,?,?)',(task,mid,v,'客户确认寄回验收后退款 ¥199 的方案',now()))
                f.update(dialogue_version=v,authorized_version=v,message_id=mid,customer_text='客户确认寄回验收后退款 ¥199 的方案',confirmed_intent='refund',human_confirmed=True,preference_conflict=False)
            if action=='ship_return':j['tracking']='SIM-'+task[:12].upper()
            if action=='pass_inspection':
                if f.get('authorized_version')!=f.get('dialogue_version') or f.get('confirmed_intent')!='refund':raise Conflict('Customer authorization changed; manual review required')
                f['return_inspected']=True
                c.execute("UPDATE tasks SET state='READY',plan=NULL,attempts=0 WHERE id=?",(task,))
            else:c.execute("UPDATE tasks SET state='WAITING_HUMAN' WHERE id=?",(task,))
            j['stage']=stage;j['version']+=1;j['messages'].append(reply)
            j['history'].append(dict(action=action,at=now(),stage=stage,sandbox=True))
            c.execute('UPDATE orders SET facts=? WHERE id=?',(json.dumps(f,ensure_ascii=False),row['order_id']))
            c.execute('UPDATE business_tickets SET body=? WHERE task_id=?',(json.dumps(meta,ensure_ascii=False),task))
            self.engine.trace(c,task,'return.progressed',action=action,stage=stage,sandbox=True)
        if action=='pass_inspection':self.runtime.queue.resume(task)
        return self.detail(task)

    def list(self):
        with self.engine.connect() as c:
            rows=c.execute('SELECT t.*,b.body,o.facts FROM business_tickets b JOIN tasks t ON t.id=b.task_id JOIN orders o ON o.id=t.order_id ORDER BY t.created_at DESC').fetchall()
            items=[];durations=[]
            for row in rows:
                f=json.loads(row['facts']);item=dict(json.loads(row['body']),id=row['id'],order_id=row['order_id'],state=row['state'],created_at=row['created_at'],
                    message=f['customer_text'],human_confirmed=f['human_confirmed'],amount_minor=f['paid_minor'])
                item['status']='done' if row['state']=='COMPLETED' else 'human' if row['state']=='WAITING_HUMAN' else 'processing'
                if item.get('journey',{}).get('stage') in {'verify','authorize','offer','paused'} and item['status']!='done':item['status']='waiting'
                stage=item.get('journey',{}).get('stage','')
                if item['status']!='done' and stage.startswith('return_'):
                    item['status']='human' if stage in {'return_review','return_exception','return_dispute'} else 'waiting' if stage in {'return_confirm','return_shipping'} else 'system'
                if item['status']=='done':
                    end=c.execute("SELECT at FROM traces WHERE task_id=? AND event IN ('result.verified','execution.reconciled') ORDER BY seq DESC LIMIT 1",(row['id'],)).fetchone()
                    if end:durations.append((datetime.fromisoformat(end[0])-datetime.fromisoformat(row['created_at'])).total_seconds())
                items.append(item)
        total=len(items);auto=sum(x['status']=='done' and not x['human_confirmed'] for x in items)
        return dict(items=items,metrics=dict(total=total,automatic=auto,human_waiting=sum(x['status']=='human' for x in items),
                    manual_completed=sum(x['status']=='done' and x['human_confirmed'] for x in items),processing=sum(x['status']=='processing' for x in items),
                    automatic_rate=auto/total if total else None,mean_completion_seconds=sum(durations)/len(durations) if durations else None),
                    business_model=bool(getattr(self.runtime,'business_router',None)),
                    model_budget=self.runtime.health() if self.runtime else None,
                    mode='live' if self.runtime and self.runtime.key else 'rule',scope='all business simulation tickets; no real cost savings measured')

    def detail(self,task):
        with self.engine.connect() as c:
            row=c.execute('SELECT body FROM business_tickets WHERE task_id=?',(task,)).fetchone()
            if not row:raise KeyError(task)
        records=[]
        with self.engine.connect() as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='business_chat'").fetchone():
                records=[json.loads(r[0]) for r in c.execute('SELECT body FROM business_chat WHERE task_id=? ORDER BY rowid',(task,))]
        return dict(task=self.engine.get(task),meta=json.loads(row['body']),chat_enabled=bool(getattr(self.runtime,'business_router',None)),chat_records=records)
