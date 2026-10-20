"""Read every official HTML; report parser coverage, not extraction accuracy."""
import argparse
import json
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.parsers import parse_document, sha
from backend.extraction import rule_extract

def main():
    p=argparse.ArgumentParser();p.add_argument('archive',type=Path);p.add_argument('--out',type=Path,default=Path('docs/results/dataset_audit.json'));a=p.parse_args()
    start=time.perf_counter();rows=[];hashes=Counter()
    with zipfile.ZipFile(a.archive) as z:
        for n in z.namelist():
            if not n.lower().endswith(('.html','.htm')):continue
            data=z.read(n);hashes[sha(data)]+=1
            parsed=parse_document(n,data)
            row={'uuid':Path(n).stem,'sha256':sha(data),'blocks':len(parsed.blocks),'tables':len(parsed.tables),'characters':sum(len(b.text) for b in parsed.blocks),'parse_errors':parsed.errors}
            try:
                out=rule_extract(parsed)
                row['rule_item_count']=sum(len(p.items) for p in out.packages)
                row['rule_warnings']=len(out.warnings)
            except Exception as e:
                row['rule_error']=str(e)
            rows.append(row)
    report={'dataset':a.archive.name,'dataset_sha256':sha(a.archive.read_bytes()),'notice_count':len(rows),'duplicate_content_count':sum(v-1 for v in hashes.values()),'parsed_with_text':sum(r['characters']>0 for r in rows),'parse_error_notices':sum(bool(r['parse_errors']) for r in rows),'rule_error_notices':sum('rule_error' in r for r in rows),'rule_items':sum(r.get('rule_item_count',0) for r in rows),'elapsed_seconds':time.perf_counter()-start,'accuracy':'未测量：没有人工核验金标','attachments_in_archive':False,'records':rows}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
