import unittest
from dataclasses import replace
from pathlib import Path

from voice_gateway.config import load_config, required_model_paths


class ConfigTests(unittest.TestCase):
    def test_default_config(self):
        root = Path(__file__).resolve().parents[1]
        config = load_config(root / "config" / "default.json")
        self.assertEqual(config.audio.sample_rate, 16000)
        self.assertEqual(config.audio.frame_samples, 512)
        self.assertEqual(config.activation.mode, "kws")
        self.assertEqual(config.activation.level_threshold_dbfs, -35.0)
        self.assertEqual(config.kws.keyword, "你好思德")
        self.assertEqual(config.asr.language, "")
        self.assertEqual(config.asr.replacements["展向"], "展项")
        self.assertEqual(config.session.speech_wait_timeout_seconds, 8.0)
        self.assertEqual(config.logging.retention_days, 30)
        self.assertEqual(config.logging.results_directory.name, "results")
        self.assertEqual(set(required_model_paths(config)), {
            "KWS tokens", "KWS encoder", "KWS decoder", "KWS joiner",
            "VAD model", "ASR model", "ASR tokens"
        })
        level_config = replace(
            config,
            activation=replace(config.activation, mode="level"),
        )
        self.assertEqual(
            set(required_model_paths(level_config)),
            {"VAD model", "ASR model", "ASR tokens"},
        )


if __name__ == "__main__":
    unittest.main()
