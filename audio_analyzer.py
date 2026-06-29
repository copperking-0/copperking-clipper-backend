"""
audio_analyzer.py — Audio excitement detection.
Analyzes volume, variance, and spikes to boost clip scores.
No changes from v1 — already cross-platform and clean.
"""

import numpy as np
import soundfile as sf
import os


def analyze_audio(filepath: str) -> dict:
    """
    Analyze audio for excitement indicators.
    Returns a score boost (0-3) and analysis summary.
    """
    try:
        audio, sample_rate = sf.read(filepath)

        if len(audio.shape) > 1:
            audio = np.mean(audio, axis=1)

        rms        = np.sqrt(np.mean(audio ** 2))
        max_volume = np.max(np.abs(audio))

        chunk_size = sample_rate // 2
        chunks     = [audio[i:i + chunk_size] for i in range(0, len(audio), chunk_size)]
        chunk_rms  = [np.sqrt(np.mean(c ** 2)) for c in chunks if len(c) == chunk_size]

        volume_variance = np.std(chunk_rms)
        peak_threshold  = rms * 2.5
        peaks           = np.sum(np.abs(audio) > peak_threshold)
        peak_ratio      = peaks / len(audio)

        spike_count = 0
        for i in range(1, len(chunk_rms)):
            if chunk_rms[i] > chunk_rms[i - 1] * 2.0:
                spike_count += 1

        boost   = 0
        reasons = []

        if rms > 0.08:
            boost += 1
            reasons.append("high volume")

        if volume_variance > 0.05:
            boost += 1
            reasons.append("dynamic audio")

        if spike_count >= 2:
            boost += 1
            reasons.append(f"{spike_count} excitement spikes")

        return {
            "boost":           min(boost, 3),
            "rms":             round(float(rms), 4),
            "peak_ratio":      round(float(peak_ratio), 4),
            "spike_count":     spike_count,
            "volume_variance": round(float(volume_variance), 4),
            "reasons":         reasons
        }

    except Exception as e:
        print(f"⚠️  Audio analysis error: {e}")
        return {"boost": 0, "reasons": ["analysis failed"]}
