"""In-memory statement parsing and conservative, long-only FIFO accounting.

No broker login, network request, or persistence is performed. An exported
statement is evidence of cash flows, not necessarily a complete cost ledger.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import deque
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, Optional


MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_ZIP_BYTES = 32 * 1024 * 1024
MAX_ZIP_MEMBERS = 256
ZERO = Decimal("0")


class StatementError(ValueError):
    """The file cannot safely be interpreted as a supported statement."""


ALIASES = {
    "date": ("发生日期", "成交日期", "交易日期", "日期", "date", "tradedate", "transactiondate", "executiondate"),
    "time": ("发生时间", "成交时间", "交易时间", "时间", "time", "tradetime", "executiontime"),
    "symbol": ("证券代码", "股票代码", "代码", "symbol", "ticker", "stockcode", "securitycode"),
    "name": ("证券名称", "股票名称", "名称", "name", "securityname", "stockname"),
    "side": ("交易类别", "业务名称", "业务类别", "买卖方向", "买卖标志", "操作", "方向", "side", "action", "transactiontype", "type"),
    "quantity": ("成交数量", "发生数量", "交易数量", "数量", "quantity", "qty", "shares", "volume"),
    "price": ("成交均价", "成交价格", "交易价格", "价格", "price", "tradeprice", "executionprice"),
    "gross": ("成交金额", "交易金额", "grossamount", "tradeamount", "amount", "gross"),
    "net": ("发生金额", "净结算金额", "结算金额", "净发生金额", "netamount", "settlementamount", "netsettlementamount", "cashflow", "net"),
    "fees_total": ("手续费合计", "费用合计", "交易费用", "fees", "fee", "totalfees", "totalfee"),
    "commission": ("佣金", "手续费", "commission"),
    "stamp": ("印花税", "stampduty", "stamptax", "tax"),
    "transfer": ("过户费", "transferfee"),
    "levy": ("交易规费", "规费", "tradinglevy", "levy"),
    "handling": ("经手费", "交易费", "handlingfee", "tradingfee"),
    "other_fee": ("其他费用", "其他费", "otherfees", "otherfee"),
    "balance": ("股份余额", "证券余额", "持仓数量", "sharebalance", "holdingbalance", "positionbalance", "balancequantity"),
    "currency": ("币种", "货币", "currency", "settlementcurrency"),
    "market": ("交易市场", "市场", "market", "exchange"),
    "identity": ("成交编号", "成交号", "流水号", "流水编号", "序号", "成交序号", "tradeid", "executionid", "transactionid", "sequence", "serial"),
    "order": ("委托编号", "委托号", "合同编号", "orderid", "ordernumber"),
    "asset": ("证券类别", "品种类别", "资产类别", "assetclass", "securitytype"),
}
FEE_KEYS = ("commission", "stamp", "transfer", "levy", "handling", "other_fee")
MISSING = {"", "--", "-", "none", "null", "n/a", "nan"}


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip().lstrip("\ufeff")
    match = re.fullmatch(r'=\s*"(.*)"', text, re.S)
    return match.group(1).replace('""', '"').strip() if match else text


def _header(value: Any) -> str:
    return re.sub(r"[\s_\-（）()]+", "", _cell(value).lower())


def _columns(headers: list[str]) -> dict[str, int]:
    normalized = [_header(value) for value in headers]
    result = {}
    for key, aliases in ALIASES.items():
        for alias in aliases:
            if _header(alias) in normalized:
                result[key] = normalized.index(_header(alias))
                break
    return result


def _read_rows(content: bytes, filename: str) -> list[list[str]]:
    if not content:
        raise StatementError("文件为空。")
    if len(content) > MAX_FILE_BYTES:
        raise StatementError("交割单最多支持 2 MiB，请缩短导出日期范围。")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".csv", ".tsv", ".txt", ".xls", ".xlsx"}:
        raise StatementError("支持 CSV、TSV、券商文本 XLS 和 XLSX 文件。")
    if content.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise StatementError("暂不支持二进制 XLS，请用表格软件另存为 CSV 或 XLSX。")
    if suffix == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                members = archive.infolist()
                if len(members) > MAX_ZIP_MEMBERS or sum(item.file_size for item in members) > MAX_ZIP_BYTES:
                    raise StatementError("XLSX 解压后内容过大，请只保留交割单工作表。")
                for member in members:
                    if member.flag_bits & 1 or member.file_size > MAX_ZIP_BYTES:
                        raise StatementError("不支持加密或过大的 XLSX。")
                if "xl/workbook.xml" not in archive.namelist():
                    raise StatementError("文件不是有效的 XLSX 工作簿。")
            import openpyxl
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                if len(workbook.worksheets) != 1:
                    raise StatementError("请只保留一张交割单工作表，避免漏算其他工作表。")
                sheet = workbook.worksheets[0]
                if (sheet.max_row or 0) > MAX_ROWS + 20 or (sheet.max_column or 0) > 128:
                    raise StatementError("XLSX 行数或列数过多，请只保留最多 5000 条交割记录。")
                rows = []
                for row in sheet.iter_rows(values_only=True):
                    if len(row) > 128:
                        raise StatementError("表格列数过多，无法识别交割单。")
                    rows.append([_cell(value) for value in row])
                    if len(rows) > MAX_ROWS + 20:
                        raise StatementError("交割单最多支持 5000 条记录。")
                return rows
            finally:
                workbook.close()
        except StatementError:
            raise
        except Exception as exc:
            raise StatementError("无法读取 XLSX，请确认文件未损坏且已保存成交数值。") from exc
    if content.startswith(b"PK") or b"\x00" in content:
        raise StatementError("文件不是可读取的文本交割单，请另存为 CSV 或 XLSX。")
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise StatementError("无法识别文本编码，请另存为 UTF-8 CSV。")
    if re.search(r"<\s*(?:html|table|!doctype)\b", text, re.I):
        raise StatementError("暂不支持 HTML 格式 XLS，请另存为 CSV 或 XLSX。")
    delimiter = "\t" if "\t" in text[:16384] else ","
    try:
        # Excel's ="value" wrapper must be removed before CSV quote handling.
        text = re.sub(r'(?m)(^|[\t,])\s*=\s*(?=")', r"\1", text)
        rows = []
        for row in csv.reader(io.StringIO(text), delimiter=delimiter, strict=True):
            if len(row) > 128:
                raise StatementError("表格列数过多，无法识别交割单。")
            rows.append([_cell(value) for value in row])
            if len(rows) > MAX_ROWS + 20:
                raise StatementError("交割单最多支持 5000 条记录。")
        return rows
    except csv.Error as exc:
        raise StatementError("CSV/TSV 格式有误，请检查引号与列分隔符。") from exc


def _number(text: str, field: str, *, optional: bool = False, signed: bool = False) -> Optional[Decimal]:
    if optional and text.lower() in MISSING - {"nan"}:
        return None
    raw = text.strip().replace("，", ",")
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    if "," in raw:
        if not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
            raise ValueError(f"{field}不是有效数字")
        raw = raw.replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{field}不是有效数字") from exc
    if not value.is_finite() or abs(value) > Decimal("1e15") or (value and abs(value) < Decimal("1e-12")):
        raise ValueError(f"{field}必须是有限且合理的数值")
    if not signed and value < ZERO:
        raise ValueError(f"{field}不能为负数")
    return value


def _date_time(day: str, clock: str) -> tuple[str, str]:
    day = day.strip()
    embedded_time = ""
    if "T" in day or " " in day:
        parts = re.split(r"[T ]+", day, maxsplit=1)
        day, embedded_time = parts[0], parts[1]
    parsed = None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            parsed = datetime.strptime(day, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError("成交日期无效")
    clock = clock or embedded_time
    if not clock or clock in {"--", "-"}:
        return parsed.isoformat(), ""
    if re.fullmatch(r"\d{6}", clock):
        clock = f"{clock[:2]}:{clock[2:4]}:{clock[4:]}"
    try:
        value = datetime.strptime(clock, "%H:%M:%S.%f" if "." in clock else "%H:%M:%S")
    except ValueError:
        try:
            value = datetime.strptime(clock, "%H:%M")
        except ValueError as exc:
            raise ValueError("成交时间无效") from exc
    return parsed.isoformat(), value.strftime("%H:%M:%S") + (f".{value.microsecond:06d}" if value.microsecond else "")


def _currency(value: str) -> str:
    aliases = {"人民币": "CNY", "人民币元": "CNY", "RMB": "CNY", "元": "CNY", "港币": "HKD", "港元": "HKD", "美元": "USD", "美金": "USD", "欧元": "EUR"}
    currency = aliases.get(value.strip().upper(), value.strip().upper())
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("币种无法识别，请填写 CNY、HKD、USD 等三字母代码")
    return currency


def _side(value: str) -> Optional[str]:
    normalized = re.sub(r"\s+", "", value).lower()
    if normalized in {"buy", "b", "买", "买入", "证券买入", "股票买入", "成交买入", "港股通买入"}:
        return "buy"
    if normalized in {"sell", "s", "卖", "卖出", "证券卖出", "股票卖出", "成交卖出", "港股通卖出"}:
        return "sell"
    return None


def _symbol(value: str, market: str, currency: str) -> str:
    symbol = value.strip().upper().lstrip("'")
    exchange = ""
    if symbol.endswith((".SH", ".SZ", ".BJ", ".HK", ".US")):
        symbol, exchange = symbol.rsplit(".", 1)
    prefix = re.match(r"^(SH|SZ|BJ|HK)(?=\d)", symbol)
    if prefix:
        exchange = prefix.group(1)
    if re.fullmatch(r"\d+\.0+", symbol):
        symbol = symbol.split(".")[0]
    symbol = re.sub(r"^(SH|SZ|BJ|HK)(?=\d)", "", symbol)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,19}", symbol):
        raise ValueError("证券代码无效")
    if symbol.isdigit() and len(symbol) < 6:
        hk = exchange == "HK" or "港" in market or "HK" in market.upper()
        symbol = symbol.zfill(5 if hk else 6)
    return symbol


def _canonical_market(raw_symbol: str, raw_market: str) -> str:
    """Keep the exchange identity before removing a provider suffix/prefix."""
    symbol = raw_symbol.strip().upper().lstrip("'")
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    suffix_market = {"SH": "CN", "SZ": "CN", "BJ": "CN", "HK": "HK", "US": "US"}.get(suffix)
    prefix = re.match(r"^(SH|SZ|BJ|HK)(?=\d)", symbol)
    if prefix:
        prefix_market = "HK" if prefix.group(1) == "HK" else "CN"
        if suffix_market and prefix_market != suffix_market:
            raise StatementError("证券代码的前缀和后缀市场冲突，请修正后重新上传。")
        suffix_market = suffix_market or prefix_market
    hint = re.sub(r"[\s_\-]+", "", raw_market.upper())
    market = None
    if hint:
        if hint in {"HK", "HKG", "HKEX", "SEHK"} or any(term in hint for term in ("港股", "香港", "沪港通", "深港通", "STOCKCONNECT")):
            market = "HK"
        elif hint in {"CN", "SH", "SZ", "BJ", "SSE", "SZSE", "BSE", "CHINA"} or any(term in hint for term in ("A股", "沪市", "深市", "北交所", "上海", "深圳", "北京")):
            market = "CN"
        elif hint in {"US", "USA", "NASDAQ", "NYSE", "AMEX", "OTC"} or any(term in hint for term in ("美股", "美国", "纳斯达克", "纽约")):
            market = "US"
        else:
            market = "OTHER"
    if suffix_market and market and suffix_market != market:
        raise StatementError("交易市场与证券代码后缀/前缀冲突，请修正市场列后重新上传。")
    if suffix_market or market:
        return suffix_market or market
    if re.fullmatch(r"\d+\.0+", symbol):
        symbol = symbol.split(".")[0]
    if symbol.isdigit():
        return "CN" if len(symbol) == 6 else "HK"
    if re.fullmatch(r"[A-Z][A-Z0-9._-]*", symbol):
        return "US"
    return "OTHER"


def _float(value: Decimal, places: int = 2) -> float:
    return float(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def _quantity(value: Decimal) -> float:
    return _float(value, 8)


def _group(currency: str) -> dict[str, Any]:
    return {"currency": currency, "buy_amount": ZERO, "sell_amount": ZERO, "fees": ZERO,
            "realized_pnl": ZERO, "closed_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "unknown_cost_quantity": ZERO, "open_quantity": ZERO, "monthly": {}, "symbols": {}}


def analyze_statement(content: bytes, filename: str, settlement_currency: Optional[str] = None) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 50
        return _analyze(content, filename, settlement_currency)


def _analyze(content: bytes, filename: str, settlement_currency: Optional[str]) -> dict[str, Any]:
    rows = _read_rows(content, filename)
    columns = {}
    header_index = 0
    for index, row in enumerate(rows[:20]):
        candidate = _columns(row)
        if {"date", "symbol", "side", "quantity"}.issubset(candidate) and ("price" in candidate or "gross" in candidate):
            columns, header_index = candidate, index
            break
    if not columns:
        raise StatementError("缺少必需列：日期、证券代码、买卖方向、数量，以及成交价格或成交金额。")
    records = [(index + 1, row) for index, row in enumerate(rows) if index > header_index and any(row)]
    if len(records) > MAX_ROWS:
        raise StatementError("交割单最多支持 5000 条记录。")
    if not records:
        raise StatementError("交割单没有数据记录。")
    override = None
    if settlement_currency:
        try:
            override = _currency(settlement_currency)
        except ValueError as exc:
            raise StatementError(str(exc)) from exc
    def get(row: list[str], key: str) -> str:
        index = columns.get(key)
        return row[index] if index is not None and index < len(row) else ""
    def is_connect(row: list[str]) -> bool:
        return any(any(term in get(row, key) for term in ("港股通", "沪港通", "深港通")) or "stockconnect" in get(row, key).replace(" ", "").lower() for key in ("side", "market", "name"))
    has_connect = any(is_connect(row) for _, row in records)
    if has_connect and not override:
        raise StatementError("检测到港股通：币种栏可能写港币，但价格和发生金额已按人民币结算。请明确选择交割单的结算币种后重试；系统不会自动换汇。")
    if not override and any(_side(get(row, "side")) and not get(row, "currency") for _, row in records):
        raise StatementError("买卖记录缺少结算币种，请选择结算币种后重试；系统不会把无币种的成交默认当作人民币。")
    warnings = ["仅按股票长仓 FIFO 复盘；不含持仓浮盈亏、现金红利及未覆盖流水，结果不代表全账户收益。"]
    if has_connect:
        warnings.append(f"港股通结算口径已由用户指定为 {override}，覆盖港股通行的币种栏；没有乘汇率。请核实券商导出说明，勿与港元行情直接比较。")
    issues: list[dict[str, Any]] = []
    events = []
    undated_uncertainties = set()
    seen = set()
    skipped = 0
    default_currency_warned = False
    def issue(row: Optional[int], code: str, message: str) -> None:
        issues.append({"row": row, "code": code, "message": message})
    for row_number, row in records:
        # A source identity AND the whole raw record must match. Identical fills
        # with different source serials are separate; rows without IDs stay.
        identity = get(row, "identity") or get(row, "order")
        fingerprint = tuple(row)
        if identity and fingerprint in seen:
            skipped += 1
            issue(row_number, "duplicate_record", "完全相同且带原始编号的记录已跳过；独立成交不会按价格/数量合并。")
            continue
        if identity:
            seen.add(fingerprint)
        side_text = get(row, "side")
        side = _side(side_text)
        sequence = get(row, "identity")
        order = int(sequence) if re.fullmatch(r"\d{1,18}", sequence) else row_number
        try:
            day, clock = _date_time(get(row, "date"), get(row, "time"))
            currency_text = get(row, "currency")
            currency = override if is_connect(row) else (_currency(currency_text) if currency_text else override or "CNY")
            if not currency_text and not is_connect(row) and not default_currency_warned:
                warnings.append(f"文件缺少部分或全部币种值，这些行按 {'用户指定的 ' if override else '默认的 '}{override or 'CNY'}；请核实结算口径，多币种文件必须补齐币种列。")
                default_currency_warned = True
            raw_symbol = get(row, "symbol")
            market = _canonical_market(raw_symbol, "HK" if is_connect(row) else get(row, "market"))
            symbol = _symbol(raw_symbol, market, currency) if raw_symbol else ""
            balance = _number(get(row, "balance"), "股份余额", optional=True)
            quantity = _number(get(row, "quantity"), "成交数量", optional=side is None, signed=side is not None)
            signed_quantity = quantity is not None and quantity < ZERO
            if side is not None and quantity is not None:
                quantity = abs(quantity)
            base = {"row": row_number, "date": day, "time": clock, "symbol": symbol, "name": get(row, "name"),
                    "currency": currency, "market": market, "balance": balance, "sequence": order,
                    "sequence_known": bool(re.fullmatch(r"\d{1,18}", sequence))}
            asset = get(row, "asset").lower()
            if side is None or (asset and any(word in asset for word in ("期权", "期货", "债券", "option", "future", "forex", "bond"))):
                skipped += 1
                issue(row_number, "unsupported_action", f"未计入股票买卖：{side_text or '未识别业务'}。")
                cash_only = any(word in side_text for word in ("红利", "利息", "转证券", "转银行", "现金", "组合费", "扣税")) or not symbol
                if symbol and not cash_only:
                    events.append({**base, "side": "unknown", "quantity": quantity or ZERO})
                continue
            if not symbol:
                raise ValueError("证券代码不能为空")
            if quantity is None or quantity <= ZERO:
                raise ValueError("成交数量必须大于零")
            price = _number(get(row, "price"), "成交价格", optional=True)
            gross = _number(get(row, "gross"), "成交金额", optional=True, signed=True)
            signed_gross = gross is not None and gross < ZERO
            if gross is not None:
                gross = abs(gross)
            if gross is None and price is None:
                raise ValueError("成交价格和成交金额不能同时为空")
            gross = gross if gross is not None else quantity * price
            if gross <= ZERO:
                raise ValueError("成交金额必须大于零")
            price = price if price is not None else gross / quantity
            if price <= ZERO:
                raise ValueError("成交价格必须大于零")
            components = [_number(get(row, key), "交易费用", optional=True) or ZERO for key in FEE_KEYS]
            total_fee = _number(get(row, "fees_total"), "手续费合计", optional=True)
            fees = sum(components, ZERO) if total_fee is None else total_fee
            net_raw = _number(get(row, "net"), "净结算金额", optional=True, signed=True)
            if signed_quantity or signed_gross or (net_raw is not None and net_raw < ZERO):
                warnings.append("券商流水的买卖方向已明确：带负号的成交数量、成交金额或净结算金额按绝对值计算；负手续费仍视为无效，请核实原始口径。")
            net = abs(net_raw) if net_raw is not None else (gross + fees if side == "buy" else gross - fees)
            if net <= ZERO:
                raise ValueError("净结算金额必须大于零")
            if abs(price * quantity - gross) > max(Decimal("0.02"), gross * Decimal("0.001")):
                issue(row_number, "amount_price_mismatch", "数量×价格与成交金额不一致，成本和卖出所得以成交金额/净结算金额为准。")
            if net_raw is not None and abs(net - (gross + fees if side == "buy" else gross - fees)) > Decimal("0.02"):
                issue(row_number, "net_amount_authoritative", "净结算金额与成交金额±费用不一致，采用净结算金额，不再重复扣费。")
            events.append({**base, "side": side, "quantity": quantity, "price": price, "gross_amount": gross,
                           "fees": fees, "net_amount": net})
        except StatementError:
            raise
        except (ValueError, InvalidOperation, OverflowError) as exc:
            skipped += 1
            issue(row_number, "invalid_row", str(exc))
            # Rejecting a security movement breaks the cost ledger. Insert an
            # uncertainty marker so later sales never earn invented profits.
            try:
                currency = override if is_connect(row) else (_currency(get(row, "currency")) if get(row, "currency") else override or "CNY")
                market = _canonical_market(get(row, "symbol"), "HK" if is_connect(row) else get(row, "market"))
                symbol = _symbol(get(row, "symbol"), market, currency)
                try:
                    day, clock = _date_time(get(row, "date"), get(row, "time"))
                except ValueError:
                    undated_uncertainties.add((currency, market, symbol))
                    raise
                events.append({"row": row_number, "date": day, "time": clock, "symbol": symbol, "name": get(row, "name"),
                               "currency": currency, "market": market, "balance": None, "sequence": order, "side": "unknown", "quantity": ZERO,
                               "sequence_known": bool(re.fullmatch(r"\d{1,18}", sequence))})
            except (ValueError, InvalidOperation):
                warnings.append(f"第 {row_number} 行缺少可定位日期/代码/币种，成本链可能不完整；请修正后重新分析。")
    events.sort(key=lambda event: (event["date"], event["time"] or "00:00:00", event["sequence"], event["row"]))
    dated_events = {}
    for event in events:
        dated_events.setdefault((event["currency"], event["market"], event["symbol"], event["date"]), []).append(event)
    ambiguous_days = set()
    for day_key, batch in dated_events.items():
        uncertain = len(batch) > 1 and any(not event["time"] and not event["sequence_known"] for event in batch)
        sides = {event["side"] for event in batch}
        if uncertain and {"buy", "sell"}.issubset(sides):
            ambiguous_days.add(day_key)
    if ambiguous_days:
        warnings.append("同日同股部分记录缺少成交时间及可排序原始序号，顺序按文件行号推定；FIFO 配对结果可能受导出顺序影响。")
        issue(None, "same_day_order_uncertain", "同日买卖顺序无法确认；该成本链的收益保守排除，直到后续日期的零余额能确认重新建仓。")
    groups: dict[str, dict[str, Any]] = {}
    ledgers: dict[tuple[str, str, str], deque] = {}
    tainted = set(undated_uncertainties)
    order_quarantine = {}
    output_trades = []
    for event in events:
        currency, market, symbol = event["currency"], event["market"], event["symbol"]
        key = (currency, market, symbol)
        group = groups.setdefault(currency, _group(currency))
        security = group["symbols"].setdefault((market, symbol), {"symbol": symbol, "market": market, "name": event["name"], "realized_pnl": ZERO, "fees": ZERO, "open_quantity": ZERO})
        if event["name"]:
            security["name"] = event["name"]
        first = key not in ledgers
        ledger = ledgers.setdefault(key, deque())
        balance, quantity = event["balance"], event["quantity"]
        side = event["side"]
        if (currency, market, symbol, event["date"]) in ambiguous_days:
            order_quarantine[key] = event["date"]
            tainted.add(key)
        if side == "unknown":
            known_quantity = sum((lot[0] for lot in ledger), ZERO)
            ledger.clear()
            unknown_quantity = balance if balance is not None else known_quantity
            if unknown_quantity > ZERO:
                ledger.append([unknown_quantity, None])
            tainted.add(key)
            issue(event["row"], "cost_basis_uncertain", "非支持业务或无效成交影响成本链；既有底仓成本改为未知，相关卖出部分不计盈亏。")
            continue
        # Broker share balance normally describes holdings AFTER this fill.
        if first and balance is not None:
            initial = balance - quantity if side == "buy" else balance + quantity
            if initial > ZERO:
                ledger.append([initial, None])
                issue(event["row"], "opening_cost_unknown", f"股份余额显示期初底仓 {_quantity(initial)} 股，成本未知。")
            elif initial < ZERO:
                tainted.add(key)
                issue(event["row"], "balance_mismatch", "股份余额与成交数量矛盾，无法确认完整成本链。")
        if not first and balance is not None:
            before_quantity = sum((lot[0] for lot in ledger), ZERO)
            expected_balance = before_quantity + quantity if side == "buy" else before_quantity - quantity
            if expected_balance != balance:
                # A missing/inconsistent security movement may PRECEDE this
                # fill. Exclude the current sale as well as subsequent sales.
                for lot in ledger:
                    lot[1] = None
                issue(event["row"], "balance_mismatch", "当笔股份余额与已有流水不一致；当笔卖出成本降为未知，相关盈亏已排除。")
        month = event["date"][:7]
        monthly = group["monthly"].setdefault(month, {"month": month, "realized_pnl": ZERO, "fees": ZERO})
        group["fees"] += event["fees"]
        security["fees"] += event["fees"]
        monthly["fees"] += event["fees"]
        if side == "buy":
            group["buy_amount"] += event["net_amount"]
            ledger.append([quantity, None if key in order_quarantine or key in undated_uncertainties else event["net_amount"]])
        else:
            group["sell_amount"] += event["net_amount"]
            remaining, known, unknown, cost = quantity, ZERO, ZERO, ZERO
            # After an unquantifiable movement, neither chronology nor bottom
            # inventory is reliable. Exclude sells until a balance reconciles.
            if key in undated_uncertainties or key in order_quarantine or (key in tainted and balance is None):
                for lot in ledger:
                    lot[1] = None
            while remaining > ZERO and ledger:
                lot = ledger[0]
                used = min(remaining, lot[0])
                if lot[1] is None:
                    unknown += used
                else:
                    portion = lot[1] * used / lot[0]
                    cost += portion
                    lot[1] -= portion
                    known += used
                lot[0] -= used
                remaining -= used
                if lot[0] == ZERO:
                    ledger.popleft()
            unknown += remaining
            if unknown > ZERO:
                group["unknown_cost_quantity"] += unknown
                issue(event["row"], "unmatched_sale", f"卖出 {_quantity(unknown)} 股缺少可确认买入成本，已从盈亏排除。")
            if known > ZERO:
                pnl = event["net_amount"] * known / quantity - cost
                group["realized_pnl"] += pnl
                security["realized_pnl"] += pnl
                monthly["realized_pnl"] += pnl
                group["closed_trades"] += 1
                if pnl > ZERO:
                    group["winning_trades"] += 1
                elif pnl < ZERO:
                    group["losing_trades"] += 1
        if balance is not None:
            accounted = sum((lot[0] for lot in ledger), ZERO)
            if accounted != balance:
                issue(event["row"], "balance_mismatch", "股份余额与 FIFO 剩余数量不一致；后续底仓成本改为未知，避免制造盈亏。")
                ledger.clear()
                if balance > ZERO:
                    ledger.append([balance, None])
            if key not in order_quarantine and key not in undated_uncertainties:
                tainted.discard(key)
            elif key in order_quarantine and balance == ZERO and event["date"] > order_quarantine[key]:
                order_quarantine.pop(key)
                if key not in undated_uncertainties:
                    tainted.discard(key)
        output_trades.append({field: (_quantity(event[field]) if field == "quantity" else _float(event[field], 8 if field == "price" else 2)) if isinstance(event[field], Decimal) else event[field]
                              for field in ("row", "date", "time", "symbol", "market", "name", "side", "quantity", "price", "gross_amount", "fees", "net_amount", "currency")})
    if not output_trades:
        raise StatementError("没有可分析的有效股票买卖记录，请检查表头、日期、数值和业务类别。")
    for (currency, market, symbol), ledger in ledgers.items():
        quantity = sum((lot[0] for lot in ledger), ZERO)
        groups[currency]["open_quantity"] += quantity
        groups[currency]["symbols"][(market, symbol)]["open_quantity"] = quantity
    currencies = []
    for currency in sorted(groups):
        group = groups[currency]
        group["monthly"] = [group["monthly"][month] for month in sorted(group["monthly"])]
        group["symbols"] = [group["symbols"][symbol] for symbol in sorted(group["symbols"])]
        group["win_rate"] = round(group["winning_trades"] / group["closed_trades"] * 100, 2) if group["closed_trades"] else None
        def serializable(value: Any, key: str = "") -> Any:
            if isinstance(value, Decimal):
                return _quantity(value) if key in {"open_quantity", "unknown_cost_quantity"} else _float(value)
            if isinstance(value, dict):
                return {item_key: serializable(item, item_key) for item_key, item in value.items()}
            if isinstance(value, list):
                return [serializable(item) for item in value]
            return value
        currencies.append(serializable(group))
    if any(group["unknown_cost_quantity"] > ZERO for group in groups.values()):
        warnings.append("存在成本未知的卖出，已排除对应盈亏；已实现盈亏仅覆盖能确认成本的成交。")
    if any(item["code"] in {"invalid_row", "cost_basis_uncertain", "balance_mismatch"} for item in issues):
        warnings.append("流水不完整或含未支持的证券变动，相关成本链已保守降级；请补齐或修正原始文件后复核。")
    if len(currencies) > 1:
        warnings.append("结果按结算币种分组，未跨币种加总，也未自动换汇。")
    return {"file_name": Path(filename).name, "total_rows": len(records), "trade_count": len(output_trades),
            "skipped_rows": skipped, "currencies": currencies, "trades": output_trades,
            "issues": issues, "warnings": list(dict.fromkeys(warnings))}


def statement_template() -> str:
    """A download-ready Chinese CSV example; all numbers are fictional."""
    return ("成交日期,成交时间,证券代码,证券名称,买卖方向,成交数量,成交价格,成交金额,手续费合计,币种,成交编号\n"
            "2026-01-05,10:00:00,600000,演示股票（虚构数据）,买入,100,10,1000,5,CNY,DEMO-001\n"
            "2026-01-06,10:00:00,600000,演示股票（虚构数据）,买入,100,11,1100,5,CNY,DEMO-002\n"
            "2026-01-08,14:00:00,600000,演示股票（虚构数据）,卖出,150,12,1800,5,CNY,DEMO-003\n")
