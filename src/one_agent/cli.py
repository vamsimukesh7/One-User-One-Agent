import argparse
import json
from pathlib import Path
from .replay import run
from .audit import inspect


def main():
    parser=argparse.ArgumentParser(description='Local evidence-driven viewer agent; no API key or network needed.')
    sub=parser.add_subparsers(dest='command',required=True)
    replay=sub.add_parser('replay',help='Decide, simulate outcomes, judge goals and report randomized impact')
    replay.add_argument('--pack',default='data/one_user_one_agent_pack')
    replay.add_argument('--out',default='runs/latest')
    replay.add_argument('--channel',choices=['home_page','push','whatsapp','in_app_message'])
    replay.add_argument('--seed',default='oua-practice')
    replay.add_argument('--max-model-calls',type=int,default=8)
    replay.add_argument('--max-tool-calls',type=int,default=20)
    audit=sub.add_parser('audit',help='Inspect durable evidence and history for a viewer')
    audit.add_argument('--db',required=True); audit.add_argument('--viewer',required=True)
    audit.add_argument('--at',help='ISO-8601 time with timezone, reconstructing knowledge at that time')
    args=parser.parse_args()
    if args.command=='replay':
        if args.max_model_calls<0 or args.max_tool_calls<0:
            parser.error('Call budgets must be nonnegative')
        try:
            print(json.dumps(run(args.pack,args.out,args.seed,args.max_model_calls,args.max_tool_calls,args.channel),indent=2))
        except (ValueError,FileNotFoundError) as exc:
            parser.exit(2,str(exc)+'\n')
    else:
        if not Path(args.db).is_file():
            parser.error('Database does not exist')
        try:print(json.dumps(inspect(args.db,args.viewer,args.at),indent=2))
        except ValueError as exc:parser.exit(2,str(exc)+'\n')


if __name__=='__main__':
    main()
