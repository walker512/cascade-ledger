import argparse,json,sys
from . import requests
from .core import build,verify,Invalid

def main():
    p=argparse.ArgumentParser(description='Compare declared failure models without contacting systems.')
    commands=p.add_subparsers(dest='command',required=True)
    c=commands.add_parser('compare');c.add_argument('left');c.add_argument('right');c.add_argument('--out',required=True);c.add_argument('--require-pass',action='store_true')
    v=commands.add_parser('verify');v.add_argument('bundle');v.add_argument('--require-pass',action='store_true')
    c=commands.add_parser('request-compare');c.add_argument('left');c.add_argument('right');c.add_argument('--out',required=True);c.add_argument('--require-pass',action='store_true')
    v=commands.add_parser('request-verify');v.add_argument('bundle');v.add_argument('--require-pass',action='store_true')
    args=p.parse_args()
    try:
        if args.command.startswith('request-'):
            report=requests.build(args.left,args.right,args.out) if args.command=='request-compare' else requests.verify(args.bundle)
            print(json.dumps({'consistent':True,'left_gate':report['left']['gate'],'right_gate':report['right']['gate'],'left_topology_gate':report['left']['topology_gate'],'right_topology_gate':report['right']['topology_gate'],'request_delta_right_minus_left':report['delta_right_minus_left'],'scope':report['scope']}))
            return 3 if args.require_pass and report['right']['gate']!='pass' else 0
        report=build(args.left,args.right,args.out) if args.command=='compare' else verify(args.bundle)
        print(json.dumps({'consistent':True,'left_gate':report['left']['gate'],'right_gate':report['right']['gate'],'down_tick_delta_right_minus_left':report['down_tick_delta_right_minus_left'],'scope':report['scope']}))
        return 3 if args.require_pass and report['right']['gate']!='pass' else 0
    except (Invalid,OSError,ValueError,RecursionError):
        print('Cannot process scenario or bundle: invalid input, inconsistent comparison, size limit or output I/O failure. No input contents are echoed.',file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
