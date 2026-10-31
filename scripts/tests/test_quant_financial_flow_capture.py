"""Official-index boundary fixtures only; never queries a live provider."""
import copy,importlib.util
from datetime import datetime
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('filings',Path(__file__).resolve().parents[1]/'capture-quant-financial-flow-filings.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row(doc='123'):
    return {'secCode':'002109','orgId':'9900002009','announcementId':doc,'adjunctUrl':'finalpage/2025-04-24/'+doc+'.PDF',
            'announcementTime':int(datetime.fromisoformat('2025-04-24T00:00:00+08:00').timestamp()*1000),
            'announcementTitle':'2024年年度报告'}


class IndexTests(unittest.TestCase):
    def test_midnight_beijing_is_previous_utc_day_but_valid_disclosure_day(self):
        r=row();total,rows=m.index_page({'totalAnnouncement':1,'announcements':[r]},1)
        self.assertEqual(total,1);self.assertEqual(rows,[r])
        self.assertEqual(m.RULES['disclosure_timestamp_zone'],'Asia/Shanghai_UTC+08:00')

    def test_pagination_preserves_all_rows_not_just_first_thirty(self):
        rows=[row(str(i)) for i in range(43)]
        n,a=m.index_page({'totalAnnouncement':43,'announcements':rows[:30]},1)
        _,b=m.index_page({'totalAnnouncement':43,'announcements':rows[30:]},2,n)
        self.assertEqual(len(a+b),43)

    def test_zero_keyword_results_are_only_a_scoped_query(self):
        self.assertEqual(m.index_page({'totalAnnouncement':0,'announcements':None},1),(0,[]))
        self.assertFalse(m.RULES['full_revision_history_certified']);self.assertFalse(m.RULES['historical_pit_certified'])

    def test_truncated_page_or_changing_total_rejected(self):
        for data,page,seen in [({'totalAnnouncement':43,'announcements':[]},2,43),({'totalAnnouncement':2,'announcements':[row()]},1,None),
                               ({'totalAnnouncement':1,'announcements':[row()]},1,2),({'totalAnnouncement':121,'announcements':[]},1,None)]:
            with self.assertRaises(ValueError):m.index_page(data,page,seen)

    def test_foreign_date_identity_path_epoch_refused(self):
        for key,value in [('secCode','000333'),('orgId','other'),('announcementTime',True),('announcementTime',10**100),
                          ('adjunctUrl','finalpage/2025-04-23/123.PDF'),('announcementId','../123')]:
            r=row();r[key]=value
            with self.assertRaises(ValueError):m.index_page({'totalAnnouncement':1,'announcements':[r]},1)

    def test_full_reports_and_restatement_not_annual_summary_or_wrong_year(self):
        for name in ('2024年年度报告','2024年年度报告（更正后）','2024年一季度报告','2024年第一季度报告（更正后）',
                     '关于前期会计差错更正及追溯调整的公告','关于公司2024年度对以前年度报告披露的财务报表数据进行追溯调整的公告'):
            self.assertTrue(m.selected({**row(),'announcementTitle':name}),name)
        for name in ('2024年年度报告摘要','2025年年度报告','关于召开2024年年度报告说明会的公告'):
            self.assertFalse(m.selected({**row(),'announcementTitle':name}),name)


if __name__=='__main__':unittest.main()
