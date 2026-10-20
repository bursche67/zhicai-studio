import pytest
from scripts.evaluate import evaluate

def test_one_to_one_matching_and_mismatches():
    a={'uuid':'u','package':'1','name':'商品','quantity':'2','unit_price':'1.00'}
    b={**a,'quantity':'3'}
    gold={'reviewed':True,'dataset_sha256':'same','items':[a,b]}
    pred={'dataset_sha256':'same','items':[b,a,a]}
    r=evaluate(gold,pred)
    assert r['fields']['quantity']['tp']==2
    assert r['fields']['quantity']['fp']==1
    assert r['fields']['quantity']['recall']==1
    assert r['fields']['quantity']['precision']==pytest.approx(2/3)
    pred['items']=[{**a,'name':'错名'}]
    r=evaluate(gold,pred)
    assert r['fields']['name']['tp']==0 and r['fields']['name']['fn']==2

def test_gold_requires_review_and_matching_dataset():
    with pytest.raises(ValueError):evaluate({'reviewed':False},{})
    with pytest.raises(ValueError):evaluate({'reviewed':True,'dataset_sha256':'a'},{'dataset_sha256':'b'})


def test_evaluation_subset_is_same_and_items_cannot_escape():
    gold={'reviewed':True,'dataset_sha256':'a','evaluation_uuids':['u'],'items':[]}
    pred={'dataset_sha256':'a','evaluation_uuids':['v'],'items':[]}
    with pytest.raises(ValueError, match='集合'): evaluate(gold,pred)
    pred.update(evaluation_uuids=['u'],items=[{'uuid':'outside'}])
    with pytest.raises(ValueError, match='范围'): evaluate(gold,pred)
