"""Bounded synthetic request attempts layered on declared topology health."""
import hashlib
from pathlib import Path
from . import core
SCOPE='Declared tick/request model only, not measured performance, production chaos, durable idempotency proof or a deployment recommendation.'
def load(raw):
 d=core.parse(raw);core.fields(d,['schema','topology','requests','policy','assertions']);core.integer(d['schema'],1,1)
 base=core.load(core.canonical(d['topology']));ids={n['id'] for n in base['nodes']}
 if type(d['requests']) is not list or not 1<=len(d['requests'])<=100:raise core.Invalid('Expected 1..100 requests.')
 seen=set()
 for r in d['requests']:
  core.fields(r,['id','target','tick','latency','copies']);core.identifier(r['id']);core.identifier(r['target'])
  if r['id'] in seen or r['target'] not in ids:raise core.Invalid('Duplicate request or unknown target.')
  seen.add(r['id']);core.integer(r['tick'],0,base['ticks']-1);core.integer(r['latency'],1,200);core.integer(r['copies'],1,3)
 p=d['policy'];core.fields(p,['timeout','retries','backoff','start_limit','breaker_failures','breaker_open_ticks','deduplicate'])
 for k,lo,hi in [('timeout',1,200),('retries',0,5),('backoff',1,200),('start_limit',1,100),('breaker_failures',0,100),('breaker_open_ticks',1,200)]:core.integer(p[k],lo,hi)
 if type(p['deduplicate']) is not bool:raise core.Invalid('Deduplication assumption must be boolean.')
 a=d['assertions'];core.fields(a,['max_failed','max_pending','max_executions'])
 for k,hi in [('max_failed',100),('max_pending',100),('max_executions',1800)]:core.integer(a[k],0,hi)
 return d

def simulate(d):
 base=core.simulate(d['topology']);ticks=d['topology']['ticks'];p=d['policy'];health={(r['node'],r['tick']):r['up'] for r in base['trace']}
 requests={r['id']:r for r in d['requests']};states={k:{'state':'pending','next_tick':r['tick'],'attempts':0,'finished_tick':None} for k,r in requests.items()}
 breakers={n['id']:{'failures':0,'open_until':-1} for n in d['topology']['nodes']};attempts=[];events=[];executions={k:0 for k in requests};committed=set()
 def event(t,kind,rid,number,**extra):events.append({'tick':t,'event':kind,'request':rid,'attempt':number,**extra})
 def fail(t,rid,attempt,reason,observed):
  state=states[rid];node=requests[rid]['target'];b=breakers[node]
  if observed:
   b['failures']+=1
   if p['breaker_failures'] and b['failures']>=p['breaker_failures']:
    b['open_until']=t+p['breaker_open_ticks'];event(t,'breaker_open',rid,attempt,until=b['open_until'],target=node)
  if state['attempts']<=p['retries']:
   state['next_tick']=t+p['backoff'];event(t,'retry_scheduled',rid,attempt,due=state['next_tick'],reason=reason)
  else:state.update(state='failed',next_tick=None,finished_tick=t)
 for tick in range(ticks):
  for b in breakers.values():
   if b['open_until']==tick:b['failures']=0
  # Completed backend operations precede deadline checks. Equality succeeds.
  for a in attempts:
   if a['backend_state']!='running' or a['complete_tick']!=tick:continue
   rid=a['request'];r=requests[rid];ok=all(health[(r['target'],t)] for t in range(a['start_tick'],tick+1));a['backend_state']='success' if ok else 'failure'
   effects=0
   if ok:
    effects=0 if p['deduplicate'] and rid in committed else 1 if p['deduplicate'] else r['copies'];committed.add(rid);executions[rid]+=effects
   event(tick,'backend_complete',rid,a['number'],success=ok,executions=effects,late=a['response']!='waiting')
   if a['response']=='waiting':
    if ok:
     a['response']='success';states[rid].update(state='success',next_tick=None,finished_tick=tick);breakers[r['target']]['failures']=0
    else:a['response']='failure';fail(tick,rid,a['number'],'backend_failure',True)
  for a in attempts:
   if a['response']=='waiting' and a['deadline']==tick:
    a['response']='timeout';event(tick,'timeout',a['request'],a['number']);fail(tick,a['request'],a['number'],'timeout',True)
  starts={n:0 for n in breakers}
  for rid in sorted(requests):
   r=requests[rid];state=states[rid]
   if state['state']!='pending' or state['next_tick']!=tick:continue
   number=state['attempts'];state['attempts']+=1;state['next_tick']=None;node=r['target']
   blocked='circuit_open' if tick<breakers[node]['open_until'] else 'rate_limit' if starts[node]>=p['start_limit'] else None
   if blocked:
    event(tick,'rejected',rid,number,reason=blocked);fail(tick,rid,number,blocked,False);continue
   starts[node]+=1;a={'request':rid,'number':number,'start_tick':tick,'deadline':tick+p['timeout'],'complete_tick':tick+r['latency'],'backend_state':'running','response':'waiting'};attempts.append(a);event(tick,'started',rid,number,target=node,copies=r['copies'])
 summary={'success':sum(s['state']=='success' for s in states.values()),'failed':sum(s['state']=='failed' for s in states.values()),'pending':sum(s['state']=='pending' for s in states.values()),'server_executions':sum(executions.values()),'backend_still_running':sum(a['backend_state']=='running' for a in attempts),'admitted_attempts':len(attempts),'rejected_attempts':sum(e['event']=='rejected' for e in events)}
 checks={key:{'observed':summary[field],'maximum':d['assertions'][key],'passed':summary[field]<=d['assertions'][key]} for key,field in [('max_failed','failed'),('max_pending','pending'),('max_executions','server_executions')]}
 return {'summary':summary,'requests':{k:{**states[k],'server_executions':executions[k]} for k in sorted(states)},'attempts':attempts,'events':events,'assertions':checks,'request_gate':'pass' if all(c['passed'] for c in checks.values()) else 'fail','gate':base['gate'] if base['gate']!='pass' else 'pass' if all(c['passed'] for c in checks.values()) else 'fail','topology_gate':base['gate']}

