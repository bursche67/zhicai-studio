"""Local reproducible seven-field evaluation; no claim of official matching rules.

Gold JSON: {"reviewed":true,"dataset_sha256":"...","items":[
 {"uuid":"...","package":"...","name":"...","category":null,"brand":null,
 "specification":null,"quantity":"1","unit_price":"10.00","total_price":"10.00"}]}
Amounts are CNY. Matching requires exact normalized UUID/package/name, then
maximum-weight one-to-one matching within duplicate-name groups.
"""
import argparse
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

FIELDS=('name','category','brand','specification','quantity','unit_price','total_price')

def normalize(field,v):
    if v is None or v=='':return None
    if field in ('quantity','unit_price','total_price'):
        return str(Decimal(str(v)).normalize())
    return ' '.join(str(v).split())

def assignment(weights):
    """Hungarian minimum-cost assignment, padded with dummy unmatched rows."""
    n=len(weights)
    if not n:return []
    u=[0]*(n+1);v=[0]*(n+1);p=[0]*(n+1);way=[0]*(n+1)
    for i in range(1,n+1):
        p[0]=i;j0=0;minimum=[float('inf')]*(n+1);used=[False]*(n+1)
        while True:
            used[j0]=True;i0=p[j0];delta=float('inf');j1=0
            for j in range(1,n+1):
                if not used[j]:
                    cost=-weights[i0-1][j-1]-u[i0]-v[j]
                    if cost<minimum[j]:minimum[j]=cost;way[j]=j0
                    if minimum[j]<delta:delta=minimum[j];j1=j
            for j in range(n+1):
                if used[j]:u[p[j]]+=delta;v[j]-=delta
                else:minimum[j]-=delta
            j0=j1
            if p[j0]==0:break
        while True:
            j1=way[j0];p[j0]=p[j1];j0=j1
            if j0==0:break
    return [(p[j]-1,j-1) for j in range(1,n+1)]

def evaluate(gold,pred):
    if gold.get('reviewed') is not True:
        raise ValueError('金标尚未人工核验：reviewed必须为true')
    if gold.get('dataset_sha256') != pred.get('dataset_sha256') or not gold.get('dataset_sha256'):
        raise ValueError('金标与预测数据集哈希必须一致且非空')
    if gold.get('evaluation_uuids') is not None:
        scope = set(gold['evaluation_uuids'])
        if not scope or scope != set(pred.get('evaluation_uuids', [])):
            raise ValueError('金标与预测的评测公告集合必须一致且非空')
        if any(item.get('uuid') not in scope for item in gold['items'] + pred['items']):
            raise ValueError('记录超出选定的评测公告范围')
    groups=defaultdict(lambda:[[],[]])
    for side,items in enumerate([gold['items'],pred['items']]):
        for item in items:
            key=tuple(normalize(k,item.get(k)) for k in ('uuid','package','name'))
            groups[key][side].append(item)
    counts={f:{'tp':0,'fp':0,'fn':0,'correct_slots':0,'slots':0} for f in FIELDS}
    for gs,ps in groups.values():
        n=max(len(gs),len(ps));weight=[]
        for i in range(n):
            weight.append([sum(normalize(f,gs[i].get(f))==normalize(f,ps[j].get(f)) for f in FIELDS) if i<len(gs) and j<len(ps) else 0 for j in range(n)])
        for i,j in assignment(weight):
            g=gs[i] if i<len(gs) else {};p=ps[j] if j<len(ps) else {}
            matched=i<len(gs) and j<len(ps)
            for f in FIELDS:
                gv,pv=normalize(f,g.get(f)),normalize(f,p.get(f));c=counts[f];c['slots']+=1
                if matched and gv==pv:c['correct_slots']+=1
                if gv is not None and gv==pv:c['tp']+=1
                else:
                    if pv is not None:c['fp']+=1
                    if gv is not None:c['fn']+=1
    def metrics(c):
        precision=c['tp']/(c['tp']+c['fp']) if c['tp']+c['fp'] else 0
        recall=c['tp']/(c['tp']+c['fn']) if c['tp']+c['fn'] else 0
        accuracy=c['correct_slots']/c['slots'] if c['slots'] else 0
        return {**c,'precision':precision,'recall':recall,'accuracy':accuracy,'composite':.4*accuracy+.3*precision+.3*recall}
    total={k:sum(c[k] for c in counts.values()) for k in ('tp','fp','fn','correct_slots','slots')}
    return {'method':'本地口径：UUID/包/名称精确匹配，同名商品最大权重一对一匹配；准确率为含空值字段槽位一致率，未匹配行计错','dataset_sha256':gold['dataset_sha256'],'fields':{f:metrics(c) for f,c in counts.items()},'overall':metrics(total)}
