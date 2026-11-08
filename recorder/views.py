from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import tasks
from .forms import JobForm
from .models import TutorialJob
from .services.crypto import encrypt


def job_list(request):
    return render(request, "recorder/list.html", {"jobs": TutorialJob.objects.all()[:50]})


def job_new(request):
    form = JobForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        job = TutorialJob.objects.create(
            url=d["url"],
            username_enc=encrypt(d["username"]),
            password_enc=encrypt(d["password"]),
            language=d["language"],
            demo_mode=d["demo_mode"],
        )
        tasks.start_explore(job.pk)
        return redirect("job_detail", pk=job.pk)
    return render(request, "recorder/new.html", {"form": form})


def job_detail(request, pk):
    job = get_object_or_404(TutorialJob, pk=pk)
    return render(request, "recorder/detail.html", {"job": job})


@require_POST
def job_approve(request, pk):
    job = get_object_or_404(TutorialJob, pk=pk)
    if job.status != TutorialJob.Status.AWAITING:
        return redirect("job_detail", pk=pk)
    for i, step in enumerate(job.steps):
        step["include"] = f"include_{i}" in request.POST
        text = request.POST.get(f"narration_{i}", "").strip()
        if text:
            step["narration"] = text[:800]
    job.status = TutorialJob.Status.RECORDING
    job.save()
    tasks.start_record(job.pk)
    return redirect("job_detail", pk=pk)
