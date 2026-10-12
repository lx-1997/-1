"""Actual Qlib expressions and a purged chronological LightGBM experiment."""
from __future__ import annotations

import base64
import binascii
import csv
from datetime import date, timedelta
import importlib.metadata
import io
import json
import math
from pathlib import Path
import re
import tempfile
import uuid


def _records(data):
    if "content_base64" not in data:
        rows = data.get("prices")
    else:
        try:
            raw = base64.b64decode(data["content_base64"], validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("文件编码无效，请重新选择 CSV")
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("CSV 最大 2 MiB")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gb18030")
            except UnicodeDecodeError:
                raise ValueError("请导出 UTF-8 或 GB18030 CSV")
        if str(data.get("file_name", "")).lower().endswith(".json") or text.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except ValueError:
                raise ValueError("JSON 格式无效")
            rows = parsed.get("prices") if isinstance(parsed, dict) else parsed
        else:
            try:
                delimiter = "\t" if "\t" in text.splitlines()[0] else ","
                reader = csv.DictReader(io.StringIO(text), delimiter=delimiter, strict=True)
                names = [str(name).strip().lower() for name in (reader.fieldnames or [])]
                if not names or len(names) != len(set(names)):
                    raise ValueError("CSV 表头重复或为空，请保留唯一的 OHLCV 列")
                reader.fieldnames = names
                rows = list(reader)
                if any(None in row for row in rows):
                    raise ValueError("CSV 行包含超出表头的字段")
            except (IndexError, csv.Error):
                raise ValueError("CSV 格式无效")
    if not isinstance(rows, list) or not rows:
        raise ValueError("请输入真实 OHLCV 记录，或选择 CSV 文件")
    if len(rows) > 5000:
        raise ValueError("最多分析 5000 条 OHLCV 记录")
    return rows


def _validate(data):
    rows = _records(data)
    normalized, seen = [], set()
    for number, row in enumerate(rows, 2):
        if not isinstance(row, dict):
            raise ValueError("OHLCV 每行必须是对象")
        symbol = str(row.get("symbol", "")).strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,19}", symbol):
            raise ValueError(f"第 {number} 行证券代码无效")
        day = str(row.get("date", "")).strip()
        try:
            parsed = date.fromisoformat(day)
            if parsed.weekday() > 4 or parsed > date.today():
                raise ValueError()
        except ValueError:
            raise ValueError(f"第 {number} 行日期必须是过去的交易工作日 YYYY-MM-DD")
        key = symbol, day
        if key in seen:
            raise ValueError("同一证券同一天不能有重复 OHLCV 记录")
        seen.add(key)
        values = {}
        for field in ["open", "high", "low", "close", "volume"]:
            try:
                if isinstance(row.get(field), bool):
                    raise ValueError()
                value = float(row[field])
            except (KeyError, ValueError, TypeError):
                raise ValueError(f"第 {number} 行缺少有效 {field}")
            if not math.isfinite(value) or value < 0 or value > 1e18 or (field != "volume" and value == 0):
                raise ValueError(f"第 {number} 行 {field} 必须是有限正值，成交量可为零")
            values[field] = value
        if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"]):
            raise ValueError(f"第 {number} 行 OHLC 高低价矛盾")
        normalized.append(dict(symbol=symbol, date=day, **values))
    symbols = sorted({r["symbol"] for r in normalized})
    if not 5 <= len(symbols) <= 20:
        raise ValueError("横截面因子研究需要 5–20 个证券")
    calendars = [{r["date"] for r in normalized if r["symbol"] == s} for s in symbols]
    days = sorted(set.intersection(*calendars))
    if len(days) < 180:
        raise ValueError("需要至少 180 个共同交易日；不会填造缺失行情")
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    weekdays = sum((first + timedelta(days=i)).weekday() < 5 for i in range((last - first).days + 1))
    if len(days) / weekdays < .8:
        raise ValueError("日频行情覆盖不足：共同日期过于稀疏，不能将周/月频当每日价格训练")
    selected = [r for r in normalized if r["date"] in set(days)]
    return selected, symbols, days, len(normalized) - len(selected)


