import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import profiles_v1
from app.services.llm_override import NodeLlmOverride, use_override
from app.services.observation import digest

PRODUCTION = {'version': 1, 'mode': 'production', 'online_enabled': True, 'endpoint': 'https://api.kie.ai/codex/v1/responses',
              'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                           'max_output_tokens': 1024, 'timeout_s': 30},
              'profiles': {}, 'allowed_actions': []}
REFERENCE = profiles_v1.VIBECODE_REPAIR_PROFILE


def production(candidate=None):
    catalog = {profiles_v1.VIBECODE_REPAIR_PROFILE_ID: candidate or dict(REFERENCE)}
    return (patch('app.services.decision_config.runtime_config', return_value={**PRODUCTION, 'profiles': catalog}),
            patch.object(profiles_v1, 'active_catalog', return_value=catalog))


class VibecodeProfileValidation(unittest.TestCase):
    def validate(self, **changes):
        candidate = {**REFERENCE, **changes}
        cfg, catalog = production(candidate)
        with cfg, catalog:
            profiles_v1.validate_candidate(candidate)

    def test_reference_profile_matches_the_documented_shape(self):
        self.assertEqual(profiles_v1.VIBECODE_REPAIR_PROFILE_ID, 'vibecode-json-repair-v1')
        self.assertEqual((REFERENCE['provider'], REFERENCE['reasoning_effort'], REFERENCE['max_output_tokens'],
                          REFERENCE['prompt_variant']), ('vibecode', None, None, 'repair-json-only-v1'))
        self.validate()

    def test_effort_tokens_and_foreign_fields_are_rejected(self):
        for change in ({'reasoning_effort': 'low'}, {'max_output_tokens': 1024}, {'kind': 'image'}, {'channel': 'cheap'},
                       {'prompt_variant': 'free-text'}, {'synthetic_only': True}, {'model_id': 'gpt-synthetic-alt'},
                       {'model_id': 'no-such-model'}, {'model_id': 'gpt-image-2-vip'}, {'provider': 'other'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(**change)

    def test_synthetic_mode_needs_the_synthetic_alias_and_flag(self):
        synthetic = {**REFERENCE, 'model_id': 'gpt-synthetic-alt', 'synthetic_only': True}
        profiles_v1.validate_vibecode_profile(synthetic, 'synthetic')
        for bad in ({**synthetic, 'synthetic_only': False}, {**synthetic, 'model_id': 'gpt-5.6-sol'}):
            with self.assertRaises(ValueError):
                profiles_v1.validate_vibecode_profile(bad, 'synthetic')

    def test_kie_validation_is_untouched(self):
        kie = profiles_v1.CATALOG['synthetic-json-v1']
        with patch.object(profiles_v1, 'active_catalog', return_value={'k': kie}):
            profiles_v1.validate_candidate(kie)


class VibecodeEffectiveBody(unittest.TestCase):
    BODY = {'model': 'gpt-5.6-sol', 'stream': True,
            'messages': [{'role': 'system', 'content': 'contract'}, {'role': 'user', 'content': 'facts'},
                         {'role': 'assistant', 'content': 'draft'}, {'role': 'user', 'content': 'last question'}]}

    def effective(self, body, candidate=None):
        cfg, catalog = production(candidate)
        with cfg, catalog:
            return profiles_v1.effective_body(body, candidate or dict(REFERENCE))

    def test_suffix_goes_only_to_the_last_user_message(self):
        source = copy.deepcopy(self.BODY)
        result = self.effective(source)
        self.assertEqual(source, self.BODY)
        self.assertEqual(result['messages'][:3], self.BODY['messages'][:3])
        self.assertTrue(result['messages'][3]['content'].startswith('last question'))
        self.assertNotEqual(result['messages'][3]['content'], 'last question')
        self.assertEqual(result['stream'], True)
        self.assertEqual(result['model'], 'gpt-5.6-sol')

    def test_body_gets_no_responses_only_knobs(self):
        result = self.effective(self.BODY)
        for key in ('reasoning', 'max_output_tokens', 'input'):
            self.assertNotIn(key, result)

    def test_transformation_is_idempotent_and_deterministic(self):
        once = self.effective(self.BODY)
        self.assertEqual(self.effective(once), once)
        self.assertEqual(digest(self.effective(self.BODY)), digest(once))

    def test_responses_shape_and_multimodal_content_are_rejected(self):
        multimodal = [{'role': 'user', 'content': [{'type': 'text', 'text': 'x'}]}]
        for body in ({'model': 'm', 'input': 'text'}, {'model': 'm', 'messages': [], 'input': 'x'},
                     {'model': 'm', 'messages': multimodal}, {'model': 'm', 'messages': [{'role': 'system', 'content': 's'}]},
                     {'model': 'm', 'messages': [{'role': 'user', 'content': 'x', 'name': 'extra'}]}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.effective(body)

    def test_kie_profile_still_rejects_a_chat_body(self):
        kie = profiles_v1.CATALOG['synthetic-json-v1']
        with patch.object(profiles_v1, 'active_catalog', return_value={'k': kie}), \
                self.assertRaisesRegex(ValueError, 'Responses text contract'):
            profiles_v1.effective_body(self.BODY, kie)


class SourceProvider(unittest.TestCase):
    def test_recorded_provider_wins(self):
        self.assertEqual(profiles_v1.source_provider({'provider': 'vibecode', 'path': '/codex/v1/responses'}), 'vibecode')
        self.assertEqual(profiles_v1.source_provider({'provider': 'kie'}), 'kie')

    def test_legacy_records_use_a_path_and_host_fingerprint(self):
        self.assertEqual(profiles_v1.source_provider({'host': 'api.kie.ai', 'path': '/codex/v1/responses'}), 'kie')
        self.assertEqual(profiles_v1.source_provider({'host': 'vibecode.moe', 'path': '/v1/chat/completions'}), 'vibecode')

    def test_anything_else_is_unknown_and_never_matches(self):
        for payload in (None, {}, {'provider': 'gpt'}, {'host': 'relay.example', 'path': '/v1/chat/completions'},
                        {'host': 'vibecode.moe', 'path': '/v1/models'}):
            self.assertEqual(profiles_v1.source_provider(payload), 'unknown')
            self.assertTrue(profiles_v1._provider_mismatch(REFERENCE, profiles_v1.source_provider(payload)))
        self.assertFalse(profiles_v1._provider_mismatch(REFERENCE, 'vibecode'))
        self.assertTrue(profiles_v1._provider_mismatch(REFERENCE, 'kie'))


class VibecodeRoute(unittest.TestCase):
    CFG = {'online_enabled': True, 'decision': PRODUCTION['decision']}

    def test_approved_hosts_https_and_chat_path_only(self):
        ok = ('https://vibecode.moe/v1/chat/completions', 'http://127.0.0.1:18766/v1/chat/completions')
        for url in ok:
            profiles_v1._check_vibecode_route(self.CFG, url)
        bad = ('http://vibecode.moe/v1/chat/completions', 'https://api.kie.ai/codex/v1/responses',
               'https://evil.example/v1/chat/completions', 'https://vibecode.moe/v1/models',
               'https://user:pw@vibecode.moe/v1/chat/completions', 'https://vibecode.moe/v1/chat/completions?x=1')
        for url in bad:
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'route differs'):
                profiles_v1._check_vibecode_route(self.CFG, url)

    def test_disabled_decision_provider_blocks_even_a_good_route(self):
        for cfg in ({'online_enabled': False, 'decision': PRODUCTION['decision']}, {'online_enabled': True, 'decision': None}):
            with self.assertRaises(ValueError):
                profiles_v1._check_vibecode_route(cfg, 'https://vibecode.moe/v1/chat/completions')

    def test_production_transport_check_is_provider_specific(self):
        with patch('app.services.decision_config.runtime_config', return_value=PRODUCTION):
            profiles_v1.synthetic_transport('https://vibecode.moe/v1/chat/completions', 'vk-x', 'vibecode')
            with self.assertRaisesRegex(ValueError, 'route differs'):
                profiles_v1.synthetic_transport('https://vibecode.moe/v1/chat/completions', 'vk-x', 'kie')


class RuntimeProviderGuard(unittest.IsolatedAsyncioTestCase):
    async def test_transform_request_blocks_a_profile_of_another_provider_before_any_io(self):
        kie_candidate = dict(profiles_v1.CATALOG['synthetic-json-v1'])
        overlay = {'attempt_id': 'a1', 'overlay_id': 'o1', 'candidate': kie_candidate}
        ctx = SimpleNamespace(attempt_id='a1', observer=None)
        token = profiles_v1._current.set(overlay)
        try:
            with use_override(NodeLlmOverride('gpt-5.6-sol', 'stable', 'text', 'vibecode', 'x')):
                with self.assertRaisesRegex(RuntimeError, 'provider differs from node provider'):
                    await profiles_v1.transform_request(ctx, {'url': 'https://vibecode.moe/v1/chat/completions',
                                                              'body': {'messages': []}, 'headers': {}})
        finally:
            profiles_v1._current.reset(token)

    async def test_no_overlay_passes_kwargs_through_untouched(self):
        kwargs = {'url': 'u', 'body': {'messages': []}}
        self.assertIs(await profiles_v1.transform_request(SimpleNamespace(attempt_id='a'), kwargs), kwargs)
