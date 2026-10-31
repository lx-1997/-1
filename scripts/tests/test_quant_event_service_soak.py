"""Protocol and rejection checks; fixtures never reach an application or provider."""
import copy
import asyncio
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('event_soak',Path(__file__).resolve().parents[1]/'quant-event-service-soak.py')
load=importlib.util.module_from_spec(spec);spec.loader.exec_module(load)


def metrics():
    own=100*1024**2
    return dict(observer_ms=[10.]*600,create_ms=[20.]*120,loop_ms=[1.]*600,peak_rss_bytes=own,
        peak_qr_disk_bytes=100*1024**2,peak_global_slots=4,peak_owner_slots=2,peak_active_slots=4,
        errors=[],external_connection_attempts=0,completed=96,exact_results=96,cancelled=24,
        remaining_slots=0,remaining_admissions=0,paper_accounts=0,elapsed_seconds=3600,rounds=30,
        memory=dict(api_lifetime_peak_bytes=own,largest_waited_child_peak_bytes=0,descendant_limit=0,
            tree_peak_upper_bound_bytes=own,all_children_waited=True,SIGCHLD_not_ignored=True,
            CPU_spawn_attempts=0,forbidden_spawn_attempts=0))


class EventSoakProtocolTests(unittest.TestCase):
    def test_fixed_full_accepts_complete_fixture_only(self):
        self.assertTrue(load.evaluate(metrics(),'full')['full_soak_passed'])

    def test_smoke_never_proves_one_hour(self):
        result=load.evaluate(metrics(),'smoke')
        self.assertTrue(result['engineering_checks_passed']);self.assertFalse(result['full_soak_passed'])

    def test_thresholds_preserve_price_workload_contract(self):
        oldspec=importlib.util.spec_from_file_location('price_soak',Path(load.__file__).with_name('quant-service-soak.py'))
        old=importlib.util.module_from_spec(oldspec);oldspec.loader.exec_module(old)
        for key in ('duration_seconds','read_p95_ms','read_p99_ms','create_p95_ms','loop_p99_ms','loop_max_ms',
                    'rss_max_bytes','qr_disk_max_bytes','global_worker_limit','owner_worker_limit',
                    'minimum_completed_jobs','minimum_cancelled_jobs','minimum_rounds','minimum_observer_reads'):
            self.assertEqual(load.PROTOCOL[key],old.PROTOCOL[key],key)
        self.assertEqual(load.PROTOCOL['knowledge_modes'],['historical_reconstruction','as_recorded'])
        for case in (0,1):
            modes={load.PROTOCOL['lane_modes'][i] for i,c in enumerate(load.PROTOCOL['lane_cases']) if c==case}
            self.assertEqual(modes,set(load.PROTOCOL['knowledge_modes']))

    def test_missing_or_nonfinite_latencies_fail(self):
        for key in ('observer_ms','create_ms','loop_ms'):
            for value in ([],[float('inf')],[float('nan')]):
                m=metrics();m[key]=value
                self.assertFalse(load.evaluate(m,'full')['full_soak_passed'],(key,value))

    def test_inadequate_workload_not_full(self):
        for key,value in [('elapsed_seconds',3599.99),('completed',31),('cancelled',9),('rounds',29)]:
            m=metrics();m[key]=value
            self.assertFalse(load.evaluate(m,'full')['full_soak_passed'],key)

    def test_limits_results_drain_and_network_are_independent(self):
        for key,value in [('peak_global_slots',5),('peak_owner_slots',3),('peak_active_slots',3),
                          ('exact_results',95),('remaining_slots',1),('remaining_admissions',1),
                          ('paper_accounts',1),('external_connection_attempts',1),('errors',[{'type':'failure'}]),
                          ('peak_qr_disk_bytes',load.PROTOCOL['qr_disk_max_bytes']+1)]:
            m=metrics();m[key]=value
            self.assertFalse(load.evaluate(m,'full')['engineering_checks_passed'],key)

    def test_no_child_memory_contract_does_not_accept_cpu_profile(self):
        for key,value in [('largest_waited_child_peak_bytes',1),('descendant_limit',4),
                          ('CPU_spawn_attempts',1),('forbidden_spawn_attempts',1),('all_children_waited',False),
                          ('SIGCHLD_not_ignored',False),('tree_peak_upper_bound_bytes',1)]:
            m=metrics();m['memory'][key]=value
            self.assertFalse(load.memory_ok(m),key)

    def test_memory_must_be_present_and_bounded(self):
        for mem in ({},{'api_lifetime_peak_bytes':False}):
            m=metrics();m['memory']=mem;self.assertFalse(load.memory_ok(m))
        m=metrics();m['memory']['api_lifetime_peak_bytes']=load.PROTOCOL['rss_max_bytes']+1
        m['memory']['tree_peak_upper_bound_bytes']=m['memory']['api_lifetime_peak_bytes']
        self.assertFalse(load.memory_ok(m))

    def test_all_event_dependencies_are_frozen(self):
        for name in ('quant_event_service.py','quant_event_replay.py','quant_event_policy.py','quant_dividend_book.py',
                     'quant_corporate_actions.py','quant_calendar_store.py','quant_exchange_schedule.py',
                     'quant_evidence_store.py','quant_research_api.py','quant_worker_slots.py'):
            self.assertIn(name,load.SOURCES)
        self.assertIn('quant-event-service-soak.py',load.source_pins())


class MonitorOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_drains_inflight_read_before_transport_can_close(self):
        state={'transport_open':True,'read_cleanup_completed':False}
        entered=asyncio.Event()
        async def reading():
            try:
                entered.set();await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                self.assertTrue(state['transport_open'])
                state['read_cleanup_completed']=True
        task=asyncio.create_task(reading());await entered.wait()
        results=await load.drain_monitors([task])
        self.assertTrue(state['read_cleanup_completed']);self.assertTrue(task.done())
        self.assertIsInstance(results[0],asyncio.CancelledError)
        state['transport_open']=False

    async def test_drain_preserves_an_already_failed_monitor(self):
        async def failed():raise RuntimeError('pre-existing read failure')
        task=asyncio.create_task(failed());await asyncio.sleep(0)
        results=await load.drain_monitors([task])
        self.assertIsInstance(results[0],RuntimeError)
        self.assertIn('pre-existing read failure',str(results[0]))


if __name__=='__main__':unittest.main()
