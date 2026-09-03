from pathlib import Path

import pytest

from app.infrastructure.essentia_analyzer import EssentiaKeyAnalyzer


@pytest.mark.integration
def test_real_analysis_fixture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "c_major.wav"
    if not fixture.exists():
        pytest.skip("adicione tests/fixtures/c_major.wav para executar a integração real")
    result = EssentiaKeyAnalyzer("ffmpeg").analyze(fixture)
    assert result.key
    assert result.scale.value in {"major", "minor", "unknown"}
