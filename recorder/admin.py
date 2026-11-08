from django.contrib import admin

from .models import TutorialJob


@admin.register(TutorialJob)
class TutorialJobAdmin(admin.ModelAdmin):
    list_display = ("url", "status", "language", "created_at")
    exclude = ("username_enc", "password_enc")
    readonly_fields = ("steps", "video_path", "error")
