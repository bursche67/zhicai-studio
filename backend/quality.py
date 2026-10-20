"""Read-only data quality diagnostics. Completeness never substitutes for accuracy."""
from decimal import Decimal
import json
import os
import shutil
from openpyxl import load_workbook
from .db import connection
from .models import Extraction
from .export import latest_documents, standard_rows, TEMPLATE

FIELDS = {'name':'产品服务名称','category':'品目','brand':'品牌','specification':'规格型号','unit_price':'单价','quantity':'数量','total_price':'总价'}


def capabilities():
    return {'html': True, 'pdf_text': True, 'docx': True, 'xlsx': True,
            'doc_conversion': bool(shutil.which(os.getenv('SOFFICE_PATH') or 'soffice')),
            'ocr_executable': bool(shutil.which(os.getenv('TESSERACT_CMD') or 'tesseract')),
            'note': '工具存在不代表真实附件效果已验收；OCR仍需chi_sim语言包。'}


def quality_report(mode, sample=False, job_id=None):
    docs = latest_documents(mode, sample, job_id)
    items = [i for d in docs for p in Extraction.model_validate_json(d['extraction']).packages for i in p.items]
    fills = {key: {'label': label, 'non_null': sum(getattr(i,key) is not None and getattr(i,key) != '' for i in items), 'total': len(items)} for key,label in FIELDS.items()}
    for row in fills.values():
        row['rate'] = row['non_null']/row['total'] if row['total'] else None
    tables, warnings = standard_rows(mode, sample, job_id)
    template = load_workbook(TEMPLATE, read_only=True)
    blue = {s.title: [(c.column-1,c.value) for c in s[1] if c.fill.fgColor.type=='rgb' and c.fill.fgColor.rgb=='FF1F4E79'] for s in template}
    template.close()
    missing = {name: {label: sum(row[col] in (None,'') for row in tables[name]) for col,label in columns} for name,columns in blue.items()}
    invalid_quantities = [{'uuid':row[1],'package':row[2],'name':row[3],'quantity':row[7]} for row in tables['标的表'] if row[7] is not None and (Decimal(str(row[7])) <= 0 or Decimal(str(row[7])) != Decimal(str(row[7])).to_integral_value())]
    with connection() as db:
        source_count = db.execute('SELECT count(DISTINCT d.source_uuid) FROM documents d JOIN jobs j ON j.id=d.job_id WHERE j.mode=? AND d.is_sample=?'+(' AND j.id=?' if job_id else ''), [mode,int(sample)]+([job_id] if job_id else [])).fetchone()[0]
        job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone() if job_id else None
        status_counts = dict(db.execute('SELECT d.status,count(*) FROM documents d JOIN jobs j ON j.id=d.job_id WHERE j.mode=? AND d.is_sample=?'+(' AND j.id=?' if job_id else '')+' GROUP BY d.status', [mode,int(sample)]+([job_id] if job_id else [])).fetchall())
    performance = None
    if job:
        manifest=json.loads(job['manifest'])
        reasons = []
        if job['status'] != 'completed': reasons.append('未完成全部公告处理，不能作为完整批次速率')
        if status_counts.get('duplicate'): reasons.append('包含复用结果，不能作为从头抽取的正式速率')
        if any(status_counts.get(s) for s in ('failed','needs_attention','partial')): reasons.append('含失败或附件未完成记录')
        if manifest.get('attempt_number') != 1 or not manifest.get('import_completed_at') or manifest.get('first_attempt_unknown'):
            reasons.append('包含重试或缺少完整尝试历史，不能作为从导入开始的单次完整抽取速率')
        denominator=manifest.get('notice_count',source_count)
        performance = {'elapsed_seconds':job['elapsed_seconds'],'notice_count':denominator,'seconds_per_notice':job['elapsed_seconds']/denominator if denominator and job['elapsed_seconds'] is not None else None,'measurement_valid':not reasons,'attempt_number':manifest.get('attempt_number'),'import_completed_at':manifest.get('import_completed_at'),'reference_eligible':False,'limitations':reasons+['本机或自部署环境观测；性能取决于数据规模和运行环境'],'definition':'导入完成至全部解析、抽取与入库（含附件）的总耗时 / 公告总篇数'}
    return {'mode':mode,'sample':sample,'job_id':job_id,'source_notices_seen':source_count,'notices_with_results':len(docs),'item_count':len(items),'seven_fields':fills,'table_row_counts':{k:len(v) for k,v in tables.items()},'blue_field_missing_counts':missing,'nonconforming_quantity_rows':invalid_quantities,'missing_url_notices':sum(not d['source_url'] for d in docs),'stored_document_status_counts':status_counts,'warnings':list(dict.fromkeys(warnings)),'performance':performance,'accuracy':None,'precision':None,'recall':None,'composite':None,'note':'字段非空率和蓝色列缺失检查不是精度；原文未披露仍留空。准确性需独立人工核验金标。历史状态条数不等于去重公告数。'}
