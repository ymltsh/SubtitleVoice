import os
import shutil
import tempfile
import unittest

from backend.database import (
    create_analysis_run,
    delete_clips_by_episode,
    finish_analysis_run,
    get_db,
    init_project_db,
    insert_clips_batch,
    replace_auto_assignments,
    set_clip_speaker,
    set_clip_trim,
)
from backend.models import Clip
from backend.speaker.cache import get_clip_wav_path


class P0P1IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.project_dir = tempfile.mkdtemp(prefix="subtitlevoice-test-")
        init_project_db(self.project_dir)
        insert_clips_batch(self.project_dir, [
            Clip(episode="E1", start=0, end=1, text="manual"),
            Clip(episode="E1", start=1, end=2, text="automatic"),
        ])
        conn = get_db(self.project_dir)
        conn.execute("INSERT INTO speakers(name, color) VALUES('A', '#fff')")
        self.speaker_id = conn.execute("SELECT id FROM speakers").fetchone()["id"]
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.project_dir, ignore_errors=True)

    def test_rerun_preserves_manual_keep_and_records_predictions(self):
        set_clip_speaker(self.project_dir, 1, self.speaker_id)
        run_id = create_analysis_run(self.project_dir, self.speaker_id, "E1", 0.4, 0)
        replace_auto_assignments(self.project_dir, self.speaker_id, "E1", 0.4, [
            {"clip_id": 1, "score": 0.99}, {"clip_id": 2, "score": 0.99},
        ], run_id)
        finish_analysis_run(self.project_dir, run_id, "success")

        conn = get_db(self.project_dir)
        rows = [dict(row) for row in conn.execute(
            "SELECT id, selected_speaker_id, assignment_source FROM clips ORDER BY id"
        )]
        prediction_count = conn.execute("SELECT COUNT(*) AS count FROM clip_predictions").fetchone()["count"]
        conn.close()
        self.assertEqual(rows[0]["assignment_source"], "manual")
        self.assertEqual(rows[0]["selected_speaker_id"], self.speaker_id)
        self.assertEqual(rows[1]["assignment_source"], "auto")
        self.assertEqual(prediction_count, 2)

    def test_trim_and_reimport_remove_only_affected_cache_files(self):
        wav_path = get_clip_wav_path(self.project_dir, 1)
        embedding_path = os.path.join(self.project_dir, "cache", "embedding", "000001.npy")
        score_path = os.path.join(self.project_dir, "cache", "retrieval", "speaker_1_E1.json")
        for path in (wav_path, embedding_path, score_path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as file:
                file.write(b"cache")

        set_clip_trim(self.project_dir, 1, 0.1, 0)
        self.assertFalse(os.path.exists(wav_path))
        self.assertFalse(os.path.exists(embedding_path))
        self.assertFalse(os.path.exists(score_path))

        # Recreate cache files, then ensure deleting/re-importing the episode
        # removes the old clip-id files as well.
        for path in (wav_path, embedding_path, score_path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as file:
                file.write(b"cache")
        delete_clips_by_episode(self.project_dir, "E1")
        self.assertFalse(os.path.exists(wav_path))
        self.assertFalse(os.path.exists(embedding_path))
        self.assertFalse(os.path.exists(score_path))


if __name__ == "__main__":
    unittest.main()
