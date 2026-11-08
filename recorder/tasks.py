"""Background jobs. Plain threads keep the MVP dependency-free; swap for Celery/RQ in production."""
import logging
import shutil
import threading

from django.db import close_old_connections

from .models import TutorialJob
from .services import explorer, narrator, video
from .services.crypto import decrypt

log = logging.getLogger(__name__)


def _fail(job, exc):
    log.exception("Job %s failed", job.pk)
    job.status = TutorialJob.Status.FAILED
    job.error = str(exc)[:1000]
    job.wipe_credentials()
    job.save()


def _explore(job_id):
    close_old_connections()
    job = TutorialJob.objects.get(pk=job_id)
    try:
        steps = explorer.explore(
            job.url,
            decrypt(job.username_enc),
            decrypt(job.password_enc),
            job.work_dir,
            job.rel_dir,
        )
        narrator.add_narration(steps, job.language)
        job.steps = steps
        job.status = TutorialJob.Status.AWAITING
        job.save()
    except Exception as e:
        _fail(job, e)
    finally:
        close_old_connections()


def _record(job_id):
    close_old_connections()
    job = TutorialJob.objects.get(pk=job_id)
    try:
        final = video.record(job, decrypt(job.username_enc), decrypt(job.password_enc))
        job.video_path = f"{job.rel_dir}/{final.name}"
        job.status = TutorialJob.Status.DONE
        job.wipe_credentials()
        job.save()
        # Keep the finished video; drop the heavy intermediates.
        shutil.rmtree(job.work_dir / "rec", ignore_errors=True)
    except Exception as e:
        _fail(job, e)
    finally:
        close_old_connections()


def start_explore(job_id):
    threading.Thread(target=_explore, args=(job_id,), daemon=True).start()


def start_record(job_id):
    threading.Thread(target=_record, args=(job_id,), daemon=True).start()
