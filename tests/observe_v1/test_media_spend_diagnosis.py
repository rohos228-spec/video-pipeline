import unittest

from app.services.actions_recovery import RecoveryAction
from app.services.diagnosis import derive_incidents
import test_diagnosis as td


class WrappedTimeouts(unittest.TestCase):
    def incidents(self, **payload):
        rows = [td.event('node_error', request=None, **payload)]
        return derive_incidents(td.SCOPE, rows, {})

    def test_wrapped_timeouts_are_uncertain(self):
        for name in ("ElevenLabsUncertainError", "KieUncertainError"):
            result = self.incidents(error_type=name)
            self.assertTrue(any(i["type"] == "uncertain_request"
                                and i["recommendation"]["action"] == "review_provider_response" for i in result), name)

    def test_cause_type_is_honoured(self):
        result = self.incidents(error_type="KieHttpError", cause_type="ReadTimeout")
        self.assertTrue(any(i["type"] == "uncertain_request" for i in result))

    def test_plain_provider_errors_stay_non_uncertain(self):
        for name in ("ElevenLabsApiError", "KieHttpError"):
            result = self.incidents(error_type=name)
            self.assertFalse(any(i["type"] == "uncertain_request" for i in result), name)

    def test_exception_classes_are_compatible_subclasses(self):
        from app.bots.kie_http import KieHttpError, KieUncertainError
        from app.services.elevenlabs_api import ElevenLabsApiError, ElevenLabsUncertainError
        self.assertTrue(issubclass(KieUncertainError, KieHttpError))
        self.assertTrue(issubclass(ElevenLabsUncertainError, ElevenLabsApiError))
