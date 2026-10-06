"""Dry run of the supervised resume of `music` (Suno through Kie): see media_dry_run_base for what is real.

The node body is the REAL generate_music.run: saved Suno prompt, kie_http.run_generation with the Suno spec,
the resume guard, Artifact and the post-step check. Only the Kie HTTP boundary and the GPT client are faked.
"""
import unittest

import pytest

import media_dry_run_base as base
from app.models import Project, ProjectStatus
from app.orchestrator.steps import generate_music
from sqlalchemy import select

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


class MusicDryRun(base.MediaDryRun):
    fixture_node_type = "music"
    status = ProjectStatus.generating_music
    running_key = "generating_music"
    api = "suno"
    media_kind = "audio"
    result_ext = ".mp3"
    key = "music"

    async def native_step(self, session, project):
        await generate_music.run(session, project, None)

    def result_files(self):
        return (self.data / "music").glob("music_*.mp3")

    async def change_request_inputs(self):
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            project.script_text = "Совсем другой закадровый текст."
            await session.commit()

    async def test_the_saved_prompt_is_reused_and_the_model_is_not_asked_again_on_restart(self):
        asked = []

        class CountingGpt(base.FakeGpt):
            async def ask_with_files(self, *a, **k):
                asked.append(1)
                return await super().ask_with_files(*a, **k)

        from unittest.mock import patch
        with patch("app.services.gpt_client.get_gpt_client", lambda: CountingGpt()):
            await self.killed_then_native_poll_timeout()
        self.assertEqual(len(asked), 1, "the restart takes the saved Suno prompt, GPT is not called again")
        self.assertTrue((self.data / "music" / ".suno_prompt.txt").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
