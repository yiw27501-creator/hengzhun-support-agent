import json
import unittest
from experiment import cases, grade


class GraderTests(unittest.TestCase):
    def setUp(self):
        self.case = cases()[0]
        self.answer = dict(self.case['expected'], evidence_ids=['stock'], reason='有可售替代品')

    def test_valid(self):
        self.assertTrue(grade(json.dumps(self.answer), self.case)['passed'])

    def test_invalid_outputs(self):
        for raw in ['null', '[]', '{}', 'not json']:
            self.assertFalse(grade(raw, self.case)['passed'])

    def test_wrong_money_and_evidence(self):
        self.answer.update(amount_minor=999, evidence_ids=['invented'])
        result = grade(json.dumps(self.answer), self.case)
        self.assertIn('amount_minor_mismatch', result['errors'])
        self.assertIn('unknown_evidence_id', result['errors'])

    def test_truncation(self):
        self.assertFalse(grade(json.dumps(self.answer), self.case, 'length')['passed'])

    def test_bool_is_not_amount(self):
        self.answer['amount_minor'] = False
        self.assertFalse(grade(json.dumps(self.answer), self.case)['schema_valid'])

    def test_dataset(self):
        data = cases()
        self.assertEqual(len(data), 30)
        self.assertEqual(len({c['id'] for c in data}), 30)
        self.assertEqual(sum(c['split'] == 'holdout' for c in data), 15)

    def test_conflict_requires_human(self):
        c = cases()[2]
        answer = dict(action='offer_substitute', amount_minor=0, sku='ALT-A',
                      evidence_ids=['customer_text'], reason='接受替代品')
        self.assertFalse(grade(json.dumps(answer), c)['passed'])
        self.assertEqual(c['expected']['action'], 'escalate')

    def test_zero_stock_rejected(self):
        c = cases()[3]
        self.assertFalse(grade(json.dumps(self.answer), c)['passed'])


if __name__ == '__main__':
    unittest.main()
