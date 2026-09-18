"""Read-only synthetic operational snapshots, isolated from executable orders."""
import argparse
import csv
import hashlib
import io
import json
import math
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS=('ticket_id','scenario','customer_id','order_id','sku','amount','created_at','status','resolution',
        'auto_closed','human_takeover','handling_seconds','receipt_verified','error_flag','error_type')
BOOLS=('auto_closed','human_takeover','receipt_verified','error_flag')

def parse(raw):
    if len(raw)>2_000_000:raise ValueError('Source exceeds 2 MB')
    reader=csv.reader(io.StringIO(raw.decode('utf-8-sig')),strict=True)
    rows=[];rejected=[];duplicates=[];seen={};total=0
    for line,values in enumerate(reader,1):
        if not values or not any(x.strip() for x in values):continue
        if line==1 and tuple(values)==FIELDS:continue
        total+=1
        try:
            if len(values)!=15:raise ValueError('Expected 15 columns')
            row=dict(zip(FIELDS,(x.strip() for x in values)))
            if any(not row[k] or len(row[k])>100 for k in FIELDS[:5]):raise ValueError('Missing or oversized identifier')
            if any(len(value)>200 for value in values):raise ValueError('Oversized field')
            for field in BOOLS:
                if row[field] not in {'true','false'}:raise ValueError('Invalid boolean: '+field)
                row[field]=row[field]=='true'
            money=Decimal(row['amount'])
            if not money.is_finite() or money<0 or money*100!=(money*100).to_integral_value():raise ValueError('Invalid two-decimal amount')
            row['amount_minor']=int(money*100)
            if not row['handling_seconds'].isdigit():raise ValueError('Handling time must be nonnegative integer seconds')
            row['handling_seconds']=int(row['handling_seconds'])
            datetime.strptime(row['created_at'],'%Y-%m-%d %H:%M:%S')
            warnings=[]
            if row['auto_closed'] and row['human_takeover']:warnings.append('auto_closed_and_human_takeover')
            if row['auto_closed'] and row['status']!='closed':warnings.append('auto_flag_but_not_closed')
            if row['error_flag']!=bool(row['error_type']):warnings.append('error_flag_label_mismatch')
            if row['status']=='closed' and not row['receipt_verified']:warnings.append('closed_without_verified_receipt')
            row['warnings']=warnings
        except (ValueError,InvalidOperation) as exc:
            rejected.append(dict(line=line,reason=str(exc),raw=values));continue
        old=seen.get(row['ticket_id'])
        if old:
            if old!=row:raise ValueError('Conflicting duplicate ticket ID; import aborted: '+row['ticket_id'])
            duplicates.append(dict(line=line,ticket_id=row['ticket_id']));continue
        seen[row['ticket_id']]=row.copy();row=dict(row,source_line=line,synthetic=True);rows.append(row)
    return dict(input_rows=total,rows=rows,rejected=rejected,duplicates=duplicates)

def stats(rows):
    n=len(rows)
    auto=[r for r in rows if r['auto_closed']]
    manual=[r for r in rows if r['human_takeover']]
    closed=[r for r in rows if r['status']=='closed']
    clean=[r for r in auto if r['status']=='closed' and not r['human_takeover'] and r['receipt_verified'] and not r['error_flag']]
    errors=[r for r in rows if r['error_flag']]
    def ratio(a,b):return a/b if b else None
    def mean(group):return sum(r['handling_seconds'] for r in group)/len(group) if group else None
    times=sorted(r['handling_seconds'] for r in rows)
    return dict(total=n,auto_flagged=len(auto),human_takeover=len(manual),closed=len(closed),error_count=len(errors),
                auto_flag_rate=ratio(len(auto),n),human_takeover_rate=ratio(len(manual),n),error_rate=ratio(len(errors),n),
                receipt_verified=sum(r['receipt_verified'] for r in rows),
                receipt_verified_rate=ratio(sum(r['receipt_verified'] for r in rows),n),
                closed_receipt_verified_rate=ratio(sum(r['receipt_verified'] for r in closed),len(closed)),
                clean_auto_proxy=len(clean),clean_auto_proxy_rate=ratio(len(clean),n),
                mean_seconds=mean(rows),p50_seconds=times[math.ceil(n*.5)-1] if n else None,p95_seconds=times[math.ceil(n*.95)-1] if n else None,
                auto_only_mean_seconds=mean([r for r in auto if not r['human_takeover']]),
                human_only_mean_seconds=mean([r for r in manual if not r['auto_closed']]),
                warning_rows=sum(bool(r['warnings']) for r in rows))

