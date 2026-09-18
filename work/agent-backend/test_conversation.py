import copy
import unittest
from conversation import validate_messages, validate_extraction


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.messages = [dict(id='m1', role='customer', text='我决定退款')]
        self.result = dict(intent='refund', needs_clarification=False, question='',
                           evidence=[dict(message_id='m1', quote='决定退款')], reason='明确退款')

    def test_valid(self):
        validate_messages(self.messages)
        self.assertTrue(validate_extraction(self.result, self.messages))

    def test_invalid_inputs(self):
        for messages in ([], [dict(id='m', role=[], text='x')], self.messages*2,
                         [dict(id='m', role='system', text='x')]):
            with self.assertRaises(ValueError): validate_messages(messages)

    def test_fabricated_quote(self):
        self.result['evidence'][0]['quote'] = '我同意换货'
        self.assertFalse(validate_extraction(self.result, self.messages))

    def test_agent_is_not_customer(self):
        self.messages[0]['role'] = 'agent'
        self.assertFalse(validate_extraction(self.result, self.messages))

    def test_no_customer_requires_abstention(self):
        result = dict(intent='undecided', needs_clarification=True, question='您希望怎样处理？', evidence=[], reason='无客户消息')
        self.assertTrue(validate_extraction(result, [dict(id='a', role='agent', text='可以退款')]))

    def test_inconsistent_or_extra_fields(self):
        for key, value in [('needs_clarification', True), ('question', '确认吗'), ('intent', []), ('execution_authorized', True)]:
            result = copy.deepcopy(self.result)
            result[key] = value
            self.assertFalse(validate_extraction(result, self.messages))

    def test_quote_validation_is_not_semantic_authorization(self):
        # Exact quotes cannot prove recency or intent correctness; semantic evaluation remains necessary.
        messages = self.messages + [dict(id='m2', role='customer', text='先别退，我再想想')]
        self.assertTrue(validate_extraction(self.result, messages))


if __name__ == '__main__': unittest.main()
