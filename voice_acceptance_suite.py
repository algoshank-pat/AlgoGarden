"""Run every voice/API acceptance scenario with per-scenario results."""
from voice_test_harness import execute_suite
from voice_test_harness import CASES
import argparse


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',action='store_true',help='List scenario groups without running models or creating test bookings.')
    args=parser.parse_args()
    if args.list:
        for name,_,_ in CASES:print(name)
        return 0
    delivered={}
    def progress(summary):
        for item in summary['scenarios']:
            if delivered.get(item['name'])!=item['status']:
                delivered[item['name']]=item['status']
                print(item['status'].upper()+': '+item['name'],flush=True)
    result=execute_suite(progress)
    print('Suite: '+result['status'].upper()+' — results/voice-suite/latest.json',flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
