"""Extraction wire schema. Amounts are CNY, quantities retain decimal precision."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

def money(value):
    if value is None or value == '' or value in ('未公布', '未知', '-', '不详'):
        return None
    if isinstance(value, bool):
        raise ValueError('金额不能为布尔值')
    s = str(value).strip().replace(',', '').replace('，', '')
    if any(x in s.upper() for x in ('USD', 'EUR', '$', '美元', '欧元')):
        raise ValueError('仅接受人民币金额')
    factor = Decimal('100000000') if '亿' in s else Decimal('10000') if '万' in s else Decimal(1)
    s = re.sub(r'人民币|亿元|万元|元|亿|万|¥|￥|[（()）]|\s', '', s)
    try:
        result = Decimal(s) * factor
    except InvalidOperation as exc:
        raise ValueError('无法规范化金额') from exc
    if not result.is_finite() or result < 0 or result > Decimal('90000000000000000'):
        raise ValueError('金额必须是SQLite可保存的非负有限数')
    return result.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)

def cents(value):
    v = money(value)
    return None if v is None else int(v * 100)

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Evidence(StrictModel):
    source: str
    locator: str
    quote: str
    field: str | None = None

class Item(StrictModel):
    name: str | None = None
    category: str | None = None
    brand: str | None = None
    product_supplier: str | None = None
    specification: str | None = None
    unit_price: Decimal | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    total_price: Decimal | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    _money = field_validator('unit_price', 'total_price', mode='before')(money)

    @field_validator('quantity')
    @classmethod
    def quantity_valid(cls, v):
        if v is not None and (not v.is_finite() or v < 0):
            raise ValueError('数量必须是非负有限数')
        return v

class Bid(StrictModel):
    supplier: str
    result: Literal['won', 'lost', 'unknown', 'invalid', 'cancelled'] = 'unknown'
    award_amount: Decimal | None = None
    review_status: str | None = None
    bid_price: Decimal | None = None
    score: Decimal | None = None
    loss_reason: str | None = None
    consortium: str | None = None
    consortium_score: Decimal | None = None
    consortium_award_amount: Decimal | None = None
    consortium_review_status: str | None = None
    consortium_bid_price: Decimal | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    _money = field_validator('award_amount', 'bid_price', 'consortium_award_amount', 'consortium_bid_price', mode='before')(money)

    @field_validator('review_status', mode='before')
    @classmethod
    def normalize_review(cls, value):
        return {'是': '通过', '否': '未通过', '合格': '通过', '不合格': '未通过'}.get(value, value)

    @model_validator(mode='after')
    def award_only_for_winner(self):
        if self.result != 'won' and self.award_amount is not None:
            raise ValueError('未中标主体不能具有中标金额')
        if not self.supplier.strip():
            raise ValueError('供应商名称为空')
        return self

class Package(StrictModel):
    code: str | None = None
    name: str | None = None
    award_amount: Decimal | None = None
    bids: list[Bid] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    _money = field_validator('award_amount', mode='before')(money)

class Extraction(StrictModel):
    project_name: str | None = None
    project_number: str | None = None
    buyer: str | None = None
    project_amount: Decimal | None = None
    award_amount: Decimal | None = None
    packages: list[Package] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    _money = field_validator('project_amount', 'award_amount', mode='before')(money)

class SupplierSelection(StrictModel):
    suppliers: list[str] = Field(min_length=2, max_length=20)

    @field_validator('suppliers')
    @classmethod
    def distinct(cls, values):
        values = [v.strip() for v in values]
        if any(not v for v in values) or len(set(values)) != len(values):
            raise ValueError('至少选择两个不同的非空供应商')
        return values


class SourceURL(StrictModel):
    uuid: str = Field(min_length=1, max_length=200, pattern=r'^[^/\\\s]+$')
    url: str = Field(min_length=1, max_length=2048)

    @field_validator('url')
    @classmethod
    def public_url(cls, value):
        from urllib.parse import urlsplit
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or any(c.isspace() for c in value):
            raise ValueError('来源必须是无账号密码的HTTP(S)地址；只保存映射，不访问该网址')
        return value


class SourceURLMapping(StrictModel):
    entries: list[SourceURL] = Field(min_length=1, max_length=5000)
    overwrite: bool = False

    @field_validator('entries')
    @classmethod
    def unique_uuids(cls, values):
        if len({v.uuid for v in values}) != len(values):
            raise ValueError('同一映射不能重复填写UUID')
        return values
