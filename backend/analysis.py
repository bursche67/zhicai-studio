"""Five scenarios. Project-level sets prevent join multiplicity inflating counts."""
from .db import connection, norm

def query(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]

def amount_summary(rows, field='project_amount'):
    values = [r[field] for r in rows if r.get(field) is not None]
    return {'known_amount_cents': sum(values) if values else None, 'missing_amount_count': sum(r.get(field) is None for r in rows)}

def project_filter(mode, sample):
    return 'p.mode=? AND p.is_sample=?', [mode, int(sample)]

def cooperation(buyer, mode='model', sample=False):
    where, args = project_filter(mode, sample)
    with connection() as db:
        winners = query(db, f'''WITH awards AS (
          SELECT b.project_id,b.supplier_id,SUM(b.award_amount) amount,
          SUM(CASE WHEN b.award_amount IS NULL THEN 1 ELSE 0 END) missing
          FROM bids b JOIN projects p ON p.id=b.project_id JOIN organizations o ON o.id=p.buyer_id
          WHERE b.result='won' AND o.name=? AND {where} GROUP BY b.project_id,b.supplier_id)
          SELECT o.name supplier,COUNT(*) cooperation_count,SUM(a.amount) known_amount_cents,
          SUM(a.missing) missing_amount_count FROM awards a JOIN organizations o ON o.id=a.supplier_id
          GROUP BY a.supplier_id ORDER BY cooperation_count DESC,supplier''', [norm(buyer)] + args)
        products = query(db, f'''SELECT s.name supplier,COUNT(DISTINCT i.project_id) cooperation_count,
          SUM(i.total_price) known_amount_cents,SUM(CASE WHEN i.total_price IS NULL THEN 1 ELSE 0 END) missing_amount_count
          FROM items i JOIN projects p ON p.id=i.project_id JOIN organizations o ON o.id=p.buyer_id
          JOIN organizations s ON s.id=i.product_supplier_id WHERE o.name=? AND {where}
          GROUP BY s.id ORDER BY cooperation_count DESC,supplier''', [norm(buyer)] + args)
    return {'winners': winners, 'product_suppliers': products, 'amount_basis': '中标供应商已明确分配的成交额；产品供应商对应商品总价，单位分'}

def buyer_bidders(buyer, mode='model', sample=False):
    where, args = project_filter(mode, sample)
    with connection() as db:
        cte = f'''WITH participants AS (SELECT DISTINCT b.project_id,b.supplier_id FROM bids b
        JOIN projects p ON p.id=b.project_id JOIN organizations o ON o.id=p.buyer_id WHERE o.name=? AND {where})'''
        top = query(db, cte + ''' SELECT o.name supplier,COUNT(*) frequency FROM participants b
          JOIN organizations o ON o.id=b.supplier_id GROUP BY b.supplier_id ORDER BY frequency DESC,supplier LIMIT 5''', [norm(buyer)] + args)
        pairs = query(db, cte + ''' SELECT x.name supplier_a,y.name supplier_b,COUNT(*) frequency
          FROM participants a JOIN participants b ON a.project_id=b.project_id AND a.supplier_id<b.supplier_id
          JOIN organizations x ON x.id=a.supplier_id JOIN organizations y ON y.id=b.supplier_id
          GROUP BY a.supplier_id,b.supplier_id ORDER BY frequency DESC,supplier_a,supplier_b''', [norm(buyer)] + args)
    return {'top5': top, 'pairs': pairs, 'frequency_basis': '唯一项目；共同投标不代表联合体或违规串标'}

def supplier_competitors(supplier, mode='model', sample=False):
    where, args = project_filter(mode, sample)
    with connection() as db:
        rows = query(db, f'''WITH related AS (SELECT DISTINCT p.id,p.project_amount,p.award_amount FROM projects p
          JOIN bids b ON b.project_id=p.id JOIN organizations s ON s.id=b.supplier_id WHERE s.name=? AND {where}),
          co AS (SELECT DISTINCT b.project_id,b.supplier_id FROM bids b JOIN related r ON r.id=b.project_id)
          SELECT s.name supplier,COUNT(*) frequency,SUM(r.project_amount) known_amount_cents,
          SUM(CASE WHEN r.project_amount IS NULL THEN 1 ELSE 0 END) missing_amount_count,
          SUM(r.award_amount) known_award_amount_cents,SUM(CASE WHEN r.award_amount IS NULL THEN 1 ELSE 0 END) missing_award_amount_count
          FROM co JOIN related r ON r.id=co.project_id JOIN organizations s ON s.id=co.supplier_id
          WHERE s.name<>? GROUP BY s.id ORDER BY frequency DESC,supplier''', [norm(supplier)] + args + [norm(supplier)])
    return {'top5': rows[:5], 'all': rows, 'amount_basis': '按共同参与的唯一项目统计；表格已知金额为预算，项目总成交额为成交口径，均非该主体个人中标份额'}

