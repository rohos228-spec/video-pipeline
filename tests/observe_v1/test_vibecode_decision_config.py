import importlib,json,unittest
from pathlib import Path
from unittest.mock import patch
from app.settings import settings

KEY='vk-test-fixture-0000'
ENDPOINT='https://vibecode.moe/v1/chat/completions'

def vibecode(**extra):
    d={'version':1,'mode':'production','online_enabled':True,'endpoint':ENDPOINT,
       'model_transport':'real','external_calls_enabled':True,'retrieval_allowed':False,'continuation_allowed':False,
       'decision':{'catalog_id':'gpt-5.6-sol-vibecode','model_id':'gpt-5.6-sol','reasoning_effort':None,
                   'max_output_tokens':2048,'timeout_s':60},
       'profiles':{},'allowed_actions':['read_events','read_contract','retry_same','human_review','wait','finish']}
    d.update(extra);return d

def kie(**extra):
    d={'version':1,'mode':'production','online_enabled':True,'endpoint':'http://127.0.0.1:18766/codex/v1/responses',
       'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
       'profiles':{},'allowed_actions':['read_events','human_review']}
    d.update(extra);return d


class VibecodeConfig(unittest.TestCase):
    def setUp(self):
        self.config=importlib.import_module('app.services.decision_config')
        key=patch.object(settings,'vibecode_api_key',KEY);key.start();self.addCleanup(key.stop)
        relay=patch.object(settings,'gpt_relay_token','');relay.start();self.addCleanup(relay.stop)

    def invalid(self,cfg,pattern=None):
        with self.assertRaisesRegex(ValueError,pattern or '.'):self.config.validate_config(cfg)

    def test_route_is_valid_and_reports_route_kind(self):
        cfg=self.config.validate_config(vibecode())
        self.assertEqual(self.config.route_kind(cfg),'vibecode_chat_completions')
        self.assertEqual(self.config.provider(cfg),'vibecode')
        with patch.object(self.config,'runtime_config',return_value=cfg):
            st=self.config.status()
        self.assertEqual((st['provider'],st['route_kind']),('vibecode','vibecode_chat_completions'))

    def test_kie_route_kind_unchanged(self):
        cfg=self.config.validate_config(kie())
        self.assertEqual(self.config.route_kind(cfg),'kie_responses');self.assertEqual(self.config.provider(cfg),'kie')
        self.assertIsNone(self.config.route_kind({'decision':None}))

    def test_cross_provider_paths_are_rejected(self):
        self.invalid(vibecode(endpoint='https://vibecode.moe/codex/v1/responses'),'approved route')
        self.invalid(kie(endpoint='http://127.0.0.1:18766/v1/chat/completions'),'approved route')

    def test_catalog_alias_and_other_models_are_not_accepted(self):
        for change in ({'catalog_id':'gpt-5.6-sol'},{'catalog_id':'gpt-5.6-terra-vibecode'},{'model_id':'gpt-5-6-sol'},
                       {'model_id':'gpt-5.6-terra'},{'catalog_id':['x']}):
            with self.subTest(change=change):
                cfg=vibecode();cfg['decision'].update(change);self.invalid(cfg)

    def test_reasoning_effort_must_be_null(self):
        cfg=vibecode();cfg['decision']['reasoning_effort']='low';self.invalid(cfg,'reasoning')
        cfg=kie();cfg['decision']['reasoning_effort']=None;self.invalid(cfg,'reasoning')

    def test_requires_explicit_real_external_and_single_post_policy(self):
        base=vibecode()
        for key in ('model_transport','external_calls_enabled','continuation_allowed','retrieval_allowed'):
            cfg=dict(base);cfg.pop(key);self.invalid(cfg)
        for change in ({'continuation_allowed':True},{'retrieval_allowed':True},{'gateway_ack':True},
                       {'external_calls_enabled':False},{'model_transport':'fixture'}):
            with self.subTest(change=change):self.invalid({**base,**change})

    def test_profiles_and_retry_profile_are_forbidden(self):
        profile={'model_id':'gpt-5-6-sol','provider':'kie','kind':'text','channel':'stable','reasoning_effort':'medium',
                 'max_output_tokens':2048,'prompt_variant':'require-apply-ops-v1','synthetic_only':False}
        self.invalid(vibecode(profiles={'p':profile}),'profile')
        self.invalid(vibecode(allowed_actions=['retry_profile','human_review'],builtin_profiles=False),'retry_profile')

    def test_synthetic_mode_is_rejected(self):
        self.invalid(vibecode(mode='synthetic',endpoint='http://127.0.0.1:1/v1/chat/completions',
                              model_transport='fixture',external_calls_enabled=False))

    def test_host_allow_list(self):
        for endpoint in ('https://evil.invalid/v1/chat/completions','http://vibecode.moe/v1/chat/completions',
                         ENDPOINT+'?x=1','https://user:pass@vibecode.moe/v1/chat/completions',ENDPOINT+'#f',
                         'https://vibecode.moe/v1/other','https://vibecode.moe.evil.invalid/v1/chat/completions'):
            with self.subTest(endpoint=endpoint):self.invalid(vibecode(endpoint=endpoint))
        self.assertTrue(self.config.validate_config(vibecode(endpoint='http://127.0.0.1:18766/v1/chat/completions')))

    def test_relay_host_is_approved_only_when_configured(self):
        relay_endpoint='https://relay.example/v1/chat/completions'
        self.invalid(vibecode(endpoint=relay_endpoint),'host')
        with patch.object(settings,'gpt_relay_token','relay-token'),patch.object(settings,'gpt_base_url','https://relay.example'):
            self.assertEqual(self.config.validate_config(vibecode(endpoint=relay_endpoint))['endpoint'],relay_endpoint)
            self.invalid(vibecode(endpoint='https://other.example/v1/chat/completions'),'host')

    def test_config_cannot_carry_credentials(self):
        for extra in ({'api_key':KEY},{'headers':{'Authorization':'Bearer '+KEY}},{'authorization':KEY}):
            with self.subTest(extra=extra):self.invalid(vibecode(**extra))
        cfg=vibecode();cfg['decision']['api_key']=KEY;self.invalid(cfg)
        with patch.object(self.config,'runtime_config',return_value=self.config.validate_config(vibecode())):
            self.assertNotIn('vk-',json.dumps(self.config.status()))

    def test_example_is_disabled_and_valid(self):
        raw=json.loads((Path(__file__).resolve().parents[2]/'config/orchestrator-runtime.vibecode.example.json').read_text())
        self.assertIs(raw['online_enabled'],False)
        cfg=self.config.validate_config(raw)
        self.assertEqual(self.config.route_kind(cfg),'vibecode_chat_completions');self.assertEqual(set(cfg['profiles']),{'vibecode-json-repair-v1'})
        with self.assertRaisesRegex(ValueError,'disabled'):self.config.check_route(cfg,cfg['endpoint'])

    def test_check_route_rejects_arbitrary_urls(self):
        cfg=self.config.validate_config(vibecode())
        self.config.check_route(cfg,ENDPOINT)
        for url in (ENDPOINT+'?x=1','https://evil.invalid/v1/chat/completions','https://vibecode.moe/codex/v1/responses',
                    'http://vibecode.moe/v1/chat/completions','https://user:pw@vibecode.moe/v1/chat/completions',''):
            with self.subTest(url=url),self.assertRaisesRegex(ValueError,'route differs'):self.config.check_route(cfg,url)

    def test_check_route_requires_key_presence_only(self):
        cfg=self.config.validate_config(vibecode())
        with patch.object(settings,'vibecode_api_key','  '),self.assertRaisesRegex(ValueError,'credential missing'):
            self.config.check_route(cfg,ENDPOINT)

    def test_kie_regressions(self):
        for change in ({'catalog_id':'unapproved-provider'},{'model_id':'gpt-synthetic-alt'},{'max_output_tokens':0}):
            cfg=kie();cfg['decision'].update(change);self.invalid(cfg)
        with self.assertRaises(ValueError):self.config.validate_config(kie(endpoint='http://outside.invalid/codex/v1/responses'))
        self.assertEqual(self.config.validate_config(kie())['decision']['model_id'],'gpt-5-6-sol')


if __name__=='__main__':unittest.main()


def test_vibecode_config_with_matching_profile_loads_and_kie_profile_is_rejected():
    import copy
    import json
    from pathlib import Path

    from app.services import decision_config

    path = Path(__file__).resolve().parents[2] / "config" / "orchestrator-runtime.vibecode.example.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "retry_profile" in raw["allowed_actions"]
    assert set(raw["profiles"]) == {"vibecode-json-repair-v1"}
    validate = getattr(decision_config, "validate_config", None) or getattr(decision_config, "validate_runtime_config")
    validate(copy.deepcopy(raw))

    kie_profile = dict(next(iter(raw["profiles"].values())), provider="kie")
    bad = copy.deepcopy(raw)
    bad["profiles"] = {"vibecode-json-repair-v1": kie_profile}
    try:
        validate(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("a Kie profile must be rejected on a Vibecode route")

    empty = copy.deepcopy(raw)
    empty["profiles"] = {}
    empty["builtin_profiles"] = False
    try:
        validate(empty)
    except ValueError:
        pass
    else:
        raise AssertionError("retry_profile without any approved profile must be rejected")
