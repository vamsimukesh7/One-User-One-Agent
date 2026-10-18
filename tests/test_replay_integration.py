"""Full CLI timeline on a tiny invented pack; never used for reported impact."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from test_agent import fixture_pack, event, NOW
from one_agent.common import iso
from one_agent.provider import store_delay
from one_agent.audit import inspect


def write_lines(path, rows):
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf8')


def write_csv(path, rows):
    with path.open('w',newline='',encoding='utf8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


class ReplayIntegration(unittest.TestCase):
    def test_cli_handles_injected_surprises_and_persists_corrected_outcomes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'pack';out=Path(temp)/'run';p=fixture_pack()
            for folder in ['data','config','replay','pilot','simulator']:(root/folder).mkdir(parents=True)
            profiles=p.profiles['test-viewer']+[dict(p.profiles['test-viewer'][0],user_id='holdout')]
            write_csv(root/'data/profiles_snapshot.csv',profiles)
            titles=[dict(t,title=t['title_id'],content_type='show') for t in p.catalogue.values()]
            write_csv(root/'data/catalogue.csv',titles)
            write_csv(root/'data/episodes.csv',[{'episode_id':'episode-'+t['title_id'],'content_slug':t['content_slug'],'season':'1','episode':'1','minutes':'4'} for t in titles])
            (root/'config/goals.json').write_text(json.dumps({'goals':list(p.goals.values())}))
            (root/'config/interventions.json').write_text(json.dumps(p.policy))
            write_csv(root/'replay/arms.csv',[{'user_id':'test-viewer','arm':'agent'},{'user_id':'holdout','arm':'holdout'}])
            history=[event('window-start',user='unused',at=NOW-timedelta(days=30)),event('past',kind='play_started',at=NOW-timedelta(hours=1))]
            write_lines(root/'data/events.jsonl',history)
            controls=[{'type':'goal_rule_corrected','goal_id':'G03','at':iso(NOW+timedelta(minutes=1)), 'new_success':{'event':'play_started'}},
                      {'type':'dependency_unavailable','dependency':'model_provider','at':iso(NOW+timedelta(minutes=1)),'until':iso(NOW+timedelta(minutes=4))},
                      {'type':'title_withdrawn','title_id':'T0','at':iso(NOW+timedelta(minutes=2,seconds=30))},
                      {'type':'dependency_degraded','dependency':'user_data_store','at':iso(NOW+timedelta(minutes=4)),'until':iso(NOW+timedelta(minutes=5))}]
            write_lines(root/'replay/control_events.jsonl',controls)
            failure_at=NOW+timedelta(minutes=4)
            failed=next(event('failure-'+str(i),at=failure_at) for i in range(1000) if store_delay(event('failure-'+str(i),at=failure_at),controls,failure_at)[1])
            triggers=[event(properties={'deeplink':'ott://c/promo/d2/title/T0'}),
                      event(deliver_at=iso(NOW+timedelta(seconds=2))),
                      event('second',at=NOW+timedelta(minutes=2)),
                      event('withdrawal-followup',at=NOW+timedelta(minutes=3)),
                      event('buffered',at=NOW+timedelta(minutes=3,seconds=10),occurred_at=iso(NOW-timedelta(days=2))),
                      event('holdout-trigger',user='holdout',at=NOW+timedelta(seconds=10)),failed]
            write_lines(root/'replay/triggers.jsonl',triggers)
            write_lines(root/'replay/live_events.jsonl',[])
            write_lines(root/'replay/model_faults.jsonl',[{'trigger_event_id':'withdrawal-followup','fault':'malformed','model_output':'not JSON'}])
            write_csv(root/'pilot/pilot_assignment.csv',[{'user_id':'test-viewer','arm':'treatment','served':'true'}, {'user_id':'holdout','arm':'holdout','served':'false'}])
            write_csv(root/'pilot/pilot_outcomes.csv',[{'user_id':'test-viewer','arm':'treatment','succeeded':'true'}, {'user_id':'holdout','arm':'holdout','succeeded':'false'}])
            signals=[event('next-day-play',kind='play_started',at=NOW+timedelta(days=1)),
                     event('next-day-completion',kind='episode_completed',at=NOW+timedelta(days=1,minutes=5))]
            # Deliberately tiny test double: tests the CLI orchestration, not simulator realism.
            simulator="""import argparse,json
from pathlib import Path
p=argparse.ArgumentParser()
for key in ['pack','actions','out','seed']:p.add_argument('--'+key)
a=p.parse_args();out=Path(a.out);out.mkdir()
actions=[json.loads(x) for x in Path(a.actions).read_text().splitlines()]
(out/'outcome_events.jsonl').write_text(SIGNALS)
(out/'simulator_report.json').write_text(json.dumps({'policy_problem_count':0,'counts':{'actions_total':len(actions),'home_page_accepted':len(actions)}}))
""".replace('SIGNALS',repr(''.join(json.dumps(e)+'\n' for e in signals)))
            (root/'simulator/simulate_outcomes.py').write_text(simulator)
            result=subprocess.run([sys.executable,'-m','one_agent','replay','--pack',str(root),'--out',str(out)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            decisions=[json.loads(x) for x in (out/'decisions.jsonl').read_text().splitlines()]
            by_id={d['trigger']['event_id']:d for d in decisions}
            self.assertEqual(len(decisions),6)
            self.assertEqual(by_id['buffered']['action'],'NO_ACTION')
            self.assertEqual(by_id['holdout-trigger']['action'],'NO_ACTION')
            self.assertIn('store_',by_id[failed['event_id']]['no_action_reason'])
            self.assertTrue(any('model_503' in x for x in by_id['second']['provider_notes']))
            self.assertEqual(by_id['withdrawal-followup']['provider_output'],'not JSON')
            audit=[json.loads(x) for x in (out/'audit.jsonl').read_text().splitlines()]
            correction=next(a for a in audit if a['type']=='rule_correction')
            self.assertEqual(len(correction['affected_plan_ids']),1)
            actions=[json.loads(x) for x in (out/'actions.jsonl').read_text().splitlines()]
            self.assertTrue(all(a['user_id']!='holdout' for a in actions))
            later=next(a for a in actions if a['trigger_event_id']=='withdrawal-followup')
            self.assertNotIn('T0',[later['hero_title_id']]+later['rows'][0]['title_ids'])
            before=inspect(out/'state.sqlite3','test-viewer',iso(NOW+timedelta(seconds=30)))
            after=inspect(out/'state.sqlite3','test-viewer',iso(NOW+timedelta(minutes=2)))
            self.assertEqual(before['plans'][0]['success']['event'],'app_open')
            self.assertEqual(after['plans'][0]['success']['event'],'play_started')
            outcomes=[json.loads(x) for x in (out/'outcomes.jsonl').read_text().splitlines()]
            goal=next(o for o in outcomes if o['user_id']=='test-viewer')
            self.assertEqual(goal['status'],'succeeded');self.assertEqual(goal['signal_refs'],['event:next-day-play'])
            effect=json.loads((out/'impact_report.json').read_text())['replay']['primary_completion_72h']
            self.assertEqual(effect['treatment_n'],1);self.assertEqual(effect['holdout_n'],1)
            self.assertEqual(effect['risk_difference'],1)
