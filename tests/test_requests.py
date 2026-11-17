"""Original synthetic workloads; abstract ticks are not measured timing."""
import copy,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from cascade_ledger import core,requests
ROOT=Path(__file__).resolve().parents[1]
def scenario():
 return {'schema':1,'topology':{'schema':1,'ticks':10,'nodes':[{'id':'api','fail_after':1,'recover_after':1,'groups':[]}],'outages':[],'assertions':[{'node':'api','max_down_ticks':10,'up_at_end':True}]},'requests':[{'id':'write','target':'api','tick':0,'latency':4,'copies':2}],'policy':{'timeout':2,'retries':1,'backoff':1,'start_limit':10,'breaker_failures':0,'breaker_open_ticks':2,'deduplicate':False},'assertions':{'max_failed':0,'max_pending':0,'max_executions':1}}
def simulate(d):return requests.simulate(requests.load(core.canonical(d)))
class RequestTests(unittest.TestCase):
 def test_timeout_does_not_cancel_backend(self):
  r=simulate(scenario());self.assertEqual(r['summary']['failed'],1);self.assertEqual(r['summary']['server_executions'],4);self.assertEqual([a['start_tick'] for a in r['attempts']],[0,3]);self.assertTrue(all(e['late'] for e in r['events'] if e['event']=='backend_complete'));self.assertEqual(r['gate'],'fail')
 def test_completion_equal_deadline_wins(self):
  d=scenario();d['policy'].update(timeout=4,deduplicate=True);r=simulate(d);self.assertEqual(r['summary']['success'],1);self.assertEqual(r['summary']['server_executions'],1);self.assertFalse(any(e['event']=='timeout' for e in r['events']));self.assertEqual(r['gate'],'pass')
 def test_perfect_dedupe_across_late_retries(self):
  d=scenario();d['policy']['deduplicate']=True;r=simulate(d);self.assertEqual(r['summary']['server_executions'],1);self.assertEqual(r['summary']['failed'],1)
 def test_outage_any_intervening_tick_fails_backend(self):
  d=scenario();d['policy'].update(timeout=4,retries=0);d['topology']['outages']=[{'node':'api','start':2,'end':3}];r=simulate(d);self.assertEqual(r['summary']['server_executions'],0);self.assertEqual(r['attempts'][0]['response'],'failure')
 def test_retry_recovers_after_outage(self):
  d=scenario();d['policy'].update(timeout=4,deduplicate=True);d['topology']['outages']=[{'node':'api','start':2,'end':3}];r=simulate(d);self.assertEqual([a['start_tick'] for a in r['attempts']],[0,5]);self.assertEqual(r['summary']['success'],1)
 def test_rate_rejection_deterministic_and_retry_bound(self):
  d=scenario();d['requests']=[{'id':x,'target':'api','tick':0,'latency':1,'copies':1} for x in ['z','a','b']];d['policy'].update(timeout=1,start_limit=1,retries=0);r=simulate(d);self.assertEqual(r['summary']['success'],1);self.assertEqual(r['requests']['a']['state'],'success');self.assertEqual(r['summary']['rejected_attempts'],2);self.assertEqual(r['summary']['failed'],2)
 def test_circuit_window_and_attempt_consumption(self):
  d=scenario();d['requests']+=[{'id':'later','target':'api','tick':2,'latency':1,'copies':1},{'id':'after','target':'api','tick':4,'latency':1,'copies':1}];d['policy'].update(retries=0,breaker_failures=1,breaker_open_ticks=2);r=simulate(d);self.assertEqual(r['requests']['later']['state'],'failed');self.assertEqual(r['requests']['after']['state'],'success');self.assertTrue(any(e['event']=='rejected' and e['reason']=='circuit_open' for e in r['events']))
 def test_pending_beyond_window_not_success(self):
  d=scenario();d['topology']['ticks']=2;d['topology']['assertions'][0]['max_down_ticks']=2;r=simulate(d);self.assertEqual(r['summary']['pending'],1);self.assertEqual(r['summary']['backend_still_running'],1);self.assertEqual(r['summary']['server_executions'],0)
 def test_retry_scheduled_beyond_window_pending(self):
  d=scenario();d['topology']['ticks']=3;d['topology']['assertions'][0]['max_down_ticks']=3;r=simulate(d);self.assertEqual(r['summary']['pending'],1);self.assertEqual(r['requests']['write']['next_tick'],3)
 def test_topology_missing_assertions_not_green(self):
  d=scenario();d['policy'].update(timeout=4,deduplicate=True);d['topology']['assertions']=[];r=simulate(d);self.assertEqual(r['request_gate'],'pass');self.assertEqual(r['gate'],'no_assertions')
 def test_topology_failure_not_hidden(self):
  d=scenario();d['policy'].update(timeout=4,deduplicate=True);d['topology']['outages']=[{'node':'api','start':8,'end':10}];d['topology']['assertions'][0]['max_down_ticks']=0;r=simulate(d);self.assertEqual(r['request_gate'],'pass');self.assertEqual(r['gate'],'fail')
 def test_report_distinguishes_overall_request_and_topology_gates(self):
  d=scenario();d['policy'].update(timeout=4,deduplicate=True);d['topology']['outages']=[{'node':'api','start':8,'end':10}];d['topology']['assertions'][0]['max_down_ticks']=0
  raw=core.canonical(d);report=requests.compare(raw,raw);text=requests.outputs(report)['review.md'].decode()
  self.assertIn('Left overall gate: fail; right overall gate: fail',text)
  self.assertIn('Left request gate: pass; right request gate: pass',text)
  self.assertIn('Left topology gate: fail; right topology gate: fail',text)
 def test_fair_comparison_rejects_workload_assertion_changes(self):
  left=scenario()
  for mutate in (lambda x:x['requests'][0].update(copies=1),lambda x:x['requests'][0].update(latency=1),lambda x:x['assertions'].update(max_failed=100),lambda x:x['topology'].update(ticks=11)):
   right=copy.deepcopy(left);mutate(right)
   with self.assertRaises(core.Invalid):requests.compare(core.canonical(left),core.canonical(right))
 def test_input_order_does_not_change_simulation(self):
  d=scenario();d['requests']+=[{'id':'another','target':'api','tick':0,'latency':1,'copies':1}];other=copy.deepcopy(d);other['requests'].reverse();self.assertEqual(simulate(d),simulate(other))
 def test_bounds_types_unknowns(self):
  for mutate in (lambda x:x['policy'].update(timeout=True),lambda x:x['policy'].update(retries=6),lambda x:x['policy'].update(deduplicate=1),lambda x:x['requests'][0].update(latency=0),lambda x:x['requests'][0].update(copies=4),lambda x:x['requests'][0].update(target='missing'),lambda x:x['requests'].append(x['requests'][0]),lambda x:x.update(extra='secret')):
   d=scenario();mutate(d)
   with self.assertRaises(core.Invalid):requests.load(core.canonical(d))
 def test_bundle_recompute_and_tamper(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);d=scenario();(p/'left').write_bytes(core.canonical(d));d['policy'].update(timeout=4,deduplicate=True);(p/'right').write_bytes(core.canonical(d));r=requests.build(p/'left',p/'right',p/'out');self.assertEqual(requests.verify(p/'out'),r);self.assertEqual(r['delta_right_minus_left']['server_executions'],-3)
   (p/'out'/'report.json').write_bytes(b'{}')
   with self.assertRaises(core.Invalid):requests.verify(p/'out')
 def test_exhaustive_single_request_against_closed_form(self):
  # Independent arithmetic oracle, not a second event-loop implementation.
  import itertools
  for ticks,latency,timeout,retries,backoff,dedupe in itertools.product((1,3,5,8),range(1,5),range(1,5),range(3),(1,2),(False,True)):
   d=scenario();d['topology']['ticks']=ticks;d['topology']['assertions'][0]['max_down_ticks']=ticks;d['requests'][0]['latency']=latency;d['policy'].update(timeout=timeout,retries=retries,backoff=backoff,deduplicate=dedupe)
   observed=simulate(d);starts=[0] if latency<=timeout else [i*(timeout+backoff) for i in range(retries+1) if i*(timeout+backoff)<ticks]
   completions=sum(t+latency<ticks for t in starts);effects=min(1,completions) if dedupe else 2*completions
   success=int(latency<=timeout and latency<ticks);failed=int(latency>timeout and retries*(timeout+backoff)+timeout<ticks)
   self.assertEqual(observed['summary']['server_executions'],effects);self.assertEqual(observed['summary']['success'],success);self.assertEqual(observed['summary']['failed'],failed);self.assertEqual(observed['summary']['pending'],1-success-failed);self.assertEqual(observed['summary']['admitted_attempts'],len(starts))
 def test_real_cli_gate_and_no_overwrite(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t);(p/'left').write_bytes(core.canonical(scenario()));d=scenario();d['policy'].update(timeout=4,deduplicate=True);(p/'right').write_bytes(core.canonical(d))
   for args in [['request-compare',str(p/'left'),str(p/'right'),'--out',str(p/'out')],['request-verify',str(p/'out')]]:
    r=subprocess.run([sys.executable,'-m','cascade_ledger',*args,'--require-pass'],cwd=ROOT,capture_output=True);self.assertEqual(r.returncode,0,r.stderr)
   with self.assertRaises(FileExistsError):requests.build(p/'left',p/'right',p/'out')
   r=subprocess.run([sys.executable,'-m','cascade_ledger','request-compare',str(p/'left'),str(p/'left'),'--out',str(p/'bad'),'--require-pass'],cwd=ROOT,capture_output=True);self.assertEqual(r.returncode,3)
