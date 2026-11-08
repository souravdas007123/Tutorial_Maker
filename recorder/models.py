import uuid
from pathlib import Path

from django.conf import settings
from django.db import models


class TutorialJob(models.Model):
    class Status(models.TextChoices):
        EXPLORING = "exploring", "Exploring the app"
        AWAITING = "awaiting", "Waiting for your approval"
        RECORDING = "recording", "Recording video"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.URLField()
    # Encrypted at rest, wiped as soon as the job finishes or fails.
    username_enc = models.TextField(blank=True)
    password_enc = models.TextField(blank=True)
    language = models.CharField(max_length=5, default="en")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.EXPLORING)
    error = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True)
    video_path = models.CharField(max_length=300, blank=True)  # relative to MEDIA_ROOT
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.url} [{self.status}]"

    @property
    def rel_dir(self) -> str:
        return f"jobs/{self.id}"

    @property
    def work_dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / self.rel_dir

    @property
    def video_url(self) -> str:
        return settings.MEDIA_URL + self.video_path if self.video_path else ""

    @property
    def in_progress(self) -> bool:
        return self.status in (self.Status.EXPLORING, self.Status.RECORDING)

    def wipe_credentials(self):
        self.username_enc = ""
        self.password_enc = ""
