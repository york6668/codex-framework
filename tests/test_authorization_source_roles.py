"""Synthetic role controls; these records cannot authenticate a real caller."""
import hashlib
import unittest

import execution_check as check
import source_evidence as sources


class AuthorizationSourceRoleTests(unittest.TestCase):
    def fixture(self, kind='authorization'):
        body = 'Synthetic fixture direct-user instruction'
        item = {'id': 'fixture-message', 'type': 'userMessage', 'text': body}
        record = {'kind': kind, 'source': 'codex', 'task_ref': 'codex:thread:fixture-thread',
                  'native_item_id': item['id'], 'digest': hashlib.sha256(body.encode()).hexdigest()}
        context = {'native_sources': [{'schemaVersion': 1, 'thread': {'id': 'fixture-thread'}, 'turns': [{'items': [item]}]}]}
        return context, record, item

    def test_delegation_cannot_supply_authorization_or_confirmation(self):
        for kind in ('authorization', 'confirmation'):
            for structured in (False, True):
                with self.subTest(kind=kind, structured=structured):
                    context, record, item = self.fixture(kind)
                    if structured:
                        item['content'] = [{'type': 'text', 'text': item['text'],
                                            'codexDelegation': {'sourceThreadId': 'fixture-parent', 'input': item['text']}}]
                    else:
                        item['text'] = '<codex_delegation>synthetic quoted user approval</codex_delegation>'
                        record['digest'] = hashlib.sha256(item['text'].encode()).hexdigest()
                    self.assertFalse(sources.verify(context, {}, record))
                    self.assertFalse(check.direct_user_message(item, item['text']))

    def test_direct_user_confirmation_and_authorization_keep_their_existing_source_relation(self):
        for kind in ('authorization', 'confirmation'):
            context, record, item = self.fixture(kind)
            self.assertTrue(sources.verify(context, {}, record))
            self.assertTrue(check.direct_user_message(item, item['text']))

    def test_assistant_statement_is_not_direct_user_evidence(self):
        for kind in ('authorization', 'confirmation'):
            context, record, item = self.fixture(kind)
            item['type'] = 'agentMessage'
            self.assertFalse(sources.verify(context, {}, record))

    def test_delegated_goal_can_define_work_without_granting_permission(self):
        context, record, item = self.fixture('goal')
        item['content'] = [{'type': 'text', 'text': item['text'],
                            'codexDelegation': {'sourceThreadId': 'fixture-parent', 'input': item['text']}}]
        self.assertTrue(sources.verify(context, {}, record))
        self.assertTrue(check.received_request(item, item['text']))
        record['kind'] = 'authorization'
        self.assertFalse(sources.verify(context, {}, record))

    def test_malformed_content_keeps_structured_error_contract(self):
        context, record, item = self.fixture()
        item['content'] = {}
        with self.assertRaisesRegex(ValueError, 'content must be an array'):
            sources.verify(context, {}, record)
        with self.assertRaisesRegex(check.CheckError, 'content must be an array'):
            check.direct_user_message(item, item['text'])

    def test_source_digest_is_still_required_after_direct_user_filter(self):
        context, record, item = self.fixture('confirmation')
        record['digest'] = hashlib.sha256(b'wrong body').hexdigest()
        self.assertFalse(sources.verify(context, {}, record))
        record.pop('digest')
        self.assertFalse(sources.verify(context, {}, record))
