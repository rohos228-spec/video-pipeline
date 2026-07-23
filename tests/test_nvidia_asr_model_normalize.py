"""Legacy FastConformer .env → Parakeet redirect."""

from app.services.nvidia_asr import normalize_nvidia_asr_model


def test_normalize_keeps_parakeet_v3() -> None:
    assert (
        normalize_nvidia_asr_model("nvidia/parakeet-tdt-0.6b-v3")
        == "nvidia/parakeet-tdt-0.6b-v3"
    )


def test_normalize_redirects_fastconformer() -> None:
    assert (
        normalize_nvidia_asr_model("nvidia/stt_ru_fastconformer_hybrid_large_pc")
        == "nvidia/parakeet-tdt-0.6b-v3"
    )


def test_normalize_empty_defaults_to_parakeet() -> None:
    assert normalize_nvidia_asr_model("") == "nvidia/parakeet-tdt-0.6b-v3"
