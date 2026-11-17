"""Original synthetic model tests; no network, production system or vendor data."""
import copy,json,os,random,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from cascade_ledger.core import *
ROOT=Path(__file__).resolve().parents[1]
LEFT=(ROOT/'examples/single.json').read_bytes()
RIGHT=(ROOT/'examples/redundant.json').read_bytes()
def node(name,providers=(),fail=1,recover=1):return {'id':name,'fail_after':fail,'recover_after':recover,'groups':[{'id':'deps','providers':list(providers),'minimum':1}] if providers else []}
def model(nodes,ticks=8,outages=None,assertions=None):return {'schema':1,'ticks':ticks,'nodes':nodes,'outages':outages or [],'assertions':assertions or []}
def outage(name,start,end):return {'node':name,'start':start,'end':end}
def states(result,name):return [r['up'] for r in result['trace'] if r['node']==name]
class Tests(unittest.TestCase):
    def test_hand_derived_chain(self):
        r=simulate(load(LEFT))
        self.assertEqual(states(r,'db-a'),[True,False,False,False,True,True,True,True,True,True])
        self.assertEqual(states(r,'api'),[True,True,False,False,False,True,True,True,True,True])
        self.assertEqual(states(r,'web'),[True,True,True,False,False,False,True,True,True,True])
        self.assertEqual(r['gate'],'fail')
    def test_redundancy_same_challenge(self):
        r=compare(LEFT,RIGHT);self.assertEqual(r['right']['gate'],'pass')
        self.assertEqual(r['down_tick_delta_right_minus_left'],{'api':-3,'db-a':0,'db-b':0,'web':-3})
        self.assertEqual(r['changed_nodes'],['api'])
    def test_failure_grace_filters_short_loss(self):
        r=compare(LEFT,(ROOT/'examples/grace.json').read_bytes())
        self.assertEqual(r['right']['nodes']['api']['down_ticks'],0);self.assertEqual(r['right']['gate'],'pass')
        self.assertEqual([x['cause'] for x in r['right']['trace'] if x['node']=='api'][2:5],['failure_grace']*3)
    def test_recovery_delay_and_exclusive_end(self):
        m=model([node('a',recover=3),node('b',['a'])],outages=[outage('a',0,2)])
        r=simulate(m);self.assertEqual(states(r,'a'),[False,False,False,False,True,True,True,True])
        self.assertEqual(states(r,'b'),[True,False,False,False,False,True,True,True])
    def test_repeated_fault_resets_recovery_streak(self):
        m=model([node('a',recover=3)],outages=[outage('a',0,1),outage('a',2,3)])
        self.assertEqual(states(simulate(m),'a'),[False,False,False,False,False,True,True,True])
    def test_good_dependency_tick_resets_failure_streak(self):
        m=model([node('a'),node('b',['a'],fail=2)],outages=[outage('a',0,1),outage('a',2,3)])
        self.assertTrue(all(states(simulate(m),'b')))
    def test_quorum_and_all_groups_required(self):
        c=node('c',['a','b']);c['groups'][0]['minimum']=2
        m=model([node('a'),node('b'),c],outages=[outage('a',0,1)])
        self.assertFalse(states(simulate(m),'c')[1])
        c['groups'][0]['minimum']=1;c['groups'].append({'id':'second','providers':['a'],'minimum':1})
        self.assertFalse(states(simulate(m),'c')[1])
    def test_cycle_latches_not_assumed_to_recover(self):
        m=model([node('a',['b']),node('b',['a'])],outages=[outage('a',0,2)])
        r=simulate(m);self.assertFalse(r['nodes']['a']['up_at_end']);self.assertFalse(r['nodes']['b']['up_at_end'])
    def test_initial_up_and_forced_priority(self):
        r=simulate(model([node('a',['b'],fail=100),node('b')],ticks=2,outages=[outage('a',0,1),outage('b',0,1)]))
        self.assertEqual(states(r,'a'),[False,False]);self.assertEqual(r['trace'][0]['cause'],'forced')
    def test_horizon_truncation_explicit(self):
        r=simulate(model([node('a',recover=20)],ticks=4,outages=[outage('a',1,2)]))
        self.assertEqual(r['nodes']['a'],{'down_ticks':3,'first_down_tick':1,'up_at_end':False})
    def test_empty_assertions_not_pass(self):
        r=simulate(model([node('a')]));self.assertEqual(r['gate'],'no_assertions')
    def test_up_at_end_false_is_no_recovery_requirement(self):
        a={'node':'a','max_down_ticks':8,'up_at_end':False}
        r=simulate(model([node('a')],outages=[outage('a',0,8)],assertions=[a]));self.assertEqual(r['gate'],'pass')
        a['up_at_end']=True;self.assertEqual(simulate(model([node('a')],outages=[outage('a',0,8)],assertions=[a]))['gate'],'fail')
    def test_input_node_order_does_not_change_simulation(self):
        m=load(LEFT);expected=simulate(m);m['nodes'].reverse();self.assertEqual(simulate(m),expected)
    def test_small_random_chains_independent_shift_oracle(self):
        rng=random.Random(711)
        for _ in range(50):
            length=rng.randint(1,8);start=rng.randint(0,6);end=rng.randint(start+1,10)
            m=model([node('n'+str(i),['n'+str(i-1)] if i else []) for i in range(length)],ticks=20,outages=[outage('n0',start,end)])
            r=simulate(m)
            for i in range(length):self.assertEqual(states(r,'n'+str(i)),[not(start+i<=t<end+i) for t in range(20)])
    def test_scenario_comparison_rejects_easier_incident_or_gate(self):
        for key in ('ticks','nodes','outages','assertions'):
            m=load(RIGHT)
            if key=='ticks':m[key]=11
            elif key=='nodes':m[key].append(node('extra'))
            elif key=='outages':m[key][0]['end']=3
            else:m[key][0]['max_down_ticks']=1
            with self.subTest(key=key),self.assertRaises(Invalid):compare(LEFT,canonical(m))
    def test_schema_bool_and_numeric_bounds(self):
        for key,val in [('schema',True),('ticks',False),('ticks',0),('ticks',201),('ticks',10**500),('ticks',1.0)]:
            m=load(LEFT);m[key]=val
            with self.assertRaises(Invalid):load(canonical(m))
    def test_node_validation(self):
        for change in [lambda n:n.update(fail_after=True),lambda n:n.update(recover_after=0),lambda n:n.update(id='=FORMULA'),lambda n:n.update(extra=1),lambda n:n.update(groups={})]:
            m=load(LEFT);change(m['nodes'][0])
            with self.assertRaises(Invalid):load(canonical(m))
    def test_dependency_validation(self):
        for providers,minimum in [(['unknown'],1),(['api'],1),(['db-a','db-a'],1),([],1),(['db-a'],0),(['db-a'],True),(['db-a'],2)]:
            m=load(LEFT);m['nodes'][2]['groups'][0].update(providers=providers,minimum=minimum)
            with self.assertRaises(Invalid):load(canonical(m))
    def test_duplicate_nodes_and_groups(self):
        m=load(LEFT);m['nodes'].append(m['nodes'][0])
        with self.assertRaises(Invalid):load(canonical(m))
        m=load(LEFT);m['nodes'][2]['groups']*=2
        with self.assertRaises(Invalid):load(canonical(m))
    def test_outage_bounds_and_overlap(self):
        for intervals in [[outage('db-a',1,1)],[outage('db-a',-1,2)],[outage('db-a',1,11)],[outage('db-a',1,4),outage('db-a',3,5)],[outage('missing',1,2)]]:
            m=load(LEFT);m['outages']=intervals
            with self.assertRaises(Invalid):load(canonical(m))
    def test_assertion_bounds_and_duplicates(self):
        for assertions in [[{'node':'web','max_down_ticks':0,'up_at_end':1}],[{'node':'missing','max_down_ticks':0,'up_at_end':True}],load(LEFT)['assertions']*2]:
            m=load(LEFT);m['assertions']=assertions
            with self.assertRaises(Invalid):load(canonical(m))
    def test_duplicate_keys_depth_nonfinite_encoding(self):
        for raw in [b'{"schema":1,"schema":1}',b'['*20000+b'0'+b']'*20000,b'{"v":1e999}',b'{"v":NaN}',b'"\\ud800"','{}'.encode('utf-16')]:
            with self.assertRaises(Invalid):load(raw)
    def test_unknown_fields(self):
        m=load(LEFT);m['secret']='SYNTHETIC'
        with self.assertRaises(Invalid):load(canonical(m))
    def test_maximum_bound_output_roundtrip(self):
        nodes=[node('node'+str(i)+'x'*40) for i in range(50)]
        for i,n in enumerate(nodes):n['groups']=[{'id':'group'+str(j)+'x'*40,'providers':[nodes[(i+j+1)%50]['id']],'minimum':1} for j in range(10)]
        m=model(nodes,ticks=200,outages=[outage(n['id'],0,200) for n in nodes])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'s';source.write_bytes(canonical(m));r=build(source,source,root/'b');self.assertEqual(len(r['left']['trace']),10000);self.assertEqual(verify(root/'b'),r)
    def test_reference_limit(self):
        nodes=[node('node'+str(i)) for i in range(50)]
        for i,n in enumerate(nodes):n['groups']=[{'id':'g','providers':[x['id'] for x in nodes if x['id']!=n['id']],'minimum':1}]
        with self.assertRaises(Invalid):load(canonical(model(nodes)))
    def test_fixed_output_bytes_and_raw_sources(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);left=root/'left';left.write_bytes(LEFT.replace(b'\n',b'\r\n'))
            with patch.object(Path,'write_text',side_effect=AssertionError('newline translation')):r=build(left,ROOT/'examples/redundant.json',root/'b')
            self.assertEqual((root/'b/left.json').read_bytes(),left.read_bytes());self.assertEqual((root/'b/report.json').read_bytes(),canonical(r));self.assertEqual(verify(root/'b'),r)
    def test_tamper_extra_and_no_overwrite(self):
        for name in ['left.json','right.json','report.json','review.md','timeline.csv','extra.txt']:
            with tempfile.TemporaryDirectory() as d:
                out=Path(d)/'b';build(ROOT/'examples/single.json',ROOT/'examples/redundant.json',out)
                p=out/name;p.write_bytes((p.read_bytes() if p.exists() else b'')+b'\n')
                with self.assertRaises(Invalid):verify(out)
                with self.assertRaises(FileExistsError):build(ROOT/'examples/single.json',ROOT/'examples/redundant.json',out)
    def test_preflight_report_limit_leaves_no_folder(self):
        with tempfile.TemporaryDirectory() as d,patch('cascade_ledger.core.OUTPUT_LIMIT',1):
            out=Path(d)/'b'
            with self.assertRaises(Invalid):build(ROOT/'examples/single.json',ROOT/'examples/redundant.json',out)
            self.assertFalse(out.exists())
    def test_regular_file_and_size_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'file';p.write_bytes(b'x'*30)
            self.assertEqual(read(p,30),b'x'*30)
            with self.assertRaises(Invalid):read(p,29)
            with self.assertRaises(Invalid):read(Path(d))
    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'link'
            try:p.symlink_to(ROOT/'examples/single.json')
            except OSError:self.skipTest('OS disallows symlink creation')
            with self.assertRaises(Invalid):read(p)
    @unittest.skipUnless(hasattr(os,'mkfifo'),'POSIX FIFO unavailable')
    def test_fifo_cli_bounded_no_private_error(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'SYNTHETIC_SECRET';os.mkfifo(p)
            r=subprocess.run([sys.executable,'-m','cascade_ledger','compare',str(p),str(p),'--out',str(Path(d)/'out')],cwd=ROOT,capture_output=True,text=True,timeout=2)
            self.assertEqual(r.returncode,2);self.assertNotIn('SYNTHETIC_SECRET',r.stderr);self.assertNotIn('Traceback',r.stderr)
    def test_cli_gate_exit_and_verified_failure(self):
        for candidate,gate,code in [('single.json',True,3),('redundant.json',True,0),('single.json',False,0)]:
            with tempfile.TemporaryDirectory() as d:
                out=Path(d)/'b'
                commands=[['compare','examples/single.json','examples/'+candidate,'--out',str(out)],['verify',str(out)]]
                for args in commands:
                    if gate:args+=['--require-pass']
                    r=subprocess.run([sys.executable,'-m','cascade_ledger',*args],cwd=ROOT,capture_output=True,text=True)
                    self.assertEqual(r.returncode,code,r.stderr)
    def test_no_assertions_cli_gate_not_silent_pass(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'m';p.write_bytes(canonical(model([node('a')])))
            r=subprocess.run([sys.executable,'-m','cascade_ledger','compare',str(p),str(p),'--out',str(root/'b'),'--require-pass'],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(r.returncode,3)
if __name__=='__main__':unittest.main()
