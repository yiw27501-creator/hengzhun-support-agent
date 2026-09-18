"""Run three isolated sandbox journeys, no paid API requests."""
import json
from pathlib import Path
from datetime import datetime, timezone
from engine import Engine


def main():
    directory = Path(__file__).parent / 'data' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True)
    engine = Engine(directory / 'demo.db')
    engine.seed()
    report = {'environment': 'sandbox', 'planner': 'rule-fixture-1', 'paid_api_calls': 0, 'journeys': []}
    for scenario in ['normal', 'conflict', 'tool_failure']:
        task = engine.create('demo-' + scenario, scenario)
        states = [task['state']]
        task = engine.run(task['id'])
        states.append(task['state'])
        if scenario == 'conflict':
            task = engine.confirm(task['id'], 'refund', 'demo-operator', 'sandbox-message-1',
                                  '模拟人工核对原话并确认客户当前选择退款；未联系真实客户')
            states.append(task['state'])
            task = engine.run(task['id'])
            states.append(task['state'])
        if scenario == 'tool_failure':
            task = engine.run(task['id'])
            states.append(task['state'])
        report['journeys'].append(dict(scenario=scenario, states=states, task=task))
        print(scenario, ' -> '.join(states))
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report:', directory / 'report.json')


if __name__ == '__main__':
    main()
