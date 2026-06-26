"""全市场扫描类技能(股东增减持/A股财报/重大事项)的共享路由闸门。

这三个技能产出的都是「全市场清单」，过去仅凭「范围词 + 事件词」两组关键词同时出现就接管，
导致两类跑偏：
  ① 句中出现泛词(如裸"市场")凑巧与事件词共现 → 误触发全市场扫描；
  ② 用户问的是**某一只具体个股**，却被当成全市场扫描，返回一整张清单 → 答非所问。
本模块对外暴露「是否锁定了具体个股」这一道共享否决闸；具体识别(代码 + 名称表)落在
`stock_name_index`，此处只做转出，保持 detector 侧 import 稳定。
"""

from __future__ import annotations

from .stock_name_index import mentions_specific_code, mentions_specific_stock  # noqa: F401

__all__ = ["mentions_specific_code", "mentions_specific_stock"]
