"""Generate explicitly synthetic notices and import into the isolated sample partition."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.db import init_db
from backend.jobs import create_job, process_job

ROOT=Path(__file__).resolve().parents[1]
def main():
    folder=ROOT/'samples';folder.mkdir(exist_ok=True)
    inputs=[]
    for i,(buyer,winner,loser) in enumerate([
        ('自建样例采购单位一','自建样例甲公司','自建样例乙公司'),
        ('自建样例采购单位一','自建样例乙公司','自建样例甲公司'),
        ('自建样例采购单位二','自建样例甲公司；自建样例乙公司','自建样例丙公司'),
    ],1):
        name=f'sample-{i:03d}.html'
        content=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>自建测试样例 非官方数据 {i}</title><body><h1>自建测试样例 非官方数据</h1>
        <p>项目名称：自建样例设备采购项目{i}</p><p>项目编号：SAMPLE-{i}</p><p>采购单位：{buyer}</p><p>项目金额：2万元</p><p>中标金额：1万元</p>
        <p>中标供应商：{winner}</p><p>投标参与方：{loser}</p><p>采购包：未分包</p>
        <table><tr><th>产品服务名称</th><th>品目</th><th>品牌</th><th>产品供应商</th><th>规格型号</th><th>单价</th><th>数量</th><th>总价</th></tr><tr><td>自建样例服务器</td><td>信息化设备</td><td>自建样例品牌</td><td>自建样例厂商</td><td>SAMPLE-100</td><td>5000元</td><td>2台</td><td>10000元</td></tr></table></body></html>'''.encode()
        (folder/name).write_bytes(content);inputs.append((name,content))
    init_db();jid=create_job(inputs,'rule',True);process_job(jid);print('Synthetic demo job:',jid)

if __name__=='__main__':main()
