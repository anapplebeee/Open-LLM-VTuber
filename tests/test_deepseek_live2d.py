from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.open_llm_vtuber.live2d_model import Live2dModel

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "live2d-models" / "deepseek"


class DeepSeekModelAssetTest(unittest.TestCase):
    def test_registry_entry_loads(self):
        model = Live2dModel("deepseek")
        self.assertTrue(
            model.model_info["url"].startswith(
                "/live2d-models/deepseek/deepseek.model3.json"
            )
        )
        self.assertEqual(model.emo_map["joy"], "happy")
        self.assertEqual(model.emo_map["smirk"], "happy2")
        self.assertEqual(model.emo_map["neutral"], "relax")
        self.assertEqual(model.emo_map["fear"], "puzzled")
        self.assertEqual(model.emo_map["surprise"], "surprised")
        self.assertEqual(model.emo_map["sadness"], "sad")
        self.assertEqual(model.emo_map["anger"], "anger")
        self.assertEqual(model.emo_map["disgust"], "unhappy")
        self.assertEqual(model.extract_emotion("[joy]好开心"), ["happy"])
        self.assertEqual(model.extract_emotion("[sadness]难过"), ["sad"])
        self.assertEqual(model.extract_emotion("[smirk]得意"), ["happy2"])
        self.assertEqual(model.extract_emotion("[fear]害怕"), ["puzzled"])
        self.assertEqual(model.extract_emotion("[surprise]惊讶"), ["surprised"])
        self.assertEqual(model.extract_emotion("[anger]生气"), ["anger"])
        self.assertEqual(model.extract_emotion("[disgust]嫌弃"), ["unhappy"])
        self.assertEqual(model.extract_emotion("[neutral]平静"), ["relax"])
        self.assertIn("[开心]", model.emo_str)
        self.assertIn("[生气]", model.emo_str)
        self.assertEqual(model.extract_emotion("[开心]好开心"), ["happy"])
        self.assertEqual(model.extract_emotion("[生气]有点生气"), ["anger"])
        self.assertEqual(model.extract_emotion("[得意]得意"), ["happy2"])

    def test_required_assets_exist(self):
        model3 = json.loads((MODEL_DIR / "deepseek.model3.json").read_text(encoding="utf-8"))
        refs = model3["FileReferences"]
        self.assertTrue((MODEL_DIR / refs["Moc"]).is_file())
        for texture in refs["Textures"]:
            self.assertTrue((MODEL_DIR / texture).is_file())
        for expression in refs["Expressions"]:
            self.assertTrue((MODEL_DIR / expression["File"]).is_file())

    def test_lip_sync_and_expressions_are_wired(self):
        model3 = json.loads((MODEL_DIR / "deepseek.model3.json").read_text(encoding="utf-8"))
        groups = {group["Name"]: group["Ids"] for group in model3["Groups"]}
        self.assertIn("ParamMouthOpenY", groups["LipSync"])
        names = {item["Name"] for item in model3["FileReferences"]["Expressions"]}
        self.assertTrue({"happy", "puzzled", "relax", "unhappy", "anger", "sad", "surprised"} <= names)
        self.assertIn("Talk", model3["FileReferences"]["Motions"])


if __name__ == "__main__":
    unittest.main()