def common_buyers(suppliers, mode='model', sample=False):
    suppliers = [norm(s) for s in suppliers]
    marks = ','.join('?' for _ in suppliers)
    where, args = project_filter(mode, sample)
    with connection() as db:
        rows = query(db, f'''SELECT p.id project_id,p.buyer_id,buyer.name buyer,s.name supplier,
          SUM(b.award_amount) known_amount_cents,SUM(CASE WHEN b.award_amount IS NULL THEN 1 ELSE 0 END) missing_amount_count
          FROM bids b JOIN projects p ON p.id=b.project_id JOIN organizations buyer ON buyer.id=p.buyer_id
          JOIN organizations s ON s.id=b.supplier_id WHERE b.result='won' AND s.name IN ({marks}) AND {where}
          GROUP BY p.id,b.supplier_id''', suppliers + args)
        buyers = {}
        for row in rows:
            buyers.setdefault(row['buyer'], []).append(row)
        result = []
        for buyer, records in buyers.items():
            if set(r['supplier'] for r in records) != set(suppliers):
                continue
            per_supplier = []
            for supplier in suppliers:
                selected = [r for r in records if r['supplier'] == supplier]
                amounts = [r['known_amount_cents'] for r in selected if r['known_amount_cents'] is not None]
                per_supplier.append({'supplier': supplier, 'cooperation_count': len(selected), 'known_amount_cents': sum(amounts) if amounts else None, 'missing_amount_count': sum(r['missing_amount_count'] for r in selected)})
            project_ids = sorted(set(r['project_id'] for r in records))
            pmarks = ','.join('?' for _ in project_ids)
            projects = query(db, f'SELECT award_amount FROM projects WHERE id IN ({pmarks})', project_ids)
            result.append({'buyer': buyer, 'suppliers': per_supplier, 'project_count': len(project_ids), **amount_summary(projects, 'award_amount')})
    return {'buyers': sorted(result, key=lambda r: (-r['project_count'], r['buyer'])), 'amount_basis': '各主体明确分配中标额；采购单位汇总为唯一项目总成交额，不累加供应商小计'}

def common_projects(suppliers, mode='model', sample=False):
    suppliers = [norm(s) for s in suppliers]
    marks = ','.join('?' for _ in suppliers)
    where, args = project_filter(mode, sample)
    with connection() as db:
        projects = query(db, f'''SELECT p.*,buyer.name buyer FROM projects p LEFT JOIN organizations buyer ON buyer.id=p.buyer_id
          WHERE {where} AND p.id IN (SELECT b.project_id FROM bids b JOIN organizations s ON s.id=b.supplier_id
          WHERE s.name IN ({marks}) GROUP BY b.project_id HAVING COUNT(DISTINCT s.name)=?) ORDER BY p.number,p.id''', args + suppliers + [len(suppliers)])
        for p in projects:
            p['results'] = query(db, f'''SELECT s.name supplier,COALESCE(k.name,k.code) package_code,b.result,b.award_amount FROM bids b
              JOIN organizations s ON s.id=b.supplier_id JOIN packages k ON k.id=b.package_id
              WHERE b.project_id=? AND s.name IN ({marks}) ORDER BY k.code,s.name''', [p['id']] + suppliers)
    awards=amount_summary(projects,'award_amount')
    return {'projects': projects, 'project_count': len(projects), **amount_summary(projects), 'known_award_amount_cents':awards['known_amount_cents'],'missing_award_amount_count':awards['missing_amount_count'], 'amount_basis': '按唯一项目分别统计预算与项目总成交额；未知不按零，跨包参与的结果分别展示'}
