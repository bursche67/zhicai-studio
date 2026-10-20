"""CLI wrapper for the shared, offline seven-field evaluator."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.evaluation import FIELDS, normalize, assignment, evaluate

def main():
    a=argparse.ArgumentParser();a.add_argument('gold',type=Path);a.add_argument('predictions',type=Path);a.add_argument('--out',type=Path,default=Path('docs/results/evaluation.json'));args=a.parse_args()
    result=evaluate(json.loads(args.gold.read_text(encoding='utf-8')),json.loads(args.predictions.read_text(encoding='utf-8')))
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result['overall'],ensure_ascii=False,indent=2))
if __name__=='__main__':main()
