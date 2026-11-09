import numpy as np
import pytest

from local_inference.model_manager import (
    firered_vad_cmvn_path,
    firered_vad_onnx_path,
)
from local_inference.vad_processor import FireRedStreamVAD


def _make_vad() -> FireRedStreamVAD:
    return FireRedStreamVAD(
        str(firered_vad_onnx_path()),
        str(firered_vad_cmvn_path()),
    )


def test_firered_vad_reports_silence_as_non_speech():
    vad = _make_vad()
    silence = np.zeros(512, dtype=np.float32)

    probabilities = [vad.probability(silence, 16000) for _ in range(5)]

    assert max(probabilities) < 0.2


def test_firered_vad_rejects_other_sample_rates():
    vad = _make_vad()

    with pytest.raises(ValueError, match="16 kHz"):
        vad.probability(np.zeros(512, dtype=np.float32), 8000)