def chronological_segments(days, horizon=5):
    first, second = int(len(days) * .6), int(len(days) * .8)
    return {
        "train": (days[0], days[first - horizon - 1]),
        "valid": (days[first], days[second - horizon - 1]),
        "test": (days[second], days[-horizon - 1]),
    }


def _number(value):
    return round(float(value), 8) if value is not None and math.isfinite(float(value)) else None


def analyze_factors(data):
    rows, symbols, days, excluded = _validate(data)
    try:
        import numpy as np
        import pandas as pd
        import qlib
        from qlib.config import REG_US
        from qlib.data import D
        from qlib.contrib.data.loader import Alpha158DL
        from qlib.contrib.eva.alpha import calc_ic
        from qlib.contrib.model.gbdt import LGBModel
        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP
        from qlib.workflow import R
    except (ImportError, OSError) as error:
        raise RuntimeError("Qlib / LightGBM 独立计算环境未就绪，请联系管理员") from error
    horizon = 5
    segments = chronological_segments(days, horizon)
    frame = pd.DataFrame(rows)
    with tempfile.TemporaryDirectory(prefix="qlib-input-") as temporary:
        directory = Path(temporary)
        (directory / "calendars").mkdir()
        (directory / "calendars/day.txt").write_text("\n".join(days) + "\n")
        (directory / "instruments").mkdir()
        (directory / "instruments/all.txt").write_text("\n".join(f"{s}\t{days[0]}\t{days[-1]}" for s in symbols) + "\n")
        for symbol in symbols:
            part = frame[frame.symbol == symbol].set_index("date").reindex(days)
            target = directory / "features" / symbol.lower()
            target.mkdir(parents=True)
            for name in ["open", "high", "low", "close", "volume"]:
                np.concatenate(([0], part[name].to_numpy())).astype("<f4").tofile(target / f"{name}.day.bin")
        qlib.init(provider_uri=str(directory), region=REG_US, kernels=1, expression_cache=None, dataset_cache=None,
                  clear_mem_cache=True, redis_port=-1, logging_level="ERROR",
                  exp_manager={"class": "MLflowExpManager", "module_path": "qlib.workflow.expm",
                               "kwargs": {"uri": "sqlite:///" + str(directory / "tracking.sqlite3"), "default_exp_name": "private-research"}})
        fields, names = Alpha158DL.get_feature_config({
            "kbar": {}, "price": {"windows": [0], "feature": ["OPEN", "HIGH", "LOW"]},
            "rolling": {"windows": [5, 20, 60], "include": ["ROC", "MA", "STD", "RSV"]},
        })
        raw = D.features(symbols, fields + [f"Ref($close,-{horizon})/$close-1"], start_time=days[0], end_time=days[-1])
        raw.columns = pd.MultiIndex.from_tuples([("feature", name) for name in names] + [("label", "forward_5d")])
        raw = raw.swaplevel().sort_index().replace([np.inf, -np.inf], np.nan)
        # Feature imputation and selection use training dates exclusively.
        dates = raw.index.get_level_values("datetime")
        training = raw.loc[(dates >= pd.Timestamp(segments["train"][0])) & (dates <= pd.Timestamp(segments["train"][1]))]
        kept = [column for column in raw["feature"].columns if training[("feature", column)].notna().mean() >= .7]
        if len(kept) < 5:
            raise ValueError("训练期有效因子不足，请补齐一致口径 OHLCV")
        medians = training["feature"][kept].median()
        features = raw["feature"][kept].fillna(medians)
        labels = raw["label"]
        prepared = pd.concat({"feature": features, "label": labels}, axis=1).dropna(subset=[("label", "forward_5d")])
        dataset = DatasetH(handler=DataHandlerLP.from_df(prepared), segments=segments)
        counts = {segment: len(dataset.prepare(segment)) for segment in segments}
        if counts["train"] < 300 or counts["valid"] < 80 or counts["test"] < 80:
            raise ValueError("训练、验证或测试样本不足，请扩大真实数据范围")
        model = LGBModel(loss="mse", early_stopping_rounds=10, num_boost_round=50,
                         num_threads=1, num_leaves=8, learning_rate=.05, verbose=-1, seed=42)
        with R.start(experiment_name="private-research-" + uuid.uuid4().hex):
            model.fit(dataset, num_boost_round=50, early_stopping_rounds=10, verbose_eval=0)
            prediction = model.predict(dataset, segment="test")
        test_labels = dataset.prepare("test", col_set="label").iloc[:, 0]
        ic, rank_ic = calc_ic(prediction, test_labels, dropna=True)
        mse = float(np.mean((prediction.to_numpy() - test_labels.to_numpy()) ** 2))
        importance = model.model.feature_importance(importance_type="gain")
        importance_rows = sorted([{"factor": factor, "importance": _number(value)} for factor, value in zip(kept, importance)],
                                 key=lambda row: row["importance"], reverse=True)
        ic_rows = [{"date": day.isoformat()[:10], "ic": _number(value), "rank_ic": _number(rank_ic.get(day))} for day, value in ic.items()]
        split_rows = [{"segment": {"train": "训练集", "valid": "验证集", "test": "测试集"}[key],
                       "start": boundaries[0], "end": boundaries[1], "samples": counts[key]} for key, boundaries in segments.items()]
    warnings = ["历史样本上的因子相关性不代表未来收益；本工具不会下单。", "输入行情应使用一致复权口径；公司行动和样本选择由导入数据决定。", "模型标签使用未来5个共同观测日的收益；各证券缺失日期会从共同日历剔除，不补造价格。"]
    if all(symbol.startswith("DEMO") for symbol in symbols):
        warnings.insert(0, "当前输入含虚构演示证券标记，仅用于功能验证，不代表真实市场表现。")
    if excluded:
        warnings.append(f"{excluded} 条非共同交易日记录未纳入；未补造行情。")
    if not ic_rows or all(row["ic"] is None for row in ic_rows):
        warnings.append("测试期截面相关性无法估计，结果不具备有效 IC 证据。")
    return {
        "tool": "factors", "title": "因子研究实验", "engine": "Qlib " + importlib.metadata.version("pyqlib") + " / LightGBM " + importlib.metadata.version("lightgbm"),
        "summary": [{"label": "测试期 IC", "value": _number(ic.mean())}, {"label": "测试期 Rank IC", "value": _number(rank_ic.mean())},
                    {"label": "测试均方误差", "value": _number(mse)}, {"label": "因子数", "value": len(kept)}, {"label": "共同交易日", "value": len(days)}],
        "tables": [{"key": "splits", "title": "时间切分与样本", "columns": [{"key": "segment", "label": "数据集"}, {"key": "start", "label": "开始日期"}, {"key": "end", "label": "结束日期"}, {"key": "samples", "label": "样本数", "type": "number"}], "rows": split_rows},
                   {"key": "importance", "title": "训练所得因子重要性", "columns": [{"key": "factor", "label": "因子"}, {"key": "importance", "label": "增益", "type": "number"}], "rows": importance_rows}],
        "charts": [{"key": "ic", "title": "测试期每日截面相关性", "type": "line", "data": ic_rows, "x_key": "date", "series": [{"key": "ic", "label": "IC"}, {"key": "rank_ic", "label": "Rank IC"}]}],
        "warnings": warnings,
        "methodology": ["使用 Qlib Alpha158 表达式子集提取价格、K线与滚动因子，实际训练 LightGBM。", "60%训练、20%验证、20%测试；两处分界前各剔除5个共同观测日，标签为未来5个共同观测日价格收益。", "工作日覆盖至少80%，不接受周/月频；交易所节假日不人工填充。", "缺失值中位数和因子可用性仅由训练集决定，测试集不参与模型调优。", "单线程，最多50轮；IC按每个测试共同日期的证券截面计算。"],
        "sources": [{"name": "用户导入 OHLCV", "as_of": days[-1], "coverage": f"{len(symbols)} 个证券 / {len(days)} 共同交易日"}],
    }
