"""Reproducible public data import. No upstream answers become merchant policy."""
import argparse
import csv
import hashlib
import http.client
import io
import json
import re
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from faq import draft

ROOT=Path(__file__).parent
REPO='bitext/Bitext-customer-support-llm-chatbot-training-dataset'
FILE='Bitext_Sample_Customer_Support_Training_Dataset_27K_responses-v11.csv'
LICENSE='https://cdla.dev/sharing-1-0/'

def normalize(text):
    return ' '.join(re.sub(r'[^\w\s{}]', ' ', text.casefold()).split())

def build(raw, revision, folder, source=None):
    folder.mkdir(parents=True, exist_ok=True)
    path=folder/'support.db'
    con=sqlite3.connect(path)
    con.executescript('''
    CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY,question TEXT NOT NULL,
      intent TEXT NOT NULL,category TEXT NOT NULL,split TEXT NOT NULL,source_row INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS faq_drafts(intent TEXT PRIMARY KEY,question TEXT NOT NULL,
      answer TEXT NOT NULL,status TEXT NOT NULL,examples TEXT NOT NULL,source_count INTEGER NOT NULL);
    ''')
    if con.execute('SELECT COUNT(*) FROM questions').fetchone()[0]:
        con.close();raise ValueError('Choose a fresh output directory; existing corpus is preserved')
    rows=list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    seen=set(); rejected=0; duplicates=0
    with con:
        for index,row in enumerate(rows,2):
            q=row['instruction'].strip(); norm=normalize(q)
            # Conservative quarantine for literal contacts; placeholders remain synthetic.
            if not q or len(q)>2000 or re.search(r'\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b|\b\d{9,}\b',q):
                rejected+=1;continue
            digest=hashlib.sha256(norm.encode()).hexdigest()
            if digest in seen:duplicates+=1;continue
            seen.add(digest)
            split='test' if int(digest[:8],16)%5==0 else 'dev'
            con.execute('INSERT INTO questions VALUES(?,?,?,?,?,?)',(digest,q,row['intent'],row['category'],split,index))
        intents=con.execute('SELECT intent,COUNT(*) FROM questions GROUP BY intent ORDER BY intent').fetchall()
        for intent,count in intents:
            examples=con.execute("SELECT id,question FROM questions WHERE intent=? AND split='dev' ORDER BY id LIMIT 5",(intent,)).fetchall()
            # Cluster representative only; answers require merchant-specific evidence and review.
            question=examples[0][1] if examples else intent
            _,answer,_,_=draft(intent)
            con.execute('INSERT INTO faq_drafts VALUES(?,?,?,?,?,?)',(intent,question,answer,'draft',json.dumps(examples,ensure_ascii=False),count))
    counts=dict(con.execute('SELECT split,COUNT(*) FROM questions GROUP BY split').fetchall())
    faq=[dict(zip(['intent','question','answer','status','examples','source_count'],r)) for r in con.execute('SELECT * FROM faq_drafts ORDER BY intent')]
    con.close()
    metadata=dict(provider='Bitext Innovations',source=source or f'https://huggingface.co/datasets/{REPO}',revision=revision,
      license='CDLA-Sharing-1.0',license_url=LICENSE,modified=True,
      changes='Questions only; contact-pattern quarantine; normalized exact dedup; hash split; FAQ draft grouping. Upstream responses excluded.',
      nature='English hybrid synthetic; not real merchant conversations',raw_sha256=hashlib.sha256(raw).hexdigest(),
      downloaded_at=datetime.now(timezone.utc).isoformat(),input_rows=len(rows),kept=len(seen),duplicates=duplicates,quarantined=rejected,
      splits=counts,intents=dict(intents),limitations='Hash split does not prevent near-duplicate or template-family leakage. Not an independent business benchmark.')
    for filename,data in [('manifest.json',metadata),('faq_drafts.json',dict(attribution=metadata,items=faq))]:
        (folder/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    return metadata

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--github',action='store_true');p.add_argument('--legacy',action='store_true');args=p.parse_args()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    source=None
    if args.legacy:
        revision='337b96868e02b40a05f9c0df3290c65a15ea573e'
        blob='9e56c982621d2b33f406d7ced07938f9dabe9e92'
        source=f'https://github.com/bitext/customer-support-llm-chatbot-training-dataset/tree/{revision}'
        url=f'https://api.github.com/repos/bitext/customer-support-llm-chatbot-training-dataset/git/blobs/{blob}'
    elif args.github:
        repo='bitext/customer-support-llm-chatbot-training-dataset'
        source='https://github.com/'+repo
        with opener.open(f'https://api.github.com/repos/{repo}/commits/main',timeout=30) as r:revision=json.load(r)['sha']
        with opener.open(f'https://api.github.com/repos/{repo}/contents/data/{FILE}?ref={revision}',timeout=30) as r:info=json.load(r)
        blob=info['sha']
        url=f'https://api.github.com/repos/{repo}/git/blobs/{blob}'
    else:
        with opener.open(f'https://huggingface.co/api/datasets/{REPO}',timeout=30) as r: revision=json.load(r)['sha']
        url=f'https://huggingface.co/datasets/{REPO}/resolve/{revision}/{FILE}'
    if args.github or args.legacy:
        total=3562316 if args.legacy else info['size']
        if total>30_000_000:raise ValueError('Source too large')
        def part(start):
            end=min(start+399999,total-1)
            local=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            data=b''
            for attempt in range(3):
                cursor=start+len(data)
                request=urllib.request.Request(url,headers={'Accept':'application/vnd.github.raw+json','Range':f'bytes={cursor}-{end}'})
                with local.open(request,timeout=30) as r:
                    if r.status!=206 or not r.headers.get('Content-Range','').startswith(f'bytes {cursor}-{end}/'):raise ValueError('Range not honored')
                    try:piece=r.read(end-cursor+1)
                    except http.client.IncompleteRead as exc:piece=exc.partial
                data+=piece
                if len(data)==end-start+1:break
            if len(data)!=end-start+1:raise ValueError('Incomplete range')
            print(f'Downloaded {start}-{end}',flush=True)
            return data
        with ThreadPoolExecutor(max_workers=3) as pool:raw=b''.join(pool.map(part,range(0,total,400000)))
        if hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()!=blob:raise ValueError('Git blob checksum mismatch')
    else:
        with opener.open(url,timeout=30) as r:raw=r.read(30_000_001)
    if len(raw)>30_000_000:raise ValueError('Download size limit exceeded')
    report=build(raw,revision,args.output,source)
    print(json.dumps({k:report[k] for k in ['input_rows','kept','duplicates','quarantined','splits','revision']}))

if __name__=='__main__':main()
