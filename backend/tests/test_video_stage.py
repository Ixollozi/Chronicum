from app.models import ProcessingStage
from app.services.processing import _decide_stage


class _Settings:
    video_preview_threshold_min = 10


def test_video_full_skips_preview():
    stage = _decide_stage("youtube", 3600, False, _Settings(), video_full=True)
    assert stage == ProcessingStage.FULL.value


def test_video_preview_for_long_when_opted_out():
    stage = _decide_stage("youtube", 3600, False, _Settings(), video_full=False)
    assert stage == ProcessingStage.PREVIEW.value


def test_force_full_wins():
    stage = _decide_stage("youtube", 3600, True, _Settings(), video_full=False)
    assert stage == ProcessingStage.FULL.value


def test_notes_always_full():
    stage = _decide_stage("note", None, False, _Settings(), video_full=False)
    assert stage == ProcessingStage.FULL.value
