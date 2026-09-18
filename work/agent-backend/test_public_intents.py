import unittest
from evaluate_public_intents import metrics

class IntentMetricsTests(unittest.TestCase):
    def test_errors_stay_in_denominator(self):
        rows=[dict(expected='get_refund',prediction='get_refund',correct=True,response_id='id',usage=dict(total_tokens=10),latency_ms=100),
              dict(expected='get_refund',correct=False,error_type='TimeoutError',latency_ms=200)]
        result=metrics(rows)
        self.assertEqual(result['accuracy'],.5)
        self.assertEqual(result['responses'],1)
        self.assertEqual(result['usage']['total_tokens'],10)
        self.assertEqual(result['confusion']['get_refund']['__invalid_or_error__'],1)

    def test_empty(self):
        self.assertIsNone(metrics([])['accuracy'])