class Operations:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS ops_datasets(id TEXT PRIMARY KEY,metadata TEXT,raw BLOB);
            CREATE TABLE IF NOT EXISTS ops_rows(dataset_id TEXT,ticket_id TEXT,body TEXT,PRIMARY KEY(dataset_id,ticket_id));''')

    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10)
        try:
            with c:yield c
        finally:c.close()

    def import_file(self,path):
        path=Path(path);raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest();parsed=parse(raw)
        if not parsed['rows']:raise ValueError('No valid rows')
        metadata=dict(id=sha,source_name=path.name,sha256=sha,imported_at=datetime.now(timezone.utc).isoformat(),
                      synthetic=True,schema_version='ops-synthetic-v1',mapping='Columns 10-14 confirmed by user; 1-9 and 15 inferred from content; timestamp timezone/currency unspecified',
                      input_rows=parsed['input_rows'],accepted=len(parsed['rows']),rejected=parsed['rejected'],duplicates=parsed['duplicates'])
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute('SELECT metadata FROM ops_datasets WHERE id=?',(sha,)).fetchone()
            if prior:return json.loads(prior[0])
            c.execute('INSERT INTO ops_datasets VALUES(?,?,?)',(sha,json.dumps(metadata,ensure_ascii=False),raw))
            c.executemany('INSERT INTO ops_rows VALUES(?,?,?)',[(sha,r['ticket_id'],json.dumps(r,ensure_ascii=False)) for r in parsed['rows']])
        return metadata

    def datasets(self):
        with self.connect() as c:return [json.loads(r[0]) for r in c.execute('SELECT metadata FROM ops_datasets ORDER BY rowid DESC')]

    def report(self,dataset_id=None):
        sources=self.datasets()
        if not sources:return dict(dataset=None,datasets=[],rows=[],metrics=stats([]),scenarios=[],error_types={},warnings=['尚未导入模拟运营数据'])
        selected=next((r for r in sources if r['id']==(dataset_id or sources[0]['id'])),None)
        if not selected:raise KeyError(dataset_id)
        with self.connect() as c:rows=[json.loads(r[0]) for r in c.execute('SELECT body FROM ops_rows WHERE dataset_id=? ORDER BY rowid',(selected['id'],))]
        labels=sorted({r['scenario'] for r in rows})
        return dict(dataset=selected,datasets=sources,rows=rows,metrics=stats(rows),
                    scenarios=[dict(scenario=s,**stats([r for r in rows if r['scenario']==s])) for s in labels],
                    error_types=dict(Counter(r['error_type'] or 'unlabeled' for r in rows if r['error_flag'])),
                    warnings=['全部记录为用户提供的模拟运营数据，不是本系统实际运行日志。',
                              '仅统计本次文件，未补造编号缺失的记录。',
                              '无错误标记不等于独立验证正确；不能据此计算真实降本或模型准确率。',
                              '时长为处理时长秒数，是否包含等待尚未定义；不同场景不能作为因果提效对照。'])

    def register_errors(self,quality,dataset_id):
        report=self.report(dataset_id);links=[]
        for row in report['rows']:
            if not row['error_flag']:continue
            key='operations:'+dataset_id+':'+row['ticket_id']
            with quality.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                old=c.execute('SELECT case_id FROM quality_sources WHERE source_key=?',(key,)).fetchone()
                if old:case_id=old[0]
                else:
                    case_id=hashlib.sha256(key.encode()).hexdigest()
                    case=dict(id=case_id,title='模拟运营待复核 · '+(row['error_type'] or 'unlabeled'),
                              expected='核查客户授权、业务回执和事件顺序；仅凭运营标签不判定根因',
                              actual='导入记录标记 error_flag=true；不是当前代码已复现缺陷',
                              evidence=f"dataset:{dataset_id}; line:{row['source_line']}; ticket:{row['ticket_id']}; error_type:{row['error_type']}",
                              source='synthetic',state='NEW',revision=0,
                              suggested_priority='P0' if row['error_type'] in {'wrong_refund','duplicate_execution'} else 'P1',
                              priority_note='建议优先复核，不自动定责或宣称修复')
                    c.execute('INSERT INTO quality_cases VALUES(?,?)',(case_id,json.dumps(case,ensure_ascii=False)))
                    c.execute('INSERT INTO quality_sources VALUES(?,?)',(key,case_id))
                    quality.event(c,'case.collected',case)
            links.append(dict(ticket_id=row['ticket_id'],case_id=case_id))
        return dict(dataset_id=dataset_id,count=len(links),links=links)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--db',type=Path,required=True);parser.add_argument('--quality-db',type=Path)
    args=parser.parse_args();args.db.parent.mkdir(parents=True,exist_ok=True)
    ops=Operations(args.db);meta=ops.import_file(args.source);report=ops.report(meta['id'])
    output=dict(dataset=meta,metrics=report['metrics'],scenarios=report['scenarios'],error_types=report['error_types'])
    if args.quality_db:
        from quality import Quality
        output['quality']=ops.register_errors(Quality(args.quality_db),meta['id'])
    print(json.dumps(output,ensure_ascii=True,indent=2))

if __name__=='__main__':main()
