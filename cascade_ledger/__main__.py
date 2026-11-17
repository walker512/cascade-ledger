import argparse,json,sys
from .core import build,verify,Invalid

def main():
    p=argparse.ArgumentParser(description='Compare declared failure models without contacting systems.')
    commands=p.add_subparsers(dest='command',required=True)
    c=commands.add_parser('compare');c.add_argument('left');c.add_argument('right');c.add_argument('--out',required=True);c.add_argument('--require-pass',action='store_true')
    v=commands.add_parser('verify');v.add_argument('bundle');v.add_argument('--require-pass',action='store_true')
    args=p.parse_args()
    try:
        report=build(args.left,args.right,args.out) if args.command=='compare' else verify(args.bundle)
        print(json.dumps({'consistent':True,'left_gate':report['left']['gate'],'right_gate':report['right']['gate'],'down_tick_delta_right_minus_left':report['down_tick_delta_right_minus_left'],'scope':report['scope']}))
        return 3 if args.require_pass and report['right']['gate']!='pass' else 0
    except (Invalid,OSError,ValueError,RecursionError):
        print('Cannot process scenario or bundle: invalid input, inconsistent comparison, size limit or output I/O failure. No input contents are echoed.',file=sys.stderr);return 2
if __name__=='__main__':sys.exit(main())
