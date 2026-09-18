"""Versioned local knowledge lifecycle. Only published effective policies enter RAG."""
import json
import re
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from quality import Quality, required
from engine import Conflict
from pathlib import Path


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result


class Knowledge(Quality):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS knowledge(id TEXT, version INTEGER, body TEXT, PRIMARY KEY(id,version))')

    def items(self):
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT body FROM knowledge ORDER BY id,version DESC')]

    def create(self, id, kind, title, text, source, tenant='sandbox', effective_from='2020-01-01T00:00:00Z', effective_until='2099-01-01T00:00:00Z', policy_refs=None):
        if not isinstance(id,str) or not re.fullmatch(r'[a-z0-9_-]{1,80}',id) or kind not in {'policy','faq'}:
            raise ValueError('Invalid identity or kind')
        if stamp(effective_from) >= stamp(effective_until):
            raise ValueError('Invalid interval')
        refs = policy_refs or []
        if not isinstance(refs,list) or any(not isinstance(x,str) or not re.fullmatch(r'[a-z0-9_-]+@[1-9][0-9]*',x) for x in refs):
            raise ValueError('Invalid references')
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT body FROM knowledge WHERE id=? ORDER BY version DESC LIMIT 1',(id,)).fetchone()
            old = json.loads(old[0]) if old else None
            if old and (old['kind'] != kind or old['tenant'] != tenant):
                raise Conflict('Identity cannot change kind or tenant')
            item = dict(id=id,version=old['version']+1 if old else 1,kind=kind,title=required(title),text=required(text),
                        source=required(source),tenant=required(tenant),effective_from=effective_from,effective_until=effective_until,
                        policy_refs=refs,status='DRAFT',revision=0,owner='local-operator')
            c.execute('INSERT INTO knowledge VALUES(?,?,?)',(id,item['version'],json.dumps(item)))
            self.event(c,'knowledge.created',item)
        return item

    def transition(self,id,version,revision,action,note):
        note=required(note)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body FROM knowledge WHERE id=? AND version=?',(id,version)).fetchone()
            if not row:raise KeyError(id)
            item=json.loads(row[0])
            if type(revision) is not int or revision!=item['revision']:raise Conflict('Stale knowledge version')
            transitions={('DRAFT','submit'):'IN_REVIEW',('IN_REVIEW','approve'):'APPROVED',
                         ('IN_REVIEW','reject'):'DRAFT',('APPROVED','publish'):'PUBLISHED',('PUBLISHED','retire'):'RETIRED'}
            target=transitions.get((item['status'],action))
            if not target:raise Conflict('Invalid knowledge transition')
            if target=='PUBLISHED':
                now=datetime.now(timezone.utc)
                if not stamp(item['effective_from'])<=now<stamp(item['effective_until']):raise Conflict('Not currently effective')
                if item['kind']=='faq':
                    valid={f"{d['id']}@{d['version']}" for d in self.active(item['tenant'],connection=c) if d['kind']=='policy'}
                    if not item['policy_refs'] or not set(item['policy_refs'])<=valid:raise Conflict('FAQ requires active approved policy references')
                for row in c.execute('SELECT body FROM knowledge WHERE id=? AND version<>?',(id,version)).fetchall():
                    previous=json.loads(row[0])
                    if previous['status']=='PUBLISHED':
                        previous.update(status='RETIRED',revision=previous['revision']+1)
                        c.execute('UPDATE knowledge SET body=? WHERE id=? AND version=?',(json.dumps(previous),id,previous['version']))
                        self.event(c,'knowledge.superseded',previous)
            item.update(status=target,revision=item['revision']+1,reviewer='local-operator',note=note)
            c.execute('UPDATE knowledge SET body=? WHERE id=? AND version=?',(json.dumps(item),id,version))
            self.event(c,'knowledge.'+action,item)
        return item

    def active(self,tenant='sandbox',connection=None):
        if connection is None:
            with self.connect() as c:return self.active(tenant,c)
        now=datetime.now(timezone.utc)
        items=[json.loads(r[0]) for r in connection.execute('SELECT body FROM knowledge')]
        live=[d for d in items if d['tenant']==tenant and d['status']=='PUBLISHED' and stamp(d['effective_from'])<=now<stamp(d['effective_until'])]
        refs={f"{d['id']}@{d['version']}" for d in live if d['kind']=='policy'}
        return [d for d in live if d['kind']=='policy' or (d['policy_refs'] and set(d['policy_refs'])<=refs)]

    def documents(self,tenant='sandbox',connection=None):
        return [dict(d,active=True,version=str(d['version'])) for d in self.active(tenant,connection) if d['kind']=='policy']

    @contextmanager
    def execution_guard(self,expected_hash):
        """Lock publication until the short local business commit finishes. No remote I/O here."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            documents=self.documents(connection=c)
            current=hashlib.sha256(json.dumps(documents,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            if current!=expected_hash:
                raise Conflict('Knowledge changed after planning; replan required')
            yield

    def import_drafts(self):
        from faq import catalog
        root=Path(__file__).parent
        existing={d['id'] for d in self.items()};created=[]
        for policy in json.loads((root/'policies.json').read_text(encoding='utf-8')):
            if policy['active'] and policy['tenant']=='sandbox' and policy['id'] not in existing:
                created.append(self.create(policy['id'],'policy',policy['title'],policy['text'],'项目合成政策 policies.json'))
        for faq in catalog(root/'data'/'bitext-corpus-20260915'/'support.db')['items']:
            id='faq-'+faq['intent']
            if id not in existing:
                created.append(self.create(id,'faq',faq['question_zh'],faq['answer'],
                    'Bitext 衍生问题；模板答案；CDLA-Sharing-1.0；见 DATA-FAQ-AGENT.md',policy_refs=faq['policy_evidence']))
        return dict(created=len(created),status='DRAFT',note='Imported drafts only. Review before publication.')
