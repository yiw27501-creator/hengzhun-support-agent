import unittest
from evaluate_retrieval import evaluate

class Stub:
    def retrieve(self, query): return [{'id':'a'}, {'id':'b'}]

class RetrievalEvalTests(unittest.TestCase):
    def test_multilabel_recall_and_unknown(self):
        report=evaluate([dict(query='x', relevant=['b','c']),dict(query='y',relevant=[])],Stub())
        self.assertEqual(report['metrics']['macro_recall_at_3'],0.5)
        self.assertEqual(report['metrics']['mrr_at_3'],0.5)
        self.assertEqual(report['metrics']['unanswerable_candidate_rate'],1)

    def test_empty_split(self):
        self.assertIsNone(evaluate([],Stub())['metrics']['macro_recall_at_3'])
