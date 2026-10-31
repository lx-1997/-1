"""Independent verifier rejection tests; no archived or application code executed."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('event_verify',Path(__file__).resolve().parents[1]/'verify-quant-event-soak.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


def memory():
    return dict(api_lifetime_peak_bytes=100,largest_waited_child_peak_bytes=0,descendant_limit=0,
        CPU_spawn_attempts=0,forbidden_spawn_attempts=0,all_children_waited=True,
        SIGCHLD_not_ignored=True,tree_peak_upper_bound_bytes=100)


def parent():
    return dict(control_parent_peak_bytes=50,largest_waited_descendant_peak_bytes=150,descendant_limit=1,
        all_children_waited=True,SIGCHLD_not_ignored=True,whole_tree_peak_upper_bound_bytes=200,accepted=True)


class IndependentEventVerifierTests(unittest.TestCase):
    def test_fixed_percentiles(self):
        self.assertEqual(v.percentile([100,4,3,1,2],.95),100)
        self.assertEqual(v.percentile(list(range(100)),.99),98)

    def test_missing_nonfinite_negative_and_bool_latency_refused(self):
        for values in ([],[float('nan')],[float('inf')],[-1],[True]):
            with self.assertRaises(ValueError):v.percentile(values,.95)

    def test_duplicate_and_nonfinite_json_refused(self):
        for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}'):
            with self.assertRaises(ValueError):v.parse(raw)

    def test_no_descendant_memory_bound(self):
        self.assertEqual(v.memory_check(memory(),parent()),200)

    def test_child_and_unwaited_memory_refused(self):
        for key,value in [('CPU_spawn_attempts',1),('forbidden_spawn_attempts',1),('largest_waited_child_peak_bytes',1),
                          ('descendant_limit',1),('all_children_waited',False),('SIGCHLD_not_ignored',False)]:
            m=memory();m[key]=value
            with self.assertRaises(ValueError):v.memory_check(m,parent())

    def test_underreported_or_overbudget_parent_refused(self):
        for key,value in [('whole_tree_peak_upper_bound_bytes',199),('largest_waited_descendant_peak_bytes',99),
                          ('all_children_waited',False),('accepted',False),('control_parent_peak_bytes',1024**3)]:
            p=parent();p[key]=value
            with self.assertRaises(ValueError):v.memory_check(memory(),p)

    def test_invalid_process_marker_refused(self):
        for pid in (0,1,True,'123'):
            with self.assertRaises(ValueError):v.absent(pid)

    def test_protocol_is_distinct_from_price_soak(self):
        self.assertRegex(v.PROTOCOL_SHA,r'^[a-f0-9]{64}$')
        self.assertEqual(v.EXPERIMENTS_SHA,'25cb54bea8b6795443cbd4d1b0ae56cfab5bfe0921fc780c6a5fcf2da210566f')


if __name__=='__main__':unittest.main()