def compare(left_raw,right_raw):
 left,right=load(left_raw),load(right_raw)
 topology=core.compare(core.canonical(left['topology']),core.canonical(right['topology']))
 def same(d):return {'requests':sorted(d['requests'],key=lambda r:r['id']),'assertions':d['assertions']}
 if core.canonical(same(left))!=core.canonical(same(right)):raise core.Invalid('Compare identical request workload and assertions.')
 a,b=simulate(left),simulate(right)
 return {'schema':1,'semantics':'request-start-health-through-completion-v1','input_sha256':{'left':hashlib.sha256(left_raw).hexdigest(),'right':hashlib.sha256(right_raw).hexdigest()},'changed_policy_fields':[k for k in sorted(left['policy']) if left['policy'][k]!=right['policy'][k]],'changed_nodes':topology['changed_nodes'],'left':a,'right':b,'delta_right_minus_left':{k:b['summary'][k]-a['summary'][k] for k in a['summary']},'scope':SCOPE}

def outputs(report):
 lines=['# Request-policy comparison','',SCOPE,'','Equal workload, outages, observation window and assertions. Policies and declared topology may differ.','',f"Left overall gate: {report['left']['gate']}; right overall gate: {report['right']['gate']}",f"Left request gate: {report['left']['request_gate']}; right request gate: {report['right']['request_gate']}",f"Left topology gate: {report['left']['topology_gate']}; right topology gate: {report['right']['topology_gate']}",'','| Metric | Left | Right | Delta |','|---|---:|---:|---:|']
 for k in report['delta_right_minus_left']:lines.append(f"| {k} | {report['left']['summary'][k]} | {report['right']['summary'][k]} | {report['delta_right_minus_left'][k]} |")
 lines+=['','See report.json for every admitted attempt, rejection, timeout, retry and backend completion. Pending work is not inferred successful. A timeout does not cancel backend execution. Deduplication is an idealized assumption, not a tested implementation. The overall gate requires both request assertions and topology assertions to pass; missing topology assertions yield no_assertions.','']
 return {'report.json':core.canonical(report),'review.md':'\n'.join(lines).encode('utf8')}

def build(left,right,out):
 raw=[core.read(left),core.read(right)];report=compare(*raw);files=outputs(report)
 if any(len(v)>core.OUTPUT_LIMIT for v in files.values()):raise core.Invalid('Request report exceeds bound.')
 files.update(dict(zip(('left.json','right.json'),raw)));root=Path(out);root.mkdir(exist_ok=False)
 for k,v in files.items():(root/k).write_bytes(v)
 return report

def verify(folder):
 root=Path(folder)
 if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()}!={'left.json','right.json','report.json','review.md'}:raise core.Invalid('Unexpected request bundle.')
 report=compare(core.read(root/'left.json'),core.read(root/'right.json'))
 for k,v in outputs(report).items():
  if core.read(root/k,core.OUTPUT_LIMIT)!=v:raise core.Invalid('Request bundle differs from recomputation.')
 return report
