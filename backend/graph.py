"""Replaceable graph adapter. SQLite is authoritative; no Neo4j claim is made."""
from typing import Protocol
from .db import connection, norm
from .analysis import query

class GraphAdapter(Protocol):
    def get_graph(self, mode: str, sample: bool, project_id: str | None, organization: str | None, limit: int) -> dict: ...

class SQLiteGraphAdapter:
    def get_graph(self, mode='model', sample=False, project_id=None, organization=None, limit=200):
        with connection() as db:
            filters, args = ['p.mode=?', 'p.is_sample=?'], [mode, int(sample)]
            if project_id:
                filters.append('p.id=?')
                args.append(project_id)
            if organization:
                filters.append('''(p.buyer_id IN (SELECT id FROM organizations WHERE name=?) OR p.id IN
                (SELECT project_id FROM bids WHERE supplier_id IN (SELECT id FROM organizations WHERE name=?)) OR p.id IN
                (SELECT project_id FROM items WHERE product_supplier_id IN (SELECT id FROM organizations WHERE name=?)))''')
                args.extend([norm(organization)] * 3)
            projects = query(db, 'SELECT p.* FROM projects p WHERE ' + ' AND '.join(filters) + ' ORDER BY p.id LIMIT ?', args + [limit + 1])
            nodes, links, truncated = {}, {}, len(projects) > limit
            def node(id, name, category):
                nodes.setdefault(id, {'id': id, 'name': name or '未命名', 'category': category})
            def edge(a, b, relation):
                links[a, b, relation] = {'source': a, 'target': b, 'relation': relation}
            for p in projects[:limit]:
                pid = 'p:' + p['id']
                node(pid, p['name'], '项目')
                if p['buyer_id']:
                    buyer = db.execute('SELECT name FROM organizations WHERE id=?', (p['buyer_id'],)).fetchone()[0]
                    oid = 'o:' + str(p['buyer_id'])
                    node(oid, buyer, '主体')
                    edge(oid, pid, '采购')
                for package in query(db, 'SELECT id,code,name FROM packages WHERE project_id=? ORDER BY code,id', (p['id'],)):
                    kid = 'k:' + package['id']
                    node(kid, package['name'] or package['code'] or '采购包（名称未知）', '采购包')
                    edge(pid, kid, '包含采购包')
                for bid in query(db, 'SELECT b.*,o.name FROM bids b JOIN organizations o ON o.id=b.supplier_id WHERE project_id=?', (p['id'],)):
                    oid = 'o:' + str(bid['supplier_id'])
                    node(oid, bid['name'], '主体')
                    edge(oid, 'k:' + bid['package_id'], '中标' if bid['result'] == 'won' else '投标')
                for item in query(db, 'SELECT i.*,o.name supplier FROM items i LEFT JOIN organizations o ON o.id=i.product_supplier_id WHERE project_id=?', (p['id'],)):
                    iid = 'i:' + item['id']
                    node(iid, item['name'], '商品')
                    edge('k:' + item['package_id'], iid, '采购标的')
                    if item['product_supplier_id']:
                        oid = 'o:' + str(item['product_supplier_id'])
                        node(oid, item['supplier'], '主体')
                        edge(oid, iid, '产品供应')
            # Hard node cap, preserve edge referential integrity.
            selected = list(nodes.values())[:limit]
            ids = {n['id'] for n in selected}
            return {'nodes': selected, 'links': [e for e in links.values() if e['source'] in ids and e['target'] in ids], 'truncated': truncated or len(nodes) > limit, 'backend': 'sqlite'}

graph_adapter: GraphAdapter = SQLiteGraphAdapter()
